"""The committed TypeScript types must match what the API currently serves.

`make contracts` regenerates them from the running API's OpenAPI document, so a
contract change that never reached the frontend surfaces here rather than as a
type error in a later slice.
"""

import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.integration

GENERATED = pathlib.Path("packages/contracts/typescript/src/generated.ts")


def test_the_generated_types_are_under_version_control() -> None:
    assert GENERATED.exists(), "run `make contracts`"
    assert "TokenResponse" in GENERATED.read_text()
    # Tracked, not merely present: the frontend builds from the committed file.
    subprocess.run(  # noqa: S603
        ["git", "ls-files", "--error-unmatch", str(GENERATED)],  # noqa: S607
        check=True,
        capture_output=True,
    )


def test_regeneration_leaves_the_tree_clean() -> None:
    committed = GENERATED.read_text()
    subprocess.run(["make", "contracts"], check=True)  # noqa: S607
    assert GENERATED.read_text() == committed, (
        "generated types are stale; run `make contracts` and commit the result"
    )
