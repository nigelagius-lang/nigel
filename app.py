from __future__ import annotations
"""
Football Analysis Dashboard — auto-updating web app.

Shows today's fixtures and lets you analyze individual matches on demand
to conserve API credits.
"""

import os
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, abort, redirect, url_for, request

load_dotenv()

# Import analysis engine
import football_analysis as fa

# ---------------------------------------------------------------------------
# League-to-country mapping (fallback when API doesn't provide country)
# ---------------------------------------------------------------------------
LEAGUE_COUNTRY_MAP = {
    # England
    "premier league": "England",
    "championship": "England",
    "league one": "England",
    "league two": "England",
    "national league": "England",
    "fa cup": "England",
    "efl cup": "England",
    "carabao cup": "England",
    "community shield": "England",
    # Germany
    "bundesliga": "Germany",
    "2. bundesliga": "Germany",
    "3. liga": "Germany",
    "dfb pokal": "Germany",
    "dfb-pokal": "Germany",
    # France
    "ligue 1": "France",
    "ligue 2": "France",
    "coupe de france": "France",
    # Italy
    "serie a": "Italy",
    "serie b": "Italy",
    "coppa italia": "Italy",
    # Spain
    "la liga": "Spain",
    "laliga": "Spain",
    "segunda division": "Spain",
    "segunda": "Spain",
    "copa del rey": "Spain",
    # Netherlands
    "eredivisie": "Netherlands",
    "eerste divisie": "Netherlands",
    # Portugal
    "primeira liga": "Portugal",
    "liga portugal": "Portugal",
    # Belgium
    "jupiler pro league": "Belgium",
    "pro league": "Belgium",
    # Turkey
    "super lig": "Turkey",
    "süper lig": "Turkey",
    # Scotland
    "scottish premiership": "Scotland",
    "scottish championship": "Scotland",
    # Greece
    "super league": "Greece",
    # Austria
    "austrian bundesliga": "Austria",
    # Switzerland
    "super league": "Switzerland",
    "swiss super league": "Switzerland",
    # Denmark
    "superliga": "Denmark",
    "superligaen": "Denmark",
    # Sweden
    "allsvenskan": "Sweden",
    # Norway
    "eliteserien": "Norway",
    # Poland
    "ekstraklasa": "Poland",
    # Czech Republic
    "czech first league": "Czech Republic",
    # Russia
    "russian premier league": "Russia",
    # Ukraine
    "ukrainian premier league": "Ukraine",
    # USA
    "mls": "USA",
    "major league soccer": "USA",
    # Brazil
    "serie a": "Brazil",
    "brasileirao": "Brazil",
    # Argentina
    "liga profesional": "Argentina",
    "primera division": "Argentina",
    # Mexico
    "liga mx": "Mexico",
    # Australia
    "a-league": "Australia",
    # Japan
    "j1 league": "Japan",
    "j-league": "Japan",
    # South Korea
    "k league": "South Korea",
    "k league 1": "South Korea",
    # China
    "chinese super league": "China",
    # Saudi Arabia
    "saudi pro league": "Saudi Arabia",
    # International
    "champions league": "Europe",
    "europa league": "Europe",
    "conference league": "Europe",
    "euro": "Europe",
    "world cup": "International",
    "copa america": "International",
    "nations league": "Europe",
    "africa cup": "Africa",
    "asian cup": "Asia",
}


def _guess_country(fixture: dict) -> str:
    """Determine country from fixture data, using API field or league name."""
    # 1. Try API-provided country field
    country = (fixture.get("country") or "").strip()
    if country:
        return country

    # 2. Try matching league name against our map
    league = (
        fixture.get("league_name")
        or fixture.get("competition_name")
        or ""
    ).strip()
    league_lower = league.lower()

    # Exact match
    if league_lower in LEAGUE_COUNTRY_MAP:
        return LEAGUE_COUNTRY_MAP[league_lower]

    # Partial/substring match
    for pattern, mapped_country in LEAGUE_COUNTRY_MAP.items():
        if pattern in league_lower or league_lower in pattern:
            return mapped_country

    # 3. Check if league name starts with "Country - League" format
    if " - " in league:
        return league.split(" - ", 1)[0].strip()

    return "Other"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key-change-me")

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {
    "fixtures": [],        # raw fixture dicts from API
    "fixture_map": {},     # slug -> raw fixture dict (for on-demand analysis)
    "reports": {},         # slug -> {html, home_name, away_name, league, kick_off, ...}
    "last_refresh": None,  # datetime of last fixture list refresh
    "refreshing": False,
    "analyzing": set(),    # slugs currently being analyzed
}


def _slug(home: str, away: str) -> str:
    """Create a URL-safe slug from team names."""
    return f"{home}-vs-{away}".lower().replace(" ", "-")


def refresh_fixtures(date_str: str | None = None):
    """Fetch today's fixture list only (1 API call). No analysis."""
    with _lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True

    try:
        fixtures = fa.fetch_todays_matches(date_str)
        fixture_map = {}
        for fix in fixtures:
            home = fix.get("home_name", "Home")
            away = fix.get("away_name", "Away")
            slug = _slug(home, away)
            fix["_slug"] = slug
            fixture_map[slug] = fix

        with _lock:
            _state["fixtures"] = fixtures
            _state["fixture_map"] = fixture_map
            _state["last_refresh"] = datetime.now(timezone.utc)
    finally:
        with _lock:
            _state["refreshing"] = False


def analyze_single(slug: str):
    """Run analysis for a single fixture by slug."""
    with _lock:
        fix = _state["fixture_map"].get(slug)
        if not fix:
            return
        if slug in _state["analyzing"]:
            return
        _state["analyzing"].add(slug)

    try:
        home = fix.get("home_name", "Home")
        away = fix.get("away_name", "Away")
        try:
            result = fa.run_analysis(fix)
            result["slug"] = slug
        except Exception as e:
            ko_unix = fix.get("date_unix", 0)
            ko_str = (
                datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC")
                if ko_unix else "TBD"
            )
            result = {
                "slug": slug,
                "home_name": home,
                "away_name": away,
                "league": fix.get("league_name", "?"),
                "kick_off": ko_str,
                "kick_off_unix": int(ko_unix) if ko_unix else 0,
                "html": "",
                "picks": [],
                "error": str(e),
            }

        with _lock:
            _state["reports"][slug] = result
    finally:
        with _lock:
            _state["analyzing"].discard(slug)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    with _lock:
        fixtures = list(_state["fixtures"])
        reports = dict(_state["reports"])
        last_refresh = _state["last_refresh"]
        refreshing = _state["refreshing"]
        analyzing = set(_state["analyzing"])

    # Build fixture cards with basic info + analysis status
    cards = []
    for fix in fixtures:
        home = fix.get("home_name", "Home")
        away = fix.get("away_name", "Away")
        slug = _slug(home, away)
        league = fix.get("league_name", fix.get("competition_name", ""))
        ko_unix = fix.get("date_unix", 0)
        if ko_unix:
            kick_off = datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC")
        else:
            kick_off = fix.get("time", fix.get("ko_time", "TBD"))

        report = reports.get(slug)
        is_analyzing = slug in analyzing

        card = {
            "slug": slug,
            "home_name": home,
            "away_name": away,
            "league": league,
            "country": _guess_country(fix),
            "kick_off": kick_off,
            "kick_off_unix": int(ko_unix) if ko_unix else 0,
            "analyzed": report is not None,
            "analyzing": is_analyzing,
            "error": report.get("error") if report else None,
            "high_pick_count": 0,
            "picks_count": 0,
        }

        if report and not report.get("error"):
            picks = report.get("picks", [])
            high = [p for p in picks
                    if p.get("hits", 0) / max(p.get("total", 1), 1) * 100 >= 80]
            card["high_pick_count"] = len(high)
            card["picks_count"] = len(picks)

        cards.append(card)

    # Sort by kick-off time
    cards.sort(key=lambda c: c.get("kick_off_unix", 0))

    # Group by league (preserving kick-off order within each group)
    from collections import OrderedDict
    leagues: OrderedDict[str, list] = OrderedDict()
    for c in cards:
        league = c.get("league") or "Other"
        leagues.setdefault(league, []).append(c)

    # Country data for the dropdown
    TOP_COUNTRIES = ["England", "France", "Germany", "Spain", "Italy"]
    league_country: dict[str, str] = {}
    country_counts: dict[str, int] = {}
    for c in cards:
        country = c.get("country") or "Other"
        lg = c.get("league") or "Other"
        league_country[lg] = country
        country_counts[country] = country_counts.get(country, 0) + 1

    top_countries = [(ct, country_counts[ct]) for ct in TOP_COUNTRIES if ct in country_counts]
    other_countries = sorted(
        [(ct, n) for ct, n in country_counts.items() if ct not in TOP_COUNTRIES]
    )

    return render_template(
        "dashboard.html",
        leagues=leagues,
        league_country=league_country,
        top_countries=top_countries,
        other_countries=other_countries,
        last_refresh=last_refresh,
        refreshing=refreshing,
        match_count=len(cards),
        api_credits=fa.api_credits_used,
    )


@app.route("/report/<slug>")
def report(slug):
    with _lock:
        r = _state["reports"].get(slug)
    if not r or not r.get("html"):
        abort(404)
    return r["html"]


@app.route("/analyze/<slug>", methods=["POST"])
def trigger_analyze(slug):
    """Analyze a single match on demand."""
    with _lock:
        exists = slug in _state["fixture_map"]
        already = slug in _state["reports"]
        busy = slug in _state["analyzing"]

    if not exists:
        return jsonify({"error": "Fixture not found"}), 404
    if busy:
        return jsonify({"status": "analyzing"}), 202
    if already:
        return jsonify({"status": "done"}), 200

    t = threading.Thread(target=analyze_single, args=(slug,), daemon=True)
    t.start()
    return jsonify({"status": "analyzing"}), 202


@app.route("/api/analyze-status/<slug>")
def analyze_status(slug):
    """Check if analysis is complete for a fixture."""
    with _lock:
        report = _state["reports"].get(slug)
        busy = slug in _state["analyzing"]

    if report:
        return jsonify({
            "status": "done",
            "error": report.get("error"),
            "high_pick_count": len([
                p for p in report.get("picks", [])
                if p.get("hits", 0) / max(p.get("total", 1), 1) * 100 >= 80
            ]),
            "picks_count": len(report.get("picks", [])),
        })
    if busy:
        return jsonify({"status": "analyzing"})
    return jsonify({"status": "pending"})


@app.route("/refresh", methods=["POST"])
def trigger_refresh():
    """Manually trigger a fixture list refresh."""
    t = threading.Thread(target=refresh_fixtures, daemon=True)
    t.start()
    return redirect(url_for("dashboard"))


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "fixture_count": len(_state["fixtures"]),
            "analyzed_count": len(_state["reports"]),
            "last_refresh": _state["last_refresh"].isoformat() if _state["last_refresh"] else None,
            "refreshing": _state["refreshing"],
            "api_credits_used": fa.api_credits_used,
        })


# ---------------------------------------------------------------------------
# Startup
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

    # Fetch fixture list only (cheap — 1 API call)
    t = threading.Thread(target=refresh_fixtures, daemon=True)
    t.start()
    print("Fixture list loading in background...")


# Auto-start when module is loaded (gunicorn --preload)
_startup()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
