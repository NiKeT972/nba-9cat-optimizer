"""Availability and recent-form tables built from tidy game logs.

nba_api's stats endpoints carry no official injury feed, so availability is inferred from
box scores: when a player last played and how many of his team's games he has missed since.
"""

from __future__ import annotations

import pandas as pd

from src import config

FORM_STATS: list[str] = ["min", "pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fgm", "fga", "ftm", "fta"]


def form_lines(logs: pd.DataFrame, as_of: pd.Timestamp, days: int = config.FORM_DAYS) -> pd.DataFrame:
    """Per-game averages over ``[as_of - days, as_of)`` for every player who played in that window.

    Returns:
        DataFrame indexed by player_id with ``games`` plus one column per stat in ``FORM_STATS``.
    """
    as_of = pd.Timestamp(as_of).normalize()
    window = logs[(logs["date"] < as_of) & (logs["date"] >= as_of - pd.Timedelta(days=days))]
    if window.empty:
        return pd.DataFrame(columns=["games", *FORM_STATS])
    g = window.groupby("player_id")
    out = g[FORM_STATS].mean()
    out.insert(0, "games", g.size())
    return out


def games_missed(logs: pd.DataFrame, schedule: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Consecutive games each player's (latest) team has played without him, up to ``as_of``.

    0 means he played his team's most recent game. Players whose team has no scheduled games
    after his last appearance also get 0.
    """
    as_of = pd.Timestamp(as_of).normalize()
    played = logs[logs["date"] < as_of]
    last = played.sort_values("date").groupby("player_id").tail(1).set_index("player_id")[["team", "date"]]
    if schedule is None or schedule.empty:
        return pd.Series(0, index=last.index, dtype=int)
    past = schedule[schedule["date"] < as_of]
    team_dates = pd.concat([
        past[["home_team", "date"]].rename(columns={"home_team": "team"}),
        past[["away_team", "date"]].rename(columns={"away_team": "team"}),
    ])
    counts = {}
    by_team = {t: d.sort_values().to_numpy() for t, d in team_dates.groupby("team")["date"]}
    for pid, row in last.iterrows():
        dates = by_team.get(row["team"])
        counts[pid] = 0 if dates is None else int((dates > row["date"].to_datetime64()).sum())
    return pd.Series(counts, dtype=int).reindex(last.index, fill_value=0)


def last_games(logs: pd.DataFrame, as_of: pd.Timestamp, n: int = config.REPORT_LAST_GAMES) -> dict[int, pd.DataFrame]:
    """Each player's ``n`` most recent box scores before ``as_of`` (newest first)."""
    as_of = pd.Timestamp(as_of).normalize()
    recent = logs[logs["date"] < as_of].sort_values("date", ascending=False).groupby("player_id").head(n)
    return {int(pid): g for pid, g in recent.groupby("player_id")}
