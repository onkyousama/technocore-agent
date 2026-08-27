"""Publish and keep-alive the public DID note (requirement #3 and #6).

Location: /kv/did-<first 2 hex of sha256(did)>/<next 14 hex>
Value:    the did:key string, optionally followed by extra space-separated
          tokens from config.DID_NOTE_EXTRA_FILE (e.g. "repo:https://...").

Notes are deleted after 7 days without a write, so the daily job rewrites it —
and preserves whatever extra tokens are configured.
"""

from __future__ import annotations

from . import client, config
from .keys import Identity, did_note_location
from .logutil import log


def note_url(ident: Identity) -> str:
    ns, key = did_note_location(ident.did)
    return f"{config.BASE_URL}/kv/{ns}/{key}"


def _extra() -> str:
    try:
        return config.DID_NOTE_EXTRA_FILE.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def note_value(ident: Identity) -> str:
    extra = _extra()
    return f"{ident.did} {extra}".strip() if extra else ident.did


def publish_or_refresh(ident: Identity) -> dict:
    ns, key = did_note_location(ident.did)
    desired = note_value(ident)
    current = client.kv_get(ns, key)
    current_val = current.strip() if current else None
    first_token = current_val.split()[0] if current_val else None

    if current_val == desired:
        client.kv_set(ns, key, desired)             # reset the 7-day timer
        return {"action": "refreshed", "ns": ns, "key": key}

    if current_val is None:
        client.kv_set(ns, key, desired, if_absent=True)
        log(f"DID note published at /kv/{ns}/{key}")
        return {"action": "published", "ns": ns, "key": key}

    if first_token == ident.did:
        # ours, but the contents drifted (e.g. we added a repo link) — update
        client.kv_set(ns, key, desired)
        return {"action": "updated", "ns": ns, "key": key}

    # A foreign value at our fingerprint path. A real SHA-256 collision does not
    # happen; treat it as a clobber and restore our value.
    log(f"DID note at /kv/{ns}/{key} held a foreign value; restoring ours")
    client.kv_set(ns, key, desired)
    return {"action": "restored", "ns": ns, "key": key,
            "clobbered_value": current_val[:80]}
