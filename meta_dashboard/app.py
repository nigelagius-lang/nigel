"""
Meta Marketing Dashboard - Streamlit Application

Performance marketing dashboard for managing Meta ads across multiple clients.
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import io
import json

# Page config must be first Streamlit command
st.set_page_config(
    page_title="Meta Marketing Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Import local modules
from src.database import init_database, Client, ClientFramework
from src.config import config, AWARENESS_LEVELS, CREATIVE_CONCEPTS
from src.meta_api import MetaAPIClient, MetaDataSync
from src.freshness_tracker import FreshnessTracker, FreshnessStatus
from src.diversity_analyzer import DiversityAnalyzer
from src.client_health import ClientHealthDashboard
from src.alerts import AlertsManager, AlertSeverity
from src.tagging import TaggingManager


# Initialize database
@st.cache_resource
def get_database():
    return init_database(config.database_path)


# Initialize components
def get_components():
    db = get_database()
    return {
        'db': db,
        'meta_api': MetaAPIClient(),
        'freshness': FreshnessTracker(db),
        'diversity': DiversityAnalyzer(db),
        'health': ClientHealthDashboard(db),
        'alerts': AlertsManager(db),
        'tagging': TaggingManager(db),
    }


# =============================================
# SIDEBAR
# =============================================

def render_sidebar():
    """Render the sidebar with navigation and settings"""
    with st.sidebar:
        st.title("📊 Meta Dashboard")
        st.caption("Performance Marketing Analytics")

        st.divider()

        # Navigation
        page = st.radio(
            "Navigation",
            ["Overview", "Client Deep-Dive", "Creative Performance",
             "Awareness Analysis", "Framework Setup", "Tagging", "Alerts", "Settings"],
            label_visibility="collapsed",
        )

        st.divider()

        # Quick stats
        components = get_components()
        alerts = components['alerts'].get_active_alerts()
        critical_count = len([a for a in alerts if a.severity == AlertSeverity.CRITICAL])
        warning_count = len([a for a in alerts if a.severity == AlertSeverity.WARNING])

        st.metric("Active Alerts", len(alerts))
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Critical", critical_count)
        with col2:
            st.metric("Warnings", warning_count)

        st.divider()

        # Last sync info
        st.caption("Data Sync")
        if st.button("Sync All Clients", type="primary", use_container_width=True):
            sync_data()

        st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return page


# =============================================
# DATA SYNC
# =============================================

def sync_data():
    """Sync data from Meta API"""
    components = get_components()

    if not config.is_meta_configured():
        st.error("Meta API not configured. Please add your access token in Settings.")
        return

    with st.spinner("Syncing data from Meta API..."):
        sync = MetaDataSync(components['meta_api'], components['db'])
        results = sync.sync_all_clients()

        for result in results:
            if result.get('status') == 'success':
                st.success(f"✓ {result['client']}: {result['records_stored']} records")
            else:
                st.error(f"✗ {result['client']}: {result.get('error', 'Unknown error')}")


# =============================================
# OVERVIEW PAGE
# =============================================

def render_overview():
    """Render the overview page with all clients at a glance"""
    st.title("Overview")
    st.caption("All clients at a glance")

    components = get_components()

    # Top metrics row
    col1, col2, col3, col4 = st.columns(4)

    # Get all health data
    all_health = components['health'].get_all_clients_health()

    total_spend = sum(h.current_week_spend for h in all_health)
    avg_roas = sum(h.roas_7day for h in all_health) / len(all_health) if all_health else 0
    total_active_ads = sum(h.active_ads for h in all_health)
    alerts = components['alerts'].get_active_alerts()

    with col1:
        st.metric("Total Week Spend", f"€{total_spend:,.0f}")
    with col2:
        st.metric("Avg ROAS (7d)", f"{avg_roas:.2f}x")
    with col3:
        st.metric("Active Ads", total_active_ads)
    with col4:
        st.metric("Alerts", len(alerts))

    st.divider()

    # Client overview table
    st.subheader("Client Performance")

    overview_df = components['health'].get_overview_dataframe()
    if not overview_df.empty:
        st.dataframe(
            overview_df,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No data available. Sync data from Meta API first.")

    # Two column layout for freshness and alerts
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Creative Freshness")
        freshness_df = components['freshness'].get_freshness_dataframe()
        if not freshness_df.empty:
            st.dataframe(
                freshness_df[['Client', 'Status Emoji', 'Days Since', 'Last 7 Days', 'Velocity Trend']],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No freshness data available")

    with col2:
        st.subheader("Recent Alerts")
        alerts_df = components['alerts'].get_alerts_dataframe()
        if not alerts_df.empty:
            st.dataframe(
                alerts_df[['Severity', 'Client', 'Type', 'Title']].head(10),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No active alerts")


# =============================================
# CLIENT DEEP-DIVE PAGE
# =============================================

def render_client_deep_dive():
    """Render detailed view for a single client"""
    st.title("Client Deep-Dive")

    components = get_components()
    db = components['db']

    # Client selector
    session = db.get_session()
    clients = session.query(Client).filter(Client.active == True).all()
    session.close()

    if not clients:
        st.warning("No clients configured. Add clients in Settings.")
        return

    client_options = {c.display_name: c.name for c in clients}
    selected_display = st.selectbox("Select Client", list(client_options.keys()))
    selected_client = client_options[selected_display]

    st.divider()

    # Get client health
    health = components['health'].get_client_health(selected_client)

    if not health:
        st.info(f"No data available for {selected_display}")
        return

    # Top metrics
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "Week Spend",
            f"€{health.current_week_spend:,.0f}",
            f"{health.spend_variance_pct:+.0f}% vs avg"
        )
    with col2:
        st.metric("ROAS (7d)", f"{health.roas_7day:.2f}x")
    with col3:
        st.metric("ROAS (30d)", f"{health.roas_30day:.2f}x")
    with col4:
        st.metric("Active Ads", health.active_ads)
    with col5:
        st.metric("Diversity Score", f"{health.diversity_score}/10")

    # Alerts for this client
    if health.alerts:
        st.warning(f"{len(health.alerts)} active alerts for this client")
        with st.expander("View Alerts"):
            for alert in health.alerts:
                severity_icon = "🔴" if alert['severity'] == 'critical' else "🟡"
                st.write(f"{severity_icon} **{alert['message']}** - {alert['value']}")

    st.divider()

    # Top and bottom performers
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Top 3 Performers (by ROAS)")
        if health.top_3_ads:
            for i, ad in enumerate(health.top_3_ads, 1):
                with st.container():
                    st.write(f"**{i}. {ad.ad_name[:50]}...**" if len(ad.ad_name) > 50 else f"**{i}. {ad.ad_name}**")
                    cols = st.columns(3)
                    cols[0].write(f"ROAS: {ad.roas:.2f}x")
                    cols[1].write(f"Spend: €{ad.spend:.0f}")
                    cols[2].write(f"Conv: {ad.conversions}")
        else:
            st.info("No top performers yet")

    with col2:
        st.subheader("Bottom 3 (ROAS < 1.0)")
        if health.bottom_3_ads:
            for i, ad in enumerate(health.bottom_3_ads, 1):
                with st.container():
                    st.write(f"**{i}. {ad.ad_name[:50]}...**" if len(ad.ad_name) > 50 else f"**{i}. {ad.ad_name}**")
                    cols = st.columns(3)
                    cols[0].write(f"ROAS: {ad.roas:.2f}x")
                    cols[1].write(f"Spend: €{ad.spend:.0f}")
                    cols[2].write(f"Status: {ad.status}")
        else:
            st.success("No underperformers")

    st.divider()

    # Performance table
    st.subheader("All Ads Performance")

    days = st.slider("Date Range (days)", 7, 90, 30)
    perf_df = components['health'].get_ad_performance_table(selected_client, days=days)

    if not perf_df.empty:
        st.dataframe(
            perf_df,
            use_container_width=True,
            hide_index=True,
        )

        # Export button
        csv = perf_df.to_csv(index=False)
        st.download_button(
            "Export to CSV",
            csv,
            f"{selected_client}_performance_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )


# =============================================
# CREATIVE PERFORMANCE PAGE
# =============================================

def render_creative_performance():
    """Render sortable table of all ads with filters"""
    st.title("Creative Performance")

    components = get_components()
    db = components['db']

    # Filters
    col1, col2, col3 = st.columns(3)

    session = db.get_session()
    clients = session.query(Client).filter(Client.active == True).all()
    session.close()

    with col1:
        client_options = ["All Clients"] + [c.display_name for c in clients]
        selected_client = st.selectbox("Client", client_options)

    with col2:
        status_options = ["All", "ACTIVE", "PAUSED"]
        selected_status = st.selectbox("Status", status_options)

    with col3:
        days = st.selectbox("Date Range", [7, 14, 30, 60, 90], index=2)

    st.divider()

    # Get performance data
    if selected_client == "All Clients":
        # Combine all clients
        all_dfs = []
        for client in clients:
            df = components['health'].get_ad_performance_table(client.name, days=days)
            if not df.empty:
                df['Client'] = client.display_name
                all_dfs.append(df)

        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
        else:
            combined_df = pd.DataFrame()
    else:
        client_name = next((c.name for c in clients if c.display_name == selected_client), None)
        if client_name:
            combined_df = components['health'].get_ad_performance_table(client_name, days=days)
            combined_df['Client'] = selected_client
        else:
            combined_df = pd.DataFrame()

    # Apply status filter
    if not combined_df.empty and selected_status != "All":
        combined_df = combined_df[combined_df['Status'] == selected_status]

    # Display
    if not combined_df.empty:
        st.write(f"Showing {len(combined_df)} ads")

        # Sort options
        sort_col = st.selectbox(
            "Sort by",
            ['Spend', 'ROAS', 'CTR', 'CPA', 'Conversions'],
            index=0,
        )

        combined_df = combined_df.sort_values(sort_col, ascending=False)

        st.dataframe(
            combined_df,
            use_container_width=True,
            hide_index=True,
        )

        # Export
        csv = combined_df.to_csv(index=False)
        st.download_button(
            "Export to CSV",
            csv,
            f"creative_performance_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv",
        )
    else:
        st.info("No data available")


# =============================================
# AWARENESS ANALYSIS PAGE
# =============================================

def render_awareness_analysis():
    """Render awareness-based analysis and diversity metrics"""
    st.title("Awareness Analysis")
    st.caption("Andromeda Diversity Analyzer - Track creative diversity across awareness levels")

    components = get_components()
    db = components['db']

    # Client selector
    session = db.get_session()
    clients = session.query(Client).filter(Client.active == True).all()
    session.close()

    if not clients:
        st.warning("No clients configured")
        return

    client_options = {c.display_name: c.name for c in clients}
    selected_display = st.selectbox("Select Client", list(client_options.keys()))
    selected_client = client_options[selected_display]

    st.divider()

    # Get diversity data
    distribution = components['diversity'].get_awareness_distribution(selected_client)

    # Top metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Diversity Score", f"{distribution.diversity_score}/10")
    with col2:
        st.metric("Tagged Creatives", distribution.total_tagged)
    with col3:
        st.metric("Untagged", distribution.total_untagged)
    with col4:
        tagging_pct = (distribution.total_tagged / (distribution.total_tagged + distribution.total_untagged) * 100) if (distribution.total_tagged + distribution.total_untagged) > 0 else 0
        st.metric("Tagging Progress", f"{tagging_pct:.0f}%")

    # Alerts
    if distribution.alerts:
        for alert in distribution.alerts:
            st.warning(alert)

    st.divider()

    # Awareness Distribution Chart
    st.subheader("Awareness Level Distribution")

    if distribution.total_tagged > 0:
        # Create pie chart
        labels = [f"L{level}: {AWARENESS_LEVELS[level]}" for level in range(1, 6)]
        values = [distribution.level_counts.get(level, 0) for level in range(1, 6)]

        fig = px.pie(
            names=labels,
            values=values,
            title="Distribution by Awareness Level",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

        # Bar chart showing target vs actual
        col1, col2 = st.columns(2)

        with col1:
            st.write("**Actual vs Target (15-25%)**")
            bar_data = pd.DataFrame({
                'Level': labels,
                'Actual %': [distribution.level_percentages.get(level, 0) for level in range(1, 6)],
                'Target Min': [15] * 5,
                'Target Max': [25] * 5,
            })

            fig = go.Figure()
            fig.add_trace(go.Bar(name='Actual', x=bar_data['Level'], y=bar_data['Actual %']))
            fig.add_hline(y=15, line_dash="dash", line_color="green", annotation_text="Target Min")
            fig.add_hline(y=25, line_dash="dash", line_color="red", annotation_text="Target Max")
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.write("**Counts by Level**")
            for level in range(1, 6):
                count = distribution.level_counts.get(level, 0)
                pct = distribution.level_percentages.get(level, 0)
                st.write(f"**Level {level}** ({AWARENESS_LEVELS[level]}): {count} ({pct:.0f}%)")

    else:
        st.info("No tagged creatives. Start tagging to see analysis.")

    st.divider()

    # Hypothesis Coverage
    st.subheader("Hypothesis Coverage")

    hypothesis_coverage = components['diversity'].get_hypothesis_coverage(selected_client)

    if hypothesis_coverage:
        for hc in hypothesis_coverage:
            with st.expander(f"Level {hc.level}: {hc.level_name} - {hc.coverage_percentage:.0f}% covered"):
                st.write(f"**Hypothesis:** {hc.hypothesis}")
                st.write(f"Testing {hc.sub_hypotheses_tested} of {hc.sub_hypotheses_total} sub-hypotheses")

                if hc.sub_hypotheses_missing:
                    st.warning(f"Missing: {', '.join(hc.sub_hypotheses_missing)}")

                # Progress bar
                st.progress(hc.coverage_percentage / 100)
    else:
        st.info("No framework configured for this client. Set up framework in Framework Setup.")

    st.divider()

    # Performance by Level
    st.subheader("Performance by Awareness Level")

    performance = components['diversity'].get_performance_by_level(selected_client)

    if performance and any(p.creative_count > 0 for p in performance):
        perf_df = pd.DataFrame([{
            'Level': f"L{p.level}: {p.level_name}",
            'Creatives': p.creative_count,
            'Total Spend': f"€{p.total_spend:,.0f}",
            'Avg ROAS': f"{p.avg_roas:.2f}x",
            'Avg CTR': f"{p.avg_ctr:.2f}%",
            'Avg CPA': f"€{p.avg_cpa:.2f}",
            'Best Sub-Hypothesis': p.best_sub_hypothesis or '-',
        } for p in performance])

        st.dataframe(perf_df, use_container_width=True, hide_index=True)

        # Best performing level
        best_level = max(performance, key=lambda x: x.avg_roas if x.creative_count > 0 else 0)
        if best_level.creative_count > 0:
            st.success(f"Best Performing: Level {best_level.level} ({best_level.level_name}) with {best_level.avg_roas:.2f}x ROAS")
    else:
        st.info("Tag creatives to see performance by awareness level")

    st.divider()

    # Gap Recommendations
    st.subheader("Recommendations")

    recommendations = components['diversity'].get_gap_recommendations(selected_client)

    if recommendations:
        for rec in recommendations:
            if rec.priority == "high":
                st.error(f"🔴 **{rec.message}**\n\n{rec.details or ''}")
            elif rec.priority == "medium":
                st.warning(f"🟡 **{rec.message}**\n\n{rec.details or ''}")
            else:
                st.info(f"🔵 **{rec.message}**\n\n{rec.details or ''}")
    else:
        st.success("No gaps identified - good coverage!")


# =============================================
# FRAMEWORK SETUP PAGE
# =============================================

def render_framework_setup():
    """Render framework management interface"""
    st.title("Framework Setup")
    st.caption("Configure awareness frameworks for each client")

    components = get_components()
    db = components['db']

    # Client selector
    session = db.get_session()
    clients = session.query(Client).filter(Client.active == True).all()
    session.close()

    if not clients:
        st.warning("No clients configured")
        return

    client_options = {c.display_name: c.name for c in clients}
    selected_display = st.selectbox("Select Client", list(client_options.keys()))
    selected_client = client_options[selected_display]

    st.divider()

    # Load existing framework
    existing_framework = components['diversity'].get_client_framework(selected_client)

    # Framework editor
    st.subheader(f"Framework for {selected_display}")

    with st.form("framework_form"):
        framework_data = []

        for level in range(1, 6):
            st.write(f"### Level {level}: {AWARENESS_LEVELS[level]}")

            # Find existing data for this level
            existing = next((f for f in existing_framework if f['awareness_level'] == level), None)

            col1, col2 = st.columns([1, 2])

            with col1:
                level_name = st.text_input(
                    "Level Name",
                    value=existing['level_name'] if existing else AWARENESS_LEVELS[level],
                    key=f"level_name_{level}",
                )

            with col2:
                hypothesis = st.text_area(
                    "Main Hypothesis",
                    value=existing['hypothesis'] if existing else "",
                    key=f"hypothesis_{level}",
                    height=80,
                    placeholder="Core belief/assumption being addressed at this level",
                )

            # Sub-hypotheses
            sub_hyp_text = st.text_area(
                "Sub-hypotheses (one per line)",
                value="\n".join(existing['sub_hypotheses']) if existing else "",
                key=f"sub_hyp_{level}",
                height=100,
                placeholder="Enter 3-5 specific variants, one per line",
            )

            sub_hypotheses = [s.strip() for s in sub_hyp_text.split("\n") if s.strip()]

            framework_data.append({
                'awareness_level': level,
                'level_name': level_name,
                'hypothesis': hypothesis,
                'sub_hypotheses': sub_hypotheses,
            })

            st.divider()

        submitted = st.form_submit_button("Save Framework", type="primary")

        if submitted:
            success = components['diversity'].save_client_framework(selected_client, framework_data)
            if success:
                st.success("Framework saved successfully!")
                st.rerun()
            else:
                st.error("Failed to save framework")

    # Quick paste option
    st.divider()
    st.subheader("Quick Import")
    st.caption("Paste JSON framework or use template")

    with st.expander("Import from JSON"):
        json_input = st.text_area(
            "Paste JSON",
            height=200,
            placeholder='[{"awareness_level": 1, "level_name": "Unaware", "hypothesis": "...", "sub_hypotheses": ["...", "..."]}]',
        )

        if st.button("Import JSON"):
            try:
                imported = json.loads(json_input)
                success = components['diversity'].save_client_framework(selected_client, imported)
                if success:
                    st.success("Imported successfully!")
                    st.rerun()
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")


# =============================================
# TAGGING PAGE
# =============================================

def render_tagging():
    """Render tagging interface"""
    st.title("Creative Tagging")
    st.caption("Tag creatives with awareness data")

    components = get_components()
    db = components['db']

    # Client selector
    session = db.get_session()
    clients = session.query(Client).filter(Client.active == True).all()
    session.close()

    if not clients:
        st.warning("No clients configured")
        return

    client_options = {c.display_name: c.name for c in clients}
    selected_display = st.selectbox("Select Client", list(client_options.keys()))
    selected_client = client_options[selected_display]

    # Tagging progress
    progress = components['tagging'].get_tagging_progress(selected_client)
    if progress:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Ads", progress['total_ads'])
        with col2:
            st.metric("Tagged", progress['tagged_ads'])
        with col3:
            st.metric("Progress", f"{progress['completion_percentage']:.0f}%")

    st.divider()

    # Tabs for different tagging operations
    tab1, tab2, tab3, tab4 = st.tabs(["Download Template", "Upload Tags", "View Untagged", "Export Tags"])

    with tab1:
        st.subheader("Download Tagging Template")
        st.write("Download a CSV template pre-filled with this client's ads.")

        include_existing = st.checkbox("Include existing tags", value=True)

        if st.button("Generate Template"):
            template_df = components['tagging'].generate_tagging_template(
                selected_client,
                include_existing_tags=include_existing,
            )

            if not template_df.empty:
                csv = template_df.to_csv(index=False)
                st.download_button(
                    "Download CSV",
                    csv,
                    f"{selected_client}_tagging_template.csv",
                    "text/csv",
                )
                st.dataframe(template_df.head(10), use_container_width=True)
            else:
                st.info("No ads found for this client")

        # Show reference sheet
        with st.expander("View Framework Reference"):
            reference = components['tagging'].generate_reference_sheet(selected_client)
            st.markdown(reference)

    with tab2:
        st.subheader("Upload Tagged CSV")

        uploaded_file = st.file_uploader("Choose CSV file", type="csv")

        if uploaded_file:
            try:
                df = pd.read_csv(uploaded_file)
                st.write(f"Preview ({len(df)} rows):")
                st.dataframe(df.head(10), use_container_width=True)

                update_existing = st.checkbox("Update existing tags", value=True)

                if st.button("Import Tags", type="primary"):
                    success, errors, messages = components['tagging'].import_tags_from_df(
                        df,
                        update_existing=update_existing,
                    )

                    if success > 0:
                        st.success(f"Successfully imported {success} tags")

                    if errors > 0:
                        st.warning(f"{errors} rows had errors")

                    if messages:
                        with st.expander("View messages"):
                            for msg in messages:
                                st.write(msg)

            except Exception as e:
                st.error(f"Error reading file: {e}")

    with tab3:
        st.subheader("Untagged Ads")

        untagged_df = components['tagging'].get_untagged_ads(selected_client)

        if not untagged_df.empty:
            st.write(f"{len(untagged_df)} ads need tagging")
            st.dataframe(untagged_df, use_container_width=True, hide_index=True)

            csv = untagged_df.to_csv(index=False)
            st.download_button(
                "Export Untagged Ads",
                csv,
                f"{selected_client}_untagged.csv",
                "text/csv",
            )
        else:
            st.success("All ads are tagged!")

    with tab4:
        st.subheader("Export All Tags")

        if st.button("Export Tags"):
            csv = components['tagging'].export_tags_to_csv(selected_client)
            st.download_button(
                "Download Tags CSV",
                csv,
                f"{selected_client}_tags_export.csv",
                "text/csv",
            )


# =============================================
# ALERTS PAGE
# =============================================

def render_alerts():
    """Render alerts page"""
    st.title("Alerts")
    st.caption("All flagged issues across clients")

    components = get_components()

    # Summary cards
    summary = components['alerts'].get_alert_summary()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Alerts", summary['total'])
    with col2:
        st.metric("Critical", summary['critical'])
    with col3:
        st.metric("Warnings", summary['warning'])
    with col4:
        st.metric("Info", summary['info'])

    st.divider()

    # Alert filters
    col1, col2 = st.columns(2)
    with col1:
        severity_filter = st.multiselect(
            "Severity",
            ["Critical", "Warning", "Info"],
            default=["Critical", "Warning"],
        )
    with col2:
        type_filter = st.multiselect(
            "Alert Type",
            list(summary['by_type'].keys()) if summary['by_type'] else [],
        )

    # Get alerts
    alerts_df = components['alerts'].get_alerts_dataframe()

    if not alerts_df.empty:
        # Apply filters
        if severity_filter:
            severity_map = {"Critical": "🔴", "Warning": "🟡", "Info": "🔵"}
            allowed_icons = [severity_map.get(s) for s in severity_filter]
            alerts_df = alerts_df[alerts_df['Severity'].isin(allowed_icons)]

        if type_filter:
            alerts_df = alerts_df[alerts_df['Type'].str.lower().str.replace(' ', '_').isin(type_filter)]

        st.dataframe(alerts_df, use_container_width=True, hide_index=True)

        # Alert details
        st.divider()
        st.subheader("Alert Details by Client")

        for client in summary['by_client'].keys():
            client_alerts = alerts_df[alerts_df['Client'] == client]
            if not client_alerts.empty:
                with st.expander(f"{client} ({len(client_alerts)} alerts)"):
                    for _, alert in client_alerts.iterrows():
                        st.write(f"{alert['Severity']} **{alert['Title']}**")
                        st.write(f"   {alert['Details']}")
                        st.divider()
    else:
        st.success("No active alerts!")

    # Alert history
    st.divider()
    st.subheader("Alert History")

    days = st.slider("Show last N days", 7, 90, 30)
    include_ack = st.checkbox("Include acknowledged alerts")

    history_df = components['alerts'].get_alert_history(days=days, include_acknowledged=include_ack)

    if not history_df.empty:
        st.dataframe(history_df, use_container_width=True, hide_index=True)
    else:
        st.info("No alert history")


# =============================================
# SETTINGS PAGE
# =============================================

def render_settings():
    """Render settings page"""
    st.title("Settings")

    components = get_components()
    db = components['db']

    # API Configuration
    st.subheader("Meta API Configuration")

    with st.expander("API Status", expanded=True):
        if config.is_meta_configured():
            st.success("Meta API is configured")

            # Test connection
            if st.button("Test Connection"):
                configured_clients = config.get_configured_clients()
                if configured_clients:
                    first_client = list(configured_clients.values())[0]
                    success, message = components['meta_api'].test_connection(first_client.ad_account_id)
                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                else:
                    st.warning("No clients configured with ad account IDs")
        else:
            st.warning("Meta API not configured")
            st.write("Add your credentials to the `.env` file:")
            st.code("""
META_ACCESS_TOKEN=your_token_here
STORELLI_AD_ACCOUNT=act_XXXXXXXXXX
# ... other ad accounts
            """)

    st.divider()

    # Client Management
    st.subheader("Client Management")

    session = db.get_session()
    clients = session.query(Client).all()

    if clients:
        client_data = [{
            'Name': c.display_name,
            'Internal Key': c.name,
            'Ad Account ID': c.ad_account_id,
            'Active': '✓' if c.active else '',
            'Daily Target': f"€{c.daily_spend_target:.0f}" if c.daily_spend_target else '-',
        } for c in clients]

        st.dataframe(pd.DataFrame(client_data), use_container_width=True, hide_index=True)

    # Add new client
    with st.expander("Add New Client"):
        with st.form("add_client"):
            col1, col2 = st.columns(2)
            with col1:
                new_name = st.text_input("Internal Name (lowercase, no spaces)", placeholder="new_client")
                new_display = st.text_input("Display Name", placeholder="New Client Inc")
            with col2:
                new_account = st.text_input("Ad Account ID", placeholder="act_XXXXXXXXXX")
                new_target = st.number_input("Daily Spend Target (€)", min_value=0.0, value=0.0)

            if st.form_submit_button("Add Client"):
                if new_name and new_display and new_account:
                    new_client = Client(
                        name=new_name.lower().replace(' ', '_'),
                        display_name=new_display,
                        ad_account_id=new_account,
                        daily_spend_target=new_target,
                    )
                    session.add(new_client)
                    session.commit()
                    st.success(f"Added client: {new_display}")
                    st.rerun()
                else:
                    st.error("Please fill all required fields")

    session.close()

    st.divider()

    # Alert Thresholds
    st.subheader("Alert Thresholds")

    st.write("Current thresholds (edit in `.env` file):")
    threshold_data = {
        'Setting': [
            'Creative Freshness Warning',
            'Creative Freshness Critical',
            'ROAS Drop Threshold',
            'CTR Decline Days',
            'Frequency Fatigue',
            'Spend Variance',
            'Min Diversity Score',
            'Low ROAS Spend Threshold',
        ],
        'Value': [
            f"{config.alerts.creative_freshness_warning_days} days",
            f"{config.alerts.creative_freshness_critical_days} days",
            f"{config.alerts.roas_drop_threshold * 100:.0f}%",
            f"{config.alerts.ctr_decline_days} days",
            f"{config.alerts.frequency_fatigue_threshold}",
            f"{config.alerts.spend_variance_threshold * 100:.0f}%",
            f"{config.alerts.min_diversity_score}/10",
            f"€{config.alerts.low_roas_spend_threshold}",
        ],
    }
    st.dataframe(pd.DataFrame(threshold_data), use_container_width=True, hide_index=True)

    st.divider()

    # Data Management
    st.subheader("Data Management")

    col1, col2 = st.columns(2)

    with col1:
        st.write(f"Data retention: {config.data_retention_days} days")
        if st.button("Clean Old Data"):
            deleted = db.cleanup_old_data(config.data_retention_days)
            st.success(f"Cleaned {deleted} old records")

    with col2:
        st.write("Database path:")
        st.code(config.database_path)


# =============================================
# MAIN APP
# =============================================

def main():
    """Main application entry point"""
    # Render sidebar and get selected page
    page = render_sidebar()

    # Render selected page
    if page == "Overview":
        render_overview()
    elif page == "Client Deep-Dive":
        render_client_deep_dive()
    elif page == "Creative Performance":
        render_creative_performance()
    elif page == "Awareness Analysis":
        render_awareness_analysis()
    elif page == "Framework Setup":
        render_framework_setup()
    elif page == "Tagging":
        render_tagging()
    elif page == "Alerts":
        render_alerts()
    elif page == "Settings":
        render_settings()


if __name__ == "__main__":
    main()
