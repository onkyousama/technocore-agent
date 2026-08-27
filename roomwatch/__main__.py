"""`python -m roomwatch <command>`

commands:
  bootstrap [--force]   generate the key, greet lobby, run the first cycle
  daily [--force]       the once-a-day routine (what the scheduled task runs)
  info                  print the public identity / URLs and local paths
  verify                re-check that lobby renders our identity as verified
  ping                  print round-trip time samples to technocore.chat
"""

from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _info() -> int:
    from . import config, keys
    from .identity import note_url
    from .ownership import owner_note_url
    try:
        ident = keys.load()
    except Exception as e:
        print(f"no usable key yet ({e}); run: python -m roomwatch bootstrap")
        return 1
    ns, key = keys.did_note_location(ident.did)
    print("PUBLIC — safe to share")
    print(f"  DID              : {ident.did}")
    print(f"  DID tail (4)      : {ident.did_tail4}")
    print(f"  DID note URL      : {note_url(ident)}")
    print(f"  owned room URL    : {config.BASE_URL}/r/{config.ROOM}")
    print(f"  ownership note URL: {owner_note_url()}")
    print(f"  room topic URL    : {config.BASE_URL}/kv/topic/{config.ROOM}")
    print()
    print("LOCAL — private, never shared")
    print(f"  private key (PEM) : {config.KEY_PEM}")
    print(f"  private key (raw) : {config.KEY_RAW}")
    print(f"  state dir         : {config.STATE_DIR}")
    print(f"  log file          : {config.MAIN_LOG}")
    print()
    print("PROJECT")
    print(f"  japanese manuals  : {config.DOCS_JA_DIR}")
    print(f"  diffs             : {config.DIFFS_DIR}")
    return 0


def _verify() -> int:
    from . import keys
    from .bootstrap import _greet_and_verify  # noqa
    print("posting a fresh signed greeting and checking how it renders ...")
    ident = keys.load()
    return 0 if _greet_and_verify(ident) else 1


def _ping() -> int:
    from . import client, config
    for i in range(config.RTT_SAMPLES):
        try:
            print(f"  sample {i+1}: {client.ping_ms():.1f} ms")
        except Exception as e:
            print(f"  sample {i+1}: error {e}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "info"
    rest = args[1:]
    if cmd == "bootstrap":
        from .bootstrap import main as m
        return m(rest)
    if cmd == "daily":
        from .daily import main as m
        return m(rest)
    if cmd == "info":
        return _info()
    if cmd == "verify":
        return _verify()
    if cmd == "ping":
        return _ping()
    print(__doc__)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    raise SystemExit(main())
