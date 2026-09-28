"""Firmware identity and integrity (version, build timestamp, commit hash,
configuration hash, firmware image hash)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FirmwareIdentity:
    version: str             # e.g. FW-1.0.0
    build_timestamp: str     # ISO-8601 UTC
    commit_hash: str
    config_hash: str         # SHA-256 of the flight configuration blob
    firmware_hash: str       # SHA-256 of the firmware image

    def mismatches(self, expected: "FirmwareIdentity") -> list[str]:
        return [f for f in ("version", "commit_hash", "config_hash", "firmware_hash")
                if getattr(self, f) != getattr(expected, f)]


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
