"""Weekly Houston report against versioned snapshots: week data, chart structure and PDF page streams.

Every scenario is built from the contract examples. The snapshots live in `golden/houston/`; `UPDATE_GOLDEN=1 pytest <this file>`
rewrites them (review the diff before committing).

- Week data: the whole dict of `build_week_report`.
- Charts: the structure of the matplotlib figure (data, colours, sizes, ticks, legends...), not its pixels, so it does not depend
  on the platform fonts.
- PDF: SHA-256 of each page content stream of the uncompressed PDF, with the charts replaced by one fixed image (the charts have
  their own snapshots) so only the layout of `houston_pdf` is under test.
"""

import hashlib
import json
import math
import os
import struct
import zlib
from collections.abc import Callable
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from matplotlib import colors as mcolors
from matplotlib.figure import Figure
from monitor.db import SessionRow
from monitor.report import charts, houston_pdf
from monitor.report.houston_pdf import render_pdf
from monitor.report.week import build_week_report
from pypdf import PdfReader

pytestmark = pytest.mark.golden

GOLDEN = Path(__file__).resolve().parent / "golden" / "houston"
EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid"
START, END = date(2027, 2, 4), date(2027, 2, 10)
NOW = datetime(2027, 2, 10, 18, 0, tzinfo=UTC)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


# 2x1 white PNG: what every chart becomes in the PDF snapshots
PIXEL = (
    bytes([0x89])
    + b"PNG"
    + bytes([13, 10, 26, 10])
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
    + _chunk(b"IDAT", zlib.compress(bytes(7)[:1] + bytes([255]) * 6))
    + _chunk(b"IEND", b"")
)


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def check(name: str, data: Any) -> None:
    path = GOLDEN / f"{name}.json"
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == text, f"{path.name} differs from the snapshot (UPDATE_GOLDEN=1 rewrites it)"


# --- scenario builders ---------------------------------------------------------------------------------------


def daily(n: int, day: str, pain: int | None, worst: int | None = None, **extra: Any) -> dict[str, Any]:
    report = example("domain/self-report.daily") | {
        "report_id": f"5e1f0e90-0000-4000-8000-0000000001{n:02d}",
        "timestamp": f"{day}T08:00:00Z",
        "pain_nrs": pain,
    }
    report.pop("pain_nrs_worst_24h")
    if worst is not None:
        report["pain_nrs_worst_24h"] = worst
    return report | extra


def odi(day: str, scores: list[int]) -> dict[str, Any]:
    items = [{"item_id": f"item_{i}", "score": s} for i, s in enumerate(scores)]
    return example("domain/self-report.weekly-odi") | {"timestamp": f"{day}T08:00:00Z", "items": items}


def run(n: int, started: str, state: str = "processed", end_reason: str | None = "completed", pain_rep: int | None = None, **extra: Any) -> SessionRow:
    payload = example("domain/session.exercise") | {"session_id": f"3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a{n:02d}", "started_at": started, "end_reason": end_reason}
    if pain_rep is not None:
        payload["events"] = [{"event_type": "pain_reported", "timestamp": started, "device_ms": 1, "boot_id": 1, "rep_index": pain_rep}]
    return SessionRow(payload | extra, state, None)


def fset(row: SessionRow, **values: float) -> dict[str, Any]:
    ids = {
        "reps": "exercise.rep_count",
        "dur": "exercise.rep_duration_s_mean",
        "elong": "stretch.full_spine.peak_elongation_mm",
        "rom": "imu.trunk.rom_deg",
        "rom2": "imu.pelvis.rom_deg",
        "emg": "emg.lat.left.rms_pct_mvc",
        "emg2": "emg.lat.right.rms_pct_mvc",
    }
    return {"session_id": row.payload["session_id"], "features": [{"feature_id": ids[k], "value": v} for k, v in values.items()]}


def ast(n: int, as_of: str, level: str, **extra: Any) -> dict[str, Any]:
    return example("domain/assessment.amber") | {"assessment_id": f"a55e55e0-0000-4000-8000-0000000001{n:02d}", "as_of": as_of, "level": level} | extra


def build(mission: Callable[[date], int | None] | None = None, **args: Any) -> dict[str, Any]:
    args = {"assessments": [], "self_reports": [], "sessions": [], "feature_sets": []} | args
    return build_week_report("AST-01", START, END, NOW, mission or (lambda d: (d - date(2026, 11, 14)).days + 1), **args)


def full() -> dict[str, Any]:
    s1, s2, old = run(1, "2027-02-05T10:00:00Z"), run(2, "2027-02-07T10:00:00Z", end_reason="aborted_pain", pain_rep=7), run(3, "2027-01-20T10:00:00Z")
    return build(
        assessments=[
            ast(1, "2027-02-05T07:00:00Z", "green"),
            ast(2, "2027-02-05T09:00:00Z", "amber"),
            ast(3, "2027-02-09T09:00:00Z", "red", escalation={"required": True, "urgency": "same_day", "reasons": []}),
            ast(4, "2027-01-15T09:00:00Z", "red"),
        ],
        self_reports=[
            daily(1, "2027-02-05", 3, 4),
            daily(2, "2027-02-06", 5, 6, red_flags=["leg_weakness"]),
            daily(3, "2027-01-30", 9, 9),
            example("domain/self-report.urgent-other") | {"timestamp": "2027-02-10T14:15:00Z", "note": "SECRET"},
            odi("2027-02-07", [2, 3, 1, 2]),
            odi("2027-01-20", [5, 5]),
        ],
        sessions=[s1, s2, old],
        feature_sets=[fset(s1, reps=12, dur=3.04, elong=8.0, rom=30.5, rom2=41.25, emg=20.0, emg2=30.0), fset(s2, reps=6, elong=9.5), fset(old, reps=99)],
    )


def rich() -> dict[str, Any]:
    """Escalation, missing data, unevaluated domain, a driver without z, a trend, a path from red down to amber."""
    drivers = [
        {"feature_id": f"f.{i}", "value": 1.0, "unit": "u", "deviation_z": z, "contribution": 0.1}
        for i, z in enumerate([2.0, -3.1, 0.4, None, 1.9, -0.2, 2.6, 1.0, -1.1, 0.0])
    ]
    rules = [
        {"rule_id": "RULE_B", "domain": "symptoms", "level": "red", "overrides_model": True, "description": "x", "evidence": {}},
        {"rule_id": "RULE_A", "domain": "spine_length", "level": "amber", "overrides_model": False, "description": "x", "evidence": {}},
        {"rule_id": "RULE_B", "domain": "symptoms", "level": "red", "overrides_model": True, "description": "x", "evidence": {}},
    ]
    last = ast(
        3,
        "2027-02-10T09:00:00Z",
        "amber",
        risk_score=0.4,
        confidence=None,
        completeness={"score": 0.5, "missing": [{"what": "emg.left", "reason": "sensor_offline"}, {"what": "imu.trunk", "reason": "not_collected"}]},
        domains=[
            {"domain": "spine_length", "level": "green", "score": 0.1234, "evaluated": True},
            {"domain": "muscle", "level": "red", "score": None, "evaluated": False},
        ],
        drivers=drivers,
        trend={"projected_level": "green"},
        fired_rules=rules,
        recommendations=[],
        escalation={"required": True, "urgency": None, "reasons": []},
    )
    return build(
        assessments=[
            ast(1, "2027-02-04T09:00:00Z", "red", risk_score=0.9, confidence=0.5),
            ast(2, "2027-02-07T09:00:00Z", "green", risk_score=None),
            last,
        ],
        self_reports=[daily(1, "2027-02-08", 7, None)],
    )


def trend_days() -> dict[str, Any]:
    first = ast(1, "2027-02-04T09:00:00Z", "green", trend={"projected_level": "red", "days_to_threshold": 3}, escalation={"required": False, "reasons": []})
    return build(
        assessments=[first, ast(2, "2027-02-05T09:00:00Z", "amber", trend={"projected_level": "amber"}, escalation={"required": False, "reasons": []})]
    )


def many() -> dict[str, Any]:
    levels = ["green", "amber", "red", "amber", "green", "red", "amber", "green", "green", "amber", "red", "red", "amber", "green", "red"]
    assessments = [
        ast(n, f"2027-02-{4 + n // 3:02d}T{8 + n % 3:02d}:30:00Z", lv, risk_score=round(0.1 * (n % 9), 2), confidence=None if n % 4 == 0 else 0.6)
        for n, lv in enumerate(levels)
    ]
    rows = [
        run(1, "2027-02-04T10:05:00Z", planned_reps=None),
        run(2, "2027-02-05T10:00:00Z", end_reason="aborted_pain", pain_rep=3),
        run(3, "2027-02-06T10:00:00Z", "processing_failed", None),
        run(4, "2027-02-07T10:00:00Z", "processed", "aborted_by_user"),
        run(5, "2027-02-08T10:00:00Z", "ended", "aborted_other"),
        run(6, "2027-02-09T10:00:00Z", "weird_state", "unknown_reason"),
    ]
    sets = [fset(rows[0], reps=0, elong=-2.0, rom=10.0), fset(rows[1], reps=5.5, elong=4.0), fset(rows[3], reps=10, elong=4.0, rom=-5.0)]
    reports = [
        daily(1, "2027-02-04", 0, 0, stiffness_nrs=2),
        daily(2, "2027-02-06", 10, 10, red_flags=["numbness_or_tingling", "leg_weakness"], other_symptoms=["other", "very_severe_pain", "unlisted_one"]),
        daily(3, "2027-02-06", 4, 5),
    ]
    return build(assessments=assessments, self_reports=reports, sessions=rows, feature_sets=sets, mission=lambda d: None)


def flat_sessions() -> dict[str, Any]:
    """Sessions whose measures coincide, so the chart axes fall back on their defaults."""
    rows = [run(1, "2027-02-05T10:00:00Z", planned_reps=0), run(2, "2027-02-06T10:00:00Z")]
    return build(sessions=rows, feature_sets=[fset(rows[0], reps=4, elong=7.0, rom=7.0), fset(rows[1], reps=4, elong=7.0, rom=0.0)])


def odi_only() -> dict[str, Any]:
    return build(self_reports=[odi("2027-02-06", [0, 1, 2, 3, 4, 5, 1, 1, 1, 1])])


SCENARIOS: dict[str, Callable[[], dict[str, Any]]] = {
    "full": full,
    "empty": build,
    "rich": rich,
    "trend_days": trend_days,
    "many": many,
    "flat_sessions": flat_sessions,
    "odi_only": odi_only,
}


# --- week data -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCENARIOS)
def test_week_data_matches_the_snapshot(name):
    check(f"week.{name}", SCENARIOS[name]())


def test_week_without_a_launch_date_has_no_mission_days():
    report = many()

    assert report["mission_day"] == {"start": None, "end": None}
    assert {d["mission_day"] for d in report["days"]} == {None}


# --- charts --------------------------------------------------------------------------------------------------


def hexc(color: Any) -> Any:
    try:
        return mcolors.to_hex(color, keep_alpha=True)
    except ValueError, TypeError:
        return str(color)


def num(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return num(value.tolist())
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 6)
    if isinstance(value, list | tuple):
        return [num(v) for v in value]
    return value


def fonts(text: Any) -> dict[str, Any]:
    return {"text": text.get_text(), "size": num(text.get_fontsize()), "weight": text.get_fontweight(), "color": hexc(text.get_color())}


def handle(h: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": type(h).__name__}
    for key in ("facecolor", "edgecolor", "color", "markerfacecolor"):
        if hasattr(h, f"get_{key}"):
            out[key] = hexc(getattr(h, f"get_{key}")())
    for key in ("hatch", "marker", "linestyle", "markersize"):
        if hasattr(h, f"get_{key}"):
            out[key] = num(getattr(h, f"get_{key}")())
    return out


def dump_axes(ax: Any) -> dict[str, Any]:
    spec = ax.get_subplotspec()
    out: dict[str, Any] = {
        "grid_ratios": num(spec.get_gridspec().get_height_ratios()),
        "slot": [spec.rowspan.start, spec.rowspan.stop, spec.colspan.start, spec.colspan.stop],
        "title": fonts(ax._left_title) | {"loc_visible": ax._left_title.get_visible()},
        "center_title": ax.get_title(),
        "xlabel": fonts(ax.xaxis.label),
        "ylabel": fonts(ax.yaxis.label),
        "xlim": num(ax.get_xlim()),
        "ylim": num(ax.get_ylim()),
        "spines": {k: [s.get_visible(), hexc(s.get_edgecolor())] for k, s in ax.spines.items()},
        "shared_x": [ax.get_shared_x_axes().joined(ax, o) for o in ax.figure.axes if o is not ax],
    }
    for axis_name, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
        ticks = axis.get_major_ticks()
        out[f"{axis_name}ticks"] = num(list(axis.get_majorticklocs()))
        out[f"{axis_name}ticklabels"] = [fonts(t) for t in axis.get_ticklabels()]
        out[f"{axis_name}tick_look"] = (
            None
            if not ticks
            else {
                "label_on": ticks[0].label1.get_visible(),
                "grid_on": ticks[0].gridline.get_visible(),
                "grid_color": hexc(ticks[0].gridline.get_color()),
                "grid_width": num(ticks[0].gridline.get_linewidth()),
                "tick_color": hexc(ticks[0].tick1line.get_color()),
            }
        )
    out["lines"] = [
        {
            "x": num(list(line.get_xdata())),
            "y": num(list(line.get_ydata())),
            "color": hexc(line.get_color()),
            "width": num(line.get_linewidth()),
            "style": line.get_linestyle(),
            "marker": line.get_marker(),
            "size": num(line.get_markersize()),
            "label": line.get_label(),
            "zorder": num(line.get_zorder()),
        }
        for line in ax.lines
    ]
    out["bars"] = [
        {
            "box": num([p.get_x(), p.get_y(), p.get_width(), p.get_height()]),
            "face": hexc(p.get_facecolor()),
            "edge": hexc(p.get_edgecolor()),
            "hatch": p.get_hatch(),
            "zorder": num(p.get_zorder()),
        }
        for p in ax.patches
    ]
    out["texts"] = [
        fonts(t)
        | {
            "pos": num(t.get_position()),
            "ha": t.get_ha(),
            "va": t.get_va(),
            "box": None if t.get_bbox_patch() is None else [t.get_bbox_patch().get_boxstyle().__class__.__name__, hexc(t.get_bbox_patch().get_facecolor())],
        }
        for t in ax.texts
    ]
    out["points"] = [
        {
            "xy": num(c.get_offsets()),
            "sizes": num(c.get_sizes()),
            "face": [hexc(f) for f in c.get_facecolor()],
            "edge": [hexc(f) for f in c.get_edgecolor()],
            "width": num(c.get_linewidths()),
            "zorder": num(c.get_zorder()),
            "marker": num(c.get_paths()[0].vertices) if c.get_paths() else None,
            "label": c.get_label(),
        }
        for c in ax.collections
    ]
    legend = ax.get_legend()
    out["legend"] = (
        None
        if legend is None
        else {
            "texts": [fonts(t) for t in legend.get_texts()],
            "handles": [handle(h) for h in legend.legend_handles],
            "frame": legend.get_frame_on(),
            "loc": legend._loc,
            "cols": legend._ncols,
            "anchor": None if legend._bbox_to_anchor is None else num(legend._bbox_to_anchor.bounds),
        }
    )
    return out


def dump_figure(fig: Figure) -> dict[str, Any]:
    return {
        "size": num(list(fig.get_size_inches())),
        "dpi": num(fig.dpi),
        "face": hexc(fig.get_facecolor()),
        "layout": type(fig.get_layout_engine()).__name__,
        "axes": [dump_axes(ax) for ax in fig.axes],
    }


@pytest.fixture
def figures(monkeypatch):
    """What each chart function drew, instead of its PNG: `figures[chart] -> dump | None`."""
    drawn: list[dict[str, Any]] = []

    def keep(fig: Figure) -> bytes:
        drawn.append(dump_figure(fig))
        return b"png"

    monkeypatch.setattr(charts, "_png", keep)

    def draw(chart: str, week: dict[str, Any]) -> dict[str, Any] | None:
        drawn.clear()
        png = charts.CHARTS[chart](week)
        assert (png is None) == (not drawn)
        return drawn[0] if drawn else None

    return draw


@pytest.mark.parametrize("chart", charts.CHARTS)
@pytest.mark.parametrize("name", SCENARIOS)
def test_chart_structure_matches_the_snapshot(figures, chart, name):
    check(f"chart.{chart}.{name}", figures(chart, SCENARIOS[name]()))


def test_charts_draw_nothing_for_an_empty_week(figures):
    week = build()

    assert [figures(chart, week) for chart in charts.CHARTS] == [None] * len(charts.CHARTS)


def test_pain_chart_is_drawn_for_assessments_alone_and_for_pain_alone(figures):
    only_levels = build(assessments=[ast(1, "2027-02-05T09:00:00Z", "red")])
    only_pain = build(self_reports=[daily(1, "2027-02-05", 4, 5)])

    assert figures("pain", only_levels) is not None
    assert figures("pain", only_pain) is not None
    assert figures("risk", only_pain) is None


def test_the_real_chart_is_a_png_with_the_configured_resolution():
    png = charts.pain_and_level(full())

    assert png is not None and png.startswith(b"\x89PNG")
    width, height = (int.from_bytes(png[i : i + 4], "big") for i in (16, 20))
    assert (width, height) == (round(7.2 * charts.DPI), round(4.1 * charts.DPI))


# --- pdf -----------------------------------------------------------------------------------------------------


@pytest.fixture
def pixel_charts(monkeypatch):
    """Every chart that has something to draw becomes the same 1x1 image."""
    for name, real in list(charts.CHARTS.items()):
        monkeypatch.setitem(charts.CHARTS, name, lambda week, real=real: None if real(week) is None else PIXEL)
    monkeypatch.setattr(houston_pdf.charts, "pain_and_level", charts.CHARTS["pain"])
    monkeypatch.setattr(houston_pdf.charts, "sessions", charts.CHARTS["sessions"])
    monkeypatch.setattr(houston_pdf.charts, "odi", charts.CHARTS["odi"])


def _stream(page: Any) -> bytes:
    contents = page.get_contents()
    assert contents is not None
    return contents.get_data()


def pages_of(pdf: bytes) -> dict[str, Any]:
    reader = PdfReader(BytesIO(pdf))
    info = reader.metadata
    return {
        "title": info.title if info else None,
        "author": info.author if info else None,
        "subject": info.subject if info else None,
        "pages": [{"box": num([float(v) for v in page.mediabox]), "sha256": hashlib.sha256(_stream(page)).hexdigest()} for page in reader.pages],
    }


@pytest.mark.parametrize("name", SCENARIOS)
def test_pdf_pages_match_the_snapshot(pixel_charts, name):
    check(f"pdf.{name}", pages_of(render_pdf(SCENARIOS[name](), compress=False)))


def test_pdf_is_compressed_by_default_and_readable_when_not():
    week = full()

    assert b"Weekly Spine Health Report" in render_pdf(week, compress=False)
    assert b"Weekly Spine Health Report" not in render_pdf(week)
    assert b"Weekly Spine Health Report" not in render_pdf(week, compress=True)


def text_of(pdf: bytes) -> str:
    return " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(pdf)).pages)


def test_pdf_text_says_what_the_week_holds(pixel_charts):
    text = text_of(render_pdf(full()))

    for expected in (
        "Weekly Spine Health Report",
        "Astronaut AST-01",
        "2027-02-04 to 2027-02-10",
        "Mission day D83 to D89",
        "Generated 2027-02-10 18:00 UTC",
        "The latest engine assessment (2027-02-09 09:00 UTC) is RED, with 74% confidence.",
        "It was GREEN at the first assessment of the week.",
        "Escalation is required (urgency: same_day). Rules: RULE_SYMPTOM_PAIN_TREND.",
        "Pain was reported on 2 of 7 days: mean 4.0/10, highest 5/10, worst in 24 h up to 6/10.",
        "2 session(s): 1 completed, 1 stopped for pain.",
        "Oswestry questionnaire (2027-02-07, informative only): 40% of the maximum limitation over 4 items.",
        "Stopped for pain (rep 7)",
        "Very severe pain",
        "Leg weakness",
        "Provisional content, pending clinical validation.",
        "Page 1",
    ):
        assert expected in text, expected


def test_pdf_never_turns_a_week_without_data_into_green(pixel_charts):
    text = text_of(render_pdf(build()))

    assert "NO DATA" in text
    assert "No data was recorded for this astronaut in the period" in text
    assert "This is not a green status: the state of the astronaut is unknown." in text
    assert "GREEN" not in text
