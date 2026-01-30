"""
Creative Freshness Tracker

Tracks how recently each client has uploaded new creatives and provides alerts.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import pandas as pd
from sqlalchemy import func

from .database import DatabaseManager, AdPerformance, Client
from .config import config


class FreshnessStatus(Enum):
    """Freshness status levels"""
    FRESH = "fresh"        # < 7 days
    WARNING = "warning"    # 7-14 days
    CRITICAL = "critical"  # > 14 days


@dataclass
class ClientFreshness:
    """Freshness data for a single client"""
    client_name: str
    display_name: str
    last_upload_date: Optional[datetime]
    days_since_upload: int
    status: FreshnessStatus
    total_creatives: int
    creatives_last_7_days: int
    creatives_last_30_days: int
    upload_velocity_trend: str  # "increasing", "stable", "decreasing"


@dataclass
class FreshnessAlert:
    """Alert for creative freshness issues"""
    client_name: str
    alert_type: str
    severity: str
    message: str
    days_since_upload: int


class FreshnessTracker:
    """Track creative freshness across all clients"""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.warning_days = config.alerts.creative_freshness_warning_days
        self.critical_days = config.alerts.creative_freshness_critical_days

    def get_freshness_status(self, days: int) -> FreshnessStatus:
        """Determine freshness status based on days since last upload"""
        if days < self.warning_days:
            return FreshnessStatus.FRESH
        elif days < self.critical_days:
            return FreshnessStatus.WARNING
        else:
            return FreshnessStatus.CRITICAL

    def get_status_color(self, status: FreshnessStatus) -> str:
        """Get color code for freshness status"""
        colors = {
            FreshnessStatus.FRESH: "#28a745",      # Green
            FreshnessStatus.WARNING: "#ffc107",    # Yellow
            FreshnessStatus.CRITICAL: "#dc3545",   # Red
        }
        return colors.get(status, "#6c757d")

    def get_status_emoji(self, status: FreshnessStatus) -> str:
        """Get emoji for freshness status"""
        emojis = {
            FreshnessStatus.FRESH: "🟢",
            FreshnessStatus.WARNING: "🟡",
            FreshnessStatus.CRITICAL: "🔴",
        }
        return emojis.get(status, "⚪")

    def calculate_upload_velocity_trend(
        self,
        session,
        client_id: int,
        current_period_days: int = 14,
        comparison_period_days: int = 14,
    ) -> str:
        """
        Calculate whether upload velocity is increasing, stable, or decreasing
        by comparing recent uploads to previous period.
        """
        now = datetime.utcnow()
        current_start = now - timedelta(days=current_period_days)
        comparison_start = current_start - timedelta(days=comparison_period_days)
        comparison_end = current_start

        # Count unique creatives in current period
        current_count = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.created_time >= current_start,
            AdPerformance.created_time < now,
        ).scalar() or 0

        # Count unique creatives in comparison period
        comparison_count = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
            AdPerformance.client_id == client_id,
            AdPerformance.created_time >= comparison_start,
            AdPerformance.created_time < comparison_end,
        ).scalar() or 0

        if comparison_count == 0:
            return "stable" if current_count == 0 else "increasing"

        change_ratio = (current_count - comparison_count) / comparison_count

        if change_ratio > 0.2:
            return "increasing"
        elif change_ratio < -0.2:
            return "decreasing"
        else:
            return "stable"

    def get_client_freshness(self, client_name: str) -> Optional[ClientFreshness]:
        """Get freshness data for a specific client"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return None

            now = datetime.utcnow()

            # Get most recent creative upload date
            last_upload = session.query(func.max(AdPerformance.created_time)).filter(
                AdPerformance.client_id == client.id,
                AdPerformance.created_time.isnot(None),
            ).scalar()

            # Calculate days since last upload
            if last_upload:
                days_since = (now - last_upload).days
            else:
                days_since = 999  # No uploads found

            # Count total unique creatives
            total_creatives = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
                AdPerformance.client_id == client.id,
            ).scalar() or 0

            # Count creatives in last 7 days
            seven_days_ago = now - timedelta(days=7)
            creatives_7d = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
                AdPerformance.client_id == client.id,
                AdPerformance.created_time >= seven_days_ago,
            ).scalar() or 0

            # Count creatives in last 30 days
            thirty_days_ago = now - timedelta(days=30)
            creatives_30d = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
                AdPerformance.client_id == client.id,
                AdPerformance.created_time >= thirty_days_ago,
            ).scalar() or 0

            # Calculate velocity trend
            velocity_trend = self.calculate_upload_velocity_trend(session, client.id)

            return ClientFreshness(
                client_name=client.name,
                display_name=client.display_name,
                last_upload_date=last_upload,
                days_since_upload=days_since,
                status=self.get_freshness_status(days_since),
                total_creatives=total_creatives,
                creatives_last_7_days=creatives_7d,
                creatives_last_30_days=creatives_30d,
                upload_velocity_trend=velocity_trend,
            )

        finally:
            session.close()

    def get_all_clients_freshness(self) -> List[ClientFreshness]:
        """Get freshness data for all clients"""
        session = self.db.get_session()
        try:
            clients = session.query(Client).filter(Client.active == True).all()
            results = []

            for client in clients:
                freshness = self.get_client_freshness(client.name)
                if freshness:
                    results.append(freshness)

            # Sort by days since upload (most stale first)
            results.sort(key=lambda x: x.days_since_upload, reverse=True)
            return results

        finally:
            session.close()

    def get_freshness_alerts(self) -> List[FreshnessAlert]:
        """Generate alerts for clients with freshness issues"""
        alerts = []
        all_freshness = self.get_all_clients_freshness()

        for freshness in all_freshness:
            if freshness.status == FreshnessStatus.CRITICAL:
                alerts.append(FreshnessAlert(
                    client_name=freshness.display_name,
                    alert_type="creative_freshness_critical",
                    severity="critical",
                    message=f"No new creatives in {freshness.days_since_upload} days",
                    days_since_upload=freshness.days_since_upload,
                ))
            elif freshness.status == FreshnessStatus.WARNING:
                alerts.append(FreshnessAlert(
                    client_name=freshness.display_name,
                    alert_type="creative_freshness_warning",
                    severity="warning",
                    message=f"No new creatives in {freshness.days_since_upload} days",
                    days_since_upload=freshness.days_since_upload,
                ))

            # Alert for decreasing velocity
            if freshness.upload_velocity_trend == "decreasing":
                alerts.append(FreshnessAlert(
                    client_name=freshness.display_name,
                    alert_type="creative_velocity_decreasing",
                    severity="warning",
                    message="Creative upload velocity is slowing down",
                    days_since_upload=freshness.days_since_upload,
                ))

        return alerts

    def get_freshness_dataframe(self) -> pd.DataFrame:
        """Get freshness data as a pandas DataFrame for display"""
        all_freshness = self.get_all_clients_freshness()

        data = []
        for f in all_freshness:
            data.append({
                'Client': f.display_name,
                'Last Upload': f.last_upload_date.strftime('%Y-%m-%d') if f.last_upload_date else 'Never',
                'Days Since': f.days_since_upload if f.days_since_upload < 999 else 'N/A',
                'Status': f.status.value.title(),
                'Status Emoji': self.get_status_emoji(f.status),
                'Total Creatives': f.total_creatives,
                'Last 7 Days': f.creatives_last_7_days,
                'Last 30 Days': f.creatives_last_30_days,
                'Velocity Trend': f.upload_velocity_trend.title(),
            })

        return pd.DataFrame(data)

    def get_upload_history(self, client_name: str, days: int = 90) -> pd.DataFrame:
        """Get historical upload data for trend visualization"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return pd.DataFrame()

            cutoff_date = datetime.utcnow() - timedelta(days=days)

            # Get all unique ads with their created dates
            results = session.query(
                func.date(AdPerformance.created_time).label('date'),
                func.count(func.distinct(AdPerformance.ad_id)).label('count'),
            ).filter(
                AdPerformance.client_id == client.id,
                AdPerformance.created_time >= cutoff_date,
                AdPerformance.created_time.isnot(None),
            ).group_by(
                func.date(AdPerformance.created_time)
            ).order_by(
                func.date(AdPerformance.created_time)
            ).all()

            data = [{'date': r.date, 'new_creatives': r.count} for r in results]
            return pd.DataFrame(data)

        finally:
            session.close()

    def get_weekly_upload_summary(self, weeks: int = 12) -> pd.DataFrame:
        """Get weekly upload summary across all clients"""
        session = self.db.get_session()
        try:
            clients = session.query(Client).filter(Client.active == True).all()
            now = datetime.utcnow()

            data = []
            for client in clients:
                for week_offset in range(weeks):
                    week_start = now - timedelta(weeks=week_offset + 1)
                    week_end = now - timedelta(weeks=week_offset)

                    count = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
                        AdPerformance.client_id == client.id,
                        AdPerformance.created_time >= week_start,
                        AdPerformance.created_time < week_end,
                    ).scalar() or 0

                    data.append({
                        'client': client.display_name,
                        'week_start': week_start.strftime('%Y-%m-%d'),
                        'week_number': week_offset + 1,
                        'uploads': count,
                    })

            return pd.DataFrame(data)

        finally:
            session.close()
