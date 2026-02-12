#!/usr/bin/env python3
"""
Football Match Analysis Tool
Connects to the FootyStats API to analyze matches and calculate hit rates
for goals, shots, fouls, cards, corners, and match result markets.

Usage:
    python football_analysis.py "Arsenal vs Chelsea"
    python football_analysis.py "Arsenal vs Chelsea" --date 2026-02-15
    python football_analysis.py --list              # list today's fixtures
"""

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


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(endpoint: str, params: dict | None = None) -> dict | list:
    """Make a GET request to the FootyStats API and return JSON."""
    global api_credits_used
    params = params or {}
    params["key"] = API_KEY
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, timeout=30)
    except requests.exceptions.ProxyError:
        print(f"Error: Connection blocked by proxy. Run this tool from your local machine.")
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        print(f"Error: Could not connect to FootyStats API: {e}")
        sys.exit(1)
    api_credits_used += 1
    if resp.status_code == 403:
        print("Error: API returned 403 Forbidden. Check your API key.")
        sys.exit(1)
    if resp.status_code == 429:
        print("Error: API rate limit exceeded. Wait and try again.")
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()
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


def fetch_league_matches(season_id: int) -> list[dict]:
    """Fetch completed matches for a season (fallback for lastx)."""
    data = api_get("league-matches", {"season_id": season_id, "max_per_page": 500})
    if not isinstance(data, list):
        return []
    return data


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
    Tries /lastx first, falls back to /league-matches.
    """
    try:
        raw = fetch_lastx(team_id)
    except Exception:
        raw = []

    if not raw and season_id:
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
        except Exception:
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
    }


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

    # Corners per team
    home_corners_list = valid([m["corners"] for m in home_matches])
    away_corners_list = valid([m["corners"] for m in away_matches])

    if home_corners_list:
        line = sum(home_corners_list) / len(home_corners_list)
        hits = sum(1 for c in home_corners_list if c > line)
        picks.append({
            "category": "FOULS & CARDS",
            "market": f"Home Team Over {line:.1f} Corners",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(home_corners_list),
            "reason": f"Line set at their last-10 average of {line:.1f} corners",
        })

    if away_corners_list:
        line = sum(away_corners_list) / len(away_corners_list)
        hits = sum(1 for c in away_corners_list if c > line)
        picks.append({
            "category": "FOULS & CARDS",
            "market": f"Away Team Over {line:.1f} Corners",
            "selection": f"Over {line:.1f}",
            "hits": hits,
            "total": len(away_corners_list),
            "reason": f"Line set at their last-10 average of {line:.1f} corners",
        })

    if not home_corners_list and not away_corners_list:
        missing_markets.append("Corners")

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
):
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

    # Data summary
    print(f"\n--- DATA SUMMARY ---")

    def fmt_avg(label, avgs, n):
        parts = [f"Avg GF {avgs['avg_goals_scored']:.1f}"]
        if avgs["avg_shots"] > 0:
            parts.append(f"Shots {avgs['avg_shots']:.1f}")
        if avgs["avg_sot"] > 0:
            parts.append(f"SOT {avgs['avg_sot']:.1f}")
        if avgs["avg_fouls"] > 0:
            parts.append(f"Fouls {avgs['avg_fouls']:.1f}")
        if avgs["avg_yellows"] > 0:
            parts.append(f"Yellows {avgs['avg_yellows']:.1f}")
        if avgs["avg_corners"] > 0:
            parts.append(f"Corners {avgs['avg_corners']:.1f}")
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

    args = parser.parse_args()

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
    print(f"\n  Pulling data...")

    home_id = int(match_info.get("homeID", match_info.get("home_id", 0)))
    away_id = int(match_info.get("awayID", match_info.get("away_id", 0)))
    season_id = match_info.get("season_id") or match_info.get("season")
    if season_id:
        season_id = int(season_id)

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

    # Step 5: Output
    format_report(
        match_info, home_last10, away_last10,
        home_ctx, away_ctx,
        picks, missing_markets,
        home_avgs, away_avgs,
    )


if __name__ == "__main__":
    main()
