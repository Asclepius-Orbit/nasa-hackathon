"""Live benchmark of real local models (Ollama) over the eval dataset. Opt-in, manual, never run by pytest or the gate.

Run it from spine_monitor/agent (the `evals` package lives there; --no-sync keeps the shared workspace environment):

    SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live --models qwen3.5:4b gemma4:e4b --repeat 3
    ... --dump failures.jsonl   # also write every failed run (question, tool calls, full answer, failed checks) as JSON lines

Without SPINE_AGENT_EVALS_LIVE=1 it prints why and exits 2. Defaults: --models from SPINE_AGENT_LLM_MODELS (env) or the
.env.example of the repo, --repeat 3, --base-url from AgentSettings (SPINE_AGENT_OLLAMA_BASE_URL).

For every model x case x repetition it builds the real agent (OllamaModel over the OpenAI-compatible endpoint, the real MCP
server over stdio), streams through ChatSession and grades the answer with the same deterministic evaluators as the offline
suite. Per model it prints a markdown table: pass rate (overall and per category), median/p90 TTFT, median total latency,
median tokens/s and how many runs fell back to the deterministic summary. Then the selection rule:
"winner = highest pass rate among models whose median TTFT <= 15 s".

Measurements: TTFT = start of ask -> first answer text. Total = whole turn (tool calls included). Tokens/s = output tokens
reported by the model (usage of every model request in the turn) / time spent inside the model streams, so it includes the
prompt processing of each request but not the MCP tool round trips. The usage is read by a thin wrapper model around the
OllamaModel (UsageRecorder); agent.py is untouched.

Import-safe: nothing runs at import; the network part only runs from main().
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydantic_ai
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.settings import ModelSettings
from spine_agent.agent import SpineAgent, build_agent
from spine_agent.fallback import McpStatusSource
from spine_agent.settings import AgentSettings

from evals.dataset import build_dataset
from evals.task import AgentRun, EvalInput, ToolCall, run_chat

ENV_FLAG = "SPINE_AGENT_EVALS_LIVE"
MODELS_VAR = "SPINE_AGENT_LLM_MODELS"
FALLBACK_MODELS = ["qwen3.5:4b", "qwen3.5:9b", "gemma4:e4b"]
TTFT_LIMIT_S = 15.0
ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"  # repo root

# ---------------------------------------------------------------- gate, arguments, models


def is_enabled(env: Mapping[str, str]) -> bool:
    return env.get(ENV_FLAG) == "1"


def models_from_env_example(text: str) -> list[str]:
    """The models of the `SPINE_AGENT_LLM_MODELS="a b c"` line of .env.example, or [] when it is not there."""
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if name.strip() == MODELS_VAR:
            return value.strip().strip("\"'").split()
    return []


def default_models(env: Mapping[str, str], example_text: str | None) -> list[str]:
    from_env = env.get(MODELS_VAR, "").replace(",", " ").split()
    return from_env or (models_from_env_example(example_text) if example_text else []) or list(FALLBACK_MODELS)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def parse_args(argv: Sequence[str], models: list[str], base_url: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="evals.run_live", description="Benchmark local Ollama models on the spine agent evals.")
    parser.add_argument("--models", nargs="+", default=models, help=f"Ollama tags (default: ${MODELS_VAR} or .env.example)")
    parser.add_argument("--repeat", type=_positive_int, default=3, help="repetitions per case (default 3)")
    parser.add_argument("--base-url", default=base_url, help="OpenAI-compatible endpoint of Ollama (default from AgentSettings)")
    parser.add_argument(
        "--dump", type=Path, default=None, help="write the failed runs (question, tool calls, full answer, failed checks) as JSON lines to this file"
    )
    return parser.parse_args(list(argv))


# ---------------------------------------------------------------- statistics


@dataclass(frozen=True)
class RunRecord:
    category: str
    passed: bool
    ttft_s: float | None
    total_s: float
    tokens_per_s: float | None
    fell_back: bool


@dataclass(frozen=True)
class ModelSummary:
    model: str
    runs: int
    pass_rate: float
    pass_rate_by_category: dict[str, tuple[int, int]]  # category -> (passed, runs)
    median_ttft_s: float | None
    p90_ttft_s: float | None
    median_total_s: float | None
    median_tokens_per_s: float | None
    fallbacks: int


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in (0, 1]); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-int(q * 1000) * len(ordered) // 1000))  # ceil(q * n), integer maths to dodge float noise
    return ordered[min(rank, len(ordered)) - 1]


def tokens_per_second(output_tokens: int | None, generation_s: float | None) -> float | None:
    if not output_tokens or not generation_s or generation_s <= 0:
        return None
    return output_tokens / generation_s


def summarize(model: str, records: Sequence[RunRecord]) -> ModelSummary:
    by_category: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        by_category[r.category].append(r.passed)
    ttfts = [r.ttft_s for r in records if r.ttft_s is not None]
    speeds = [r.tokens_per_s for r in records if r.tokens_per_s is not None]
    return ModelSummary(
        model=model,
        runs=len(records),
        pass_rate=sum(r.passed for r in records) / len(records) if records else 0.0,
        pass_rate_by_category={c: (sum(v), len(v)) for c, v in sorted(by_category.items())},
        median_ttft_s=median(ttfts),
        p90_ttft_s=percentile(ttfts, 0.9),
        median_total_s=median([r.total_s for r in records]),
        median_tokens_per_s=median(speeds),
        fallbacks=sum(r.fell_back for r in records),
    )


# ---------------------------------------------------------------- tables and selection


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _rate(passed: int, runs: int) -> str:
    return f"{round(100 * passed / runs) if runs else 0}% ({passed}/{runs})"


def format_summary(s: ModelSummary) -> str:
    passed = round(s.pass_rate * s.runs)
    lines = [
        f"### {s.model}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Runs | {s.runs} |",
        f"| Pass rate | {_rate(passed, s.runs)} |",
        f"| Median TTFT (s) | {_fmt(s.median_ttft_s)} |",
        f"| p90 TTFT (s) | {_fmt(s.p90_ttft_s)} |",
        f"| Median total latency (s) | {_fmt(s.median_total_s)} |",
        f"| Median tokens/s | {_fmt(s.median_tokens_per_s, 1)} |",
        f"| Fallback runs | {s.fallbacks} |",
        "",
        "| Category | Pass rate |",
        "|---|---|",
        *(f"| {category} | {_rate(p, n)} |" for category, (p, n) in s.pass_rate_by_category.items()),
    ]
    return "\n".join(lines)


def format_comparison(summaries: Sequence[ModelSummary]) -> str:
    lines = [
        "### Comparison",
        "",
        "| Model | Pass rate | Median TTFT (s) | p90 TTFT (s) | Median total (s) | Median tokens/s | Fallbacks |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.model} | {_rate(round(s.pass_rate * s.runs), s.runs)} | {_fmt(s.median_ttft_s)} | {_fmt(s.p90_ttft_s)} | {_fmt(s.median_total_s)} "
            f"| {_fmt(s.median_tokens_per_s, 1)} | {s.fallbacks} |"
        )
    return "\n".join(lines)


def select_winner(summaries: Sequence[ModelSummary], ttft_limit_s: float) -> ModelSummary | None:
    """Highest pass rate among the models whose median TTFT is within the limit; ties go to the lower median TTFT."""
    eligible = [s for s in summaries if s.median_ttft_s is not None and s.median_ttft_s <= ttft_limit_s]
    if not eligible:
        return None
    return min(eligible, key=lambda s: (-s.pass_rate, s.median_ttft_s or 0.0))


def format_selection(summaries: Sequence[ModelSummary], ttft_limit_s: float) -> str:
    rule = f"winner = highest pass rate among models whose median TTFT <= {ttft_limit_s:g} s"
    winner = select_winner(summaries, ttft_limit_s)
    if winner is None:
        return f"{rule}\nno model qualifies"
    return f"{rule}\nwinner: {winner.model} (pass rate {_rate(round(winner.pass_rate * winner.runs), winner.runs)}, median TTFT {_fmt(winner.median_ttft_s)} s)"


# ---------------------------------------------------------------- failure diagnostics


@dataclass(frozen=True)
class Check:
    """One evaluator verdict on one run: its name, whether it passed and, when it said so, why."""

    name: str
    passed: bool
    reason: str | None


@dataclass(frozen=True)
class Diagnostic:
    """Everything needed to judge by hand whether a failed run is a model error or an evaluator that is too strict."""

    case_id: str
    language: str
    category: str
    question: str
    tool_calls: list[ToolCall]
    fell_back: bool
    answer: str
    failed: list[Check]


def diagnose(case_id: str, inputs: EvalInput, category: str, run: AgentRun | None, checks: Sequence[Check]) -> Diagnostic | None:
    """The diagnostic of a run, or None when every check passed. `run` is None when the task itself raised."""
    failed = [c for c in checks if not c.passed]
    if not failed:
        return None
    return Diagnostic(
        case_id=case_id,
        language=inputs.language,
        category=category,
        question=inputs.question,
        tool_calls=list(run.tool_calls) if run else [],
        fell_back=run.fell_back if run else False,
        answer=run.text if run else "",
        failed=failed,
    )


def _format_call(call: ToolCall) -> str:
    return f"{call.name}({json.dumps(call.args, ensure_ascii=False)})"


def format_failures(model: str, diagnostics: Sequence[Diagnostic]) -> str:
    """The "### Failures: <model>" section, one block per failed run with the FULL answer; empty when nothing failed."""
    if not diagnostics:
        return ""
    lines = [f"### Failures: {model}", ""]
    for d in diagnostics:
        calls = ", ".join(_format_call(c) for c in d.tool_calls) or "none"
        lines += [
            f"#### {d.case_id} [{d.language}] ({d.category})",
            f"question: {d.question}",
            f"tool calls: {calls}",
            f"fell back: {'yes' if d.fell_back else 'no'}",
            "answer:",
            d.answer,
            "failed evaluators:",
            *(f"- {c.name}: {c.reason}" if c.reason else f"- {c.name}" for c in d.failed),
            "",
        ]
    return "\n".join(lines)


def diagnostic_json_line(model: str, d: Diagnostic) -> str:
    return json.dumps(
        {
            "model": model,
            "case": d.case_id,
            "language": d.language,
            "category": d.category,
            "question": d.question,
            "tool_calls": [{"name": c.name, "args": c.args} for c in d.tool_calls],
            "fell_back": d.fell_back,
            "answer": d.answer,
            "failed": [{"name": c.name, "reason": c.reason} for c in d.failed],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------- quiet output


def silence_noise() -> None:
    """Keep the pydantic-ai banner out of the report."""
    pydantic_ai.BANNER_ENABLED = False


def build_quiet_agent(settings: AgentSettings, model: Any, build: Callable[..., SpineAgent] = build_agent) -> SpineAgent:
    """The agent with the MCP server's stderr sent to the null device, so its INFO logs do not interleave with the report."""
    return build(settings, model=model, log_file=Path(os.devnull))


# ---------------------------------------------------------------- preflight


def _urlopen(url: str, timeout: float) -> bytes:  # pragma: no cover  (real network; preflight takes it as a parameter)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def preflight(base_url: str, models: Sequence[str], fetch: Callable[[str, float], bytes] = _urlopen) -> str | None:
    """None when Ollama answers and has every model; otherwise ONE line saying what is wrong."""
    base = base_url.rstrip("/")
    try:
        listing = json.loads(fetch(f"{base}/models", 5.0))
        available = {item["id"] for item in listing["data"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"Cannot reach Ollama at {base}: {error}. Start it (docker compose --profile llm up -d) or pass --base-url."
    missing = [m for m in models if m not in available]
    if missing:
        return f"Not pulled in Ollama: {', '.join(missing)}. Run: " + "; ".join(f"ollama pull {m}" for m in missing)
    return None


# ---------------------------------------------------------------- usage recorder


class UsageRecorder(WrapperModel):
    """Wraps a model and adds up, over every request of the run, the output tokens and the time spent inside the stream."""

    def __init__(self, wrapped: Any) -> None:
        super().__init__(wrapped)
        self.output_tokens = 0
        self.generation_s = 0.0

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse]:
        started = time.perf_counter()
        async with self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
            try:
                yield stream
            finally:
                self.generation_s += time.perf_counter() - started
                self.output_tokens += stream.usage.output_tokens


# ---------------------------------------------------------------- the benchmark (needs a real Ollama)


def _case_id(report_case: Any) -> str:
    return report_case.source_case_name or report_case.name  # pragma: no cover  (real Ollama)


async def _benchmark_model(model: str, base_url: str, repeat: int) -> tuple[list[RunRecord], list[Diagnostic]]:  # pragma: no cover  (real Ollama)
    settings = AgentSettings(model=model, ollama_base_url=base_url)
    fallback = McpStatusSource(settings)

    async def task(inputs: EvalInput) -> AgentRun:
        recorder = UsageRecorder(OllamaModel(model, provider=OllamaProvider(base_url=base_url)))
        run = await run_chat(build_quiet_agent(settings, recorder), fallback, inputs)
        run.output_tokens, run.generation_s = recorder.output_tokens, recorder.generation_s
        return run

    report = await build_dataset().evaluate(task, repeat=repeat, max_concurrency=1, progress=False)
    records: list[RunRecord] = []
    diagnostics: list[Diagnostic] = []
    for case in report.cases:
        category = case.metadata.category if case.metadata else "unknown"
        checks = [Check(name, bool(a.value), a.reason) for name, a in case.assertions.items()]
        checks += [Check(f.name, False, f.error_message) for f in case.evaluator_failures]
        diagnostic = diagnose(_case_id(case), case.inputs, category, case.output, checks)
        records.append(
            RunRecord(
                category=category,
                passed=diagnostic is None,
                ttft_s=case.output.ttft_s,
                total_s=case.output.total_s,
                tokens_per_s=tokens_per_second(case.output.output_tokens, case.output.generation_s),
                fell_back=case.output.fell_back,
            )
        )
        if diagnostic:
            diagnostics.append(diagnostic)
    # A run whose task raised (programming error, MCP server failing to start) counts as a failed run without timings.
    for failure in report.failures:
        category = failure.metadata.category if failure.metadata else "unknown"
        records.append(RunRecord(category=category, passed=False, ttft_s=None, total_s=0.0, tokens_per_s=None, fell_back=False))
        diagnostic = diagnose(_case_id(failure), failure.inputs, category, None, [Check("task_error", False, failure.error_message)])
        if diagnostic:
            diagnostics.append(diagnostic)
    return records, diagnostics


async def _benchmark(models: Sequence[str], base_url: str, repeat: int, dump: Path | None) -> list[ModelSummary]:  # pragma: no cover  (real Ollama)
    summaries: list[ModelSummary] = []
    if dump:
        dump.write_text("", encoding="utf-8")
    for model in models:
        print(f"Running {model} ({repeat} x {len(build_dataset().cases)} runs)...", file=sys.stderr, flush=True)
        records, diagnostics = await _benchmark_model(model, base_url, repeat)
        summary = summarize(model, records)
        summaries.append(summary)
        print(format_summary(summary), "", sep="\n", flush=True)
        if diagnostics:
            print(format_failures(model, diagnostics), flush=True)
        if dump and diagnostics:
            with dump.open("a", encoding="utf-8") as handle:
                handle.writelines(diagnostic_json_line(model, d) + "\n" for d in diagnostics)
    return summaries


def _run_live(env: Mapping[str, str], argv: Sequence[str]) -> int:  # pragma: no cover  (real file system, Ollama and MCP server)
    silence_noise()
    example = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else None
    args = parse_args(argv, default_models(env, example), AgentSettings().ollama_base_url)
    problem = preflight(args.base_url, args.models)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    summaries = asyncio.run(_benchmark(args.models, args.base_url, args.repeat, args.dump))
    print(format_comparison(summaries), "", format_selection(summaries, TTFT_LIMIT_S), sep="\n")
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    if not is_enabled(env):
        print(f"Live evals call real models and take minutes: set {ENV_FLAG}=1 to run them (they never run in pytest or CI).", file=sys.stderr)
        return 2
    return _run_live(env, sys.argv[1:] if argv is None else argv)  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
