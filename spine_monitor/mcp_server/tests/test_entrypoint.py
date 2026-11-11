"""Punto de entrada del servidor: opciones (`parse_args`), conversión de `--now` y elección de dataset/reloj en `build_server`."""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import spine_mcp_server
from mcp.client.client import Client
from mcp.server.mcpserver import MCPServer
from spine_mcp_server import _aware_iso, build_server, parse_args  # pyright: ignore[reportPrivateUsage] -- se prueba el conversor de `--now`
from spine_mcp_server.http_client import HttpAppReader, HttpSimulator
from spine_mcp_server.mocks import EXAMPLES_DIR

real_client = httpx.Client
AST = "AST-01"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_defaults_are_the_demo_dataset_and_no_pinned_clock():
    args = parse_args([])
    assert args.dataset == "demo"
    assert args.now is None
    assert sorted(vars(args)) == ["dataset", "now"]


def test_dataset_examples_is_accepted_and_demo_can_be_explicit():
    assert parse_args(["--dataset", "examples"]).dataset == "examples"
    assert parse_args(["--dataset", "demo"]).dataset == "demo"
    assert parse_args(["--dataset=examples"]).dataset == "examples"
    assert parse_args(["--dataset", "http"]).dataset == "http"


def test_invalid_dataset_exits_with_status_2_and_lists_the_choices(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as caught:
        parse_args(["--dataset", "other"])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "spine_mcp_server: error: argument --dataset: invalid choice: 'other'" in err
    assert "'demo'" in err and "'examples'" in err and "'http'" in err


def test_now_accepts_z_and_offsets_and_converts_to_utc():
    assert parse_args(["--now", "2026-11-13T12:00:00Z"]).now == NOW
    plus = parse_args(["--now", "2026-11-13T14:00:00+02:00"]).now
    assert plus == NOW
    assert plus.utcoffset() == timedelta(0)
    assert plus.hour == 12  # convertido, no solo igual: la hora de pared es la UTC
    minus = parse_args(["--now", "2026-11-13T06:30:00-05:30"]).now
    assert (minus.hour, minus.minute, minus.tzinfo) == (12, 0, UTC)
    assert parse_args(["--now=2026-11-13T12:34:56.789Z"]).now == NOW.replace(minute=34, second=56, microsecond=789000)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--now", "2026-11-13T12:00:00"], "argument --now: timestamp needs a timezone (e.g. 'Z'): '2026-11-13T12:00:00'"),
        (["--now", "2026-11-13"], "argument --now: timestamp needs a timezone (e.g. 'Z'): '2026-11-13'"),
        (["--now", "yesterday"], "argument --now: not an ISO 8601 timestamp: 'yesterday'"),
        (["--now", ""], "argument --now: not an ISO 8601 timestamp: ''"),
    ],
)
def test_now_rejects_naive_and_malformed_values_with_the_exact_message(argv: list[str], message: str, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as caught:
        parse_args(argv)
    assert caught.value.code == 2
    assert f"spine_mcp_server: error: {message}" in capsys.readouterr().err


def test_unknown_option_exits_with_status_2(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as caught:
        parse_args(["--bogus"])
    assert caught.value.code == 2
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_help_documents_every_option_choice_and_text(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("NO_COLOR", "1")
    with pytest.raises(SystemExit) as caught:
        parse_args(["--help"])
    assert caught.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert out.startswith("usage: spine_mcp_server [-h] [--dataset {demo,examples,http}] [--now NOW]")
    assert "Servidor MCP de Asclepius Orbit por stdio." in out
    assert "--dataset {demo,examples,http}" in out
    assert (
        "demo: 14 días de AST-01 relativos al reloj (por defecto); examples: la foto única de los ejemplos de los contratos; "
        "http: la API de lectura de la app en SPINE_APP_URL (por defecto http://127.0.0.1:8002) y el motor en SPINE_ENGINE_URL "
        "(por defecto http://127.0.0.1:8001)." in out
    )
    assert "--now NOW" in out
    assert "Fija el reloj del dataset demo (ISO 8601 con zona, p. ej. 2026-11-13T12:00:00Z)." in out


def test_aware_iso_converts_any_offset_to_utc():
    assert _aware_iso("2026-11-13T12:00:00Z") == NOW
    converted = _aware_iso("2026-11-13T01:00:00+13:00")
    assert converted == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert converted.tzinfo is UTC
    assert _aware_iso("2026-11-13T12:00:00+00:00").tzinfo is UTC
    assert _aware_iso("2026-11-13T12:00:00.250000Z").microsecond == 250000
    assert _aware_iso("2026-11-13T12:00:00-07:00") == datetime(2026, 11, 13, 19, 0, tzinfo=timezone(timedelta(0)))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("2026-11-13T12:00:00", "timestamp needs a timezone (e.g. 'Z'): '2026-11-13T12:00:00'"),
        ("nonsense", "not an ISO 8601 timestamp: 'nonsense'"),
    ],
)
def test_aware_iso_errors_name_the_offending_text(text: str, message: str):
    with pytest.raises(argparse.ArgumentTypeError) as caught:
        _aware_iso(text)
    assert str(caught.value) == message


def test_malformed_timestamp_error_keeps_the_original_exception_as_cause():
    with pytest.raises(argparse.ArgumentTypeError) as caught:
        _aware_iso("nonsense")
    assert isinstance(caught.value.__cause__, ValueError)


class _FrozenClock(datetime):
    """Frontera del reloj: `datetime.now(tz)` devuelve un instante fijo y anota la zona pedida."""

    asked: list[object] = []  # noqa: RUF012 -- registro compartido, intencional en este doble

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206 -- misma firma que `datetime.now`
        cls.asked.append(tz)
        return datetime(2026, 11, 13, 12, 59, 59, tzinfo=UTC)


class _ForbiddenClock(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206 -- misma firma que `datetime.now`
        raise AssertionError("the examples dataset must not read the clock")


async def _status(argv: list[str]) -> dict[str, object]:
    async with Client(build_server(argv)) as client:
        result = await client.call_tool("get_current_status", {"astronaut_id": AST})
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.integration
@pytest.mark.anyio
async def test_demo_without_now_reads_the_wall_clock_in_utc(monkeypatch: pytest.MonkeyPatch):
    _FrozenClock.asked = []
    monkeypatch.setattr(spine_mcp_server, "datetime", _FrozenClock)
    status = await _status([])
    assert _FrozenClock.asked == [UTC]
    assert status["generated_at"] == "2026-11-13T12:00:00Z"  # 12:59:59 redondea a la hora
    assert status["latest_assessment"]["as_of"] == "2026-11-13T12:00:00Z"  # pyright: ignore[reportIndexIssue,reportArgumentType] -- JSON anidado sin tipar


@pytest.mark.integration
@pytest.mark.anyio
async def test_pinned_now_wins_over_the_clock(monkeypatch: pytest.MonkeyPatch):
    _FrozenClock.asked = []
    monkeypatch.setattr(spine_mcp_server, "datetime", _FrozenClock)
    status = await _status(["--now", "2026-11-06T09:30:00Z"])
    assert _FrozenClock.asked == []
    assert status["generated_at"] == "2026-11-06T09:00:00Z"


@pytest.mark.integration
@pytest.mark.anyio
async def test_examples_dataset_serves_the_contract_example_without_touching_the_clock(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(spine_mcp_server, "datetime", _ForbiddenClock)
    status = await _status(["--dataset", "examples"])
    assert status["latest_assessment"]["assessment_id"] == "a55e55e0-0000-4000-8000-0000000000a1"  # pyright: ignore[reportIndexIssue,reportArgumentType] -- JSON anidado sin tipar
    assert status["generated_at"] != "2026-11-13T12:00:00Z"


@pytest.mark.integration
@pytest.mark.anyio
async def test_demo_server_wires_the_demo_simulator_and_assess_base():
    async with Client(build_server(["--now", "2026-11-13T12:00:00Z"])) as client:
        result = await client.call_tool(
            "simulate_scenario", {"astronaut_id": AST, "overrides": [{"feature_id": "stretch.lumbar.peak_elongation_mm", "mode": "add", "value": 5}]}
        )
    assert result.structured_content is not None
    scenario = result.structured_content["scenario"]
    assert (scenario["risk_score"], scenario["level"]) == (0.88, "red")
    assert result.structured_content["reference"]["risk_score"] == 0.62


def test_building_the_server_does_not_import_jsonschema():
    # Cold start: jsonschema (via rfc3987_syntax) costs ~1.4 s and nothing validates against schemas until a tool runs.
    code = "import sys, spine_mcp_server; spine_mcp_server.build_server(['--dataset', 'examples']); print('jsonschema' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


def _reader(server: MCPServer) -> HttpAppReader:
    """El `AppReader` con el que `create_server` ha cableado la tool `get_current_status`."""
    closure = server._tool_manager._tools["get_current_status"].fn.__closure__  # pyright: ignore[reportPrivateUsage,reportUnknownMemberType,reportAttributeAccessIssue] -- `_tool_manager` y los cierres no son API pública
    assert closure is not None
    readers = [cell.cell_contents for cell in closure if isinstance(cell.cell_contents, HttpAppReader)]
    assert len(readers) == 1
    return readers[0]


def _simulator(server: MCPServer) -> HttpSimulator:
    closure = server._tool_manager._tools["simulate_scenario"].fn.__closure__  # pyright: ignore[reportPrivateUsage,reportUnknownMemberType,reportAttributeAccessIssue] -- se comprueba el cableado
    assert closure is not None
    simulators = [cell.cell_contents for cell in closure if isinstance(cell.cell_contents, HttpSimulator)]
    assert len(simulators) == 1
    return simulators[0]


@pytest.mark.parametrize(
    "configured, expected", [(None, "http://127.0.0.1:8001"), ("   ", "http://127.0.0.1:8001"), (" http://engine.test:9000/ ", "http://engine.test:9000")]
)
def test_http_dataset_wires_engine_url(monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: str):
    if configured is None:
        monkeypatch.delenv("SPINE_ENGINE_URL", raising=False)
    else:
        monkeypatch.setenv("SPINE_ENGINE_URL", configured)
    assert _simulator(build_server(["--dataset", "http"]))._base == expected  # pyright: ignore[reportPrivateUsage] -- se comprueba la URL configurada


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_dataset_reads_status_from_the_url_in_spine_app_url(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404, json={"type": "not_found", "title": "Not found", "status": 404})

    monkeypatch.setenv("SPINE_APP_URL", "http://app.test:9000")
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    async with Client(build_server(["--dataset", "http"])) as client:
        result = await client.call_tool("get_current_status", {"astronaut_id": AST})
    assert seen == ["http://app.test:9000/crew/AST-01/status"]
    assert result.is_error


@pytest.mark.integration
@pytest.mark.anyio
async def test_http_dataset_simulation_takes_the_base_from_the_app_and_posts_it_to_the_engine(monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=json.loads((EXAMPLES_DIR / "engine/assess-request.json").read_text(encoding="utf-8")))
        return httpx.Response(200, json=json.loads((EXAMPLES_DIR / "engine/simulate-response.json").read_text(encoding="utf-8")))

    monkeypatch.setenv("SPINE_APP_URL", "http://app.test:9000")
    monkeypatch.setenv("SPINE_ENGINE_URL", "http://engine.test:9001")
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    async with Client(build_server(["--dataset", "http"])) as client:
        result = await client.call_tool("simulate_scenario", {"astronaut_id": AST, "overrides": []})
    assert not result.is_error
    assert seen == [("GET", "http://app.test:9000/crew/AST-01/assess-base"), ("POST", "http://engine.test:9001/simulate")]


def test_http_dataset_defaults_to_the_local_app_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SPINE_APP_URL", raising=False)
    assert _reader(build_server(["--dataset", "http"]))._base == "http://127.0.0.1:8002"  # pyright: ignore[reportPrivateUsage] -- se comprueba la URL con la que se cablea el cliente


@pytest.mark.parametrize("blank", ["", "   "])
def test_http_dataset_uses_the_default_url_when_spine_app_url_is_blank(monkeypatch: pytest.MonkeyPatch, blank: str):
    monkeypatch.setenv("SPINE_APP_URL", blank)
    assert _reader(build_server(["--dataset", "http"]))._base == "http://127.0.0.1:8002"  # pyright: ignore[reportPrivateUsage] -- se comprueba la URL con la que se cablea el cliente


def test_http_dataset_reads_the_env_var_when_building_not_when_importing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPINE_APP_URL", "http://other.test:1234/")
    assert _reader(build_server(["--dataset", "http"]))._base == "http://other.test:1234"  # pyright: ignore[reportPrivateUsage] -- se comprueba la URL con la que se cablea el cliente
    monkeypatch.setenv("SPINE_APP_URL", "http://second.test")
    assert _reader(build_server(["--dataset", "http"]))._base == "http://second.test"  # pyright: ignore[reportPrivateUsage] -- se comprueba la URL con la que se cablea el cliente


@pytest.mark.integration
@pytest.mark.anyio
async def test_spine_app_url_is_ignored_by_the_demo_dataset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPINE_APP_URL", "http://unreachable.invalid")
    status = await _status(["--now", "2026-11-13T12:00:00Z"])
    assert status["generated_at"] == "2026-11-13T12:00:00Z"
