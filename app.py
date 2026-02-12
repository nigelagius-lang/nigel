from __future__ import annotations
"""
Football Analysis Dashboard — auto-updating web app.

Runs on Flask with APScheduler to auto-fetch today's fixtures and
generate reports in the background.
"""

import os
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, abort, redirect, url_for

load_dotenv()

# Import analysis engine
import football_analysis as fa

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key-change-me")

# ---------------------------------------------------------------------------
# In-memory store for today's reports
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {
    "fixtures": [],        # raw fixture dicts from API
    "reports": {},         # slug -> {html, home_name, away_name, league, kick_off, ...}
    "last_refresh": None,  # datetime of last auto-refresh
    "refreshing": False,
}


def _slug(home: str, away: str) -> str:
    """Create a URL-safe slug from team names."""
    return f"{home}-vs-{away}".lower().replace(" ", "-")


def refresh_reports(date_str: str | None = None):
    """Fetch today's fixtures and run analysis for each match."""
    with _lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True

    try:
        fixtures = fa.fetch_todays_matches(date_str)
        reports = {}

        for fix in fixtures:
            home = fix.get("home_name", "Home")
            away = fix.get("away_name", "Away")
            slug = _slug(home, away)
            try:
                result = fa.run_analysis(fix)
                result["slug"] = slug
                reports[slug] = result
            except Exception as e:
                reports[slug] = {
                    "slug": slug,
                    "home_name": home,
                    "away_name": away,
                    "league": fix.get("league_name", "?"),
                    "kick_off": "?",
                    "kick_off_unix": 0,
                    "html": "",
                    "picks": [],
                    "error": str(e),
                }

        with _lock:
            _state["fixtures"] = fixtures
            _state["reports"] = reports
            _state["last_refresh"] = datetime.now(timezone.utc)
    finally:
        with _lock:
            _state["refreshing"] = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    with _lock:
        reports = list(_state["reports"].values())
        last_refresh = _state["last_refresh"]
        refreshing = _state["refreshing"]

    # Sort by kick-off time
    reports.sort(key=lambda r: r.get("kick_off_unix", 0))

    # Count high-confidence picks per match
    for r in reports:
        high_picks = [p for p in r.get("picks", [])
                      if p.get("hits", 0) / max(p.get("total", 1), 1) * 100 >= 80]
        r["high_pick_count"] = len(high_picks)

    return render_template(
        "dashboard.html",
        reports=reports,
        last_refresh=last_refresh,
        refreshing=refreshing,
        match_count=len(reports),
    )


@app.route("/report/<slug>")
def report(slug):
    with _lock:
        r = _state["reports"].get(slug)
    if not r or not r.get("html"):
        abort(404)
    return r["html"]


@app.route("/refresh", methods=["POST"])
def trigger_refresh():
    """Manually trigger a refresh."""
    t = threading.Thread(target=refresh_reports, daemon=True)
    t.start()
    return redirect(url_for("dashboard"))


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "match_count": len(_state["reports"]),
            "last_refresh": _state["last_refresh"].isoformat() if _state["last_refresh"] else None,
            "refreshing": _state["refreshing"],
            "api_credits_used": fa.api_credits_used,
        })


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

def start_scheduler():
    """Run refresh every 30 minutes in a background thread."""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(refresh_reports, "interval", minutes=30, id="auto_refresh")
    scheduler.start()


# ---------------------------------------------------------------------------
# Startup (works with both `python app.py` and gunicorn --preload)
# ---------------------------------------------------------------------------

_started = False

def _startup():
    global _started
    if _started:
        return
    _started = True

    if not fa.API_KEY:
        print("Warning: FOOTYSTATS_API_KEY not set. Add it to .env or environment.")
        return

    # Initial fetch in background so the app starts serving immediately
    t = threading.Thread(target=refresh_reports, daemon=True)
    t.start()

    # Start recurring scheduler
    start_scheduler()
    print("Scheduler started (refreshes every 30 min).")


# Auto-start when module is loaded (gunicorn --preload)
_startup()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
