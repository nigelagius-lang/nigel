"""
Client Health Dashboard

Provides health metrics, trends, and top/bottom performers for each client.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import func, and_, desc

from .database import DatabaseManager, Client, AdPerformance, CreativeTag
from .config import config


@dataclass
class AdPerformanceSummary:
    """Summary of a single ad's performance"""
    ad_id: str
    ad_name: str
    campaign_name: str
    status: str
    spend: float
    impressions: int
    clicks: int
    ctr: float
    cpc: float
    conversions: int
    roas: float
    cpa: float
    frequency: float
    creative_url: Optional[str]
    thumbnail_url: Optional[str]


@dataclass
class ClientHealthMetrics:
    """Health metrics for a single client"""
    client_name: str
    display_name: str

    # Spend metrics
    current_week_spend: float
    last_week_spend: float
    historical_avg_spend: float
    spend_variance_pct: float

    # ROAS metrics
    roas_7day: float
    roas_30day: float
    roas_90day: float
    roas_trend: str  # "up", "down", "stable"

    # Ad counts
    total_ads: int
    active_ads: int
    paused_ads: int
    ads_in_learning: int

    # Top/Bottom performers
    top_3_ads: List[AdPerformanceSummary]
    bottom_3_ads: List[AdPerformanceSummary]

    # Alerts
    alerts: List[Dict[str, Any]]

    # Diversity score
    diversity_score: float


class ClientHealthDashboard:
    """Generate health metrics for clients"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_client_spend_metrics(
        self,
        session,
        client_id: int,
        current_week_start: datetime,
        current_week_end: datetime,
    ) -> Tuple[float, float, float, float]:
        """Calculate spend metrics for a client"""
        # Current week spend
        current_spend = session.query(func.sum(AdPerformance.spend)).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.date >= current_week_start.date(),
            AdPerformance.date <= current_week_end.date(),
        ).scalar() or 0.0

        # Last week spend
        last_week_start = current_week_start - timedelta(days=7)
        last_week_end = current_week_end - timedelta(days=7)
        last_spend = session.query(func.sum(AdPerformance.spend)).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.date >= last_week_start.date(),
            AdPerformance.date <= last_week_end.date(),
        ).scalar() or 0.0

        # Historical average (last 90 days, weekly)
        ninety_days_ago = current_week_start - timedelta(days=90)
        total_historical = session.query(func.sum(AdPerformance.spend)).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.date >= ninety_days_ago.date(),
            AdPerformance.date < current_week_start.date(),
        ).scalar() or 0.0
        avg_weekly_spend = total_historical / 12 if total_historical else 0.0

        # Spend variance
        variance_pct = 0.0
        if avg_weekly_spend > 0:
            variance_pct = ((current_spend - avg_weekly_spend) / avg_weekly_spend) * 100

        return current_spend, last_spend, avg_weekly_spend, variance_pct

    def get_roas_metrics(
        self,
        session,
        client_id: int,
    ) -> Tuple[float, float, float, str]:
        """Calculate ROAS for different time periods"""
        now = datetime.utcnow()

        def calc_period_roas(days: int) -> float:
            start_date = (now - timedelta(days=days)).date()
            result = session.query(
                func.sum(AdPerformance.spend).label('spend'),
                func.sum(AdPerformance.conversion_value).label('revenue'),
            ).filter(
                AdPerformance.client_id == client_id,
                AdPerformance.date >= start_date,
            ).first()

            if result.spend and result.spend > 0 and result.revenue:
                return result.revenue / result.spend
            return 0.0

        roas_7d = calc_period_roas(7)
        roas_30d = calc_period_roas(30)
        roas_90d = calc_period_roas(90)

        # Determine trend
        if roas_7d > roas_30d * 1.1:
            trend = "up"
        elif roas_7d < roas_30d * 0.9:
            trend = "down"
        else:
            trend = "stable"

        return roas_7d, roas_30d, roas_90d, trend

    def get_ad_counts(
        self,
        session,
        client_id: int,
    ) -> Tuple[int, int, int, int]:
        """Get counts of ads by status"""
        now = datetime.utcnow()
        recent_date = (now - timedelta(days=1)).date()

        # Get most recent data for each ad
        subquery = session.query(
            AdPerformance.ad_id,
            func.max(AdPerformance.date).label('max_date')
        ).filter(
            AdPerformance.client_id == client_id,
        ).group_by(AdPerformance.ad_id).subquery()

        total_ads = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
            AdPerformance.client_id == client_id,
        ).scalar() or 0

        active_ads = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.effective_status == 'ACTIVE',
            AdPerformance.date >= recent_date,
        ).scalar() or 0

        paused_ads = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.effective_status == 'PAUSED',
            AdPerformance.date >= recent_date,
        ).scalar() or 0

        # Ads in learning phase (typically high frequency variance or recent creation)
        seven_days_ago = (now - timedelta(days=7)).date()
        learning_ads = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.created_time >= now - timedelta(days=7),
            AdPerformance.effective_status == 'ACTIVE',
        ).scalar() or 0

        return total_ads, active_ads, paused_ads, learning_ads

    def get_top_bottom_ads(
        self,
        session,
        client_id: int,
        days: int = 7,
    ) -> Tuple[List[AdPerformanceSummary], List[AdPerformanceSummary]]:
        """Get top 3 and bottom 3 performing ads by ROAS"""
        start_date = (datetime.utcnow() - timedelta(days=days)).date()

        # Aggregate performance per ad
        ad_perf = session.query(
            AdPerformance.ad_id,
            AdPerformance.ad_name,
            AdPerformance.campaign_name,
            AdPerformance.effective_status,
            AdPerformance.creative_url,
            AdPerformance.thumbnail_url,
            func.sum(AdPerformance.spend).label('spend'),
            func.sum(AdPerformance.impressions).label('impressions'),
            func.sum(AdPerformance.clicks).label('clicks'),
            func.avg(AdPerformance.ctr).label('ctr'),
            func.avg(AdPerformance.cpc).label('cpc'),
            func.sum(AdPerformance.conversions).label('conversions'),
            func.sum(AdPerformance.conversion_value).label('revenue'),
            func.avg(AdPerformance.frequency).label('frequency'),
        ).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.date >= start_date,
            AdPerformance.spend > 0,  # Only ads with spend
        ).group_by(
            AdPerformance.ad_id,
        ).having(
            func.sum(AdPerformance.spend) > 10  # Minimum spend threshold
        ).all()

        # Calculate ROAS and CPA for each
        ads_with_metrics = []
        for ad in ad_perf:
            spend = float(ad.spend or 0)
            revenue = float(ad.revenue or 0)
            conversions = int(ad.conversions or 0)

            roas = revenue / spend if spend > 0 else 0
            cpa = spend / conversions if conversions > 0 else 0

            ads_with_metrics.append(AdPerformanceSummary(
                ad_id=ad.ad_id,
                ad_name=ad.ad_name or '',
                campaign_name=ad.campaign_name or '',
                status=ad.effective_status or '',
                spend=spend,
                impressions=int(ad.impressions or 0),
                clicks=int(ad.clicks or 0),
                ctr=float(ad.ctr or 0),
                cpc=float(ad.cpc or 0),
                conversions=conversions,
                roas=roas,
                cpa=cpa,
                frequency=float(ad.frequency or 0),
                creative_url=ad.creative_url,
                thumbnail_url=ad.thumbnail_url,
            ))

        # Sort by ROAS
        ads_with_metrics.sort(key=lambda x: x.roas, reverse=True)

        top_3 = ads_with_metrics[:3]
        bottom_3 = [a for a in ads_with_metrics[-3:] if a.roas < 1.0][::-1]  # Only show if ROAS < 1

        return top_3, bottom_3

    def generate_health_alerts(
        self,
        client_name: str,
        metrics: 'ClientHealthMetrics',
        session,
        client_id: int,
    ) -> List[Dict[str, Any]]:
        """Generate alerts based on health metrics"""
        alerts = []
        thresholds = config.alerts

        # ROAS drop alert
        if metrics.roas_30day > 0:
            roas_change = (metrics.roas_7day - metrics.roas_30day) / metrics.roas_30day
            if roas_change < -thresholds.roas_drop_threshold:
                alerts.append({
                    'type': 'roas_drop',
                    'severity': 'critical',
                    'message': f"ROAS dropped {abs(roas_change)*100:.0f}% week-over-week",
                    'value': f"{metrics.roas_7day:.2f}x → from {metrics.roas_30day:.2f}x avg",
                })

        # Spend variance alert
        if abs(metrics.spend_variance_pct) > thresholds.spend_variance_threshold * 100:
            direction = "exceeded" if metrics.spend_variance_pct > 0 else "below"
            alerts.append({
                'type': 'spend_variance',
                'severity': 'warning',
                'message': f"Daily spend {direction} target by {abs(metrics.spend_variance_pct):.0f}%",
                'value': f"€{metrics.current_week_spend:.0f} vs €{metrics.historical_avg_spend:.0f} avg",
            })

        # Check for CTR decline (3+ consecutive days)
        ctr_declining = self._check_ctr_decline(session, client_id, thresholds.ctr_decline_days)
        if ctr_declining:
            alerts.append({
                'type': 'ctr_decline',
                'severity': 'warning',
                'message': f"CTR declining for {thresholds.ctr_decline_days}+ consecutive days",
                'value': "Review creative fatigue",
            })

        # Check for high frequency ads
        high_freq_ads = self._get_high_frequency_ads(session, client_id, thresholds.frequency_fatigue_threshold)
        if high_freq_ads:
            alerts.append({
                'type': 'frequency_fatigue',
                'severity': 'warning',
                'message': f"{len(high_freq_ads)} ads with frequency > {thresholds.frequency_fatigue_threshold}",
                'value': "Creative fatigue risk",
            })

        # Check for low ROAS ads with high spend
        low_roas_high_spend = self._get_low_roas_high_spend_ads(
            session, client_id,
            thresholds.low_roas_spend_threshold
        )
        if low_roas_high_spend:
            alerts.append({
                'type': 'low_roas_high_spend',
                'severity': 'critical',
                'message': f"{len(low_roas_high_spend)} ads spending >€{thresholds.low_roas_spend_threshold}/day with ROAS < 1.0",
                'value': "Consider pausing or optimizing",
            })

        # Diversity score alert
        if metrics.diversity_score < thresholds.min_diversity_score:
            alerts.append({
                'type': 'low_diversity',
                'severity': 'warning',
                'message': f"Creative diversity score is {metrics.diversity_score}/10",
                'value': f"Target: ≥{thresholds.min_diversity_score}/10",
            })

        return alerts

    def _check_ctr_decline(self, session, client_id: int, days: int) -> bool:
        """Check if CTR has been declining for consecutive days"""
        now = datetime.utcnow()

        daily_ctr = []
        for i in range(days + 1):
            date = (now - timedelta(days=i)).date()
            ctr = session.query(func.avg(AdPerformance.ctr)).filter(
                AdPerformance.client_id == client_id,
                AdPerformance.date == date,
            ).scalar()
            if ctr is not None:
                daily_ctr.append(float(ctr))

        if len(daily_ctr) < days:
            return False

        # Check if each day is lower than the previous
        for i in range(len(daily_ctr) - 1):
            if daily_ctr[i] >= daily_ctr[i + 1]:
                return False

        return True

    def _get_high_frequency_ads(self, session, client_id: int, threshold: float) -> List[str]:
        """Get ads with frequency above threshold"""
        recent_date = (datetime.utcnow() - timedelta(days=1)).date()

        high_freq = session.query(AdPerformance.ad_id).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.date >= recent_date,
            AdPerformance.frequency > threshold,
            AdPerformance.effective_status == 'ACTIVE',
        ).distinct().all()

        return [a[0] for a in high_freq]

    def _get_low_roas_high_spend_ads(
        self,
        session,
        client_id: int,
        spend_threshold: float,
    ) -> List[str]:
        """Get active ads with low ROAS but high daily spend"""
        recent_date = (datetime.utcnow() - timedelta(days=1)).date()

        low_roas_ads = session.query(AdPerformance.ad_id).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.date >= recent_date,
            AdPerformance.spend >= spend_threshold,
            AdPerformance.roas < 1.0,
            AdPerformance.effective_status == 'ACTIVE',
        ).distinct().all()

        return [a[0] for a in low_roas_ads]

    def get_client_health(self, client_name: str) -> Optional[ClientHealthMetrics]:
        """Get comprehensive health metrics for a client"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return None

            now = datetime.utcnow()
            # Week starts on Monday
            current_week_start = now - timedelta(days=now.weekday())
            current_week_end = now

            # Get all metrics
            current_spend, last_spend, avg_spend, variance = self.get_client_spend_metrics(
                session, client.id, current_week_start, current_week_end
            )

            roas_7d, roas_30d, roas_90d, roas_trend = self.get_roas_metrics(session, client.id)

            total_ads, active_ads, paused_ads, learning_ads = self.get_ad_counts(session, client.id)

            top_3, bottom_3 = self.get_top_bottom_ads(session, client.id)

            # Get diversity score (import here to avoid circular import)
            from .diversity_analyzer import DiversityAnalyzer
            diversity = DiversityAnalyzer(self.db)
            distribution = diversity.get_awareness_distribution(client_name)
            diversity_score = distribution.diversity_score

            # Build metrics object first (without alerts)
            metrics = ClientHealthMetrics(
                client_name=client.name,
                display_name=client.display_name,
                current_week_spend=current_spend,
                last_week_spend=last_spend,
                historical_avg_spend=avg_spend,
                spend_variance_pct=variance,
                roas_7day=roas_7d,
                roas_30day=roas_30d,
                roas_90day=roas_90d,
                roas_trend=roas_trend,
                total_ads=total_ads,
                active_ads=active_ads,
                paused_ads=paused_ads,
                ads_in_learning=learning_ads,
                top_3_ads=top_3,
                bottom_3_ads=bottom_3,
                alerts=[],
                diversity_score=diversity_score,
            )

            # Generate alerts
            alerts = self.generate_health_alerts(client_name, metrics, session, client.id)
            metrics.alerts = alerts

            return metrics

        finally:
            session.close()

    def get_all_clients_health(self) -> List[ClientHealthMetrics]:
        """Get health metrics for all clients"""
        session = self.db.get_session()
        try:
            clients = session.query(Client).filter(Client.active == True).all()
            results = []

            for client in clients:
                health = self.get_client_health(client.name)
                if health:
                    results.append(health)

            return results

        finally:
            session.close()

    def get_overview_dataframe(self) -> pd.DataFrame:
        """Get overview of all clients as DataFrame"""
        all_health = self.get_all_clients_health()

        data = []
        for h in all_health:
            # Determine ROAS trend emoji
            trend_emoji = "→"
            if h.roas_trend == "up":
                trend_emoji = "↑"
            elif h.roas_trend == "down":
                trend_emoji = "↓"

            data.append({
                'Client': h.display_name,
                'Week Spend': f"€{h.current_week_spend:,.0f}",
                'Spend vs Avg': f"{h.spend_variance_pct:+.0f}%",
                'ROAS (7d)': f"{h.roas_7day:.2f}x",
                'ROAS (30d)': f"{h.roas_30day:.2f}x",
                'Trend': trend_emoji,
                'Active Ads': h.active_ads,
                'Learning': h.ads_in_learning,
                'Diversity': f"{h.diversity_score}/10",
                'Alerts': len(h.alerts),
            })

        return pd.DataFrame(data)

    def get_ad_performance_table(
        self,
        client_name: str,
        days: int = 7,
        sort_by: str = 'spend',
        ascending: bool = False,
    ) -> pd.DataFrame:
        """Get detailed ad performance table for a client"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return pd.DataFrame()

            start_date = (datetime.utcnow() - timedelta(days=days)).date()

            # Aggregate performance per ad
            ads = session.query(
                AdPerformance.ad_id,
                AdPerformance.ad_name,
                AdPerformance.campaign_name,
                AdPerformance.adset_name,
                AdPerformance.effective_status,
                AdPerformance.creative_format,
                AdPerformance.thumbnail_url,
                func.sum(AdPerformance.spend).label('spend'),
                func.sum(AdPerformance.impressions).label('impressions'),
                func.sum(AdPerformance.reach).label('reach'),
                func.avg(AdPerformance.frequency).label('frequency'),
                func.sum(AdPerformance.clicks).label('clicks'),
                func.avg(AdPerformance.ctr).label('ctr'),
                func.avg(AdPerformance.cpc).label('cpc'),
                func.avg(AdPerformance.cpm).label('cpm'),
                func.sum(AdPerformance.conversions).label('conversions'),
                func.sum(AdPerformance.conversion_value).label('revenue'),
            ).filter(
                AdPerformance.client_id == client.id,
                AdPerformance.date >= start_date,
            ).group_by(AdPerformance.ad_id).all()

            # Get tags
            tags = {t.ad_id: t for t in session.query(CreativeTag).filter(
                CreativeTag.client_id == client.id
            ).all()}

            data = []
            for ad in ads:
                spend = float(ad.spend or 0)
                revenue = float(ad.revenue or 0)
                conversions = int(ad.conversions or 0)

                roas = revenue / spend if spend > 0 else 0
                cpa = spend / conversions if conversions > 0 else 0

                tag = tags.get(ad.ad_id)

                data.append({
                    'Ad ID': ad.ad_id,
                    'Ad Name': ad.ad_name or '',
                    'Campaign': ad.campaign_name or '',
                    'Ad Set': ad.adset_name or '',
                    'Status': ad.effective_status or '',
                    'Format': ad.creative_format or '',
                    'Spend': spend,
                    'Impressions': int(ad.impressions or 0),
                    'Reach': int(ad.reach or 0),
                    'Frequency': round(float(ad.frequency or 0), 2),
                    'Clicks': int(ad.clicks or 0),
                    'CTR': round(float(ad.ctr or 0), 2),
                    'CPC': round(float(ad.cpc or 0), 2),
                    'CPM': round(float(ad.cpm or 0), 2),
                    'Conversions': conversions,
                    'ROAS': round(roas, 2),
                    'CPA': round(cpa, 2),
                    'Awareness Level': tag.awareness_level if tag else None,
                    'Concept': tag.creative_concept if tag else None,
                })

            df = pd.DataFrame(data)
            if not df.empty and sort_by in df.columns:
                df = df.sort_values(sort_by, ascending=ascending)

            return df

        finally:
            session.close()
