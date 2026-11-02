"""Behavioural tests for the roster advisor in web/core.js (run under Node)."""

from __future__ import annotations

import json
import subprocess

import pytest

from app.static_site import web_config
from src import config
from tests.test_web_parity import CORE, NODE, players_fixture, sched_js, schedule_fixture, to_js

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")

RUNNER = r"""
const NC = require(process.argv[1]);
let input = ""; process.stdin.on("data", (d) => (input += d)).on("end", () => {
  const { players, cfg, rosters, schedule, today, punt } = JSON.parse(input);
  const rows = NC.valuePlayers(players, cfg, punt || []);
  const advice = NC.advise(rows, rosters, schedule, today, cfg, { punt: punt || [] });
  const z = Object.fromEntries(rows.map((r) => [r.id, { total: r.total, z: r.z }]));
  process.stdout.write(JSON.stringify({ advice, z }));
});
"""


def advise(df, rosters, punt=None, with_schedule=True):
    payload = json.dumps({
        "players": to_js(df), "cfg": web_config(), "rosters": rosters, "punt": punt or [],
        "schedule": sched_js(schedule_fixture()) if with_schedule else [], "today": "2026-11-04",
    })
    res = subprocess.run([NODE, "-e", RUNNER, str(CORE)], input=payload, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def ids(df, a, b):
    return [int(i) for i in df.index[a:b]]


def test_waiver_move_fills_the_obvious_hole():
    df = players_fixture(150, seed=3)
    my = ids(df, 0, 13)
    df.loc[my, "blk"] = 0.05  # my team blocks nothing
    star_blocker = int(df.index[140])
    df.loc[star_blocker, ["blk", "reb", "fgm", "fga"]] = [3.5, 11.0, 7.0, 11.0]
    out = advise(df, {"my": my, "opp": ids(df, 13, 26), "other": ids(df, 26, 100)})
    moves = out["advice"]["moves"]
    assert moves, "expected at least one waiver suggestion"
    assert moves[0]["add"] == star_blocker
    assert "BLK" in moves[0]["helps"]
    assert moves[0]["drop"] in my
    assert "dExpected" in moves[0] and "dWin" in moves[0]  # re-simulated against the opponent


def test_suggested_moves_clear_the_gain_threshold():
    df = players_fixture(150, seed=4)
    out = advise(df, {"my": ids(df, 0, 13), "opp": ids(df, 13, 26), "other": ids(df, 26, 120)})
    cfg = web_config()
    for m in out["advice"]["moves"]:
        assert m["gain"] >= cfg["advice_min_gain"] or m["dExpected"] >= cfg["advice_min_gain"] / 2
    assert len(out["advice"]["moves"]) <= cfg["advice_max"]


def test_trades_are_fair_to_the_partner_and_help_me():
    df = players_fixture(150, seed=5)
    my, opp, other = ids(df, 40, 53), ids(df, 0, 13), ids(df, 13, 40)
    out = advise(df, {"my": my, "opp": opp, "other": other})
    z, cfg = out["z"], web_config()
    trades = out["advice"]["trades"]
    for t in trades:
        assert t["give"] in my and t["get"] in opp + other
        partner_change = z[str(t["give"])]["total"] - z[str(t["get"])]["total"]
        assert partner_change >= -cfg["advice_fair_tol"] - 1e-9  # no fleecing
        assert t["gain"] >= cfg["advice_min_gain"]
    assert len({t["give"] for t in trades}) == len(trades)  # each of my players offered once


def test_no_trade_asks_for_a_superstar_for_a_scrub():
    df = players_fixture(60, seed=6)
    my, other = ids(df, 0, 13), ids(df, 13, 26)
    out = advise(df, {"my": my, "opp": [], "other": other}, with_schedule=False)
    z = out["z"]
    best_other = max(other, key=lambda i: z[str(i)]["total"])
    worst_mine = min(my, key=lambda i: z[str(i)]["total"])
    assert not any(t["get"] == best_other and t["give"] == worst_mine for t in out["advice"]["trades"])


def test_punt_suggested_for_a_hopeless_category():
    df = players_fixture(150, seed=7)
    my = ids(df, 0, 13)
    df.loc[my, "ftm"] = df.loc[my, "fta"] * 0.45  # dreadful free-throw shooters
    out = advise(df, {"my": my, "opp": [], "other": []}, with_schedule=False)
    cats = [s["cat"] for s in out["advice"]["strategy"]]
    assert "FT_PCT" in cats
    assert out["advice"]["teamZ"]["FT_PCT"] <= web_config()["advice_punt_threshold"]
    # Already punting it: no repeat suggestion
    again = advise(df, {"my": my, "opp": [], "other": []}, punt=["FT_PCT"], with_schedule=False)
    assert "FT_PCT" not in [s["cat"] for s in again["advice"]["strategy"]]


def test_matchup_classification_is_consistent():
    df = players_fixture(150, seed=8)
    out = advise(df, {"my": ids(df, 0, 13), "opp": ids(df, 13, 26), "other": []})
    m, safe = out["advice"]["matchup"], web_config()["advice_safe"]
    by_cat = {c["cat"]: c["pWin"] for c in m["cats"]}
    assert all(by_cat[c] >= safe for c in m["safe"])
    assert all(by_cat[c] <= 1 - safe for c in m["lost"])
    assert not set(m["safe"]) & set(m["targets"]) and not set(m["lost"]) & set(m["targets"])
    assert sum(m["expected"]) == pytest.approx(len(config.CATEGORIES))


def test_no_roster_no_advice():
    df = players_fixture(40)
    out = advise(df, {"my": [], "opp": [], "other": []})["advice"]
    assert out["moves"] == [] and out["trades"] == [] and out["matchup"] is None


def test_never_suggests_dropping_a_star():
    """A stacked team's best player can have the lowest need-weighted fit; he must not be the drop."""
    df = players_fixture(150, seed=21)
    my = ids(df, 0, 13)
    star = my[0]
    df.loc[my, ["pts", "reb", "ast", "stl", "blk", "fg3m"]] *= 1.8  # dominant everywhere...
    df.loc[star, "tov"] = 6.0  # ...but the star turns it over a lot
    out = advise(df, {"my": my, "opp": ids(df, 13, 26), "other": ids(df, 26, 100)})
    z = out["z"]
    lowest = min(my, key=lambda i: z[str(i)]["total"])
    for m in out["advice"]["moves"]:
        assert m["drop"] == lowest and m["drop"] != star
