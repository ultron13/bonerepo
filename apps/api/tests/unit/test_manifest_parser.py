import pathlib

import pytest

from plimsoll_api.plans.manifest import ManifestError, parse_manifest

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "manifests"


def test_a_valid_manifest_parses() -> None:
    manifest = parse_manifest((FIXTURES / "valid.yaml").read_text())
    assert manifest.version == 1
    assert manifest.jmeter_version == "5.6.3"
    assert manifest.plans[0].path == "checkout.jmx"
    assert manifest.plans[0].variables == ["API_HOST", "API_TOKEN"]
    assert manifest.plans[0].data[0].path == "data/users.csv"
    assert manifest.plans[0].data[0].distribution == "shared"
    assert manifest.plugins[0].id == "jpgc-casutg"


def test_a_variable_carrying_a_value_is_refused() -> None:
    """Values come from the platform's secret store, never the manifest."""
    with pytest.raises(ManifestError) as raised:
        parse_manifest((FIXTURES / "secret.yaml").read_text())
    assert "API_TOKEN" in str(raised.value)


def test_an_unsupported_schema_version_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("version: 2\nengine: jmeter\nplans: []\n")


def test_an_unpinned_plugin_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("version: 1\nengine: jmeter\nplugins:\n  - id: jpgc-casutg\nplans: []\n")


def test_a_plugin_without_a_checksum_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest(
            "version: 1\nengine: jmeter\nplugins:\n  - id: jpgc-casutg\n"
            '    version: "2.10"\nplans: []\n'
        )


def test_the_distribution_defaults_to_shared() -> None:
    manifest = parse_manifest(
        "version: 1\nengine: jmeter\nplans:\n  - path: p.jmx\n    data:\n      - path: data/a.csv\n"
    )
    assert manifest.plans[0].data[0].distribution == "shared"


def test_malformed_yaml_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("version: 1\n  engine: [unclosed\n")


def test_a_document_that_is_not_a_mapping_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("- just\n- a\n- list\n")
