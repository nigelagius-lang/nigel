"""
Meta Marketing API Integration

Connects to Meta (Facebook) Marketing API to pull ad performance data.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.exceptions import FacebookRequestError

from .config import config, META_AD_FIELDS, META_INSIGHTS_FIELDS, META_CREATIVE_FIELDS

logger = logging.getLogger(__name__)


@dataclass
class AdData:
    """Structured ad data from Meta API"""
    ad_id: str
    ad_name: str
    campaign_id: str
    campaign_name: str
    adset_id: str
    adset_name: str
    status: str
    effective_status: str
    created_time: Optional[datetime]
    creative_id: Optional[str]
    creative_format: Optional[str]
    creative_url: Optional[str]
    thumbnail_url: Optional[str]


@dataclass
class InsightsData:
    """Ad performance insights from Meta API"""
    ad_id: str
    date_start: str
    date_stop: str
    spend: float = 0.0
    impressions: int = 0
    reach: int = 0
    frequency: float = 0.0
    clicks: int = 0
    ctr: float = 0.0
    cpc: float = 0.0
    cpm: float = 0.0
    conversions: int = 0
    conversion_value: float = 0.0
    roas: float = 0.0
    cpa: float = 0.0
    actions: Optional[Dict] = None
    action_values: Optional[Dict] = None


class MetaAPIError(Exception):
    """Custom exception for Meta API errors"""
    pass


class MetaAPIClient:
    """Client for interacting with Meta Marketing API"""

    def __init__(self, access_token: Optional[str] = None):
        """Initialize the Meta API client"""
        self.access_token = access_token or config.meta_access_token
        self._api = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize the Facebook Ads API"""
        if not self.access_token:
            logger.error("No Meta access token provided")
            return False

        try:
            self._api = FacebookAdsApi.init(access_token=self.access_token)
            self._initialized = True
            logger.info("Meta API initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Meta API: {e}")
            return False

    def test_connection(self, ad_account_id: str) -> Tuple[bool, str]:
        """Test API connection with a specific ad account"""
        if not self._initialized:
            if not self.initialize():
                return False, "Failed to initialize API"

        try:
            account = AdAccount(ad_account_id)
            # Try to fetch basic account info
            account_info = account.api_get(fields=['name', 'account_status', 'currency'])
            return True, f"Connected to: {account_info.get('name', 'Unknown')} ({account_info.get('currency', 'N/A')})"
        except FacebookRequestError as e:
            error_msg = f"API Error: {e.api_error_message()}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Connection failed: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

    def get_account_info(self, ad_account_id: str) -> Optional[Dict[str, Any]]:
        """Get basic ad account information"""
        if not self._initialized:
            self.initialize()

        try:
            account = AdAccount(ad_account_id)
            info = account.api_get(fields=[
                'name',
                'account_status',
                'currency',
                'timezone_name',
                'amount_spent',
                'balance',
            ])
            return dict(info)
        except FacebookRequestError as e:
            logger.error(f"Failed to get account info for {ad_account_id}: {e.api_error_message()}")
            return None

    def get_ads(self, ad_account_id: str, status_filter: Optional[List[str]] = None) -> List[AdData]:
        """Fetch all ads from an ad account"""
        if not self._initialized:
            self.initialize()

        try:
            account = AdAccount(ad_account_id)

            params = {
                'limit': 500,  # Max per request
            }
            if status_filter:
                params['filtering'] = [{'field': 'effective_status', 'operator': 'IN', 'value': status_filter}]

            fields = [
                'id',
                'name',
                'status',
                'effective_status',
                'created_time',
                'creative{id,name,object_type,thumbnail_url,image_url,video_id}',
                'adset_id',
                'campaign_id',
            ]

            ads = account.get_ads(fields=fields, params=params)
            result = []

            for ad in ads:
                # Get campaign and adset names
                campaign_name = ""
                adset_name = ""

                try:
                    campaign = Campaign(ad.get('campaign_id'))
                    campaign_info = campaign.api_get(fields=['name'])
                    campaign_name = campaign_info.get('name', '')
                except:
                    pass

                try:
                    adset = AdSet(ad.get('adset_id'))
                    adset_info = adset.api_get(fields=['name'])
                    adset_name = adset_info.get('name', '')
                except:
                    pass

                # Parse creative info
                creative = ad.get('creative', {})
                creative_id = creative.get('id') if creative else None
                creative_format = creative.get('object_type') if creative else None
                creative_url = creative.get('image_url') or creative.get('thumbnail_url') if creative else None
                thumbnail_url = creative.get('thumbnail_url') if creative else None

                # Parse created time
                created_time = None
                if ad.get('created_time'):
                    try:
                        created_time = datetime.fromisoformat(ad['created_time'].replace('Z', '+00:00'))
                    except:
                        pass

                result.append(AdData(
                    ad_id=ad['id'],
                    ad_name=ad.get('name', ''),
                    campaign_id=ad.get('campaign_id', ''),
                    campaign_name=campaign_name,
                    adset_id=ad.get('adset_id', ''),
                    adset_name=adset_name,
                    status=ad.get('status', ''),
                    effective_status=ad.get('effective_status', ''),
                    created_time=created_time,
                    creative_id=creative_id,
                    creative_format=creative_format,
                    creative_url=creative_url,
                    thumbnail_url=thumbnail_url,
                ))

            logger.info(f"Fetched {len(result)} ads from {ad_account_id}")
            return result

        except FacebookRequestError as e:
            logger.error(f"Failed to fetch ads from {ad_account_id}: {e.api_error_message()}")
            raise MetaAPIError(f"Failed to fetch ads: {e.api_error_message()}")

    def get_ads_insights(
        self,
        ad_account_id: str,
        date_start: Optional[str] = None,
        date_stop: Optional[str] = None,
        level: str = 'ad',
        time_increment: int = 1,  # Daily by default
    ) -> List[InsightsData]:
        """Fetch ad insights/performance data"""
        if not self._initialized:
            self.initialize()

        # Default to last 30 days if no dates provided
        if not date_start:
            date_start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        if not date_stop:
            date_stop = datetime.now().strftime('%Y-%m-%d')

        try:
            account = AdAccount(ad_account_id)

            params = {
                'level': level,
                'time_range': {
                    'since': date_start,
                    'until': date_stop,
                },
                'time_increment': time_increment,
                'limit': 1000,
            }

            fields = [
                'ad_id',
                'ad_name',
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

            insights = account.get_insights(fields=fields, params=params)
            result = []

            for insight in insights:
                # Extract conversions (purchases)
                conversions = 0
                conversion_value = 0.0
                actions = insight.get('actions', [])
                action_values = insight.get('action_values', [])

                if actions:
                    for action in actions:
                        if action.get('action_type') == 'purchase':
                            conversions = int(action.get('value', 0))
                            break
                        elif action.get('action_type') == 'omni_purchase':
                            conversions = int(action.get('value', 0))
                            break

                if action_values:
                    for av in action_values:
                        if av.get('action_type') == 'purchase':
                            conversion_value = float(av.get('value', 0))
                            break
                        elif av.get('action_type') == 'omni_purchase':
                            conversion_value = float(av.get('value', 0))
                            break

                # Calculate ROAS
                spend = float(insight.get('spend', 0))
                roas = 0.0
                if spend > 0 and conversion_value > 0:
                    roas = conversion_value / spend

                # Also check purchase_roas field
                purchase_roas = insight.get('purchase_roas', [])
                if purchase_roas and isinstance(purchase_roas, list):
                    for pr in purchase_roas:
                        if pr.get('action_type') in ['purchase', 'omni_purchase']:
                            roas = float(pr.get('value', roas))
                            break

                # Calculate CPA
                cpa = 0.0
                if conversions > 0:
                    cpa = spend / conversions

                result.append(InsightsData(
                    ad_id=insight.get('ad_id', ''),
                    date_start=insight.get('date_start', date_start),
                    date_stop=insight.get('date_stop', date_stop),
                    spend=spend,
                    impressions=int(insight.get('impressions', 0)),
                    reach=int(insight.get('reach', 0)),
                    frequency=float(insight.get('frequency', 0)),
                    clicks=int(insight.get('clicks', 0)),
                    ctr=float(insight.get('ctr', 0)),
                    cpc=float(insight.get('cpc', 0)),
                    cpm=float(insight.get('cpm', 0)),
                    conversions=conversions,
                    conversion_value=conversion_value,
                    roas=roas,
                    cpa=cpa,
                    actions=actions,
                    action_values=action_values,
                ))

            logger.info(f"Fetched {len(result)} insight records from {ad_account_id}")
            return result

        except FacebookRequestError as e:
            logger.error(f"Failed to fetch insights from {ad_account_id}: {e.api_error_message()}")
            raise MetaAPIError(f"Failed to fetch insights: {e.api_error_message()}")

    def get_ad_creatives(self, ad_account_id: str) -> List[Dict[str, Any]]:
        """Fetch all ad creatives from an account"""
        if not self._initialized:
            self.initialize()

        try:
            account = AdAccount(ad_account_id)

            fields = [
                'id',
                'name',
                'object_type',
                'thumbnail_url',
                'image_url',
                'video_id',
                'effective_object_story_id',
                'title',
                'body',
            ]

            creatives = account.get_ad_creatives(fields=fields)
            result = [dict(c) for c in creatives]

            logger.info(f"Fetched {len(result)} creatives from {ad_account_id}")
            return result

        except FacebookRequestError as e:
            logger.error(f"Failed to fetch creatives from {ad_account_id}: {e.api_error_message()}")
            raise MetaAPIError(f"Failed to fetch creatives: {e.api_error_message()}")

    def get_lifetime_spend(self, ad_account_id: str, ad_id: str) -> float:
        """Get lifetime spend for a specific ad"""
        if not self._initialized:
            self.initialize()

        try:
            ad = Ad(ad_id)
            insights = ad.get_insights(
                fields=['spend'],
                params={'date_preset': 'lifetime'}
            )

            if insights:
                return float(insights[0].get('spend', 0))
            return 0.0

        except FacebookRequestError as e:
            logger.warning(f"Failed to get lifetime spend for ad {ad_id}: {e.api_error_message()}")
            return 0.0


class MetaDataSync:
    """Synchronize Meta API data with local database"""

    def __init__(self, api_client: MetaAPIClient, db_manager):
        self.api = api_client
        self.db = db_manager

    def sync_client_data(
        self,
        client_name: str,
        ad_account_id: str,
        date_start: Optional[str] = None,
        date_stop: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sync all data for a specific client"""
        from .database import AdPerformance, DataSyncLog, Client

        session = self.db.get_session()
        sync_log = DataSyncLog(
            client_name=client_name,
            sync_type='full',
            status='in_progress',
        )
        session.add(sync_log)
        session.commit()

        try:
            # Get or create client
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                # Client should already exist, but create if not
                from .config import config
                client_config = config.clients.get(client_name)
                display_name = client_config.display_name if client_config else client_name
                client = Client(
                    name=client_name,
                    display_name=display_name,
                    ad_account_id=ad_account_id
                )
                session.add(client)
                session.commit()

            # Fetch ads
            ads = self.api.get_ads(ad_account_id)

            # Fetch insights
            insights = self.api.get_ads_insights(
                ad_account_id,
                date_start=date_start,
                date_stop=date_stop,
            )

            # Create lookup for insights by ad_id and date
            insights_lookup = {}
            for insight in insights:
                key = (insight.ad_id, insight.date_start)
                insights_lookup[key] = insight

            # Create lookup for ads
            ads_lookup = {ad.ad_id: ad for ad in ads}

            # Store performance data
            records_stored = 0
            for insight in insights:
                ad_data = ads_lookup.get(insight.ad_id)

                # Check if record already exists
                existing = session.query(AdPerformance).filter(
                    AdPerformance.client_id == client.id,
                    AdPerformance.ad_id == insight.ad_id,
                    AdPerformance.date == datetime.strptime(insight.date_start, '%Y-%m-%d').date()
                ).first()

                if existing:
                    # Update existing record
                    existing.spend = insight.spend
                    existing.impressions = insight.impressions
                    existing.reach = insight.reach
                    existing.frequency = insight.frequency
                    existing.clicks = insight.clicks
                    existing.ctr = insight.ctr
                    existing.cpc = insight.cpc
                    existing.cpm = insight.cpm
                    existing.conversions = insight.conversions
                    existing.conversion_value = insight.conversion_value
                    existing.roas = insight.roas
                    existing.cpa = insight.cpa
                    existing.actions = insight.actions
                    existing.action_values = insight.action_values
                    existing.fetched_at = datetime.utcnow()
                else:
                    # Create new record
                    perf = AdPerformance(
                        client_id=client.id,
                        date=datetime.strptime(insight.date_start, '%Y-%m-%d').date(),
                        ad_id=insight.ad_id,
                        ad_name=ad_data.ad_name if ad_data else '',
                        campaign_id=ad_data.campaign_id if ad_data else '',
                        campaign_name=ad_data.campaign_name if ad_data else '',
                        adset_id=ad_data.adset_id if ad_data else '',
                        adset_name=ad_data.adset_name if ad_data else '',
                        status=ad_data.status if ad_data else '',
                        effective_status=ad_data.effective_status if ad_data else '',
                        creative_id=ad_data.creative_id if ad_data else None,
                        creative_format=ad_data.creative_format if ad_data else None,
                        creative_url=ad_data.creative_url if ad_data else None,
                        thumbnail_url=ad_data.thumbnail_url if ad_data else None,
                        created_time=ad_data.created_time if ad_data else None,
                        spend=insight.spend,
                        impressions=insight.impressions,
                        reach=insight.reach,
                        frequency=insight.frequency,
                        clicks=insight.clicks,
                        ctr=insight.ctr,
                        cpc=insight.cpc,
                        cpm=insight.cpm,
                        conversions=insight.conversions,
                        conversion_value=insight.conversion_value,
                        roas=insight.roas,
                        cpa=insight.cpa,
                        actions=insight.actions,
                        action_values=insight.action_values,
                    )
                    session.add(perf)
                    records_stored += 1

            session.commit()

            # Update sync log
            sync_log.status = 'success'
            sync_log.records_fetched = len(insights)
            sync_log.records_stored = records_stored
            sync_log.completed_at = datetime.utcnow()
            session.commit()

            logger.info(f"Synced {records_stored} records for {client_name}")
            return {
                'status': 'success',
                'client': client_name,
                'ads_fetched': len(ads),
                'insights_fetched': len(insights),
                'records_stored': records_stored,
            }

        except Exception as e:
            sync_log.status = 'failed'
            sync_log.error_message = str(e)
            sync_log.completed_at = datetime.utcnow()
            session.commit()
            logger.error(f"Sync failed for {client_name}: {e}")
            raise

        finally:
            session.close()

    def sync_all_clients(
        self,
        date_start: Optional[str] = None,
        date_stop: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Sync data for all configured clients"""
        results = []

        for client_key, client_config in config.get_configured_clients().items():
            try:
                result = self.sync_client_data(
                    client_name=client_key,
                    ad_account_id=client_config.ad_account_id,
                    date_start=date_start,
                    date_stop=date_stop,
                )
                results.append(result)
            except Exception as e:
                results.append({
                    'status': 'failed',
                    'client': client_key,
                    'error': str(e),
                })

        return results
