"""Charts of the Houston report, drawn with matplotlib (Agg, no pyplot: no global state, safe off the event loop).

Each function takes the week data (`monitor.report.week`) and returns PNG bytes, or None when the week has nothing to
draw (the PDF then says so in words). Colours are colour-blind friendly and nothing relies on colour alone: levels are
also written in the cells and outcomes differ by hatch and marker.
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")

from matplotlib import rc_context  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from monitor.report.labels import LEVEL_LABELS, humanize  # noqa: E402

INK = "#1F2933"
MUTED = "#5F6B7A"
GRID = "#D9DEE5"
BLUE = "#1F5FBF"
ORANGE = "#C2410C"
TEAL = "#0F766E"
LEVEL_COLORS = {"green": "#2F855A", "amber": "#D69E2E", "red": "#C53030", "nodata": "#A0AEC0"}
LEVEL_TEXT = {"green": "white", "amber": INK, "red": "white", "nodata": INK}
DPI = 200
STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 8,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "axes.titlecolor": INK,
    "axes.titlesize": 9.5,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "xtick.color": INK,
    "ytick.color": INK,
    "legend.frameon": False,
    "legend.fontsize": 7.5,
}
Week = Mapping[str, Any]


@contextmanager
def _figure(width: float, height: float, **kwargs: Any):
    with rc_context(rc=STYLE):  # pyright: ignore[reportArgumentType] - rc keys are plain strings
        yield Figure(figsize=(width, height), dpi=DPI, layout="constrained", facecolor="white", **kwargs)


def _png(fig: Figure) -> bytes:
    FigureCanvasAgg(fig)
    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


def _day_label(day: Mapping[str, Any]) -> str:
    when = date.fromisoformat(day["date"])
    mission = day["mission_day"]
    return f"{when:%a %d}" + ("" if mission is None else f"\nD{mission}")


def _values(days: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    return [float("nan") if d[key] is None else float(d[key]) for d in days]


def pain_and_level(week: Week) -> bytes | None:
    """Daily pain (now and worst of 24 h) over the risk level of each day and the days with urgent symptoms."""
    days = week["days"]
    if all(d["pain_nrs"] is None for d in days) and all(d["level"] == "nodata" for d in days):
        return None
    xs = list(range(len(days)))
    with _figure(7.2, 4.1) as fig:
        pain, band = fig.subplots(2, 1, sharex=True, height_ratios=[5, 1.5])
        pain.set_title("Pain reported by the astronaut (NRS 0-10)")
        pain.set_ylim(-0.3, 10.6)
        pain.set_yticks(range(0, 11, 2))
        pain.set_ylabel("Numeric rating scale")
        pain.plot(xs, _values(days, "pain_nrs"), color=BLUE, marker="o", markersize=5, linewidth=2, label="Pain now")
        pain.plot(xs, _values(days, "pain_nrs_worst_24h"), color=ORANGE, marker="s", markersize=4.5, linewidth=1.5, linestyle="--", label="Worst in last 24 h")
        flagged = [x for x, d in zip(xs, days, strict=True) if d["red_flags"] or d["other_symptoms"]]
        if flagged:
            pain.scatter(flagged, [10.2] * len(flagged), marker="v", s=46, color=LEVEL_COLORS["red"], zorder=5, label="Red flag / urgent symptom reported")
        pain.legend(loc="upper left", ncols=3, bbox_to_anchor=(0, -0.02), fontsize=7)
        pain.tick_params(labelbottom=False)

        band.set_title("Risk level of the engine (last assessment of each day)", fontsize=8.5)
        band.grid(False)
        for x, day in zip(xs, days, strict=True):
            level = day["level"]
            band.barh(0, 0.94, left=x - 0.47, height=1, color=LEVEL_COLORS[level], edgecolor="white", hatch="///" if level == "nodata" else None)
            band.text(x, 0, LEVEL_LABELS[level].upper(), ha="center", va="center", fontsize=6.5, fontweight="bold", color=LEVEL_TEXT[level])
        band.set_ylim(-0.5, 0.5)
        band.set_yticks([])
        band.spines["left"].set_visible(False)
        band.set_xticks(xs, [_day_label(d) for d in days])
        band.set_xlim(-0.6, len(days) - 0.4)
        return _png(fig)


def risk_trend(week: Week) -> bytes | None:
    """Risk score and confidence of every assessment of the week, each point coloured (and labelled) by its level."""
    points = [a for a in week["assessments"] if a["risk_score"] is not None]
    if not points:
        return None
    xs = range(len(points))
    with _figure(7.2, 2.6) as fig:
        ax = fig.subplots()
        ax.set_title("Engine risk score and confidence per assessment")
        ax.plot(xs, [a["risk_score"] for a in points], color=INK, linewidth=1.2, zorder=2)
        ax.scatter(xs, [a["risk_score"] for a in points], c=[LEVEL_COLORS[a["level"]] for a in points], s=60, edgecolor=INK, linewidth=0.6, zorder=3)
        conf = [(x, a["confidence"]) for x, a in zip(xs, points, strict=True) if a["confidence"] is not None]
        if conf:
            ax.plot([c[0] for c in conf], [c[1] for c in conf], color=TEAL, linestyle="--", marker="D", markersize=3.5, linewidth=1.2)
        ax.set_ylim(-0.05, 1.05)
        ax.set_ylabel("0 - 1")
        step = max(1, len(points) // 7)
        ax.set_xticks(list(xs)[::step], [a["as_of"][5:16].replace("T", "\n") for a in points][::step], fontsize=7)
        handles = [Line2D([], [], color=INK, marker="o", markerfacecolor="white", label="Risk score (point colour = level)")]
        if conf:
            handles.append(Line2D([], [], color=TEAL, linestyle="--", marker="D", markersize=3.5, label="Confidence"))
        handles += [Patch(facecolor=LEVEL_COLORS[k], label=LEVEL_LABELS[k]) for k in ("green", "amber", "red")]
        ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncols=5, fontsize=7)
        return _png(fig)


def drivers(week: Week) -> bytes | None:
    """Deviation (z) of the top drivers of the latest assessment against the astronaut's own baseline."""
    latest = week["latest_assessment"]
    rows = [d for d in (latest["drivers"] if latest else []) if d["deviation_z"] is not None]
    if not rows:
        return None
    rows = sorted(rows, key=lambda d: abs(d["deviation_z"]), reverse=True)[:8][::-1]
    with _figure(7.2, 0.9 + 0.42 * len(rows)) as fig:
        ax = fig.subplots()
        ax.set_title("Main drivers: deviation from own baseline (z-score)")
        zs = [d["deviation_z"] for d in rows]
        colors = [ORANGE if abs(z) >= 2 else BLUE for z in zs]
        ax.barh(range(len(rows)), zs, color=colors, height=0.62)
        ax.axvline(0, color=INK, linewidth=0.8)
        for bound in (-2, 2):
            ax.axvline(bound, color=MUTED, linewidth=0.8, linestyle=":")
        ax.set_yticks(range(len(rows)), [d["feature_id"] for d in rows], fontsize=6.8)
        span = max(2.6, max(abs(z) for z in zs) * 1.25)
        ax.set_xlim(-span, span)
        for y, z in enumerate(zs):
            ax.text(z + (0.06 if z >= 0 else -0.06), y, f"{z:+.1f}", va="center", ha="left" if z >= 0 else "right", fontsize=7.5, fontweight="bold")
        ax.set_xlabel("z (dotted lines at +/-2)")
        ax.grid(axis="y", visible=False)
        ax.legend(handles=[Patch(color=ORANGE, label="|z| >= 2"), Patch(color=BLUE, label="|z| < 2")], loc="lower right", fontsize=7)
        return _png(fig)


def sessions(week: Week) -> bytes | None:
    """Repetitions of each session (outcome by colour and hatch), and its lumbar elongation and range of motion."""
    rows = week["sessions"]
    if not rows:
        return None
    xs = list(range(len(rows)))
    labels = [f"{date.fromisoformat(s['date']):%a %d}\n{s['started_at'][11:16]}Z" for s in rows]
    with _figure(7.2, 4.4) as fig:
        reps, measures = fig.subplots(2, 1, sharex=True, height_ratios=[1, 1])
        reps.set_title("Repetitions per session")
        for x, s in zip(xs, rows, strict=True):
            if s["reps"] is None:
                reps.text(x, 0.5, "no\nmeasure", ha="center", va="bottom", fontsize=6.5, color=MUTED)
                continue
            pain = s["pain_stop"]
            reps.bar(x, s["reps"], width=0.55, color=LEVEL_COLORS["red"] if pain else TEAL, hatch="///" if pain else None, edgecolor="white")
            reps.text(
                x,
                s["reps"] / 2,
                f"{s['reps']:g}",
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
                color="white",
                bbox={"boxstyle": "round,pad=0.25", "fc": INK, "ec": "none"},
            )
        planned = [(x, s["planned_reps"]) for x, s in zip(xs, rows, strict=True) if s["planned_reps"]]
        reps.scatter([p[0] for p in planned], [p[1] for p in planned], marker="_", s=420, color=INK, linewidth=1.6, zorder=4)
        top = max([s["reps"] or 0 for s in rows] + [s["planned_reps"] or 0 for s in rows])
        reps.set_ylim(0, max(top, 1) * 1.3)
        reps.set_ylabel("Repetitions")
        reps.legend(
            handles=[
                Patch(facecolor=TEAL, label="Not stopped for pain"),
                Patch(facecolor=LEVEL_COLORS["red"], hatch="///", edgecolor="white", label="Stopped for pain"),
                Line2D([], [], color=INK, marker="_", markersize=10, linestyle="", label="Planned"),
            ],
            loc="upper right",
            ncols=3,
        )
        reps.tick_params(labelbottom=False)

        measures.set_title("Lumbar elongation and range of motion")
        _measure_line(measures, xs, [s["elongation_mm"] for s in rows], BLUE, "o", "Peak elongation (mm)")
        measures.set_ylabel("mm", color=BLUE)
        right = measures.twinx()
        right.spines["right"].set_visible(True)
        right.grid(False)
        _measure_line(right, xs, [s["rom_deg"] for s in rows], ORANGE, "s", "IMU range of motion (deg)")
        right.set_ylabel("deg", color=ORANGE)
        handles = [
            Line2D([], [], color=BLUE, marker="o", label="Peak elongation (mm)"),
            Line2D([], [], color=ORANGE, marker="s", label="IMU range of motion (deg)"),
        ]
        measures.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.3), ncols=2)
        measures.set_xticks(xs, labels, fontsize=7)
        measures.set_xlim(-0.6, len(rows) - 0.4)
        return _png(fig)


def _measure_line(ax: Axes, xs: Sequence[int], values: Sequence[float | None], color: str, marker: str, label: str) -> None:
    ax.plot(xs, [float("nan") if v is None else v for v in values], color=color, marker=marker, markersize=5, linewidth=1.6, label=label)
    present = [v for v in values if v is not None]
    if present:
        pad = (max(present) - min(present)) * 0.25 or max(abs(present[0]) * 0.1, 1.0)
        ax.set_ylim(min(present) - pad, max(present) + pad)
    else:
        ax.set_yticks([])


def odi(week: Week) -> bytes | None:
    """Items of the week's Oswestry (0 = no limitation .. 5 = maximum), informative only."""
    data = week["odi"]
    if data is None:
        return None
    items = data["items"][::-1]
    with _figure(7.2, 0.95 + 0.36 * len(items)) as fig:
        ax = fig.subplots()
        ax.set_title(f"Oswestry items, {data['date']} (informative)")
        scores = [i["score"] for i in items]
        ax.barh(range(len(items)), scores, color=[TEAL if s <= 1 else BLUE if s <= 3 else ORANGE for s in scores], height=0.6)
        ax.set_yticks(range(len(items)), [humanize(i["item_id"]) for i in items])
        ax.set_xlim(0, 5.6)
        ax.set_xticks(range(6))
        ax.set_xlabel("Score: 0 no limitation ... 5 maximum limitation")
        ax.grid(axis="y", visible=False)
        for y, s in enumerate(scores):
            ax.text(s + 0.08, y, str(s), va="center", fontsize=8, fontweight="bold")
        return _png(fig)


CHARTS: dict[str, Callable[[Week], bytes | None]] = {"pain": pain_and_level, "risk": risk_trend, "drivers": drivers, "sessions": sessions, "odi": odi}
