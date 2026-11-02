# NBA 9-Cat Optimizer

**Projections, z-score valuation and Monte Carlo matchup simulation for 9-category head-to-head fantasy basketball.**

Built for my own 12-team, 9-cat H2H league (PTS, REB, AST, STL, BLK, 3PM, FG%, FT%, TO), and used every week to set
lineups, pick up free agents, judge trades and see how this week's matchup is likely to go.

- **Live app:** https://nba-9cat-optimizer.vercel.app (rankings, my team, waivers, trades, matchup simulator, backtest)

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

**Data refresh.** stats.nba.com blocks most cloud IPs, so nothing downloads at request time. Refresh locally and
push (or let the scheduled [`refresh-data`](.github/workflows/refresh-data.yml) Action try):

```bash
python -m src.data.pipeline --force    # latest games
python -m app.static_site              # rebuild public/
git add data/raw public && git commit -m "Refresh data" && git push   # Vercel redeploys
```

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
