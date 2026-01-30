"""
Database models and management for Meta Marketing Dashboard
"""
import os
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    Boolean, Text, ForeignKey, Date, Index, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

Base = declarative_base()


class Client(Base):
    """Client configuration and settings"""
    __tablename__ = 'clients'

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # Internal key
    display_name = Column(String(100), nullable=False)
    ad_account_id = Column(String(50), nullable=False)
    daily_spend_target = Column(Float, default=0.0)
    currency = Column(String(10), default='EUR')
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    frameworks = relationship("ClientFramework", back_populates="client", cascade="all, delete-orphan")
    performance_data = relationship("AdPerformance", back_populates="client", cascade="all, delete-orphan")
    creative_tags = relationship("CreativeTag", back_populates="client", cascade="all, delete-orphan")
    alerts = relationship("AlertHistory", back_populates="client", cascade="all, delete-orphan")


class ClientFramework(Base):
    """Client-specific awareness framework (Schwartz levels with hypotheses)"""
    __tablename__ = 'client_frameworks'

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    awareness_level = Column(Integer, nullable=False)  # 1-5
    level_name = Column(String(50), nullable=False)  # e.g., "Unaware", "Problem Aware"
    hypothesis = Column(Text, nullable=False)  # Core hypothesis for this level
    sub_hypotheses = Column(JSON, default=list)  # List of sub-hypotheses
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    client = relationship("Client", back_populates="frameworks")

    __table_args__ = (
        Index('idx_framework_client_level', 'client_id', 'awareness_level'),
    )


class AdPerformance(Base):
    """Daily snapshot of ad performance metrics"""
    __tablename__ = 'ad_performance'

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    date = Column(Date, nullable=False)  # Snapshot date

    # Ad identifiers
    ad_id = Column(String(50), nullable=False)
    ad_name = Column(String(500))
    campaign_id = Column(String(50))
    campaign_name = Column(String(500))
    adset_id = Column(String(50))
    adset_name = Column(String(500))

    # Status
    status = Column(String(50))  # ACTIVE, PAUSED, etc.
    effective_status = Column(String(50))

    # Creative info
    creative_id = Column(String(50))
    creative_format = Column(String(50))  # video, image, carousel
    creative_url = Column(Text)  # Image/video URL
    thumbnail_url = Column(Text)

    # Dates
    created_time = Column(DateTime)  # When ad was created
    launched_time = Column(DateTime)  # When ad started running

    # Spend metrics
    spend = Column(Float, default=0.0)
    spend_lifetime = Column(Float, default=0.0)

    # Delivery metrics
    impressions = Column(Integer, default=0)
    reach = Column(Integer, default=0)
    frequency = Column(Float, default=0.0)

    # Engagement metrics
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0.0)  # Click-through rate
    cpc = Column(Float, default=0.0)  # Cost per click
    cpm = Column(Float, default=0.0)  # Cost per 1000 impressions

    # Conversion metrics
    conversions = Column(Integer, default=0)
    conversion_value = Column(Float, default=0.0)
    roas = Column(Float, default=0.0)  # Return on ad spend
    cpa = Column(Float, default=0.0)  # Cost per acquisition

    # Additional action data (JSON for flexibility)
    actions = Column(JSON)  # All actions from Meta API
    action_values = Column(JSON)  # Values of actions

    # Metadata
    fetched_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    client = relationship("Client", back_populates="performance_data")

    __table_args__ = (
        Index('idx_perf_client_date', 'client_id', 'date'),
        Index('idx_perf_ad_date', 'ad_id', 'date'),
        Index('idx_perf_date', 'date'),
    )


class CreativeTag(Base):
    """Creative tagging for awareness analysis"""
    __tablename__ = 'creative_tags'

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    ad_id = Column(String(50), nullable=False)

    # Awareness tagging
    awareness_level = Column(Integer)  # 1-5
    hypothesis = Column(Text)  # Selected hypothesis from client framework
    sub_hypothesis = Column(Text)  # Selected sub-hypothesis
    creative_concept = Column(String(100))  # Pattern interrupt, Education, etc.

    # Additional metadata
    notes = Column(Text)
    tagged_by = Column(String(100))
    tagged_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    client = relationship("Client", back_populates="creative_tags")

    __table_args__ = (
        Index('idx_tag_client_ad', 'client_id', 'ad_id'),
        Index('idx_tag_awareness', 'awareness_level'),
    )


class AlertHistory(Base):
    """Log of triggered alerts"""
    __tablename__ = 'alerts_history'

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('clients.id'))
    alert_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)  # info, warning, critical
    title = Column(String(500), nullable=False)
    message = Column(Text)

    # Reference data
    ad_id = Column(String(50))
    metric_name = Column(String(100))
    metric_value = Column(Float)
    threshold_value = Column(Float)

    # Status
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime)
    acknowledged_by = Column(String(100))

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    client = relationship("Client", back_populates="alerts")

    __table_args__ = (
        Index('idx_alert_client_type', 'client_id', 'alert_type'),
        Index('idx_alert_created', 'created_at'),
        Index('idx_alert_severity', 'severity'),
    )


class DataSyncLog(Base):
    """Track data synchronization status"""
    __tablename__ = 'data_sync_log'

    id = Column(Integer, primary_key=True)
    client_name = Column(String(50))
    sync_type = Column(String(50))  # ads, insights, creatives
    status = Column(String(20))  # success, failed, in_progress
    records_fetched = Column(Integer, default=0)
    records_stored = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)


class DatabaseManager:
    """Manage database connections and operations"""

    def __init__(self, db_path: str = "data/dashboard.db"):
        self.db_path = db_path
        self._ensure_directory()
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        self.Session = sessionmaker(bind=self.engine)

    def _ensure_directory(self):
        """Ensure database directory exists"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def create_tables(self):
        """Create all database tables"""
        Base.metadata.create_all(self.engine)

    def get_session(self):
        """Get a new database session"""
        return self.Session()

    def cleanup_old_data(self, retention_days: int = 90):
        """Remove data older than retention period"""
        cutoff_date = datetime.utcnow() - timedelta(days=retention_days)

        with self.get_session() as session:
            # Clean old performance data
            deleted = session.query(AdPerformance).filter(
                AdPerformance.date < cutoff_date.date()
            ).delete()

            # Clean old alerts
            session.query(AlertHistory).filter(
                AlertHistory.created_at < cutoff_date,
                AlertHistory.acknowledged == True
            ).delete()

            # Clean old sync logs
            session.query(DataSyncLog).filter(
                DataSyncLog.started_at < cutoff_date
            ).delete()

            session.commit()
            return deleted

    def get_or_create_client(self, name: str, display_name: str, ad_account_id: str) -> Client:
        """Get existing client or create new one"""
        session = self.get_session()
        try:
            client = session.query(Client).filter(Client.name == name).first()
            if not client:
                client = Client(
                    name=name,
                    display_name=display_name,
                    ad_account_id=ad_account_id
                )
                session.add(client)
                session.commit()
            return client
        finally:
            session.close()

    def get_all_clients(self) -> List[Client]:
        """Get all active clients"""
        session = self.get_session()
        try:
            return session.query(Client).filter(Client.active == True).all()
        finally:
            session.close()


# Initialize database manager
def init_database(db_path: str = "data/dashboard.db") -> DatabaseManager:
    """Initialize database and create tables"""
    db = DatabaseManager(db_path)
    db.create_tables()
    return db
