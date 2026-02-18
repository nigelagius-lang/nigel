#!/usr/bin/env python3
# Football Match Analysis Tool
# Connects to the FootyStats API to analyze matches and calculate hit rates
# for goals, shots, fouls, cards, corners, and match result markets.
#
# Usage:
#     python football_analysis.py "Arsenal vs Chelsea"
#     python football_analysis.py "Arsenal vs Chelsea" --date 2026-02-15
#     python football_analysis.py --list              # list today's fixtures

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.football-data-api.com"
API_KEY = os.getenv("FOOTYSTATS_API_KEY", "")
api_credits_used = 0
DEBUG = False


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(endpoint: str, params: dict | None = None) -> dict | list:
    """Make a GET request to the FootyStats API and return JSON."""
    global api_credits_used
    params = params or {}
    params["key"] = API_KEY
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    if DEBUG:
        display_params = {k: v for k, v in params.items() if k != "key"}
        print(f"  [DEBUG] GET {url} params={display_params}")
    try:
        resp = requests.get(url, params=params, timeout=30)
    except requests.exceptions.ProxyError:
        raise ConnectionError("Connection blocked by proxy. Run this tool from your local machine.")
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"Could not connect to FootyStats API: {e}")
    api_credits_used += 1
    if resp.status_code == 403:
        raise PermissionError("API returned 403 Forbidden. Check your API key.")
    if resp.status_code == 429:
        raise RuntimeError("API rate limit exceeded. Wait and try again.")
    resp.raise_for_status()
    data = resp.json()
    if DEBUG:
        import json
        preview = json.dumps(data, indent=2)[:2000]
        print(f"  [DEBUG] Response ({resp.status_code}):\n{preview}")
    # The API wraps some responses in {"success": true, "data": [...]}
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


def fetch_todays_matches(date_str: str | None = None, tz: str = "Etc/UTC") -> list[dict]:
    """Return today's (or a given date's) fixtures."""
    params: dict = {"timezone": tz}
    if date_str:
        params["date"] = date_str
    matches = api_get("todays-matches", params)
    if not isinstance(matches, list):
        return []
    return matches


def fetch_league_table(season_id: int) -> list[dict]:
    """Return the league table for a given season."""
    data = api_get("league-tables", {"season_id": season_id, "include": "stats"})
    if isinstance(data, dict):
        # Some responses nest under "league_table"
        return data.get("league_table", data.get("table", [data]))
    return data if isinstance(data, list) else []


def fetch_lastx(team_id: int) -> list[dict]:
    """Return the last‑X matches for a team (aims for last 10)."""
    data = api_get("lastx", {"team_id": team_id})
    if isinstance(data, dict):
        # Response might nest matches under a key
        for key in ("lastx", "matches", "data"):
            if key in data and isinstance(data[key], list):
                return data[key][:10]
        return []
    if isinstance(data, list):
        return data[:10]
    return []


_league_matches_cache: dict[int, list[dict]] = {}

def fetch_league_matches(season_id: int) -> list[dict]:
    """Fetch completed matches for a season. Results are cached per season_id."""
    if season_id in _league_matches_cache:
        return _league_matches_cache[season_id]
    data = api_get("league-matches", {"season_id": season_id, "max_per_page": 500})
    if not isinstance(data, list):
        data = []
    _league_matches_cache[season_id] = data
    return data


_league_referees_cache: dict[int, list[dict]] = {}

def fetch_league_referees(season_id: int) -> list[dict]:
    """Fetch all referees for a league season (cached)."""
    if season_id in _league_referees_cache:
        return _league_referees_cache[season_id]
    try:
        data = api_get("league-referees", {"season_id": season_id})
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    _league_referees_cache[season_id] = data
    return data


_referee_cache: dict[int, dict] = {}

def fetch_referee(referee_id: int) -> dict:
    """Fetch individual referee stats via /referee endpoint (cached).

    The response includes at least ``id`` and ``full_name``.
    May also include card/foul averages depending on the API tier.
    """
    if referee_id in _referee_cache:
        return _referee_cache[referee_id]
    try:
        data = api_get("referee", {"referee_id": referee_id})
        result: dict = {}
        if isinstance(data, list) and data:
            result = data[0]
        elif isinstance(data, dict):
            result = data
        _referee_cache[referee_id] = result
        return result
    except Exception:
        _referee_cache[referee_id] = {}
        return {}


_league_players_cache: dict[int, list[dict]] = {}

def fetch_league_players(season_id: int) -> list[dict]:
    """Fetch ALL players for a league season, paginating through all pages."""
    if season_id in _league_players_cache:
        return _league_players_cache[season_id]
    all_players: list[dict] = []
    page = 1
    while page <= 10:  # safety cap
        batch = api_get("league-players", {"season_id": season_id, "page": page})
        if not isinstance(batch, list) or not batch:
            break
        all_players.extend(batch)
        if len(batch) < 200:  # last page
            break
        page += 1
    if DEBUG:
        # Show which club_team_ids we got
        cids = set(p.get("club_team_id") for p in all_players if p.get("club_team_id"))
        print(f"  [DEBUG] league-players: {len(all_players)} players across {page} page(s), {len(cids)} teams")
    _league_players_cache[season_id] = all_players
    return all_players


_league_teams_cache: dict[int, list[dict]] = {}

def fetch_league_teams(season_id: int) -> list[dict]:
    """Fetch all teams in a league season."""
    if season_id in _league_teams_cache:
        return _league_teams_cache[season_id]
    data = api_get("league-teams", {"season_id": season_id})
    if not isinstance(data, list):
        data = []
    _league_teams_cache[season_id] = data
    return data


def resolve_club_team_id(season_id: int, fixture_team_name: str) -> int | None:
    """
    Map a fixture team name to the club_team_id used in league-players.
    Uses league-teams endpoint to find the correct ID by fuzzy name match.
    """
    teams = fetch_league_teams(season_id)
    best_id, best_score = None, 0.0
    for t in teams:
        t_name = t.get("name", t.get("team_name", t.get("cleanName", "")))
        t_id = t.get("id") or t.get("team_id")
        if not t_name or not t_id:
            continue
        score = similarity(fixture_team_name, t_name)
        if score > best_score:
            best_score = score
            best_id = int(t_id)
    if DEBUG:
        print(f"  [DEBUG] resolve_club_team_id('{fixture_team_name}') -> {best_id} (score={best_score:.2f})")
    return best_id if best_score > 0.5 else None


def fetch_player_detail(player_id: int, competition_id: int = 0) -> dict:
    """
    Fetch detailed stats for a single player via /player-stats.
    Returns the entry matching competition_id if possible.
    The 'detailed' sub-object (shots, fouls, etc.) is flattened into the result.
    """
    data = api_get("player-stats", {"player_id": player_id})
    if not isinstance(data, list) or not data:
        return {}

    # Find the entry for the right competition (e.g. Premier League 15050)
    best = data[0]
    if competition_id:
        for entry in data:
            if entry.get("competition_id") == competition_id:
                best = entry
                break

    # Flatten the 'detailed' sub-object into the main dict
    detailed = best.get("detailed")
    if isinstance(detailed, dict):
        for k, v in detailed.items():
            if k not in best:
                best[k] = v
    return best


def safe_float(val, default=0.0) -> float:
    """Convert a value to float safely."""
    try:
        v = float(val)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _extract_detail_stat(detail: dict, *keys: str) -> float:
    """Try multiple keys on a player-stats response, return the first valid float."""
    for k in keys:
        v = detail.get(k)
        if v is not None:
            f = safe_float(v)
            if f > 0:
                return f
    return 0.0


def get_team_players(season_id: int, club_team_id: int, competition_id: int = 0, max_detail: int = 8) -> list[dict]:
    """
    Return player stats for a team.
    1. Gets roster from league-players (matched by club_team_id)
    2. Fetches detailed stats (/player-stats) for top players to get
       shots, shots on target, and fouls committed.
    """
    all_players = fetch_league_players(season_id)
    # Use competition_id from the first player if not provided
    if not competition_id and all_players:
        competition_id = all_players[0].get("competition_id", 0)
    if DEBUG:
        print(f"  [DEBUG] league-players returned {len(all_players)} total, filtering for club_team_id={club_team_id}, competition_id={competition_id}")

    # Filter to this team's players
    roster = []
    for p in all_players:
        pid_team = p.get("club_team_id")
        if pid_team is None:
            continue
        if int(pid_team) != club_team_id:
            continue
        appearances = safe_int(p.get("appearances_overall", 0), 0)
        if appearances < 1:
            continue
        roster.append(p)

    # Sort by appearances descending — fetch detail for the top N
    roster.sort(key=lambda x: safe_int(x.get("appearances_overall", 0), 0), reverse=True)
    if DEBUG:
        print(f"  [DEBUG] Found {len(roster)} players for club_team_id={club_team_id}")

    team_players = []
    for i, p in enumerate(roster):
        player_id = p.get("id")
        name = p.get("known_as") or p.get("full_name") or "Unknown"
        position = p.get("position", "")
        appearances = safe_int(p.get("appearances_overall", 0), 0)

        shots_pg = 0.0
        sot_pg = 0.0
        fouls_pg = 0.0
        shots_total = 0.0
        sot_total = 0.0
        fouls_total = 0.0

        # Fetch detailed stats for top players (by appearances)
        if i < max_detail and player_id:
            if DEBUG:
                print(f"    [DEBUG] Fetching detail for {name} (id={player_id})...")
            detail = fetch_player_detail(player_id, competition_id)
            if DEBUG and i == 0 and detail:
                import json
                # Show what competition was matched
                print(f"    [DEBUG] Matched competition: {detail.get('league', '?')} ({detail.get('competition_id', '?')})")
                # Show keys related to shots/fouls (including from flattened 'detailed')
                detail_keys = [k for k in sorted(detail.keys()) if "shot" in k.lower() or "foul" in k.lower()]
                print(f"    [DEBUG] Detail shot/foul keys: {detail_keys}")
                # Show the raw 'detailed' sub-object if it exists
                raw_detailed = detail.get("detailed")
                if isinstance(raw_detailed, dict):
                    print(f"    [DEBUG] 'detailed' sub-keys: {sorted(raw_detailed.keys())}")
                    print(f"    [DEBUG] 'detailed' sample: {json.dumps(raw_detailed, indent=2, default=str)[:2000]}")
                else:
                    print(f"    [DEBUG] No 'detailed' sub-object found")

            # Shots per game — try many possible field names
            shots_pg = _extract_detail_stat(detail,
                "shots_per_game", "shots_per_90_overall",
                "shots_per_90", "avg_shots_per_game")
            shots_total = _extract_detail_stat(detail,
                "shots_overall", "total_shots_overall",
                "total_shots", "shots_total")
            if shots_pg == 0 and shots_total > 0 and appearances > 0:
                shots_pg = shots_total / appearances

            # Shots on target per game
            sot_pg = _extract_detail_stat(detail,
                "shots_on_target_per_game", "shots_on_target_per_90_overall",
                "shots_on_target_per_90", "avg_shots_on_target_per_game")
            sot_total = _extract_detail_stat(detail,
                "shots_on_target_overall", "total_shots_on_target",
                "shots_on_target_total")
            if sot_pg == 0 and sot_total > 0 and appearances > 0:
                sot_pg = sot_total / appearances

            # Fouls committed per game
            fouls_pg = _extract_detail_stat(detail,
                "fouls_committed_per_game", "fouls_committed_per_90_overall",
                "fouls_per_game", "fouls_per_90",
                "avg_fouls_committed_per_game")
            fouls_total = _extract_detail_stat(detail,
                "fouls_committed_overall", "fouls_committed",
                "total_fouls_committed", "fouls_overall")
            if fouls_pg == 0 and fouls_total > 0 and appearances > 0:
                fouls_pg = fouls_total / appearances

        team_players.append({
            "name": name,
            "position": position,
            "appearances": appearances,
            "shots_total": shots_total,
            "shots_per_game": shots_pg,
            "sot_total": sot_total,
            "sot_per_game": sot_pg,
            "fouls_total": fouls_total,
            "fouls_per_game": fouls_pg,
        })

    return team_players


def compute_player_picks(players: list[dict], team_name: str) -> list[dict]:
    """
    Compute over/under picks for player fouls, shots, and SOT.
    Uses per-game averages to estimate probability of hitting thresholds.
    """
    picks = []
    for p in players:
        if p["appearances"] < 3:
            continue

        name = p["name"]
        apps = p["appearances"]

        # --- Player Shots ---
        if p["shots_total"] > 0 or p["shots_per_game"] > 0:
            for threshold in [0.5, 1.5, 2.5, 3.5]:
                avg = p["shots_per_game"]
                total = p["shots_total"] if p["shots_total"] > 0 else avg * apps
                if avg > 0:
                    games_over = _estimate_games_over(total, apps, threshold)
                    pct = (games_over / apps * 100) if apps > 0 else 0
                    if pct >= 50:
                        picks.append({
                            "category": "Player Shots",
                            "market": f"{name} Over {threshold:.1f} Shots",
                            "hits": games_over,
                            "total": apps,
                            "reason": f"{team_name} | Avg {avg:.1f} shots/game ({apps} apps)",
                        })

        # --- Player Shots on Target ---
        if p["sot_total"] > 0 or p["sot_per_game"] > 0:
            for threshold in [0.5, 1.5, 2.5]:
                avg = p["sot_per_game"]
                total = p["sot_total"] if p["sot_total"] > 0 else avg * apps
                if avg > 0:
                    games_over = _estimate_games_over(total, apps, threshold)
                    pct = (games_over / apps * 100) if apps > 0 else 0
                    if pct >= 50:
                        picks.append({
                            "category": "Player SOT",
                            "market": f"{name} Over {threshold:.1f} Shots on Target",
                            "hits": games_over,
                            "total": apps,
                            "reason": f"{team_name} | Avg {avg:.1f} SOT/game ({apps} apps)",
                        })

        # --- Player Fouls Committed ---
        if p["fouls_total"] > 0 or p["fouls_per_game"] > 0:
            for threshold in [0.5, 1.5, 2.5]:
                avg = p["fouls_per_game"]
                total = p["fouls_total"] if p["fouls_total"] > 0 else avg * apps
                if avg > 0:
                    games_over = _estimate_games_over(total, apps, threshold)
                    pct = (games_over / apps * 100) if apps > 0 else 0
                    if pct >= 50:
                        picks.append({
                            "category": "Player Fouls",
                            "market": f"{name} Over {threshold:.1f} Fouls",
                            "hits": games_over,
                            "total": apps,
                            "reason": f"{team_name} | Avg {avg:.1f} fouls/game ({apps} apps)",
                        })

    return picks


def _estimate_games_over(total: float, apps: int, threshold: float) -> int:
    """
    Estimate how many games a player exceeded a threshold, given only
    their season total and appearances.  Uses a Poisson CDF approximation.
    """
    import math
    if apps <= 0 or total <= 0:
        return 0
    lam = total / apps  # average per game (lambda)
    k = int(threshold)  # e.g. threshold 0.5 -> k=0, threshold 1.5 -> k=1
    cdf = 0.0
    for i in range(k + 1):
        cdf += math.exp(-lam) * (lam ** i) / math.factorial(i)
    prob_over = 1.0 - cdf
    return round(prob_over * apps)


# ---------------------------------------------------------------------------
# Match field extraction helpers
# ---------------------------------------------------------------------------

def safe_int(val, default=-1) -> int:
    """Convert a value to int, treating negatives as missing."""
    try:
        v = int(val)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def extract_match_stats(match: dict, team_id: int) -> dict | None:
    """
    Extract per-match stats for *team_id* from a match object.
    Returns None if essential data is missing.
    """
    home_id = match.get("homeID") or match.get("home_id")
    away_id = match.get("awayID") or match.get("away_id")

    if home_id is None or away_id is None:
        return None

    home_id, away_id = int(home_id), int(away_id)
    is_home = team_id == home_id

    home_goals = safe_int(match.get("homeGoalCount", match.get("home_goals", -1)))
    away_goals = safe_int(match.get("awayGoalCount", match.get("away_goals", -1)))
    total_goals = safe_int(match.get("totalGoalCount", -1))
    if total_goals < 0 and home_goals >= 0 and away_goals >= 0:
        total_goals = home_goals + away_goals

    # Shots
    home_shots = safe_int(match.get("team_a_shots", match.get("home_shots", match.get("homeShots", -1))))
    away_shots = safe_int(match.get("team_b_shots", match.get("away_shots", match.get("awayShots", -1))))
    home_sot = safe_int(match.get("team_a_shotsOnTarget",
                  match.get("home_shots_on_target",
                  match.get("homeShotsOnTarget", -1))))
    away_sot = safe_int(match.get("team_b_shotsOnTarget",
                  match.get("away_shots_on_target",
                  match.get("awayShotsOnTarget", -1))))

    # Fouls
    home_fouls = safe_int(match.get("team_a_fouls", match.get("home_fouls", match.get("homeFouls", -1))))
    away_fouls = safe_int(match.get("team_b_fouls", match.get("away_fouls", match.get("awayFouls", -1))))

    # Cards
    home_yellows = safe_int(match.get("team_a_yellow_cards",
                     match.get("home_yellow_cards",
                     match.get("homeYellowCards", -1))))
    away_yellows = safe_int(match.get("team_b_yellow_cards",
                     match.get("away_yellow_cards",
                     match.get("awayYellowCards", -1))))
    home_reds = safe_int(match.get("team_a_red_cards",
                  match.get("home_red_cards",
                  match.get("homeRedCards", -1))))
    away_reds = safe_int(match.get("team_b_red_cards",
                  match.get("away_red_cards",
                  match.get("awayRedCards", -1))))

    # Corners
    home_corners = safe_int(match.get("team_a_corners",
                     match.get("home_corners",
                     match.get("homeCorners", -1))))
    away_corners = safe_int(match.get("team_b_corners",
                     match.get("away_corners",
                     match.get("awayCorners", -1))))

    # Half-time goals
    home_ht_goals = safe_int(match.get("homeHTGoalCount",
                      match.get("ht_goals_team_a",
                      match.get("home_ht_goals", -1))))
    away_ht_goals = safe_int(match.get("awayHTGoalCount",
                      match.get("ht_goals_team_b",
                      match.get("away_ht_goals", -1))))

    # Second-half goals (derived)
    home_2h_goals = (home_goals - home_ht_goals) if home_goals >= 0 and home_ht_goals >= 0 else -1
    away_2h_goals = (away_goals - away_ht_goals) if away_goals >= 0 and away_ht_goals >= 0 else -1

    # Half-time corners
    home_ht_corners = safe_int(match.get("team_a_corners_halftime",
                        match.get("ht_corners_team_a",
                        match.get("home_ht_corners", -1))))
    away_ht_corners = safe_int(match.get("team_b_corners_halftime",
                        match.get("ht_corners_team_b",
                        match.get("away_ht_corners", -1))))

    # Second-half corners (derived)
    home_2h_corners = (home_corners - home_ht_corners) if home_corners >= 0 and home_ht_corners >= 0 else -1
    away_2h_corners = (away_corners - away_ht_corners) if away_corners >= 0 and away_ht_corners >= 0 else -1

    # Half-time shots (if available)
    home_ht_shots = safe_int(match.get("team_a_shots_halftime",
                      match.get("ht_shots_team_a",
                      match.get("home_ht_shots", -1))))
    away_ht_shots = safe_int(match.get("team_b_shots_halftime",
                      match.get("ht_shots_team_b",
                      match.get("away_ht_shots", -1))))
    home_2h_shots = (home_shots - home_ht_shots) if home_shots >= 0 and home_ht_shots >= 0 else -1
    away_2h_shots = (away_shots - away_ht_shots) if away_shots >= 0 and away_ht_shots >= 0 else -1

    # Opponent info
    opponent_id = away_id if is_home else home_id
    opponent_name = (match.get("away_name", "") if is_home
                     else match.get("home_name", ""))

    goals_scored = home_goals if is_home else away_goals
    goals_conceded = away_goals if is_home else home_goals

    return {
        "is_home": is_home,
        "goals_scored": goals_scored,
        "goals_conceded": goals_conceded,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "total_goals": total_goals,
        "shots": home_shots if is_home else away_shots,
        "shots_against": away_shots if is_home else home_shots,
        "sot": home_sot if is_home else away_sot,
        "sot_against": away_sot if is_home else home_sot,
        "fouls": home_fouls if is_home else away_fouls,
        "fouls_against": away_fouls if is_home else home_fouls,
        "yellows": home_yellows if is_home else away_yellows,
        "yellows_against": away_yellows if is_home else home_yellows,
        "reds": home_reds if is_home else away_reds,
        "reds_against": away_reds if is_home else home_reds,
        "corners": home_corners if is_home else away_corners,
        "corners_against": away_corners if is_home else home_corners,
        "match_fouls": (home_fouls + away_fouls) if home_fouls >= 0 and away_fouls >= 0 else -1,
        "match_yellows": (home_yellows + away_yellows) if home_yellows >= 0 and away_yellows >= 0 else -1,
        "match_reds": (home_reds + away_reds) if home_reds >= 0 and away_reds >= 0 else -1,
        "match_corners": (home_corners + away_corners) if home_corners >= 0 and away_corners >= 0 else -1,
        "match_shots": (home_shots + away_shots) if home_shots >= 0 and away_shots >= 0 else -1,
        # Half-time / second-half breakdowns
        "goals_scored_1h": home_ht_goals if is_home else away_ht_goals,
        "goals_conceded_1h": away_ht_goals if is_home else home_ht_goals,
        "goals_scored_2h": home_2h_goals if is_home else away_2h_goals,
        "goals_conceded_2h": away_2h_goals if is_home else home_2h_goals,
        "total_goals_1h": (home_ht_goals + away_ht_goals) if home_ht_goals >= 0 and away_ht_goals >= 0 else -1,
        "total_goals_2h": (home_2h_goals + away_2h_goals) if home_2h_goals >= 0 and away_2h_goals >= 0 else -1,
        "corners_1h": home_ht_corners if is_home else away_ht_corners,
        "corners_2h": home_2h_corners if is_home else away_2h_corners,
        "match_corners_1h": (home_ht_corners + away_ht_corners) if home_ht_corners >= 0 and away_ht_corners >= 0 else -1,
        "match_corners_2h": (home_2h_corners + away_2h_corners) if home_2h_corners >= 0 and away_2h_corners >= 0 else -1,
        "shots_1h": home_ht_shots if is_home else away_ht_shots,
        "shots_2h": home_2h_shots if is_home else away_2h_shots,
        "match_shots_1h": (home_ht_shots + away_ht_shots) if home_ht_shots >= 0 and away_ht_shots >= 0 else -1,
        "match_shots_2h": (home_2h_shots + away_2h_shots) if home_2h_shots >= 0 and away_2h_shots >= 0 else -1,
        "opponent_id": opponent_id,
        "opponent_name": opponent_name,
    }


# ---------------------------------------------------------------------------
# Fuzzy match search
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    return name.lower().strip().replace("fc ", "").replace(" fc", "").replace(".", "")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def find_match(fixtures: list[dict], query: str) -> dict | None:
    """
    Find a fixture matching a query like 'Arsenal vs Chelsea'.
    Returns the best match or None.
    """
    parts = [p.strip() for p in query.lower().replace(" v ", " vs ").split(" vs ")]
    if len(parts) != 2:
        # Try to find any fixture containing the query as a substring
        parts = [query.strip()]

    best, best_score = None, 0.0
    for fix in fixtures:
        home = fix.get("home_name", "")
        away = fix.get("away_name", "")
        if len(parts) == 2:
            # Score both orderings
            s1 = similarity(parts[0], home) + similarity(parts[1], away)
            s2 = similarity(parts[0], away) + similarity(parts[1], home)
            score = max(s1, s2)
        else:
            score = max(similarity(parts[0], home), similarity(parts[0], away))
        if score > best_score:
            best_score = score
            best = fix

    if best and best_score > 0.5:
        return best
    return None


# ---------------------------------------------------------------------------
# Team last-10 data retrieval
# ---------------------------------------------------------------------------

def get_team_last10(team_id: int, season_id: int | None = None) -> list[dict]:
    """
    Get extracted stats for a team's last 10 matches.
    Uses /league-matches to get individual match data.
    """
    raw = []
    if season_id:
        try:
            all_matches = fetch_league_matches(season_id)
            # Filter to completed matches involving team_id
            raw = [
                m for m in all_matches
                if m.get("status") == "complete"
                and (int(m.get("homeID", 0)) == team_id or int(m.get("awayID", 0)) == team_id)
            ]
            # Sort by date descending, take last 10
            raw.sort(key=lambda m: m.get("date_unix", 0), reverse=True)
            raw = raw[:10]
        except Exception as e:
            if DEBUG:
                print(f"  [DEBUG] league-matches failed: {e}")
            raw = []

    results = []
    for m in raw:
        stats = extract_match_stats(m, team_id)
        if stats:
            results.append(stats)
    return results[:10]


# ---------------------------------------------------------------------------
# League position lookup for contextual analysis
# ---------------------------------------------------------------------------

def build_position_map(season_id: int) -> dict[int, int]:
    """Return {team_id: league_position} from the league table."""
    try:
        table = fetch_league_table(season_id)
    except Exception:
        return {}
    pos_map = {}
    for entry in table:
        tid = entry.get("id") or entry.get("team_id") or entry.get("teamId")
        pos = entry.get("position") or entry.get("tablePosition")
        if tid and pos:
            pos_map[int(tid)] = int(pos)
    return pos_map


def get_h2h_matches(team_a_id: int, team_b_id: int, season_id: int | None = None) -> list[dict]:
    """Return head-to-head matches between two teams from league-matches data.

    Each entry is the raw match dict (not extracted).  Results are sorted by
    date descending (most recent first).
    """
    if not season_id:
        return []
    try:
        all_matches = fetch_league_matches(season_id)
    except Exception:
        return []
    h2h = []
    for m in all_matches:
        if m.get("status") != "complete":
            continue
        hid = int(m.get("homeID", 0) or 0)
        aid = int(m.get("awayID", 0) or 0)
        if (hid == team_a_id and aid == team_b_id) or (hid == team_b_id and aid == team_a_id):
            h2h.append(m)
    h2h.sort(key=lambda m: m.get("date_unix", 0), reverse=True)
    return h2h


def get_league_team_count(season_id: int) -> int:
    """Return the number of teams in a league season (from league table)."""
    try:
        table = fetch_league_table(season_id)
        return len(table) if table else 20
    except Exception:
        return 20


# ---------------------------------------------------------------------------
# Analysis calculations
# ---------------------------------------------------------------------------

def valid(values: list[int]) -> list[int]:
    """Filter out missing (-1) values."""
    return [v for v in values if v >= 0]


def avg(values: list[int]) -> float:
    v = valid(values)
    return sum(v) / len(v) if v else 0.0


def hit_rate(values: list[bool]) -> tuple[int, int]:
    """Return (hits, total)."""
    return sum(values), len(values)


def contextual_split(matches: list[dict], pos_map: dict[int, int], total_teams: int):
    """
    Split matches into context buckets:
      - vs_top: opponent in top half
      - vs_bottom: opponent in bottom half
      - home_form: matches played at home
      - away_form: matches played away
    """
    mid = max(total_teams // 2, 1)
    vs_top, vs_bottom = [], []
    home_form, away_form = [], []

    for m in matches:
        opp_pos = pos_map.get(m["opponent_id"], 0)
        if opp_pos > 0:
            if opp_pos <= mid:
                vs_top.append(m)
            else:
                vs_bottom.append(m)
        if m["is_home"]:
            home_form.append(m)
        else:
            away_form.append(m)

    return {
        "vs_top": vs_top,
        "vs_bottom": vs_bottom,
        "home_form": home_form[:5],
        "away_form": away_form[:5],
    }


def compute_team_averages(matches: list[dict]) -> dict:
    """Compute averages across a list of extracted match stats."""
    return {
        "avg_goals_scored": avg([m["goals_scored"] for m in matches]),
        "avg_goals_conceded": avg([m["goals_conceded"] for m in matches]),
        "avg_shots": avg([m["shots"] for m in matches]),
        "avg_sot": avg([m["sot"] for m in matches]),
        "avg_fouls": avg([m["fouls"] for m in matches]),
        "avg_yellows": avg([m["yellows"] for m in matches]),
        "avg_reds": avg([m["reds"] for m in matches]),
        "avg_corners": avg([m["corners"] for m in matches]),
        # Half-by-half breakdowns
        "avg_goals_scored_1h": avg([m["goals_scored_1h"] for m in matches]),
        "avg_goals_scored_2h": avg([m["goals_scored_2h"] for m in matches]),
        "avg_goals_conceded_1h": avg([m["goals_conceded_1h"] for m in matches]),
        "avg_goals_conceded_2h": avg([m["goals_conceded_2h"] for m in matches]),
        "avg_corners_1h": avg([m["corners_1h"] for m in matches]),
        "avg_corners_2h": avg([m["corners_2h"] for m in matches]),
        "avg_shots_1h": avg([m["shots_1h"] for m in matches]),
        "avg_shots_2h": avg([m["shots_2h"] for m in matches]),
    }


def compute_aggression_profile(matches: list[dict]) -> dict:
    """
    Classify a team's attacking intensity and half-by-half pattern.
    Uses goals, shots, and corners from the last N matches.
    """
    home_form = [m for m in matches if m["is_home"]]
    away_form = [m for m in matches if not m["is_home"]]

    gs_1h = valid([m["goals_scored_1h"] for m in matches])
    gs_2h = valid([m["goals_scored_2h"] for m in matches])
    gc_1h = valid([m["goals_conceded_1h"] for m in matches])
    gc_2h = valid([m["goals_conceded_2h"] for m in matches])
    c_1h = valid([m["corners_1h"] for m in matches])
    c_2h = valid([m["corners_2h"] for m in matches])
    sh_1h = valid([m["shots_1h"] for m in matches])
    sh_2h = valid([m["shots_2h"] for m in matches])

    avg_gs_1h = sum(gs_1h) / len(gs_1h) if gs_1h else 0
    avg_gs_2h = sum(gs_2h) / len(gs_2h) if gs_2h else 0
    avg_gc_1h = sum(gc_1h) / len(gc_1h) if gc_1h else 0
    avg_gc_2h = sum(gc_2h) / len(gc_2h) if gc_2h else 0
    avg_c_1h = sum(c_1h) / len(c_1h) if c_1h else 0
    avg_c_2h = sum(c_2h) / len(c_2h) if c_2h else 0
    avg_sh_1h = sum(sh_1h) / len(sh_1h) if sh_1h else 0
    avg_sh_2h = sum(sh_2h) / len(sh_2h) if sh_2h else 0

    total_gs = avg_gs_1h + avg_gs_2h
    total_gc = avg_gc_1h + avg_gc_2h
    total_shots = avg([m["shots"] for m in matches])
    total_corners = avg([m["corners"] for m in matches])

    # Style classification
    if total_gs >= 1.5 and total_shots >= 12:
        style = "Aggressive"
    elif total_gs >= 1.0 or total_shots >= 10:
        style = "Balanced"
    else:
        style = "Defensive"

    # Tempo classification
    if avg_gs_1h > 0 and avg_gs_2h > 0:
        if avg_gs_1h > avg_gs_2h * 1.3:
            tempo = "Fast Starters"
        elif avg_gs_2h > avg_gs_1h * 1.3:
            tempo = "Second Half Surgers"
        else:
            tempo = "Even Tempo"
    else:
        tempo = "Unknown"

    # Home vs away split
    home_avgs = compute_team_averages(home_form) if home_form else {}
    away_avgs = compute_team_averages(away_form) if away_form else {}

    return {
        "style": style,
        "tempo": tempo,
        "avg_goals_scored_1h": avg_gs_1h,
        "avg_goals_scored_2h": avg_gs_2h,
        "avg_goals_conceded_1h": avg_gc_1h,
        "avg_goals_conceded_2h": avg_gc_2h,
        "avg_corners_1h": avg_c_1h,
        "avg_corners_2h": avg_c_2h,
        "avg_shots_1h": avg_sh_1h,
        "avg_shots_2h": avg_sh_2h,
        "avg_goals_total": total_gs,
        "avg_conceded_total": total_gc,
        "avg_shots_total": total_shots,
        "avg_corners_total": total_corners,
        "home_avgs": home_avgs,
        "away_avgs": away_avgs,
    }


def compute_matchup_analysis(home_profile: dict, away_profile: dict,
                             home_name: str, away_name: str) -> str:
    """Generate a text analysis of how the two teams' styles match up."""
    lines = []

    hs, aws = home_profile["style"], away_profile["style"]
    if hs == "Aggressive" and aws == "Aggressive":
        lines.append(f"Both {home_name} and {away_name} play aggressively — expect an open, high-tempo game with plenty of shots and corners.")
    elif hs == "Aggressive" and aws == "Defensive":
        lines.append(f"{home_name} are aggressive attackers facing a defensive {away_name}. The home side should dominate shots and corners.")
    elif hs == "Defensive" and aws == "Aggressive":
        lines.append(f"{away_name} bring attacking intent against a defensively-minded {home_name}. Away corners and shots could be high.")
    elif hs == "Defensive" and aws == "Defensive":
        lines.append(f"Both sides tend to play defensively — this could be a tight, low-scoring affair with fewer corners.")
    else:
        lines.append(f"{home_name} ({hs}) vs {away_name} ({aws}) — a balanced contest expected.")

    # Tempo insights
    ht, at = home_profile["tempo"], away_profile["tempo"]
    if ht == "Fast Starters" and at == "Fast Starters":
        lines.append("Both teams are fast starters — first half goals are very likely.")
    elif ht == "Fast Starters" or at == "Fast Starters":
        starter = home_name if ht == "Fast Starters" else away_name
        lines.append(f"{starter} tend to score early — look at 1st half goal markets.")
    if ht == "Second Half Surgers" or at == "Second Half Surgers":
        surger = home_name if ht == "Second Half Surgers" else away_name
        lines.append(f"{surger} get stronger in the 2nd half — second half goals are likely.")

    # Corner edge
    hc = home_profile["avg_corners_total"]
    ac = away_profile["avg_corners_total"]
    if hc >= 5 and ac >= 5:
        lines.append(f"Both sides win lots of corners (avg {hc:.1f} + {ac:.1f}) — match corner overs look strong.")
    elif hc >= 5:
        lines.append(f"{home_name} average {hc:.1f} corners/game — look at home team corner overs.")
    elif ac >= 5:
        lines.append(f"{away_name} average {ac:.1f} corners/game — look at away team corner overs.")

    return " ".join(lines)


def compute_hit_rates(home_matches: list[dict], away_matches: list[dict]) -> list[dict]:
    """
    Compute all market hit rates.
    home_matches = last 10 for the home team
    away_matches = last 10 for the away team
    Returns a list of {market, selection, hits, total, reason}.
    """
    picks = []
    missing_markets = []

    # ---- GOALS ----
    # Over X.5 goals (using each team's last 10 match totals)
    home_totals = valid([m["total_goals"] for m in home_matches])
    away_totals = valid([m["total_goals"] for m in away_matches])
    all_totals = home_totals + away_totals

    if all_totals:
        combined_avg = sum(all_totals) / len(all_totals)
        for threshold, label in [(0.5, "Over 0.5"), (1.5, "Over 1.5"), (2.5, "Over 2.5"), (3.5, "Over 3.5")]:
            hits = sum(1 for g in all_totals if g > threshold)
            total = len(all_totals)
            picks.append({
                "category": "GOALS",
                "market": f"Match Goals {label}",
                "selection": label,
                "hits": hits,
                "total": total,
                "reason": f"Avg {combined_avg:.1f} goals/match across both teams' last 10",
            })
    else:
        missing_markets.append("Goals totals")

    # BTTS
    home_btts = [(m["home_goals"], m["away_goals"]) for m in home_matches
                 if m["home_goals"] >= 0 and m["away_goals"] >= 0]
    away_btts = [(m["home_goals"], m["away_goals"]) for m in away_matches
                 if m["home_goals"] >= 0 and m["away_goals"] >= 0]
    all_btts = home_btts + away_btts
    if all_btts:
        hits = sum(1 for h, a in all_btts if h > 0 and a > 0)
        picks.append({
            "category": "GOALS",
            "market": "BTTS",
            "selection": "Yes",
            "hits": hits,
            "total": len(all_btts),
            "reason": f"{hits}/{len(all_btts)} games saw both teams score",
        })
    else:
        missing_markets.append("BTTS")

    # Home team over 0.5 goals
    home_scored = valid([m["goals_scored"] for m in home_matches])
    if home_scored:
        hits = sum(1 for g in home_scored if g > 0)
        picks.append({
            "category": "GOALS",
            "market": "Home Team Over 0.5 Goals",
            "selection": "Yes",
            "hits": hits,
            "total": len(home_scored),
            "reason": f"Home team scored in {hits}/{len(home_scored)} of their last 10",
        })

    # Away team over 0.5 goals
    away_scored = valid([m["goals_scored"] for m in away_matches])
    if away_scored:
        hits = sum(1 for g in away_scored if g > 0)
        picks.append({
            "category": "GOALS",
            "market": "Away Team Over 0.5 Goals",
            "selection": "Yes",
            "hits": hits,
            "total": len(away_scored),
            "reason": f"Away team scored in {hits}/{len(away_scored)} of their last 10",
        })

    # Home clean sheet
    home_conceded = valid([m["goals_conceded"] for m in home_matches])
    if home_conceded:
        hits = sum(1 for g in home_conceded if g == 0)
        picks.append({
            "category": "GOALS",
            "market": "Home Team Clean Sheet",
            "selection": "Yes",
            "hits": hits,
            "total": len(home_conceded),
            "reason": f"Home team kept {hits} clean sheets in last {len(home_conceded)}",
        })

    # Away clean sheet
    away_conceded = valid([m["goals_conceded"] for m in away_matches])
    if away_conceded:
        hits = sum(1 for g in away_conceded if g == 0)
        picks.append({
            "category": "GOALS",
            "market": "Away Team Clean Sheet",
            "selection": "Yes",
            "hits": hits,
            "total": len(away_conceded),
            "reason": f"Away team kept {hits} clean sheets in last {len(away_conceded)}",
        })

    # ---- SHOTS ----
    home_shots_list = valid([m["shots"] for m in home_matches])
    away_shots_list = valid([m["shots"] for m in away_matches])
    home_sot_list = valid([m["sot"] for m in home_matches])
    away_sot_list = valid([m["sot"] for m in away_matches])
    match_shots_list = valid([m["match_shots"] for m in home_matches]) + \
                       valid([m["match_shots"] for m in away_matches])

    if home_shots_list:
        line = sum(home_shots_list) / len(home_shots_list)
        hits = sum(1 for s in home_shots_list if s > line)
        picks.append({
            "category": "SHOTS",
            "market": f"Home Team Over {line:.1f} Shots",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(home_shots_list),
            "reason": f"Line set at their last-10 average of {line:.1f} shots",
        })

    if away_shots_list:
        line = sum(away_shots_list) / len(away_shots_list)
        hits = sum(1 for s in away_shots_list if s > line)
        picks.append({
            "category": "SHOTS",
            "market": f"Away Team Over {line:.1f} Shots",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(away_shots_list),
            "reason": f"Line set at their last-10 average of {line:.1f} shots",
        })

    if home_sot_list:
        line = sum(home_sot_list) / len(home_sot_list)
        hits = sum(1 for s in home_sot_list if s > line)
        picks.append({
            "category": "SHOTS",
            "market": f"Home Team Over {line:.1f} SOT",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(home_sot_list),
            "reason": f"Line set at their last-10 average of {line:.1f} SOT",
        })

    if away_sot_list:
        line = sum(away_sot_list) / len(away_sot_list)
        hits = sum(1 for s in away_sot_list if s > line)
        picks.append({
            "category": "SHOTS",
            "market": f"Away Team Over {line:.1f} SOT",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(away_sot_list),
            "reason": f"Line set at their last-10 average of {line:.1f} SOT",
        })

    if match_shots_list:
        line = sum(match_shots_list) / len(match_shots_list)
        hits = sum(1 for s in match_shots_list if s > line)
        picks.append({
            "category": "SHOTS",
            "market": f"Combined Match Shots Over {line:.1f}",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(match_shots_list),
            "reason": f"Combined avg {line:.1f} total shots per match",
        })

    if not home_shots_list and not away_shots_list:
        missing_markets.append("Shots")

    # ---- FOULS & CARDS ----
    home_fouls_list = valid([m["fouls"] for m in home_matches])
    away_fouls_list = valid([m["fouls"] for m in away_matches])
    match_fouls_list = valid([m["match_fouls"] for m in home_matches]) + \
                       valid([m["match_fouls"] for m in away_matches])
    home_yellows_list = valid([m["yellows"] for m in home_matches])
    away_yellows_list = valid([m["yellows"] for m in away_matches])
    match_yellows_list = valid([m["match_yellows"] for m in home_matches]) + \
                         valid([m["match_yellows"] for m in away_matches])
    match_reds_list = valid([m["match_reds"] for m in home_matches]) + \
                      valid([m["match_reds"] for m in away_matches])

    if match_fouls_list:
        line = sum(match_fouls_list) / len(match_fouls_list)
        hits = sum(1 for f in match_fouls_list if f > line)
        picks.append({
            "category": "FOULS & CARDS",
            "market": f"Match Total Fouls Over {line:.1f}",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(match_fouls_list),
            "reason": f"Combined avg {line:.1f} fouls per match",
        })
    elif not home_fouls_list and not away_fouls_list:
        missing_markets.append("Fouls")

    if match_yellows_list:
        line = sum(match_yellows_list) / len(match_yellows_list)
        hits = sum(1 for y in match_yellows_list if y > line)
        picks.append({
            "category": "FOULS & CARDS",
            "market": f"Match Total Yellows Over {line:.1f}",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(match_yellows_list),
            "reason": f"Combined avg {line:.1f} yellows per match",
        })
    elif not home_yellows_list and not away_yellows_list:
        missing_markets.append("Yellow cards")

    if match_reds_list:
        hits = sum(1 for r in match_reds_list if r > 0)
        picks.append({
            "category": "FOULS & CARDS",
            "market": "At Least 1 Red Card",
            "selection": "Yes",
            "hits": hits,
            "total": len(match_reds_list),
            "reason": f"{hits}/{len(match_reds_list)} games had at least 1 red card",
        })
    else:
        missing_markets.append("Red cards")

    # ---- CORNERS ----
    home_corners_list = valid([m["corners"] for m in home_matches])
    away_corners_list = valid([m["corners"] for m in away_matches])
    match_corners_list = valid([m["match_corners"] for m in home_matches]) + \
                         valid([m["match_corners"] for m in away_matches])

    # Fixed-threshold team corner markets
    for label, clist, team in [("Home Team", home_corners_list, "home"),
                                ("Away Team", away_corners_list, "away")]:
        if clist:
            c_avg = sum(clist) / len(clist)
            for thresh in [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]:
                hits = sum(1 for c in clist if c > thresh)
                pct = (hits / len(clist) * 100) if clist else 0
                if pct >= 30:  # only include if meaningful
                    picks.append({
                        "category": "CORNERS",
                        "market": f"{label} Over {thresh:.1f} Corners",
                        "selection": f"Over {thresh:.1f}",
                        "hits": hits,
                        "total": len(clist),
                        "reason": f"{label} avg {c_avg:.1f} corners/game (last {len(clist)})",
                    })

    # Match total corner markets
    if match_corners_list:
        mc_avg = sum(match_corners_list) / len(match_corners_list)
        for thresh in [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]:
            hits = sum(1 for c in match_corners_list if c > thresh)
            pct = (hits / len(match_corners_list) * 100)
            if pct >= 30:
                picks.append({
                    "category": "CORNERS",
                    "market": f"Match Total Over {thresh:.1f} Corners",
                    "selection": f"Over {thresh:.1f}",
                    "hits": hits,
                    "total": len(match_corners_list),
                    "reason": f"Combined avg {mc_avg:.1f} corners/match",
                })

    # Half-time corner markets
    home_c1h = valid([m["corners_1h"] for m in home_matches])
    away_c1h = valid([m["corners_1h"] for m in away_matches])
    home_c2h = valid([m["corners_2h"] for m in home_matches])
    away_c2h = valid([m["corners_2h"] for m in away_matches])

    for label, c1h, c2h in [("Home Team", home_c1h, home_c2h),
                              ("Away Team", away_c1h, away_c2h)]:
        if c1h:
            c1h_avg = sum(c1h) / len(c1h)
            for thresh in [1.5, 2.5, 3.5, 4.5]:
                hits = sum(1 for c in c1h if c > thresh)
                pct = (hits / len(c1h) * 100)
                if pct >= 30:
                    picks.append({
                        "category": "CORNERS",
                        "market": f"{label} 1st Half Over {thresh:.1f} Corners",
                        "selection": f"Over {thresh:.1f}",
                        "hits": hits,
                        "total": len(c1h),
                        "reason": f"{label} avg {c1h_avg:.1f} corners in 1st half",
                    })
        if c2h:
            c2h_avg = sum(c2h) / len(c2h)
            for thresh in [1.5, 2.5, 3.5, 4.5]:
                hits = sum(1 for c in c2h if c > thresh)
                pct = (hits / len(c2h) * 100)
                if pct >= 30:
                    picks.append({
                        "category": "CORNERS",
                        "market": f"{label} 2nd Half Over {thresh:.1f} Corners",
                        "selection": f"Over {thresh:.1f}",
                        "hits": hits,
                        "total": len(c2h),
                        "reason": f"{label} avg {c2h_avg:.1f} corners in 2nd half",
                    })

    if not home_corners_list and not away_corners_list:
        missing_markets.append("Corners")

    # ---- HALF-TIME / SECOND-HALF GOALS ----
    all_1h = valid([m["total_goals_1h"] for m in home_matches]) + \
             valid([m["total_goals_1h"] for m in away_matches])
    all_2h = valid([m["total_goals_2h"] for m in home_matches]) + \
             valid([m["total_goals_2h"] for m in away_matches])

    if all_1h:
        avg_1h = sum(all_1h) / len(all_1h)
        for thresh in [0.5, 1.5, 2.5]:
            hits = sum(1 for g in all_1h if g > thresh)
            picks.append({
                "category": "HALF-TIME GOALS",
                "market": f"1st Half Over {thresh:.1f} Goals",
                "selection": f"Over {thresh:.1f}",
                "hits": hits,
                "total": len(all_1h),
                "reason": f"Avg {avg_1h:.1f} goals in 1st half across both teams' games",
            })

    if all_2h:
        avg_2h = sum(all_2h) / len(all_2h)
        for thresh in [0.5, 1.5, 2.5]:
            hits = sum(1 for g in all_2h if g > thresh)
            picks.append({
                "category": "HALF-TIME GOALS",
                "market": f"2nd Half Over {thresh:.1f} Goals",
                "selection": f"Over {thresh:.1f}",
                "hits": hits,
                "total": len(all_2h),
                "reason": f"Avg {avg_2h:.1f} goals in 2nd half across both teams' games",
            })

    # ---- FIRST HALF SHOTS ----
    all_sh_1h = valid([m["match_shots_1h"] for m in home_matches]) + \
                valid([m["match_shots_1h"] for m in away_matches])
    all_sh_2h = valid([m["match_shots_2h"] for m in home_matches]) + \
                valid([m["match_shots_2h"] for m in away_matches])

    if all_sh_1h:
        avg_sh1 = sum(all_sh_1h) / len(all_sh_1h)
        hits = sum(1 for s in all_sh_1h if s > avg_sh1)
        picks.append({
            "category": "SHOTS",
            "market": f"1st Half Match Shots Over {avg_sh1:.1f}",
            "selection": f"Over {avg_sh1:.1f}",
            "hits": hits,
            "total": len(all_sh_1h),
            "reason": f"Avg {avg_sh1:.1f} combined shots in 1st half",
        })
    if all_sh_2h:
        avg_sh2 = sum(all_sh_2h) / len(all_sh_2h)
        hits = sum(1 for s in all_sh_2h if s > avg_sh2)
        picks.append({
            "category": "SHOTS",
            "market": f"2nd Half Match Shots Over {avg_sh2:.1f}",
            "selection": f"Over {avg_sh2:.1f}",
            "hits": hits,
            "total": len(all_sh_2h),
            "reason": f"Avg {avg_sh2:.1f} combined shots in 2nd half",
        })

    # ---- MATCH RESULT ----
    home_results = [(m["goals_scored"], m["goals_conceded"])
                    for m in home_matches if m["goals_scored"] >= 0 and m["goals_conceded"] >= 0]
    away_results = [(m["goals_scored"], m["goals_conceded"])
                    for m in away_matches if m["goals_scored"] >= 0 and m["goals_conceded"] >= 0]

    if home_results and away_results:
        home_wins = sum(1 for s, c in home_results if s > c)
        home_draws = sum(1 for s, c in home_results if s == c)
        away_wins = sum(1 for s, c in away_results if s > c)
        away_draws = sum(1 for s, c in away_results if s == c)
        away_losses = sum(1 for s, c in away_results if s < c)

        n_home = len(home_results)
        n_away = len(away_results)

        # Estimate probabilities from form
        home_win_pct = ((home_wins / n_home) + (away_losses / n_away)) / 2
        draw_pct = ((home_draws / n_home) + (away_draws / n_away)) / 2
        away_win_pct = 1.0 - home_win_pct - draw_pct
        # Clamp
        away_win_pct = max(0.0, away_win_pct)

        picks.append({
            "category": "MATCH RESULT",
            "market": "Home Win",
            "selection": "Home Win",
            "hits": int(round(home_win_pct * 10)),
            "total": 10,
            "reason": f"Home win {home_wins}/{n_home}, Away loss {away_losses}/{n_away}",
        })
        picks.append({
            "category": "MATCH RESULT",
            "market": "Draw",
            "selection": "Draw",
            "hits": int(round(draw_pct * 10)),
            "total": 10,
            "reason": f"Home draw {home_draws}/{n_home}, Away draw {away_draws}/{n_away}",
        })
        picks.append({
            "category": "MATCH RESULT",
            "market": "Away Win",
            "selection": "Away Win",
            "hits": int(round(away_win_pct * 10)),
            "total": 10,
            "reason": f"Away win {away_wins}/{n_away}, Home loss {n_home - home_wins - home_draws}/{n_home}",
        })

    return picks, missing_markets


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_report(
    match_info: dict,
    home_matches: list[dict],
    away_matches: list[dict],
    home_ctx: dict,
    away_ctx: dict,
    picks: list[dict],
    missing_markets: list[str],
    home_avgs: dict,
    away_avgs: dict,
    home_players: list[dict] | None = None,
    away_players: list[dict] | None = None,
):
    home_players = home_players or []
    away_players = away_players or []
    home_name = match_info.get("home_name", "Home")
    away_name = match_info.get("away_name", "Away")
    league = match_info.get("league_name", match_info.get("competition_name", "Unknown League"))
    # Try to get season/league name from nested data
    if league == "Unknown League":
        league = match_info.get("competition", {}).get("name", "Unknown League") if isinstance(match_info.get("competition"), dict) else league

    kick_off_unix = match_info.get("date_unix", 0)
    if kick_off_unix:
        kick_off = datetime.fromtimestamp(int(kick_off_unix), tz=timezone.utc).strftime("%H:%M UTC")
    else:
        kick_off = match_info.get("time", match_info.get("ko_time", "TBD"))

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  MATCH: {home_name} vs {away_name} | {league} | {kick_off}")
    print(sep)

    # Contextual analysis summary
    print(f"\n--- CONTEXTUAL ANALYSIS ---")
    for team_name, ctx, avgs in [(home_name, home_ctx, home_avgs), (away_name, away_ctx, away_avgs)]:
        print(f"\n  {team_name}:")
        if ctx["vs_top"]:
            top_avgs = compute_team_averages(ctx["vs_top"])
            print(f"    vs Top Half ({len(ctx['vs_top'])} games): {top_avgs['avg_goals_scored']:.1f} GF, {top_avgs['avg_goals_conceded']:.1f} GA")
        if ctx["vs_bottom"]:
            bot_avgs = compute_team_averages(ctx["vs_bottom"])
            print(f"    vs Bottom Half ({len(ctx['vs_bottom'])} games): {bot_avgs['avg_goals_scored']:.1f} GF, {bot_avgs['avg_goals_conceded']:.1f} GA")
        if ctx["home_form"]:
            hf_avgs = compute_team_averages(ctx["home_form"])
            print(f"    Home Form (last {len(ctx['home_form'])}): {hf_avgs['avg_goals_scored']:.1f} GF, {hf_avgs['avg_goals_conceded']:.1f} GA")
        if ctx["away_form"]:
            af_avgs = compute_team_averages(ctx["away_form"])
            print(f"    Away Form (last {len(ctx['away_form'])}): {af_avgs['avg_goals_scored']:.1f} GF, {af_avgs['avg_goals_conceded']:.1f} GA")

    # Sort picks by hit rate descending
    rated_picks = []
    for p in picks:
        pct = (p["hits"] / p["total"] * 100) if p["total"] > 0 else 0
        rated_picks.append({**p, "pct": pct})
    rated_picks.sort(key=lambda x: x["pct"], reverse=True)

    # 90%+ picks
    tier90 = [p for p in rated_picks if p["pct"] >= 90]
    tier80 = [p for p in rated_picks if 80 <= p["pct"] < 90]

    if tier90:
        print(f"\n--- 90%+ LIKELY PICKS ---")
        for p in tier90:
            print(f"  {p['category']:16s} | {p['market']:35s} | {p['hits']}/{p['total']} | {p['reason']}")

    if tier80:
        print(f"\n--- 80-89% LIKELY PICKS ---")
        for p in tier80:
            print(f"  {p['category']:16s} | {p['market']:35s} | {p['hits']}/{p['total']} | {p['reason']}")

    if not tier90 and not tier80:
        print(f"\n  No picks hit 80%+ threshold for this match.")
        print(f"\n--- TOP PICKS (below 80%) ---")
        for p in rated_picks[:8]:
            print(f"  {p['category']:16s} | {p['market']:35s} | {p['hits']}/{p['total']} ({p['pct']:.0f}%) | {p['reason']}")

    # Player stats summary
    for team_label, players in [(home_name, home_players), (away_name, away_players)]:
        notable = [p for p in players if p["shots_per_game"] >= 1.0 or p["fouls_per_game"] >= 1.0]
        if notable:
            print(f"\n--- PLAYER STATS: {team_label} ---")
            print(f"  {'Player':22s} | {'Pos':4s} | {'Apps':4s} | {'Shots/G':7s} | {'SOT/G':6s} | {'Fouls/G':7s}")
            for p in notable[:10]:
                print(f"  {p['name']:22s} | {p['position']:4s} | {p['appearances']:4d} | {p['shots_per_game']:7.1f} | {p['sot_per_game']:6.1f} | {p['fouls_per_game']:7.1f}")

    # Data summary
    print(f"\n--- DATA SUMMARY ---")

    def fmt_avg(label, avgs, n):
        parts = [f"Avg GF {avgs['avg_goals_scored']:.1f}"]
        if avgs.get("avg_goals_scored_1h", 0) > 0 or avgs.get("avg_goals_scored_2h", 0) > 0:
            parts.append(f"GF 1H/2H {avgs['avg_goals_scored_1h']:.1f}/{avgs['avg_goals_scored_2h']:.1f}")
        if avgs["avg_shots"] > 0:
            parts.append(f"Shots {avgs['avg_shots']:.1f}")
        if avgs.get("avg_shots_1h", 0) > 0 or avgs.get("avg_shots_2h", 0) > 0:
            parts.append(f"Shots 1H/2H {avgs['avg_shots_1h']:.1f}/{avgs['avg_shots_2h']:.1f}")
        if avgs["avg_sot"] > 0:
            parts.append(f"SOT {avgs['avg_sot']:.1f}")
        if avgs["avg_fouls"] > 0:
            parts.append(f"Fouls {avgs['avg_fouls']:.1f}")
        if avgs["avg_yellows"] > 0:
            parts.append(f"Yellows {avgs['avg_yellows']:.1f}")
        if avgs["avg_corners"] > 0:
            parts.append(f"Corners {avgs['avg_corners']:.1f}")
        if avgs.get("avg_corners_1h", 0) > 0 or avgs.get("avg_corners_2h", 0) > 0:
            parts.append(f"Corners 1H/2H {avgs['avg_corners_1h']:.1f}/{avgs['avg_corners_2h']:.1f}")
        print(f"  {label} Last {n}: {' | '.join(parts)}")

    fmt_avg(home_name, home_avgs, len(home_matches))
    fmt_avg(away_name, away_avgs, len(away_matches))

    combined_goals = valid([m["total_goals"] for m in home_matches]) + \
                     valid([m["total_goals"] for m in away_matches])
    if combined_goals:
        print(f"  Combined avg goals per match: {sum(combined_goals)/len(combined_goals):.2f}")

    print(f"  API credits used: {api_credits_used}")

    if missing_markets:
        print(f"\n  Note: Data unavailable for: {', '.join(missing_markets)}")

    print()


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------

def generate_html_report(
    match_info: dict,
    home_matches: list[dict],
    away_matches: list[dict],
    home_ctx: dict,
    away_ctx: dict,
    picks: list[dict],
    missing_markets: list[str],
    home_avgs: dict,
    away_avgs: dict,
    home_players: list[dict] | None = None,
    away_players: list[dict] | None = None,
    home_profile: dict | None = None,
    away_profile: dict | None = None,
    matchup_text: str = "",
    write_file: bool = True,
) -> str:
    """Generate a styled HTML report. Returns file path (write_file=True) or HTML string."""
    import html as html_mod
    import tempfile
    import webbrowser

    home_players = home_players or []
    away_players = away_players or []
    home_profile = home_profile or {}
    away_profile = away_profile or {}

    home_name = html_mod.escape(match_info.get("home_name", "Home"))
    away_name = html_mod.escape(match_info.get("away_name", "Away"))
    league = html_mod.escape(match_info.get("league_name", match_info.get("competition_name", "Unknown League")))
    if league == "Unknown League":
        league = html_mod.escape(
            match_info.get("competition", {}).get("name", "Unknown League")
            if isinstance(match_info.get("competition"), dict) else "Unknown League"
        )

    kick_off_unix = match_info.get("date_unix", 0)
    if kick_off_unix:
        kick_off = datetime.fromtimestamp(int(kick_off_unix), tz=timezone.utc).strftime("%H:%M UTC")
    else:
        kick_off = match_info.get("time", match_info.get("ko_time", "TBD"))

    # Rate and sort picks
    rated_picks = []
    for p in picks:
        pct = (p["hits"] / p["total"] * 100) if p["total"] > 0 else 0
        rated_picks.append({**p, "pct": pct})
    rated_picks.sort(key=lambda x: x["pct"], reverse=True)

    tier90 = [p for p in rated_picks if p["pct"] >= 90]
    tier80 = [p for p in rated_picks if 80 <= p["pct"] < 90]
    tier70 = [p for p in rated_picks if 70 <= p["pct"] < 80]
    rest = [p for p in rated_picks if p["pct"] < 70]

    def pick_rows(picks_list: list[dict]) -> str:
        rows = ""
        for p in picks_list:
            pct = p["pct"]
            if pct >= 90:
                badge = '<span class="badge badge-hot">90%+</span>'
            elif pct >= 80:
                badge = '<span class="badge badge-warm">80%+</span>'
            elif pct >= 70:
                badge = '<span class="badge badge-mid">70%+</span>'
            else:
                badge = f'<span class="badge badge-low">{pct:.0f}%</span>'
            rows += f"""<tr>
                <td>{html_mod.escape(p['category'])}</td>
                <td><strong>{html_mod.escape(p['market'])}</strong></td>
                <td class="center">{p['hits']}/{p['total']}</td>
                <td class="center">{badge}</td>
                <td>{html_mod.escape(p['reason'])}</td>
            </tr>"""
        return rows

    def ctx_rows(team_name: str, ctx: dict) -> str:
        rows = ""
        if ctx["vs_top"]:
            a = compute_team_averages(ctx["vs_top"])
            rows += f'<tr><td>{html_mod.escape(team_name)}</td><td>vs Top Half</td><td>{len(ctx["vs_top"])}</td><td>{a["avg_goals_scored"]:.1f}</td><td>{a["avg_goals_conceded"]:.1f}</td></tr>'
        if ctx["vs_bottom"]:
            a = compute_team_averages(ctx["vs_bottom"])
            rows += f'<tr><td>{html_mod.escape(team_name)}</td><td>vs Bottom Half</td><td>{len(ctx["vs_bottom"])}</td><td>{a["avg_goals_scored"]:.1f}</td><td>{a["avg_goals_conceded"]:.1f}</td></tr>'
        if ctx["home_form"]:
            a = compute_team_averages(ctx["home_form"])
            rows += f'<tr><td>{html_mod.escape(team_name)}</td><td>Home Form</td><td>{len(ctx["home_form"])}</td><td>{a["avg_goals_scored"]:.1f}</td><td>{a["avg_goals_conceded"]:.1f}</td></tr>'
        if ctx["away_form"]:
            a = compute_team_averages(ctx["away_form"])
            rows += f'<tr><td>{html_mod.escape(team_name)}</td><td>Away Form</td><td>{len(ctx["away_form"])}</td><td>{a["avg_goals_scored"]:.1f}</td><td>{a["avg_goals_conceded"]:.1f}</td></tr>'
        return rows

    def summary_row(label: str, avgs: dict, n: int) -> str:
        cells = [
            f"<td><strong>{html_mod.escape(label)}</strong></td>",
            f"<td>{n}</td>",
            f"<td>{avgs['avg_goals_scored']:.1f}</td>",
            f"<td>{avgs['avg_shots']:.1f}</td>" if avgs["avg_shots"] > 0 else "<td>-</td>",
            f"<td>{avgs['avg_sot']:.1f}</td>" if avgs["avg_sot"] > 0 else "<td>-</td>",
            f"<td>{avgs['avg_fouls']:.1f}</td>" if avgs["avg_fouls"] > 0 else "<td>-</td>",
            f"<td>{avgs['avg_yellows']:.1f}</td>" if avgs["avg_yellows"] > 0 else "<td>-</td>",
            f"<td>{avgs['avg_corners']:.1f}</td>" if avgs["avg_corners"] > 0 else "<td>-</td>",
        ]
        return "<tr>" + "".join(cells) + "</tr>"

    combined_goals = valid([m["total_goals"] for m in home_matches]) + \
                     valid([m["total_goals"] for m in away_matches])
    combined_avg = f"{sum(combined_goals)/len(combined_goals):.2f}" if combined_goals else "-"

    missing_note = ""
    if missing_markets:
        missing_note = f'<p class="missing">Data unavailable for: {html_mod.escape(", ".join(missing_markets))}</p>'

    # Build tier sections
    picks_html = ""
    if tier90:
        picks_html += f'<h2 class="section-title hot">90%+ Likely Picks</h2><table class="picks">'
        picks_html += '<tr><th>Category</th><th>Market</th><th>Hit Rate</th><th>Confidence</th><th>Reason</th></tr>'
        picks_html += pick_rows(tier90) + '</table>'
    if tier80:
        picks_html += f'<h2 class="section-title warm">80-89% Likely Picks</h2><table class="picks">'
        picks_html += '<tr><th>Category</th><th>Market</th><th>Hit Rate</th><th>Confidence</th><th>Reason</th></tr>'
        picks_html += pick_rows(tier80) + '</table>'
    if tier70:
        picks_html += f'<h2 class="section-title mid">70-79% Picks</h2><table class="picks">'
        picks_html += '<tr><th>Category</th><th>Market</th><th>Hit Rate</th><th>Confidence</th><th>Reason</th></tr>'
        picks_html += pick_rows(tier70) + '</table>'
    if not tier90 and not tier80 and not tier70:
        picks_html += f'<h2 class="section-title">Top Picks (below 70%)</h2><table class="picks">'
        picks_html += '<tr><th>Category</th><th>Market</th><th>Hit Rate</th><th>Confidence</th><th>Reason</th></tr>'
        picks_html += pick_rows(rest[:10]) + '</table>'
    elif rest:
        picks_html += f'<details><summary>All Other Picks ({len(rest)})</summary><table class="picks">'
        picks_html += '<tr><th>Category</th><th>Market</th><th>Hit Rate</th><th>Confidence</th><th>Reason</th></tr>'
        picks_html += pick_rows(rest) + '</table></details>'

    # Build player stats section
    def player_table_html(team_label: str, players: list[dict]) -> str:
        notable = [p for p in players if p["shots_per_game"] >= 0.5 or p["fouls_per_game"] >= 0.5]
        if not notable:
            return ""
        rows = ""
        for p in notable[:12]:
            rows += f"""<tr>
                <td>{html_mod.escape(p['name'])}</td>
                <td class="center">{html_mod.escape(p['position'])}</td>
                <td class="center">{p['appearances']}</td>
                <td class="center">{p['shots_per_game']:.1f}</td>
                <td class="center">{p['sot_per_game']:.1f}</td>
                <td class="center">{p['fouls_per_game']:.1f}</td>
            </tr>"""
        return f"""<div class="card">
            <h3>{html_mod.escape(team_label)}</h3>
            <table>
                <tr><th>Player</th><th>Pos</th><th>Apps</th><th>Shots/G</th><th>SOT/G</th><th>Fouls/G</th></tr>
                {rows}
            </table>
        </div>"""

    player_stats_html = ""
    if home_players or away_players:
        player_stats_html = f'<h2 class="section-title">Player Stats (Fouls, Shots, SOT)</h2><div class="grid">'
        player_stats_html += player_table_html(home_name, home_players)
        player_stats_html += player_table_html(away_name, away_players)
        player_stats_html += '</div>'

    # Build Aggression Profile & Half-by-Half section
    aggression_html = ""
    if home_profile and away_profile and (home_profile.get("style") or away_profile.get("style")):
        def _half_row(label: str, prof: dict) -> str:
            return f"""<tr>
                <td><strong>{html_mod.escape(label)}</strong></td>
                <td class="center">{html_mod.escape(prof.get('style', '-'))}</td>
                <td class="center">{html_mod.escape(prof.get('tempo', '-'))}</td>
                <td class="center">{prof.get('avg_goals_scored_1h', 0):.2f}</td>
                <td class="center">{prof.get('avg_goals_scored_2h', 0):.2f}</td>
                <td class="center">{prof.get('avg_goals_conceded_1h', 0):.2f}</td>
                <td class="center">{prof.get('avg_goals_conceded_2h', 0):.2f}</td>
                <td class="center">{prof.get('avg_shots_1h', 0):.1f}</td>
                <td class="center">{prof.get('avg_shots_2h', 0):.1f}</td>
                <td class="center">{prof.get('avg_corners_1h', 0):.1f}</td>
                <td class="center">{prof.get('avg_corners_2h', 0):.1f}</td>
            </tr>"""

        matchup_para = ""
        if matchup_text:
            matchup_para = f'<div class="card" style="margin-bottom:16px;padding:16px;border-left:3px solid #5ba3d9;"><p style="line-height:1.6;">{html_mod.escape(matchup_text)}</p></div>'

        aggression_html = f"""<h2 class="section-title">Aggression Profile &amp; Half-by-Half Analysis</h2>
{matchup_para}
<table>
    <tr>
        <th>Team</th><th>Style</th><th>Tempo</th>
        <th>GF 1H</th><th>GF 2H</th><th>GA 1H</th><th>GA 2H</th>
        <th>Shots 1H</th><th>Shots 2H</th><th>Corners 1H</th><th>Corners 2H</th>
    </tr>
    {_half_row(home_name, home_profile)}
    {_half_row(away_name, away_profile)}
</table>"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{home_name} vs {away_name} | Match Analysis</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f1923;
        color: #e0e6ed;
        padding: 24px;
        max-width: 1100px;
        margin: 0 auto;
    }}
    .header {{
        background: linear-gradient(135deg, #1a2a3a, #243447);
        border-radius: 12px;
        padding: 32px;
        margin-bottom: 24px;
        text-align: center;
        border: 1px solid #2d4a5e;
    }}
    .header h1 {{
        font-size: 28px;
        color: #fff;
        margin-bottom: 8px;
    }}
    .header .vs {{ color: #5ba3d9; font-weight: 400; }}
    .header .meta {{
        color: #8a9bb0;
        font-size: 14px;
        margin-top: 4px;
    }}
    .section-title {{
        font-size: 18px;
        margin: 28px 0 12px;
        padding: 10px 16px;
        border-radius: 8px;
        background: #1a2a3a;
        border-left: 4px solid #5ba3d9;
    }}
    .section-title.hot {{ border-left-color: #22c55e; color: #22c55e; }}
    .section-title.warm {{ border-left-color: #eab308; color: #eab308; }}
    .section-title.mid {{ border-left-color: #f97316; color: #f97316; }}
    table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 16px;
        background: #162029;
        border-radius: 8px;
        overflow: hidden;
    }}
    th {{
        background: #1a2a3a;
        color: #8a9bb0;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 10px 14px;
        text-align: left;
    }}
    td {{
        padding: 10px 14px;
        border-top: 1px solid #1e3040;
        font-size: 14px;
    }}
    tr:hover td {{ background: #1a2a3a; }}
    .center {{ text-align: center; }}
    .badge {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 600;
    }}
    .badge-hot {{ background: #22c55e22; color: #22c55e; border: 1px solid #22c55e44; }}
    .badge-warm {{ background: #eab30822; color: #eab308; border: 1px solid #eab30844; }}
    .badge-mid {{ background: #f9731622; color: #f97316; border: 1px solid #f9731644; }}
    .badge-low {{ background: #64748b22; color: #94a3b8; border: 1px solid #64748b44; }}
    details {{
        margin-top: 16px;
    }}
    summary {{
        cursor: pointer;
        color: #5ba3d9;
        font-size: 14px;
        padding: 8px 0;
    }}
    .grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        margin-bottom: 24px;
    }}
    .card {{
        background: #162029;
        border-radius: 8px;
        padding: 20px;
        border: 1px solid #1e3040;
    }}
    .card h3 {{
        font-size: 14px;
        color: #5ba3d9;
        margin-bottom: 12px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    .stat-row {{
        display: flex;
        justify-content: space-between;
        padding: 4px 0;
        font-size: 14px;
    }}
    .stat-label {{ color: #8a9bb0; }}
    .stat-value {{ font-weight: 600; }}
    .footer {{
        text-align: center;
        color: #4a5e73;
        font-size: 12px;
        margin-top: 32px;
        padding-top: 16px;
        border-top: 1px solid #1e3040;
    }}
    .missing {{
        color: #f97316;
        font-size: 13px;
        margin-top: 12px;
        font-style: italic;
    }}
    @media (max-width: 700px) {{
        .grid {{ grid-template-columns: 1fr; }}
        body {{ padding: 12px; }}
    }}
</style>
</head>
<body>

<div class="header">
    <h1>{home_name} <span class="vs">vs</span> {away_name}</h1>
    <div class="meta">{league} &middot; {kick_off}</div>
</div>

{picks_html}

<h2 class="section-title">Contextual Analysis</h2>
<table>
    <tr><th>Team</th><th>Context</th><th>Games</th><th>Avg GF</th><th>Avg GA</th></tr>
    {ctx_rows(home_name, home_ctx)}
    {ctx_rows(away_name, away_ctx)}
</table>

{aggression_html}

{player_stats_html}

<h2 class="section-title">Data Summary</h2>
<div class="grid">
    <div class="card">
        <h3>{home_name} (Last {len(home_matches)})</h3>
        <div class="stat-row"><span class="stat-label">Avg Goals</span><span class="stat-value">{home_avgs['avg_goals_scored']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Goals 1H / 2H</span><span class="stat-value">{home_avgs.get('avg_goals_scored_1h', 0):.1f} / {home_avgs.get('avg_goals_scored_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Conceded 1H / 2H</span><span class="stat-value">{home_avgs.get('avg_goals_conceded_1h', 0):.1f} / {home_avgs.get('avg_goals_conceded_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Shots</span><span class="stat-value">{home_avgs['avg_shots']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Shots 1H / 2H</span><span class="stat-value">{home_avgs.get('avg_shots_1h', 0):.1f} / {home_avgs.get('avg_shots_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg SOT</span><span class="stat-value">{home_avgs['avg_sot']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Corners</span><span class="stat-value">{home_avgs['avg_corners']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Corners 1H / 2H</span><span class="stat-value">{home_avgs.get('avg_corners_1h', 0):.1f} / {home_avgs.get('avg_corners_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Fouls</span><span class="stat-value">{home_avgs['avg_fouls']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Yellows</span><span class="stat-value">{home_avgs['avg_yellows']:.1f}</span></div>
    </div>
    <div class="card">
        <h3>{away_name} (Last {len(away_matches)})</h3>
        <div class="stat-row"><span class="stat-label">Avg Goals</span><span class="stat-value">{away_avgs['avg_goals_scored']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Goals 1H / 2H</span><span class="stat-value">{away_avgs.get('avg_goals_scored_1h', 0):.1f} / {away_avgs.get('avg_goals_scored_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Conceded 1H / 2H</span><span class="stat-value">{away_avgs.get('avg_goals_conceded_1h', 0):.1f} / {away_avgs.get('avg_goals_conceded_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Shots</span><span class="stat-value">{away_avgs['avg_shots']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Shots 1H / 2H</span><span class="stat-value">{away_avgs.get('avg_shots_1h', 0):.1f} / {away_avgs.get('avg_shots_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg SOT</span><span class="stat-value">{away_avgs['avg_sot']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Corners</span><span class="stat-value">{away_avgs['avg_corners']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Corners 1H / 2H</span><span class="stat-value">{away_avgs.get('avg_corners_1h', 0):.1f} / {away_avgs.get('avg_corners_2h', 0):.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Fouls</span><span class="stat-value">{away_avgs['avg_fouls']:.1f}</span></div>
        <div class="stat-row"><span class="stat-label">Avg Yellows</span><span class="stat-value">{away_avgs['avg_yellows']:.1f}</span></div>
    </div>
</div>

<p style="text-align:center; color:#8a9bb0; font-size:14px;">
    Combined avg goals per match: <strong>{combined_avg}</strong>
</p>

{missing_note}

<div class="footer">
    Generated by Football Analysis Tool &middot; FootyStats API &middot; {api_credits_used} API credits used
</div>

</body>
</html>"""

    if not write_file:
        return page

    # Write to a temp file and open in browser
    report_dir = os.path.dirname(os.path.abspath(__file__))
    safe_home = home_name.replace(" ", "_").lower()
    safe_away = away_name.replace(" ", "_").lower()
    filepath = os.path.join(report_dir, f"report_{safe_home}_vs_{safe_away}.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(page)

    webbrowser.open(f"file://{filepath}")
    return filepath


# ---------------------------------------------------------------------------
# List fixtures mode
# ---------------------------------------------------------------------------

def list_fixtures(date_str: str | None = None):
    """Print all fixtures for a given date."""
    fixtures = fetch_todays_matches(date_str)
    if not fixtures:
        print("No fixtures found for this date.")
        return

    date_label = date_str or "today"
    print(f"\nFixtures for {date_label} ({len(fixtures)} matches):\n")
    for i, fix in enumerate(fixtures, 1):
        home = fix.get("home_name", "?")
        away = fix.get("away_name", "?")
        league = fix.get("league_name", fix.get("competition_name", ""))
        ko_unix = fix.get("date_unix", 0)
        if ko_unix:
            ko = datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M")
        else:
            ko = fix.get("time", "TBD")
        status = fix.get("status", "")
        score = ""
        if status == "complete":
            hg = fix.get("homeGoalCount", "?")
            ag = fix.get("awayGoalCount", "?")
            score = f" [{hg}-{ag}]"
        print(f"  {i:3d}. {home:25s} vs {away:25s} | {league:30s} | {ko}{score}")

    print(f"\n  API credits used: {api_credits_used}\n")


# ---------------------------------------------------------------------------
# Reusable analysis entry point (for web app / programmatic use)
# ---------------------------------------------------------------------------

def run_analysis(fixture: dict) -> dict:
    """
    Run full analysis on a single fixture dict (from fetch_todays_matches).
    Returns a dict with:
      - html: full HTML report string
      - home_name, away_name, league, kick_off, kick_off_unix
      - picks: list of picks
      - home_avgs, away_avgs
      - api_credits: credits used for this analysis
      - error: str or None
    """
    global api_credits_used
    credits_before = api_credits_used

    home_name = fixture.get("home_name", "Home")
    away_name = fixture.get("away_name", "Away")
    league = fixture.get("league_name", fixture.get("competition_name", "Unknown"))
    ko_unix = fixture.get("date_unix", 0)
    ko_str = (
        datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC")
        if ko_unix else "TBD"
    )

    result = {
        "home_name": home_name,
        "away_name": away_name,
        "league": league,
        "kick_off": ko_str,
        "kick_off_unix": int(ko_unix) if ko_unix else 0,
        "html": "",
        "picks": [],
        "home_avgs": {},
        "away_avgs": {},
        "api_credits": 0,
        "error": None,
    }

    try:
        home_id = int(fixture.get("homeID", fixture.get("home_id", 0)))
        away_id = int(fixture.get("awayID", fixture.get("away_id", 0)))

        season_id = None
        for key in ("competition_id", "season_id", "league_id", "season"):
            val = fixture.get(key)
            if val is not None:
                try:
                    season_id = int(val)
                    if season_id > 0:
                        break
                except (ValueError, TypeError):
                    continue

        home_last10 = get_team_last10(home_id, season_id)
        away_last10 = get_team_last10(away_id, season_id)

        if not home_last10 and not away_last10:
            result["error"] = "No match history available"
            return result

        pos_map = {}
        total_teams = 20
        if season_id:
            pos_map = build_position_map(season_id)
            if pos_map:
                total_teams = max(pos_map.values())

        home_ctx = contextual_split(home_last10, pos_map, total_teams)
        away_ctx = contextual_split(away_last10, pos_map, total_teams)
        home_avgs = compute_team_averages(home_last10)
        away_avgs = compute_team_averages(away_last10)
        picks, missing_markets = compute_hit_rates(home_last10, away_last10)

        # Aggression profiling
        home_profile = compute_aggression_profile(home_last10)
        away_profile = compute_aggression_profile(away_last10)
        matchup_text = compute_matchup_analysis(home_profile, away_profile, home_name, away_name)

        home_players = []
        away_players = []
        if season_id and season_id > 0:
            home_club_id = resolve_club_team_id(season_id, home_name)
            away_club_id = resolve_club_team_id(season_id, away_name)
            if home_club_id and away_club_id:
                try:
                    home_players = get_team_players(season_id, home_club_id, competition_id=season_id)
                    away_players = get_team_players(season_id, away_club_id, competition_id=season_id)
                except Exception:
                    pass
            player_picks = (
                compute_player_picks(home_players, home_name)
                + compute_player_picks(away_players, away_name)
            )
            picks.extend(player_picks)

        html = generate_html_report(
            fixture, home_last10, away_last10,
            home_ctx, away_ctx,
            picks, missing_markets,
            home_avgs, away_avgs,
            home_players=home_players,
            away_players=away_players,
            home_profile=home_profile,
            away_profile=away_profile,
            matchup_text=matchup_text,
            write_file=False,
        )

        result["html"] = html
        result["picks"] = picks
        result["home_avgs"] = home_avgs
        result["away_avgs"] = away_avgs

    except Exception as e:
        result["error"] = str(e)

    result["api_credits"] = api_credits_used - credits_before
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Football Match Analysis Tool (FootyStats API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n  python football_analysis.py "Arsenal vs Chelsea"\n  python football_analysis.py --list\n  python football_analysis.py --list --date 2026-02-15',
    )
    parser.add_argument("match", nargs="?", help='Match query, e.g. "Arsenal vs Chelsea"')
    parser.add_argument("--list", action="store_true", help="List today's fixtures")
    parser.add_argument("--date", type=str, default=None, help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--timezone", type=str, default="Etc/UTC", help="Timezone (default: Etc/UTC)")
    parser.add_argument("--debug", action="store_true", help="Show raw API responses")

    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    if not API_KEY:
        print("Error: FOOTYSTATS_API_KEY not set. Add it to .env or set the environment variable.")
        sys.exit(1)

    if args.list:
        list_fixtures(args.date)
        return

    if not args.match:
        parser.print_help()
        sys.exit(1)

    # Step 1: Find the match in today's fixtures
    print(f"\nSearching fixtures for: {args.match}")
    fixtures = fetch_todays_matches(args.date, args.timezone)
    if not fixtures:
        print("No fixtures found for this date. Check your FootyStats league settings.")
        print(f"API credits used: {api_credits_used}")
        sys.exit(1)

    match_info = find_match(fixtures, args.match)
    if not match_info:
        print(f"Could not find a match matching '{args.match}' in {len(fixtures)} fixtures.")
        print("Available matches:")
        for fix in fixtures[:20]:
            print(f"  {fix.get('home_name', '?')} vs {fix.get('away_name', '?')}")
        if len(fixtures) > 20:
            print(f"  ... and {len(fixtures) - 20} more. Use --list to see all.")
        print(f"API credits used: {api_credits_used}")
        sys.exit(1)

    home_name = match_info.get("home_name", "Home")
    away_name = match_info.get("away_name", "Away")
    league = match_info.get("league_name", match_info.get("competition_name", "Unknown"))
    ko_unix = match_info.get("date_unix", 0)
    ko_str = datetime.fromtimestamp(int(ko_unix), tz=timezone.utc).strftime("%H:%M UTC") if ko_unix else "TBD"

    print(f"\n  Found: {home_name} vs {away_name}")
    print(f"  League: {league}")
    print(f"  Kick-off: {ko_str}")
    if DEBUG:
        import json
        print(f"  [DEBUG] Match object keys: {list(match_info.keys())}")
        print(f"  [DEBUG] Match object:\n{json.dumps(match_info, indent=2, default=str)[:3000]}")
    print(f"\n  Pulling data...")

    home_id = int(match_info.get("homeID", match_info.get("home_id", 0)))
    away_id = int(match_info.get("awayID", match_info.get("away_id", 0)))
    # Try to get a numeric season/league ID for table lookups and match history
    season_id = None
    for key in ("competition_id", "season_id", "league_id", "season"):
        val = match_info.get(key)
        if val is not None:
            try:
                season_id = int(val)
                if season_id > 0:
                    break
            except (ValueError, TypeError):
                continue
    if DEBUG:
        print(f"  [DEBUG] Using season_id={season_id}")

    if not season_id or season_id <= 0:
        print("  Warning: Could not determine league ID. Match history may be unavailable.")

    # Step 2: Pull last 10 matches for both teams
    print(f"  Fetching last 10 for {home_name}...")
    home_last10 = get_team_last10(home_id, season_id)
    print(f"    Got {len(home_last10)} matches")

    print(f"  Fetching last 10 for {away_name}...")
    away_last10 = get_team_last10(away_id, season_id)
    print(f"    Got {len(away_last10)} matches")

    if not home_last10 and not away_last10:
        print("  Could not retrieve match history for either team.")
        print(f"  API credits used: {api_credits_used}")
        sys.exit(1)

    # Step 3: Contextual analysis (league position split)
    pos_map = {}
    total_teams = 20  # default
    if season_id:
        print(f"  Fetching league table for context...")
        pos_map = build_position_map(season_id)
        if pos_map:
            total_teams = max(pos_map.values())

    home_ctx = contextual_split(home_last10, pos_map, total_teams)
    away_ctx = contextual_split(away_last10, pos_map, total_teams)

    # Step 4: Compute hit rates
    home_avgs = compute_team_averages(home_last10)
    away_avgs = compute_team_averages(away_last10)
    picks, missing_markets = compute_hit_rates(home_last10, away_last10)

    # Step 4a: Aggression profiling
    home_profile = compute_aggression_profile(home_last10)
    away_profile = compute_aggression_profile(away_last10)
    matchup_text = compute_matchup_analysis(home_profile, away_profile, home_name, away_name)
    print(f"\n  Aggression: {home_name} ({home_profile['style']}, {home_profile['tempo']})")
    print(f"  Aggression: {away_name} ({away_profile['style']}, {away_profile['tempo']})")

    # Step 4b: Player stats (fouls, shots, SOT)
    home_players = []
    away_players = []
    if season_id and season_id > 0:
        print(f"  Resolving team IDs for player data...")
        home_club_id = resolve_club_team_id(season_id, home_name)
        away_club_id = resolve_club_team_id(season_id, away_name)
        if home_club_id and away_club_id:
            print(f"  Fetching player stats (top 8 per team)...")
            try:
                home_players = get_team_players(season_id, home_club_id, competition_id=season_id)
                away_players = get_team_players(season_id, away_club_id, competition_id=season_id)
                print(f"    {home_name}: {len(home_players)} players | {away_name}: {len(away_players)} players")
            except Exception as e:
                print(f"    Player stats unavailable: {e}")
        else:
            print(f"  Could not resolve team IDs for player lookup")

        # Compute player-level picks and merge into picks list
        player_picks = (
            compute_player_picks(home_players, home_name)
            + compute_player_picks(away_players, away_name)
        )
        picks.extend(player_picks)
        if DEBUG:
            print(f"  [DEBUG] {len(player_picks)} player picks generated")
    else:
        print("  Skipping player stats (no season_id)")

    # Step 5: Output
    report_args = (
        match_info, home_last10, away_last10,
        home_ctx, away_ctx,
        picks, missing_markets,
        home_avgs, away_avgs,
    )
    report_kwargs = dict(
        home_players=home_players,
        away_players=away_players,
        home_profile=home_profile,
        away_profile=away_profile,
        matchup_text=matchup_text,
    )
    format_report(*report_args, home_players=home_players, away_players=away_players)

    # Step 6: Generate HTML report and open in browser
    filepath = generate_html_report(*report_args, **report_kwargs)
    print(f"  HTML report saved to: {filepath}")
    print(f"  Opening in browser...")


if __name__ == "__main__":
    main()
