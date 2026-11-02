"""Tests for the league view (team ratings, power rankings) and waiver claims in web/core.js."""

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
  const { fn, players, cfg, args } = JSON.parse(input);
  const rows = NC.valuePlayers(players, cfg, []);
  let out;
  if (fn === "league") out = NC.leagueTable(rows, args.teams, args.schedule, args.today, cfg);
  if (fn === "advise") out = NC.advise(rows, args.rosters, args.schedule, args.today, cfg, {});
  if (fn === "games") out = NC.dailyLineups(rows, args.ids, args.schedule, args.today, true, cfg,
      args.from ? new Map(Object.entries(args.from).map(([k, v]) => [Number(k), v])) : null).length;
  process.stdout.write(JSON.stringify(out));
});
"""


def run(fn, df, **args):
    payload = json.dumps({"fn": fn, "players": to_js(df), "cfg": web_config(), "args": args})
    res = subprocess.run([NODE, "-e", RUNNER, str(CORE)], input=payload, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def league_teams(df, n_teams=6, size=13):
    ids = [int(i) for i in df.index]
    return [{"id": f"t{k}", "name": f"Team {k}", "players": ids[k * size:(k + 1) * size]} for k in range(n_teams)]


SCHED = sched_js(schedule_fixture())


def test_all_play_records_are_consistent():
    df = players_fixture(120, seed=11)
    teams = league_teams(df)
    table = run("league", df, teams=teams, schedule=SCHED, today="2026-11-04")
    n_cats, n = len(config.CATEGORIES), len(teams)
    for t in table:
        rec = t["perGameRecord"]
        assert rec["w"] + rec["l"] + rec["t"] == n_cats * (n - 1)
    # every category result is counted once for each side: total wins == total losses
    assert sum(t["perGameRecord"]["w"] for t in table) == sum(t["perGameRecord"]["l"] for t in table)
    assert [t["rank"] for t in table] == list(range(1, n + 1))
    assert sorted(t["weekRank"] for t in table) == list(range(1, n + 1))


def test_identical_teams_tie_everything():
    df = players_fixture(30, seed=12)
    same = [int(i) for i in df.index[:13]]
    table = run("league", df, teams=[{"id": "a", "name": "A", "players": same}, {"id": "b", "name": "B", "players": same}],
                schedule=SCHED, today="2026-11-04")
    for t in table:
        assert t["perGameRecord"]["t"] == len(config.CATEGORIES) and t["perGameRecord"]["pct"] == 0.5


def test_stronger_team_ranks_first_and_category_ranks_follow_stats():
    df = players_fixture(120, seed=13)
    teams = league_teams(df, n_teams=4)
    strong = teams[2]["players"]
    df.loc[strong, ["pts", "reb", "ast", "stl", "blk", "fg3m"]] *= 2.0
    table = run("league", df, teams=teams, schedule=SCHED, today="2026-11-04")
    assert table[0]["id"] == "t2"
    for c in config.CATEGORIES:
        lines = sorted(table, key=lambda t: t["catRank"][c])
        vals = [t["perGame"][c] for t in lines]
        assert vals == (sorted(vals) if c == "TOV" else sorted(vals, reverse=True)), c  # fewer TO ranks higher


def test_teams_without_players_are_skipped():
    df = players_fixture(40, seed=14)
    teams = league_teams(df, n_teams=2) + [{"id": "empty", "name": "Empty", "players": []}]
    assert {t["id"] for t in run("league", df, teams=teams, schedule=SCHED, today="2026-11-04")} == {"t0", "t1"}


def test_waiver_claim_loses_games_before_it_clears():
    df = players_fixture(30, seed=15)
    pid = int(df.index[0])  # team AAA plays on 2026-11-04 (today) in the fixture schedule
    full = run("games", df, ids=[pid], schedule=SCHED, today="2026-11-04")
    claimed = run("games", df, ids=[pid], schedule=SCHED, today="2026-11-04", **{"from": {str(pid): "2026-11-05"}})
    assert claimed < full


def test_advisor_marks_waiver_claims():
    df = players_fixture(150, seed=3)
    my = [int(i) for i in df.index[:13]]
    df.loc[my, "blk"] = 0.05
    target = int(df.index[140])
    df.loc[target, ["blk", "reb", "fgm", "fga"]] = [3.5, 11.0, 7.0, 11.0]
    rosters = {"my": my, "opp": [int(i) for i in df.index[13:26]], "other": [int(i) for i in df.index[26:100]],
               "waivers": [target]}
    moves = run("advise", df, rosters=rosters, schedule=SCHED, today="2026-11-04")["moves"]
    top = next(m for m in moves if m["add"] == target)
    assert top["claim"] is True and top["claimDate"] == "2026-11-05"
    free = [m for m in moves if m["add"] != target]
    assert all(m["claim"] is False for m in free)
