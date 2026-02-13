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
# Over 3.5 Cards – Aggression Score Model (1-100)
# ---------------------------------------------------------------------------

# Common words stripped when detecting derbies
_COMMON_FOOTBALL_WORDS = frozenset({
    "fc", "cf", "sc", "ac", "as", "us", "ss", "city", "united",
    "athletic", "real", "sporting", "club", "de", "al", "the",
})


def _compute_league_avg_cards(season_id):
    """Return average total match cards for a league (used for H2H comparison)."""
    matches = fa.fetch_league_matches(season_id)
    card_counts = []
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
    if not card_counts:
        return 3.8
    return sum(card_counts) / len(card_counts)


def _extract_card_foul_data(last10):
    """Extract card and foul arrays from last-10 stats, split by home/away.

    Returns dict with keys:
      all_cards, all_fouls, home_cards, home_fouls, away_cards, away_fouls
    Each value list is most-recent-first.
    """
    all_cards, all_fouls = [], []
    home_cards, home_fouls = [], []
    away_cards, away_fouls = [], []
    for m in last10:
        y = m.get("yellows", -1)
        r = m.get("reds", -1)
        f = m.get("fouls", -1)
        if y < 0:
            continue
        cards = y + max(r, 0)
        fouls = f if f >= 0 else 0
        all_cards.append(cards)
        all_fouls.append(fouls)
        if m.get("is_home"):
            home_cards.append(cards)
            home_fouls.append(fouls)
        else:
            away_cards.append(cards)
            away_fouls.append(fouls)
    return {
        "all_cards": all_cards, "all_fouls": all_fouls,
        "home_cards": home_cards, "home_fouls": home_fouls,
        "away_cards": away_cards, "away_fouls": away_fouls,
    }


def _safe_avg(vals):
    """Average of a list, or 0 if empty."""
    return sum(vals) / len(vals) if vals else 0.0


def _compute_aggression_score(data, is_home_upcoming, position, total_teams,
                               is_derby, h2h_card_avg, league_avg_cards):
    """Compute a team's Aggression Score (1-100).

    Components:
      1. Card Rate (40 pts max) — avg cards/game from last 10 with home/away
         split weighted toward the upcoming venue.
      2. Foul Rate (25 pts max) — avg fouls/game, same venue-weighting.
      3. League Position (15 pts max) — relegation / title fight boost.
      4. H2H Factor (10 pts max) — how physical H2H matches are vs league avg.
      5. Derby Boost (10 pts max) — significant boost for local rivalries.
    """
    # --- 1. Card Rate (0-40) -----------------------------------------------
    # Venue-weighted average: 65% venue-matching form, 35% other venue
    if is_home_upcoming:
        venue_cards = data["home_cards"]
        other_cards = data["away_cards"]
    else:
        venue_cards = data["away_cards"]
        other_cards = data["home_cards"]

    if venue_cards and other_cards:
        avg_cards = _safe_avg(venue_cards) * 0.65 + _safe_avg(other_cards) * 0.35
    elif venue_cards:
        avg_cards = _safe_avg(venue_cards)
    elif other_cards:
        avg_cards = _safe_avg(other_cards)
    else:
        avg_cards = _safe_avg(data["all_cards"])

    # Scale: 0 cards/game → 0 pts, 4+ cards/game → 40 pts
    card_score = min(avg_cards / 4.0, 1.0) * 40.0

    # --- 2. Foul Rate (0-25) -----------------------------------------------
    if is_home_upcoming:
        venue_fouls = data["home_fouls"]
        other_fouls = data["away_fouls"]
    else:
        venue_fouls = data["away_fouls"]
        other_fouls = data["home_fouls"]

    if venue_fouls and other_fouls:
        avg_fouls = _safe_avg(venue_fouls) * 0.65 + _safe_avg(other_fouls) * 0.35
    elif venue_fouls:
        avg_fouls = _safe_avg(venue_fouls)
    elif other_fouls:
        avg_fouls = _safe_avg(other_fouls)
    else:
        avg_fouls = _safe_avg(data["all_fouls"])

    # Scale: 0 fouls → 0 pts, 16+ fouls → 25 pts
    foul_score = min(avg_fouls / 16.0, 1.0) * 25.0

    # --- 3. League Position (0-15) ------------------------------------------
    position_score = 0.0
    if position and total_teams:
        relegation_zone = total_teams - 2  # bottom 3
        near_relegation = total_teams - 4  # bottom 5
        if position >= relegation_zone or position <= 3:
            position_score = 15.0
        elif position >= near_relegation or position <= 5:
            position_score = 10.0
        else:
            position_score = 5.0

    # --- 4. H2H Factor (0-10) ----------------------------------------------
    h2h_score = 0.0
    if h2h_card_avg is not None and league_avg_cards > 0:
        ratio = h2h_card_avg / league_avg_cards
        if ratio >= 1.5:
            h2h_score = 10.0
        elif ratio >= 1.2:
            h2h_score = 7.0
        elif ratio >= 1.0:
            h2h_score = 4.0
        else:
            h2h_score = max(0.0, ratio * 3.0)

    # --- 5. Derby Boost (0-10) ----------------------------------------------
    derby_score = 10.0 if is_derby else 0.0

    total = card_score + foul_score + position_score + h2h_score + derby_score
    return {
        "total": round(min(max(total, 1.0), 100.0), 1),
        "card_score": round(card_score, 1),
        "foul_score": round(foul_score, 1),
        "position_score": round(position_score, 1),
        "h2h_score": round(h2h_score, 1),
        "derby_score": round(derby_score, 1),
        "avg_cards_pg": round(avg_cards, 2),
        "avg_fouls_pg": round(avg_fouls, 2),
    }


def _run_over35cards():
    """Analyse all fixtures for Over 3.5 Total Cards using Aggression Score model.

    Per-team Aggression Score (1-100) computed from:
      - Cards/fouls from last 10 matches (home/away venue split)
      - League position context (relegation / title fight boost)
      - Head-to-head factor (historical H2H cards vs league average)
      - Derby boost (significant uplift for local derbies)

    Skip logic:
      - Requires last-10 data for BOTH teams (min 3 matches with card data).
      - Requires min 2 H2H matches in the same league season.

    Output: Top 5 selections ranked by combined aggression score.
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

        # Caches (lazily populated per league)
        league_avg_cache: dict[int, float] = {}
        position_cache: dict[int, dict[int, int]] = {}
        team_count_cache: dict[int, int] = {}

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

            if not season_id:
                continue

            # --- Team last-10 stats ----------------------------------------
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            # SKIP: require last-10 data for BOTH teams (min 3 with card data)
            home_data = _extract_card_foul_data(home_last10)
            away_data = _extract_card_foul_data(away_last10)
            if len(home_data["all_cards"]) < 3 or len(away_data["all_cards"]) < 3:
                continue

            # --- H2H data (min 2 matches required) -------------------------
            h2h_matches = fa.get_h2h_matches(home_id, away_id, season_id)
            if len(h2h_matches) < 2:
                continue

            # Compute H2H average cards per match
            h2h_card_counts = []
            for m in h2h_matches:
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
                h2h_card_counts.append(hy + ay + max(hr, 0) + max(ar, 0))

            h2h_card_avg = (_safe_avg(h2h_card_counts)
                            if h2h_card_counts else None)

            # --- League average cards (cached) ------------------------------
            if season_id not in league_avg_cache:
                league_avg_cache[season_id] = _compute_league_avg_cards(season_id)
            lg_avg_cards = league_avg_cache[season_id]

            # --- League positions (cached) ----------------------------------
            if season_id not in position_cache:
                position_cache[season_id] = fa.build_position_map(season_id)
            if season_id not in team_count_cache:
                team_count_cache[season_id] = fa.get_league_team_count(season_id)
            pos_map = position_cache[season_id]
            total_teams = team_count_cache[season_id]

            home_pos = pos_map.get(home_id, 0)
            away_pos = pos_map.get(away_id, 0)

            # --- Derby detection -------------------------------------------
            home_words = set(home_name.lower().split()) - _COMMON_FOOTBALL_WORDS
            away_words = set(away_name.lower().split()) - _COMMON_FOOTBALL_WORDS
            is_derby = bool(home_words & away_words)

            # --- Compute per-team Aggression Scores -------------------------
            home_agg = _compute_aggression_score(
                home_data, is_home_upcoming=True,
                position=home_pos, total_teams=total_teams,
                is_derby=is_derby,
                h2h_card_avg=h2h_card_avg,
                league_avg_cards=lg_avg_cards,
            )
            away_agg = _compute_aggression_score(
                away_data, is_home_upcoming=False,
                position=away_pos, total_teams=total_teams,
                is_derby=is_derby,
                h2h_card_avg=h2h_card_avg,
                league_avg_cards=lg_avg_cards,
            )

            combined_score = round((home_agg["total"] + away_agg["total"]) / 2, 1)

            # --- Confidence tier -------------------------------------------
            if combined_score >= 70:
                tier = "Elite"
            elif combined_score >= 55:
                tier = "Strong"
            else:
                tier = "Moderate"

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": ko_unix,
                "combined_score": combined_score,
                "home_aggression": home_agg["total"],
                "away_aggression": away_agg["total"],
                "home_card_score": home_agg["card_score"],
                "away_card_score": away_agg["card_score"],
                "home_foul_score": home_agg["foul_score"],
                "away_foul_score": away_agg["foul_score"],
                "home_position_score": home_agg["position_score"],
                "away_position_score": away_agg["position_score"],
                "h2h_score": home_agg["h2h_score"],
                "derby_score": home_agg["derby_score"],
                "home_avg_cards_pg": home_agg["avg_cards_pg"],
                "away_avg_cards_pg": away_agg["avg_cards_pg"],
                "home_avg_fouls_pg": home_agg["avg_fouls_pg"],
                "away_avg_fouls_pg": away_agg["avg_fouls_pg"],
                "home_data_games": len(home_data["all_cards"]),
                "away_data_games": len(away_data["all_cards"]),
                "h2h_matches": len(h2h_card_counts),
                "h2h_avg_cards": round(h2h_card_avg, 1) if h2h_card_avg is not None else None,
                "league_avg_cards": round(lg_avg_cards, 1),
                "home_position": home_pos,
                "away_position": away_pos,
                "derby": is_derby,
                "confidence_tier": tier,
            })

        # Sort by combined aggression score descending, take top 5
        results.sort(key=lambda r: r["combined_score"], reverse=True)
        top5 = results[:5]

        with _lock:
            _state["over35c_results"] = top5
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
