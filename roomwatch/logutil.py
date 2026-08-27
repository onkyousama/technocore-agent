"""Plain-text run log plus a size cap for record files (requirement #9).

When a record file grows past MAX_LOG_BYTES we drop the oldest lines and keep
roughly the newest half, aligned to a line boundary.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from . import config

_started = time.time()

# Windows consoles default to cp932 here; force UTF-8 so Japanese log lines and
# the scheduled-task output never raise UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def trim_file(path: Path, max_bytes: int = config.MAX_LOG_BYTES) -> bool:
    """Return True if the file was trimmed."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return False
    if size <= max_bytes:
        return False
    keep = max_bytes // 2
    with path.open("rb") as fh:
        fh.seek(size - keep)
        tail = fh.read()
    nl = tail.find(b"\n")
    if nl != -1:
        tail = tail[nl + 1:]
    marker = (f"# --- trimmed {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}: "
              f"dropped {size - len(tail)} of {size} bytes ---\n").encode("utf-8")
    path.write_bytes(marker + tail)
    return True


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    try:
        config.MAIN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with config.MAIN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as e:
        print(f"(could not write log file: {e})", file=sys.stderr)


def rotate_all() -> None:
    """Trim every append-only record file."""
    for p in (config.MAIN_LOG, config.MEASURE_HISTORY):
        if trim_file(p):
            log(f"trimmed {p.name} (exceeded {config.MAX_LOG_BYTES} bytes)")
