"""My Team: roster input (persisted), start/sit for this week, team category profile."""

import json

import pandas as pd
import streamlit as st

from app.charts import diverging_category_bars
from app.common import (
    CAT_LABEL_COLS, ROSTER_KEYS, data_or_stop, player_picker, roster_json, rosters, save_rosters,
    schedule_or_warn, settings, style_z,
)
from src import config
from src.valuation.decisions import daily_lineups, start_sit, week_window
from src.valuation.team import category_need_weights, team_category_z

st.title("My Team")
players = data_or_stop()
s = settings()
current = rosters()

# ---------------------------------------------------------------------------
# Roster input
# ---------------------------------------------------------------------------
with st.expander("Edit rosters", expanded=not current[config.MY_TEAM]):
    with st.form("roster_form"):
        picks = {
            config.MY_TEAM: player_picker("My team", players, current[config.MY_TEAM], "pick_me",
                                          max_selections=config.ROSTER_SPOTS + 3),
            config.OPPONENT: player_picker("This week's opponent", players, current[config.OPPONENT], "pick_opp",
                                           max_selections=config.ROSTER_SPOTS + 3),
            config.OTHER_ROSTERED: player_picker(
                "Everyone else who is rostered in my league", players, current[config.OTHER_ROSTERED], "pick_other",
                help="Anyone not in these three boxes is treated as a free agent on the Waivers page."),
        }
        if st.form_submit_button("Save rosters", type="primary"):
            save_rosters(picks, players)
            st.success("Saved." if config.ROSTER_SAVE_ENABLED else "Saved for this session.")
            st.rerun()
    c1, c2 = st.columns(2)
    c1.download_button("Download rosters (JSON)", roster_json(players), "my_rosters.json", "application/json")
    up = c2.file_uploader("Load rosters (JSON)", type="json", label_visibility="collapsed")
    if up is not None:
        raw = json.loads(up.read())
        loaded = {k: [int(p["player_id"]) for p in raw.get(k, []) if int(p["player_id"]) in players.index]
                  for k in ROSTER_KEYS}
        save_rosters(loaded, players)
        st.rerun()

my_ids = [i for i in current[config.MY_TEAM] if i in players.index]
if not my_ids:
    st.info("Add your players above to see start/sit and your category profile.")
    st.stop()

# ---------------------------------------------------------------------------
# Start / sit
# ---------------------------------------------------------------------------
sched = schedule_or_warn()
if sched is not None:
    start, end = week_window(s["as_of"], s["include_today"])
    st.subheader(f"Start / sit · {start:%a %b %d} – {end:%a %b %d}")
    ss = start_sit(players, my_ids, sched, s["as_of"], s["include_today"])
    total_games, started = int(ss["games_left"].sum()), int(ss["games_started"].sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Games left", total_games)
    m2.metric("Games you can start", started, help=f"{config.ACTIVE_LINEUP_SLOTS} active slots per day")
    m3.metric("Games lost to bench", total_games - started)

    table = ss[["name", "team", "active", "z_total", "games_left", "games_started", "weekly_value_started"]]
    st.dataframe(
        style_z(table.rename(columns={"z_total": "Value / game", "weekly_value_started": "Weekly value"}),
                ["Value / game", "Weekly value"]),
        hide_index=True, use_container_width=True,
        column_config={"name": "Player", "team": "Team", "active": "Active",
                       "games_left": "Games left", "games_started": "Starts"},
    )
    st.caption("Weekly value = per-game z-value × games you can actually start (positions are not modelled).")

    lineup = daily_lineups(players, my_ids, sched, s["as_of"], s["include_today"])
    if not lineup.empty:
        grid = lineup.assign(day=lineup["date"].dt.strftime("%a %d"),
                             slot=lineup["start"].map({True: "Start", False: "Bench"}))
        pivot = grid.pivot_table(index="name", columns="day", values="slot", aggfunc="first").fillna("")
        pivot = pivot[sorted(pivot.columns, key=lambda d: pd.to_datetime(grid.loc[grid["day"] == d, "date"].iloc[0]))]
        with st.expander("Daily lineup"):
            st.dataframe(pivot, use_container_width=True)

# ---------------------------------------------------------------------------
# Category profile
# ---------------------------------------------------------------------------
st.subheader("Category profile")
team_z = team_category_z(players, my_ids)
need = category_need_weights(team_z, s["punt"])
c1, c2 = st.columns([3, 2])
with c1:
    st.plotly_chart(
        diverging_category_bars(team_z.rename(index=config.CAT_LABELS), "Team strength (sum of per-game z)"),
        use_container_width=True, theme="streamlit")
with c2:
    st.markdown("**Weakest categories**")
    live = team_z.drop(list(s["punt"]), errors="ignore").sort_values()
    for cat, z in live.head(3).items():
        st.markdown(f"- **{config.CAT_LABELS[cat]}**: {z:+.2f} z (waiver weight ×{need[cat]:.2f})")
    st.caption("A league-average 13-man roster sums to about 0 in every category.")

st.subheader("Roster")
roster_table = players.loc[my_ids, ["name", "team", "z_total", *CAT_LABEL_COLS]].sort_values("z_total", ascending=False)
st.dataframe(style_z(roster_table.rename(columns={**CAT_LABEL_COLS, "z_total": "Value"}),
                     ["Value", *CAT_LABEL_COLS.values()]),
             hide_index=True, use_container_width=True, column_config={"name": "Player", "team": "Team"})
