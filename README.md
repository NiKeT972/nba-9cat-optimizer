# NBA 9-Cat Optimizer

**Projections, z-score valuation and Monte Carlo matchup simulation for 9-category head-to-head fantasy basketball.**

Built for my own 12-team, 9-cat H2H league (PTS, REB, AST, STL, BLK, 3PM, FG%, FT%, TO), and used every week to set
lineups, pick up free agents, judge trades and see how this week's matchup is likely to go.

- **Live app:** https://nba-9cat-optimizer.vercel.app (advisor, league power rankings, player rankings, waivers, trades, matchup simulator, backtest)

<!-- Screenshots: run the app with real data, then `python scripts/capture_screenshots.py`. -->
| Player rankings | Matchup simulator |
|---|---|
| ![Player rankings](docs/screenshots/rankings.png) | ![Matchup simulator](docs/screenshots/matchup.png) |
| **Waivers** | **Trade analyzer** |
| ![Waivers](docs/screenshots/waivers.png) | ![Trade analyzer](docs/screenshots/trade.png) |

---

## The problem

Nine-category head-to-head is a weekly best-of-nine. Three things make the usual "rank by points" view misleading:

1. **Categories have different scales.** One extra steal is worth far more than one extra point.
2. **Percentages depend on volume.** A 60% shooter on 2 attempts barely moves a team's FG%; a 55% shooter on 20 does.
3. **You don't need to win everything.** You need five categories *this week*, against *this opponent*, with the
   games your players actually have left.

## Approach

```
nba_api ──► tidy game logs ──► projections ──► z-score values ──► start/sit · waivers · trades
 (cached     (one row per        (recency blend   (top-156 pool,                │
  parquet)    player-game)        + shrinkage)     volume-weighted %)            ▼
                                                                 Monte Carlo matchup (10,000 weeks)
```

| Module | What it does |
|---|---|
| `src/data` | `nba_api` downloads with throttling, exponential back-off and a parquet cache; cleaning into tidy tables; roster JSON |
| `src/projections` | Per-game projections; walk-forward backtest |
| `src/valuation` | Z-scores, punt mode, team totals, start/sit, waivers, trade analyzer |
| `src/simulation` | Vectorised Monte Carlo of the weekly matchup |
| `app/` | Streamlit app (5 tools + method page), Plotly charts, web-app builder |
| `web/` | Deployed web app: browser engine (`core.js`, parity-tested against Python) and UI |
| `src/config.py` | **Every** tunable number: weights, shrinkage, pool size, simulation count, … |

## The maths, simply

### 1. Projections: recent form, but not overreacting

For each player, stats are converted to **per-minute rates** over three windows (last 7 days, last 15 days, season),
blended with configurable weights (default 20% / 30% / 50%), then multiplied by **projected minutes**. Per-minute
rates mean a role change (20 → 36 minutes) shows up immediately.

Small samples are **shrunk** (empirical-Bayes style). The season rate is pulled toward a prior, either last season's
rate or the league average for rookies, as if that prior were worth 300 minutes of play:

```
rate_season = (this season's total + 300 × prior rate) / (this season's minutes + 300)
```

Each short window is shrunk toward the season rate the same way. After one 40-point game a rookie projects
around 17, not 40; after 20 such games, about 34. Minutes get the same treatment.

### 2. Z-scores: putting nine categories on one scale

For each category: `z = (player value − pool mean) / pool standard deviation`. Turnovers are negated (fewer is better).
The **pool** is the top 156 players (12 teams × 13 roster spots). Since who is "top 156" depends on the z-scores,
the pool is re-selected and the z-scores recomputed until it stops changing.

**FG% and FT% are never z-scored as raw percentages.** Instead:

```
impact = (player% − league%) × attempts      =  "extra makes vs. an average shooter on the same shots"
```

and the *impact* is z-scored. Because impact is additive, team totals are just sums. **Punt mode** drops chosen
categories from the total and from the pool selection.

### 3. Decisions
- **Start/sit:** weekly value = per-game value × games you can actually start this week (the schedule plus a
  10-slot daily lineup cap, so a 14th player with a game on a crowded night counts for nothing).
- **Waivers:** sum your roster's z in each category, then weight each category by need,
  `w = 2 / (1 + e^(team_z / 3))`: about 2 for your weakest categories, 1 for average, about 0 for categories you
  dominate or punt. Free agents are ranked by `Σ w × z`, paired with a suggested drop, with an optional
  games-this-week sort for streaming.
- **Trades:** team category totals before and after (percentages recomputed from makes/attempts), z changes,
  a need-weighted score, and an automatic drop when you receive more players than you send.

### 4. Matchup simulation

Each remaining player-game is simulated 10,000 times:

- a shared **role shock** `s ~ Gamma(mean 1, CV fitted to the player's recent minutes)` scales every stat in that
  game. That gives the over-dispersion real box scores have, and the correlation between a player's stats;
- REB, AST, STL, BLK and TO are drawn `~ Poisson(s × projection)`;
- shooting is drawn as **attempts then makes**: `FGA ~ Poisson`, `FGM ~ Binomial(FGA, FG%)`,
  `3PM ~ Binomial(FGM, 3PM share)`, and the same for free throws, so `PTS = 2·FGM + 3PM + FTM` holds in every
  simulated game.

Totals are summed over started games only, added to stats already banked this week, and compared category by
category. The output is the win probability per category, the expected category score (e.g. **5.3 – 3.7**), the
matchup win probability, the score distribution, and the **close categories** (25–75%) worth targeting.

### 5. Advisor: roster decisions in plain language

The **Advisor** tab turns everything above into ranked recommendations, each with a one-click action:

- **Matchup:** the expected score and win chance, which categories to chase (close), which are locked in, and
  which are out of reach so you don't waste moves on them.
- **Waiver moves:** the best add/drop pairs by need-weighted value. Each one is **re-simulated against your actual
  opponent**, so you see the change in this week's expected categories and win probability next to the long-term gain.
- **Lineup:** starts lost to the daily slot cap, and players who have stopped playing (likely injured).
- **Trade ideas:** 1-for-1 offers that raise your need-weighted value while the partner loses at most
  `ADVICE_TRADE_FAIR_TOL` of raw value, so they're offers a real manager could accept.
- **Strategy:** categories so far behind (`ADVICE_PUNT_THRESHOLD`) that you should either fix them or punt them.

All thresholds live in `src/config.py`; behaviour is covered by `tests/test_web_advisor.py`.

### 6. League view: team ratings and weekly power rankings

Enter every team in the league (name and players; paste straight from Yahoo/ESPN), mark yourself and this week's
opponent, and list players on waivers. The **League** tab then shows:

- **Power rankings by all-play record:** each team's projected per-game line compared with every other team in all
  nine categories (wins-losses-ties). A second ranking uses each roster's projected totals for the games it can still
  start **this week**. ▲▼ show movement since last week.
- **Category ratings:** every team ranked 1-12 in each category, plus projected per-game and this-week stat lines.
- **Availability:** players are tagged by owner, **Waivers** or **FA** everywhere. The advisor says *Add* for free
  agents and *Put in a claim* for waiver players, and only counts a claimed player's games after the claim clears
  (`WAIVER_CLAIM_DAYS`).

### 7. Multiple leagues, IL, grades and injury reports

- **Multiple leagues:** switch between leagues from the header. Each league has its own teams, waivers, punts and
  settings: **teams, roster spots (e.g. 13 or 15), IL spots and starters per day**. Valuation adapts: z-scores are
  measured against that league's rosterable pool (teams × roster spots), and start/sit, the simulator and the
  advisor use its starters per day.
- **IL:** every team has an IL list. IL players don't use a roster spot and never start, but they're owned, not free
  agents. The advisor suggests moving injured players to a free IL spot.
- **Grades (A+ to F):** each player's 9-cat value is ranked within the league's rosterable pool and mapped to a letter
  (`GRADE_BANDS`, e.g. top 3% = A+, last rosterable = D, waiver-level = F). **Form** applies the same scale to the last
  `FORM_DAYS` days. Teams are graded on their all-play record, and every team gets a report card.
- **Injury report:** nba_api has no official injury feed, so status is inferred from box scores (no game in
  `INACTIVE_AFTER_DAYS`, or `INJURY_MISSED_GAMES`+ team games missed in a row). Each injured player gets a card with
  his last game, games missed, projection vs recent form, last 5 box scores and a stash / hold / drop recommendation.
  You can add the official status and an expected return date; the simulator then counts his games from that day.

## Backtest

`python -m src.projections.backtest` runs a **walk-forward** test on last season: every week it projects each player
from games before that date, and scores against what he averaged the following week. Players projected under
20 MPG are excluded. FG%/FT% errors are attempt-weighted. The model is compared with two naive baselines.

<!-- BACKTEST:START -->
| Category | MAE: blend | MAE: season_avg | MAE: last7_avg | Blend vs season avg |
|---|---:|---:|---:|---:|
| PTS | 3.557 | 3.665 | 4.476 | +3.0% |
| REB | 1.351 | 1.372 | 1.744 | +1.5% |
| AST | 1.031 | 1.064 | 1.292 | +3.1% |
| STL | 0.517 | 0.523 | 0.679 | +1.2% |
| BLK | 0.375 | 0.374 | 0.474 | -0.1% |
| 3PM | 0.691 | 0.697 | 0.885 | +0.8% |
| FG% | 6.88 pp | 7.02 pp | 9.59 pp | +2.0% |
| FT% | 10.64 pp | 10.88 pp | 17.62 pp | +2.2% |
| TO | 0.672 | 0.687 | 0.865 | +2.2% |

_2025-26 regular season, 22 weekly projection dates, 4,496 player-weeks (players projected >= 20 MPG). FG%/FT% errors are attempt-weighted, in percentage points. Positive improvement = lower error than the naive season average._
<!-- BACKTEST:END -->

## How to run

```bash
git clone https://github.com/NiKeT972/nba-9cat-optimizer && cd nba-9cat-optimizer
python -m venv .venv && source .venv/bin/activate      # Python 3.11+
pip install -r requirements.txt

make data        # download 2026-27 + 2025-26 game logs and the schedule (cached as parquet)
make app         # streamlit run app/streamlit_app.py
make test        # pytest
make backtest    # walk-forward backtest -> reports/
make site        # web app -> public/ (deployed on Vercel)
```

No API keys are needed: `nba_api` reads the public stats.nba.com endpoints. Optional settings are in
[`.env.example`](.env.example).

**Weekly routine:** `make refresh` → open **My Team** (rosters persist in `data/my_rosters.json`) → set this week's
opponent → **Matchup Simulator** for target categories → **Waivers** in streaming mode.

## Deployment

**Vercel (the live app).** The deployed app is a static, client-side web app: no server, instant
interactions, rosters saved in the browser (with JSON import/export and a paste-a-list box).

```
Python pipeline ──► public/data/app.json ──► web/core.js (browser engine) ──► web/app.js (UI)
(nba_api, projections,    (settings, projections,        (z-scores, punts, start/sit,
 backtest)                 schedule, backtest)             waivers, trades, Monte Carlo)
```

`web/core.js` is a port of the Python valuation, decision and simulation code.
[`tests/test_web_parity.py`](tests/test_web_parity.py) runs it under Node against the Python functions on the
same inputs (z-scores exactly; simulation probabilities within 3 points), so the two can't drift apart.
[`vercel.json`](vercel.json) serves `public/` with no build step.

**Data refresh (daily, on your own computer).** stats.nba.com blocks cloud servers: requests from GitHub
Actions time out (verified), so nothing downloads at request time and the refresh runs on a home connection.
`scripts/refresh.ps1` downloads the newest games in *strict* mode (a failed download is an error, never a silent
re-publish of old data), rebuilds `public/`, commits and pushes; Vercel redeploys. Register it once with Windows
Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-daily-task.ps1            # daily at 12:00
powershell -ExecutionPolicy Bypass -File scripts\install-daily-task.ps1 -Time 09:30
```

Each run is logged to `data/refresh.log`. On macOS/Linux the same steps are
`python -m src.data.pipeline --force --strict && python -m app.static_site` followed by a commit and push.
The [`refresh-data`](.github/workflows/refresh-data.yml) workflow is kept for manual runs (for example with an
`NBA_API_PROXY` secret that can reach stats.nba.com).

**Streamlit (local power-user version).** `streamlit run app/streamlit_app.py` runs the same tools in Python,
with rosters saved to `data/my_rosters.json`. It can also go on Streamlit Community Cloud (main file
`app/streamlit_app.py`, secrets `NBA_OFFLINE = "1"`, `NBA_ROSTER_SAVE = "0"`).

## Tests

`pytest` covers the z-score maths (standardisation, TO sign, iterative pool, punting), percentage handling (pool %
is makes/attempts, impact formula, volume beating raw %, zero-attempt safety), projections (no look-ahead,
shrinkage, minutes scaling), the decision tools, the simulator (means match projections, PTS identity,
over-dispersion, reproducibility) and the backtest (identical evaluation sets for every model). CI runs on every push.

## Limitations

- **No injury feed.** "Inactive" is inferred from no game in 10 days; day-to-day and returning players are not modelled.
- **No positions.** Daily lineups use the top-10 players by value and ignore position eligibility (nba_api has no
  fantasy positions).
- **Independent players.** The simulation treats players as independent; teammates' usage is not modelled.
- **Simple minutes model.** Projected minutes come from recent averages; rotation changes are only seen after they happen.
- **Z-score ranking is linear.** It ignores the H2H fact that being slightly ahead in a category matters more
  than being far ahead (the waiver need-weights and the simulator partly address this).

## Future work

- Tune projection weights and shrinkage on the backtest (time-series cross-validation) and add a gradient-boosted
  minutes model with opponent pace and rest days.
- Injury/news feed and Yahoo/ESPN/Sleeper roster import.
- Positional lineup optimisation (integer programming) for daily starts.
- Teammate-correlation in the simulator and trade suggestions searched from all rosters.
