"""Tests for the projection engine."""

from __future__ import annotations

import pandas as pd
import pytest

from src import config
from src.projections.project import project_players

AS_OF = pd.Timestamp("2026-12-01")


def make_logs(player_id: int, name: str, dates, minutes=30.0, **per_game) -> pd.DataFrame:
    """Game logs where every game has identical stats (overridable per game via lists)."""
    rows = []
    for i, d in enumerate(dates):
        row = {"player_id": player_id, "name": name, "team_id": 1, "team": "BOS",
               "game_id": f"{player_id}-{i}", "date": pd.Timestamp(d), "opponent": "NYK", "is_home": True,
               "min": minutes[i] if isinstance(minutes, list) else minutes}
        for stat in config.PROJECTED_STATS:
            v = per_game.get(stat, 0.0)
            row[stat] = v[i] if isinstance(v, list) else v
        rows.append(row)
    return pd.DataFrame(rows)


def season_dates(n: int, end=AS_OF - pd.Timedelta(days=1), step_days: int = 2):
    return [end - pd.Timedelta(days=step_days * i) for i in range(n)][::-1]


def test_steady_player_projects_to_his_average():
    # 40 identical games: shrinkage, blend and windows should all agree.
    stats = dict(pts=20.0, reb=5.0, ast=4.0, stl=1.0, blk=0.5, fg3m=2.0, tov=2.0, fgm=8.0, fga=16.0, ftm=2.0, fta=2.5)
    logs = make_logs(1, "Steady", season_dates(40), **stats)
    proj = project_players(logs, as_of=AS_OF).loc[1]
    for stat, value in stats.items():
        assert proj[stat] == pytest.approx(value, rel=1e-9)
    assert proj["min"] == pytest.approx(30.0)
    assert proj["fg_pct"] == pytest.approx(0.5)


def test_hot_streak_lands_between_season_and_recent():
    n = 40
    pts = [10.0] * (n - 3) + [30.0] * 3
    logs = make_logs(1, "Hot", season_dates(n), pts=pts)
    proj = project_players(logs, as_of=AS_OF).loc[1, "pts"]
    season_avg = sum(pts) / n
    assert season_avg < proj < 30.0


def test_season_only_weights_ignore_recent_form():
    pts = [10.0] * 37 + [30.0] * 3
    logs = make_logs(1, "Hot", season_dates(40), pts=pts)
    season_only = project_players(logs, as_of=AS_OF, weights={"season": 1.0}).loc[1, "pts"]
    blended = project_players(logs, as_of=AS_OF).loc[1, "pts"]
    assert season_only < blended


def test_no_lookahead():
    logs = make_logs(1, "P", season_dates(20), pts=10.0)
    future = make_logs(1, "P", [AS_OF, AS_OF + pd.Timedelta(days=3)], pts=50.0)
    future["game_id"] = ["f1", "f2"]
    a = project_players(logs, as_of=AS_OF)
    b = project_players(pd.concat([logs, future]), as_of=AS_OF)
    pd.testing.assert_frame_equal(a, b)


def test_rookie_shrinks_toward_league_average_and_converges():
    # A league of 10 established players at 10 pts in 30 min, and a rookie who
    # scored 40 in his debut. One game should not make him a 40-ppg player.
    vets = pd.concat([make_logs(100 + i, f"Vet{i}", season_dates(30), pts=10.0) for i in range(10)])
    one_game = make_logs(1, "Rookie", season_dates(1), pts=40.0)
    many_games = make_logs(1, "Rookie", season_dates(30), pts=40.0)

    p1 = project_players(pd.concat([vets, one_game]), as_of=AS_OF).loc[1, "pts"]
    p30 = project_players(pd.concat([vets, many_games]), as_of=AS_OF).loc[1, "pts"]
    assert 10.0 < p1 < 40.0
    assert p1 < p30 < 40.0  # more evidence -> closer to his own rate


def test_last_season_prior_is_used():
    vets = pd.concat([make_logs(100 + i, f"Vet{i}", season_dates(30), pts=10.0) for i in range(10)])
    star_now = make_logs(1, "Star", season_dates(2), pts=12.0)  # slow start
    star_last = make_logs(1, "Star", season_dates(70, end=pd.Timestamp("2026-04-10"), step_days=2), pts=30.0)
    no_prior = project_players(pd.concat([vets, star_now]), as_of=AS_OF).loc[1, "pts"]
    with_prior = project_players(pd.concat([vets, star_now]), as_of=AS_OF, prior_logs=star_last).loc[1, "pts"]
    assert with_prior > no_prior
    assert with_prior > 12.0


def test_projected_minutes_scale_counting_stats():
    # Same per-minute rate, but minutes jumped from 20 to 36 in the last week.
    mins = [20.0] * 30 + [36.0] * 3
    pts = [m * 0.5 for m in mins]
    logs = make_logs(1, "Promoted", season_dates(33), minutes=mins, pts=pts)
    proj = project_players(logs, as_of=AS_OF).loc[1]
    assert proj["pts"] / proj["min"] == pytest.approx(0.5)
    assert 20.0 < proj["min"] < 36.0


def test_inactive_flag():
    logs = make_logs(1, "Hurt", season_dates(10, end=AS_OF - pd.Timedelta(days=30)), pts=10.0)
    assert not project_players(logs, as_of=AS_OF).loc[1, "active"]


def test_bad_weights_raise():
    logs = make_logs(1, "P", season_dates(5), pts=10.0)
    with pytest.raises(ValueError):
        project_players(logs, as_of=AS_OF, weights={"last99": 1.0})
