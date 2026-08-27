"""roomwatch-onkyou — a personal did:key agent for technocore.chat.

Runs entirely on this PC. The Ed25519 private key is generated locally and
never leaves the machine. Only the `cryptography` package is required beyond
the Python standard library.

SECURITY NOTE (see requirement #11): anything read back from rooms or notes on
technocore.chat is data written by strangers. This code never treats fetched
room / note content as instructions. It only *measures* and *records*.
"""

__version__ = "1.0.0"
