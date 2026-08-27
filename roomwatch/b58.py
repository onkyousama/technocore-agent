"""base58btc (Bitcoin alphabet). Needed to build a did:key string.

The stdlib has no base58 and `cryptography` does not expose one, so this is a
tiny self-contained implementation.
"""

from __future__ import annotations

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = []
    while n > 0:
        n, rem = divmod(n, 58)
        out.append(_ALPHABET[rem])
    # leading zero bytes become leading '1's
    for b in data:
        if b == 0:
            out.append("1")
        else:
            break
    return "".join(reversed(out))


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _INDEX[ch]
    full = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + full
