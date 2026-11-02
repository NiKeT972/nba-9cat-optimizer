"""Tests for roster persistence, start/sit, waivers and trade analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config
from src.data import roster as roster_io
from src.valuation.decisions import (
    analyze_trade,
    daily_lineups,
    games_remaining,
    rank_waivers,
    start_sit,
    week_window,
)
from src.valuation.team import category_need_weights, team_category_z, team_totals
from src.valuation.zscores import value_players
from tests.test_zscores import make_players

Z_COLS = [f"z_{c}" for c in config.CATEGORIES]


@pytest.fixture
def valued() -> pd.DataFrame:
    df = make_players(60, seed=11)
    df["team"] = ["BOS", "NYK", "LAL"] * 20
    df["active"] = True
    table, _ = value_players(df, pool_size=40)
    return table


@pytest.fixture
def schedule() -> pd.DataFrame:
    # Week of Mon 2026-11-02 .. Sun 2026-11-08
    rows = [
        ("0022600101", "2026-11-02", 1, "BOS", 2, "NYK"),
        ("0022600102", "2026-11-04", 3, "LAL", 1, "BOS"),
        ("0022600103", "2026-11-05", 2, "NYK", 3, "LAL"),
        ("0022600104", "2026-11-07", 1, "BOS", 3, "LAL"),
        ("0022600105", "2026-11-09", 2, "NYK", 1, "BOS"),  # next week
    ]
    df = pd.DataFrame(rows, columns=["game_id", "date", "home_team_id", "home_team", "away_team_id", "away_team"])
    df["date"] = pd.to_datetime(df["date"])
    df["week"] = 1
    return df


# ---------------------------------------------------------------------------
# Roster persistence
# ---------------------------------------------------------------------------
def test_roster_roundtrip(tmp_path):
    path = tmp_path / "r.json"
    rosters = {config.MY_TEAM: [3, 1, 2], config.OPPONENT: [7]}
    roster_io.save_rosters(rosters, names={1: "A", 2: "B", 3: "C", 7: "G"}, path=path)
    assert roster_io.load_rosters(path) == rosters
    assert '"name": "C"' in path.read_text()
    assert roster_io.rostered_ids(rosters) == {1, 2, 3, 7}


def test_roster_missing_or_corrupt_file(tmp_path):
    assert roster_io.load_rosters(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert roster_io.load_rosters(bad) == {}


def test_ids_from_names(valued):
    pid = valued.index[0]
    assert roster_io.ids_from_names([valued.loc[pid, "name"]], valued) == [pid]
    with pytest.raises(KeyError):
        roster_io.ids_from_names(["Nobody"], valued)


# ---------------------------------------------------------------------------
# Schedule / start-sit
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "today,include,expected",
    [
        ("2026-11-04", True, ("2026-11-04", "2026-11-08")),   # Wednesday
        ("2026-11-04", False, ("2026-11-05", "2026-11-08")),
        ("2026-11-02", True, ("2026-11-02", "2026-11-08")),   # Monday
        ("2026-11-08", True, ("2026-11-08", "2026-11-08")),   # Sunday
    ],
)
def test_week_window(today, include, expected):
    start, end = week_window(today, include)
    assert (start, end) == (pd.Timestamp(expected[0]), pd.Timestamp(expected[1]))


def test_games_remaining(valued, schedule):
    g = games_remaining(valued, schedule, "2026-11-04", "2026-11-08")
    by_team = valued.assign(g=g).groupby("team")["g"].first().to_dict()
    assert by_team == {"BOS": 2, "LAL": 3, "NYK": 1}


def test_start_sit_weekly_value(valued, schedule):
    my = list(valued.index[:6])
    ss = start_sit(valued, my, schedule, "2026-11-04", slots=99)
    np.testing.assert_allclose(ss["weekly_value"], ss["z_total"] * ss["games_left"])
    assert (ss["games_started"] == ss["games_left"]).all()  # no cap hit with 99 slots


def test_start_sit_slot_cap_benches_weaker_player(valued, schedule):
    lal = valued[valued["team"] == "LAL"].sort_values("z_total", ascending=False)
    best, worse = lal.index[0], lal.index[-1]
    ss = start_sit(valued, [best, worse], schedule, "2026-11-04", slots=1)
    assert ss.loc[best, "games_started"] == 3
    assert ss.loc[worse, "games_started"] == 0
    assert ss.loc[worse, "benched_games"] == 3
    lineup = daily_lineups(valued, [best, worse], schedule, "2026-11-04", slots=1)
    assert lineup.loc[lineup["start"], "player_id"].eq(best).all()


# ---------------------------------------------------------------------------
# Team helpers
# ---------------------------------------------------------------------------
def test_team_totals_percentages_are_makes_over_attempts(valued):
    ids = list(valued.index[:5])
    tot = team_totals(valued, ids)
    sub = valued.loc[ids]
    assert tot["FG_PCT"] == pytest.approx(sub["fgm"].sum() / sub["fga"].sum())
    assert tot["FT_PCT"] == pytest.approx(sub["ftm"].sum() / sub["fta"].sum())
    assert tot["PTS"] == pytest.approx(sub["pts"].sum())


def test_team_totals_with_games_multiplier(valued):
    ids = list(valued.index[:3])
    games = pd.Series([2, 3, 4], index=ids)
    tot = team_totals(valued, ids, games=games)
    assert tot["REB"] == pytest.approx((valued.loc[ids, "reb"] * games).sum())


def test_need_weights():
    team_z = pd.Series({c: 0.0 for c in config.CATEGORIES})
    team_z["BLK"], team_z["PTS"] = -9.0, 9.0
    w = category_need_weights(team_z, punt=["FT_PCT"])
    assert w["REB"] == pytest.approx(1.0)
    assert w["BLK"] > 1.9 and w["PTS"] < 0.1
    assert w["FT_PCT"] == 0.0


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------
def test_waivers_exclude_rostered_players(valued):
    my = list(valued.index[:13])
    others = list(valued.index[13:30])
    fa = rank_waivers(valued, my, others, top_n=None)
    assert not set(fa.index) & (set(my) | set(others))
    assert len(fa) == len(valued) - 30


def test_waivers_prioritise_weak_categories():
    """Two free agents with identical total z: the one who fills my hole ranks first."""
    cats = config.CATEGORIES
    rows = {}
    # My team: great at everything except BLK.
    for i in range(13):
        rows[i] = {f"z_{c}": (1.0 if c != "BLK" else -1.5) for c in cats}
    rows[100] = {f"z_{c}": (4.0 if c == "BLK" else 0.0) for c in cats}  # shot-blocker
    rows[101] = {f"z_{c}": (4.0 if c == "PTS" else 0.0) for c in cats}  # scorer
    valued = pd.DataFrame.from_dict(rows, orient="index")
    valued["z_total"] = valued[[f"z_{c}" for c in cats]].sum(axis=1)
    valued["name"] = [f"P{i}" for i in valued.index]
    valued["team"] = "BOS"
    valued["active"] = True

    fa = rank_waivers(valued, my_ids=list(range(13)), rostered=[], top_n=None)
    assert fa.loc[100, "z_total"] == fa.loc[101, "z_total"]
    assert fa.index[0] == 100
    assert fa.loc[100, "helps"].startswith("BLK")
    assert fa.loc[100, "net_gain"] > fa.loc[101, "net_gain"]


def test_waivers_punted_category_is_ignored(valued):
    my = list(valued.index[:13])
    fa = rank_waivers(valued, my, [], punt=list(set(config.CATEGORIES) - {"STL"}), top_n=None)
    # Only STL counts, so fit ordering follows STL z ordering.
    assert fa["fit"].is_monotonic_decreasing
    assert fa["z_STL"].is_monotonic_decreasing


def test_waivers_weekly_streaming_sort(valued, schedule):
    my = list(valued.index[:13])
    gl = games_remaining(valued, schedule, "2026-11-04", "2026-11-08")
    fa = rank_waivers(valued, my, [], games_left=gl, top_n=None)
    np.testing.assert_allclose(fa["weekly_fit"], fa["fit"] * fa["games_left"])
    assert fa["weekly_fit"].is_monotonic_decreasing


def test_waivers_hide_inactive(valued):
    v = valued.copy()
    fa_id = v.index[-1]
    v.loc[fa_id, "active"] = False
    assert fa_id not in rank_waivers(v, list(v.index[:13]), [], top_n=None).index
    assert fa_id in rank_waivers(v, list(v.index[:13]), [], only_active=False, top_n=None).index


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------
def test_trade_one_for_one(valued):
    my = list(valued.index[:13])
    give, get = [my[0]], [valued.index[20]]
    res = analyze_trade(valued, my, give, get)
    cats = res.categories
    assert cats.loc["PTS", "delta"] == pytest.approx(valued.loc[get[0], "pts"] - valued.loc[give[0], "pts"])
    after = [i for i in my if i != give[0]] + get
    sub = valued.loc[after]
    assert cats.loc["FG%", "after"] == pytest.approx(sub["fgm"].sum() / sub["fga"].sum())
    expected = valued.loc[get[0], "z_total"] - valued.loc[give[0], "z_total"]
    assert res.total_z_delta == pytest.approx(expected)
    assert res.roster_size_change == 0
    assert set(res.improved) | set(res.hurt) <= set(cats.index)


def test_trade_two_for_one_and_partner_side(valued):
    my = list(valued.index[:13])
    theirs = list(valued.index[13:26])
    res = analyze_trade(valued, my, give=my[:2], get=[theirs[0]], their_ids=theirs)
    assert res.roster_size_change == -1
    # z-sums are linear, so whatever I gain the partner loses (no punts on either side).
    assert res.their_total_z_delta == pytest.approx(-res.total_z_delta)


def test_trade_punt_excluded_from_total(valued):
    my = list(valued.index[:13])
    res = analyze_trade(valued, my, [my[0]], [valued.index[30]], punt=["FT_PCT", "TOV"])
    kept = [config.CAT_LABELS[c] for c in config.CATEGORIES if c not in ("FT_PCT", "TOV")]
    assert res.total_z_delta == pytest.approx(res.categories.loc[kept, "z_delta"].sum())
    assert "FT%" not in res.improved + res.hurt


def test_trade_rejects_players_not_on_roster(valued):
    with pytest.raises(ValueError):
        analyze_trade(valued, [1, 2, 3], give=[50], get=[51])
