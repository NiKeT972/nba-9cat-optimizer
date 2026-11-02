"""Matchup Simulator: Monte Carlo of this week's head-to-head."""

import pandas as pd
import streamlit as st

from app.charts import category_radar, score_distribution_bars, win_probability_bars
from app.common import data_or_stop, load_logs, player_picker, rosters, schedule_or_warn, settings
from src import config
from src.simulation.matchup import simulate_matchup
from src.valuation.decisions import start_sit, week_window

st.title("Matchup Simulator")
players = data_or_stop()
sched = schedule_or_warn()
if sched is None:
    st.stop()
s = settings()
r = rosters()

c1, c2 = st.columns(2)
with c1:
    my_ids = player_picker("My roster", players, r[config.MY_TEAM], "sim_me")
with c2:
    opp_ids = player_picker("Opponent roster", players, r[config.OPPONENT], "sim_opp")

c1, c2, c3 = st.columns(3)
n_sims = c1.select_slider("Simulations", [1_000, 5_000, 10_000, 25_000], value=config.SIM_COUNT)
skip_inactive = c2.toggle("Skip inactive players", value=True)
wtd = c3.toggle("Include stats already banked this week", value=True)

if not my_ids or not opp_ids:
    st.info("Pick both rosters (saved rosters from **My Team** are pre-filled).")
    st.stop()

start, end = week_window(s["as_of"], s["include_today"])
st.caption(f"Simulating {start:%a %b %d} – {end:%a %b %d}")


@st.cache_data(show_spinner="Simulating…")
def run(my: tuple, opp: tuple, n: int, as_of: pd.Timestamp, today: bool, inactive: bool, use_wtd: bool, punt: tuple):
    return simulate_matchup(players, list(my), list(opp), sched, as_of, logs=load_logs(config.SEASON), n_sims=n,
                            include_today=today, exclude_inactive=inactive, use_week_to_date=use_wtd)


res = run(tuple(my_ids), tuple(opp_ids), n_sims, s["as_of"], s["include_today"], skip_inactive, wtd, s["punt"])

m1, m2, m3, m4 = st.columns(4)
m1.metric("Expected score", f"{res.expected_score[0]:.1f} – {res.expected_score[1]:.1f}")
m2.metric("Win probability", f"{res.p_win_matchup:.0%}", help=f"Tie: {res.p_tie_matchup:.0%}")
m3.metric("My games left", res.my_games)
m4.metric("Their games left", res.opp_games)

if res.targets:
    st.markdown("**Swing categories worth targeting:** " + ", ".join(
        f"{c} ({res.categories.loc[c, 'p_win']:.0%})" for c in res.targets))
else:
    st.markdown("**No close categories** — every category is at least 75/25 one way.")

c1, c2 = st.columns(2)
with c1:
    st.plotly_chart(win_probability_bars(res.categories), use_container_width=True, theme="streamlit")
with c2:
    my_ss = start_sit(players, my_ids, sched, s["as_of"], s["include_today"])
    opp_ss = start_sit(players, opp_ids, sched, s["as_of"], s["include_today"])
    weekly = {
        "Me": my_ss[[f"wk_{c}" for c in config.CATEGORIES]].sum().rename(lambda x: x[3:]),
        "Opponent": opp_ss[[f"wk_{c}" for c in config.CATEGORIES]].sum().rename(lambda x: x[3:]),
    }
    st.plotly_chart(category_radar(weekly, "Projected weekly strength (z × starts)"),
                    use_container_width=True, theme="streamlit")

c1, c2 = st.columns([2, 3])
with c1:
    st.plotly_chart(score_distribution_bars(res.score_distribution), use_container_width=True, theme="streamlit")
with c2:
    t = res.categories.copy()
    pct = t.index.isin(["FG%", "FT%"])
    show = pd.DataFrame({
        "Me": [f"{v:.3f}" if p else f"{v:.1f}" for v, p in zip(t["my_mean"], pct)],
        "Opponent": [f"{v:.3f}" if p else f"{v:.1f}" for v, p in zip(t["opp_mean"], pct)],
        "Win": t["p_win"].map("{:.0%}".format),
        "Tie": t["p_tie"].map("{:.0%}".format),
        "Median margin": [f"{v:+.3f}" if p else f"{v:+.0f}" for v, p in zip(t["median_margin"], pct)],
        "Close": t["close"].map({True: "◆ target", False: ""}),
    }, index=t.index)
    st.markdown("**Category detail** (weekly totals, mean of simulations)")
    st.dataframe(show, use_container_width=True)
st.caption(f"{res.n_sims:,} simulated weeks. Each player-game: Gamma role shock × Poisson counting stats; "
           "shots simulated as attempts → makes. TO margin is flipped so positive is always good for you.")
