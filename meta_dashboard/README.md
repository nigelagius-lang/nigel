# Meta Marketing Dashboard

Performance marketing dashboard for managing Meta ads across multiple clients, featuring creative freshness tracking, awareness-based diversity analysis, and comprehensive health monitoring.

## Quick Start

### 1. Install Dependencies

```bash
cd meta_dashboard
pip install -r requirements.txt
```

### 2. Configure Meta API

Copy the example environment file and add your credentials:

```bash
cp .env.example .env
```

Edit `.env` and add:

```
META_ACCESS_TOKEN=your_meta_access_token_here
STORELLI_AD_ACCOUNT=act_XXXXXXXXXX
FEELNUYU_AD_ACCOUNT=act_XXXXXXXXXX
# ... add all 8 client ad account IDs
```

### 3. Run the Dashboard

```bash
streamlit run app.py
```

The dashboard will open at `http://localhost:8501`

---

## Getting Your Meta API Access Token

### Step 1: Create a Meta App

1. Go to [Meta for Developers](https://developers.facebook.com/)
2. Click "My Apps" → "Create App"
3. Choose "Business" type
4. Name your app (e.g., "Marketing Dashboard")
5. Add the "Marketing API" product to your app

### Step 2: Get Access Token

1. Go to [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. Select your app from the dropdown
3. Click "Generate Access Token"
4. Grant these permissions:
   - `ads_read`
   - `ads_management`
   - `business_management`
   - `read_insights`
5. Copy the token

### Step 3: Get Ad Account IDs

1. Go to [Meta Business Suite](https://business.facebook.com/)
2. Navigate to Settings → Ad Accounts
3. Find each client's ad account ID (format: `act_XXXXXXXXXX`)

### Step 4: Extend Token (Recommended)

Short-lived tokens expire in ~1 hour. To extend:

1. Go to [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/)
2. Paste your token and click "Debug"
3. Click "Extend Access Token" at the bottom
4. Copy the new long-lived token (valid ~60 days)

---

## Features

### 1. Overview Dashboard
- All 8 clients at a glance
- Key metrics: spend, ROAS, active ads, alerts
- Creative freshness status
- Recent alerts summary

### 2. Client Deep-Dive
- Detailed performance for each client
- Top 3 and bottom 3 performing ads
- ROAS trends (7-day, 30-day, 90-day)
- Full ad performance table with export

### 3. Creative Performance
- Sortable table of all ads across clients
- Filter by client, status, date range
- Export to CSV

### 4. Awareness Analysis (Andromeda)
- Creative diversity score (0-10)
- Distribution across 5 awareness levels
- Hypothesis coverage tracking
- Performance by awareness level
- Auto-generated gap recommendations

### 5. Framework Setup
- Configure custom awareness frameworks per client
- Define hypotheses and sub-hypotheses for each level
- Import frameworks from JSON

### 6. Creative Tagging
- Download tagging templates (pre-filled with ads)
- Upload tagged CSV files
- View untagged ads
- Export all tags

### 7. Alerts
- Creative freshness (>7 days warning, >14 days critical)
- ROAS drops (>20% week-over-week)
- CTR decline (3+ consecutive days)
- Ad frequency fatigue (>3.0)
- Spend variance (>15% from target)
- Low diversity score (<5/10)
- High-spend low-ROAS ads

### 8. Settings
- API connection testing
- Client management
- Alert threshold configuration
- Data cleanup

---

## Client List

The dashboard is pre-configured for 8 clients:

1. **Storelli** - Goalkeeper protection gear
2. **FeelNuyu** - Magnesium wellness products
3. **Luna Daily** - Intimate care
4. **Designrr** - eBook creation SaaS
5. **SelfTalk Plus** - Mental wellness app
6. **Belong** - Community platform
7. **Celebrate Recovery Store** - Recovery resources
8. **Axium Wealth** - Financial services for accredited investors

---

## Awareness Levels (Eugene Schwartz)

The dashboard uses the 5 awareness levels for creative diversity tracking:

| Level | Name | Description |
|-------|------|-------------|
| 1 | Unaware | Doesn't know they have a problem |
| 2 | Problem Aware | Knows the problem, not the solution |
| 3 | Solution Aware | Knows solutions exist, not your product |
| 4 | Product Aware | Knows your product, needs convincing |
| 5 | Most Aware | Ready to buy, needs the right offer |

**Target Distribution:** 15-25% per level for optimal diversity.

---

## Tagging Workflow

### 1. Generate Template
1. Go to Tagging → Download Template
2. Select client and download CSV

### 2. Fill in Tags
For each ad, add:
- `awareness_level` (1-5)
- `hypothesis` (from client framework)
- `sub_hypothesis` (from client framework)
- `creative_concept` (Pattern Interrupt, Education, Social Proof, etc.)

### 3. Upload Tags
1. Go to Tagging → Upload Tags
2. Upload your filled CSV
3. Review import results

### 4. Analyze
- Check Awareness Analysis for diversity metrics
- Review gap recommendations
- Track performance by level

---

## Creative Concepts

Available concepts for tagging:

- Pattern Interrupt
- Education/Science
- Social Proof
- Comparison
- Testimonial
- Urgency/Scarcity
- Objection Handling
- Stats/Data
- Story/Narrative
- Before/After
- Demonstration
- Authority/Expert
- Fear of Missing Out
- Aspirational
- Problem Agitation
- Humor
- Controversy
- Behind the Scenes
- User Generated Content
- Founder Story

---

## Data Storage

- **Database:** SQLite (local file at `data/dashboard.db`)
- **Retention:** 90 days by default (configurable)
- **Tables:**
  - `clients` - Client configuration
  - `client_frameworks` - Awareness frameworks per client
  - `ad_performance` - Daily performance snapshots
  - `creative_tags` - Awareness tagging data
  - `alerts_history` - Historical alerts log
  - `data_sync_log` - API sync tracking

---

## Customization

### Add New Client

1. Go to Settings → Client Management → Add New Client
2. Enter internal name, display name, ad account ID
3. Sync data from Meta API
4. Set up awareness framework

### Modify Alert Thresholds

Edit `.env` file:

```
CREATIVE_FRESHNESS_WARNING_DAYS=7
CREATIVE_FRESHNESS_CRITICAL_DAYS=14
ROAS_DROP_THRESHOLD=0.20
# ... etc
```

### Add Notifications (Future)

Uncomment and configure in `.env`:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX
SENDGRID_API_KEY=your_key
ALERT_EMAIL=you@example.com
```

---

## Troubleshooting

### "Meta API not configured"
- Check that `META_ACCESS_TOKEN` is set in `.env`
- Ensure token hasn't expired

### "Failed to fetch ads"
- Verify ad account ID format (`act_XXXXXXXXXX`)
- Check API permissions
- Test token in Graph API Explorer

### No data showing
- Click "Sync All Clients" in sidebar
- Check Settings → API Status → Test Connection
- Review error messages in sync results

### Token expired
- Generate new token in Graph API Explorer
- Update `.env` file
- Restart dashboard

---

## Support

For issues or feature requests, contact your dashboard administrator.
