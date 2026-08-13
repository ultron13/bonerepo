"""plimsoll.yaml -- the tester-owned half of the contract.

Two rules are enforced here rather than left to review: variables are names and
never values, and a plugin pins both a version and a checksum. An unpinned
plugin is a supply-chain hole and an unreproducible run.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

SUPPORTED_VERSION = 1
SHARED = "shared"


class ManifestError(Exception):
    """The manifest is malformed, unsupported, or breaks a contract rule."""


class ManifestData(BaseModel):
    path: str = Field(min_length=1)
    # partitioned and unique ship with the v0.2 workload work.
    distribution: str = SHARED


class ManifestPlan(BaseModel):
    path: str = Field(min_length=1)
    data: list[ManifestData] = Field(default_factory=list)
    variables: list[str] = Field(default_factory=list)

    @field_validator("variables", mode="before")
    @classmethod
    def _names_only(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        for entry in value:
            if isinstance(entry, dict):
                named = ", ".join(str(key) for key in entry)
                raise ValueError(
                    f"variable {named} carries a value; manifests declare names only, "
                    "and values resolve from the platform's secret store at run start"
                )
        return value


class ManifestPlugin(BaseModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(min_length=1)


class Manifest(BaseModel):
    version: int
    engine: str = "jmeter"
    jmeter_version: str | None = None
    plugins: list[ManifestPlugin] = Field(default_factory=list)
    plans: list[ManifestPlan] = Field(default_factory=list)


def _readable(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )


def parse_manifest(text: str) -> Manifest:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"The manifest is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise ManifestError("The manifest must be a mapping.")
    if document.get("version") != SUPPORTED_VERSION:
        raise ManifestError(
            f"Manifest schema version {document.get('version')!r} is not supported; "
            f"this Plimsoll understands version {SUPPORTED_VERSION}."
        )

    payload = dict(document)
    jmeter = payload.pop("jmeter", None)
    if isinstance(jmeter, dict):
        payload["jmeter_version"] = jmeter.get("version")

    try:
        return Manifest.model_validate(payload)
    except ValidationError as exc:
        raise ManifestError(_readable(exc)) from exc
