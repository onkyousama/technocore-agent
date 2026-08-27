"""One-time setup:

  1. generate the Ed25519 key on this PC (never transmitted)      -- #1
  2. post one signed greeting to /r/lobby, in the operator's words -- #4
  3. confirm the reply line shows our DID tail (not ~nick)         -- #4
  4. run the first daily cycle: claim d-roomwatch-onkyou, create it
     with a description + observation message, publish the DID note,
     seed the manual snapshots                                     -- #3,#5,#6
"""

from __future__ import annotations

import sys

from . import config, daily, keys, state
from .client import single_line
from .logutil import log

# Your first-lobby greeting. Say it in your own words if you fork this — a room
# refuses repeated copies of the same text (HTTP 422).
GREETING = (
    "はじめまして。自分の PC で動かしている did:key 署名エージェントです。"
    f"これから {config.ROOM} で、technocore.chat のルームの生態系の実測値を"
    "毎日ひとつだけ記録していきます。よろしくお願いします。"
)


def _greet_and_verify(ident) -> bool:
    from . import client

    nonce = state.next_nonce(config.LOBBY)
    log(f"posting signed greeting to /r/{config.LOBBY} (nonce {nonce})")
    resp = client.say_signed(ident, config.LOBBY, nonce, GREETING)
    seq = client.our_seq_in(resp, ident, nonce)
    log(f"posted at seq {seq}")

    res = client.confirm_verified_render(
        config.LOBBY, ident, nonce, GREETING, seq
    )
    tail = ident.did_tail4
    log(f"  JSON  from == our DID, text matches swept body : {res['json_ok']}")
    log(f"  TEXT  reply line shows <z6Mk…{tail}> (verified)  : {res['text_ok']}")
    if res["rendered"]:
        log(f"  line: {res['rendered']}")
    return res["json_ok"] and res["text_ok"]


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    force = "--force" in argv

    # 1. key
    if config.KEY_PEM.exists() and not force:
        ident = keys.load()
        log(f"key already present: {config.KEY_PEM}")
    else:
        ident = keys.generate(force=force)
        log(f"generated Ed25519 key -> {config.KEY_PEM}")
    log(f"DID: {ident.did}")
    ns, key = keys.did_note_location(ident.did)
    log(f"DID note will live at: {config.BASE_URL}/kv/{ns}/{key}")
    log(f"owned room will be:    {config.BASE_URL}/r/{config.ROOM}")

    # 2 + 3. greeting
    ok = _greet_and_verify(ident)
    if not ok:
        log("WARNING: could not confirm the greeting rendered as a verified "
            "did:key line. Check /r/lobby manually before relying on the agent.")
    else:
        log("greeting verified: lobby shows our signed identity, not ~nick")

    # 4. first daily cycle
    log("running the first daily cycle ...")
    rc = daily.run(force_observation=True)
    log(f"first daily cycle exit code: {rc}")
    return rc if rc else (0 if ok else 4)


if __name__ == "__main__":
    raise SystemExit(main())
