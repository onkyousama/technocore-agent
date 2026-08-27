"""Own d-roomwatch-onkyou and keep the ownership note alive (requirements #5, #6).

Claim:   GET /kv/room-owners/d-roomwatch-onkyou/set-signed/<did>/<sig>/<nonce>/<did>?if_absent=1
         signature covers  room-owners|d-roomwatch-onkyou|<nonce>|<did>
Refresh: same write without if_absent, nonce greater than /kv/room-nonce/<room>.
"""

from __future__ import annotations

import re

from . import client, config, state
from .client import HttpError
from .keys import Identity
from .logutil import log

_NS = "room-owners"


def _server_room_nonce() -> int:
    body = client.kv_get("room-nonce", config.ROOM)
    if not body:
        return 0
    m = re.search(r"\d+", body)
    return int(m.group(0)) if m else 0


def _next_nonce() -> int:
    """Greater than both our local counter and the server's shared replay
    counter /kv/room-nonce/<room>."""
    scope = f"note:{_NS}/{config.ROOM}"
    local = state.next_nonce(scope)          # already persisted
    server = _server_room_nonce()
    if server + 1 > local:
        n = server + 1
        state.bump_nonce(scope, n)
        return n
    return local


def owner_note_url() -> str:
    return f"{config.BASE_URL}/kv/{_NS}/{config.ROOM}"


def claim_or_refresh(ident: Identity) -> dict:
    current = client.kv_get(_NS, config.ROOM)
    current_val = current.strip() if current else None

    if current_val and current_val.startswith("did:key:") and current_val != ident.did:
        log(f"room {config.ROOM} is owned by another key ({current_val[:24]}…); "
            f"will use fallback room {config.FALLBACK_ROOM}")
        return {"status": "owned_by_other", "owner": current_val}

    nonce = _next_nonce()
    value = ident.did
    try:
        if current_val == ident.did:
            client.kv_set_signed(ident, _NS, config.ROOM, value, nonce)
            return {"status": "refreshed", "nonce": nonce}
        resp = client.kv_set_signed(ident, _NS, config.ROOM, value, nonce, if_absent=True)
        log(f"claimed ownership of {config.ROOM} (nonce {nonce})")
        return {"status": "claimed", "nonce": nonce, "body": resp.body[:200]}
    except HttpError as e:
        if e.status == 409:
            # lost the race — re-read and decide
            again = client.kv_get(_NS, config.ROOM)
            again_val = again.strip() if again else None
            if again_val == ident.did:
                return {"status": "already_ours"}
            log(f"lost ownership race for {config.ROOM}: now {again_val!r}")
            return {"status": "owned_by_other", "owner": again_val}
        raise
