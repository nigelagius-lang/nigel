"""
Configuration management for Meta Marketing Dashboard
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import Dict, Optional

# Load environment variables
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

@dataclass
class ClientConfig:
    """Configuration for a single client"""
    name: str
    display_name: str
    ad_account_id: str
    daily_spend_target: float = 0.0
    currency: str = "EUR"
    active: bool = True

@dataclass
class AlertThresholds:
    """Alert threshold settings"""
    creative_freshness_warning_days: int = 7
    creative_freshness_critical_days: int = 14
    roas_drop_threshold: float = 0.20
    ctr_decline_days: int = 3
    frequency_fatigue_threshold: float = 3.0
    spend_variance_threshold: float = 0.15
    min_diversity_score: int = 5
    low_roas_spend_threshold: float = 50.0

@dataclass
class Config:
    """Main configuration class"""
    # Meta API
    meta_access_token: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""

    # Database
    database_path: str = "data/dashboard.db"
    data_retention_days: int = 90

    # Dashboard settings
    default_currency: str = "EUR"
    auto_refresh_hours: int = 1

    # Alert thresholds
    alerts: AlertThresholds = field(default_factory=AlertThresholds)

    # Clients
    clients: Dict[str, ClientConfig] = field(default_factory=dict)

    @classmethod
    def load_from_env(cls) -> 'Config':
        """Load configuration from environment variables"""
        config = cls(
            meta_access_token=os.getenv('META_ACCESS_TOKEN', ''),
            meta_app_id=os.getenv('META_APP_ID', ''),
            meta_app_secret=os.getenv('META_APP_SECRET', ''),
            database_path=os.getenv('DATABASE_PATH', 'data/dashboard.db'),
            data_retention_days=int(os.getenv('DATA_RETENTION_DAYS', '90')),
            default_currency=os.getenv('DEFAULT_CURRENCY', 'EUR'),
            auto_refresh_hours=int(os.getenv('AUTO_REFRESH_HOURS', '1')),
            alerts=AlertThresholds(
                creative_freshness_warning_days=int(os.getenv('CREATIVE_FRESHNESS_WARNING_DAYS', '7')),
                creative_freshness_critical_days=int(os.getenv('CREATIVE_FRESHNESS_CRITICAL_DAYS', '14')),
                roas_drop_threshold=float(os.getenv('ROAS_DROP_THRESHOLD', '0.20')),
                ctr_decline_days=int(os.getenv('CTR_DECLINE_DAYS', '3')),
                frequency_fatigue_threshold=float(os.getenv('FREQUENCY_FATIGUE_THRESHOLD', '3.0')),
                spend_variance_threshold=float(os.getenv('SPEND_VARIANCE_THRESHOLD', '0.15')),
                min_diversity_score=int(os.getenv('MIN_DIVERSITY_SCORE', '5')),
                low_roas_spend_threshold=float(os.getenv('LOW_ROAS_SPEND_THRESHOLD', '50')),
            )
        )

        # Load client configurations
        client_mappings = {
            'moussse': ('MOUSSSE_AD_ACCOUNT', 'Moussse'),
            'storelli': ('STORELLI_AD_ACCOUNT', 'Storelli'),
            'feelnuyu': ('FEELNUYU_AD_ACCOUNT', 'FeelNuyu'),
            'luna_daily': ('LUNA_DAILY_AD_ACCOUNT', 'Luna Daily'),
            'designrr': ('DESIGNRR_AD_ACCOUNT', 'Designrr'),
            'selftalk_plus': ('SELFTALK_PLUS_AD_ACCOUNT', 'SelfTalk Plus'),
            'belong': ('BELONG_AD_ACCOUNT', 'Belong'),
            'celebrate_recovery': ('CELEBRATE_RECOVERY_AD_ACCOUNT', 'Celebrate Recovery Store'),
            'axium_wealth': ('AXIUM_WEALTH_AD_ACCOUNT', 'Axium Wealth'),
        }

        for key, (env_var, display_name) in client_mappings.items():
            ad_account = os.getenv(env_var, '')
            if ad_account and ad_account != 'act_XXXXXXXXXX':
                config.clients[key] = ClientConfig(
                    name=key,
                    display_name=display_name,
                    ad_account_id=ad_account
                )

        return config

    def is_meta_configured(self) -> bool:
        """Check if Meta API is configured"""
        return bool(self.meta_access_token and self.meta_access_token != 'your_access_token_here')

    def get_configured_clients(self) -> Dict[str, ClientConfig]:
        """Get only clients with valid ad account IDs"""
        return {k: v for k, v in self.clients.items() if v.ad_account_id}


# Global config instance
config = Config.load_from_env()


# Creative concepts available for tagging
CREATIVE_CONCEPTS = [
    "Pattern Interrupt",
    "Education/Science",
    "Social Proof",
    "Comparison",
    "Testimonial",
    "Urgency/Scarcity",
    "Objection Handling",
    "Stats/Data",
    "Story/Narrative",
    "Before/After",
    "Demonstration",
    "Authority/Expert",
    "Fear of Missing Out",
    "Aspirational",
    "Problem Agitation",
    "Humor",
    "Controversy",
    "Behind the Scenes",
    "User Generated Content",
    "Founder Story",
]

# Awareness levels (Schwartz)
AWARENESS_LEVELS = {
    1: "Unaware",
    2: "Problem Aware",
    3: "Solution Aware",
    4: "Product Aware",
    5: "Most Aware",
}

# Default ad fields to fetch from Meta API
META_AD_FIELDS = [
    'id',
    'name',
    'status',
    'effective_status',
    'created_time',
    'creative',
    'adset_id',
    'campaign_id',
]

META_INSIGHTS_FIELDS = [
    'spend',
    'impressions',
    'reach',
    'frequency',
    'clicks',
    'ctr',
    'cpc',
    'cpm',
    'actions',
    'action_values',
    'conversions',
    'cost_per_action_type',
    'purchase_roas',
]

META_CREATIVE_FIELDS = [
    'id',
    'name',
    'object_type',
    'thumbnail_url',
    'image_url',
    'video_id',
    'effective_object_story_id',
]
