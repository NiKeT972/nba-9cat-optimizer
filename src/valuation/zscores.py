"""Z-score player valuation for 9-category leagues.

The maths, in plain words
-------------------------
For each category we ask "how many standard deviations better than a typical
*rosterable* player is this guy?" and add those up.

* **Counting stats** (PTS, REB, AST, STL, BLK, 3PM, TO): ``z = (x - mean) / std``,
  with mean and std taken over the player pool. TO is multiplied by -1 because
  fewer turnovers win the category.
* **Percentages** (FG%, FT%): a 60% shooter on 2 attempts barely moves a fantasy
  team's FG%, while a 55% shooter on 20 attempts moves it a lot. So we z-score the
  *impact* instead of the raw percentage::

      impact = (player_pct - pool_pct) * attempts
             = makes - pool_pct * attempts

  i.e. "extra makes compared with a league-average shooter taking the same shots".
  ``pool_pct`` is total makes / total attempts across the pool.
* **The pool** is the top ``ZSCORE_POOL_SIZE`` players (12 teams x 13 = 156). Who is
  in the top 156 depends on the z-scores, which depend on the pool, so we iterate:
  score everyone against the current pool, take the new top 156, repeat until the
  pool stops changing.
* **Punting** drops chosen categories from the total (and from the pool selection,
  since a punt team would roster different players).

All scores are per game; Phase 4 scales them by games played.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src import config


@dataclass
class PoolStats:
    """Everything needed to put any player (or team) on the pool's z-score scale."""

    mean: pd.Series  # per category, raw value / impact
    std: pd.Series  # per category (population std, ddof=0)
    pool_pct: dict[str, float]  # league FG% / FT% used for the impact calculation
    pool_ids: pd.Index  # player_ids in the final pool
    iterations: int = 0
    punt: tuple[str, ...] = field(default_factory=tuple)


def normalize_punt(punt: Iterable[str] | None) -> tuple[str, ...]:
    """Validate punted categories against ``config.CATEGORIES``."""
    punt = tuple(punt or ())
    unknown = set(punt) - set(config.CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown categories to punt: {sorted(unknown)}. Valid: {config.CATEGORIES}")
    return punt


def pool_percentages(df: pd.DataFrame) -> dict[str, float]:
    """Attempt-weighted FG% and FT% of the players in ``df`` (sum of makes / sum of attempts)."""
    out = {}
    for cat, (makes, att) in config.PCT_COLUMNS.items():
        total_att = df[att].sum()
        out[cat] = float(df[makes].sum() / total_att) if total_att > 0 else 0.0
    return out


def category_values(df: pd.DataFrame, pool_pct: dict[str, float]) -> pd.DataFrame:
    """Raw per-category values: counting stats as-is, percentages as volume-weighted impact.

    Args:
        df: Player projections with the columns in ``config.CAT_COLUMNS`` and ``config.PCT_COLUMNS``.
        pool_pct: League percentages from :func:`pool_percentages`.

    Returns:
        DataFrame indexed like ``df`` with one column per category in ``config.CATEGORIES``.
    """
    vals = pd.DataFrame(index=df.index)
    for cat in config.CATEGORIES:
        if cat in config.CAT_COLUMNS:
            vals[cat] = df[config.CAT_COLUMNS[cat]].astype(float)
        else:
            makes, att = config.PCT_COLUMNS[cat]
            # makes - pct*att == (player_pct - pct) * att, and is 0 (not NaN) with 0 attempts.
            vals[cat] = df[makes].astype(float) - pool_pct[cat] * df[att].astype(float)
    return vals


def _zscore(vals: pd.DataFrame, mean: pd.Series, std: pd.Series) -> pd.DataFrame:
    safe_std = std.replace(0, np.nan)
    z = (vals - mean) / safe_std
    z = z.fillna(0.0)
    for cat in config.NEGATIVE_CATS:
        z[cat] = -z[cat]
    return z


def fit_pool(
    df: pd.DataFrame,
    pool_size: int = config.ZSCORE_POOL_SIZE,
    punt: Iterable[str] | None = None,
    max_iter: int = config.ZSCORE_MAX_ITER,
) -> PoolStats:
    """Find the self-consistent top-``pool_size`` pool and its category means/stds.

    Starts with every player as the pool, then repeatedly re-selects the top
    ``pool_size`` by total z (excluding punted categories) until membership is stable.
    """
    punt = normalize_punt(punt)
    keep = [c for c in config.CATEGORIES if c not in punt]
    pool_size = min(pool_size, len(df))
    pool_ids = df.index

    for it in range(1, max_iter + 1):
        pool = df.loc[pool_ids]
        pct = pool_percentages(pool)
        pool_vals = category_values(pool, pct)
        mean, std = pool_vals.mean(), pool_vals.std(ddof=0)

        totals = _zscore(category_values(df, pct), mean, std)[keep].sum(axis=1)
        new_ids = totals.nlargest(pool_size).index
        if set(new_ids) == set(pool_ids):
            break
        pool_ids = new_ids

    return PoolStats(mean=mean, std=std, pool_pct=pct, pool_ids=pd.Index(pool_ids), iterations=it, punt=punt)


def apply_zscores(df: pd.DataFrame, stats: PoolStats, punt: Iterable[str] | None = None) -> pd.DataFrame:
    """Score players in ``df`` against an already-fitted pool.

    Returns:
        DataFrame with ``z_<CAT>`` for every category and ``z_total`` (sum over non-punted cats).
    """
    punt = normalize_punt(punt if punt is not None else stats.punt)
    z = _zscore(category_values(df, stats.pool_pct), stats.mean, stats.std)
    out = z.add_prefix("z_")
    out["z_total"] = z[[c for c in config.CATEGORIES if c not in punt]].sum(axis=1)
    return out


def value_players(
    projections: pd.DataFrame,
    punt: Iterable[str] | None = None,
    pool_size: int = config.ZSCORE_POOL_SIZE,
    max_iter: int = config.ZSCORE_MAX_ITER,
) -> tuple[pd.DataFrame, PoolStats]:
    """Rank players by total z-value.

    Args:
        projections: Per-game projections (output of ``project_players``), indexed by player_id.
        punt: Categories to leave out of the total, e.g. ``["FT_PCT", "TOV"]``.
        pool_size: Size of the reference pool (default 156 = 12 teams x 13).
        max_iter: Cap on pool re-selection iterations.

    Returns:
        ``(table, stats)`` where ``table`` is ``projections`` plus ``z_<CAT>``,
        ``z_total``, ``rank`` and ``in_pool`` columns sorted by ``z_total``, and
        ``stats`` is the fitted :class:`PoolStats` for scoring other players or teams.
    """
    stats = fit_pool(projections, pool_size=pool_size, punt=punt, max_iter=max_iter)
    table = projections.join(apply_zscores(projections, stats))
    table["in_pool"] = table.index.isin(stats.pool_ids)
    table = table.sort_values("z_total", ascending=False)
    table["rank"] = np.arange(1, len(table) + 1)
    return table, stats


if __name__ == "__main__":
    import argparse

    from src.projections.project import current_projections

    parser = argparse.ArgumentParser(description="Print z-score rankings.")
    parser.add_argument("--punt", nargs="*", default=[], choices=config.CATEGORIES)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    table, stats = value_players(current_projections(), punt=args.punt)
    cols = ["rank", "name", "team", "z_total", *[f"z_{c}" for c in config.CATEGORIES]]
    shown = table[cols].head(args.top).rename(columns={f"z_{c}": config.CAT_LABELS[c] for c in config.CATEGORIES})
    print(f"Pool converged in {stats.iterations} iterations. League FG% {stats.pool_pct['FG_PCT']:.3f}, "
          f"FT% {stats.pool_pct['FT_PCT']:.3f}")
    print(shown.round(2).to_string(index=False))
