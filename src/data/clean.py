"""Turn raw nba_api frames into tidy, analysis-ready tables."""

from __future__ import annotations

import pandas as pd

from src import config

REGULAR_SEASON_GAME_PREFIX = "002"  # NBA game ids: 001 preseason, 002 regular season, 004 playoffs ...

GAME_LOG_COLUMNS: list[str] = [
    "player_id", "name", "team_id", "team", "game_id", "date", "opponent", "is_home",
    *[c.lower() for c in config.STAT_COLUMNS],
]


def parse_minutes(value: object) -> float:
    """Convert a minutes value (``34``, ``34.5`` or ``"34:30"``) to decimal minutes."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return 0.0
        if ":" in value:
            mins, secs = value.split(":", 1)
            return int(mins) + int(secs) / 60
    return float(value)


def clean_game_logs(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the tidy player game-log table from raw LeagueGameLog (player mode) output.

    One row per player per game played. Did-not-play rows (0 minutes) are dropped,
    duplicates removed, stats cast to float, and columns snake_cased.

    Output columns: ``player_id, name, team_id, team, game_id, date, opponent,
    is_home, min, pts, reb, oreb, dreb, ast, stl, blk, tov, fgm, fga, fg3m, fg3a,
    ftm, fta, pf, plus_minus``.
    """
    if raw.empty:
        return pd.DataFrame(columns=GAME_LOG_COLUMNS)

    df = raw.copy()
    df["MIN"] = df["MIN"].map(parse_minutes)
    for col in config.STAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)

    # MATCHUP looks like "BOS vs. NYK" (home) or "BOS @ NYK" (away).
    matchup = df["MATCHUP"].astype(str)
    df["is_home"] = matchup.str.contains(" vs. ", regex=False)
    df["opponent"] = matchup.str.split(r" vs\. | @ ", regex=True).str[-1].str.strip()

    tidy = pd.DataFrame(
        {
            "player_id": df["PLAYER_ID"].astype("int64"),
            "name": df["PLAYER_NAME"].astype(str),
            "team_id": df["TEAM_ID"].astype("int64"),
            "team": df["TEAM_ABBREVIATION"].astype(str),
            "game_id": df["GAME_ID"].astype(str),
            "date": pd.to_datetime(df["GAME_DATE"]).dt.normalize(),
            "opponent": df["opponent"],
            "is_home": df["is_home"].astype(bool),
        }
    )
    for col in config.STAT_COLUMNS:
        tidy[col.lower()] = df[col].values

    tidy = tidy[tidy["min"] > 0]
    tidy = tidy.drop_duplicates(subset=["player_id", "game_id"], keep="last")
    return tidy.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True)[GAME_LOG_COLUMNS]


def clean_schedule(raw_games: pd.DataFrame) -> pd.DataFrame:
    """Tidy the ScheduleLeagueV2 ``SeasonGames`` frame to regular-season games only.

    Output columns: ``game_id, date, week, home_team_id, home_team, away_team_id, away_team``.
    ``date`` is the US-Eastern calendar date (what fantasy platforms use).
    """
    cols = ["game_id", "date", "week", "home_team_id", "home_team", "away_team_id", "away_team"]
    if raw_games.empty:
        return pd.DataFrame(columns=cols)

    g = raw_games.copy()
    g["gameId"] = g["gameId"].astype(str).str.zfill(10)
    g = g[g["gameId"].str.startswith(REGULAR_SEASON_GAME_PREFIX)]
    # Knockout games (e.g. NBA Cup quarter-finals) are listed before the teams are known,
    # with team id 0 and no tricode. Drop them; a later refresh brings them back with teams.
    known = (g["homeTeam_teamId"].fillna(0).astype("int64") != 0) & (g["awayTeam_teamId"].fillna(0).astype("int64") != 0)
    g = g[known & g["homeTeam_teamTricode"].notna() & g["awayTeam_teamTricode"].notna()]
    out = pd.DataFrame(
        {
            "game_id": g["gameId"],
            "date": pd.to_datetime(g["gameDateEst"]).dt.tz_localize(None).dt.normalize(),
            "week": pd.to_numeric(g["weekNumber"], errors="coerce").astype("Int64"),
            "home_team_id": g["homeTeam_teamId"].astype("int64"),
            "home_team": g["homeTeam_teamTricode"].astype(str),
            "away_team_id": g["awayTeam_teamId"].astype("int64"),
            "away_team": g["awayTeam_teamTricode"].astype(str),
        }
    )
    return out.drop_duplicates("game_id").sort_values(["date", "game_id"]).reset_index(drop=True)


def team_games(schedule: pd.DataFrame) -> pd.DataFrame:
    """Explode the schedule to one row per team per game.

    Output columns: ``team_id, team, game_id, date, [week,] opponent, is_home``.
    """
    cols = ["team_id", "team", "game_id", "date", *(["week"] if "week" in schedule else []), "opponent", "is_home"]
    home = schedule.rename(columns={"home_team_id": "team_id", "home_team": "team", "away_team": "opponent"})
    home = home.assign(is_home=True)[cols]
    away = schedule.rename(columns={"away_team_id": "team_id", "away_team": "team", "home_team": "opponent"})
    away = away.assign(is_home=False)[cols]
    return pd.concat([home, away], ignore_index=True).sort_values(["date", "team"]).reset_index(drop=True)


def games_between(
    schedule: pd.DataFrame, start: pd.Timestamp | str, end: pd.Timestamp | str
) -> pd.Series:
    """Number of games each team plays in the inclusive date window ``[start, end]``.

    Returns:
        Series indexed by team abbreviation; teams with no games get 0.
    """
    tg = team_games(schedule)
    start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    window = tg[(tg["date"] >= start) & (tg["date"] <= end)]
    counts = window.groupby("team").size()
    all_teams = sorted(tg["team"].unique())
    return counts.reindex(all_teams, fill_value=0).astype(int)
