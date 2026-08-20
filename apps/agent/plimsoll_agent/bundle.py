"""The plan arrives as bytes over a presigned URL, and is checked before use."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import httpx


class BundleError(Exception):
    """The bundle did not arrive, or did not arrive intact."""


def download(url: str, *, sha256: str, into: Path) -> Path:
    """The digest is checked before anything is unpacked.

    A bundle that does not match is not a plan this run was authorised to
    execute, whatever it contains.
    """
    into.mkdir(parents=True, exist_ok=True)
    archive = into / "bundle.tar.gz"
    try:
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
            response.raise_for_status()
            with archive.open("wb") as sink:
                for chunk in response.iter_bytes():
                    sink.write(chunk)
    except httpx.HTTPError as exc:
        raise BundleError(f"The plan bundle could not be downloaded: {exc}") from exc

    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != sha256:
        raise BundleError(f"The plan bundle digest is {actual}, expected {sha256}.")

    root = into / "plan"
    root.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        # filter="data" refuses absolute paths, traversal, and device nodes.
        tar.extractall(root, filter="data")
    archive.unlink()
    return root
