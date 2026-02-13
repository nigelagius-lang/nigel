from __future__ import annotations
"""
Football Analysis Dashboard — auto-updating web app.

Shows today's fixtures and lets you analyze individual matches on demand
to conserve API credits.
"""

import math
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
    "over25_status": "idle",  # idle | running | done
    "over25_results": [],     # list of top picks
    "over25_error": None,
    "over10c_status": "idle",  # idle | running | done
    "over10c_results": [],     # list of top corner picks
    "over10c_error": None,
    "over15fh_status": "idle",  # idle | running | done
    "over15fh_results": [],     # list of top first-half goals picks
    "over15fh_error": None,
    "over35c_status": "idle",   # idle | running | done
    "over35c_results": [],      # list of high-prob over 3.5 cards picks
    "over35c_error": None,
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
# Over 2.5 Goals analysis
# ---------------------------------------------------------------------------

def _run_over25():
    """Analyze all fixtures for Over 2.5 Goals probability."""
    with _lock:
        if _state["over25_status"] == "running":
            return
        _state["over25_status"] = "running"
        _state["over25_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        results = []
        for fix in fixtures:
            home_name = fix.get("home_name", "Home")
            away_name = fix.get("away_name", "Away")
            home_id = int(fix.get("homeID", fix.get("home_id", 0)))
            away_id = int(fix.get("awayID", fix.get("away_id", 0)))
            league = fix.get("league_name", fix.get("competition_name", "Unknown"))
            ko_unix = fix.get("date_unix", 0)
            ko_str = (
                datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC")
                if ko_unix else "TBD"
            )

            if not home_id or not away_id:
                continue

            # Get season_id for league-matches lookup
            season_id = None
            for key in ("competition_id", "season_id", "league_id", "season"):
                val = fix.get(key)
                if val is not None:
                    try:
                        season_id = int(val)
                        if season_id > 0:
                            break
                    except (ValueError, TypeError):
                        continue

            # Fetch last 10 matches for each team
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            if not home_last10 and not away_last10:
                continue

            # Calculate Over 2.5 stats for home team
            home_over25 = 0
            home_total_goals = []
            for m in home_last10:
                tg = m.get("total_goals", -1)
                if tg >= 0:
                    home_total_goals.append(tg)
                    if tg > 2:
                        home_over25 += 1
            home_matches = len(home_total_goals)
            home_pct = (home_over25 / home_matches * 100) if home_matches > 0 else 0
            home_avg_scored = 0.0
            home_avg_conceded = 0.0
            if home_last10:
                scored = [m.get("goals_scored", 0) for m in home_last10 if m.get("goals_scored", -1) >= 0]
                conceded = [m.get("goals_conceded", 0) for m in home_last10 if m.get("goals_conceded", -1) >= 0]
                home_avg_scored = sum(scored) / len(scored) if scored else 0
                home_avg_conceded = sum(conceded) / len(conceded) if conceded else 0

            # Calculate Over 2.5 stats for away team
            away_over25 = 0
            away_total_goals = []
            for m in away_last10:
                tg = m.get("total_goals", -1)
                if tg >= 0:
                    away_total_goals.append(tg)
                    if tg > 2:
                        away_over25 += 1
            away_matches = len(away_total_goals)
            away_pct = (away_over25 / away_matches * 100) if away_matches > 0 else 0
            away_avg_scored = 0.0
            away_avg_conceded = 0.0
            if away_last10:
                scored = [m.get("goals_scored", 0) for m in away_last10 if m.get("goals_scored", -1) >= 0]
                conceded = [m.get("goals_conceded", 0) for m in away_last10 if m.get("goals_conceded", -1) >= 0]
                away_avg_scored = sum(scored) / len(scored) if scored else 0
                away_avg_conceded = sum(conceded) / len(conceded) if conceded else 0

            # Combined probability: weighted average of both teams' O2.5 rates
            # Also factor in expected goals (home attack + away attack vs defenses)
            if home_matches > 0 and away_matches > 0:
                combined_pct = (home_pct + away_pct) / 2
            elif home_matches > 0:
                combined_pct = home_pct
            elif away_matches > 0:
                combined_pct = away_pct
            else:
                continue

            expected_goals = home_avg_scored + away_avg_scored

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": int(ko_unix) if ko_unix else 0,
                "probability": round(combined_pct, 1),
                "home_over25_pct": round(home_pct, 1),
                "away_over25_pct": round(away_pct, 1),
                "home_avg_scored": round(home_avg_scored, 2),
                "home_avg_conceded": round(home_avg_conceded, 2),
                "away_avg_scored": round(away_avg_scored, 2),
                "away_avg_conceded": round(away_avg_conceded, 2),
                "expected_goals": round(expected_goals, 2),
                "home_matches": home_matches,
                "away_matches": away_matches,
            })

        # Sort by probability descending, take top 4
        results.sort(key=lambda r: r["probability"], reverse=True)
        top4 = results[:4]

        with _lock:
            _state["over25_results"] = top4
            _state["over25_status"] = "done"

    except Exception as e:
        with _lock:
            _state["over25_status"] = "done"
            _state["over25_error"] = str(e)


@app.route("/api/over25", methods=["POST"])
def trigger_over25():
    """Trigger Over 2.5 Goals analysis for all fixtures."""
    with _lock:
        if _state["over25_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_over25, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/over25-status")
def over25_status():
    """Poll Over 2.5 analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["over25_status"],
            "results": _state["over25_results"],
            "error": _state["over25_error"],
            "api_credits_used": fa.api_credits_used,
        })


# ---------------------------------------------------------------------------
# Over 10 Corners analysis
# ---------------------------------------------------------------------------

def _run_over10corners():
    """Analyze all fixtures for Over 10 Corners probability."""
    with _lock:
        if _state["over10c_status"] == "running":
            return
        _state["over10c_status"] = "running"
        _state["over10c_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        results = []
        for fix in fixtures:
            home_name = fix.get("home_name", "Home")
            away_name = fix.get("away_name", "Away")
            home_id = int(fix.get("homeID", fix.get("home_id", 0)))
            away_id = int(fix.get("awayID", fix.get("away_id", 0)))
            league = fix.get("league_name", fix.get("competition_name", "Unknown"))
            ko_unix = fix.get("date_unix", 0)
            ko_str = (
                datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC")
                if ko_unix else "TBD"
            )

            if not home_id or not away_id:
                continue

            # Get season_id for league-matches lookup
            season_id = None
            for key in ("competition_id", "season_id", "league_id", "season"):
                val = fix.get(key)
                if val is not None:
                    try:
                        season_id = int(val)
                        if season_id > 0:
                            break
                    except (ValueError, TypeError):
                        continue

            # Fetch last 10 matches for each team
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            if not home_last10 and not away_last10:
                continue

            # Calculate Over 10 Corners stats for home team
            home_over10 = 0
            home_corner_totals = []
            home_corners_for = []
            home_corners_against = []
            for m in home_last10:
                mc = m.get("match_corners", -1)
                cf = m.get("corners", -1)
                ca = m.get("corners_against", -1)
                if mc >= 0:
                    home_corner_totals.append(mc)
                    if mc > 10:
                        home_over10 += 1
                if cf >= 0:
                    home_corners_for.append(cf)
                if ca >= 0:
                    home_corners_against.append(ca)

            home_matches = len(home_corner_totals)
            home_pct = (home_over10 / home_matches * 100) if home_matches > 0 else 0
            home_avg_total = sum(home_corner_totals) / home_matches if home_matches > 0 else 0
            home_avg_for = sum(home_corners_for) / len(home_corners_for) if home_corners_for else 0
            home_avg_against = sum(home_corners_against) / len(home_corners_against) if home_corners_against else 0

            # Calculate Over 10 Corners stats for away team
            away_over10 = 0
            away_corner_totals = []
            away_corners_for = []
            away_corners_against = []
            for m in away_last10:
                mc = m.get("match_corners", -1)
                cf = m.get("corners", -1)
                ca = m.get("corners_against", -1)
                if mc >= 0:
                    away_corner_totals.append(mc)
                    if mc > 10:
                        away_over10 += 1
                if cf >= 0:
                    away_corners_for.append(cf)
                if ca >= 0:
                    away_corners_against.append(ca)

            away_matches = len(away_corner_totals)
            away_pct = (away_over10 / away_matches * 100) if away_matches > 0 else 0
            away_avg_total = sum(away_corner_totals) / away_matches if away_matches > 0 else 0
            away_avg_for = sum(away_corners_for) / len(away_corners_for) if away_corners_for else 0
            away_avg_against = sum(away_corners_against) / len(away_corners_against) if away_corners_against else 0

            # Combined probability
            if home_matches > 0 and away_matches > 0:
                combined_pct = (home_pct + away_pct) / 2
                combined_avg = (home_avg_total + away_avg_total) / 2
            elif home_matches > 0:
                combined_pct = home_pct
                combined_avg = home_avg_total
            elif away_matches > 0:
                combined_pct = away_pct
                combined_avg = away_avg_total
            else:
                continue

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": int(ko_unix) if ko_unix else 0,
                "probability": round(combined_pct, 1),
                "home_over10_pct": round(home_pct, 1),
                "away_over10_pct": round(away_pct, 1),
                "home_avg_total_corners": round(home_avg_total, 1),
                "away_avg_total_corners": round(away_avg_total, 1),
                "combined_avg_corners": round(combined_avg, 1),
                "home_avg_for": round(home_avg_for, 1),
                "home_avg_against": round(home_avg_against, 1),
                "away_avg_for": round(away_avg_for, 1),
                "away_avg_against": round(away_avg_against, 1),
                "home_matches": home_matches,
                "away_matches": away_matches,
            })

        # Sort by probability descending, take top 4
        results.sort(key=lambda r: r["probability"], reverse=True)
        top4 = results[:4]

        with _lock:
            _state["over10c_results"] = top4
            _state["over10c_status"] = "done"

    except Exception as e:
        with _lock:
            _state["over10c_status"] = "done"
            _state["over10c_error"] = str(e)


@app.route("/api/over10corners", methods=["POST"])
def trigger_over10corners():
    """Trigger Over 10 Corners analysis for all fixtures."""
    with _lock:
        if _state["over10c_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_over10corners, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/over10corners-status")
def over10corners_status():
    """Poll Over 10 Corners analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["over10c_status"],
            "results": _state["over10c_results"],
            "error": _state["over10c_error"],
            "api_credits_used": fa.api_credits_used,
        })


# ---------------------------------------------------------------------------
# Over 1.5 First Half Goals analysis
# ---------------------------------------------------------------------------

def _run_over15fh():
    """Analyze all fixtures for Over 1.5 First Half Goals probability."""
    with _lock:
        if _state["over15fh_status"] == "running":
            return
        _state["over15fh_status"] = "running"
        _state["over15fh_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        results = []
        for fix in fixtures:
            home_name = fix.get("home_name", "Home")
            away_name = fix.get("away_name", "Away")
            home_id = int(fix.get("homeID", fix.get("home_id", 0)))
            away_id = int(fix.get("awayID", fix.get("away_id", 0)))
            league = fix.get("league_name", fix.get("competition_name", "Unknown"))
            ko_unix = fix.get("date_unix", 0)
            ko_str = (
                datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC")
                if ko_unix else "TBD"
            )

            if not home_id or not away_id:
                continue

            # Get season_id for league-matches lookup
            season_id = None
            for key in ("competition_id", "season_id", "league_id", "season"):
                val = fix.get(key)
                if val is not None:
                    try:
                        season_id = int(val)
                        if season_id > 0:
                            break
                    except (ValueError, TypeError):
                        continue

            # Fetch last 10 matches for each team
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            if not home_last10 and not away_last10:
                continue

            # Calculate Over 1.5 FH stats for home team
            home_over15 = 0
            home_fh_totals = []
            home_fh_scored = []
            home_fh_conceded = []
            for m in home_last10:
                tg1h = m.get("total_goals_1h", -1)
                gs1h = m.get("goals_scored_1h", -1)
                gc1h = m.get("goals_conceded_1h", -1)
                if tg1h >= 0:
                    home_fh_totals.append(tg1h)
                    if tg1h > 1:
                        home_over15 += 1
                if gs1h >= 0:
                    home_fh_scored.append(gs1h)
                if gc1h >= 0:
                    home_fh_conceded.append(gc1h)

            home_matches = len(home_fh_totals)
            home_pct = (home_over15 / home_matches * 100) if home_matches > 0 else 0
            home_avg_fh_total = sum(home_fh_totals) / home_matches if home_matches > 0 else 0
            home_avg_fh_scored = sum(home_fh_scored) / len(home_fh_scored) if home_fh_scored else 0
            home_avg_fh_conceded = sum(home_fh_conceded) / len(home_fh_conceded) if home_fh_conceded else 0

            # Calculate Over 1.5 FH stats for away team
            away_over15 = 0
            away_fh_totals = []
            away_fh_scored = []
            away_fh_conceded = []
            for m in away_last10:
                tg1h = m.get("total_goals_1h", -1)
                gs1h = m.get("goals_scored_1h", -1)
                gc1h = m.get("goals_conceded_1h", -1)
                if tg1h >= 0:
                    away_fh_totals.append(tg1h)
                    if tg1h > 1:
                        away_over15 += 1
                if gs1h >= 0:
                    away_fh_scored.append(gs1h)
                if gc1h >= 0:
                    away_fh_conceded.append(gc1h)

            away_matches = len(away_fh_totals)
            away_pct = (away_over15 / away_matches * 100) if away_matches > 0 else 0
            away_avg_fh_total = sum(away_fh_totals) / away_matches if away_matches > 0 else 0
            away_avg_fh_scored = sum(away_fh_scored) / len(away_fh_scored) if away_fh_scored else 0
            away_avg_fh_conceded = sum(away_fh_conceded) / len(away_fh_conceded) if away_fh_conceded else 0

            # Combined probability
            if home_matches > 0 and away_matches > 0:
                combined_pct = (home_pct + away_pct) / 2
                combined_avg = (home_avg_fh_total + away_avg_fh_total) / 2
            elif home_matches > 0:
                combined_pct = home_pct
                combined_avg = home_avg_fh_total
            elif away_matches > 0:
                combined_pct = away_pct
                combined_avg = away_avg_fh_total
            else:
                continue

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": int(ko_unix) if ko_unix else 0,
                "probability": round(combined_pct, 1),
                "home_over15fh_pct": round(home_pct, 1),
                "away_over15fh_pct": round(away_pct, 1),
                "home_avg_fh_total": round(home_avg_fh_total, 2),
                "away_avg_fh_total": round(away_avg_fh_total, 2),
                "combined_avg_fh": round(combined_avg, 2),
                "home_avg_fh_scored": round(home_avg_fh_scored, 2),
                "home_avg_fh_conceded": round(home_avg_fh_conceded, 2),
                "away_avg_fh_scored": round(away_avg_fh_scored, 2),
                "away_avg_fh_conceded": round(away_avg_fh_conceded, 2),
                "home_matches": home_matches,
                "away_matches": away_matches,
            })

        # Sort by probability descending, take top 4
        results.sort(key=lambda r: r["probability"], reverse=True)
        top4 = results[:4]

        with _lock:
            _state["over15fh_results"] = top4
            _state["over15fh_status"] = "done"

    except Exception as e:
        with _lock:
            _state["over15fh_status"] = "done"
            _state["over15fh_error"] = str(e)


@app.route("/api/over15fh", methods=["POST"])
def trigger_over15fh():
    """Trigger Over 1.5 First Half Goals analysis for all fixtures."""
    with _lock:
        if _state["over15fh_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_over15fh, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/over15fh-status")
def over15fh_status():
    """Poll Over 1.5 First Half Goals analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["over15fh_status"],
            "results": _state["over15fh_results"],
            "error": _state["over15fh_error"],
            "api_credits_used": fa.api_credits_used,
        })


# ---------------------------------------------------------------------------
# Over 3.5 Cards – Poisson / Negative Binomial model
# ---------------------------------------------------------------------------

# Minimum matches with card data required per team before we trust the data.
# Below this threshold the team's contribution is replaced by the league avg.
_MIN_CARD_MATCHES = 3

# Default fouls-per-game when a team lacks data (league-neutral estimate)
_DEFAULT_FOULS_PG = 12.0


def _poisson_cdf(lam, k):
    """Compute P(X <= k) for Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    cdf = 0.0
    for i in range(k + 1):
        cdf += math.exp(-lam) * (lam ** i) / math.factorial(i)
    return min(cdf, 1.0)


def _neg_binom_cdf(mean, var, k):
    """Compute P(X <= k) for Negative Binomial (mean/variance parameterisation).
    Falls back to Poisson when variance <= mean.
    """
    if var <= mean or mean <= 0:
        return _poisson_cdf(mean, k)
    p = mean / var          # success probability
    r = mean * p / (1 - p)  # shape = mean² / (var - mean)
    cdf = 0.0
    for i in range(k + 1):
        log_pmf = (math.lgamma(i + r) - math.lgamma(i + 1) - math.lgamma(r)
                   + i * math.log(1 - p) + r * math.log(p))
        cdf += math.exp(log_pmf)
    return min(cdf, 1.0)


def _weighted_avg(values, alpha=0.15):
    """Exponentially-decay weighted average (index 0 = most recent)."""
    if not values:
        return 0.0
    weights = [math.exp(-alpha * i) for i in range(len(values))]
    tw = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / tw


def _compute_league_card_stats(season_id):
    """Return (avg_total_cards, variance, avg_team_cards, avg_fouls) for a league."""
    matches = fa.fetch_league_matches(season_id)
    card_counts = []
    team_card_counts = []
    foul_counts = []
    for m in matches:
        if m.get("status") != "complete":
            continue
        hy = fa.safe_int(m.get("team_a_yellow_cards",
              m.get("home_yellow_cards", m.get("homeYellowCards", -1))))
        ay = fa.safe_int(m.get("team_b_yellow_cards",
              m.get("away_yellow_cards", m.get("awayYellowCards", -1))))
        if hy < 0 or ay < 0:
            continue
        hr = fa.safe_int(m.get("team_a_red_cards",
              m.get("home_red_cards", m.get("homeRedCards", -1))))
        ar = fa.safe_int(m.get("team_b_red_cards",
              m.get("away_red_cards", m.get("awayRedCards", -1))))
        total = hy + ay + max(hr, 0) + max(ar, 0)
        card_counts.append(total)
        # Average per-team cards (home side as representative)
        team_card_counts.append(hy + max(hr, 0))
        team_card_counts.append(ay + max(ar, 0))
        # Fouls
        hf = fa.safe_int(m.get("team_a_fouls",
              m.get("home_fouls", m.get("homeFouls", -1))))
        af = fa.safe_int(m.get("team_b_fouls",
              m.get("away_fouls", m.get("awayFouls", -1))))
        if hf >= 0:
            foul_counts.append(hf)
        if af >= 0:
            foul_counts.append(af)
    if not card_counts:
        return 3.8, 3.0, 1.9, _DEFAULT_FOULS_PG
    avg = sum(card_counts) / len(card_counts)
    var = (sum((c - avg) ** 2 for c in card_counts) / len(card_counts)
           if len(card_counts) > 1 else avg)
    avg_team = sum(team_card_counts) / len(team_card_counts) if team_card_counts else avg / 2
    avg_fouls = sum(foul_counts) / len(foul_counts) if foul_counts else _DEFAULT_FOULS_PG
    return avg, var, avg_team, avg_fouls


def _compute_referee_card_avg_from_history(season_id, referee_id):
    """Compute referee's avg total cards from league-matches history (fallback)."""
    matches = fa.fetch_league_matches(season_id)
    card_counts = []
    for m in matches:
        if m.get("status") != "complete":
            continue
        mid_ref = m.get("refereeID", m.get("referee_id", 0))
        try:
            mid_ref = int(mid_ref) if mid_ref else 0
        except (ValueError, TypeError):
            mid_ref = 0
        if mid_ref != referee_id:
            continue
        hy = fa.safe_int(m.get("team_a_yellow_cards",
              m.get("home_yellow_cards", m.get("homeYellowCards", -1))))
        ay = fa.safe_int(m.get("team_b_yellow_cards",
              m.get("away_yellow_cards", m.get("awayYellowCards", -1))))
        if hy < 0 or ay < 0:
            continue
        hr = fa.safe_int(m.get("team_a_red_cards",
              m.get("home_red_cards", m.get("homeRedCards", -1))))
        ar = fa.safe_int(m.get("team_b_red_cards",
              m.get("away_red_cards", m.get("awayRedCards", -1))))
        total = hy + ay + max(hr, 0) + max(ar, 0)
        card_counts.append(total)
    if not card_counts:
        return None, 0
    return sum(card_counts) / len(card_counts), len(card_counts)


def _resolve_referee(referee_id, referee_name_hint, season_id):
    """Resolve referee name and average cards using multiple data sources.

    Priority:
      1. FootyStats /referee endpoint (gets name + potentially card stats)
      2. FootyStats /league-referees for the season (name + per-league stats)
      3. Compute from league-matches history (our own calculation)

    Returns (name: str, avg_cards: float | None, match_count: int).
    """
    name = referee_name_hint or ""
    avg_cards = None
    match_count = 0

    # --- Source 1: Individual referee endpoint ---
    if referee_id:
        ref_data = fa.fetch_referee(referee_id)
        if ref_data:
            name = name or ref_data.get("full_name", ref_data.get("name", ""))
            # The API may return card stats under various field names
            for field in ("cards_per_game", "avg_cards_per_game",
                          "cards_per_match", "average_cards",
                          "yellow_cards_per_game"):
                val = ref_data.get(field)
                if val is not None:
                    try:
                        avg_cards = float(val)
                        if avg_cards > 0:
                            match_count = int(ref_data.get(
                                "matches", ref_data.get("appearances", 50)
                            ))
                            break
                    except (ValueError, TypeError):
                        continue

    # --- Source 2: League referees endpoint ---
    if avg_cards is None and season_id and referee_id:
        league_refs = fa.fetch_league_referees(season_id)
        for ref in league_refs:
            rid = ref.get("id", ref.get("referee_id", 0))
            try:
                rid = int(rid) if rid else 0
            except (ValueError, TypeError):
                rid = 0
            if rid != referee_id:
                continue
            name = name or ref.get("full_name", ref.get("name", ""))
            for field in ("cards_per_game", "avg_cards_per_game",
                          "cards_per_match", "average_cards",
                          "yellow_cards_per_game"):
                val = ref.get(field)
                if val is not None:
                    try:
                        avg_cards = float(val)
                        if avg_cards > 0:
                            match_count = int(ref.get(
                                "matches", ref.get("appearances", 0)
                            ))
                            break
                    except (ValueError, TypeError):
                        continue
            break  # found the referee entry

    # --- Source 3: Compute from league-matches history ---
    if avg_cards is None and season_id and referee_id:
        avg_cards, match_count = _compute_referee_card_avg_from_history(
            season_id, referee_id
        )

    return name.strip() or "", avg_cards, match_count


_COMMON_FOOTBALL_WORDS = frozenset({
    "fc", "cf", "sc", "ac", "as", "us", "ss", "city", "united",
    "athletic", "real", "sporting", "club", "de", "al", "the",
})


def _run_over35cards():
    """Analyse all fixtures for Over 3.5 Total Cards using a probabilistic model.

    Model:
      λ = team_interaction × league_adj × referee_factor × importance × derby
    Distribution:
      Negative Binomial when overdispersed, Poisson otherwise.
    Output:
      P(total_cards >= 4), filtered to >= 60%, ranked descending.

    Key safeguards:
      - Both teams must have >= _MIN_CARD_MATCHES; missing team data is
        replaced by league averages and the match is flagged as partial data.
      - Referee data is resolved via the /referee and /league-referees API
        endpoints, falling back to computing from league-matches history.
      - Confidence tier accounts for data quality (partial → cap at Moderate).
    """
    with _lock:
        if _state["over35c_status"] == "running":
            return
        _state["over35c_status"] = "running"
        _state["over35c_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff_ts = now_ts + 48 * 3600  # next 48 hours

        # Caches (lazily populated, shared across fixtures in the same league)
        league_baselines: dict[int, tuple[float, float, float, float]] = {}
        referee_cache: dict[int, tuple[str, float | None, int]] = {}

        results = []

        for fix in fixtures:
            ko_unix = int(fix.get("date_unix", 0) or 0)
            if ko_unix and (ko_unix < now_ts - 3600 or ko_unix > cutoff_ts):
                continue

            home_name = fix.get("home_name", "Home")
            away_name = fix.get("away_name", "Away")
            home_id = int(fix.get("homeID", fix.get("home_id", 0)))
            away_id = int(fix.get("awayID", fix.get("away_id", 0)))
            league = fix.get("league_name", fix.get("competition_name", "Unknown"))
            ko_str = (
                datetime.fromtimestamp(ko_unix, tz=timezone.utc).strftime("%H:%M UTC")
                if ko_unix else "TBD"
            )

            if not home_id or not away_id:
                continue

            # --- Season ID -------------------------------------------------
            season_id = None
            for key in ("competition_id", "season_id", "league_id", "season"):
                val = fix.get(key)
                if val is not None:
                    try:
                        season_id = int(val)
                        if season_id > 0:
                            break
                    except (ValueError, TypeError):
                        continue

            # --- League baseline (cached) ----------------------------------
            if season_id and season_id not in league_baselines:
                league_baselines[season_id] = _compute_league_card_stats(season_id)
            lg_avg, lg_var, lg_team_avg, lg_fouls_avg = league_baselines.get(
                season_id, (3.8, 3.0, 1.9, _DEFAULT_FOULS_PG)
            )

            # --- Referee resolution (cached per referee_id) ----------------
            referee_name_hint = (
                fix.get("referee") or fix.get("referee_name") or ""
            ).strip()
            referee_id = fix.get("refereeID", fix.get("referee_id", 0))
            try:
                referee_id = int(referee_id) if referee_id else 0
            except (ValueError, TypeError):
                referee_id = 0

            if referee_id and referee_id not in referee_cache:
                referee_cache[referee_id] = _resolve_referee(
                    referee_id, referee_name_hint, season_id
                )
            if referee_id:
                ref_name, ref_avg_cards, ref_matches = referee_cache[referee_id]
            else:
                ref_name, ref_avg_cards, ref_matches = "", None, 0

            # Use the best available referee name
            referee_display = ref_name or referee_name_hint or ""

            # --- Team last-10 stats ----------------------------------------
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            if not home_last10 and not away_last10:
                continue

            # -- Extract card/foul arrays (most-recent-first) ---------------
            home_match_cards, home_team_cards, home_fouls_list = [], [], []
            for m in home_last10:
                my = m.get("match_yellows", -1)
                mr = m.get("match_reds", -1)
                if my >= 0:
                    home_match_cards.append(my + max(mr, 0))
                ty = m.get("yellows", -1)
                tr = m.get("reds", -1)
                if ty >= 0:
                    home_team_cards.append(ty + max(tr, 0))
                f = m.get("fouls", -1)
                if f >= 0:
                    home_fouls_list.append(f)

            away_match_cards, away_team_cards, away_fouls_list = [], [], []
            for m in away_last10:
                my = m.get("match_yellows", -1)
                mr = m.get("match_reds", -1)
                if my >= 0:
                    away_match_cards.append(my + max(mr, 0))
                ty = m.get("yellows", -1)
                tr = m.get("reds", -1)
                if ty >= 0:
                    away_team_cards.append(ty + max(tr, 0))
                f = m.get("fouls", -1)
                if f >= 0:
                    away_fouls_list.append(f)

            # -- Data-quality gate ------------------------------------------
            # Both teams MUST have sufficient data or we substitute league avg.
            home_has_data = len(home_match_cards) >= _MIN_CARD_MATCHES
            away_has_data = len(away_match_cards) >= _MIN_CARD_MATCHES

            # If NEITHER team has any card data at all, skip entirely
            if not home_match_cards and not away_match_cards:
                continue

            data_quality = "full"

            if home_has_data:
                home_w_match = _weighted_avg(home_match_cards)
                home_w_team = _weighted_avg(home_team_cards) if home_team_cards else lg_team_avg
                home_w_fouls = _weighted_avg(home_fouls_list) if home_fouls_list else lg_fouls_avg
            else:
                # Insufficient home data → use league averages
                home_w_match = lg_avg
                home_w_team = lg_team_avg
                home_w_fouls = lg_fouls_avg
                data_quality = "partial"

            if away_has_data:
                away_w_match = _weighted_avg(away_match_cards)
                away_w_team = _weighted_avg(away_team_cards) if away_team_cards else lg_team_avg
                away_w_fouls = _weighted_avg(away_fouls_list) if away_fouls_list else lg_fouls_avg
            else:
                away_w_match = lg_avg
                away_w_team = lg_team_avg
                away_w_fouls = lg_fouls_avg
                data_quality = "partial"

            if not home_has_data and not away_has_data:
                data_quality = "low"

            # -- Team interaction lambda ------------------------------------
            team_lambda = (home_w_match + away_w_match) / 2

            # -- League baseline adjustment ---------------------------------
            if lg_avg > 0:
                league_adj = 0.7 + 0.3 * (lg_avg / max(team_lambda, 0.1))
                league_adj = max(0.85, min(1.15, league_adj))
            else:
                league_adj = 1.0

            # -- Referee factor ---------------------------------------------
            referee_factor = 1.0
            has_referee = False
            if ref_avg_cards is not None and ref_avg_cards > 0 and lg_avg > 0:
                referee_factor = ref_avg_cards / lg_avg
                referee_factor = max(0.6, min(1.6, referee_factor))
                has_referee = True

            # -- Match importance factor ------------------------------------
            importance_factor = 1.0

            # -- Derby multiplier -------------------------------------------
            derby_factor = 1.0
            home_words = set(home_name.lower().split()) - _COMMON_FOOTBALL_WORDS
            away_words = set(away_name.lower().split()) - _COMMON_FOOTBALL_WORDS
            if home_words & away_words:
                derby_factor = 1.15

            # -- Final λ ----------------------------------------------------
            lam = (team_lambda * league_adj * referee_factor
                   * importance_factor * derby_factor)
            lam = max(0.5, min(12.0, lam))

            # -- Variance for distribution choice ---------------------------
            def _sample_var(vals):
                if len(vals) < 2:
                    return lg_var
                m = sum(vals) / len(vals)
                return sum((c - m) ** 2 for c in vals) / len(vals)

            h_var = _sample_var(home_match_cards) if home_has_data else lg_var
            a_var = _sample_var(away_match_cards) if away_has_data else lg_var
            combined_var = (h_var + a_var) / 2

            # -- P(total cards >= 4) ----------------------------------------
            if combined_var > lam * 1.3:
                prob = 1.0 - _neg_binom_cdf(lam, combined_var, 3)
                model_used = "NegBinom"
            else:
                prob = 1.0 - _poisson_cdf(lam, 3)
                model_used = "Poisson"

            prob_pct = round(prob * 100, 2)

            if prob_pct < 60:
                continue

            # -- Confidence tier (accounts for data quality) ----------------
            if data_quality == "low":
                tier = "Low Data"
            elif data_quality == "partial":
                # Cap at Moderate when one team lacks data
                tier = "Moderate"
            elif prob_pct >= 82 and has_referee:
                tier = "Elite"
            elif prob_pct >= 82:
                # High probability but no referee data → cap at Strong
                tier = "Strong"
            elif prob_pct >= 70:
                tier = "Strong"
            else:
                tier = "Moderate"

            # -- Aggression score (display) ---------------------------------
            home_aggression = home_w_fouls + home_w_team * 2.0
            away_aggression = away_w_fouls + away_w_team * 2.0
            combined_aggression = round(home_aggression + away_aggression, 1)

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": ko_unix,
                "referee_name": referee_display or "Not Available",
                "referee_avg_cards": round(ref_avg_cards, 1) if ref_avg_cards else None,
                "referee_matches": ref_matches,
                "combined_aggression": combined_aggression,
                "projected_cards": round(lam, 2),
                "probability": prob_pct,
                "confidence_tier": tier,
                "data_quality": data_quality,
                "home_avg_match_cards": round(home_w_match, 1),
                "away_avg_match_cards": round(away_w_match, 1),
                "home_fouls_avg": round(home_w_fouls, 1),
                "away_fouls_avg": round(away_w_fouls, 1),
                "home_data_games": len(home_match_cards),
                "away_data_games": len(away_match_cards),
                "derby": derby_factor > 1.0,
                "model_type": model_used,
            })

        results.sort(key=lambda r: r["probability"], reverse=True)

        with _lock:
            _state["over35c_results"] = results
            _state["over35c_status"] = "done"

    except Exception as e:
        with _lock:
            _state["over35c_status"] = "done"
            _state["over35c_error"] = str(e)


@app.route("/api/over35cards", methods=["POST"])
def trigger_over35cards():
    """Trigger Over 3.5 Cards analysis."""
    with _lock:
        if _state["over35c_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_over35cards, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/over35cards-status")
def over35cards_status():
    """Poll Over 3.5 Cards analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["over35c_status"],
            "results": _state["over35c_results"],
            "error": _state["over35c_error"],
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
