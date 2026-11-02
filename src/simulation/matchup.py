"""Monte Carlo simulation of a head-to-head 9-category week.

Generative model for one player in one game
-------------------------------------------
Let ``mu_x`` be the player's projected per-game value of stat ``x`` (from the
projection engine, which is itself fitted to recent games).

1. **Role shock** ``s ~ Gamma(mean=1, CV=c)``. One draw per player-game scales
   every stat, so a 40-minute night lifts points, rebounds *and* turnovers together.
   ``c`` is the player's recent minutes CV, shrunk toward a league prior.
2. **Counting stats** ``REB, AST, STL, BLK, TO ~ Poisson(s * mu)``. Mixing Poisson
   over a Gamma gives the extra variance real box scores show.
3. **Shooting** is simulated as attempts then makes, so FG%/FT% come out of the
   same draws as points::

       FGA ~ Poisson(s * mu_FGA)     FGM ~ Binomial(FGA, fg%)
       3PM ~ Binomial(FGM, 3PM/FGM)  FTA ~ Poisson(s * mu_FTA)   FTM ~ Binomial(FTA, ft%)
       PTS = 2*FGM + 3PM + FTM

Team totals are summed over every remaining *started* game (the daily-lineup cap
from Phase 4 decides who starts), added to week-to-date stats, and compared
category by category. Team FG% = total FGM / total FGA.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src import config
from src.valuation.decisions import daily_lineups, week_window

POISSON_COUNTING = {"REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk", "TOV": "tov"}
RAW_KEYS = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV", "FGM", "FGA", "FTM", "FTA"]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def minutes_cv(
    logs: pd.DataFrame | None,
    player_ids: Iterable[int],
    as_of: pd.Timestamp,
    recent_games: int = config.SIM_RECENT_GAMES,
) -> pd.Series:
    """Per-player coefficient of variation of minutes over recent games, shrunk to the prior.

    ``cv^2 = (n * cv_obs^2 + k * cv_prior^2) / (n + k)``, capped at ``SIM_MINUTES_CV_MAX``.
    """
    ids = pd.Index(list(player_ids))
    prior = config.SIM_MINUTES_CV_DEFAULT
    if logs is None or logs.empty:
        return pd.Series(prior, index=ids)
    recent = logs[(logs["player_id"].isin(ids)) & (logs["date"] < as_of)]
    recent = recent.sort_values("date").groupby("player_id").tail(recent_games)
    g = recent.groupby("player_id")["min"]
    n = g.size().reindex(ids, fill_value=0).astype(float)
    mean = g.mean().reindex(ids)
    std = g.std(ddof=1).reindex(ids)
    cv_obs = (std / mean).where(n > 1, 0.0).fillna(0.0)
    k = config.SIM_MINUTES_CV_PRIOR_GAMES
    cv2 = (n * cv_obs**2 + k * prior**2) / (n + k)
    return np.sqrt(cv2).clip(upper=config.SIM_MINUTES_CV_MAX)


def week_to_date(
    logs: pd.DataFrame | None, ids: Iterable[int], week_start: pd.Timestamp, sim_start: pd.Timestamp
) -> dict[str, float]:
    """Stats already banked this week (games in ``[week_start, sim_start)``) for these players."""
    out = dict.fromkeys(RAW_KEYS, 0.0)
    if logs is None or logs.empty or sim_start <= week_start:
        return out
    done = logs[logs["player_id"].isin(list(ids)) & (logs["date"] >= week_start) & (logs["date"] < sim_start)]
    for key in RAW_KEYS:
        out[key] = float(done[key.lower()].sum())
    return out


def scheduled_games(
    valued: pd.DataFrame,
    ids: Iterable[int],
    schedule: pd.DataFrame,
    today: pd.Timestamp,
    include_today: bool,
    slots: int,
    exclude_inactive: bool,
) -> pd.DataFrame:
    """Started player-games for the rest of the week (one row per player per game)."""
    ids = [i for i in ids if i in valued.index]
    if exclude_inactive and "active" in valued:
        ids = [i for i in ids if bool(valued.loc[i, "active"])]
    if not ids:
        return pd.DataFrame(columns=["date", "player_id", "name", "team", "opponent", "z_total", "start"])
    lineup = daily_lineups(valued, ids, schedule, today, include_today=include_today, slots=slots)
    return lineup[lineup["start"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Simulation core
# ---------------------------------------------------------------------------
def simulate_team(
    games: pd.DataFrame, valued: pd.DataFrame, cv: pd.Series, n_sims: int, rng: np.random.Generator
) -> dict[str, np.ndarray]:
    """Simulate weekly team totals.

    Args:
        games: One row per started player-game (needs ``player_id``).
        valued: Projections indexed by player_id (per-game means).
        cv: Role-shock CV per player_id.
        n_sims: Number of simulated weeks.
        rng: NumPy random generator.

    Returns:
        ``{stat: array(n_sims)}`` for every key in ``RAW_KEYS``.
    """
    if games.empty:
        return {k: np.zeros(n_sims) for k in RAW_KEYS}

    p = valued.loc[games["player_id"]]
    mu = {k: p[k.lower()].to_numpy(float) for k in ["REB", "AST", "STL", "BLK", "TOV", "FGA", "FTA"]}
    fgm, fga, ftm, fta, fg3m = (p[c].to_numpy(float) for c in ["fgm", "fga", "ftm", "fta", "fg3m"])
    fg_pct = np.divide(fgm, fga, out=np.zeros_like(fgm), where=fga > 0).clip(0, 1)
    ft_pct = np.divide(ftm, fta, out=np.zeros_like(ftm), where=fta > 0).clip(0, 1)
    three_share = np.divide(fg3m, fgm, out=np.zeros_like(fgm), where=fgm > 0).clip(0, 1)

    c = cv.reindex(games["player_id"]).fillna(config.SIM_MINUTES_CV_DEFAULT).to_numpy(float)
    shape = 1.0 / np.maximum(c, 1e-6) ** 2  # Gamma(k, 1/k): mean 1, CV = 1/sqrt(k)
    shock = rng.gamma(shape, 1.0 / shape, size=(n_sims, len(games)))

    out: dict[str, np.ndarray] = {}
    for cat, col in POISSON_COUNTING.items():
        out[cat] = rng.poisson(shock * mu[cat]).sum(axis=1)
    sim_fga = rng.poisson(shock * mu["FGA"])
    sim_fgm = rng.binomial(sim_fga, fg_pct)
    sim_3pm = rng.binomial(sim_fgm, three_share)
    sim_fta = rng.poisson(shock * mu["FTA"])
    sim_ftm = rng.binomial(sim_fta, ft_pct)
    out["FGA"], out["FGM"] = sim_fga.sum(axis=1), sim_fgm.sum(axis=1)
    out["FTA"], out["FTM"] = sim_fta.sum(axis=1), sim_ftm.sum(axis=1)
    out["FG3M"] = sim_3pm.sum(axis=1)
    out["PTS"] = 2 * out["FGM"] + out["FG3M"] + out["FTM"]
    return {k: v.astype(float) for k, v in out.items()}


def category_values(totals: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Turn raw simulated totals into the nine category values (percentages from makes/attempts)."""
    vals = {cat: totals[cat] for cat in ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV"]}
    for cat, (m, a) in {"FG_PCT": ("FGM", "FGA"), "FT_PCT": ("FTM", "FTA")}.items():
        vals[cat] = np.divide(totals[m], totals[a], out=np.zeros_like(totals[m]), where=totals[a] > 0)
    return vals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@dataclass
class MatchupResult:
    """Simulation output."""

    categories: pd.DataFrame  # per category: my_mean, opp_mean, p_win, p_tie, p_loss, median_margin, close
    expected_score: tuple[float, float]  # e.g. (5.3, 3.7)
    p_win_matchup: float
    p_tie_matchup: float
    score_distribution: pd.Series  # P(I win exactly k categories), ties counted as 0.5
    targets: list[str]  # close categories, most winnable swing first
    my_games: int
    opp_games: int
    n_sims: int


def simulate_matchup(
    valued: pd.DataFrame,
    my_ids: Iterable[int],
    opp_ids: Iterable[int],
    schedule: pd.DataFrame,
    today: pd.Timestamp | str,
    logs: pd.DataFrame | None = None,
    n_sims: int = config.SIM_COUNT,
    seed: int | None = config.SIM_SEED,
    include_today: bool = True,
    slots: int = config.ACTIVE_LINEUP_SLOTS,
    exclude_inactive: bool = True,
    use_week_to_date: bool = True,
) -> MatchupResult:
    """Simulate the rest of this fantasy week ``n_sims`` times.

    Args:
        valued: Player projections (z-score columns optional) indexed by player_id.
        my_ids: My roster.
        opp_ids: Opponent's roster.
        schedule: Tidy schedule (``src.data.clean.clean_schedule``).
        today: Simulation date; games from today (or tomorrow) to the end of the week are simulated.
        logs: This season's tidy game logs. Used for minutes variability and week-to-date stats.
        n_sims: Number of simulated weeks.
        seed: RNG seed (None = random).
        include_today: Simulate today's games (set False once they have tipped off).
        slots: Daily active lineup slots.
        exclude_inactive: Skip players flagged inactive (likely injured).
        use_week_to_date: Add stats already accumulated since the start of the week.

    Returns:
        :class:`MatchupResult`.
    """
    today = pd.Timestamp(today).normalize()
    my_ids, opp_ids = list(my_ids), list(opp_ids)
    sim_start, _ = week_window(today, include_today)
    week_start = today - pd.Timedelta(days=(today.weekday() - config.WEEK_START_WEEKDAY) % 7)
    rng = np.random.default_rng(seed)
    if "z_total" not in valued:
        valued = valued.assign(z_total=valued["pts"])  # only used to order daily lineups

    cv = minutes_cv(logs, my_ids + opp_ids, as_of=today)
    sides = {}
    for side, ids in (("my", my_ids), ("opp", opp_ids)):
        games = scheduled_games(valued, ids, schedule, today, include_today, slots, exclude_inactive)
        totals = simulate_team(games, valued, cv, n_sims, rng)
        if use_week_to_date:
            banked = week_to_date(logs, ids, week_start, sim_start)
            totals = {k: v + banked[k] for k, v in totals.items()}
        sides[side] = (category_values(totals), len(games))

    mine, my_games = sides["my"]
    theirs, opp_games = sides["opp"]
    rows, my_points = {}, np.zeros(n_sims)
    for cat in config.CATEGORIES:
        diff = mine[cat] - theirs[cat]
        if cat in config.NEGATIVE_CATS:
            diff = -diff
        win, tie = diff > 0, diff == 0
        my_points += win + config.SIM_TIE_VALUE * tie
        rows[config.CAT_LABELS[cat]] = {
            "my_mean": mine[cat].mean(),
            "opp_mean": theirs[cat].mean(),
            "p_win": win.mean(),
            "p_tie": tie.mean(),
            "p_loss": 1 - win.mean() - tie.mean(),
            "median_margin": float(np.median(diff)),  # positive = good for me (TO already flipped)
        }
    cats = pd.DataFrame.from_dict(rows, orient="index")
    lo, hi = config.SIM_CLOSE_BAND
    cats["close"] = cats["p_win"].between(lo, hi)
    targets = list((cats.loc[cats["close"], "p_win"] - 0.5).abs().sort_values().index)

    n_cats = len(config.CATEGORIES)
    exp_my = float(cats["p_win"].sum() + config.SIM_TIE_VALUE * cats["p_tie"].sum())
    return MatchupResult(
        categories=cats,
        expected_score=(exp_my, n_cats - exp_my),
        p_win_matchup=float((my_points > n_cats / 2).mean()),
        p_tie_matchup=float((my_points == n_cats / 2).mean()),
        score_distribution=pd.Series(my_points).value_counts(normalize=True).sort_index(),
        targets=targets,
        my_games=my_games,
        opp_games=opp_games,
        n_sims=n_sims,
    )
