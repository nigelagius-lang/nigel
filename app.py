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
    "super league greece": "Greece",
    "greek super league": "Greece",
    # Austria
    "austrian bundesliga": "Austria",
    # Switzerland
    "swiss super league": "Switzerland",
    "super league switzerland": "Switzerland",
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
    "brasileirao": "Brazil",
    "brasileirao serie a": "Brazil",
    "brasileirao serie b": "Brazil",
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
    # International / Continental
    "champions league": "Europe",
    "europa league": "Europe",
    "conference league": "Europe",
    "euro": "Europe",
    "world cup": "International",
    "copa america": "International",
    "nations league": "Europe",
    "africa cup": "Africa",
    "asian cup": "Asia",
    "copa libertadores": "South America",
    "copa sudamericana": "South America",
    "afc champions league": "Asia",
    "caf champions league": "Africa",
}


def _guess_country(fixture: dict) -> str:
    """Determine country from fixture data, using API field or league name."""
    # 1. Try API-provided country field (several possible names)
    for field in ("country", "country_name"):
        country = (fixture.get(field) or "").strip()
        if country:
            # Normalise to title case (API may return "england" lowercase)
            return country.title()

    # 2. Try nested competition object
    comp = fixture.get("competition")
    if isinstance(comp, dict):
        country = (comp.get("country") or comp.get("country_name") or "").strip()
        if country:
            return country.title()

    # 3. Derive from league/competition name
    league = (
        fixture.get("league_name")
        or fixture.get("competition_name")
        or ""
    ).strip()
    league_lower = league.lower()

    # 3a. Check for "Country - League" format first (many APIs use this)
    if " - " in league:
        return league.split(" - ", 1)[0].strip().title()

    # 3b. Exact match against our map
    if league_lower in LEAGUE_COUNTRY_MAP:
        return LEAGUE_COUNTRY_MAP[league_lower]

    # 3c. Check if a map key is a full-word match within the league name
    #     e.g. "English Premier League" contains "premier league"
    #     Only match if the pattern forms complete words (not partial)
    best_match = ""
    best_country = ""
    for pattern, mapped_country in LEAGUE_COUNTRY_MAP.items():
        if pattern in league_lower:
            # Prefer longer (more specific) pattern matches
            if len(pattern) > len(best_match):
                best_match = pattern
                best_country = mapped_country
    if best_country:
        return best_country

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
    "shot_overs_status": "idle",
    "shot_overs_results": [],
    "shot_overs_error": None,
    "shot_overs_ts": 0,           # cache timestamp
    "corner_overs_status": "idle",
    "corner_overs_results": [],
    "corner_overs_error": None,
    "corner_overs_ts": 0,         # cache timestamp
    "card_risk_status": "idle",
    "card_risk_results": [],
    "card_risk_error": None,
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

    # Sort by country, then league, then kick-off time
    cards.sort(key=lambda c: (
        c.get("country") or "Other",
        c.get("league") or "Other",
        c.get("kick_off_unix", 0),
    ))

    # Group by country+league so same-named leagues in different countries
    # stay separate (e.g. Italy Serie A vs Brazil Serie A)
    from collections import OrderedDict
    sections: OrderedDict[str, list] = OrderedDict()
    section_country: dict[str, str] = {}
    section_league: dict[str, str] = {}
    country_counts: dict[str, int] = {}

    for c in cards:
        country = c.get("country") or "Other"
        league = c.get("league") or "Other"
        key = f"{country}::{league}"
        sections.setdefault(key, []).append(c)
        section_country[key] = country
        section_league[key] = league
        country_counts[country] = country_counts.get(country, 0) + 1

    TOP_COUNTRIES = ["England", "France", "Germany", "Spain", "Italy"]
    top_countries = [(ct, country_counts[ct]) for ct in TOP_COUNTRIES if ct in country_counts]
    other_countries = sorted(
        [(ct, n) for ct, n in country_counts.items() if ct not in TOP_COUNTRIES]
    )

    return render_template(
        "dashboard.html",
        sections=sections,
        section_country=section_country,
        section_league=section_league,
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


# ---------------------------------------------------------------------------
# Shared probability helpers (Poisson / Negative Binomial)
# ---------------------------------------------------------------------------

def _poisson_cdf(lam, k):
    """P(X <= k) for Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    cdf = 0.0
    for i in range(int(k) + 1):
        cdf += math.exp(-lam) * (lam ** i) / math.factorial(i)
    return min(cdf, 1.0)


def _neg_binom_cdf(mean, var, k):
    """P(X <= k) for NegBin parameterised by mean/variance.
    Falls back to Poisson when variance <= mean.
    """
    if var <= mean or mean <= 0:
        return _poisson_cdf(mean, k)
    p = mean / var
    r = mean * p / (1 - p)
    cdf = 0.0
    for i in range(int(k) + 1):
        log_pmf = (math.lgamma(i + r) - math.lgamma(i + 1) - math.lgamma(r)
                   + i * math.log(1 - p) + r * math.log(p))
        cdf += math.exp(log_pmf)
    return min(cdf, 1.0)


def _prob_over(mean, var, line):
    """P(X > line).  Uses NegBin if overdispersed, else Poisson."""
    k = int(line)  # e.g. line 24.5 → k=24
    if var > mean * 1.3:
        return 1.0 - _neg_binom_cdf(mean, var, k)
    return 1.0 - _poisson_cdf(mean, k)


# ---------------------------------------------------------------------------
# Top 5 Shot Over Opportunities
# ---------------------------------------------------------------------------

_BLOCKED_SHOT_LEAGUES = {
    "friendly", "qualification", "u19", "u20", "u21", "u23",
    "women", "reserve", "youth", "amateurs",
}


def _is_allowed_shot_league(league_name: str) -> bool:
    """Check if fixture is in a valid competition for shot analysis.

    All professional leagues are allowed; only friendlies, youth, and
    qualification rounds are excluded.
    """
    name_lower = league_name.lower()
    for blocked in _BLOCKED_SHOT_LEAGUES:
        if blocked in name_lower:
            return False
    return True


def _compute_league_shot_stats(season_id):
    """Compute league-level shot statistics for normalization.

    Returns dict with: median_total, mean_total, std_total,
    median_home, median_away, match_count.
    """
    matches = fa.fetch_league_matches(season_id)
    total_shots_list = []
    home_shots_list = []
    away_shots_list = []
    for m in matches:
        if m.get("status") != "complete":
            continue
        hs = fa.safe_int(m.get("team_a_shots", m.get("home_shots", m.get("homeShots", -1))))
        aws = fa.safe_int(m.get("team_b_shots", m.get("away_shots", m.get("awayShots", -1))))
        if hs < 0 or aws < 0:
            continue
        total_shots_list.append(hs + aws)
        home_shots_list.append(hs)
        away_shots_list.append(aws)
    if not total_shots_list:
        return {"median_total": 24.0, "mean_total": 24.0, "std_total": 6.0,
                "median_home": 13.0, "median_away": 11.0, "match_count": 0}
    total_shots_list.sort()
    home_shots_list.sort()
    away_shots_list.sort()
    n = len(total_shots_list)
    median_t = total_shots_list[n // 2]
    mean_t = sum(total_shots_list) / n
    var_t = sum((x - mean_t) ** 2 for x in total_shots_list) / max(n - 1, 1)
    std_t = var_t ** 0.5
    nh = len(home_shots_list)
    na = len(away_shots_list)
    return {
        "median_total": median_t,
        "mean_total": round(mean_t, 2),
        "std_total": round(std_t, 2),
        "median_home": home_shots_list[nh // 2],
        "median_away": away_shots_list[na // 2],
        "match_count": n,
    }


def _extract_shot_profile(last10, is_home_upcoming):
    """Extract shot statistics from last-10 data.

    Returns dict with averages (for/against, SOT, overall) plus
    home/away split and recent-5 trend.
    """
    if not last10:
        return None

    shots_for_all, shots_ag_all = [], []
    sot_for_all, sot_ag_all = [], []
    home_shots, away_shots = [], []
    home_shots_ag, away_shots_ag = [], []

    for m in last10:
        sf = m.get("shots", -1)
        sa = m.get("shots_against", -1)
        sot = m.get("sot", -1)
        sota = m.get("sot_against", -1)
        if sf >= 0:
            shots_for_all.append(sf)
            if m.get("is_home"):
                home_shots.append(sf)
            else:
                away_shots.append(sf)
        if sa >= 0:
            shots_ag_all.append(sa)
            if m.get("is_home"):
                home_shots_ag.append(sa)
            else:
                away_shots_ag.append(sa)
        if sot >= 0:
            sot_for_all.append(sot)
        if sota >= 0:
            sot_ag_all.append(sota)

    if not shots_for_all:
        return None

    avg_shots = _safe_avg(shots_for_all)
    avg_shots_ag = _safe_avg(shots_ag_all) if shots_ag_all else avg_shots
    avg_sot = _safe_avg(sot_for_all) if sot_for_all else 0.0
    avg_sot_ag = _safe_avg(sot_ag_all) if sot_ag_all else 0.0

    # Home/away split averages
    if is_home_upcoming:
        venue_shots = _safe_avg(home_shots) if home_shots else avg_shots
        venue_shots_ag = _safe_avg(home_shots_ag) if home_shots_ag else avg_shots_ag
    else:
        venue_shots = _safe_avg(away_shots) if away_shots else avg_shots
        venue_shots_ag = _safe_avg(away_shots_ag) if away_shots_ag else avg_shots_ag

    # Recent 5-match trend
    recent5 = shots_for_all[:5] if len(shots_for_all) >= 5 else shots_for_all
    trend_avg = _safe_avg(recent5)

    # Shot conversion rate
    conversion = (avg_sot / avg_shots * 100) if avg_shots > 0 else 0.0

    return {
        "avg_shots": round(avg_shots, 2),
        "avg_shots_against": round(avg_shots_ag, 2),
        "avg_sot": round(avg_sot, 2),
        "avg_sot_against": round(avg_sot_ag, 2),
        "venue_shots": round(venue_shots, 2),
        "venue_shots_against": round(venue_shots_ag, 2),
        "recent5_avg": round(trend_avg, 2),
        "conversion_pct": round(conversion, 1),
        "match_count": len(shots_for_all),
    }


# ---------------------------------------------------------------------------
# Card Risk Engine – Top 5 Most Likely Players to Be Carded (24h)
# ---------------------------------------------------------------------------

def _minmax_norm(val: float, lo: float, hi: float) -> float:
    """Normalize val to 0-1 via min-max.  Clamps to [0, 1]."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (val - lo) / (hi - lo)))


def _get_player_card_stats(detail: dict, appearances: int) -> dict:
    """Extract card-relevant per-90 stats from a player-stats response.

    Returns dict with yc_per90, fouls_per90, tackles_per90, duels_per90,
    minutes, and rc_per90.  All fallback to 0 when unavailable.
    """
    mins = fa._extract_detail_stat(detail,
        "minutes_played_overall", "minutes_overall",
        "minutes_played", "mins_played")
    if mins <= 0 and appearances > 0:
        mins = appearances * 70  # fallback estimate

    nineties = mins / 90.0 if mins > 0 else max(appearances, 1)

    # Yellow cards
    yc_total = fa._extract_detail_stat(detail,
        "yellow_cards_overall", "yellow_cards", "total_yellow_cards",
        "yellowCards_overall")
    yc_per90 = fa._extract_detail_stat(detail,
        "yellow_cards_per_90", "yellow_cards_per_90_overall",
        "yellow_cards_per_game")
    if yc_per90 == 0 and yc_total > 0:
        yc_per90 = yc_total / nineties

    # Red cards
    rc_total = fa._extract_detail_stat(detail,
        "red_cards_overall", "red_cards", "total_red_cards",
        "redCards_overall")
    rc_per90 = rc_total / nineties if rc_total > 0 else 0.0

    # Fouls committed
    fouls_total = fa._extract_detail_stat(detail,
        "fouls_committed_overall", "fouls_committed",
        "total_fouls_committed", "fouls_overall")
    fouls_per90 = fa._extract_detail_stat(detail,
        "fouls_committed_per_game", "fouls_committed_per_90_overall",
        "fouls_per_game", "fouls_per_90",
        "avg_fouls_committed_per_game")
    if fouls_per90 == 0 and fouls_total > 0:
        fouls_per90 = fouls_total / nineties

    # Tackles (may not be available – fallback to position heuristic later)
    tackles_per90 = fa._extract_detail_stat(detail,
        "tackles_per_game", "tackles_per_90", "tackles_per_90_overall",
        "avg_tackles_per_game", "tackles_successful_per_game")
    tackles_total = fa._extract_detail_stat(detail,
        "tackles_overall", "total_tackles", "tackles_successful_overall",
        "tackles")
    if tackles_per90 == 0 and tackles_total > 0:
        tackles_per90 = tackles_total / nineties

    # Duels (may not be available)
    duels_per90 = fa._extract_detail_stat(detail,
        "duels_per_game", "duels_per_90", "duels_won_per_game",
        "total_duels_per_game")
    duels_total = fa._extract_detail_stat(detail,
        "duels_overall", "total_duels", "duels_won_overall", "duels_total")
    if duels_per90 == 0 and duels_total > 0:
        duels_per90 = duels_total / nineties

    return {
        "yc_per90": yc_per90,
        "rc_per90": rc_per90,
        "fouls_per90": fouls_per90,
        "tackles_per90": tackles_per90,
        "duels_per90": duels_per90,
        "minutes": mins,
        "yc_total": yc_total,
    }


def _position_tackle_estimate(position: str) -> float:
    """Fallback tackle/90 estimate by position when API data is missing."""
    pos = position.lower()
    if any(p in pos for p in ("defend", "back", "cb", "lb", "rb", "wing-back")):
        return 2.5
    if any(p in pos for p in ("midfield", "mid", "dm", "cm")):
        return 2.0
    if any(p in pos for p in ("forward", "striker", "wing", "att")):
        return 1.0
    return 1.5


def _position_duel_estimate(position: str) -> float:
    """Fallback duels/90 estimate by position when API data is missing."""
    pos = position.lower()
    if any(p in pos for p in ("defend", "back", "cb", "lb", "rb", "wing-back")):
        return 6.0
    if any(p in pos for p in ("midfield", "mid", "dm", "cm")):
        return 5.5
    if any(p in pos for p in ("forward", "striker", "wing", "att")):
        return 4.0
    return 4.5


def _run_card_risk():
    """Analyse all fixtures (next 24h) for player card risk.

    Card Risk Score (CRS) per player:
      CRS = 0.40×BasePlayerRisk + 0.20×RefereeScore + 0.15×MatchContextScore
          + 0.15×OpponentRisk + 0.10×TeamAggressionScore

    Card probability via logistic: 1 / (1 + e^(-8*(CRS-0.5)))

    Output: Top 5 players above 35% probability, sorted descending.
    """
    with _lock:
        if _state["card_risk_status"] == "running":
            return
        _state["card_risk_status"] = "running"
        _state["card_risk_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        import math

        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff_ts = now_ts + 24 * 3600  # next 24 hours only

        all_players: list[dict] = []

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

            # --- Season ID ---
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

            # --- Team last-10 (for TeamAggression & MatchContext) ---
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)
            home_data = _extract_card_foul_data(home_last10)
            away_data = _extract_card_foul_data(away_last10)

            # Team aggression scores (normalized 0-1)
            h_cards_pg = _safe_avg(home_data["all_cards"]) if home_data["all_cards"] else 0
            h_fouls_pg = _safe_avg(home_data["all_fouls"]) if home_data["all_fouls"] else 0
            a_cards_pg = _safe_avg(away_data["all_cards"]) if away_data["all_cards"] else 0
            a_fouls_pg = _safe_avg(away_data["all_fouls"]) if away_data["all_fouls"] else 0

            # Normalize team aggression: cards 0-5 range, fouls 0-20 range
            home_team_aggr = (
                0.50 * _minmax_norm(h_cards_pg, 0, 5) +
                0.50 * _minmax_norm(h_fouls_pg, 0, 20)
            )
            away_team_aggr = (
                0.50 * _minmax_norm(a_cards_pg, 0, 5) +
                0.50 * _minmax_norm(a_fouls_pg, 0, 20)
            )

            # --- Opponent risk factors (fouls drawn / shots as dribble proxy) ---
            home_fouls_drawn = []
            away_fouls_drawn = []
            home_shots_list = []
            away_shots_list = []
            for m in home_last10:
                fa_val = m.get("fouls_against", -1)
                if fa_val >= 0:
                    home_fouls_drawn.append(fa_val)
                sh = m.get("shots", -1)
                if sh >= 0:
                    home_shots_list.append(sh)
            for m in away_last10:
                fa_val = m.get("fouls_against", -1)
                if fa_val >= 0:
                    away_fouls_drawn.append(fa_val)
                sh = m.get("shots", -1)
                if sh >= 0:
                    away_shots_list.append(sh)

            home_fouls_drawn_avg = _safe_avg(home_fouls_drawn)
            away_fouls_drawn_avg = _safe_avg(away_fouls_drawn)
            home_shots_avg = _safe_avg(home_shots_list)
            away_shots_avg = _safe_avg(away_shots_list)

            # OpponentRisk for home players = how tricky the away team is
            opp_risk_for_home = (
                0.40 * _minmax_norm(away_shots_avg, 5, 18) +  # dribbles proxy
                0.30 * _minmax_norm(away_fouls_drawn_avg, 5, 18) +
                0.30 * 0.5  # position mismatch fallback
            )
            opp_risk_for_away = (
                0.40 * _minmax_norm(home_shots_avg, 5, 18) +
                0.30 * _minmax_norm(home_fouls_drawn_avg, 5, 18) +
                0.30 * 0.5
            )

            # --- Match context ---
            # Derby detection (word overlap)
            home_words = set(home_name.lower().split())
            away_words = set(away_name.lower().split())
            common = home_words & away_words - {"fc", "sc", "cf", "ac", "afc", "united", "city", "town", "club"}
            is_derby = len(common) >= 1 or (
                home_name.split()[0].lower() == away_name.split()[0].lower()
                and len(home_name.split()[0]) > 3
            )
            derby_flag = 1.0 if is_derby else 0.0

            # H2H avg cards
            h2h_matches = fa.get_h2h_matches(home_id, away_id, season_id)
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
            h2h_avg = _safe_avg(h2h_card_counts) if h2h_card_counts else 0

            # Match importance: position context
            try:
                pos_map = fa.build_position_map(season_id)
                total_teams = fa.get_league_team_count(season_id)
            except Exception:
                pos_map = {}
                total_teams = 0
            home_pos = pos_map.get(home_id, 0)
            away_pos = pos_map.get(away_id, 0)
            importance = 0.0
            for p in (home_pos, away_pos):
                if p and total_teams:
                    if p <= 3 or p >= total_teams - 2:
                        importance = max(importance, 1.0)
                    elif p <= 5 or p >= total_teams - 4:
                        importance = max(importance, 0.65)
                    else:
                        importance = max(importance, 0.3)

            match_context = (
                0.40 * importance +
                0.30 * derby_flag +
                0.30 * _minmax_norm(h2h_avg, 2, 8)
            )

            # --- Referee score ---
            ref_score = 0.5  # default when referee data unavailable
            try:
                ref_id = fix.get("refereeID", fix.get("referee_id", 0))
                if ref_id:
                    ref_data = fa.fetch_referee(int(ref_id))
                    if ref_data:
                        ref_yc = fa.safe_float(ref_data.get(
                            "yellow_cards_per_game",
                            ref_data.get("avg_yellow_cards_per_game",
                            ref_data.get("yellowCards_per_game", 0))))
                        ref_rc = fa.safe_float(ref_data.get(
                            "red_cards_per_game",
                            ref_data.get("avg_red_cards_per_game", 0)))
                        ref_fouls = fa.safe_float(ref_data.get(
                            "fouls_per_game",
                            ref_data.get("avg_fouls_per_game", 0)))
                        ref_score = (
                            0.60 * _minmax_norm(ref_yc, 2, 7) +
                            0.25 * _minmax_norm(ref_rc, 0, 0.5) +
                            0.15 * _minmax_norm(ref_fouls, 18, 35)
                        )
                else:
                    # Try league referees list to find match referee
                    league_refs = fa.fetch_league_referees(season_id)
                    for lr in league_refs:
                        lr_id = lr.get("id", 0)
                        if lr_id:
                            yc_pg = fa.safe_float(lr.get(
                                "yellow_cards_per_game",
                                lr.get("avg_yellow_cards_per_game", 0)))
                            if yc_pg > 0:
                                ref_score = _minmax_norm(yc_pg, 2, 7)
                                break
            except Exception:
                pass

            # --- Get players for both teams ---
            # Fetch league roster once (cached), filter per team
            try:
                all_league_players = fa.fetch_league_players(season_id)
            except Exception:
                all_league_players = []

            for team_side in ("home", "away"):
                if team_side == "home":
                    team_id = home_id
                    team_name = home_name
                    opp_name = away_name
                    team_aggr = home_team_aggr
                    opp_risk = opp_risk_for_home
                else:
                    team_id = away_id
                    team_name = away_name
                    opp_name = home_name
                    team_aggr = away_team_aggr
                    opp_risk = opp_risk_for_away

                # Filter roster to this team
                roster = []
                for rp in all_league_players:
                    cid = rp.get("club_team_id")
                    if cid is None:
                        continue
                    try:
                        if int(cid) != team_id:
                            continue
                    except (ValueError, TypeError):
                        continue
                    apps = fa.safe_int(rp.get("appearances_overall", 0), 0)
                    if apps < 3:
                        continue
                    roster.append(rp)

                # Sort by appearances, take likely starters
                roster.sort(
                    key=lambda x: fa.safe_int(x.get("appearances_overall", 0), 0),
                    reverse=True)
                starters = roster[:14]

                for idx, rp in enumerate(starters):
                    pid = rp.get("id", 0)
                    name = rp.get("known_as") or rp.get("full_name") or "Unknown"
                    position = rp.get("position", "")
                    appearances = fa.safe_int(rp.get("appearances_overall", 0), 0)

                    # Fetch detailed stats (card-specific) for top players
                    detail = {}
                    if pid and idx < 15:
                        try:
                            detail = fa.fetch_player_detail(pid, season_id)
                        except Exception:
                            pass

                    stats = _get_player_card_stats(detail, appearances)

                    # Fallback tackles/duels by position
                    tackles = stats["tackles_per90"]
                    if tackles == 0:
                        tackles = _position_tackle_estimate(position)
                    duels = stats["duels_per90"]
                    if duels == 0:
                        duels = _position_duel_estimate(position)

                    # --- BasePlayerRisk (0-1) ---
                    base_risk = (
                        0.35 * _minmax_norm(stats["yc_per90"], 0, 0.8) +
                        0.30 * _minmax_norm(stats["fouls_per90"], 0, 3.0) +
                        0.20 * _minmax_norm(tackles, 0, 5.0) +
                        0.15 * _minmax_norm(duels, 0, 10.0)
                    )

                    # --- Full CRS ---
                    crs = (
                        0.40 * base_risk +
                        0.20 * ref_score +
                        0.15 * match_context +
                        0.15 * opp_risk +
                        0.10 * team_aggr
                    )

                    # Logistic transformation → probability %
                    prob = 1.0 / (1.0 + math.exp(-8.0 * (crs - 0.5)))
                    prob_pct = round(prob * 100, 1)

                    if prob_pct < 35:
                        continue

                    # Risk tag
                    if prob_pct >= 65:
                        risk_tag = "Very High"
                    elif prob_pct >= 50:
                        risk_tag = "High"
                    else:
                        risk_tag = "Moderate"

                    all_players.append({
                        "player_name": name,
                        "position": position,
                        "team_name": team_name,
                        "opponent_name": opp_name,
                        "league": league,
                        "kick_off": ko_str,
                        "kick_off_unix": ko_unix,
                        "card_prob": prob_pct,
                        "risk_tag": risk_tag,
                        "crs": round(crs, 3),
                        "base_risk": round(base_risk, 3),
                        "ref_score": round(ref_score, 3),
                        "match_context": round(match_context, 3),
                        "opp_risk": round(opp_risk, 3),
                        "team_aggr": round(team_aggr, 3),
                        "yc_per90": round(stats["yc_per90"], 2),
                        "fouls_per90": round(stats["fouls_per90"], 2),
                        "yc_total": int(stats["yc_total"]),
                    })

        # Sort by probability descending, take top 5
        all_players.sort(key=lambda x: x["card_prob"], reverse=True)
        top5 = all_players[:5]

        with _lock:
            _state["card_risk_status"] = "done"
            _state["card_risk_results"] = top5
            _state["card_risk_error"] = None

    except Exception as exc:
        import traceback
        traceback.print_exc()
        with _lock:
            _state["card_risk_status"] = "done"
            _state["card_risk_results"] = []
            _state["card_risk_error"] = str(exc)


def _run_shot_overs():
    """Analyse fixtures across all professional leagues for Shot Over opportunities.

    Model:
      1. Compute expected shots per team (home/away split + opponent conceding).
      2. Apply adjustments: recent trend, league normalization, 1X2-implied dominance.
      3. Use Negative Binomial / Poisson to compute P(total > line).
      4. Compute EV from bookmaker odds if available.
      5. Rank by EV / probability edge, return top 5.
    """
    with _lock:
        if _state["shot_overs_status"] == "running":
            return
        # 5-minute cache
        now_ts_cache = int(datetime.now(timezone.utc).timestamp())
        if (_state["shot_overs_status"] == "done"
                and now_ts_cache - _state["shot_overs_ts"] < 300
                and _state["shot_overs_results"]):
            return
        _state["shot_overs_status"] = "running"
        _state["shot_overs_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff_ts = now_ts + 48 * 3600

        league_shot_cache: dict[int, dict] = {}
        results = []

        for fix in fixtures:
            ko_unix = int(fix.get("date_unix", 0) or 0)
            if ko_unix and (ko_unix < now_ts - 3600 or ko_unix > cutoff_ts):
                continue
            # Skip postponed
            status = (fix.get("status") or "").lower()
            if status in ("postponed", "cancelled", "suspended"):
                continue

            league = fix.get("league_name", fix.get("competition_name", "Unknown"))
            if not _is_allowed_shot_league(league):
                continue

            home_name = fix.get("home_name", "Home")
            away_name = fix.get("away_name", "Away")
            home_id = int(fix.get("homeID", fix.get("home_id", 0)))
            away_id = int(fix.get("awayID", fix.get("away_id", 0)))
            ko_str = (
                datetime.fromtimestamp(ko_unix, tz=timezone.utc).strftime("%H:%M UTC")
                if ko_unix else "TBD"
            )

            if not home_id or not away_id:
                continue

            # Season ID
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

            # League stats (cached)
            if season_id not in league_shot_cache:
                league_shot_cache[season_id] = _compute_league_shot_stats(season_id)
            lg = league_shot_cache[season_id]

            # Skip leagues with insufficient data
            if lg["match_count"] < 15:
                continue

            # Team data
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            home_prof = _extract_shot_profile(home_last10, is_home_upcoming=True)
            away_prof = _extract_shot_profile(away_last10, is_home_upcoming=False)

            # Skip if insufficient data
            if not home_prof or not away_prof:
                continue
            if home_prof["match_count"] < 3 or away_prof["match_count"] < 3:
                continue

            # --- Step 1: Baseline expected shots ---
            # Home team expected shots = (Home avg shots at home + Away avg shots conceded away) / 2
            home_expected = (home_prof["venue_shots"] + away_prof["venue_shots_against"]) / 2
            # Away team expected shots = (Away avg shots away + Home avg shots conceded at home) / 2
            away_expected = (away_prof["venue_shots"] + home_prof["venue_shots_against"]) / 2

            # --- Step 2: Adjustments ---
            # a) Recent 5-match trend (25% weight) — capped to prevent inflation
            home_trend_adj = 1.0
            if home_prof["avg_shots"] > 0:
                home_trend_adj = 0.75 + 0.25 * (home_prof["recent5_avg"] / home_prof["avg_shots"])
                home_trend_adj = max(0.90, min(1.10, home_trend_adj))
            away_trend_adj = 1.0
            if away_prof["avg_shots"] > 0:
                away_trend_adj = 0.75 + 0.25 * (away_prof["recent5_avg"] / away_prof["avg_shots"])
                away_trend_adj = max(0.90, min(1.10, away_trend_adj))

            # b) League normalization multiplier
            lg_median = lg["median_total"] if lg["median_total"] > 0 else 24.0
            home_shot_idx = home_prof["avg_shots"] / (lg["median_home"] if lg["median_home"] > 0 else 13)
            away_shot_idx = away_prof["avg_shots"] / (lg["median_away"] if lg["median_away"] > 0 else 11)

            # c) Shot pace / conversion factor
            home_conv_adj = 1.0 + (home_prof["conversion_pct"] - 33.0) / 200.0
            away_conv_adj = 1.0 + (away_prof["conversion_pct"] - 33.0) / 200.0
            home_conv_adj = max(0.95, min(1.05, home_conv_adj))
            away_conv_adj = max(0.95, min(1.05, away_conv_adj))

            # d) 1X2 odds-implied dominance
            odds_1 = fa.safe_float(fix.get("odds_ft_1", fix.get("odds_home", 0)))
            odds_x = fa.safe_float(fix.get("odds_ft_x", fix.get("odds_draw", 0)))
            odds_2 = fa.safe_float(fix.get("odds_ft_2", fix.get("odds_away", 0)))
            dominance_adj_h, dominance_adj_a = 1.0, 1.0
            if odds_1 > 0 and odds_2 > 0:
                imp_home = 1.0 / odds_1
                imp_away = 1.0 / odds_2
                total_imp = imp_home + imp_away + (1.0 / odds_x if odds_x > 0 else 0.25)
                fav_ratio = imp_home / total_imp
                dog_ratio = imp_away / total_imp
                # Favorites tend to have more shots; underdogs trailing generate shots late
                dominance_adj_h = 0.95 + fav_ratio * 0.15
                dominance_adj_a = 0.95 + dog_ratio * 0.15
                dominance_adj_h = max(0.95, min(1.08, dominance_adj_h))
                dominance_adj_a = max(0.95, min(1.08, dominance_adj_a))

            # Apply adjustments — cap total multiplier to ±15% of baseline
            home_multiplier = home_trend_adj * home_conv_adj * dominance_adj_h
            away_multiplier = away_trend_adj * away_conv_adj * dominance_adj_a
            home_multiplier = max(0.85, min(1.15, home_multiplier))
            away_multiplier = max(0.85, min(1.15, away_multiplier))
            adj_home = home_expected * home_multiplier
            adj_away = away_expected * away_multiplier

            # --- Step 3: Total expected shots ---
            total_expected = adj_home + adj_away

            # --- Determine shot line ---
            # Try bookmaker shot lines first, fall back to league median
            bk_line = None
            bk_over_odds = None
            bk_under_odds = None
            # Probe fixture for shots line fields
            for lf in ("total_shots_line", "shots_line", "shots_over_under_line",
                        "odds_shots_over_line"):
                val = fix.get(lf)
                if val is not None:
                    try:
                        bk_line = float(val)
                        break
                    except (ValueError, TypeError):
                        pass
            for of in ("odds_shots_over", "odds_total_shots_over", "shots_over_odds"):
                val = fix.get(of)
                if val is not None:
                    try:
                        bk_over_odds = float(val)
                        break
                    except (ValueError, TypeError):
                        pass
            for uf in ("odds_shots_under", "odds_total_shots_under", "shots_under_odds"):
                val = fix.get(uf)
                if val is not None:
                    try:
                        bk_under_odds = float(val)
                        break
                    except (ValueError, TypeError):
                        pass

            # Fallback: use league median as line
            shot_line = bk_line if bk_line and bk_line > 0 else round(lg_median - 0.5) + 0.5

            # --- Step 4: Probability computation (analytical NegBin/Poisson) ---
            # Compute variance from recent shot data
            all_match_shots = []
            for m in home_last10:
                ms = m.get("shots", -1)
                msa = m.get("shots_against", -1)
                if ms >= 0 and msa >= 0:
                    all_match_shots.append(ms + msa)
            for m in away_last10:
                ms = m.get("shots", -1)
                msa = m.get("shots_against", -1)
                if ms >= 0 and msa >= 0:
                    all_match_shots.append(ms + msa)
            if len(all_match_shots) >= 2:
                sample_mean = sum(all_match_shots) / len(all_match_shots)
                sample_var = sum((x - sample_mean) ** 2 for x in all_match_shots) / (len(all_match_shots) - 1)
            else:
                sample_var = total_expected  # Poisson assumption

            prob_over_main = _prob_over(total_expected, sample_var, shot_line)
            prob_over_plus1 = _prob_over(total_expected, sample_var, shot_line + 1)
            prob_over_minus1 = _prob_over(total_expected, sample_var, shot_line - 1)

            # --- Step 5: EV calculation ---
            implied_prob = 0.0
            ev_pct = 0.0
            if bk_over_odds and bk_over_odds > 1.0:
                implied_prob = round(1.0 / bk_over_odds * 100, 1)
                ev_pct = round((prob_over_main * bk_over_odds - 1) * 100, 2)
            elif prob_over_main >= 0.50:
                # No bookmaker odds; estimate fair odds from our probability
                fair_odds = 1.0 / prob_over_main if prob_over_main > 0 else 10.0
                implied_prob = round(prob_over_main * 100, 1)
                ev_pct = 0.0  # No edge calculable without bookmaker odds

            prob_pct = round(prob_over_main * 100, 1)

            # Filter: model probability >= 50%
            if prob_pct < 50:
                continue

            # Filter: EV >= 1% if bookmaker odds available, else just probability
            if bk_over_odds and bk_over_odds > 1.0 and ev_pct < 1.0:
                continue

            # Shot pace indicator
            total_shot_rate = total_expected / 90.0  # per minute
            if total_shot_rate >= 0.30:
                pace = "High"
            elif total_shot_rate >= 0.24:
                pace = "Medium"
            else:
                pace = "Low"

            # Style matchup indicator
            combined_idx = (home_shot_idx + away_shot_idx) / 2
            if combined_idx >= 1.15:
                style = "Aggressive"
            elif combined_idx >= 0.95:
                style = "Balanced"
            else:
                style = "Slow"

            # Probability edge vs implied
            prob_edge = prob_pct - implied_prob if implied_prob > 0 else 0.0
            shots_vs_line = round(total_expected - shot_line, 1)

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": ko_unix,
                "league_median_shots": lg["median_total"],
                "model_expected_total": round(total_expected, 1),
                "line": shot_line,
                "model_prob": prob_pct,
                "prob_over_plus1": round(prob_over_plus1 * 100, 1),
                "prob_over_minus1": round(prob_over_minus1 * 100, 1),
                "implied_prob": implied_prob,
                "ev_pct": ev_pct,
                "home_expected": round(adj_home, 1),
                "away_expected": round(adj_away, 1),
                "home_avg_shots": home_prof["avg_shots"],
                "away_avg_shots": away_prof["avg_shots"],
                "home_sot": home_prof["avg_sot"],
                "away_sot": away_prof["avg_sot"],
                "home_conversion": home_prof["conversion_pct"],
                "away_conversion": away_prof["conversion_pct"],
                "shot_pace": pace,
                "style_matchup": style,
                "shots_vs_line": shots_vs_line,
                "prob_edge": round(prob_edge, 1),
                "has_bk_odds": bk_over_odds is not None and bk_over_odds > 1.0,
                "bk_over_odds": round(bk_over_odds, 2) if bk_over_odds else None,
            })

        # Ranking: EV > probability edge > shots vs line
        results.sort(key=lambda r: (r["ev_pct"], r["prob_edge"], r["shots_vs_line"]),
                     reverse=True)
        top5 = results[:5]

        with _lock:
            _state["shot_overs_results"] = top5
            _state["shot_overs_status"] = "done"
            _state["shot_overs_ts"] = int(datetime.now(timezone.utc).timestamp())

    except Exception as e:
        with _lock:
            _state["shot_overs_status"] = "done"
            _state["shot_overs_error"] = str(e)


# ---------------------------------------------------------------------------
# Top 5 Corner Over Opportunities
# ---------------------------------------------------------------------------

def _compute_league_corner_stats(season_id):
    """Compute league-level corner statistics for normalization.

    Returns dict with: median_total, mean_total, std_total,
    median_home, median_away, match_count.
    """
    matches = fa.fetch_league_matches(season_id)
    total_corners_list = []
    home_corners_list = []
    away_corners_list = []
    for m in matches:
        if m.get("status") != "complete":
            continue
        hc = fa.safe_int(m.get("team_a_corners",
              m.get("home_corners", m.get("homeCorners", -1))))
        ac = fa.safe_int(m.get("team_b_corners",
              m.get("away_corners", m.get("awayCorners", -1))))
        if hc < 0 or ac < 0:
            continue
        total_corners_list.append(hc + ac)
        home_corners_list.append(hc)
        away_corners_list.append(ac)
    if not total_corners_list:
        return {"median_total": 10.0, "mean_total": 10.0, "std_total": 3.0,
                "median_home": 5.0, "median_away": 5.0, "match_count": 0}
    total_corners_list.sort()
    home_corners_list.sort()
    away_corners_list.sort()
    n = len(total_corners_list)
    median_t = total_corners_list[n // 2]
    mean_t = sum(total_corners_list) / n
    var_t = sum((x - mean_t) ** 2 for x in total_corners_list) / max(n - 1, 1)
    std_t = var_t ** 0.5
    nh = len(home_corners_list)
    na = len(away_corners_list)
    return {
        "median_total": median_t,
        "mean_total": round(mean_t, 2),
        "std_total": round(std_t, 2),
        "median_home": home_corners_list[nh // 2],
        "median_away": away_corners_list[na // 2],
        "match_count": n,
    }


def _extract_corner_profile(last10, is_home_upcoming):
    """Extract corner statistics from last-10 data with home/away split."""
    if not last10:
        return None

    corners_for_all, corners_ag_all = [], []
    home_corners, away_corners = [], []
    home_corners_ag, away_corners_ag = [], []
    shots_for_all, blocked_for_all = [], []

    for m in last10:
        cf = m.get("corners", -1)
        ca = m.get("corners_against", -1)
        sf = m.get("shots", -1)
        if cf >= 0:
            corners_for_all.append(cf)
            if m.get("is_home"):
                home_corners.append(cf)
            else:
                away_corners.append(cf)
        if ca >= 0:
            corners_ag_all.append(ca)
            if m.get("is_home"):
                home_corners_ag.append(ca)
            else:
                away_corners_ag.append(ca)
        if sf >= 0:
            shots_for_all.append(sf)
        # Blocked shots (shots_against − sot_against if both available)
        sa = m.get("shots_against", -1)
        sota = m.get("sot_against", -1)
        if sa >= 0 and sota >= 0 and sa >= sota:
            blocked_for_all.append(sa - sota)

    if not corners_for_all:
        return None

    avg_corners = _safe_avg(corners_for_all)
    avg_corners_ag = _safe_avg(corners_ag_all) if corners_ag_all else avg_corners

    if is_home_upcoming:
        venue_corners = _safe_avg(home_corners) if home_corners else avg_corners
        venue_corners_ag = _safe_avg(home_corners_ag) if home_corners_ag else avg_corners_ag
    else:
        venue_corners = _safe_avg(away_corners) if away_corners else avg_corners
        venue_corners_ag = _safe_avg(away_corners_ag) if away_corners_ag else avg_corners_ag

    # Recent 5-match trend
    recent5 = corners_for_all[:5] if len(corners_for_all) >= 5 else corners_for_all
    trend_avg = _safe_avg(recent5)

    avg_shots = _safe_avg(shots_for_all) if shots_for_all else 0.0
    avg_blocked = _safe_avg(blocked_for_all) if blocked_for_all else 0.0

    return {
        "avg_corners": round(avg_corners, 2),
        "avg_corners_against": round(avg_corners_ag, 2),
        "venue_corners": round(venue_corners, 2),
        "venue_corners_against": round(venue_corners_ag, 2),
        "recent5_avg": round(trend_avg, 2),
        "avg_shots": round(avg_shots, 2),
        "avg_blocked": round(avg_blocked, 2),
        "match_count": len(corners_for_all),
    }


def _run_corner_overs():
    """Analyse ALL league fixtures for Corner Over opportunities.

    Model:
      1. Expected corners per team (home/away split + opponent conceding).
      2. Adjustments: shot volume, blocked shots, recent trend, league norm, dominance.
      3. Match interaction multiplier (style logic).
      4. NegBin/Poisson probability of Over main line.
      5. EV from bookmaker odds if available.
      6. Rank by EV / probability edge, return top 5.
    """
    with _lock:
        if _state["corner_overs_status"] == "running":
            return
        now_ts_cache = int(datetime.now(timezone.utc).timestamp())
        if (_state["corner_overs_status"] == "done"
                and now_ts_cache - _state["corner_overs_ts"] < 300
                and _state["corner_overs_results"]):
            return
        _state["corner_overs_status"] = "running"
        _state["corner_overs_error"] = None
        fixtures = list(_state["fixtures"])

    try:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff_ts = now_ts + 48 * 3600

        league_corner_cache: dict[int, dict] = {}
        results = []

        for fix in fixtures:
            ko_unix = int(fix.get("date_unix", 0) or 0)
            if ko_unix and (ko_unix < now_ts - 3600 or ko_unix > cutoff_ts):
                continue
            status = (fix.get("status") or "").lower()
            if status in ("postponed", "cancelled", "suspended"):
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

            # Season ID
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

            # League stats (cached)
            if season_id not in league_corner_cache:
                league_corner_cache[season_id] = _compute_league_corner_stats(season_id)
            lg = league_corner_cache[season_id]

            # Skip leagues with insufficient data (< 50 matches)
            if lg["match_count"] < 50:
                continue

            # Team data
            home_last10 = fa.get_team_last10(home_id, season_id)
            away_last10 = fa.get_team_last10(away_id, season_id)

            home_prof = _extract_corner_profile(home_last10, is_home_upcoming=True)
            away_prof = _extract_corner_profile(away_last10, is_home_upcoming=False)

            if not home_prof or not away_prof:
                continue
            if home_prof["match_count"] < 5 or away_prof["match_count"] < 5:
                continue

            # --- Step 1: Baseline expected corners ---
            home_expected = (home_prof["venue_corners"] + away_prof["venue_corners_against"]) / 2
            away_expected = (away_prof["venue_corners"] + home_prof["venue_corners_against"]) / 2

            # --- Step 2: Adjustments ---
            # a) Recent 5-match trend (25% weight) — capped to prevent inflation
            home_trend_adj = 1.0
            if home_prof["avg_corners"] > 0:
                home_trend_adj = 0.75 + 0.25 * (home_prof["recent5_avg"] / home_prof["avg_corners"])
                home_trend_adj = max(0.90, min(1.10, home_trend_adj))
            away_trend_adj = 1.0
            if away_prof["avg_corners"] > 0:
                away_trend_adj = 0.75 + 0.25 * (away_prof["recent5_avg"] / away_prof["avg_corners"])
                away_trend_adj = max(0.90, min(1.10, away_trend_adj))

            # b) Shot volume factor (more shots → more corners from saves/deflections)
            lg_median_total = lg["median_total"] if lg["median_total"] > 0 else 10.0
            shot_adj_h = 1.0
            shot_adj_a = 1.0
            if home_prof["avg_shots"] > 0:
                shot_adj_h = 1.0 + (home_prof["avg_shots"] - 12.0) / 100.0
                shot_adj_h = max(0.96, min(1.05, shot_adj_h))
            if away_prof["avg_shots"] > 0:
                shot_adj_a = 1.0 + (away_prof["avg_shots"] - 12.0) / 100.0
                shot_adj_a = max(0.96, min(1.05, shot_adj_a))

            # c) Blocked shot inflation (blocked shots → more corners)
            block_adj_h = 1.0 + (home_prof["avg_blocked"]) / 100.0
            block_adj_a = 1.0 + (away_prof["avg_blocked"]) / 100.0
            block_adj_h = max(1.0, min(1.05, block_adj_h))
            block_adj_a = max(1.0, min(1.05, block_adj_a))

            # d) 1X2 odds-implied dominance
            odds_1 = fa.safe_float(fix.get("odds_ft_1", fix.get("odds_home", 0)))
            odds_x = fa.safe_float(fix.get("odds_ft_x", fix.get("odds_draw", 0)))
            odds_2 = fa.safe_float(fix.get("odds_ft_2", fix.get("odds_away", 0)))
            dominance_adj_h, dominance_adj_a = 1.0, 1.0
            if odds_1 > 0 and odds_2 > 0:
                imp_home = 1.0 / odds_1
                imp_away = 1.0 / odds_2
                total_imp = imp_home + imp_away + (1.0 / odds_x if odds_x > 0 else 0.25)
                fav_ratio = imp_home / total_imp
                # Favorites push for corners; underdogs defend deep → corners
                dominance_adj_h = 0.95 + fav_ratio * 0.12
                dominance_adj_a = 0.95 + (1 - fav_ratio) * 0.12
                dominance_adj_h = max(0.95, min(1.06, dominance_adj_h))
                dominance_adj_a = max(0.95, min(1.06, dominance_adj_a))

            # e) League normalization
            home_corner_idx = home_prof["avg_corners"] / (lg["median_home"] if lg["median_home"] > 0 else 5)
            away_corner_idx = away_prof["avg_corners"] / (lg["median_away"] if lg["median_away"] > 0 else 5)

            # Apply adjustments — cap total multiplier to ±15% of baseline
            home_multiplier = home_trend_adj * shot_adj_h * block_adj_h * dominance_adj_h
            away_multiplier = away_trend_adj * shot_adj_a * block_adj_a * dominance_adj_a
            home_multiplier = max(0.85, min(1.15, home_multiplier))
            away_multiplier = max(0.85, min(1.15, away_multiplier))
            adj_home = home_expected * home_multiplier
            adj_away = away_expected * away_multiplier

            # --- Step 3: Match interaction multiplier ---
            interaction = 1.0
            # High-shot team vs blocking team → more corners
            if home_prof["avg_shots"] > 13 and away_prof["avg_blocked"] > 3:
                interaction += 0.03
            if away_prof["avg_shots"] > 13 and home_prof["avg_blocked"] > 3:
                interaction += 0.03
            # Both below median → decrease
            if home_corner_idx < 0.9 and away_corner_idx < 0.9:
                interaction -= 0.03
            interaction = max(0.95, min(1.08, interaction))

            total_expected = (adj_home + adj_away) * interaction

            # --- Corner line ---
            bk_corner_line = None
            bk_corner_over_odds = None
            bk_corner_under_odds = None
            for lf in ("total_corners_line", "corners_line", "odds_corners_over_line",
                        "corners_over_under_line"):
                val = fix.get(lf)
                if val is not None:
                    try:
                        bk_corner_line = float(val)
                        break
                    except (ValueError, TypeError):
                        pass
            for of in ("odds_corners_over", "odds_total_corners_over", "corners_over_odds"):
                val = fix.get(of)
                if val is not None:
                    try:
                        bk_corner_over_odds = float(val)
                        break
                    except (ValueError, TypeError):
                        pass
            for uf in ("odds_corners_under", "odds_total_corners_under", "corners_under_odds"):
                val = fix.get(uf)
                if val is not None:
                    try:
                        bk_corner_under_odds = float(val)
                        break
                    except (ValueError, TypeError):
                        pass

            corner_line = 10.5  # Fixed: always evaluate Over 10.5 corners

            # --- Step 4: Probability ---
            all_match_corners = []
            for m in home_last10:
                mc = m.get("match_corners", -1)
                if mc >= 0:
                    all_match_corners.append(mc)
            for m in away_last10:
                mc = m.get("match_corners", -1)
                if mc >= 0:
                    all_match_corners.append(mc)
            if len(all_match_corners) >= 2:
                c_mean = sum(all_match_corners) / len(all_match_corners)
                c_var = sum((x - c_mean) ** 2 for x in all_match_corners) / (len(all_match_corners) - 1)
            else:
                c_var = total_expected

            prob_over_main = _prob_over(total_expected, c_var, corner_line)
            prob_over_plus1 = _prob_over(total_expected, c_var, corner_line + 1)
            prob_over_minus1 = _prob_over(total_expected, c_var, corner_line - 1)

            # --- Step 5: EV ---
            implied_prob = 0.0
            ev_pct = 0.0
            if bk_corner_over_odds and bk_corner_over_odds > 1.0:
                implied_prob = round(1.0 / bk_corner_over_odds * 100, 1)
                ev_pct = round((prob_over_main * bk_corner_over_odds - 1) * 100, 2)
            elif prob_over_main >= 0.55:
                implied_prob = round(prob_over_main * 100, 1)
                ev_pct = 0.0

            prob_pct = round(prob_over_main * 100, 1)

            if prob_pct < 55:
                continue
            if bk_corner_over_odds and bk_corner_over_odds > 1.0 and ev_pct < 3.0:
                continue

            # Over potential indicator
            combined_idx = (home_corner_idx + away_corner_idx) / 2
            if combined_idx >= 1.15:
                over_potential = "High"
            elif combined_idx >= 0.95:
                over_potential = "Medium"
            else:
                over_potential = "Low"

            prob_edge = prob_pct - implied_prob if implied_prob > 0 else 0.0
            corners_vs_line = round(total_expected - corner_line, 1)

            results.append({
                "home_name": home_name,
                "away_name": away_name,
                "league": league,
                "kick_off": ko_str,
                "kick_off_unix": ko_unix,
                "league_median_corners": lg["median_total"],
                "model_expected_total": round(total_expected, 1),
                "line": corner_line,
                "model_prob": prob_pct,
                "prob_over_plus1": round(prob_over_plus1 * 100, 1),
                "prob_over_minus1": round(prob_over_minus1 * 100, 1),
                "implied_prob": implied_prob,
                "ev_pct": ev_pct,
                "home_expected": round(adj_home, 1),
                "away_expected": round(adj_away, 1),
                "home_avg_corners": home_prof["avg_corners"],
                "away_avg_corners": away_prof["avg_corners"],
                "home_avg_shots": home_prof["avg_shots"],
                "away_avg_shots": away_prof["avg_shots"],
                "home_avg_blocked": home_prof["avg_blocked"],
                "away_avg_blocked": away_prof["avg_blocked"],
                "over_potential": over_potential,
                "corners_vs_line": corners_vs_line,
                "prob_edge": round(prob_edge, 1),
                "has_bk_odds": bk_corner_over_odds is not None and bk_corner_over_odds > 1.0,
                "bk_over_odds": round(bk_corner_over_odds, 2) if bk_corner_over_odds else None,
            })

        results.sort(key=lambda r: (r["ev_pct"], r["prob_edge"], r["corners_vs_line"]),
                     reverse=True)
        top5 = results[:5]

        with _lock:
            _state["corner_overs_results"] = top5
            _state["corner_overs_status"] = "done"
            _state["corner_overs_ts"] = int(datetime.now(timezone.utc).timestamp())

    except Exception as e:
        with _lock:
            _state["corner_overs_status"] = "done"
            _state["corner_overs_error"] = str(e)


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


@app.route("/api/shot-overs", methods=["POST"])
def trigger_shot_overs():
    """Trigger Shot Over Opportunities analysis."""
    with _lock:
        if _state["shot_overs_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_shot_overs, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/shot-overs-status")
def shot_overs_status():
    """Poll Shot Over analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["shot_overs_status"],
            "results": _state["shot_overs_results"],
            "error": _state["shot_overs_error"],
            "api_credits_used": fa.api_credits_used,
        })


@app.route("/api/corner-overs", methods=["POST"])
def trigger_corner_overs():
    """Trigger Corner Over Opportunities analysis."""
    with _lock:
        if _state["corner_overs_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_corner_overs, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/corner-overs-status")
def corner_overs_status():
    """Poll Corner Over analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["corner_overs_status"],
            "results": _state["corner_overs_results"],
            "error": _state["corner_overs_error"],
            "api_credits_used": fa.api_credits_used,
        })


# ---------------------------------------------------------------------------
# Card Risk Routes
# ---------------------------------------------------------------------------

@app.route("/api/card-risk", methods=["POST"])
def trigger_card_risk():
    """Trigger Card Risk analysis for next 24h."""
    with _lock:
        if _state["card_risk_status"] == "running":
            return jsonify({"status": "running"}), 202
        if not _state["fixtures"]:
            return jsonify({"error": "No fixtures loaded. Refresh first."}), 400

    t = threading.Thread(target=_run_card_risk, daemon=True)
    t.start()
    return jsonify({"status": "running"}), 202


@app.route("/api/card-risk-status")
def card_risk_status():
    """Poll Card Risk analysis status and results."""
    with _lock:
        return jsonify({
            "status": _state["card_risk_status"],
            "results": _state["card_risk_results"],
            "error": _state["card_risk_error"],
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
