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

from . import (client, config, docswatch, identity, measure, ownership,
               publish, state)
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

def _room_exists(room: str) -> bool | None:
    """True if the server currently holds this room, False if it was reaped or
    never created, None if the check itself failed (caller must not assume)."""
    try:
        j = client.read_room_json(room, limit=1)
        return bool(j.get("count") or j.get("last_seq"))
    except HttpError as e:
        if e.status == 404:
            return False
        return None
    except Exception:
        return None


def ensure_room(ident: Identity, target_ok: bool) -> dict:
    """Make sure d-roomwatch-onkyou exists as a >=2-message room, recreating it
    if it was reaped while the PC was off (7-day idle / 24h single-message).
    Returns {'room': <where to post>, ...}."""
    run = state.load_last_run()
    if not target_ok:
        return {"room": config.FALLBACK_ROOM, "created_now": False,
                "reason": "not owner"}

    exists = _room_exists(config.ROOM)
    if run.get("room_initialized") and exists is not False:
        # exists, or the check failed (exists is None) — do not risk a duplicate
        # description post; the observation post later keeps the room alive
        return {"room": config.ROOM, "created_now": False}

    reaped = bool(run.get("room_initialized")) and exists is False
    what = "recreating reaped" if reaped else "creating"

    # Post the description line. The observation line posted later in this same
    # run is the mandatory 2nd message so the room is not a 24h single-message
    # room (#6).
    try:
        nonce = state.next_nonce(config.ROOM)
        client.say_signed(ident, config.ROOM, nonce, DESCRIPTION_LINE)
        run["room_initialized"] = True
        run["room_created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        run.pop("room_pending", None)
        state.save_last_run(run)
        log(f"{what} {config.ROOM}: posted the description line")
        return {"room": config.ROOM, "created_now": True, "reaped": reaped}
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
        try:
            log(f"publish (catch-up): {publish.push_pending()}")
        except Exception as e:
            log(f"publish push_pending failed (non-fatal): {e}")
        log("observation already posted today — notes refreshed, exiting 0")
        rotate_all()
        return 0

    # --- manuals ----------------------------------------------
    docs = docswatch.check()
    log(f"docs: {docs['status']} "
        f"(seeded={docs['seeded']}, unchanged={len(docs['unchanged'])}, "
        f"errors={docs['errors']})")

    # A changed-manual line is its own post (#8). The stored snapshot is NOT
    # advanced until the line is posted AND the GitHub append is done, so a
    # crash here just means the change is re-detected and re-handled next run
    # (the append is idempotent, the line re-post is dup-filtered within secs).
    posted_ok = []
    for c in docs["changed"]:
        line = f"docs changed: {c['name']} +{c['added']}/-{c['removed']}"
        try:
            _post_signed(ident, target, line)
            posted_ok.append(c["name"])
            log(f"posted to /r/{target}: {line}")
        except HttpError as e:
            if e.status == 422:               # already there from an earlier try
                posted_ok.append(c["name"])
                log(f"docs-change line already present (422): {line}")
            else:
                log(f"could not post docs-change line ({e.status}): {line}")
        except Exception as e:
            log(f"could not post docs-change line: {e}")

    try:
        if docs["changed"]:
            pub = publish.publish_docs(docs["changed"])
        else:
            pub = publish.push_pending()      # self-heal any earlier failure
        log(f"publish: {pub}")
        pub_ok = bool(pub.get("ok"))
    except Exception as e:
        log(f"publish step failed (non-fatal): {e}")
        pub_ok = False

    # Advance the snapshot only for docs that were both announced and appended.
    done = [c for c in docs["changed"] if c["name"] in posted_ok and pub_ok]
    if done:
        docswatch.commit_changes(done)
        log(f"docs snapshot advanced for: {[c['name'] for c in done]}")

    # --- measure --------------------------------------------
    record, obs_line = measure.build(docs["status"])
    if room_info.get("reason") == "cap-400":
        obs_line += " note=room-pending-retry"
    if own["status"] == "owned_by_other":
        obs_line += " note=fallback-not-owner"
    log(f"measurement: {obs_line}")

    # --- post the one observation line --------------------
    # Allocate the nonce up front so we can look the message up even if the POST
    # raises (a timeout may still have landed; a 422 on an internal retry means
    # it landed). We only mark the day done once we have CONFIRMED it is in the
    # room — otherwise the next run posts it (nothing half-done is left behind).
    obs_nonce = state.next_nonce(target)
    seq = None
    try:
        resp = client.say_signed(ident, target, obs_nonce, obs_line)
        seq = client.our_seq_in(resp, ident, obs_nonce)
    except HttpError as e:
        log(f"observation POST returned HTTP {e.status} "
            f"({e.body[:120]}); checking whether it landed anyway")

    res = client.confirm_verified_render(target, ident, obs_nonce, obs_line, seq)
    landed = res["json_ok"] or res["text_ok"]
    if not landed:
        log("observation did NOT land — leaving today unmarked; the next run "
            "will retry it")
        rotate_all()
        return 3

    verified = res["json_ok"] and res["text_ok"]
    log(f"observation in /r/{target} at seq {seq}; verified did:key render: {verified}")

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
