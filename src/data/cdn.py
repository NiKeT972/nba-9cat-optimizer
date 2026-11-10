"""Second data source: the NBA's public CDN (cdn.nba.com).

stats.nba.com (behind nba_api) refuses most cloud servers, GitHub Actions included. The
static JSON that NBA.com's own scoreboard uses is served from cdn.nba.com instead:

* ``staticData/scheduleLeagueV2.json`` - the full current-season schedule
* ``liveData/boxscore/boxscore_<gameId>.json`` - the box score of one game

This module converts those into *exactly* the raw frames the stats endpoints return
(ScheduleLeagueV2 ``SeasonGames`` and LeagueGameLog player rows), so everything
downstream (cleaning, projections, the web app) is unchanged. Game logs are updated
incrementally: only finished games missing from the cached file are downloaded.
"""

from __future__ import annotations

import logging
import re

import pandas as pd
import requests

from src import config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nba-9cat-optimizer)",
    "Referer": "https://www.nba.com/",
    "Accept": "application/json",
}
FINAL_STATUS = 3  # gameStatus: 1 scheduled, 2 live, 3 final
_ISO_MIN = re.compile(r"PT(?:(\d+)M)?(?:([\d.]+)S)?")


def _get_json(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=config.API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def iso_minutes(value: str | None) -> float:
    """'PT34M12.00S' -> 34.2 minutes; empty / missing -> 0."""
    if not value:
        return 0.0
    m = _ISO_MIN.fullmatch(str(value).strip())
    if not m:
        return 0.0
    return float(m.group(1) or 0) + float(m.group(2) or 0) / 60


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
def schedule_frame(doc: dict) -> pd.DataFrame:
    """scheduleLeagueV2.json -> rows shaped like ScheduleLeagueV2 ``SeasonGames`` (plus gameStatus)."""
    rows = []
    for day in doc.get("leagueSchedule", {}).get("gameDates", []):
        for g in day.get("games", []):
            home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            rows.append({
                "gameId": str(g.get("gameId", "")),
                "gameDateEst": g.get("gameDateEst"),
                "weekNumber": g.get("weekNumber"),
                "gameStatus": g.get("gameStatus"),
                "homeTeam_teamId": home.get("teamId"),
                "homeTeam_teamTricode": home.get("teamTricode"),
                "awayTeam_teamId": away.get("teamId"),
                "awayTeam_teamTricode": away.get("teamTricode"),
            })
    return pd.DataFrame(rows)


def download_schedule() -> pd.DataFrame:
    """Current-season schedule from the CDN."""
    return schedule_frame(_get_json(config.SCHEDULE_CDN_URL))


# ---------------------------------------------------------------------------
# Box scores -> LeagueGameLog player rows
# ---------------------------------------------------------------------------
STAT_MAP = {
    "PTS": "points", "REB": "reboundsTotal", "OREB": "reboundsOffensive", "DREB": "reboundsDefensive",
    "AST": "assists", "STL": "steals", "BLK": "blocks", "TOV": "turnovers",
    "FGM": "fieldGoalsMade", "FGA": "fieldGoalsAttempted", "FG3M": "threePointersMade",
    "FG3A": "threePointersAttempted", "FTM": "freeThrowsMade", "FTA": "freeThrowsAttempted",
    "PF": "foulsPersonal", "PLUS_MINUS": "plusMinusPoints",
}


def boxscore_rows(doc: dict, game_date: str, season_id: str) -> list[dict]:
    """One box-score JSON -> LeagueGameLog-style rows for every player who played."""
    game = doc.get("game", {})
    gid = str(game.get("gameId", ""))
    home, away = game.get("homeTeam") or {}, game.get("awayTeam") or {}
    rows = []
    for team, opp, is_home in ((home, away, True), (away, home, False)):
        tri, opp_tri = team.get("teamTricode", ""), opp.get("teamTricode", "")
        matchup = f"{tri} vs. {opp_tri}" if is_home else f"{tri} @ {opp_tri}"
        for p in team.get("players", []) or []:
            st = p.get("statistics") or {}
            minutes = iso_minutes(st.get("minutes") or st.get("minutesCalculated"))
            if str(p.get("played", "1")) == "0" or minutes <= 0:
                continue
            name = p.get("name") or f"{p.get('firstName', '')} {p.get('familyName', '')}".strip()
            row = {
                "SEASON_ID": season_id, "PLAYER_ID": int(p["personId"]), "PLAYER_NAME": name,
                "TEAM_ID": int(team.get("teamId", 0)), "TEAM_ABBREVIATION": tri, "TEAM_NAME": tri,
                "GAME_ID": gid, "GAME_DATE": game_date, "MATCHUP": matchup, "WL": None,
                "MIN": round(minutes, 2),
            }
            for col, key in STAT_MAP.items():
                row[col] = float(st.get(key) or 0)
            rows.append(row)
    return rows


def update_game_logs(cached: pd.DataFrame, schedule: pd.DataFrame, today: pd.Timestamp | None = None,
                     fetch=None) -> pd.DataFrame:
    """Append box scores of finished games that are missing from ``cached``.

    Args:
        cached: Raw LeagueGameLog-style frame from the parquet cache.
        schedule: Frame from :func:`schedule_frame` (needs gameId, gameDateEst, gameStatus).
        today: Only games before this ET date are considered (default: today).
        fetch: Injectable ``game_id -> box score JSON`` (tests); defaults to the CDN.

    Returns:
        ``cached`` plus the new rows. Raises if every needed download fails.
    """
    fetch = fetch or (lambda gid: _get_json(config.BOXSCORE_CDN_URL.format(game_id=gid)))
    today = pd.Timestamp(today or pd.Timestamp.now(tz="America/New_York").date()).normalize()
    have = set(cached["GAME_ID"].astype(str).str.zfill(10)) if not cached.empty else set()
    sched = schedule.copy()
    sched["gameId"] = sched["gameId"].astype(str).str.zfill(10)
    sched["date"] = pd.to_datetime(sched["gameDateEst"]).dt.tz_localize(None).dt.normalize()
    todo = sched[sched["gameId"].str.startswith("002") & (sched["gameStatus"] == FINAL_STATUS)
                 & (sched["date"] < today) & ~sched["gameId"].isin(have)]
    season_id = str(cached["SEASON_ID"].iloc[0]) if not cached.empty and "SEASON_ID" in cached else ""
    new_rows, failed = [], []
    for g in todo.itertuples():
        try:
            new_rows += boxscore_rows(fetch(g.gameId), g.date.strftime("%Y-%m-%d"), season_id)
        except Exception as exc:  # noqa: BLE001 - one missing box score shouldn't sink the refresh
            failed.append(g.gameId)
            logger.warning("Box score %s failed: %s", g.gameId, exc)
    if failed and len(failed) == len(todo):
        raise RuntimeError(f"All {len(failed)} box-score downloads from the CDN failed")
    logger.info("CDN: added %d player rows from %d games (%d failed)", len(new_rows), len(todo) - len(failed), len(failed))
    if not new_rows:
        return cached
    return pd.concat([cached, pd.DataFrame(new_rows)], ignore_index=True)
