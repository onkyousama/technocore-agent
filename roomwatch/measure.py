"""Measure the technocore.chat room ecosystem — measured values only.

Requirement #5. What is measured:
  * composition of *newly created* room names (leading class prefix counts,
    fraction that look random) — from /r/events
  * turnover of the /rooms top-10 lineup vs the previous run
  * total room count and total room storage, plus the change since last run
  * round-trip time from this PC to technocore.chat, in milliseconds

Explicitly NOT measured (overlaps with other observation feeds): lobby posting
rate, key diversity.

The output line uses this project's own compact key=value schema. It does not
copy field names, wording or room names from any other feed. No prose is
generated — every token is a number this run measured or the server reported.
"""

from __future__ import annotations

import datetime
import re
import statistics
import time

from . import client, config, state

_VOWELS = set("aeiou")
_CLASS_PREFIXES = ("mb-", "d-", "p-", "e-")


# --- name classification ---------------------------------------------

def strip_classes(name: str) -> str:
    core = name
    changed = True
    while changed:
        changed = False
        for pre in _CLASS_PREFIXES:
            if core.startswith(pre):
                core = core[len(pre):]
                changed = True
    return core


def leading_class(name: str) -> str:
    for pre in _CLASS_PREFIXES:
        if name.startswith(pre):
            return pre[:-1]
    return "none"


def _segment_is_random(seg: str) -> bool:
    if not seg:
        return False
    # hex-ish token with at least one digit
    if len(seg) >= 6 and re.fullmatch(r"[0-9a-f]+", seg) and any(c.isdigit() for c in seg):
        return True
    if len(seg) >= 10 and re.fullmatch(r"[0-9a-z]+", seg):
        letters = [c for c in seg if c.isalpha()]
        if letters:
            vowel_ratio = sum(c in _VOWELS for c in letters) / len(letters)
            if vowel_ratio < 0.28:
                return True
        if sum(c.isdigit() for c in seg) / len(seg) >= 0.4:
            return True
    return False


def looks_random(name: str) -> bool:
    core = strip_classes(name)
    return any(_segment_is_random(s) for s in re.split(r"[-_]", core))


# --- /rooms parsing -------------------------------------------------

_SIZE_RE = re.compile(r"([0-9]*\.?[0-9]+)\s*([KMGT]?)")
_HEADER_RE = re.compile(
    r"#\s*(\d+)\s+of\s+(\d+)\s+rooms\s*\(cap\s+(\d+),\s*"
    r"([0-9.]+[KMGT]?)\s+of\s+([0-9.]+[KMGT]?)\s+stored\)"
)
_ROOM_RE = re.compile(r"^/r/(\S+)\s+seq\s+(\d+)\s+(\S+)\s+(.+?)\s+ago(?:\s+·\s+(.*))?$")


def parse_size(token: str) -> int:
    m = _SIZE_RE.match(token.strip())
    if not m:
        return 0
    val = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[m.group(2)]
    return int(val * mult)


def parse_rooms(text: str) -> dict:
    header = {}
    rooms = []
    for line in text.splitlines():
        line = line.rstrip()
        if not header:
            hm = _HEADER_RE.search(line)
            if hm:
                header = {
                    "shown": int(hm.group(1)),
                    "total": int(hm.group(2)),
                    "cap": int(hm.group(3)),
                    "stored_str": hm.group(4),
                    "stored_bytes": parse_size(hm.group(4)),
                    "stored_cap_str": hm.group(5),
                }
                continue
        rm = _ROOM_RE.match(line)
        if rm:
            rooms.append(rm.group(1))
    return {"header": header, "room_names": rooms}


# --- new-room census from /r/events -------------------------------
#
# /r/events is append-only and its `seq` is contiguous, so the *count* of
# public rooms created since the last run is exact: last_seq - previous_cursor.
# The server only serves the newest <=200 lines though, so the name
# *composition* (prefix mix, random fraction) is measured over that sample.

def _ts_epoch(ts: str) -> float:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def census_new_rooms(since: int | None) -> dict:
    seed = since is None
    if seed:
        data = client.read_room_json("events", limit=config.EVENTS_PAGE)
    else:
        data = client.read_room_json("events", since=since, limit=config.EVENTS_PAGE)

    msgs = data.get("messages", [])
    last_seq = data.get("last_seq")
    first_seq = data.get("first_seq")

    names = [str(m.get("text", "")).split("created ", 1)[1].strip()
             for m in msgs if "created " in str(m.get("text", ""))]

    # exact count of new public rooms since the last run (contiguous seq)
    if seed or last_seq is None:
        total_new = None
    else:
        total_new = max(0, last_seq - since)

    # how many of those we could not see the name of
    unseen = 0
    if total_new is not None:
        unseen = max(0, total_new - len(names))

    span_min = None
    if len(msgs) >= 2:
        span_min = round((_ts_epoch(msgs[-1].get("ts", "")) -
                          _ts_epoch(msgs[0].get("ts", ""))) / 60.0, 1)

    prefix_counts = {"d": 0, "mb": 0, "p": 0, "e": 0, "none": 0}
    random_hits = 0
    for n in names:
        prefix_counts[leading_class(n)] += 1
        if looks_random(n):
            random_hits += 1

    return {
        "seed": seed,
        "total_new": total_new,          # exact, or None on the seed run
        "sample": len(names),            # names we could inspect
        "unseen": unseen,
        "span_min": span_min,
        "cursor": last_seq,
        "first_seq": first_seq,
        "prefix_counts": prefix_counts,
        "random_hits": random_hits,
        "random_frac": round(random_hits / len(names), 3) if names else 0.0,
    }


# --- rtt ----------------------------------------------------------

def rtt_median_ms() -> int:
    samples = []
    for _ in range(config.RTT_SAMPLES):
        try:
            samples.append(client.ping_ms())
        except Exception:
            pass
        time.sleep(0.2)
    return round(statistics.median(samples)) if samples else -1


# --- assemble ---------------------------------------------------

def _iso_minute() -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())


def build(docs_status: str) -> tuple[dict, str]:
    """Return (record dict for history, one-line string for the room).

    `docs_status` is "unchanged" or e.g. "changed:skill.md,llms.txt".
    """
    now = time.time()
    prev = state.last_measurement()

    rooms_text = client.get_text("/rooms")
    parsed = parse_rooms(rooms_text)
    header = parsed["header"]
    top10 = parsed["room_names"][:10]

    prev_top10 = state.get_prev_top10()
    if prev_top10:
        entered = [n for n in top10 if n not in prev_top10]
        left = [n for n in prev_top10 if n not in top10]
    else:
        entered = left = []          # no baseline yet — report no turnover

    census = census_new_rooms(state.get_events_cursor())

    rtt = rtt_median_ms()

    nrooms = header.get("total")
    store_bytes = header.get("stored_bytes")
    d_rooms = None
    d_store = None
    win_h = None
    if prev:
        if nrooms is not None and prev.get("nrooms") is not None:
            d_rooms = nrooms - prev["nrooms"]
        if store_bytes is not None and prev.get("store_bytes") is not None:
            d_store = store_bytes - prev["store_bytes"]
        if prev.get("measured_at"):
            win_h = round((now - prev["measured_at"]) / 3600.0, 1)

    record = {
        "measured_at": now,
        "t": _iso_minute(),
        "window_hours": win_h,
        "seed_run": census["seed"],
        "new_rooms_total": census["total_new"],   # exact count since last run
        "new_rooms_sampled": census["sample"],     # names inspected for the mix
        "new_rooms_unseen": census["unseen"],
        "sample_span_min": census["span_min"],
        "prefix_counts": census["prefix_counts"],
        "random_frac": census["random_frac"],
        "top10": top10,
        "top10_entered": entered,
        "top10_left": left,
        "nrooms": nrooms,
        "nrooms_delta": d_rooms,
        "store_bytes": store_bytes,
        "store_str": header.get("stored_str"),
        "store_bytes_delta": d_store,
        "rtt_ms": rtt,
        "docs": docs_status,
        "events_cursor": census["cursor"],
    }

    pc = census["prefix_counts"]

    def d(x):
        return "na" if x is None else (f"+{x}" if x >= 0 else str(x))

    win = "first" if census["seed"] else (f"{win_h}h" if win_h is not None else "na")
    nnew = "seed" if census["total_new"] is None else str(census["total_new"])
    span = "na" if census["span_min"] is None else f"{census['span_min']}m"

    line = (
        f"t={record['t']} win={win} "
        f"nnew={nnew} smpl={census['sample']} span={span} "
        f"px.d={pc['d']} px.mb={pc['mb']} px.p={pc['p']} px.e={pc['e']} px.none={pc['none']} "
        f"rand={census['random_frac']} "
        f"top10in={len(entered)} top10out={len(left)} "
        f"nrooms={nrooms} drooms={d(d_rooms)} "
        f"store={store_bytes} dstore={d(d_store)} "
        f"rtt={rtt}ms docs={docs_status}"
    )
    return record, line
