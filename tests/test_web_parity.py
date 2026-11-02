"""Parity tests: the browser engine (web/core.js) must match the Python reference implementation.

Runs core.js under Node with the same inputs as the Python functions. Skipped if Node is
not installed (GitHub's ubuntu runners have it, so CI always runs these).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.static_site import web_config
from src import config
from src.simulation.matchup import simulate_matchup
from src.valuation.decisions import analyze_trade, rank_waivers, start_sit
from src.valuation.team import team_totals
from src.valuation.zscores import value_players
from tests.test_zscores import make_players

NODE = shutil.which("node")
CORE = Path(__file__).resolve().parents[1] / "web" / "core.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")

RUNNER = r"""
const NC = require(process.argv[1]);
let input = ""; process.stdin.on("data", (d) => (input += d)).on("end", () => {
  const { fn, players, cfg, args } = JSON.parse(input);
  const punt = args.punt || [];
  const rows = NC.valuePlayers(players, cfg, punt);
  let out;
  if (fn === "value") out = rows.map((r) => ({ id: r.id, z: r.z, total: r.total, inPool: r.inPool, iterations: rows.fit.iterations }));
  if (fn === "totals") out = NC.teamTotals(NC.byId(rows), args.ids);
  if (fn === "startsit") out = NC.startSit(rows, args.ids, args.schedule, args.today, true, cfg);
  if (fn === "waivers") out = NC.rankWaivers(rows, args.my, args.rostered, cfg, { punt }).rows;
  if (fn === "trade") out = NC.analyzeTrade(rows, args.my, args.give, args.get, cfg, { punt });
  if (fn === "sim") out = NC.simulateMatchup(rows, args.my, args.opp, args.schedule, args.today, cfg, { nSims: 20000 });
  process.stdout.write(JSON.stringify(out));
});
"""

TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def players_fixture(n: int = 120, seed: int = 5) -> pd.DataFrame:
    df = make_players(n, seed=seed)
    df["team"] = [TEAMS[i % len(TEAMS)] for i in range(n)]
    df["active"] = True
    df["cv"] = 0.25
    return df


def to_js(df: pd.DataFrame) -> list[dict]:
    cols = ["name", "team", "active", "cv", *config.PROJECTED_STATS]
    return [{"id": int(i), **{c: (bool(r[c]) if c == "active" else r[c]) for c in cols}} for i, r in df[cols].iterrows()]


def schedule_fixture() -> pd.DataFrame:
    rows, start = [], pd.Timestamp("2026-11-02")
    for d in range(7):
        day = start + pd.Timedelta(days=d)
        for k in range(0, len(TEAMS), 2):
            if (d + k) % 3:  # uneven game counts per team
                rows.append((f"g{d}{k}", day, 1, TEAMS[k], 2, TEAMS[k + 1]))
    df = pd.DataFrame(rows, columns=["game_id", "date", "home_team_id", "home_team", "away_team_id", "away_team"])
    return df


def sched_js(s: pd.DataFrame) -> list[dict]:
    return [{"d": r.date.strftime("%Y-%m-%d"), "h": r.home_team, "a": r.away_team} for r in s.itertuples()]


def run(fn: str, df: pd.DataFrame, **args):
    payload = json.dumps({"fn": fn, "players": to_js(df), "cfg": web_config(), "args": args})
    res = subprocess.run([NODE, "-e", RUNNER, str(CORE)], input=payload, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@pytest.mark.parametrize("punt", [[], ["FT_PCT", "TOV"], ["FG_PCT", "BLK", "REB"]])
def test_zscores_match_python(punt):
    df = players_fixture()
    py, stats = value_players(df, punt=punt, pool_size=config.ZSCORE_POOL_SIZE)
    js = {r["id"]: r for r in run("value", df, punt=punt)}
    for pid, row in py.iterrows():
        assert js[pid]["total"] == pytest.approx(row["z_total"], abs=1e-9)
        for c in config.CATEGORIES:
            assert js[pid]["z"][c] == pytest.approx(row[f"z_{c}"], abs=1e-9)
        assert js[pid]["inPool"] == bool(row["in_pool"])


def test_iterative_pool_matches_python_on_big_league():
    df = players_fixture(400, seed=9)
    py, stats = value_players(df)
    js = run("value", df)
    assert js[0]["iterations"] == stats.iterations
    assert {r["id"] for r in js if r["inPool"]} == set(stats.pool_ids)


def test_team_totals_match_python():
    df = players_fixture()
    ids = [int(i) for i in df.index[:13]]
    py = team_totals(df, ids)
    js = run("totals", df, ids=ids)
    for c in config.CATEGORIES:
        assert js[c] == pytest.approx(py[c], rel=1e-12)


def test_start_sit_matches_python():
    df = players_fixture()
    valued, _ = value_players(df)
    ids = [int(i) for i in df.index[:14]]
    sched = schedule_fixture()
    py = start_sit(valued, ids, sched, "2026-11-04")
    js = {r["id"]: r for r in run("startsit", df, ids=ids, schedule=sched_js(sched), today="2026-11-04")}
    for pid, r in py.iterrows():
        assert js[pid]["games"] == r["games_left"]
        assert js[pid]["started"] == r["games_started"]
        assert js[pid]["weeklyStarted"] == pytest.approx(r["weekly_value_started"], abs=1e-9)


def test_waivers_match_python():
    df = players_fixture()
    valued, _ = value_players(df, punt=["TOV"])
    my, others = [int(i) for i in df.index[:13]], [int(i) for i in df.index[13:40]]
    py = rank_waivers(valued, my, others, punt=["TOV"], top_n=None)
    js = {r["id"]: r for r in run("waivers", df, my=my, rostered=others, punt=["TOV"])}
    assert set(js) == set(py.index)
    for pid, r in py.iterrows():
        assert js[pid]["fit"] == pytest.approx(r["fit"], abs=1e-9)
        assert js[pid]["netGain"] == pytest.approx(r["net_gain"], abs=1e-9)


def test_trade_matches_python():
    df = players_fixture()
    valued, _ = value_players(df)
    my = [int(i) for i in df.index[:13]]
    give, get = [my[0]], [int(df.index[50]), int(df.index[51])]
    py = analyze_trade(valued, my, give, get)
    js = run("trade", df, my=my, give=give, get=get)
    assert js["total"] == pytest.approx(py.total_z_delta, abs=1e-9)
    assert js["needWeighted"] == pytest.approx(py.need_weighted_delta, abs=1e-9)
    assert js["dropped"] == [int(i) for i in py.dropped]
    for row in js["cats"]:
        label = config.CAT_LABELS[row["cat"]]
        assert row["zDelta"] == pytest.approx(py.categories.loc[label, "z_delta"], abs=1e-9)


def test_simulation_agrees_with_python_statistically():
    df = players_fixture()
    valued, _ = value_players(df)
    my, opp = [int(i) for i in df.index[:13]], [int(i) for i in df.index[13:26]]
    sched = schedule_fixture()
    py = simulate_matchup(valued, my, opp, sched, "2026-11-04", n_sims=20_000)
    js = run("sim", df, my=my, opp=opp, schedule=sched_js(sched), today="2026-11-04")
    assert js["myGames"] == py.my_games and js["oppGames"] == py.opp_games
    assert js["expected"][0] == pytest.approx(py.expected_score[0], abs=0.15)
    for row in js["cats"]:
        label = config.CAT_LABELS[row["cat"]]
        assert row["pWin"] == pytest.approx(py.categories.loc[label, "p_win"], abs=0.03), label
        assert row["my"] == pytest.approx(py.categories.loc[label, "my_mean"], rel=0.02, abs=1e-3), label
