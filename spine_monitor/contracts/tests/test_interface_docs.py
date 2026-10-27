from pathlib import Path

import pytest
import yaml

from spine_contracts import topics

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
DOCS = ["asyncapi.yaml", "openapi.yaml"]


def load(name: str):
    return yaml.safe_load((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


def iter_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


@pytest.mark.parametrize("name", DOCS)
def test_external_refs_point_to_existing_schemas(name):
    external = [ref for ref in iter_refs(load(name)) if not ref.startswith("#")]

    assert external, "the document should reference the JSON Schemas"
    for ref in external:
        assert (CONTRACTS_DIR / ref.split("#")[0]).is_file(), f"{name}: {ref} does not exist"


@pytest.mark.parametrize("name", DOCS)
def test_internal_refs_resolve(name):
    document = load(name)
    for ref in (r for r in iter_refs(document) if r.startswith("#/")):
        node = document
        for part in ref[2:].split("/"):
            assert part in node, f"{name}: broken {ref}"
            node = node[part]


def test_asyncapi_addresses_match_the_topic_helpers():
    channels = load("asyncapi.yaml")["channels"]
    ids = {"astronaut_id": "AST-01", "device_id": "wb-01", "stream_id": "emg_lat_left"}

    assert channels["stream"]["address"].format(**ids) == topics.stream("AST-01", "wb-01", "emg_lat_left")
    assert channels["status"]["address"].format(**ids) == topics.status("AST-01", "wb-01")
    assert channels["cmd"]["address"].format(**ids) == topics.cmd("AST-01", "wb-01")
    assert channels["ack"]["address"].format(**ids) == topics.ack("AST-01", "wb-01")
    assert channels["clock"]["address"].format(**ids) == topics.clock("AST-01", "wb-01")


def test_openapi_paths_are_the_engine_endpoints():
    assert set(load("openapi.yaml")["paths"]) == {"/health", "/features", "/baseline", "/assess", "/simulate"}
