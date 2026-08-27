"""Local JSON state: nonces, cursors, run bookkeeping.

All of this is derived / resumable data. The source of truth for identity is
the key file; the source of truth for anything posted is the service itself.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from . import config


def _load(path: Path, default):
    try:
        # utf-8-sig tolerates a BOM if some editor added one
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# --- nonces ----------------------------------------------------------

def next_nonce(scope: str) -> int:
    """A strictly increasing counter per scope.

    `scope` is the room name (for messages) or "note:<ns>/<key>" (for signed
    notes). A millisecond clock is the base; we bump past the stored value so
    two calls in the same millisecond still increase (requirement #2).
    """
    data = _load(config.NONCES_FILE, {})
    now_ms = time.time_ns() // 1_000_000
    nxt = max(now_ms, int(data.get(scope, 0)) + 1)
    data[scope] = nxt
    _save(config.NONCES_FILE, data)
    return nxt


def peek_nonce(scope: str) -> int:
    return int(_load(config.NONCES_FILE, {}).get(scope, 0))


def bump_nonce(scope: str, value: int) -> None:
    """Force the stored counter to at least `value`."""
    data = _load(config.NONCES_FILE, {})
    if value > int(data.get(scope, 0)):
        data[scope] = value
        _save(config.NONCES_FILE, data)


# --- events cursor -------------------------------------------------

def get_events_cursor() -> int | None:
    return _load(config.EVENTS_CURSOR_FILE, {}).get("since")


def set_events_cursor(seq: int) -> None:
    _save(config.EVENTS_CURSOR_FILE, {"since": seq})


# --- /rooms top-10 snapshot --------------------------------------

def get_prev_top10() -> list[str]:
    return _load(config.TOP10_FILE, {}).get("names", [])


def set_top10(names: list[str]) -> None:
    _save(config.TOP10_FILE, {"names": names, "at": _now_iso()})


# --- last measurement (for trend deltas) ------------------------

def last_measurement() -> dict | None:
    try:
        lines = config.MEASURE_HISTORY.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for ln in reversed(lines):
        ln = ln.strip()
        if ln:
            try:
                return json.loads(ln)
            except ValueError:
                continue
    return None


def append_measurement(record: dict) -> None:
    config.MEASURE_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with config.MEASURE_HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- run bookkeeping --------------------------------------------

def load_last_run() -> dict:
    return _load(config.LAST_RUN_FILE, {})


def save_last_run(obj: dict) -> None:
    _save(config.LAST_RUN_FILE, obj)


def observation_already_posted_today() -> bool:
    return load_last_run().get("obs_date") == date.today().isoformat()


def mark_observation_posted() -> None:
    d = load_last_run()
    d["obs_date"] = date.today().isoformat()
    d["obs_at"] = _now_iso()
    save_last_run(d)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
