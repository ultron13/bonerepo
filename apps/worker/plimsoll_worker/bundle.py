"""One fetch per run, packed and staged for every generator.

The alternative -- a clone per generator -- would put the customer's Git
credential inside every container running a user-supplied plan, and would need
git and credential handling in the generator image. Staging centrally keeps the
credential in the control plane and makes "every generator ran the same bytes"
a digest rather than a hope.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plimsoll_api import storage
from plimsoll_api.git.client import fetch_plan

# Fixed metadata, so the same tree packs to the same bytes on every worker and
# the digest means what it says.
EPOCH = 0


@dataclass(frozen=True)
class BundleRef:
    key: str
    sha256: str


def _reset(entry: tarfile.TarInfo) -> tarfile.TarInfo:
    entry.uid = entry.gid = 0
    entry.uname = entry.gname = ""
    entry.mtime = EPOCH
    return entry


def _pack(roots: list[tuple[Path, str]]) -> bytes:
    buffer = io.BytesIO()
    # The gzip layer is built explicitly with mtime=0: tarfile's own "w:gz"
    # stamps the current time into the gzip header, which would give two
    # identical trees two different digests.
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6, mtime=EPOCH) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for root, prefix in roots:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.add(
                            path, arcname=f"{prefix}/{path.relative_to(root)}", filter=_reset
                        )
    return buffer.getvalue()


async def stage(run_id: uuid.UUID, plans: list[dict[str, Any]]) -> BundleRef:
    """Fetch each plan at its pinned commit and pack the directory it lives in.

    The plan's own directory is what travels, because everything a plan
    references -- CSV data, .properties, the manifest -- resolves by relative
    path from the plan file.
    """
    payloads: list[bytes] = []
    for plan in plans:
        plan_path = str(plan["planPath"])
        directory = Path(plan_path).parent.as_posix()
        async with fetch_plan(plan["access"], str(plan["commitSha"]), plan_path) as root:
            payloads.append(_pack([(root / directory, directory)]))

    # v0.1 runs one plan per test; when that changes, the packs merge here
    # rather than at the call site.
    payload = payloads[0]
    digest = hashlib.sha256(payload).hexdigest()
    key = storage.bundle_key(run_id)
    storage.put_bytes(key, payload)
    return BundleRef(key=key, sha256=digest)
