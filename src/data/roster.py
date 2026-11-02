"""Persist fantasy rosters to a local JSON file.

File format (human-editable)::

    {
      "My Team":   [{"player_id": 1628983, "name": "Shai Gilgeous-Alexander"}, ...],
      "Opponent":  [...],
      "Other rostered players": [...]
    }

Players are stored by ``player_id`` (names can collide or change spelling); the name
is kept alongside purely for readability.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src import config

Rosters = dict[str, list[int]]


def load_rosters(path: Path = config.ROSTER_FILE) -> Rosters:
    """Read rosters as ``{roster_name: [player_id, ...]}``. Missing/corrupt file -> empty rosters."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {name: [int(p["player_id"]) for p in players] for name, players in raw.items()}


def save_rosters(rosters: Rosters, names: pd.Series | dict[int, str] | None = None,
                 path: Path = config.ROSTER_FILE) -> None:
    """Write rosters to JSON.

    Args:
        rosters: ``{roster_name: [player_id, ...]}``.
        names: Optional ``player_id -> name`` lookup stored alongside ids for readability.
        path: Destination file.
    """
    lookup = dict(names) if names is not None else {}
    payload = {
        roster: [{"player_id": int(pid), "name": lookup.get(pid, "")} for pid in ids]
        for roster, ids in rosters.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX: never leaves a half-written file


def rostered_ids(rosters: Rosters) -> set[int]:
    """Every player on any roster."""
    return {pid for ids in rosters.values() for pid in ids}


def ids_from_names(names: list[str], players: pd.DataFrame) -> list[int]:
    """Map display names to player_ids using a table indexed by player_id with a ``name`` column.

    Raises:
        KeyError: if a name is unknown.
    """
    lookup = {n: pid for pid, n in players["name"].items()}
    missing = [n for n in names if n not in lookup]
    if missing:
        raise KeyError(f"Unknown players: {missing}")
    return [lookup[n] for n in names]
