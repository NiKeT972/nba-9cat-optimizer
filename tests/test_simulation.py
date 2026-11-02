"""Tests for the Monte Carlo matchup simulator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config
from src.simulation.matchup import (
    category_values,
    minutes_cv,
    simulate_matchup,
    simulate_team,
    week_to_date,
)

TODAY = pd.Timestamp("2026-11-04")  # Wednesday; week = Mon 11-02 .. Sun 11-08


def player(pid, team, scale=1.0, **over):
    base = dict(pts=0.0, reb=6.0, ast=4.0, stl=1.0, blk=0.8, fg3m=1.5, tov=2.0,
                fgm=7.0, fga=15.0, ftm=3.0, fta=4.0, min=30.0)
    base = {k: v * scale for k, v in base.items()}
    base.update(over)
    base["pts"] = 2 * base["fgm"] + base["fg3m"] + base["ftm"]
    return {"player_id": pid, "name": f"P{pid}", "team": team, "active": True, **base}


def make_valued(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows).set_index("player_id")
    df["z_total"] = df["pts"]
    return df


@pytest.fixture
def schedule() -> pd.DataFrame:
    rows = [
        ("0022600101", "2026-11-02", 1, "AAA", 2, "BBB"),
        ("0022600102", "2026-11-04", 1, "AAA", 3, "CCC"),
        ("0022600103", "2026-11-05", 2, "BBB", 4, "DDD"),
        ("0022600104", "2026-11-06", 3, "CCC", 4, "DDD"),
        ("0022600105", "2026-11-07", 1, "AAA", 2, "BBB"),
        ("0022600106", "2026-11-08", 3, "CCC", 4, "DDD"),
    ]
    df = pd.DataFrame(rows, columns=["game_id", "date", "home_team_id", "home_team", "away_team_id", "away_team"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_identical_teams_are_a_coin_flip(schedule):
    # From Wednesday, AAA (Wed, Sat) and BBB (Thu, Sat) both have 2 games left.
    valued = make_valued([player(1, "AAA"), player(2, "AAA"), player(3, "BBB"), player(4, "BBB")])
    res = simulate_matchup(valued, [1, 2], [3, 4], schedule, TODAY, n_sims=20_000)
    assert res.my_games == res.opp_games
    assert res.expected_score[0] == pytest.approx(4.5, abs=0.15)
    assert sum(res.expected_score) == pytest.approx(9.0)
    assert res.categories["p_win"].between(0.35, 0.65).all()


def test_stronger_team_wins_most_categories(schedule):
    valued = make_valued([player(1, "AAA", 1.6), player(2, "AAA", 1.6), player(3, "BBB"), player(4, "BBB")])
    res = simulate_matchup(valued, [1, 2], [3, 4], schedule, TODAY)
    for cat in ["PTS", "REB", "AST", "STL", "BLK", "3PM"]:
        assert res.categories.loc[cat, "p_win"] > 0.6
    for cat in ["PTS", "REB", "AST"]:  # high-volume stats: less noise over a week
        assert res.categories.loc[cat, "p_win"] > 0.8
    # More volume means more turnovers too, which loses the TO category.
    assert res.categories.loc["TO", "p_win"] < 0.2
    assert res.p_win_matchup > 0.8


def test_more_games_matter(schedule):
    # Same players, but CCC plays 3 games this week vs BBB's 2.
    valued = make_valued([player(1, "CCC"), player(2, "BBB")])
    res = simulate_matchup(valued, [1], [2], schedule, TODAY)
    assert res.my_games == 3 and res.opp_games == 2
    assert res.categories.loc["PTS", "p_win"] > 0.7


def test_simulated_means_match_projections(schedule):
    valued = make_valued([player(1, "AAA"), player(2, "CCC")])
    games = pd.DataFrame({"player_id": [1, 1, 2, 2, 2]})
    rng = np.random.default_rng(0)
    totals = simulate_team(games, valued, pd.Series({1: 0.25, 2: 0.25}), 50_000, rng)
    for key in ["REB", "AST", "STL", "TOV", "FGA", "FGM", "FTA", "FTM", "FG3M", "PTS"]:
        expected = 5 * valued.loc[1, key.lower()]  # both players identical, 5 games
        assert totals[key].mean() == pytest.approx(expected, rel=0.02), key
    # Points are built from makes, so the identity holds exactly in every simulation.
    np.testing.assert_array_equal(totals["PTS"], 2 * totals["FGM"] + totals["FG3M"] + totals["FTM"])
    assert (totals["FGM"] <= totals["FGA"]).all() and (totals["FG3M"] <= totals["FGM"]).all()


def test_role_shock_adds_overdispersion():
    valued = make_valued([player(1, "AAA")])
    games = pd.DataFrame({"player_id": [1]})
    calm = simulate_team(games, valued, pd.Series({1: 1e-6}), 50_000, np.random.default_rng(1))
    wild = simulate_team(games, valued, pd.Series({1: 0.5}), 50_000, np.random.default_rng(1))
    assert calm["REB"].var() == pytest.approx(6.0, rel=0.05)  # plain Poisson: var == mean
    assert wild["REB"].var() > 1.5 * calm["REB"].var()


def test_team_percentages_are_makes_over_attempts():
    totals = {k: np.array([0.0, 0.0]) for k in ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV"]}
    totals.update(FGM=np.array([10.0, 0.0]), FGA=np.array([20.0, 0.0]), FTM=np.array([3.0, 1.0]), FTA=np.array([4.0, 2.0]))
    vals = category_values(totals)
    assert vals["FG_PCT"].tolist() == [0.5, 0.0]  # 0 attempts -> 0, not NaN
    assert vals["FT_PCT"].tolist() == [0.75, 0.5]


def test_seed_makes_results_reproducible(schedule):
    valued = make_valued([player(1, "AAA"), player(3, "CCC", 1.1)])
    a = simulate_matchup(valued, [1], [3], schedule, TODAY, n_sims=2000, seed=7)
    b = simulate_matchup(valued, [1], [3], schedule, TODAY, n_sims=2000, seed=7)
    pd.testing.assert_frame_equal(a.categories, b.categories)


def test_inactive_players_are_skipped(schedule):
    valued = make_valued([player(1, "AAA"), player(2, "AAA"), player(3, "CCC")])
    valued.loc[2, "active"] = False
    res = simulate_matchup(valued, [1, 2], [3], schedule, TODAY, n_sims=500)
    assert res.my_games == 2
    res_all = simulate_matchup(valued, [1, 2], [3], schedule, TODAY, n_sims=500, exclude_inactive=False)
    assert res_all.my_games == 4


def test_week_to_date_is_added(schedule):
    logs = pd.DataFrame({
        "player_id": [1, 1, 3], "date": pd.to_datetime(["2026-11-02", "2026-10-30", "2026-11-03"]),
        "min": [30.0, 30.0, 30.0], **{k.lower(): [10.0, 99.0, 1.0] for k in
        ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV", "FGM", "FGA", "FTM", "FTA"]},
    })
    banked = week_to_date(logs, [1], pd.Timestamp("2026-11-02"), TODAY)
    assert banked["REB"] == 10.0  # last week's game (99) excluded

    valued = make_valued([player(1, "AAA"), player(3, "CCC")])
    with_wtd = simulate_matchup(valued, [1], [3], schedule, TODAY, logs=logs, n_sims=4000)
    without = simulate_matchup(valued, [1], [3], schedule, TODAY, logs=logs, n_sims=4000, use_week_to_date=False)
    diff = with_wtd.categories.loc["REB", "my_mean"] - without.categories.loc["REB", "my_mean"]
    assert diff == pytest.approx(10.0, abs=0.5)


def test_close_categories_are_flagged(schedule):
    valued = make_valued([player(1, "AAA"), player(3, "CCC")])
    res = simulate_matchup(valued, [1], [3], schedule, TODAY)
    lo, hi = config.SIM_CLOSE_BAND
    assert set(res.targets) == set(res.categories.index[res.categories["p_win"].between(lo, hi)])
    assert res.score_distribution.sum() == pytest.approx(1.0)


def test_minutes_cv_shrinks_to_prior():
    logs = pd.DataFrame({"player_id": [1] * 10 + [2] * 2, "date": pd.date_range("2026-10-20", periods=12),
                         "min": [10, 40] * 5 + [30, 30]})
    cv = minutes_cv(logs, [1, 2, 3], as_of=pd.Timestamp("2026-11-30"))
    assert cv[3] == pytest.approx(config.SIM_MINUTES_CV_DEFAULT)  # no data -> prior
    assert cv[2] < config.SIM_MINUTES_CV_DEFAULT  # steady minutes pull it down a bit
    assert cv[1] > config.SIM_MINUTES_CV_DEFAULT  # erratic minutes push it up
    assert (cv <= config.SIM_MINUTES_CV_MAX).all()
