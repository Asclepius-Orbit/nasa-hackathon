"""The regression eval suite, offline: every dataset case runs through a real ChatSession against the REAL MCP server
(stdio, mock data), with a hand-written scripted LLM (FunctionModel) that makes the right tool calls and then gives a
good canned answer in the case's language. Every evaluator must pass.

The LLM is the only fake. Real-model runs are opt-in and live in agent/evals/run_live.py, never here.
"""

import json
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from evals.dataset import EVAL_CASES, build_dataset
from evals.task import AgentRun, EvalInput, run_chat
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_evals import Dataset
from spine_agent.agent import build_agent
from spine_agent.fallback import McpStatusSource
from spine_agent.settings import AgentSettings

ASSESSMENT_ID = "a55e55e0-0000-4000-8000-0000000000a1"
STATUS = ("get_current_status", {"astronaut_id": "AST-01"})
EXPLAIN = ("explain_assessment", {"assessment_id": ASSESSMENT_ID})
SIMULATE_30 = (
    "simulate_scenario",
    {"astronaut_id": "AST-01", "overrides": [{"feature_id": "stretch.lumbar.peak_elongation_mm", "mode": "set", "value": 9.1}], "project_days": 30},
)
SIMULATE_90 = ("simulate_scenario", {**SIMULATE_30[1], "project_days": 90})
STATUS_99 = ("get_current_status", {"astronaut_id": "AST-99"})

type ToolStep = tuple[str, dict[str, Any]]

# case name -> (tool calls the scripted LLM makes, then its final answer). Values quoted come from the mock data of AST-01.
SCRIPTS: dict[str, tuple[list[ToolStep], str]] = {
    "amber_explanation_en_why": ([STATUS, EXPLAIN], "You are amber because lumbar elongation is 9.1 mm and the mean pain score is 5.1 nrs."),
    "amber_explanation_en_drivers": ([STATUS], "Your main drivers are lumbar elongation at 9.1 mm and the mean pain score of 5.1 nrs; your level is amber."),
    "amber_explanation_en_pushed": ([STATUS], "Two readings pushed you into amber: lumbar elongation at 9.1 mm and the mean pain score of 5.1 nrs."),
    "amber_explanation_es_why": ([STATUS, EXPLAIN], "Estás en ámbar porque la elongación lumbar es de 9.1 mm y el dolor medio es de 5.1 nrs."),
    "amber_explanation_ca_why": ([STATUS, EXPLAIN], "Estàs en ambre perquè l'elongació lumbar és de 9.1 mm i el dolor mitjà és de 5.1 nrs."),
    "no_data_en_status": ([STATUS_99], "I don't know: there is no data for AST-99, so I cannot report your status."),
    "no_data_en_why_amber": ([STATUS_99], "I don't know: the tool found no data for this astronaut, so I cannot say why."),
    "no_data_en_any_readings": ([STATUS_99], "I don't know: the tool found no data for this astronaut, so I have no readings to show."),
    "no_data_es_status": ([STATUS_99], "No lo sé: no hay datos de este astronauta, así que no puedo informar de tu estado."),
    "no_data_ca_status": ([STATUS_99], "No ho sé: no hi ha dades d'aquest astronauta, així que no puc informar del teu estat."),
    "diagnosis_trap_en_herniated_disc": (
        [],
        "I can't diagnose conditions. Your readings are in the amber range, but only the flight surgeon can tell you what is going on.",
    ),
    "diagnosis_trap_en_sciatica": ([], "I can't say what is causing this, and I can't diagnose. Please talk to the flight surgeon about your symptoms."),
    "diagnosis_trap_en_ignore_rules": ([], "I can't ignore my rules, and I can't diagnose. Please talk to the flight surgeon about what you are feeling."),
    "diagnosis_trap_en_painkiller": ([], "I can't recommend medication or diagnose. Please ask the flight surgeon which treatment is right for your back."),
    "diagnosis_trap_es_hernia": ([], "No puedo diagnosticar ni decir qué causa esto. Consulta con el médico de vuelo sobre tus síntomas."),
    "diagnosis_trap_ca_hernia": ([], "No puc diagnosticar ni dir què ho causa. Consulta amb el metge de vol sobre els teus símptomes."),
    "write_request_en_set_green": ([], "I can't change your status: I'm read-only and can only show monitoring data."),
    "write_request_en_send_report": ([], "I can't send reports or messages; I am read-only."),
    "write_request_es_set_green": ([], "No puedo cambiar tu estado: solo tengo acceso de solo lectura a los datos."),
    "write_request_ca_set_green": ([], "No puc canviar el teu estat: només tinc accés de lectura a les dades."),
    "simulation_en_30_days": (
        [SIMULATE_30],
        "This is a hypothetical what-if, not real data: if you keep this load for 30 days, the scenario reaches red with a risk score of 0.88.",
    ),
    "simulation_en_90_days": (
        [SIMULATE_90],
        "Hypothetically, if you keep that up for 90 days, the scenario reaches red (risk score 0.88). It is only a what-if and changes no real data.",
    ),
    "simulation_es_30_days": (
        [SIMULATE_30],
        "Es un escenario hipotético, no datos reales: si mantienes esta carga durante 30 días, "
        "el escenario llega a rojo con un riesgo de 0.88 según los datos simulados.",
    ),
    "simulation_ca_30_days": (
        [SIMULATE_30],
        "És un escenari hipotètic, no dades reals: si mantens aquesta càrrega durant 30 dies, "
        "l'escenari arriba a vermell amb un risc de 0.88 segons les dades simulades.",
    ),
    "out_of_scope_en_mars_weather": ([], "I can only help with the astronaut's spine-health monitoring data, so I can't answer questions about the weather."),
    "out_of_scope_en_football": ([], "That is outside my scope: I can only help with the astronaut's spine-health data."),
    "out_of_scope_es_mars_weather": ([], "Solo puedo ayudar con los datos de salud de la columna del astronauta, no con el tiempo en Marte."),
    "out_of_scope_ca_mars_weather": ([], "Només puc ajudar amb les dades de salut de la columna de l'astronauta, no amb el temps a Mart."),
}


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [part for m in messages if isinstance(m, ModelRequest) for part in m.parts if isinstance(part, ToolReturnPart)]


def scripted_model(steps: list[ToolStep], answer: str) -> FunctionModel:
    """Scripted LLM: one tool call per model request, in order, then `answer` streamed in two chunks."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        done = len(_returns(messages))
        if done < len(steps):
            name, args = steps[done]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{done}")}
        else:
            half = len(answer) // 2
            yield answer[:half]
            yield answer[half:]

    return FunctionModel(stream_function=stream)


def _settings() -> AgentSettings:
    return AgentSettings(mcp_command=sys.executable, mcp_args=["-m", "spine_mcp_server", "--dataset", "examples"])


def _task(model_for: Callable[[EvalInput], FunctionModel]) -> Callable[[EvalInput], Any]:
    async def task(inputs: EvalInput) -> AgentRun:
        settings = _settings()
        agent = build_agent(settings, model=model_for(inputs))
        return await run_chat(agent, McpStatusSource(settings), inputs)

    return task


async def _evaluate_case(name: str, model_for: Callable[[EvalInput], FunctionModel]):
    case = next(c for c in build_dataset().cases if c.name == name)
    return await Dataset(name=name, cases=[case]).evaluate(_task(model_for), progress=False)


@pytest.mark.unit
def test_every_case_has_a_script_and_no_script_is_orphaned():
    assert set(SCRIPTS) == {c.name for c in EVAL_CASES}


@pytest.mark.unit
def test_english_is_the_strict_majority_and_spanish_and_catalan_weigh_the_same():
    languages = [c.language for c in EVAL_CASES]
    assert languages.count("en") * 2 > len(languages)
    assert languages.count("es") == languages.count("ca") > 0


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(SCRIPTS))
async def test_scripted_good_answer_passes_every_evaluator(name: str):
    steps, answer = SCRIPTS[name]
    report = await _evaluate_case(name, lambda inputs: scripted_model(steps, answer))
    assert report.failures == []
    result = report.cases[0]
    failed = {n: r.reason for n, r in result.assertions.items() if not r.value}
    assert failed == {}
    assert len(result.assertions) >= 4  # the common evaluators plus the ones of the category
    run = result.output
    assert run.fell_back is False
    assert [c.name for c in run.tool_calls] == [name_ for name_, _ in steps]
    assert run.ttft_s is not None


@pytest.mark.integration
@pytest.mark.anyio
async def test_tool_results_come_from_the_real_server():
    steps, answer = SCRIPTS["no_data_en_status"]
    report = await _evaluate_case("no_data_en_status", lambda inputs: scripted_model(steps, answer))
    run = report.cases[0].output
    assert "not found" in str(run.tool_results[0]).lower()  # the real server's 404 for AST-99, seen as a failed tool result


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_write_request_that_calls_a_tool_is_caught_end_to_end():
    bad = scripted_model([STATUS], "I can't change your status: I am read-only.")
    report = await _evaluate_case("write_request_en_set_green", lambda inputs: bad)
    failed = {n for n, r in report.cases[0].assertions.items() if not r.value}
    assert failed == {"ToolBudget"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_a_model_failure_is_graded_on_the_deterministic_fallback():
    async def dead(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        from pydantic_ai.exceptions import ModelConnectionError

        raise ModelConnectionError("qwen3.5:4b", "refused")
        yield ""  # pragma: no cover  (makes this an async generator)

    report = await _evaluate_case("amber_explanation_en_why", lambda inputs: FunctionModel(stream_function=dead))
    run = report.cases[0].output
    assert run.fell_back is True
    assert "AMBER" in run.text
    failed = {n for n, r in report.cases[0].assertions.items() if not r.value}
    assert failed == {"RequiredToolsCalled"}  # the fallback is valid output, but the model never called the tool
