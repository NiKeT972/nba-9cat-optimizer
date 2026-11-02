"""Per-game player projections: a shrunk, weighted blend of recent and season form.

Method (all done on *per-minute* rates, then scaled by projected minutes)
------------------------------------------------------------------------
1. **Prior rate** for each player = last season's per-minute rate (itself shrunk
   toward league average), or the league-average rate for rookies / no history.
2. **Season rate** = this season's totals shrunk toward the prior::

       rate_season = (stat_season + K_s * rate_prior) / (min_season + K_s)

   ``K_s`` (``SHRINK_SEASON_PRIOR_MINUTES``) is how many minutes the prior is
   "worth". After 1 game a player is mostly prior; after 20 games mostly himself.
3. **Window rates** (last 7 / last 15 days) are shrunk the same way toward the
   season rate with a smaller ``K_w``. A window with no games simply equals the
   season rate, so there is no special-casing for missing windows.
4. **Blend**: ``rate = sum(w_i * rate_i)`` with the configurable weights.
5. **Minutes** get the identical treatment (in games instead of minutes), and the
   per-game projection is ``rate * projected_minutes``.

Everything is a pure function of ``(logs, as_of)`` and uses only games strictly
before ``as_of``, so it can be backtested without look-ahead.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from src import config


def _sums(logs: pd.DataFrame, stats: list[str]) -> pd.DataFrame:
    """Per-player totals of ``stats`` plus minutes and games played."""
    if logs.empty:
        return pd.DataFrame(columns=["games", "min", *stats], dtype=float)
    agg = logs.groupby("player_id")[["min", *stats]].sum()
    agg["games"] = logs.groupby("player_id").size()
    return agg.astype(float)


def _league_rates(logs: pd.DataFrame, stats: list[str]) -> tuple[pd.Series, float]:
    """Minutes-weighted league per-minute rates and average minutes per game."""
    if logs.empty or logs["min"].sum() == 0:
        return pd.Series(0.0, index=stats), 0.0
    rates = logs[stats].sum() / logs["min"].sum()
    mpg = logs["min"].sum() / len(logs)
    return rates, float(mpg)


def _shrink(totals: pd.DataFrame, denom: pd.Series, prior: pd.DataFrame | pd.Series, k: float) -> pd.DataFrame:
    """(totals + k * prior) / (denom + k), row-aligned. ``prior`` is a rate per unit of ``denom``."""
    return totals.add(prior * k, fill_value=0.0).div(denom + k, axis=0)


def _prior_rates(
    player_ids: pd.Index,
    prior_logs: pd.DataFrame | None,
    league_rate: pd.Series,
    league_mpg: float,
    stats: list[str],
) -> tuple[pd.DataFrame, pd.Series]:
    """Per-player prior per-minute rates and prior minutes per game.

    Players with last-season data get that (shrunk toward league average);
    everyone else gets the league average.
    """
    league_frame = pd.DataFrame([league_rate.values] * len(player_ids), index=player_ids, columns=stats)
    prior_rate, prior_mpg = league_frame, pd.Series(league_mpg, index=player_ids)
    if prior_logs is None or prior_logs.empty:
        return prior_rate, prior_mpg

    last = _sums(prior_logs, stats).reindex(player_ids)
    has = last["games"].fillna(0) > 0
    if has.any():
        shrunk = _shrink(last.loc[has, stats], last.loc[has, "min"], league_frame.loc[has], config.SHRINK_SEASON_PRIOR_MINUTES)
        prior_rate.loc[has, stats] = shrunk.values
        prior_mpg.loc[has] = (last.loc[has, "min"] / last.loc[has, "games"]).values
    return prior_rate, prior_mpg


def project_players(
    logs: pd.DataFrame,
    as_of: pd.Timestamp | str | None = None,
    prior_logs: pd.DataFrame | None = None,
    weights: Mapping[str, float] | None = None,
    window_days: Mapping[str, int] | None = None,
    stats: list[str] | None = None,
) -> pd.DataFrame:
    """Project per-game stats for every player who has played this season.

    Args:
        logs: Tidy game logs for the current season (from ``src.data.clean``).
        as_of: Projection date. Only games strictly before it are used.
            Defaults to the day after the last game in ``logs``.
        prior_logs: Optional tidy game logs for the previous season, used as the
            shrinkage prior. Strongly recommended early in the season.
        weights: Blend weights keyed by window name plus ``"season"``
            (default ``config.PROJ_WEIGHTS``). Normalised to sum to 1.
        window_days: Window lengths in days keyed by name (default ``config.PROJ_WINDOW_DAYS``).
        stats: Columns to project (default ``config.PROJECTED_STATS``).

    Returns:
        One row per player, indexed by ``player_id``, with ``name, team, games,
        last_game, days_since_game, active, own_data_weight, min`` and one column per
        projected stat (per game), plus derived ``fg_pct`` and ``ft_pct``.
        ``own_data_weight`` in [0, 1] says how much of the season rate comes from this
        season's games rather than the prior.
    """
    weights = dict(weights or config.PROJ_WEIGHTS)
    window_days = dict(window_days or config.PROJ_WINDOW_DAYS)
    stats = list(stats or config.PROJECTED_STATS)
    missing = set(weights) - set(window_days) - {"season"}
    if missing:
        raise ValueError(f"weights reference unknown windows: {missing}")
    total_w = sum(weights.values())
    if total_w <= 0:
        raise ValueError("weights must sum to a positive number")
    weights = {k: v / total_w for k, v in weights.items()}

    if as_of is None:
        as_of = logs["date"].max() + pd.Timedelta(days=1)
    as_of = pd.Timestamp(as_of).normalize()
    season = logs[logs["date"] < as_of]
    if season.empty:
        return pd.DataFrame()

    ids = pd.Index(sorted(season["player_id"].unique()), name="player_id")
    league_rate, league_mpg = _league_rates(season, stats)
    prior_rate, prior_mpg = _prior_rates(ids, prior_logs, league_rate, league_mpg, stats)

    # --- season level -------------------------------------------------------
    s = _sums(season, stats).reindex(ids)
    rate_season = _shrink(s[stats], s["min"], prior_rate, config.SHRINK_SEASON_PRIOR_MINUTES)
    mpg_season = (s["min"] + config.SHRINK_SEASON_MPG_GAMES * prior_mpg) / (s["games"] + config.SHRINK_SEASON_MPG_GAMES)

    rate = rate_season * weights.get("season", 0.0)
    mpg = mpg_season * weights.get("season", 0.0)

    # --- recent windows, shrunk toward the season level ---------------------
    for name, days in window_days.items():
        w = weights.get(name, 0.0)
        if w == 0:
            continue
        recent = season[season["date"] >= as_of - pd.Timedelta(days=days)]
        r = _sums(recent, stats).reindex(ids).fillna(0.0)
        rate += w * _shrink(r[stats], r["min"], rate_season, config.SHRINK_RECENT_MINUTES)
        mpg += w * (r["min"] + config.SHRINK_RECENT_MPG_GAMES * mpg_season) / (r["games"] + config.SHRINK_RECENT_MPG_GAMES)

    proj = rate.mul(mpg, axis=0)

    # --- metadata -----------------------------------------------------------
    latest = season.sort_values("date").groupby("player_id").tail(1).set_index("player_id").reindex(ids)
    out = pd.DataFrame(index=ids)
    out["name"] = latest["name"]
    out["team"] = latest["team"]
    out["games"] = s["games"].astype(int)
    out["last_game"] = latest["date"]
    out["days_since_game"] = (as_of - latest["date"]).dt.days
    out["active"] = out["days_since_game"] <= config.INACTIVE_AFTER_DAYS
    out["own_data_weight"] = s["min"] / (s["min"] + config.SHRINK_SEASON_PRIOR_MINUTES)
    out["min"] = mpg
    out[stats] = proj
    if {"fgm", "fga"} <= set(stats):
        out["fg_pct"] = np.where(out["fga"] > 0, out["fgm"] / out["fga"], np.nan)
    if {"ftm", "fta"} <= set(stats):
        out["ft_pct"] = np.where(out["fta"] > 0, out["ftm"] / out["fta"], np.nan)
    return out.sort_values("pts", ascending=False)


def current_projections(season: str = config.SEASON, force: bool = False) -> pd.DataFrame:
    """Load cached data for ``season`` (+ previous season as prior) and project as of today."""
    from src.data.pipeline import load_game_logs

    logs = load_game_logs(season, force=force)
    try:
        prior = load_game_logs(config.previous_season(season))
    except Exception:  # noqa: BLE001 - prior is a nice-to-have
        prior = None
    return project_players(logs, as_of=pd.Timestamp.today().normalize(), prior_logs=prior)


if __name__ == "__main__":
    cols = ["name", "team", "games", "min", "pts", "reb", "ast", "stl", "blk", "fg3m", "fg_pct", "ft_pct", "tov"]
    print(current_projections()[cols].head(25).round(2).to_string())
