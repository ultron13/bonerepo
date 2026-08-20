"""Grouping failures by what they are, not by how they were worded.

A run that fails a million times has usually failed at a handful of things a
million times over. Storing each occurrence recreates the anti-pattern the
architecture forbids; storing a group with a count and one readable example
tells an operator the same thing and fits on a screen.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from plimsoll_agent.jtl import Sample

# What varies between occurrences of one fault and must not split its group:
# identifiers, timings, hostnames with an ordinal, hexadecimal blobs.
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_NUMBER = re.compile(r"\d+")

# Enough to recognise a fault, bounded so one enormous message cannot dominate
# a row.
SAMPLE_LIMIT = 2000


def _normalise(message: str) -> str:
    text = _UUID.sub("<id>", message)
    text = _HEX.sub("<hex>", text)
    return _NUMBER.sub("<n>", text).strip()


def fingerprint(code: str, message: str, transaction: str) -> str:
    """Stable across occurrences, distinct across faults.

    The transaction is part of the identity: the same message on two different
    requests is two different problems to investigate.
    """
    material = f"{transaction}\x00{code}\x00{_normalise(message)}"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class _Group:
    code: str
    message: str
    transaction: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    sample: str = ""


@dataclass
class ErrorFolder:
    """One per generator, drained alongside the metric windows."""

    _groups: dict[str, _Group] = field(default_factory=dict)

    def record(self, sample: Sample) -> None:
        if sample.success:
            return
        key = fingerprint(sample.response_code, sample.message, sample.label)
        group = self._groups.get(key)
        if group is None:
            group = self._groups[key] = _Group(
                code=sample.response_code,
                message=_normalise(sample.message)[:SAMPLE_LIMIT],
                transaction=sample.label,
                first_seen=sample.at,
                # The first occurrence verbatim: a count without an example
                # tells an operator a number and nothing they can act on.
                sample=sample.message[:SAMPLE_LIMIT],
            )
        group.count += 1
        group.first_seen = min(group.first_seen, sample.at)
        group.last_seen = max(group.last_seen, sample.at)

    def drain(self) -> list[dict[str, str]]:
        drained = [
            {
                "fingerprint": key,
                "errorCode": group.code,
                "message": group.message,
                "transaction": group.transaction,
                "count": str(group.count),
                "firstSeen": str(group.first_seen),
                "lastSeen": str(group.last_seen),
                "sample": group.sample,
            }
            for key, group in sorted(self._groups.items())
        ]
        self._groups.clear()
        return drained
