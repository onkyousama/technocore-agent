"""HTTP client for technocore.chat, built on the standard library only.

Every write here is either anonymous or signed with the local Ed25519 key.
Responses are parsed for their structured fields only; free text from rooms
and notes is never executed or followed (requirement #11).
"""

from __future__ import annotations

import http.client
import json
import random
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import config
from .keys import Identity


# --- single-line sweep --------------------------------------------------

def single_line(text: str) -> str:
    """Reproduce the server's single-line sweep, then trim the ends.

    Per llms.txt (SINGLE LINE / NORMALIZATION): every character in Unicode
    general categories Cc, Cf, Cs, Co, Zl, Zp becomes a space, then the ends
    are trimmed. The server never normalizes (NFC/NFD), so the swept text is
    exactly the bytes it stores and verifies a signature against — the signed
    payload must use this, not the raw text (requirement #2).
    """
    out = []
    for ch in text:
        if ch in ("\t", "\n", "\r"):
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp"):
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip()


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


@dataclass
class Response:
    status: int
    body: str
    elapsed_ms: float


# --- low level ---------------------------------------------------------

# transient network faults worth retrying: DNS/refused/unreachable (URLError),
# read timeouts (TimeoutError / socket.timeout), connection resets and truncated
# responses (OSError / http.client.HTTPException e.g. RemoteDisconnected,
# IncompleteRead). The read is inside the try so a mid-body drop is retried too.
_TRANSIENT = (urllib.error.URLError, TimeoutError, socket.timeout,
              ConnectionError, http.client.HTTPException, OSError)


def _request(method: str, url: str, *, data: bytes | None = None,
             timeout: float | None = None, retries: int = 3) -> Response:
    headers = {"User-Agent": config.USER_AGENT, "Accept": "*/*"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    attempt = 0
    while True:
        attempt += 1
        last = attempt > retries
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
                elapsed = (time.perf_counter() - start) * 1000.0
                return Response(resp.status, body, elapsed)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if e.fp else ""
            if e.code == 429 and not last:
                time.sleep(min(_retry_after_seconds(body, e.headers), 45))
                continue
            if e.code in (500, 502, 503, 504) and not last:
                time.sleep(_backoff(attempt))
                continue
            raise HttpError(e.code, body, url)
        except _TRANSIENT as e:
            if not last:
                time.sleep(_backoff(attempt))
                continue
            raise HttpError(0, f"{type(e).__name__}: {e}", url)


def _backoff(attempt: int) -> float:
    # 2s, 4s, 8s ... capped, with jitter so retries don't hammer in lockstep
    return min(2.0 * (2 ** (attempt - 1)), 20.0) + random.uniform(0, 1.5)


def _retry_after_seconds(body: str, headers) -> float:
    try:
        ra = headers.get("Retry-After")
        if ra:
            return float(ra)
    except Exception:
        pass
    # the body names the seconds to wait; grab the first integer
    import re
    m = re.search(r"(\d+)\s*second", body)
    return float(m.group(1)) if m else 5.0


def _q(segment: str) -> str:
    # percent-encode one path segment; ':' is legal in a path segment and the
    # did:key route pattern contains it literally, so keep it readable.
    return urllib.parse.quote(segment, safe=":")


# --- reads ------------------------------------------------------------

def get_text(path: str, **params) -> str:
    url = config.BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request("GET", url).body


def read_room_json(room: str, *, since: int | None = None,
                   limit: int | None = None, wait: int | None = None) -> dict:
    params = {"format": "json"}
    if since is not None:
        params["since"] = since
    if limit is not None:
        params["limit"] = limit
    if wait is not None:
        params["wait"] = wait
    url = f"{config.BASE_URL}/r/{_q(room)}?" + urllib.parse.urlencode(params)
    timeout = config.LONG_POLL_TIMEOUT if wait else config.HTTP_TIMEOUT
    return json.loads(_request("GET", url, timeout=timeout).body)


def _strip_untrusted_banner(body: str) -> str:
    """A /kv read prefixes the value with a one-line 'UNTRUSTED CONTENT'
    banner and a blank line. Return just the stored value."""
    if body.startswith("!! UNTRUSTED CONTENT"):
        nl = body.find("\n")
        if nl != -1:
            rest = body[nl + 1:]
            if rest.startswith("\n"):
                rest = rest[1:]
            elif rest.startswith("\r\n"):
                rest = rest[2:]
            return rest.rstrip("\n")
    return body.rstrip("\n")


def kv_get(ns: str, key: str) -> str | None:
    """Return the stored note value (banner stripped), or None if absent."""
    url = f"{config.BASE_URL}/kv/{_q(ns)}/{_q(key)}"
    try:
        return _strip_untrusted_banner(_request("GET", url).body)
    except HttpError as e:
        if e.status == 404:
            return None
        raise


def ping_ms(path: str = "/healthz") -> float:
    """Round-trip time to the service in milliseconds (requirement #5)."""
    return _request("GET", config.BASE_URL + path, retries=0).elapsed_ms


# --- writes ---------------------------------------------------------
#
# All writes use the POST/JSON lane. The signature scheme is identical to the
# GET lane (base64url, 86 chars, over the swept text), but a JSON body carries
# Japanese text and characters like '/' without any URL-encoding ambiguity.

def _post_json(path: str, body: dict, *, want_json: bool = True):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    resp = _request("POST", config.BASE_URL + path, data=data)
    if want_json:
        try:
            return json.loads(resp.body)
        except ValueError:
            return {"_raw": resp.body}
    return resp


def say_signed(ident: Identity, room: str, nonce: int, text: str) -> dict:
    """Post a signed message. Signs `<room>|<nonce>|<swept text>` and sends the
    swept text — the exact bytes the server stores. Returns the room JSON."""
    swept = single_line(text)
    payload = f"{room}|{nonce}|{swept}".encode("utf-8")
    sig = ident.sign_b64url(payload)
    assert len(sig) == 86, f"signature must be 86 chars, got {len(sig)}"
    return _post_json(f"/r/{_q(room)}?format=json", {
        "did": ident.did, "sig": sig, "nonce": str(nonce), "text": swept,
    })


def kv_set(ns: str, key: str, value: str, *, if_absent: bool = False,
           if_match: str | None = None) -> Response:
    body = {"value": value}
    if if_absent:
        body["if_absent"] = True
    if if_match is not None:
        body["if"] = if_match
    return _post_json(f"/kv/{_q(ns)}/{_q(key)}", body, want_json=False)


def our_seq_in(post_response: dict, ident: Identity, nonce: int) -> int | None:
    for m in post_response.get("messages", []):
        if m.get("from") == ident.did and m.get("nonce") == nonce:
            return m.get("seq")
    return None


def confirm_verified_render(room: str, ident: Identity, nonce: int,
                            text: str, around_seq: int | None,
                            tries: int = 4) -> dict:
    """Read the message back and confirm the service treated it as a verified
    did:key write: JSON `from` == our DID, and the text view renders it as
    <z6Mk…tail4> (an unsigned write would render <~nick>)."""
    swept = single_line(text)
    tail = ident.did_tail4
    since = (around_seq - 1) if around_seq else None
    lim = 200 if since else 150     # no seq to anchor on: scan a wider window
    json_ok = text_ok = False
    rendered = None
    for _ in range(tries):
        j = (read_room_json(room, since=since, limit=lim) if since
             else read_room_json(room, limit=lim))
        for m in j.get("messages", []):
            if m.get("from") == ident.did and m.get("nonce") == nonce:
                json_ok = json_ok or (m.get("text") == swept)
        t = get_text(f"/r/{_q(room)}",
                     **({"since": since, "limit": lim} if since else {"limit": lim}))
        for ln in t.splitlines():
            if "z6Mk" in ln and f"…{tail}>" in ln:
                # make sure it is *our* line, not another z6Mk writer
                if swept[:16] in ln or (since and str(around_seq) in ln.split("]")[0]):
                    text_ok = True
                    rendered = ln.strip()
        if json_ok and text_ok:
            break
        time.sleep(1.0)
    return {"json_ok": json_ok, "text_ok": text_ok, "rendered": rendered}


def kv_set_signed(ident: Identity, ns: str, key: str, value: str, nonce: int,
                  *, if_absent: bool = False) -> Response:
    """Signed note write (accepted only for room-owners / room-allow).
    Signs `<ns>|<key>|<nonce>|<value>`."""
    swept = single_line(value)
    payload = f"{ns}|{key}|{nonce}|{swept}".encode("utf-8")
    sig = ident.sign_b64url(payload)
    assert len(sig) == 86
    body = {"did": ident.did, "sig": sig, "nonce": str(nonce), "value": swept}
    if if_absent:
        body["if_absent"] = True
    return _post_json(f"/kv/{_q(ns)}/{_q(key)}", body, want_json=False)
