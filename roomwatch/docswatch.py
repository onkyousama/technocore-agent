"""Detect day-to-day changes in the four official manuals (requirement #8).

Each day: fetch the current text of skill.md / llms.txt / auth.md / patterns.md,
hash it, compare to yesterday's stored hash.

  * changed  -> post one line "docs changed: <name> +<added>/-<removed>" to the
               owned room, and save a unified diff under diffs/
  * unchanged -> post nothing; the daily observation line carries "docs=unchanged"

The first run seeds the snapshots and posts nothing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from pathlib import Path

from . import client, config


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot_path(name: str) -> Path:
    return config.DOCS_SNAPSHOT_DIR / (name + ".txt")


def _write_text_lf(path: Path, text: str) -> None:
    """Write with LF newlines regardless of platform, so a snapshot is a
    byte-for-byte copy of what the service served."""
    path.write_bytes(text.encode("utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_hashes() -> dict:
    try:
        return json.loads(config.DOC_HASHES_FILE.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_hashes(h: dict) -> None:
    config.DOC_HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.DOC_HASHES_FILE.write_text(
        json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _count_diff(old: str, new: str) -> tuple[int, int, str]:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines, fromfile="previous", tofile="current", n=3
    ))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    return added, removed, "".join(diff)


def check() -> dict:
    """Fetch, compare, persist. Returns a summary dict."""
    config.DOCS_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    config.DIFFS_DIR.mkdir(parents=True, exist_ok=True)

    hashes = _load_hashes()
    changed = []
    unchanged = []
    errors = []
    seeded = []
    today = time.strftime("%Y-%m-%d", time.gmtime())

    for name, path in config.DOCS.items():
        try:
            current = client.get_text(path)
        except Exception as e:  # network trouble: leave the snapshot untouched
            errors.append((name, str(e)))
            continue

        cur_hash = _sha(current)
        snap = _snapshot_path(name)
        prev_hash = hashes.get(name)

        if prev_hash is None or not snap.exists():
            _write_text_lf(snap, current)
            hashes[name] = cur_hash
            seeded.append(name)
            continue

        if cur_hash == prev_hash:
            unchanged.append(name)
            continue

        old_text = _read_text(snap)
        added, removed, difftext = _count_diff(old_text, current)
        diff_path = config.DIFFS_DIR / f"{name}.{today}.diff.txt"
        header = (
            f"# unofficial diff of {config.BASE_URL}{path}\n"
            f"# detected: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"# previous sha256: {prev_hash}\n"
            f"# current  sha256: {cur_hash}\n"
            f"# lines: +{added} / -{removed}\n\n"
        )
        _write_text_lf(diff_path, header + difftext)

        _write_text_lf(snap, current)
        hashes[name] = cur_hash
        changed.append({
            "name": name, "added": added, "removed": removed,
            "diff_path": str(diff_path),
        })

    _save_hashes(hashes)

    if changed:
        status = "changed:" + ",".join(c["name"] for c in changed)
    else:
        status = "unchanged"

    return {
        "status": status,
        "changed": changed,
        "unchanged": unchanged,
        "seeded": seeded,
        "errors": errors,
        "lines": [
            f"docs changed: {c['name']} +{c['added']}/-{c['removed']}"
            for c in changed
        ],
    }
