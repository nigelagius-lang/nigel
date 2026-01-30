"""
Tagging Interface Utilities

Handles CSV upload, template generation, and tag management.
"""
import io
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

import pandas as pd
from sqlalchemy import func

from .database import DatabaseManager, Client, CreativeTag, AdPerformance, ClientFramework
from .config import AWARENESS_LEVELS, CREATIVE_CONCEPTS


class TaggingManager:
    """Manage creative tagging operations"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def generate_tagging_template(
        self,
        client_name: str,
        include_existing_tags: bool = True,
    ) -> pd.DataFrame:
        """
        Generate a tagging template for a specific client.

        Returns a DataFrame with columns:
        - client, ad_id, ad_name, campaign_name, adset_name, creative_url
        - awareness_level, hypothesis, sub_hypothesis, creative_concept, notes
        """
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return pd.DataFrame()

            # Get all unique ads for this client
            ads = session.query(
                AdPerformance.ad_id,
                AdPerformance.ad_name,
                AdPerformance.campaign_name,
                AdPerformance.adset_name,
                AdPerformance.creative_url,
                AdPerformance.thumbnail_url,
                AdPerformance.creative_format,
            ).filter(
                AdPerformance.client_id == client.id,
            ).distinct(AdPerformance.ad_id).all()

            # Get existing tags
            existing_tags = {}
            if include_existing_tags:
                tags = session.query(CreativeTag).filter(
                    CreativeTag.client_id == client.id,
                ).all()
                for tag in tags:
                    existing_tags[tag.ad_id] = {
                        'awareness_level': tag.awareness_level,
                        'hypothesis': tag.hypothesis,
                        'sub_hypothesis': tag.sub_hypothesis,
                        'creative_concept': tag.creative_concept,
                        'notes': tag.notes,
                    }

            # Get client framework for reference
            framework = session.query(ClientFramework).filter(
                ClientFramework.client_id == client.id
            ).order_by(ClientFramework.awareness_level).all()

            # Build framework reference text
            framework_ref = []
            for f in framework:
                framework_ref.append(f"Level {f.awareness_level}: {f.level_name}")
                if f.sub_hypotheses:
                    for i, sh in enumerate(f.sub_hypotheses, 1):
                        framework_ref.append(f"  {f.awareness_level}.{i}: {sh}")

            data = []
            for ad in ads:
                existing = existing_tags.get(ad.ad_id, {})
                data.append({
                    'client': client.display_name,
                    'ad_id': ad.ad_id,
                    'ad_name': ad.ad_name or '',
                    'campaign_name': ad.campaign_name or '',
                    'adset_name': ad.adset_name or '',
                    'creative_url': ad.creative_url or ad.thumbnail_url or '',
                    'creative_format': ad.creative_format or '',
                    'awareness_level': existing.get('awareness_level', ''),
                    'hypothesis': existing.get('hypothesis', ''),
                    'sub_hypothesis': existing.get('sub_hypothesis', ''),
                    'creative_concept': existing.get('creative_concept', ''),
                    'notes': existing.get('notes', ''),
                })

            df = pd.DataFrame(data)

            # Sort by campaign then ad name
            if not df.empty:
                df = df.sort_values(['campaign_name', 'ad_name'])

            return df

        finally:
            session.close()

    def generate_blank_template(self) -> pd.DataFrame:
        """Generate a blank template with just column headers"""
        columns = [
            'client',
            'ad_id',
            'ad_name',
            'campaign_name',
            'adset_name',
            'creative_url',
            'awareness_level',
            'hypothesis',
            'sub_hypothesis',
            'creative_concept',
            'notes',
        ]
        return pd.DataFrame(columns=columns)

    def generate_reference_sheet(self, client_name: str) -> str:
        """
        Generate a reference sheet with the client's framework.
        Returns markdown-formatted text.
        """
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return "Client not found"

            frameworks = session.query(ClientFramework).filter(
                ClientFramework.client_id == client.id
            ).order_by(ClientFramework.awareness_level).all()

            lines = [
                f"# Tagging Reference: {client.display_name}",
                "",
                "## Awareness Levels & Hypotheses",
                "",
            ]

            for f in frameworks:
                lines.append(f"### Level {f.awareness_level}: {f.level_name}")
                lines.append(f"**Hypothesis:** {f.hypothesis}")
                if f.sub_hypotheses:
                    lines.append("")
                    lines.append("**Sub-hypotheses:**")
                    for sh in f.sub_hypotheses:
                        lines.append(f"- {sh}")
                lines.append("")

            lines.append("## Creative Concepts")
            lines.append("")
            for concept in CREATIVE_CONCEPTS:
                lines.append(f"- {concept}")

            return "\n".join(lines)

        finally:
            session.close()

    def validate_tags_df(self, df: pd.DataFrame) -> Tuple[bool, List[str]]:
        """
        Validate a tags DataFrame before import.
        Returns (is_valid, list_of_errors).
        """
        errors = []

        # Check required columns
        required_cols = ['ad_id', 'awareness_level']
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            errors.append(f"Missing required columns: {', '.join(missing_cols)}")
            return False, errors

        # Check for empty ad_ids
        empty_ad_ids = df['ad_id'].isna() | (df['ad_id'] == '')
        if empty_ad_ids.any():
            errors.append(f"{empty_ad_ids.sum()} rows have empty ad_id")

        # Validate awareness levels (1-5 or empty)
        if 'awareness_level' in df.columns:
            for idx, row in df.iterrows():
                level = row.get('awareness_level')
                if pd.notna(level) and level != '':
                    try:
                        level_int = int(level)
                        if level_int < 1 or level_int > 5:
                            errors.append(f"Row {idx+2}: awareness_level must be 1-5, got {level}")
                    except (ValueError, TypeError):
                        errors.append(f"Row {idx+2}: invalid awareness_level '{level}'")

        # Validate creative concepts
        if 'creative_concept' in df.columns:
            valid_concepts = set(CREATIVE_CONCEPTS)
            for idx, row in df.iterrows():
                concept = row.get('creative_concept')
                if pd.notna(concept) and concept != '' and concept not in valid_concepts:
                    # Warn but don't fail - might be custom concept
                    errors.append(f"Row {idx+2}: unknown creative_concept '{concept}' (warning)")

        is_valid = len([e for e in errors if 'warning' not in e.lower()]) == 0
        return is_valid, errors

    def import_tags_from_csv(
        self,
        csv_content: str,
        update_existing: bool = True,
    ) -> Tuple[int, int, List[str]]:
        """
        Import tags from CSV content.
        Returns (success_count, error_count, error_messages).
        """
        try:
            df = pd.read_csv(io.StringIO(csv_content))
        except Exception as e:
            return 0, 0, [f"Failed to parse CSV: {str(e)}"]

        return self.import_tags_from_df(df, update_existing)

    def import_tags_from_df(
        self,
        df: pd.DataFrame,
        update_existing: bool = True,
    ) -> Tuple[int, int, List[str]]:
        """
        Import tags from a DataFrame.
        Returns (success_count, error_count, error_messages).
        """
        # Validate first
        is_valid, validation_errors = self.validate_tags_df(df)
        if not is_valid:
            return 0, len(df), validation_errors

        session = self.db.get_session()
        success_count = 0
        error_count = 0
        errors = validation_errors.copy()  # Include warnings

        try:
            # Cache client lookups
            clients_cache = {}

            for idx, row in df.iterrows():
                try:
                    # Get client
                    client_name = str(row.get('client', '')).strip()
                    client_key = client_name.lower().replace(' ', '_')

                    if client_key not in clients_cache:
                        # Try exact match first, then partial
                        client = session.query(Client).filter(
                            Client.name == client_key
                        ).first()
                        if not client:
                            client = session.query(Client).filter(
                                Client.display_name.ilike(f"%{client_name}%")
                            ).first()
                        clients_cache[client_key] = client

                    client = clients_cache.get(client_key)
                    if not client:
                        errors.append(f"Row {idx+2}: Client '{client_name}' not found")
                        error_count += 1
                        continue

                    ad_id = str(row['ad_id']).strip()

                    # Parse awareness level
                    awareness_level = None
                    if pd.notna(row.get('awareness_level')) and row.get('awareness_level') != '':
                        awareness_level = int(row['awareness_level'])

                    # Check if tag exists
                    existing = session.query(CreativeTag).filter(
                        CreativeTag.client_id == client.id,
                        CreativeTag.ad_id == ad_id,
                    ).first()

                    if existing:
                        if update_existing:
                            if awareness_level is not None:
                                existing.awareness_level = awareness_level
                            if pd.notna(row.get('hypothesis')):
                                existing.hypothesis = str(row['hypothesis'])
                            if pd.notna(row.get('sub_hypothesis')):
                                existing.sub_hypothesis = str(row['sub_hypothesis'])
                            if pd.notna(row.get('creative_concept')):
                                existing.creative_concept = str(row['creative_concept'])
                            if pd.notna(row.get('notes')):
                                existing.notes = str(row['notes'])
                            existing.updated_at = datetime.utcnow()
                    else:
                        tag = CreativeTag(
                            client_id=client.id,
                            ad_id=ad_id,
                            awareness_level=awareness_level,
                            hypothesis=str(row.get('hypothesis', '')) if pd.notna(row.get('hypothesis')) else None,
                            sub_hypothesis=str(row.get('sub_hypothesis', '')) if pd.notna(row.get('sub_hypothesis')) else None,
                            creative_concept=str(row.get('creative_concept', '')) if pd.notna(row.get('creative_concept')) else None,
                            notes=str(row.get('notes', '')) if pd.notna(row.get('notes')) else None,
                        )
                        session.add(tag)

                    success_count += 1

                except Exception as e:
                    errors.append(f"Row {idx+2}: {str(e)}")
                    error_count += 1

            session.commit()
            return success_count, error_count, errors

        except Exception as e:
            session.rollback()
            return 0, len(df), [f"Import failed: {str(e)}"]

        finally:
            session.close()

    def export_tags_to_csv(self, client_name: Optional[str] = None) -> str:
        """Export all tags to CSV format"""
        session = self.db.get_session()
        try:
            query = session.query(
                Client.display_name.label('client'),
                CreativeTag.ad_id,
                CreativeTag.awareness_level,
                CreativeTag.hypothesis,
                CreativeTag.sub_hypothesis,
                CreativeTag.creative_concept,
                CreativeTag.notes,
                CreativeTag.tagged_at,
            ).join(Client)

            if client_name:
                query = query.filter(Client.name == client_name)

            results = query.all()

            data = []
            for r in results:
                data.append({
                    'client': r.client,
                    'ad_id': r.ad_id,
                    'awareness_level': r.awareness_level,
                    'hypothesis': r.hypothesis or '',
                    'sub_hypothesis': r.sub_hypothesis or '',
                    'creative_concept': r.creative_concept or '',
                    'notes': r.notes or '',
                    'tagged_at': r.tagged_at.strftime('%Y-%m-%d %H:%M') if r.tagged_at else '',
                })

            df = pd.DataFrame(data)
            return df.to_csv(index=False)

        finally:
            session.close()

    def get_untagged_ads(self, client_name: str) -> pd.DataFrame:
        """Get list of ads that haven't been tagged yet"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return pd.DataFrame()

            # Get all ad IDs that have tags
            tagged_ids = session.query(CreativeTag.ad_id).filter(
                CreativeTag.client_id == client.id,
            ).subquery()

            # Get ads without tags
            untagged = session.query(
                AdPerformance.ad_id,
                AdPerformance.ad_name,
                AdPerformance.campaign_name,
                AdPerformance.adset_name,
                AdPerformance.creative_url,
            ).filter(
                AdPerformance.client_id == client.id,
                ~AdPerformance.ad_id.in_(tagged_ids),
            ).distinct(AdPerformance.ad_id).all()

            data = []
            for ad in untagged:
                data.append({
                    'ad_id': ad.ad_id,
                    'ad_name': ad.ad_name or '',
                    'campaign_name': ad.campaign_name or '',
                    'adset_name': ad.adset_name or '',
                    'creative_url': ad.creative_url or '',
                })

            return pd.DataFrame(data)

        finally:
            session.close()

    def get_tagging_progress(self, client_name: str) -> Dict[str, Any]:
        """Get tagging progress for a client"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return {}

            total_ads = session.query(
                func.count(func.distinct(AdPerformance.ad_id))
            ).filter(
                AdPerformance.client_id == client.id,
            ).scalar() or 0

            tagged_ads = session.query(
                func.count(CreativeTag.id)
            ).filter(
                CreativeTag.client_id == client.id,
            ).scalar() or 0

            # Count by awareness level
            level_counts = {}
            for level in range(1, 6):
                count = session.query(func.count(CreativeTag.id)).filter(
                    CreativeTag.client_id == client.id,
                    CreativeTag.awareness_level == level,
                ).scalar() or 0
                level_counts[level] = count

            return {
                'client_name': client.display_name,
                'total_ads': total_ads,
                'tagged_ads': tagged_ads,
                'untagged_ads': total_ads - tagged_ads,
                'completion_percentage': (tagged_ads / total_ads * 100) if total_ads > 0 else 0,
                'by_level': level_counts,
            }

        finally:
            session.close()
