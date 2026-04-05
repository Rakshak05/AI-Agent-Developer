"""
REAL-TIME RECRUITMENT DASHBOARD
=================================
Beautiful, interactive dashboard showing live recruitment metrics.
Built with Streamlit for rapid development and great UX.

Usage:
  streamlit run dashboard.py

Features:
  - Live candidate tier distribution (pie chart)
  - Application funnel visualization
  - Anti-cheat copy-ring network graph
  - Email thread viewer
  - Real-time status updates from SQLite
  - Candidate search and filtering
"""

import streamlit as st
import pandas as pd
import sqlite3
import json
from pathlib import Path
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go

# Page config
st.set_page_config(
    page_title="Recruitment AI Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = Path("data/recruitment.db")

# Custom CSS
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        margin: 10px 0;
    }
    .tier-fast-track { color: #00cc96; font-weight: bold; }
    .tier-consider { color: #ef553b; font-weight: bold; }
    .tier-reject { color: #636efa; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=60)  # Cache for 60 seconds
def load_candidates():
    """Load candidates from database."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT 
            id, name, email, score, tier, status,
            github_url, cover_letter, created_at, updated_at
        FROM candidates
        ORDER BY score DESC
    """, conn)
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_email_stats():
    """Load email activity statistics."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT direction, round, COUNT(*) as count
        FROM email_threads
        GROUP BY direction, round
        ORDER BY round, direction
    """, conn)
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_strikes():
    """Load anti-cheat strikes."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT s.candidate_id, c.name, s.reason, s.details, s.created_at
        FROM strikes s
        JOIN candidates c ON s.candidate_id = c.id
        ORDER BY s.created_at DESC
    """, conn)
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_system_logs():
    """Load recent system logs."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT event_type, candidate_id, details, created_at
        FROM system_log
        ORDER BY created_at DESC
        LIMIT 100
    """, conn)
    conn.close()
    return df


# ── SIDEBAR ──────────────────────────────────────────────────────────────────

st.sidebar.title("🤖 Recruitment AI")
st.sidebar.markdown("**Real-Time Dashboard**")
st.sidebar.markdown("---")

# Navigation
page = st.sidebar.radio(
    "Navigate to:",
    ["📊 Overview", "👥 Candidates", "📧 Email Activity", "⚠️ Anti-Cheat", "📝 System Logs"]
)

# Auto-refresh toggle
auto_refresh = st.sidebar.checkbox("Auto-refresh (every 60s)", value=False)
if auto_refresh:
    st.rerun()

# ── MAIN CONTENT ─────────────────────────────────────────────────────────────

st.title("🎯 AI-Powered Recruitment Dashboard")
st.markdown("---")

if page == "📊 Overview":
    # Load data
    candidates_df = load_candidates()
    
    if candidates_df.empty:
        st.warning("No candidate data found. Run the pipeline first: `python main.py --run-pipeline`")
        st.stop()
    
    # Key metrics
    col1, col2, col3, col4 = st.columns(4)
    
    total_candidates = len(candidates_df)
    fast_track = len(candidates_df[candidates_df['tier'] == 'Fast-Track'])
    in_progress = len(candidates_df[candidates_df['status'].str.contains('round_', na=False)])
    eliminated = len(candidates_df[candidates_df['status'] == 'eliminated'])
    
    col1.metric("Total Applicants", total_candidates)
    col2.metric("🟢 Fast-Track", fast_track, f"{fast_track/total_candidates*100:.1f}%" if total_candidates > 0 else "0%")
    col3.metric("📧 In Progress", in_progress)
    col4.metric("❌ Eliminated", eliminated)
    
    st.markdown("---")
    
    # Tier distribution pie chart
    col1, col2 = st.columns(2)
    
    with col1:
        tier_counts = candidates_df['tier'].value_counts().reset_index()
        tier_counts.columns = ['Tier', 'Count']
        
        fig_pie = px.pie(
            tier_counts,
            values='Count',
            names='Tier',
            title='Candidate Tier Distribution',
            color='Tier',
            color_discrete_map={
                'Fast-Track': '#00cc96',
                'Consider': '#ef553b',
                'Reject': '#636efa'
            }
        )
        fig_pie.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig_pie, use_container_width=True)
    
    with col2:
        # Status breakdown
        status_counts = candidates_df['status'].value_counts().reset_index()
        status_counts.columns = ['Status', 'Count']
        
        fig_bar = px.bar(
            status_counts,
            x='Status',
            y='Count',
            title='Application Status Breakdown',
            color='Status',
            color_continuous_scale='Viridis'
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    
    st.markdown("---")
    
    # Score distribution histogram
    fig_hist = px.histogram(
        candidates_df,
        x='score',
        nbins=30,
        title='Candidate Score Distribution',
        color='tier',
        labels={'score': 'AI Score', 'count': 'Number of Candidates'}
    )
    st.plotly_chart(fig_hist, use_container_width=True)

elif page == "👥 Candidates":
    candidates_df = load_candidates()
    
    if candidates_df.empty:
        st.warning("No candidate data found.")
        st.stop()
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        tier_filter = st.multiselect(
            "Filter by Tier:",
            options=candidates_df['tier'].unique(),
            default=candidates_df['tier'].unique()
        )
    
    with col2:
        status_filter = st.multiselect(
            "Filter by Status:",
            options=candidates_df['status'].unique(),
            default=candidates_df['status'].unique()
        )
    
    with col3:
        min_score = st.slider("Minimum Score:", 0.0, 100.0, 0.0)
    
    # Apply filters
    filtered_df = candidates_df[
        (candidates_df['tier'].isin(tier_filter)) &
        (candidates_df['status'].isin(status_filter)) &
        (candidates_df['score'] >= min_score)
    ]
    
    st.subheader(f"Candidates ({len(filtered_df)} shown)")
    
    # Display table
    display_df = filtered_df[[
        'name', 'email', 'score', 'tier', 'status', 'github_url'
    ]].copy()
    
    # Add clickable links
    display_df['GitHub'] = display_df['github_url'].apply(
        lambda x: f"[Link]({x})" if pd.notna(x) and x else "-"
    )
    
    st.dataframe(
        display_df.drop(columns=['github_url']),
        use_container_width=True,
        hide_index=True
    )
    
    # Export button
    csv = filtered_df.to_csv(index=False)
    st.download_button(
        label="📥 Download Filtered Results as CSV",
        data=csv,
        file_name=f"candidates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

elif page == "📧 Email Activity":
    email_stats = load_email_stats()
    
    if email_stats.empty:
        st.warning("No email activity found.")
        st.stop()
    
    st.subheader("Email Communication Metrics")
    
    # Create pivot table for visualization
    pivot = email_stats.pivot(index='round', columns='direction', values='count').fillna(0)
    
    # Convert to long format for plotting
    plot_data = pivot.reset_index().melt(id_vars=['round'], var_name='direction', value_name='count')
    
    if not plot_data.empty and plot_data['count'].sum() > 0:
        fig_bar = px.bar(
            plot_data,
            x='round',
            y='count',
            color='direction',
            title='Email Activity by Round and Direction',
            labels={'count': 'Number of Emails', 'round': 'Round'},
            barmode='group'
        )
        
        # Update layout for better appearance
        fig_bar.update_layout(
            xaxis=dict(type='category'),
            height=500
        )
        
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("No email activity data available.")
    
    # Raw stats with corrected indexing starting from 1
    if not email_stats.empty:
        # Create a copy with proper index starting from 1
        email_stats_display = email_stats.reset_index(drop=True)
        email_stats_display.index = range(1, len(email_stats_display) + 1)
        st.dataframe(email_stats_display, use_container_width=True)
    else:
        st.dataframe(email_stats, use_container_width=True)

elif page == "⚠️ Anti-Cheat":
    strikes_df = load_strikes()
    
    if strikes_df.empty:
        st.info("No anti-cheat violations detected yet.")
    else:
        st.subheader(f"Anti-Cheat Violations Detected ({len(strikes_df)} total)")
        
        # Violation types
        violation_counts = strikes_df['reason'].value_counts().reset_index()
        violation_counts.columns = ['Violation Type', 'Count']
        
        fig_violations = px.pie(
            violation_counts,
            values='Count',
            names='Violation Type',
            title='Violation Type Distribution'
        )
        st.plotly_chart(fig_violations, use_container_width=True)
        
        # Detailed strikes table
        st.dataframe(
            strikes_df[['name', 'reason', 'details', 'created_at']],
            use_container_width=True,
            hide_index=True
        )

elif page == "📝 System Logs":
    logs_df = load_system_logs()
    
    if logs_df.empty:
        st.info("No system logs available.")
        st.stop()
    
    st.subheader("Recent System Activity")
    
    # Event type filter
    event_types = logs_df['event_type'].unique()
    selected_events = st.multiselect(
        "Filter by Event Type:",
        options=event_types,
        default=event_types
    )
    
    filtered_logs = logs_df[logs_df['event_type'].isin(selected_events)]
    
    # Display logs
    for _, log in filtered_logs.head(50).iterrows():
        with st.expander(f"**{log['event_type']}** - {log['created_at'][:19]}"):
            st.json(json.loads(log['details']) if log['details'] else {})
            st.caption(f"Candidate ID: {log['candidate_id']}")

# ── FOOTER ───────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: gray;'>
        <p>Built with ❤️ using Streamlit | Last updated: {}</p>
        <p><i>AI-Powered Recruitment Pipeline v2.0</i></p>
    </div>
    """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    unsafe_allow_html=True
)
