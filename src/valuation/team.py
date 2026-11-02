"""Team-level aggregation: category totals, z-sums and category need weights."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from src import config
from src.valuation.zscores import normalize_punt

COUNT_COLS: list[str] = list(config.CAT_COLUMNS.values())
SHOT_COLS: list[str] = [c for pair in config.PCT_COLUMNS.values() for c in pair]


def team_totals(players: pd.DataFrame, ids: Iterable[int], games: pd.Series | None = None) -> pd.Series:
    """Category totals for a group of players.

    Counting stats are summed; FG% and FT% are total makes / total attempts (never an
    average of percentages).

    Args:
        players: Projections indexed by player_id.
        ids: Players on the team.
        games: Optional per-player multiplier (e.g. games this week). Default 1 = per-game totals.

    Returns:
        Series indexed by ``config.CATEGORIES`` followed by the raw makes/attempts columns.
    """
    ids = [i for i in ids if i in players.index]
    sub = players.loc[ids, COUNT_COLS + SHOT_COLS].astype(float)
    if games is not None:
        sub = sub.mul(games.reindex(ids).fillna(0).astype(float), axis=0)
    sums = sub.sum()
    out = {cat: sums[col] for cat, col in config.CAT_COLUMNS.items()}
    for cat, (m, a) in config.PCT_COLUMNS.items():
        out[cat] = sums[m] / sums[a] if sums[a] > 0 else np.nan
    ordered = pd.Series({c: out[c] for c in config.CATEGORIES})
    return pd.concat([ordered, sums[SHOT_COLS]])


def team_category_z(valued: pd.DataFrame, ids: Iterable[int]) -> pd.Series:
    """Sum of each category's per-game z-scores over a roster.

    Because the percentage z-scores are built from additive "impact" (extra makes),
    summing them across players is valid, unlike summing raw percentages.
    A league-average 13-man roster sums to roughly 0 in every category.
    """
    ids = [i for i in ids if i in valued.index]
    return valued.loc[ids, [f"z_{c}" for c in config.CATEGORIES]].sum().rename(lambda c: c[2:])


def category_need_weights(
    team_z: pd.Series, punt: Iterable[str] | None = None, scale: float = config.WAIVER_NEED_SCALE
) -> pd.Series:
    """How much each category is needed, in [0, 2].

    ``w = 2 / (1 + exp(team_z / scale))``: weakest categories approach 2, average is 1,
    dominant categories approach 0. Punted categories get 0.
    """
    w = 2.0 / (1.0 + np.exp(team_z.astype(float) / scale))
    for cat in normalize_punt(punt):
        w[cat] = 0.0
    return w
