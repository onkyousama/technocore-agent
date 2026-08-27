"""Paths and constants. Nothing here talks to the network."""

from __future__ import annotations

import os
from pathlib import Path

# --- locations -------------------------------------------------------------

# The code lives in the project; private material and runtime state live in
# ~/.technocore (requirement #1).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ROOMWATCH_HOME", Path.home() / ".technocore"))

KEY_PEM = DATA_DIR / "ed25519_private.pem"          # PKCS#8, unencrypted
KEY_RAW = DATA_DIR / "ed25519_private.raw"          # 32 raw bytes (backup form)
DID_FILE = DATA_DIR / "did.txt"                     # public — the did:key string
PUB_FILE = DATA_DIR / "ed25519_public.txt"          # public — hex + multibase

STATE_DIR = DATA_DIR / "state"
LOG_DIR = DATA_DIR / "logs"
DOCS_SNAPSHOT_DIR = STATE_DIR / "docs"              # yesterday's copy of each manual

NONCES_FILE = STATE_DIR / "nonces.json"
EVENTS_CURSOR_FILE = STATE_DIR / "events_cursor.json"
TOP10_FILE = STATE_DIR / "top10.json"
MEASURE_HISTORY = STATE_DIR / "measure_history.jsonl"
LAST_RUN_FILE = STATE_DIR / "last_run.json"
DOC_HASHES_FILE = DOCS_SNAPSHOT_DIR / "hashes.json"
MAIN_LOG = LOG_DIR / "daily.log"

# Translated manuals and diffs live in the project so they are easy to read.
DOCS_JA_DIR = PROJECT_ROOT / "docs-ja"
DIFFS_DIR = PROJECT_ROOT / "diffs"
DOCS_SRC_DIR = PROJECT_ROOT / "docs-src"

# --- service --------------------------------------------------------------

BASE_URL = os.environ.get("ROOMWATCH_BASE_URL", "https://technocore.chat")

# The room this agent owns (requirement #5). If you fork this tool, set
# ROOMWATCH_ROOM to your own d-<name> — the default below is already owned by
# the original author and you will not be able to claim it.
ROOM = os.environ.get("ROOMWATCH_ROOM", "d-roomwatch-onkyou")
LOBBY = "lobby"                    # greeting target (requirement #4)
# Used only while ROOM cannot be created because the service is at its room cap
# (requirement #6). The existing, long-lived room named "technocore".
FALLBACK_ROOM = "technocore"

# Official manuals watched for changes (requirement #8).
# name -> path on the service
DOCS = {
    "skill.md": "/skill.md",
    "llms.txt": "/llms.txt",
    "auth.md": "/auth.md",
    "patterns.md": "/patterns.md",
}

# --- limits / behaviour --------------------------------------------------

HTTP_TIMEOUT = 20                  # seconds, per request
LONG_POLL_TIMEOUT = 15            # seconds, for wait= requests
MAX_LOG_BYTES = 1_048_576         # 1 MiB — trim record files past this (#9)
EVENTS_PAGE = 200                 # max /r/events lines the server will serve
RTT_SAMPLES = 5                   # ping samples; report the median
USER_AGENT = f"roomwatch-onkyou/1.0 (local daily observer)"

TASK_NAME = "TechnocoreRoomwatchOnkyou"
