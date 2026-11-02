"""Tests for z-score maths and percentage-category handling."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config
from src.valuation.zscores import (
    apply_zscores,
    category_values,
    fit_pool,
    pool_percentages,
    value_players,
)

Z_COLS = [f"z_{c}" for c in config.CATEGORIES]


def make_players(n: int = 40, seed: int = 0) -> pd.DataFrame:
    """Random but plausible per-game projections."""
    rng = np.random.default_rng(seed)
    fga = rng.uniform(4, 20, n)
    fta = rng.uniform(0.5, 8, n)
    df = pd.DataFrame(
        {
            "name": [f"P{i}" for i in range(n)],
            "pts": rng.uniform(5, 30, n),
            "reb": rng.uniform(2, 12, n),
            "ast": rng.uniform(1, 9, n),
            "stl": rng.uniform(0.3, 2, n),
            "blk": rng.uniform(0.1, 2.5, n),
            "fg3m": rng.uniform(0, 4, n),
            "tov": rng.uniform(0.5, 4, n),
            "fga": fga,
            "fgm": fga * rng.uniform(0.40, 0.60, n),
            "fta": fta,
            "ftm": fta * rng.uniform(0.60, 0.92, n),
        },
        index=pd.Index(range(n), name="player_id"),
    )
    return df


# ---------------------------------------------------------------------------
# Core z-score maths
# ---------------------------------------------------------------------------
def test_zscores_are_standardised_over_the_pool():
    df = make_players(40)
    table, stats = value_players(df, pool_size=40)  # pool = everyone
    z = table[Z_COLS]
    np.testing.assert_allclose(z.mean().values, 0.0, atol=1e-12)
    np.testing.assert_allclose(z.std(ddof=0).values, 1.0, atol=1e-12)


def test_counting_stat_zscore_matches_formula():
    df = make_players(30)
    table, stats = value_players(df, pool_size=30)
    expected = (df["pts"] - df["pts"].mean()) / df["pts"].std(ddof=0)
    pd.testing.assert_series_equal(table["z_PTS"].sort_index(), expected, check_names=False)


def test_turnovers_are_inverted():
    df = make_players(30)
    table, _ = value_players(df, pool_size=30)
    worst_to = df["tov"].idxmax()
    best_to = df["tov"].idxmin()
    assert table.loc[worst_to, "z_TOV"] == table["z_TOV"].min() < 0
    assert table.loc[best_to, "z_TOV"] == table["z_TOV"].max() > 0
    expected = -(df["tov"] - df["tov"].mean()) / df["tov"].std(ddof=0)
    pd.testing.assert_series_equal(table["z_TOV"].sort_index(), expected, check_names=False)


def test_total_is_sum_of_categories():
    table, _ = value_players(make_players(30), pool_size=30)
    np.testing.assert_allclose(table["z_total"], table[Z_COLS].sum(axis=1))


def test_constant_category_gives_zero_not_nan():
    df = make_players(20)
    df["blk"] = 1.0
    table, _ = value_players(df, pool_size=20)
    assert (table["z_BLK"] == 0).all()
    assert table["z_total"].notna().all()


# ---------------------------------------------------------------------------
# Percentage categories
# ---------------------------------------------------------------------------
def test_pool_percentage_is_attempt_weighted_not_mean_of_percentages():
    df = pd.DataFrame({"fgm": [1.0, 9.0], "fga": [1.0, 20.0], "ftm": [0.0, 0.0], "fta": [1.0, 1.0]})
    pct = pool_percentages(df)
    assert pct["FG_PCT"] == pytest.approx(10 / 21)  # not (1.0 + 0.45) / 2
    assert pct["FG_PCT"] != pytest.approx((1.0 + 0.45) / 2)


def test_percentage_impact_formula():
    df = pd.DataFrame({"fgm": [6.0, 11.0], "fga": [10.0, 20.0], "ftm": [4.0, 9.0], "fta": [5.0, 10.0],
                       **{c: [0.0, 0.0] for c in config.CAT_COLUMNS.values()}})
    pct = {"FG_PCT": 0.47, "FT_PCT": 0.78}
    vals = category_values(df, pct)
    # (0.60 - 0.47) * 10 = 1.3 ; (0.55 - 0.47) * 20 = 1.6
    assert vals["FG_PCT"].tolist() == pytest.approx([1.3, 1.6])
    # (0.80 - 0.78) * 5 = 0.1 ; (0.90 - 0.78) * 10 = 1.2
    assert vals["FT_PCT"].tolist() == pytest.approx([0.1, 1.2])


def test_volume_beats_raw_percentage():
    """A 55% shooter on 20 FGA helps more than a 70% shooter on 2 FGA."""
    df = make_players(30, seed=1)
    df.loc[0, ["fgm", "fga"]] = [1.4, 2.0]    # 70% on 2 attempts
    df.loc[1, ["fgm", "fga"]] = [11.0, 20.0]  # 55% on 20 attempts
    table, stats = value_players(df, pool_size=30)
    assert stats.pool_pct["FG_PCT"] < 0.55
    assert table.loc[1, "z_FG_PCT"] > table.loc[0, "z_FG_PCT"]


def test_high_volume_bad_shooter_hurts_more_than_low_volume_bad_shooter():
    df = make_players(30, seed=2)
    df.loc[0, ["ftm", "fta"]] = [0.5, 1.0]   # 50% on 1 FTA
    df.loc[1, ["ftm", "fta"]] = [5.0, 10.0]  # 50% on 10 FTA
    table, _ = value_players(df, pool_size=30)
    assert table.loc[1, "z_FT_PCT"] < table.loc[0, "z_FT_PCT"] < 0


def test_zero_attempts_is_neutral_impact():
    df = make_players(30, seed=3)
    df.loc[0, ["fgm", "fga", "ftm", "fta"]] = 0.0
    _, stats = value_players(df, pool_size=30)
    vals = category_values(df, stats.pool_pct)
    assert vals.loc[0, "FG_PCT"] == 0.0 and vals.loc[0, "FT_PCT"] == 0.0
    assert not vals.isna().any().any()


# ---------------------------------------------------------------------------
# Iterative pool
# ---------------------------------------------------------------------------
def test_pool_is_self_consistent_top_n():
    df = make_players(200, seed=4)
    table, stats = value_players(df, pool_size=50)
    assert len(stats.pool_ids) == 50
    assert set(table.head(50).index) == set(stats.pool_ids)  # top 50 under the final pool's scale
    assert stats.iterations < config.ZSCORE_MAX_ITER


def test_pool_stats_are_computed_on_pool_only():
    df = make_players(200, seed=5)
    table, stats = value_players(df, pool_size=50)
    pool = table.loc[stats.pool_ids]
    np.testing.assert_allclose(pool[Z_COLS].mean().values, 0.0, atol=1e-9)
    # Everyone outside the pool is on average below it.
    assert table.loc[~table["in_pool"], "z_total"].max() <= pool["z_total"].min()


def test_pool_size_larger_than_player_count():
    table, stats = value_players(make_players(10), pool_size=156)
    assert len(stats.pool_ids) == 10


# ---------------------------------------------------------------------------
# Punting
# ---------------------------------------------------------------------------
def test_punt_excludes_categories_from_total():
    df = make_players(60, seed=6)
    table, _ = value_players(df, punt=["FT_PCT", "TOV"], pool_size=30)
    kept = [f"z_{c}" for c in config.CATEGORIES if c not in ("FT_PCT", "TOV")]
    np.testing.assert_allclose(table["z_total"], table[kept].sum(axis=1))
    assert "z_FT_PCT" in table  # still reported, just not counted


def test_punt_changes_rankings_toward_punted_weakness():
    df = make_players(60, seed=7)
    df.loc[0, ["ftm", "fta"]] = [2.0, 8.0]  # dreadful FT shooter
    df.loc[0, ["reb", "blk"]] = [14.0, 3.0]  # elite big
    normal, _ = value_players(df, pool_size=30)
    punt, _ = value_players(df, punt=["FT_PCT"], pool_size=30)
    assert punt.loc[0, "rank"] < normal.loc[0, "rank"]


def test_unknown_punt_category_raises():
    with pytest.raises(ValueError):
        value_players(make_players(10), punt=["DUNKS"])


def test_apply_zscores_scores_new_players_on_same_scale():
    df = make_players(40, seed=8)
    _, stats = value_players(df, pool_size=20)
    newcomer = df.iloc[[0]].copy()
    newcomer.index = pd.Index([999], name="player_id")
    z = apply_zscores(newcomer, stats)
    original = apply_zscores(df.iloc[[0]], stats)
    np.testing.assert_allclose(z.values, original.values)
