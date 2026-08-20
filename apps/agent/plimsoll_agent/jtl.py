"""Reading results.jtl while JMeter is still writing it."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

# JMeter's default CSV columns, read by name rather than position: the column
# set is configurable, and a positional read would silently mis-parse a plan
# that configured it differently.
REQUIRED = ("timeStamp", "elapsed", "label", "success")


@dataclass(frozen=True)
class Sample:
    at: float
    label: str
    elapsed: int
    success: bool
    response_code: str
    message: str


class JtlReader:
    """Holds the header and any partial trailing line between reads.

    JMeter appends to the file as the run proceeds, so a read can land in the
    middle of a line. Keeping the remainder is what stops a torn row becoming
    a wrong sample.
    """

    def __init__(self) -> None:
        self._header: list[str] | None = None
        self._remainder = ""

    def feed(self, text: str) -> Iterator[Sample]:
        buffer = self._remainder + text
        # Everything up to the last newline is complete; the rest is a line
        # JMeter has not finished writing.
        complete, newline, self._remainder = buffer.rpartition("\n")
        if not newline:
            self._remainder = buffer
            return
        for row in csv.reader(io.StringIO(complete)):
            if not row:
                continue
            if self._header is None:
                # Recognised by what it contains, not by what comes first: the
                # column set is configurable in order as well as in membership,
                # and keying on position would skip every row of a reordered
                # file rather than fail visibly.
                if all(name in row for name in REQUIRED):
                    self._header = row
                continue
            record = dict(zip(self._header, row, strict=False))
            if not all(key in record for key in REQUIRED):
                continue
            try:
                yield Sample(
                    # JMeter writes epoch milliseconds.
                    at=int(record["timeStamp"]) / 1000,
                    label=record["label"],
                    elapsed=int(record["elapsed"]),
                    success=record["success"] == "true",
                    response_code=record.get("responseCode", ""),
                    message=record.get("responseMessage", ""),
                )
            except ValueError:
                # A torn or malformed row. The JTL in object storage remains
                # authoritative, so dropping one here loses nothing recoverable.
                continue
