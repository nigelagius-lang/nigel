"""
Andromeda Diversity Analyzer

Tracks creative diversity based on Eugene Schwartz's 5 awareness levels
with custom frameworks per client.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

import pandas as pd
from sqlalchemy import func

from .database import DatabaseManager, Client, ClientFramework, CreativeTag, AdPerformance
from .config import config, AWARENESS_LEVELS, CREATIVE_CONCEPTS


@dataclass
class AwarenessDistribution:
    """Distribution of creatives across awareness levels"""
    level_counts: Dict[int, int]
    level_percentages: Dict[int, float]
    total_tagged: int
    total_untagged: int
    diversity_score: float  # 0-10
    alerts: List[str]


@dataclass
class HypothesisCoverage:
    """Coverage of hypotheses within an awareness level"""
    level: int
    level_name: str
    hypothesis: str
    sub_hypotheses_total: int
    sub_hypotheses_tested: int
    sub_hypotheses_missing: List[str]
    coverage_percentage: float


@dataclass
class ConceptDiversity:
    """Diversity of creative concepts within a level"""
    level: int
    level_name: str
    concept_counts: Dict[str, int]
    total_concepts_used: int
    dominant_concept: Optional[str]
    missing_concepts: List[str]
    is_over_indexed: bool  # One concept > 50%


@dataclass
class PerformanceByLevel:
    """Performance metrics grouped by awareness level"""
    level: int
    level_name: str
    creative_count: int
    total_spend: float
    avg_roas: float
    avg_ctr: float
    avg_cpa: float
    best_sub_hypothesis: Optional[str]
    best_sub_hypothesis_roas: float


@dataclass
class GapRecommendation:
    """Auto-generated recommendation for creative gaps"""
    client_name: str
    priority: str  # high, medium, low
    recommendation_type: str
    message: str
    details: Optional[str]


class DiversityAnalyzer:
    """Analyze creative diversity using awareness frameworks"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    # =========================================
    # Framework Management
    # =========================================

    def get_client_framework(self, client_name: str) -> List[Dict[str, Any]]:
        """Get the awareness framework for a specific client"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return []

            frameworks = session.query(ClientFramework).filter(
                ClientFramework.client_id == client.id
            ).order_by(ClientFramework.awareness_level).all()

            return [{
                'awareness_level': f.awareness_level,
                'level_name': f.level_name,
                'hypothesis': f.hypothesis,
                'sub_hypotheses': f.sub_hypotheses or [],
            } for f in frameworks]

        finally:
            session.close()

    def save_client_framework(
        self,
        client_name: str,
        framework_data: List[Dict[str, Any]]
    ) -> bool:
        """Save or update client awareness framework"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return False

            # Delete existing framework
            session.query(ClientFramework).filter(
                ClientFramework.client_id == client.id
            ).delete()

            # Insert new framework
            for level_data in framework_data:
                framework = ClientFramework(
                    client_id=client.id,
                    awareness_level=level_data['awareness_level'],
                    level_name=level_data.get('level_name', AWARENESS_LEVELS.get(level_data['awareness_level'], '')),
                    hypothesis=level_data['hypothesis'],
                    sub_hypotheses=level_data.get('sub_hypotheses', []),
                )
                session.add(framework)

            session.commit()
            return True

        except Exception as e:
            session.rollback()
            raise

        finally:
            session.close()

    def get_framework_dropdowns(self, client_name: str) -> Dict[str, List[str]]:
        """Get dropdown options for tagging based on client framework"""
        framework = self.get_client_framework(client_name)

        dropdowns = {
            'awareness_levels': [],
            'hypotheses': {},
            'sub_hypotheses': {},
            'creative_concepts': CREATIVE_CONCEPTS,
        }

        for level in framework:
            level_key = f"Level {level['awareness_level']}: {level['level_name']}"
            dropdowns['awareness_levels'].append(level_key)
            dropdowns['hypotheses'][level['awareness_level']] = level['hypothesis']
            dropdowns['sub_hypotheses'][level['awareness_level']] = level['sub_hypotheses']

        return dropdowns

    # =========================================
    # Tagging Operations
    # =========================================

    def tag_creative(
        self,
        client_name: str,
        ad_id: str,
        awareness_level: int,
        hypothesis: str,
        sub_hypothesis: str,
        creative_concept: str,
        notes: Optional[str] = None,
        tagged_by: Optional[str] = None,
    ) -> bool:
        """Tag a creative with awareness data"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return False

            # Check if tag exists
            existing = session.query(CreativeTag).filter(
                CreativeTag.client_id == client.id,
                CreativeTag.ad_id == ad_id,
            ).first()

            if existing:
                existing.awareness_level = awareness_level
                existing.hypothesis = hypothesis
                existing.sub_hypothesis = sub_hypothesis
                existing.creative_concept = creative_concept
                existing.notes = notes
                existing.tagged_by = tagged_by
                existing.updated_at = datetime.utcnow()
            else:
                tag = CreativeTag(
                    client_id=client.id,
                    ad_id=ad_id,
                    awareness_level=awareness_level,
                    hypothesis=hypothesis,
                    sub_hypothesis=sub_hypothesis,
                    creative_concept=creative_concept,
                    notes=notes,
                    tagged_by=tagged_by,
                )
                session.add(tag)

            session.commit()
            return True

        except Exception:
            session.rollback()
            return False

        finally:
            session.close()

    def bulk_import_tags(self, tags_df: pd.DataFrame) -> Tuple[int, int, List[str]]:
        """
        Bulk import tags from a DataFrame.
        Expected columns: client, ad_id, awareness_level, hypothesis, sub_hypothesis, creative_concept
        Returns: (success_count, error_count, error_messages)
        """
        session = self.db.get_session()
        success_count = 0
        error_count = 0
        errors = []

        try:
            # Cache client lookups
            clients_cache = {}
            for _, row in tags_df.iterrows():
                try:
                    client_name = str(row.get('client', '')).strip().lower().replace(' ', '_')

                    if client_name not in clients_cache:
                        client = session.query(Client).filter(Client.name == client_name).first()
                        clients_cache[client_name] = client

                    client = clients_cache.get(client_name)
                    if not client:
                        errors.append(f"Client not found: {row.get('client')}")
                        error_count += 1
                        continue

                    ad_id = str(row['ad_id'])
                    awareness_level = int(row.get('awareness_level', 0))

                    # Check if tag exists
                    existing = session.query(CreativeTag).filter(
                        CreativeTag.client_id == client.id,
                        CreativeTag.ad_id == ad_id,
                    ).first()

                    if existing:
                        existing.awareness_level = awareness_level
                        existing.hypothesis = str(row.get('hypothesis', ''))
                        existing.sub_hypothesis = str(row.get('sub_hypothesis', ''))
                        existing.creative_concept = str(row.get('creative_concept', ''))
                        existing.updated_at = datetime.utcnow()
                    else:
                        tag = CreativeTag(
                            client_id=client.id,
                            ad_id=ad_id,
                            awareness_level=awareness_level,
                            hypothesis=str(row.get('hypothesis', '')),
                            sub_hypothesis=str(row.get('sub_hypothesis', '')),
                            creative_concept=str(row.get('creative_concept', '')),
                        )
                        session.add(tag)

                    success_count += 1

                except Exception as e:
                    errors.append(f"Error on row with ad_id {row.get('ad_id', 'unknown')}: {str(e)}")
                    error_count += 1

            session.commit()
            return success_count, error_count, errors

        except Exception as e:
            session.rollback()
            return 0, len(tags_df), [f"Bulk import failed: {str(e)}"]

        finally:
            session.close()

    def export_tagging_template(self, client_name: str) -> pd.DataFrame:
        """Generate a blank tagging template for a client"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return pd.DataFrame()

            # Get all ads for this client
            ads = session.query(
                AdPerformance.ad_id,
                AdPerformance.ad_name,
                AdPerformance.campaign_name,
                AdPerformance.adset_name,
                AdPerformance.creative_url,
            ).filter(
                AdPerformance.client_id == client.id,
            ).distinct(AdPerformance.ad_id).all()

            # Get existing tags
            existing_tags = {}
            tags = session.query(CreativeTag).filter(
                CreativeTag.client_id == client.id,
            ).all()
            for tag in tags:
                existing_tags[tag.ad_id] = {
                    'awareness_level': tag.awareness_level,
                    'hypothesis': tag.hypothesis,
                    'sub_hypothesis': tag.sub_hypothesis,
                    'creative_concept': tag.creative_concept,
                }

            data = []
            for ad in ads:
                existing = existing_tags.get(ad.ad_id, {})
                data.append({
                    'client': client.display_name,
                    'ad_id': ad.ad_id,
                    'ad_name': ad.ad_name,
                    'campaign_name': ad.campaign_name,
                    'adset_name': ad.adset_name,
                    'creative_url': ad.creative_url or '',
                    'awareness_level': existing.get('awareness_level', ''),
                    'hypothesis': existing.get('hypothesis', ''),
                    'sub_hypothesis': existing.get('sub_hypothesis', ''),
                    'creative_concept': existing.get('creative_concept', ''),
                })

            return pd.DataFrame(data)

        finally:
            session.close()

    # =========================================
    # Analysis Functions
    # =========================================

    def get_awareness_distribution(self, client_name: str) -> AwarenessDistribution:
        """Get distribution of creatives across awareness levels"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return AwarenessDistribution({}, {}, 0, 0, 0.0, [])

            # Count tagged creatives per level
            level_counts = {}
            for level in range(1, 6):
                count = session.query(func.count(CreativeTag.id)).filter(
                    CreativeTag.client_id == client.id,
                    CreativeTag.awareness_level == level,
                ).scalar() or 0
                level_counts[level] = count

            total_tagged = sum(level_counts.values())

            # Count untagged ads
            tagged_ad_ids = session.query(CreativeTag.ad_id).filter(
                CreativeTag.client_id == client.id,
            ).subquery()

            total_untagged = session.query(func.count(func.distinct(AdPerformance.ad_id))).filter(
                AdPerformance.client_id == client.id,
                ~AdPerformance.ad_id.in_(tagged_ad_ids),
            ).scalar() or 0

            # Calculate percentages
            level_percentages = {}
            if total_tagged > 0:
                for level, count in level_counts.items():
                    level_percentages[level] = (count / total_tagged) * 100
            else:
                level_percentages = {level: 0.0 for level in range(1, 6)}

            # Calculate diversity score (0-10)
            # Perfect distribution would be 20% per level
            # Score decreases based on deviation from ideal
            ideal_percentage = 20.0
            total_deviation = sum(abs(pct - ideal_percentage) for pct in level_percentages.values())
            max_deviation = 4 * 100  # Maximum possible deviation
            diversity_score = 10 * (1 - (total_deviation / max_deviation))
            diversity_score = max(0, min(10, diversity_score))

            # Generate alerts
            alerts = []
            target_min = 15
            target_max = 25

            for level, pct in level_percentages.items():
                level_name = AWARENESS_LEVELS.get(level, f"Level {level}")
                if pct < 5 and total_tagged > 10:
                    alerts.append(f"⚠️ Under-covered: Only {pct:.0f}% Level {level} ({level_name})")
                elif pct > 50:
                    alerts.append(f"⚠️ Over-indexed: {pct:.0f}% Level {level} ({level_name})")

            return AwarenessDistribution(
                level_counts=level_counts,
                level_percentages=level_percentages,
                total_tagged=total_tagged,
                total_untagged=total_untagged,
                diversity_score=round(diversity_score, 1),
                alerts=alerts,
            )

        finally:
            session.close()

    def get_hypothesis_coverage(self, client_name: str) -> List[HypothesisCoverage]:
        """Get coverage of hypotheses for each awareness level"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return []

            # Get framework
            frameworks = session.query(ClientFramework).filter(
                ClientFramework.client_id == client.id
            ).all()

            results = []
            for framework in frameworks:
                # Get sub-hypotheses being used
                used_sub_hyp = session.query(func.distinct(CreativeTag.sub_hypothesis)).filter(
                    CreativeTag.client_id == client.id,
                    CreativeTag.awareness_level == framework.awareness_level,
                    CreativeTag.sub_hypothesis.isnot(None),
                    CreativeTag.sub_hypothesis != '',
                ).all()

                used_set = {h[0] for h in used_sub_hyp if h[0]}
                all_sub_hyp = set(framework.sub_hypotheses or [])
                missing = list(all_sub_hyp - used_set)

                coverage_pct = 0.0
                if all_sub_hyp:
                    coverage_pct = (len(used_set) / len(all_sub_hyp)) * 100

                results.append(HypothesisCoverage(
                    level=framework.awareness_level,
                    level_name=framework.level_name,
                    hypothesis=framework.hypothesis,
                    sub_hypotheses_total=len(all_sub_hyp),
                    sub_hypotheses_tested=len(used_set),
                    sub_hypotheses_missing=missing,
                    coverage_percentage=coverage_pct,
                ))

            return results

        finally:
            session.close()

    def get_concept_diversity(self, client_name: str) -> List[ConceptDiversity]:
        """Get diversity of creative concepts within each level"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return []

            results = []
            for level in range(1, 6):
                # Count concepts at this level
                concept_counts = {}
                tags = session.query(CreativeTag).filter(
                    CreativeTag.client_id == client.id,
                    CreativeTag.awareness_level == level,
                    CreativeTag.creative_concept.isnot(None),
                    CreativeTag.creative_concept != '',
                ).all()

                for tag in tags:
                    concept = tag.creative_concept
                    concept_counts[concept] = concept_counts.get(concept, 0) + 1

                total = sum(concept_counts.values())
                used_concepts = set(concept_counts.keys())
                missing = [c for c in CREATIVE_CONCEPTS if c not in used_concepts]

                # Find dominant concept
                dominant = None
                is_over_indexed = False
                if concept_counts:
                    dominant = max(concept_counts, key=concept_counts.get)
                    if total > 0 and (concept_counts[dominant] / total) > 0.5:
                        is_over_indexed = True

                results.append(ConceptDiversity(
                    level=level,
                    level_name=AWARENESS_LEVELS.get(level, f"Level {level}"),
                    concept_counts=concept_counts,
                    total_concepts_used=len(used_concepts),
                    dominant_concept=dominant,
                    missing_concepts=missing[:5],  # Top 5 missing
                    is_over_indexed=is_over_indexed,
                ))

            return results

        finally:
            session.close()

    def get_performance_by_level(self, client_name: str, days: int = 30) -> List[PerformanceByLevel]:
        """Get performance metrics grouped by awareness level"""
        session = self.db.get_session()
        try:
            client = session.query(Client).filter(Client.name == client_name).first()
            if not client:
                return []

            cutoff_date = (datetime.utcnow() - timedelta(days=days)).date()
            results = []

            for level in range(1, 6):
                # Get tagged ads at this level
                tagged_ads = session.query(CreativeTag.ad_id).filter(
                    CreativeTag.client_id == client.id,
                    CreativeTag.awareness_level == level,
                ).all()

                ad_ids = [a[0] for a in tagged_ads]

                if not ad_ids:
                    results.append(PerformanceByLevel(
                        level=level,
                        level_name=AWARENESS_LEVELS.get(level, f"Level {level}"),
                        creative_count=0,
                        total_spend=0.0,
                        avg_roas=0.0,
                        avg_ctr=0.0,
                        avg_cpa=0.0,
                        best_sub_hypothesis=None,
                        best_sub_hypothesis_roas=0.0,
                    ))
                    continue

                # Aggregate performance
                perf = session.query(
                    func.sum(AdPerformance.spend).label('total_spend'),
                    func.avg(AdPerformance.roas).label('avg_roas'),
                    func.avg(AdPerformance.ctr).label('avg_ctr'),
                    func.avg(AdPerformance.cpa).label('avg_cpa'),
                ).filter(
                    AdPerformance.client_id == client.id,
                    AdPerformance.ad_id.in_(ad_ids),
                    AdPerformance.date >= cutoff_date,
                ).first()

                # Find best performing sub-hypothesis
                best_sub_hyp = None
                best_roas = 0.0

                sub_hyp_perf = session.query(
                    CreativeTag.sub_hypothesis,
                    func.avg(AdPerformance.roas).label('avg_roas'),
                ).join(
                    AdPerformance,
                    AdPerformance.ad_id == CreativeTag.ad_id
                ).filter(
                    CreativeTag.client_id == client.id,
                    CreativeTag.awareness_level == level,
                    AdPerformance.date >= cutoff_date,
                ).group_by(CreativeTag.sub_hypothesis).all()

                for sh, roas in sub_hyp_perf:
                    if sh and roas and roas > best_roas:
                        best_roas = roas
                        best_sub_hyp = sh

                results.append(PerformanceByLevel(
                    level=level,
                    level_name=AWARENESS_LEVELS.get(level, f"Level {level}"),
                    creative_count=len(ad_ids),
                    total_spend=float(perf.total_spend or 0),
                    avg_roas=float(perf.avg_roas or 0),
                    avg_ctr=float(perf.avg_ctr or 0),
                    avg_cpa=float(perf.avg_cpa or 0),
                    best_sub_hypothesis=best_sub_hyp,
                    best_sub_hypothesis_roas=best_roas,
                ))

            return results

        finally:
            session.close()

    def get_gap_recommendations(self, client_name: str) -> List[GapRecommendation]:
        """Generate auto-recommendations for creative gaps"""
        recommendations = []

        # Get distribution
        distribution = self.get_awareness_distribution(client_name)

        # Check for under-covered levels
        for level, pct in distribution.level_percentages.items():
            if pct < 10 and distribution.total_tagged > 10:
                level_name = AWARENESS_LEVELS.get(level, f"Level {level}")
                recommendations.append(GapRecommendation(
                    client_name=client_name,
                    priority="high",
                    recommendation_type="awareness_gap",
                    message=f"Needs more Level {level} ({level_name}) creatives - only {pct:.0f}% coverage",
                    details=f"Target: 15-25% coverage. Current: {pct:.0f}%",
                ))

        # Check for over-indexed levels
        for level, pct in distribution.level_percentages.items():
            if pct > 40:
                level_name = AWARENESS_LEVELS.get(level, f"Level {level}")
                recommendations.append(GapRecommendation(
                    client_name=client_name,
                    priority="medium",
                    recommendation_type="over_indexed",
                    message=f"Over-indexed on Level {level} ({level_name}) - {pct:.0f}% coverage",
                    details="Diversify to earlier awareness stages for broader reach",
                ))

        # Check hypothesis coverage
        hypothesis_coverage = self.get_hypothesis_coverage(client_name)
        for hc in hypothesis_coverage:
            if hc.coverage_percentage < 50 and hc.sub_hypotheses_total > 0:
                recommendations.append(GapRecommendation(
                    client_name=client_name,
                    priority="medium",
                    recommendation_type="hypothesis_gap",
                    message=f"Testing {hc.sub_hypotheses_tested} of {hc.sub_hypotheses_total} sub-hypotheses in Level {hc.level}",
                    details=f"Missing: {', '.join(hc.sub_hypotheses_missing[:3])}",
                ))

        # Check concept diversity
        concept_diversity = self.get_concept_diversity(client_name)
        for cd in concept_diversity:
            if cd.is_over_indexed and cd.dominant_concept:
                recommendations.append(GapRecommendation(
                    client_name=client_name,
                    priority="low",
                    recommendation_type="concept_diversity",
                    message=f"Level {cd.level}: Over-reliant on '{cd.dominant_concept}' concept",
                    details=f"Consider adding: {', '.join(cd.missing_concepts[:3])}",
                ))

        # Check performance-based recommendations
        performance = self.get_performance_by_level(client_name)
        for perf in performance:
            if perf.best_sub_hypothesis and perf.best_sub_hypothesis_roas > 2.0:
                recommendations.append(GapRecommendation(
                    client_name=client_name,
                    priority="high",
                    recommendation_type="high_performer",
                    message=f"'{perf.best_sub_hypothesis}' performing at {perf.best_sub_hypothesis_roas:.1f}x ROAS",
                    details="Create more variants of this winning sub-hypothesis",
                ))

        # Check low diversity score
        if distribution.diversity_score < config.alerts.min_diversity_score:
            recommendations.append(GapRecommendation(
                client_name=client_name,
                priority="high",
                recommendation_type="low_diversity",
                message=f"Creative diversity score is {distribution.diversity_score}/10",
                details="Improve balance across all 5 awareness levels",
            ))

        # Sort by priority
        priority_order = {'high': 0, 'medium': 1, 'low': 2}
        recommendations.sort(key=lambda x: priority_order.get(x.priority, 3))

        return recommendations

    def get_all_clients_diversity_summary(self) -> pd.DataFrame:
        """Get diversity summary for all clients"""
        session = self.db.get_session()
        try:
            clients = session.query(Client).filter(Client.active == True).all()

            data = []
            for client in clients:
                distribution = self.get_awareness_distribution(client.name)
                recommendations = self.get_gap_recommendations(client.name)

                data.append({
                    'Client': client.display_name,
                    'Tagged Creatives': distribution.total_tagged,
                    'Untagged': distribution.total_untagged,
                    'Diversity Score': distribution.diversity_score,
                    'L1 (Unaware)': f"{distribution.level_percentages.get(1, 0):.0f}%",
                    'L2 (Problem)': f"{distribution.level_percentages.get(2, 0):.0f}%",
                    'L3 (Solution)': f"{distribution.level_percentages.get(3, 0):.0f}%",
                    'L4 (Product)': f"{distribution.level_percentages.get(4, 0):.0f}%",
                    'L5 (Most Aware)': f"{distribution.level_percentages.get(5, 0):.0f}%",
                    'High Priority Gaps': len([r for r in recommendations if r.priority == 'high']),
                    'Total Recommendations': len(recommendations),
                })

            return pd.DataFrame(data)

        finally:
            session.close()
