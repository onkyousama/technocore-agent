"""Ed25519 identity: generate locally, load, derive did:key, sign.

The private key is created on this PC with `cryptography` and written to
~/.technocore. It is never sent anywhere (requirement #1). Only the *public*
did:key string and the public key bytes are ever transmitted.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import config
from .b58 import b58encode, b58decode

# multicodec varint prefix for an ed25519 public key
_ED25519_PUB_MULTICODEC = b"\xed\x01"


# --- did:key --------------------------------------------------------------

def did_from_public_bytes(pub_raw: bytes) -> str:
    if len(pub_raw) != 32:
        raise ValueError("ed25519 public key must be 32 bytes")
    return "did:key:z" + b58encode(_ED25519_PUB_MULTICODEC + pub_raw)


def public_bytes_from_did(did: str) -> bytes:
    if not did.startswith("did:key:z"):
        raise ValueError("not a did:key")
    decoded = b58decode(did[len("did:key:z"):])
    if decoded[:2] != _ED25519_PUB_MULTICODEC:
        raise ValueError("did:key is not ed25519-pub")
    return decoded[2:]


def fingerprint(did: str) -> str:
    """First 16 lowercase hex chars of SHA-256(did:key string)."""
    return hashlib.sha256(did.encode("utf-8")).hexdigest()[:16]


def did_note_location(did: str) -> tuple[str, str]:
    """(namespace, key) for the DID note: /kv/did-<first 2>/<next 14>."""
    fp = fingerprint(did)
    return f"did-{fp[:2]}", fp[2:16]


# --- key file management ------------------------------------------------

def _lock_down(path) -> None:
    """Restrict the private key file to the current user."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 (POSIX view)
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
            # Reset inheritance, grant only this user full control.
            # (output discarded — icacls prints in the OS codepage)
            subprocess.run(
                ["icacls", str(path), "/inheritance:r",
                 "/grant:r", f"{user}:(F)"],
                check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def generate(force: bool = False) -> "Identity":
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if config.KEY_PEM.exists() and not force:
        raise FileExistsError(
            f"key already exists at {config.KEY_PEM}; pass force=True to replace"
        )
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    config.KEY_PEM.write_bytes(pem)
    config.KEY_RAW.write_bytes(raw)
    _lock_down(config.KEY_PEM)
    _lock_down(config.KEY_RAW)

    ident = Identity(priv)
    config.DID_FILE.write_text(ident.did + "\n", encoding="utf-8")
    pub_raw = ident.public_bytes
    config.PUB_FILE.write_text(
        "ed25519 public key (safe to publish)\n"
        f"hex        : {pub_raw.hex()}\n"
        f"multibase  : z{b58encode(_ED25519_PUB_MULTICODEC + pub_raw)}\n"
        f"did:key    : {ident.did}\n",
        encoding="utf-8",
    )
    return ident


def load() -> "Identity":
    if not config.KEY_PEM.exists():
        raise FileNotFoundError(
            f"no key at {config.KEY_PEM}; run `python -m roomwatch bootstrap` first"
        )
    priv = serialization.load_pem_private_key(
        config.KEY_PEM.read_bytes(), password=None
    )
    if not isinstance(priv, Ed25519PrivateKey):
        raise TypeError("key file is not an Ed25519 private key")
    return Identity(priv)


class Identity:
    def __init__(self, priv: Ed25519PrivateKey):
        self._priv = priv
        self.public_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.did = did_from_public_bytes(self.public_bytes)

    @property
    def did_tail4(self) -> str:
        return self.did[-4:]

    def sign_b64url(self, payload: bytes) -> str:
        """Ed25519 signature as unpadded base64url — 86 characters."""
        sig = self._priv.sign(payload)
        return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")

    # convenience: verify our own signature (used by the read-back check)
    def verify(self, payload: bytes, sig_b64url: str) -> bool:
        pad = "=" * (-len(sig_b64url) % 4)
        sig = base64.urlsafe_b64decode(sig_b64url + pad)
        try:
            self._priv.public_key().verify(sig, payload)
            return True
        except Exception:
            return False
