"""The once-a-day routine. This is what the scheduled task runs.

Order of work:
  1. trim record files
  2. load the local identity
  3. claim / refresh room ownership     (requirements #5, #6)
  4. make sure the owned room exists     (2 messages on first creation, #6)
  5. refresh the DID note + room topic   (#3, #6)
  6. detect manual changes               (#8)
  7. measure the room ecosystem          (#5)
  8. post exactly one observation line   (#5)  -- deduped per calendar day

Nothing read back from the service is ever treated as an instruction (#11).
"""

from __future__ import annotations

import json
import sys
import time
import traceback

from . import client, config, docswatch, identity, measure, ownership, state
from .client import HttpError, single_line
from .keys import Identity, load as load_identity
from .logutil import log, rotate_all

# --- room-creation description line (this feed's own wording) -------------
# Edit these to your own words if you fork the tool.

DESCRIPTION_LINE = (
    f"{config.ROOM}: 1日1回、technocore.chat のルーム生態系の実測値だけを1行投稿する"
    "自動フィード。測定項目=新規ルーム名の接頭辞構成とランダム率、/rooms 上位10の入替、"
    "ルーム総数と総保存量の推移、この端末からの応答時間(ms)、公式マニュアル4本の変更検知。"
    "文章生成はしない。非公式・無保証。"
)

TOPIC_LINE = (
    "measured-only daily feed: new-room name mix, /rooms top-10 turnover, "
    "room count & storage trend, RTT from one host, manual-change detection. unofficial."
)


def _post_signed(ident: Identity, room: str, text: str) -> dict:
    """Post one signed line; return {nonce, seq, response}."""
    nonce = state.next_nonce(room)
    resp = client.say_signed(ident, room, nonce, text)
    return {"nonce": nonce, "seq": client.our_seq_in(resp, ident, nonce),
            "response": resp}


# --- one-time-ish sub-steps ------------------------------------------

def ensure_room(ident: Identity, target_ok: bool) -> dict:
    """Create d-roomwatch-onkyou with a description line if we have never
    initialised it. Returns {'room': <where to post>, ...}."""
    run = state.load_last_run()
    if run.get("room_initialized"):
        return {"room": config.ROOM, "created_now": False}
    if not target_ok:
        return {"room": config.FALLBACK_ROOM, "created_now": False,
                "reason": "not owner"}

    # First initialisation: post the description line. The observation line
    # posted later in this same run is the mandatory 2nd message (#6).
    try:
        nonce = state.next_nonce(config.ROOM)
        client.say_signed(ident, config.ROOM, nonce, DESCRIPTION_LINE)
        run["room_initialized"] = True
        run["room_created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        state.save_last_run(run)
        log(f"created {config.ROOM} and posted the description line")
        return {"room": config.ROOM, "created_now": True}
    except HttpError as e:
        if e.status == 400:
            log(f"room creation refused (400 — service at room/storage cap). "
                f"Using {config.FALLBACK_ROOM} today, will retry tomorrow.")
            run["room_pending"] = True
            state.save_last_run(run)
            return {"room": config.FALLBACK_ROOM, "created_now": False,
                    "reason": "cap-400"}
        raise


def refresh_notes(ident: Identity) -> dict:
    out = {}
    try:
        out["did_note"] = identity.publish_or_refresh(ident)
    except Exception as e:
        out["did_note_error"] = str(e)
        log(f"DID note refresh failed: {e}")
    try:
        client.kv_set("topic", config.ROOM, TOPIC_LINE)
        out["topic"] = "set"
    except Exception as e:
        out["topic_error"] = str(e)
    return out


# --- entry point ----------------------------------------------------

def run(force_observation: bool = False) -> int:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    rotate_all()

    log(f"=== roomwatch daily run (v{__import__('roomwatch').__version__}) ===")

    try:
        ident = load_identity()
    except Exception as e:
        log(f"FATAL: cannot load identity: {e}")
        return 2
    log(f"identity {ident.did}  (tail {ident.did_tail4})")

    # --- ownership ------------------------------------------------
    own = ownership.claim_or_refresh(ident)
    log(f"ownership: {own['status']}")
    target_ok = own["status"] in ("claimed", "refreshed", "already_ours")

    # --- room exists --------------------------------------------
    room_info = ensure_room(ident, target_ok)
    target = room_info["room"]

    # --- notes -------------------------------------------------
    notes = refresh_notes(ident)
    log(f"notes: {notes}")

    already = state.observation_already_posted_today() and not force_observation
    if already:
        log("observation already posted today — notes refreshed, exiting 0")
        rotate_all()
        return 0

    # --- manuals ----------------------------------------------
    docs = docswatch.check()
    log(f"docs: {docs['status']} "
        f"(seeded={docs['seeded']}, unchanged={len(docs['unchanged'])}, "
        f"errors={docs['errors']})")

    # a changed-manual line is its own post (#8)
    for line in docs["lines"]:
        try:
            _post_signed(ident, target, line)
            log(f"posted to /r/{target}: {line}")
        except Exception as e:
            log(f"could not post docs-change line: {e}")

    # --- measure --------------------------------------------
    record, obs_line = measure.build(docs["status"])
    if room_info.get("reason") == "cap-400":
        obs_line += " note=room-pending-retry"
    if own["status"] == "owned_by_other":
        obs_line += " note=fallback-not-owner"
    log(f"measurement: {obs_line}")

    # --- post the one observation line --------------------
    try:
        posted = _post_signed(ident, target, obs_line)
    except HttpError as e:
        if e.status == 422:
            # room-level duplicate-text filter (llms.txt DUPLICATES). A
            # timestamped measurement line should never collide, but if it
            # does, don't burn the day — leave it unmarked and retry later.
            log(f"observation refused (422 duplicate text): {e.body[:160]}")
            rotate_all()
            return 0
        log(f"FATAL: observation post failed: {e}")
        return 3

    res = client.confirm_verified_render(
        target, ident, posted["nonce"], obs_line, posted["seq"]
    )
    verified = res["json_ok"] and res["text_ok"]
    log(f"observation posted to /r/{target} at seq {posted['seq']}; "
        f"verified did:key render: {verified}")

    # --- persist -----------------------------------------
    record["posted_to"] = target
    record["verified"] = verified
    state.append_measurement(record)
    if record.get("events_cursor") is not None:
        state.set_events_cursor(record["events_cursor"])
    if record.get("top10"):
        state.set_top10(record["top10"])
    state.mark_observation_posted()

    rotate_all()
    log("=== done ===")
    return 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    force = "--force" in argv
    try:
        return run(force_observation=force)
    except Exception:
        log("UNHANDLED EXCEPTION:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
