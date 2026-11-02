"""Waivers: free agents ranked by how much they help my weakest categories."""

import streamlit as st

from app.common import CAT_LABEL_COLS, data_or_stop, rosters, schedule_or_warn, settings, style_z
from src import config
from src.data.roster import rostered_ids
from src.valuation.decisions import games_remaining, rank_waivers, waiver_need_weights, week_window

st.title("Waivers")
players = data_or_stop()
s = settings()
r = rosters()
my_ids = [i for i in r[config.MY_TEAM] if i in players.index]
if not my_ids:
    st.info("Enter your roster on the **My Team** page first.")
    st.stop()
if not r[config.OTHER_ROSTERED]:
    st.warning("You haven't entered the rest of your league's rosters (My Team → Edit rosters), so every "
               "player not on your or your opponent's team counts as a free agent.")

c1, c2, c3 = st.columns([2, 1, 1])
mode = c1.segmented_control("Rank by", ["Season-long fit", "This week (streaming)"], default="Season-long fit")
only_active = c2.toggle("Active only", value=True)
top_n = c3.number_input("Show", 10, 200, config.WAIVER_TOP_N, step=5)

games_left = None
if mode == "This week (streaming)":
    sched = schedule_or_warn()
    if sched is not None:
        start, end = week_window(s["as_of"], s["include_today"])
        games_left = games_remaining(players, sched, start, end)

fa = rank_waivers(players, my_ids, rostered_ids(r), punt=s["punt"], games_left=games_left,
                  only_active=only_active, top_n=int(top_n))
need = waiver_need_weights(players, my_ids, s["punt"])

st.markdown("**Category weights for your team** — weak categories count up to ×2, strong ones toward ×0:")
st.markdown(" · ".join(f"{config.CAT_LABELS[c]} ×{need[c]:.2f}" for c in config.CATEGORIES))

cols = ["name", "team", "fit", "net_gain", "drop", "helps", "z_total", *CAT_LABEL_COLS]
if games_left is not None:
    cols = ["name", "team", "games_left", "weekly_fit", "fit", "net_gain", "drop", "helps", "z_total", *CAT_LABEL_COLS]
out = fa[cols].rename(columns={**CAT_LABEL_COLS, "z_total": "Raw value"})
z_cols = [c for c in ["weekly_fit", "fit", "net_gain", "Raw value", *CAT_LABEL_COLS.values()] if c in out]
st.dataframe(
    style_z(out, z_cols), hide_index=True, use_container_width=True, height=640,
    column_config={"name": "Player", "team": "Team", "fit": "Fit", "net_gain": "Net gain",
                   "drop": "Suggested drop", "helps": "Helps most", "games_left": "Games left",
                   "weekly_fit": "Weekly fit"},
)
st.caption("Fit = Σ (category weight × player z). Net gain = fit − fit of your lowest-value player (the suggested drop).")
