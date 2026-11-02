"""Tests for the walk-forward backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config
from src.projections.backtest import actuals, backtest, summarize, to_markdown
from tests.test_projections import make_logs


def league(n_players: int = 12, n_games: int = 40, seed: int = 0) -> pd.DataFrame:
    """Players with stable true rates plus Poisson noise, one game every 2 days."""
    rng = np.random.default_rng(seed)
    dates = [pd.Timestamp("2025-10-21") + pd.Timedelta(days=2 * i) for i in range(n_games)]
    parts = []
    for pid in range(n_players):
        rate = rng.uniform(0.5, 1.5)
        fga = rng.poisson(14 * rate, n_games).astype(float)
        fgm = rng.binomial(fga.astype(int), 0.47).astype(float)
        fta = rng.poisson(4 * rate, n_games).astype(float)
        ftm = rng.binomial(fta.astype(int), 0.78).astype(float)
        fg3m = rng.binomial(fgm.astype(int), 0.3).astype(float)
        parts.append(make_logs(
            pid, f"P{pid}", dates, minutes=30.0,
            fga=list(fga), fgm=list(fgm), fta=list(fta), ftm=list(ftm), fg3m=list(fg3m),
            pts=list(2 * fgm + fg3m + ftm), reb=list(rng.poisson(6 * rate, n_games).astype(float)),
            ast=list(rng.poisson(4 * rate, n_games).astype(float)), stl=list(rng.poisson(1, n_games).astype(float)),
            blk=list(rng.poisson(0.7, n_games).astype(float)), tov=list(rng.poisson(2 * rate, n_games).astype(float)),
        ))
    return pd.concat(parts, ignore_index=True)


def test_actuals_window():
    logs = league(2, 10)
    start = pd.Timestamp("2025-10-25")
    a = actuals(logs, start, 7)
    window = logs[(logs["date"] >= start) & (logs["date"] < start + pd.Timedelta(days=7))]
    assert a.loc[0, "pts"] == pytest.approx(window[window["player_id"] == 0]["pts"].mean())
    assert a.loc[0, "fga_tot"] == window[window["player_id"] == 0]["fga"].sum()


def test_backtest_shapes_and_fair_comparison():
    errors = backtest(league())
    assert set(errors["model"]) == {"blend", "season_avg", "last7_avg"}
    assert set(errors["category"]) == set(config.CATEGORIES)
    # Every model is scored on exactly the same player-dates per category.
    keys = errors.groupby(["model", "category"]).apply(
        lambda g: tuple(sorted(zip(g["as_of"], g["player_id"]))), include_groups=False)
    for cat in config.CATEGORIES:
        assert keys["blend", cat] == keys["season_avg", cat] == keys["last7_avg", cat]


def test_projection_dates_never_see_scoring_week():
    logs = league()
    errors = backtest(logs)
    first_game = logs["date"].min()
    assert errors["as_of"].min() >= first_game + pd.Timedelta(days=config.BACKTEST_WARMUP_DAYS)
    assert errors["as_of"].max() <= logs["date"].max()


def test_blend_beats_last7_on_stable_players():
    # True rates never change, so a 7-day average is pure noise-chasing.
    summary = summarize(backtest(league(n_players=20, n_games=60, seed=3)))
    assert (summary["blend"] < summary["last7_avg"]).mean() >= 0.75
    assert "blend_vs_season_avg_%" in summary


def test_markdown_table_has_every_category():
    summary = summarize(backtest(league()))
    md = to_markdown(summary, 3, 100, "2025-26")
    for label in config.CAT_LABELS.values():
        assert f"| {label} |" in md


def test_update_readme_replaces_marked_section(tmp_path):
    from src.projections.backtest import update_readme

    readme = tmp_path / "README.md"
    readme.write_text("intro\n<!-- BACKTEST:START -->\nold\n<!-- BACKTEST:END -->\noutro\n")
    assert update_readme("| new |", path=readme)
    text = readme.read_text()
    assert "| new |" in text and "old" not in text and text.startswith("intro") and text.endswith("outro\n")
