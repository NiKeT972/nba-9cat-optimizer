"""Tests for player/team grades, injury status and return dates in web/core.js."""

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
  const rows = NC.addGrades(NC.valuePlayers(players, cfg, []), cfg, []);
  const by = NC.byId(rows);
  let out;
  if (fn === "grades") out = rows.map((r) => ({ id: r.id, rank: r.rank, grade: r.grade, formGrade: r.formGrade }));
  if (fn === "gradeForRank") out = args.ranks.map((k) => NC.gradeForRank(k, args.pool, cfg.grade_bands));
  if (fn === "teamGrade") out = args.pcts.map((x) => NC.teamGrade(x, cfg.team_grade_bands));
  if (fn === "status") out = args.ids.map((i) => NC.injuryStatus(by.get(i), cfg, args.notes?.[i] || null));
  if (fn === "advice") out = NC.injuryAdvice(by.get(args.id), cfg, args.opts);
  if (fn === "sim") out = NC.simulateMatchup(rows, args.my, args.opp, args.schedule, args.today, cfg,
      { nSims: 500, returns: args.returns ? new Map(Object.entries(args.returns).map(([k, v]) => [Number(k), v])) : null }).myGames;
  process.stdout.write(JSON.stringify(out));
});
"""


def run(fn, df, cfg_override=None, **args):
    payload = json.dumps({"fn": fn, "players": to_js_full(df), "cfg": {**web_config(), **(cfg_override or {})}, "args": args})
    res = subprocess.run([NODE, "-e", RUNNER, str(CORE)], input=payload, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def to_js_full(df):
    rows = to_js(df)
    for r in rows:
        extra = df.loc[r["id"]]
        r["days_since_game"] = int(extra.get("days_since_game", 1))
        r["missed"] = int(extra.get("missed", 0))
        r["form"] = extra.get("form") if isinstance(extra.get("form"), dict) else None
    return rows


def fixture(n=200, seed=31):
    df = players_fixture(n, seed=seed)
    df["days_since_game"] = 1
    df["missed"] = 0
    df["form"] = None
    return df


def test_grade_bands_follow_rank_within_the_league_pool():
    pool = 156
    ranks = [1, 4, 5, 13, 40, 78, 150, 156, 157, 300]

    def expected(rank):  # reference implementation straight from config.GRADE_BANDS
        return next((g for g, cut in config.GRADE_BANDS if rank / pool <= cut), "F")

    grades = run("gradeForRank", fixture(20), ranks=ranks, pool=pool)
    assert grades == [expected(k) for k in ranks]
    assert grades[0] == "A+" and grades[-1] == "F" and grades[-3] == "D"  # last rosterable player is a D
    # the same rank is a better grade in a bigger league
    small = run("gradeForRank", fixture(20), ranks=[60], pool=100)[0]
    big = run("gradeForRank", fixture(20), ranks=[60], pool=200)[0]
    assert config.GRADE_BANDS and [g for g, _ in config.GRADE_BANDS].index(big) < [g for g, _ in config.GRADE_BANDS].index(small)


def test_season_grades_are_monotonic_in_rank():
    order = [g for g, _ in config.GRADE_BANDS] + ["F"]
    rows = sorted(run("grades", fixture()), key=lambda r: r["rank"])
    idx = [order.index(r["grade"]) for r in rows]
    assert idx == sorted(idx) and rows[0]["grade"] == "A+"


def test_hot_form_beats_season_grade_and_missing_form_has_no_grade():
    df = fixture()
    mid = int(df.index[150])
    hot = {k: float(df.loc[mid, k]) * 2.2 for k in ["pts", "reb", "ast", "stl", "blk", "fg3m", "fgm", "fga", "ftm", "fta"]}
    df.at[mid, "form"] = {"games": 5, "min": 34.0, "tov": float(df.loc[mid, "tov"]), **hot}
    rows = {r["id"]: r for r in run("grades", df)}
    order = [g for g, _ in config.GRADE_BANDS] + ["F"]
    assert order.index(rows[mid]["formGrade"]) < order.index(rows[mid]["grade"])
    assert rows[int(df.index[0])]["formGrade"] is None


def test_team_grades():
    assert run("teamGrade", fixture(20), pcts=[0.9, 0.6, 0.5, 0.45, 0.1]) == ["A+", "A-", "B-", "C", "F"]


def test_injury_status_inference_and_override():
    df = fixture(40)
    a, b, c = (int(i) for i in df.index[:3])
    df.loc[a, ["active", "days_since_game"]] = [False, 15]
    df.loc[b, "missed"] = 3
    out = run("status", df, ids=[a, b, c], notes={str(c): {"status": "Day-to-day"}})
    assert out[0]["level"] == "out" and "15 days" in out[0]["label"]
    assert out[1]["level"] == "risk" and "3" in out[1]["label"]
    assert out[2]["label"] == "Day-to-day"


def test_injury_advice_stash_star_drop_scrub():
    df = fixture()
    star, scrub = int(df.index[0]), int(df.index[-1])
    rows = {r["id"]: r for r in run("grades", df)}
    star = min(rows, key=lambda i: rows[i]["rank"])
    scrub = max(rows, key=lambda i: rows[i]["rank"])
    df.loc[[star, scrub], ["active", "days_since_game"]] = [False, 30]
    assert run("advice", df, id=star, opts={"freeIL": 1})["action"] == "Move to IL"
    assert run("advice", df, id=star, opts={"freeIL": 0})["action"] == "Hold"
    assert run("advice", df, id=scrub, opts={"freeIL": 1})["action"] == "Consider dropping"


def test_return_date_counts_games_only_from_return():
    df = fixture(60)
    hurt = int(df.index[0])
    df.loc[hurt, "active"] = False
    sched = sched_js(schedule_fixture())
    opp = [int(df.index[1])]
    team = df.loc[hurt, "team"]
    after_return = sum(1 for g in sched if "2026-11-06" <= g["d"] <= "2026-11-08" and team in (g["h"], g["a"]))
    this_week = sum(1 for g in sched if "2026-11-04" <= g["d"] <= "2026-11-08" and team in (g["h"], g["a"]))
    assert 0 < after_return < this_week  # the fixture must actually test something
    assert run("sim", df, my=[hurt], opp=opp, schedule=sched, today="2026-11-04") == 0
    assert run("sim", df, my=[hurt], opp=opp, schedule=sched, today="2026-11-04", returns={str(hurt): "2026-11-06"}) == after_return
