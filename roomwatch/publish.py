"""Reflect upstream manual changes to the public GitHub repo.

When the daily run detects a manual change it:
  1. appends the changed *original text* to the tail of the matching
     docs-ja/<name>.ja.md as a "未訳の変更（原文・日付）" section, and
  2. commits docs-ja/ and pushes to GitHub.

Auth, in order of preference:
  * `gh` CLI already authenticated  -> plain `git push` (gh's credential helper)
  * a fine-grained PAT in <project>/.env as GITHUB_TOKEN  -> pushed via
    GIT_ASKPASS, so the token is only ever in the child process environment —
    never in argv, never on disk in the public tree, never logged.

The token and the private key never leave this machine except as the HTTPS
Authorization git sends to github.com to push.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .logutil import log

_ASKPASS_CMD = config.STATE_DIR / "git_askpass.cmd"


# --- token / gh -------------------------------------------------------

def read_env_token() -> str | None:
    """GITHUB_TOKEN (or GH_TOKEN) from the .env file outside the public tree."""
    try:
        text = config.ENV_FILE.read_text(encoding="utf-8-sig")
    except (FileNotFoundError, OSError):
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() in ("GITHUB_TOKEN", "GH_TOKEN"):
            return v.strip().strip('"').strip("'") or None
    return None


def gh_authenticated() -> bool:
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True,
                           text=True, timeout=15)
        return r.returncode == 0
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


# --- git plumbing ---------------------------------------------------

class PublishError(RuntimeError):
    pass


def _git(*args: str, token: str | None = None, check: bool = True) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        _write_askpass()
        env["GIT_ASKPASS"] = str(_ASKPASS_CMD)
        env["GH_PAT"] = token          # consumed by the askpass helper only
    r = subprocess.run(["git", "-C", str(config.PUBLISH_DIR), *args],
                       capture_output=True, text=True, env=env, timeout=120)
    if check and r.returncode != 0:
        raise PublishError(f"git {' '.join(args)} -> {r.returncode}: "
                           f"{_mask(r.stderr.strip() or r.stdout.strip())}")
    return r.stdout.strip()


def _write_askpass() -> None:
    # contains NO secret — it echoes an env var git will have set for it
    if not _ASKPASS_CMD.exists():
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        _ASKPASS_CMD.write_text("@echo %GH_PAT%\r\n", encoding="ascii")


def _mask(s: str) -> str:
    return re.sub(r"(github_pat_|ghp_|gho_)[A-Za-z0-9_]+", r"\1***", s)


def _remote_slug() -> str:
    url = _git("remote", "get-url", "origin")
    m = re.search(r"github\.com[/:]([^/]+/[^/.]+)", url)
    if not m:
        raise PublishError(f"cannot parse owner/repo from remote: {url}")
    return m.group(1)


def is_repo() -> bool:
    try:
        return _git("rev-parse", "--is-inside-work-tree", check=False) == "true"
    except Exception:
        return False


def _has_unpushed() -> bool:
    try:
        out = _git("rev-list", "--count",
                   f"origin/{config.PUBLISH_BRANCH}..HEAD", check=False)
        return out.isdigit() and int(out) > 0
    except Exception:
        return False


def _push(token: str | None) -> None:
    slug = _remote_slug()
    if token:
        # username in the URL, password (the token) via GIT_ASKPASS
        url = f"https://x-access-token@github.com/{slug}.git"
        _git("push", url, f"HEAD:{config.PUBLISH_BRANCH}", token=token)
    else:
        _git("push", "origin", f"HEAD:{config.PUBLISH_BRANCH}")


# --- append untranslated change --------------------------------------

def append_untranslated_change(name: str, service_path: str, added: int,
                               removed: int, diff_text: str,
                               date: str) -> Path | None:
    ja_name = config.DOCS_JA_FILES.get(name)
    if not ja_name:
        return None
    target = config.PUBLISH_DOCS_JA / ja_name
    if not target.exists():
        log(f"publish: {target} missing, cannot append change for {name}")
        return None

    section = (
        f"\n\n---\n\n"
        f"## 未訳の変更（原文・{date}）\n\n"
        f"> 原文 <{config.BASE_URL}{service_path}> がこの日に変更されました"
        f"（+{added} / -{removed} 行）。**以下は英語原文の差分で、まだ日本語訳に"
        f"反映されていません。** 訳を更新したらこのセクションを削除してください。\n\n"
        f"```diff\n{diff_text.rstrip()}\n```\n"
    )
    with target.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(section)
    return target


# --- top-level entry points ----------------------------------------

def publish_docs(changed: list[dict]) -> dict:
    """`changed` is docswatch.check()['changed']. Append each, commit, push."""
    if not is_repo():
        return {"ok": False, "reason": f"{config.PUBLISH_DIR} is not a git repo "
                f"— run the one-time setup first"}

    date = time.strftime("%Y-%m-%d", time.gmtime())
    touched = []
    for c in changed:
        name = c["name"]
        service_path = config.DOCS.get(name, "")
        diff_text = ""
        try:
            diff_text = Path(c["diff_path"]).read_text(encoding="utf-8")
            # drop our own header comment lines, keep the real diff
            diff_text = "\n".join(
                ln for ln in diff_text.splitlines()
                if not ln.startswith("# ")
            ).strip()
        except (OSError, KeyError):
            pass
        p = append_untranslated_change(name, service_path, c["added"],
                                       c["removed"], diff_text, date)
        if p:
            touched.append(p)

    if not touched:
        return {"ok": False, "reason": "no docs-ja files matched"}

    gh_ok = gh_authenticated()
    token = None if gh_ok else read_env_token()
    auth = "gh" if gh_ok else ("pat" if token else "none")

    _git("add", "--", *[str(p.relative_to(config.PUBLISH_DIR)) for p in touched])
    staged = _git("diff", "--cached", "--name-only")
    if not staged:
        return {"ok": True, "committed": False, "reason": "nothing staged"}

    names = ", ".join(c["name"] for c in changed)
    _git("commit", "-m",
         f"docs-ja: append untranslated upstream change ({names}, {date})")

    if auth == "none":
        return {"ok": False, "committed": True, "pushed": False,
                "reason": "no GitHub auth — set GITHUB_TOKEN in .env; "
                          "commit is local and will push next run"}
    try:
        _push(token)
        return {"ok": True, "committed": True, "pushed": True, "auth": auth,
                "files": [p.name for p in touched]}
    except PublishError as e:
        return {"ok": False, "committed": True, "pushed": False, "error": str(e)}


def push_pending() -> dict:
    """Push any local commits that never made it to GitHub (self-heal)."""
    if not is_repo():
        return {"ok": False, "reason": "not a git repo"}
    try:
        _git("fetch", "origin", config.PUBLISH_BRANCH, check=False)
    except Exception:
        pass
    if not _has_unpushed():
        return {"ok": True, "pushed": False, "reason": "up to date"}
    gh_ok = gh_authenticated()
    token = None if gh_ok else read_env_token()
    if not gh_ok and token is None:
        return {"ok": False, "pushed": False, "reason": "no auth"}
    try:
        _push(token)
        return {"ok": True, "pushed": True}
    except PublishError as e:
        return {"ok": False, "pushed": False, "error": str(e)}
