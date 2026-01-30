"""
Alerts and Notifications System

Generates, tracks, and optionally sends alerts for various conditions.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

import pandas as pd
from sqlalchemy import func

from .database import DatabaseManager, Client, AlertHistory, AdPerformance
from .config import config
from .freshness_tracker import FreshnessTracker, FreshnessStatus
from .diversity_analyzer import DiversityAnalyzer
from .client_health import ClientHealthDashboard


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(Enum):
    CREATIVE_FRESHNESS = "creative_freshness"
    ROAS_DROP = "roas_drop"
    CTR_DECLINE = "ctr_decline"
    FREQUENCY_FATIGUE = "frequency_fatigue"
    SPEND_VARIANCE = "spend_variance"
    LOW_DIVERSITY = "low_diversity"
    AWARENESS_GAP = "awareness_gap"
    LOW_ROAS_HIGH_SPEND = "low_roas_high_spend"
    VELOCITY_DECREASE = "velocity_decrease"


@dataclass
class Alert:
    """Represents a single alert"""
    client_name: str
    display_name: str
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    message: str
    ad_id: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold_value: Optional[float] = None
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class AlertsManager:
    """Manage all alerts across the system"""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.freshness_tracker = FreshnessTracker(db)
        self.diversity_analyzer = DiversityAnalyzer(db)
        self.health_dashboard = ClientHealthDashboard(db)

    def check_all_alerts(self) -> List[Alert]:
        """Run all alert checks and return list of alerts"""
        alerts = []

        # Get all clients
        session = self.db.get_session()
        try:
            clients = session.query(Client).filter(Client.active == True).all()

            for client in clients:
                # Freshness alerts
                alerts.extend(self._check_freshness_alerts(client))

                # Health alerts
                alerts.extend(self._check_health_alerts(client))

                # Diversity alerts
                alerts.extend(self._check_diversity_alerts(client))

            return alerts

        finally:
            session.close()

    def _check_freshness_alerts(self, client: Client) -> List[Alert]:
        """Check creative freshness alerts for a client"""
        alerts = []
        freshness = self.freshness_tracker.get_client_freshness(client.name)

        if not freshness:
            return alerts

        # Critical freshness (> 14 days)
        if freshness.status == FreshnessStatus.CRITICAL:
            alerts.append(Alert(
                client_name=client.name,
                display_name=client.display_name,
                alert_type=AlertType.CREATIVE_FRESHNESS,
                severity=AlertSeverity.CRITICAL,
                title=f"No new creatives in {freshness.days_since_upload} days",
                message=f"{client.display_name} hasn't uploaded new creatives in {freshness.days_since_upload} days. "
                        f"This is above the {config.alerts.creative_freshness_critical_days}-day threshold.",
                metric_name="days_since_upload",
                metric_value=freshness.days_since_upload,
                threshold_value=config.alerts.creative_freshness_critical_days,
            ))
        # Warning freshness (7-14 days)
        elif freshness.status == FreshnessStatus.WARNING:
            alerts.append(Alert(
                client_name=client.name,
                display_name=client.display_name,
                alert_type=AlertType.CREATIVE_FRESHNESS,
                severity=AlertSeverity.WARNING,
                title=f"Creative freshness warning ({freshness.days_since_upload} days)",
                message=f"{client.display_name} hasn't uploaded new creatives in {freshness.days_since_upload} days.",
                metric_name="days_since_upload",
                metric_value=freshness.days_since_upload,
                threshold_value=config.alerts.creative_freshness_warning_days,
            ))

        # Velocity decrease
        if freshness.upload_velocity_trend == "decreasing":
            alerts.append(Alert(
                client_name=client.name,
                display_name=client.display_name,
                alert_type=AlertType.VELOCITY_DECREASE,
                severity=AlertSeverity.WARNING,
                title="Creative upload velocity decreasing",
                message=f"{client.display_name}'s creative upload frequency is slowing down compared to previous periods.",
            ))

        return alerts

    def _check_health_alerts(self, client: Client) -> List[Alert]:
        """Check health-related alerts for a client"""
        alerts = []
        health = self.health_dashboard.get_client_health(client.name)

        if not health:
            return alerts

        # Convert health alerts to Alert objects
        for h_alert in health.alerts:
            severity = AlertSeverity.WARNING
            if h_alert.get('severity') == 'critical':
                severity = AlertSeverity.CRITICAL

            alert_type_map = {
                'roas_drop': AlertType.ROAS_DROP,
                'spend_variance': AlertType.SPEND_VARIANCE,
                'ctr_decline': AlertType.CTR_DECLINE,
                'frequency_fatigue': AlertType.FREQUENCY_FATIGUE,
                'low_roas_high_spend': AlertType.LOW_ROAS_HIGH_SPEND,
                'low_diversity': AlertType.LOW_DIVERSITY,
            }

            alert_type = alert_type_map.get(h_alert.get('type'), AlertType.ROAS_DROP)

            alerts.append(Alert(
                client_name=client.name,
                display_name=client.display_name,
                alert_type=alert_type,
                severity=severity,
                title=h_alert.get('message', ''),
                message=h_alert.get('value', ''),
            ))

        return alerts

    def _check_diversity_alerts(self, client: Client) -> List[Alert]:
        """Check diversity-related alerts for a client"""
        alerts = []

        distribution = self.diversity_analyzer.get_awareness_distribution(client.name)

        # Low diversity score
        if distribution.diversity_score < config.alerts.min_diversity_score:
            alerts.append(Alert(
                client_name=client.name,
                display_name=client.display_name,
                alert_type=AlertType.LOW_DIVERSITY,
                severity=AlertSeverity.WARNING,
                title=f"Low creative diversity score: {distribution.diversity_score}/10",
                message=f"{client.display_name}'s creative diversity is below the {config.alerts.min_diversity_score}/10 threshold. "
                        f"Consider balancing creatives across all awareness levels.",
                metric_name="diversity_score",
                metric_value=distribution.diversity_score,
                threshold_value=config.alerts.min_diversity_score,
            ))

        # Check for missing awareness levels (< 5% coverage with significant data)
        if distribution.total_tagged >= 10:
            missing_levels = []
            for level, pct in distribution.level_percentages.items():
                if pct < 5:
                    from .config import AWARENESS_LEVELS
                    level_name = AWARENESS_LEVELS.get(level, f"Level {level}")
                    missing_levels.append(f"Level {level} ({level_name})")

            if len(missing_levels) >= 2:
                alerts.append(Alert(
                    client_name=client.name,
                    display_name=client.display_name,
                    alert_type=AlertType.AWARENESS_GAP,
                    severity=AlertSeverity.WARNING,
                    title=f"Missing coverage in {len(missing_levels)} awareness levels",
                    message=f"{client.display_name} has minimal coverage in: {', '.join(missing_levels)}",
                ))

        return alerts

    def save_alerts_to_history(self, alerts: List[Alert]):
        """Save alerts to the history table"""
        session = self.db.get_session()
        try:
            for alert in alerts:
                # Get client ID
                client = session.query(Client).filter(Client.name == alert.client_name).first()
                client_id = client.id if client else None

                # Check if similar alert already exists (within last hour)
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                existing = session.query(AlertHistory).filter(
                    AlertHistory.client_id == client_id,
                    AlertHistory.alert_type == alert.alert_type.value,
                    AlertHistory.created_at > one_hour_ago,
                ).first()

                if existing:
                    continue  # Don't duplicate recent alerts

                history = AlertHistory(
                    client_id=client_id,
                    alert_type=alert.alert_type.value,
                    severity=alert.severity.value,
                    title=alert.title,
                    message=alert.message,
                    ad_id=alert.ad_id,
                    metric_name=alert.metric_name,
                    metric_value=alert.metric_value,
                    threshold_value=alert.threshold_value,
                )
                session.add(history)

            session.commit()

        finally:
            session.close()

    def get_active_alerts(self, client_name: Optional[str] = None) -> List[Alert]:
        """Get currently active alerts, optionally filtered by client"""
        alerts = self.check_all_alerts()

        if client_name:
            alerts = [a for a in alerts if a.client_name == client_name]

        # Sort by severity (critical first) then by created_at
        severity_order = {AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 1, AlertSeverity.INFO: 2}
        alerts.sort(key=lambda x: (severity_order.get(x.severity, 3), x.created_at))

        return alerts

    def get_alerts_dataframe(self, client_name: Optional[str] = None) -> pd.DataFrame:
        """Get alerts as a DataFrame for display"""
        alerts = self.get_active_alerts(client_name)

        data = []
        for alert in alerts:
            severity_emoji = {
                AlertSeverity.CRITICAL: "🔴",
                AlertSeverity.WARNING: "🟡",
                AlertSeverity.INFO: "🔵",
            }

            data.append({
                'Severity': severity_emoji.get(alert.severity, "⚪"),
                'Client': alert.display_name,
                'Type': alert.alert_type.value.replace('_', ' ').title(),
                'Title': alert.title,
                'Details': alert.message,
                'Created': alert.created_at.strftime('%Y-%m-%d %H:%M'),
            })

        return pd.DataFrame(data)

    def get_alert_history(
        self,
        client_name: Optional[str] = None,
        days: int = 30,
        include_acknowledged: bool = False,
    ) -> pd.DataFrame:
        """Get historical alerts from database"""
        session = self.db.get_session()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)

            query = session.query(AlertHistory).filter(
                AlertHistory.created_at > cutoff,
            )

            if client_name:
                client = session.query(Client).filter(Client.name == client_name).first()
                if client:
                    query = query.filter(AlertHistory.client_id == client.id)

            if not include_acknowledged:
                query = query.filter(AlertHistory.acknowledged == False)

            query = query.order_by(AlertHistory.created_at.desc())
            results = query.all()

            # Get client names
            clients = {c.id: c.display_name for c in session.query(Client).all()}

            data = []
            for r in results:
                data.append({
                    'ID': r.id,
                    'Client': clients.get(r.client_id, 'Unknown'),
                    'Type': r.alert_type,
                    'Severity': r.severity,
                    'Title': r.title,
                    'Message': r.message,
                    'Created': r.created_at.strftime('%Y-%m-%d %H:%M'),
                    'Acknowledged': '✓' if r.acknowledged else '',
                })

            return pd.DataFrame(data)

        finally:
            session.close()

    def acknowledge_alert(self, alert_id: int, acknowledged_by: Optional[str] = None) -> bool:
        """Mark an alert as acknowledged"""
        session = self.db.get_session()
        try:
            alert = session.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
            if alert:
                alert.acknowledged = True
                alert.acknowledged_at = datetime.utcnow()
                alert.acknowledged_by = acknowledged_by
                session.commit()
                return True
            return False

        finally:
            session.close()

    def get_alert_summary(self) -> Dict[str, Any]:
        """Get summary of current alerts"""
        alerts = self.get_active_alerts()

        summary = {
            'total': len(alerts),
            'critical': len([a for a in alerts if a.severity == AlertSeverity.CRITICAL]),
            'warning': len([a for a in alerts if a.severity == AlertSeverity.WARNING]),
            'info': len([a for a in alerts if a.severity == AlertSeverity.INFO]),
            'by_type': {},
            'by_client': {},
        }

        for alert in alerts:
            # Count by type
            type_key = alert.alert_type.value
            summary['by_type'][type_key] = summary['by_type'].get(type_key, 0) + 1

            # Count by client
            summary['by_client'][alert.display_name] = summary['by_client'].get(alert.display_name, 0) + 1

        return summary


# Optional: Notification senders (implement when ready)

class SlackNotifier:
    """Send alerts to Slack (placeholder)"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send_alert(self, alert: Alert) -> bool:
        """Send a single alert to Slack"""
        # TODO: Implement Slack notification
        # import requests
        # color = "#dc3545" if alert.severity == AlertSeverity.CRITICAL else "#ffc107"
        # payload = {
        #     "attachments": [{
        #         "color": color,
        #         "title": f"{alert.display_name}: {alert.title}",
        #         "text": alert.message,
        #     }]
        # }
        # response = requests.post(self.webhook_url, json=payload)
        # return response.status_code == 200
        pass

    def send_daily_summary(self, alerts: List[Alert]) -> bool:
        """Send daily alert summary to Slack"""
        # TODO: Implement
        pass


class EmailNotifier:
    """Send alerts via email (placeholder)"""

    def __init__(self, api_key: str, from_email: str):
        self.api_key = api_key
        self.from_email = from_email

    def send_alert(self, alert: Alert, to_email: str) -> bool:
        """Send a single alert via email"""
        # TODO: Implement email notification using SendGrid or similar
        pass

    def send_daily_digest(self, alerts: List[Alert], to_email: str) -> bool:
        """Send daily digest email"""
        # TODO: Implement
        pass
