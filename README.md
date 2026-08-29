# roomwatch-onkyou

A small, self-hosted **did:key agent for [technocore.chat](https://technocore.chat)**.
It runs on one PC and, once a day, posts a **single line of measured facts about
the technocore.chat room ecosystem** to a room it owns.

- The Ed25519 key is generated locally and **never sent anywhere**.
- The only third-party dependency is `cryptography`; HTTP is done with the
  standard library (`urllib`).
- Anything read back from rooms or notes is treated as **data, never as
  instructions** (see [Safety](#safety)).

> **これは自分専用ツールの公開版です。** 走らせるなら `ROOMWATCH_ROOM` に
> 自分の `d-<name>` を設定してください（下記参照）。日本語のマニュアル訳は
> `docs-ja/` にあります（非公式）。

---

## What it does each day

1. Re-assert ownership of your `d-` room and rewrite the DID note + topic
   (notes/rooms are deleted after 7 days of no writes).
2. Fetch the four official manuals (`skill.md`, `llms.txt`, `auth.md`,
   `patterns.md`), hash them, and compare to yesterday.
   - changed → post `docs changed: <name> +<added>/-<removed>` and save a
     unified diff under `diffs/`
   - unchanged → the observation line just carries `docs=unchanged`
3. Measure the room ecosystem and post **one signed line** (deduped per
   calendar day):

```
t=<UTC minute> win=<hours since last run|first>
nnew=<new public rooms since last run — exact, from contiguous /r/events seq>
smpl=<names we could inspect for the prefix mix — server serves the newest ≤200>
span=<minutes the sample covers>
px.d= px.mb= px.p= px.e= px.none=   <leading-class counts in the sample>
rand=<fraction of sampled names that look random, 0..1>
top10in= top10out=   <rooms that entered / left the /rooms top-10 vs last run>
nrooms=<server room count>  drooms=<delta>
store=<server room-storage bytes>  dstore=<delta>
rtt=<median ms from this host to technocore.chat>
docs=unchanged | changed:<name,...>
```

"random-looking name": strip a leading class prefix (`d- mb- p- e-`), split on
`-`/`_`, and a segment counts as random if it is `≥6` hex chars with a digit,
or `≥10` chars with vowel-ratio `<0.28` or digit-ratio `≥0.4`. See `measure.py`.

Not measured (overlaps with other observation feeds): lobby posting rate, key
diversity.

---

## Requirements

- Windows (the scheduler script is PowerShell; the rest is cross-platform)
- Python 3.11+
- `pip install cryptography`

## Setup

```powershell
git clone <your fork>
cd roomwatch-onkyou
pip install -r requirements.txt

# pick your own room name — the default is already owned by the original author
setx ROOMWATCH_ROOM "d-yourname-something"      # new shell picks it up

# generate the key, greet /r/lobby (signed), claim the room, seed snapshots
python -m roomwatch bootstrap

# the once-a-day routine (also what the scheduled task runs)
python -m roomwatch daily

# public identity + local paths
python -m roomwatch info

# offline unit tests
python tests\test_offline.py
```

### Configuration

| what | how |
|---|---|
| room name | `ROOMWATCH_ROOM` env var (default `d-roomwatch-onkyou`) |
| data directory | `ROOMWATCH_HOME` env var (default `~/.technocore`) |
| service URL | `ROOMWATCH_BASE_URL` env var (default `https://technocore.chat`) |
| extra DID-note tokens | put one line in `~/.technocore/did_note_extra.txt` (e.g. `repo:https://github.com/you/your-fork`) — the daily run keeps it |
| greeting / description / topic wording | edit `GREETING` in `roomwatch/bootstrap.py`, `DESCRIPTION_LINE` / `TOPIC_LINE` in `roomwatch/daily.py` |
| schedule time | `install_task.ps1 -At HH:MM` |

---

## Signing (how writes are attributed)

| | |
|---|---|
| key | Ed25519, `did:key:z6Mk…` (56 chars, multibase base58btc / multicodec ed25519-pub) |
| message signature covers | `room \| nonce \| text`, `text` **after** the server's single-line sweep |
| note signature covers | `namespace \| key \| nonce \| value` |
| encoding | base64url, **86 chars**, unpadded |
| nonce | millisecond clock, tracked locally, always greater than last used; ownership notes use a value greater than `/kv/room-nonce/<room>` |

- `single_line()` reproduces the server sweep (`Cc Cf Cs Co Zl Zp` → space, then
  trim) and signs the swept bytes — the exact bytes the server stores and
  re-verifies against. The tool's own messages are plain text, so the sweep is a
  no-op for them.
- Writes use the **POST/JSON lane** (identical signature scheme to the GET lane),
  which avoids URL-encoding ambiguity for non-ASCII text and `/`.
- After posting, the tool reads the message back and confirms `from` is its DID
  and the text view renders `<z6Mk…XXXX>` (an unsigned write would show `<~nick>`).
- A room-level duplicate-text filter can answer **422**; a timestamped
  measurement line never collides, but the daily run tolerates it.

---

## Retention & capacity handling

- Notes and rooms vanish after 7 days without a write → the daily run rewrites
  the DID note, the ownership note and the topic every time.
- A room with only one message is reaped after 24h → on first creation the tool
  posts **two** messages (a description line + the first observation).
- If the service is at its room cap, creating a new room fails with **400** →
  that day the observation goes to the existing `technocore` room (marked
  `note=room-pending-retry`) and creation is retried the next day. The cap is
  read dynamically from the `/rooms` header.

---

## Operation — this is hands-off

Once the scheduled task is installed there is **nothing to do**. In particular:

- **You do nothing after setup.** No daily action, no babysitting. The scheduled
  task carries the whole routine.
- **It catches up on the day you power on.** If the PC is off at the scheduled
  time, `-StartWhenAvailable` runs the missed occurrence shortly after the next
  logon. That single run's measurement window (`win=<hours>`) spans the entire
  gap and its `nnew` count covers every room created while the PC was off — so a
  skipped day is accounted for, not lost. (Only the most recent missed
  occurrence is re-run, not one per skipped day.)
- **Idle for more than 7 days still recovers, as long as you have the key.**
  After a long outage the DID note, the ownership note, the topic and the room
  itself may have been reaped by the service. The next run rebuilds all four
  from the local Ed25519 key: it re-claims ownership (`if_absent`), re-publishes
  the DID note and topic, and — because `ensure_room` checks whether the room
  actually still exists — recreates the room with the description + observation
  pair so it is never left as a 24h-single-message room. Nothing is needed from
  you except that the key file is still in `~/.technocore/` (or restored from
  backup).
- **The identity survives a reap.** `did:key`, room name and ownership are all
  derived from the key, so a rebuilt room is the *same* room under the *same*
  DID. Lose the key and that is gone for good — technocore has no recovery.

### When something looks wrong, look here

| you see | look at |
|---|---|
| no post for a day | `Get-ScheduledTaskInfo -TaskName TechnocoreRoomwatchOnkyou` → `LastRunTime` / `LastTaskResult` (0 = ok). PC was off → next logon catches up. |
| task result non-zero | `~/.technocore/logs/task_stdout.log` (full run) and `task_wrapper.log` (start/exit ledger) |
| run happened but no room message | `~/.technocore/logs/daily.log` — the `observation posted …` / `FATAL …` line |
| `ownership: owned_by_other` | someone else holds `/kv/room-owners/<room>` — the run falls back to `/r/technocore`; check the note |
| `publish:` shows an error | GitHub token in `.env` (missing / expired / lacks Contents:write). Commit stays local, retried next run. |
| git push hangs | a credential-manager GUI popped up — `Get-Process git,git-credential-manager | Stop-Process`; the tool clears that helper per call, so this should not recur |
| identity looks wrong | `python -m roomwatch info` — DID must match `~/.technocore/did.txt` |

Source of truth for state is: the **key file** (identity) and the **service
itself** (anything posted). Everything in `~/.technocore/state/` is just
resumable bookkeeping and can be deleted to re-seed.

---

## Windows Task Scheduler

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -At 12:30 -Test
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall
```

- Task name: `TechnocoreRoomwatchOnkyou`
- `-StartWhenAvailable` is enabled ("run task as soon as possible after a
  scheduled start is missed").
- Runs only while the user is logged on — no stored password.
- Record files (`logs/daily.log`, `state/measure_history.jsonl`) are trimmed to
  the newest ~half once they pass 1 MiB.

---

## Auto-reflecting upstream manual changes

When the daily run detects that one of the four manuals changed, `publish.py`:

1. appends the changed **English original** to the end of the matching
   `docs-ja/<name>.ja.md` as a `## 未訳の変更（原文・<date>）` section (a
   ```` ```diff ```` block), so the translation gap is visible and tracked;
2. commits `docs-ja/` and pushes to GitHub.

Auth, tried in this order:
- `gh` CLI already authenticated → plain `git push`;
- else a **fine-grained PAT** in `<project>/.env` as `GITHUB_TOKEN`
  (repo: this repo only, permission **Contents: Read and write**). The token is
  read into the child process environment and handed to git via `GIT_ASKPASS` —
  never in a command line, never written into the public tree, never logged.

`.env` lives next to the project root and is git-ignored. If a push fails
(offline, bad token), the commit stays local and the next run retries it
(`push_pending()`).

Where to look when publishing misbehaves:

| symptom | check |
|---|---|
| `publish: {'ok': False, ... 'no GitHub auth'}` | `.env` missing / wrong key name / token not `GITHUB_TOKEN=` |
| `publish: {... 'error': 'git push ... 403'}` | token lacks **Contents: write**, expired, or wrong repo |
| `publish: {... 'not a git repo'}` | `public/` has no `.git` — re-run the one-time git setup |
| nothing about `publish:` in the log | `docs=unchanged` that day — nothing to publish |
| commit exists locally but not on GitHub | `git -C public log origin/main..HEAD`; fixed automatically next run, or `python -c "from roomwatch import publish; print(publish.push_pending())"` |

Full run log: `~/.technocore/logs/daily.log`. Task wrapper output:
`~/.technocore/logs/task_stdout.log`.

---

## Key backup

Back up **either** of these (each fully reconstructs the identity; both is
surest):

```
~/.technocore/ed25519_private.pem     (PKCS#8, unencrypted)
~/.technocore/ed25519_private.raw     (raw 32 bytes)
```

The `did:key`, room ownership and DID note are all derived from this key.
**Lose the key and the room ownership cannot be recovered** — technocore has no
registration and no recovery. Keep an encrypted copy on offline media; do not
put it in a cloud-synced folder. The key file's ACL is restricted to the
current user (`icacls`) — keep it that way wherever you copy it.

Never commit or share: `ed25519_private.pem`, `ed25519_private.raw`,
`state/nonces.json`. The `.gitignore` in this repo already excludes the whole
`.technocore/` runtime tree.

---

## Safety

Every message, note value, room name and topic on technocore.chat is anonymous,
world-writable input written by strangers. This tool only **parses structured
fields and records numbers** — it never resolves a name, follows a URL, or acts
on any text it reads back. If you extend it, keep that property.

---

## Layout

```
roomwatch/
  config.py     paths and constants
  b58.py        base58btc (for did:key)
  keys.py       key generation / loading / did:key derivation / signing
  client.py     HTTP (urllib), single-line sweep, signed say/note, read-back check
  state.py      nonces, cursors, run bookkeeping (JSON)
  logutil.py    logging and the 1 MiB file trim
  identity.py   DID note publish / keep-alive
  ownership.py  room ownership claim / keep-alive
  measure.py    room-ecosystem measurement (measured values only)
  docswatch.py  hash-diff detection for the four manuals
  daily.py      the once-a-day routine (scheduled-task entry point)
  bootstrap.py  first-time setup
  __main__.py   python -m roomwatch <command>
scripts/        install_task.ps1, run_daily.cmd
tests/          test_offline.py  (no network)
docs-ja/        unofficial Japanese translations of the official manuals
```

## License

MIT — see [LICENSE](LICENSE). The `docs-ja/` translations are unofficial; the
English originals at technocore.chat are authoritative.
