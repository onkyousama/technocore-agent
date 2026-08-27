"""Publish and keep-alive the public DID note (requirement #3 and #6).

Location: /kv/did-<first 2 hex of sha256(did)>/<next 14 hex>
Value:    the did:key string (the bytes a reader needs to verify our messages)

Notes are deleted after 7 days without a write, so the daily job rewrites it.
"""

from __future__ import annotations

from . import client, config
from .keys import Identity, did_note_location
from .logutil import log


def note_url(ident: Identity) -> str:
    ns, key = did_note_location(ident.did)
    return f"{config.BASE_URL}/kv/{ns}/{key}"


def publish_or_refresh(ident: Identity) -> dict:
    ns, key = did_note_location(ident.did)
    current = client.kv_get(ns, key)
    current_val = current.strip() if current else None

    if current_val == ident.did:
        client.kv_set(ns, key, ident.did)          # reset the 7-day timer
        return {"action": "refreshed", "ns": ns, "key": key}

    if current_val is None:
        client.kv_set(ns, key, ident.did, if_absent=True)
        log(f"DID note published at /kv/{ns}/{key}")
        return {"action": "published", "ns": ns, "key": key}

    # Something else is at our fingerprint path. A real SHA-256 collision is
    # not a thing that happens; treat it as a clobber and restore our value.
    log(f"DID note at /kv/{ns}/{key} held a foreign value; restoring ours")
    client.kv_set(ns, key, ident.did)
    return {"action": "restored", "ns": ns, "key": key, "clobbered_value": current_val[:80]}
