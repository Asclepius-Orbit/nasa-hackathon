"""PDF of the weekly Houston report: renders the week data (`monitor.report.week`), nothing else.

The render reads only the dict it is given, so what the PDF can say is exactly what `build_week_report` let in: codes,
numbers and instants, never free text. Wording about levels is conservative: no assessment is `No data`, never green.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, Image, KeepTogether, NextPageTemplate, PageTemplate, Paragraph, Spacer, Table, TableStyle

from monitor.report import charts
from monitor.report.labels import END_REASON_LABELS, LEVEL_LABELS, OTHER_SYMPTOM_LABELS, SESSION_STATE_LABELS, humanize

Week = Mapping[str, Any]

NAVY = colors.HexColor("#14213D")
INK = colors.HexColor(charts.INK)
MUTED = colors.HexColor(charts.MUTED)
RULE = colors.HexColor("#D9DEE5")
PANEL = colors.HexColor("#F3F5F8")
LEVEL_FILL = {k: colors.HexColor(v) for k, v in charts.LEVEL_COLORS.items()}
LEVEL_INK = {k: colors.HexColor(v) if v != "white" else colors.white for k, v in charts.LEVEL_TEXT.items()}
LEVEL_RANK = {"green": 0, "amber": 1, "red": 2}
MARGIN = 1.8 * cm
WIDTH = A4[0] - 2 * MARGIN

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.2, leading=13, textColor=INK, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.8, leading=10.5, textColor=MUTED)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8, leading=10)
CELL_HEAD = ParagraphStyle("cellhead", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
CODE = ParagraphStyle("code", parent=CELL, fontName="Courier", fontSize=7.6)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=NAVY)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=11, bulletIndent=0, spaceAfter=3)
KPI_VALUE = ParagraphStyle("kpiv", parent=BODY, fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=NAVY)
KPI_LABEL = ParagraphStyle("kpil", parent=SMALL, fontSize=7.4, leading=9.5)
NOTE = ParagraphStyle("note", parent=SMALL, backColor=PANEL, borderPadding=5, leading=11)


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(text), style)


def _markup(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    """Paragraph with reportlab markup: the caller escapes every dynamic piece."""
    return Paragraph(text, style)


def _code(text: str) -> Paragraph:
    return Paragraph(escape(text), CODE)


def _num(value: float | None, fmt: str = "{:g}", unit: str = "") -> str:
    return "-" if value is None else fmt.format(value) + unit


def _confidence(assessment: Mapping[str, Any]) -> str:
    return _num(None if assessment["confidence"] is None else assessment["confidence"] * 100, "{:.0f}", "%")


def _day(iso: str) -> str:
    return f"{date.fromisoformat(iso):%a %d %b}"


def _stamp(iso: str) -> str:
    return f"{datetime.fromisoformat(iso):%Y-%m-%d %H:%M} UTC"


def _mission(day: int | None) -> str:
    return "-" if day is None else f"D{day}"


def _heading(text: str) -> Flowable:
    table = Table([[Paragraph(escape(text), H1)]], colWidths=[WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.8, NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table.spaceBefore, table.spaceAfter = 16, 7
    return table


def _grid(rows: Sequence[Sequence[Any]], widths: Sequence[float], extra: Sequence[tuple[Any, ...]] = ()) -> Table:
    """Table with a navy header row, zebra stripes and thin rules. Header cells may be plain strings."""
    head = [Paragraph(escape(c), CELL_HEAD) if isinstance(c, str) else c for c in rows[0]]
    body = [[Paragraph(escape(c), CELL) if isinstance(c, str) else c for c in row] for row in rows[1:]]
    table = Table([head, *body], colWidths=widths, repeatRows=1)
    style: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        *[("BACKGROUND", (0, i), (-1, i), PANEL) for i in range(2, len(rows), 2)],
        *extra,
    ]
    table.setStyle(TableStyle(style))
    return table


def _chip(level: str, width: float = 2.6 * cm) -> Table:
    chip = Table(
        [
            [
                Paragraph(
                    f"<b>{escape(LEVEL_LABELS[level].upper())}</b>", ParagraphStyle("chip", parent=BODY, alignment=1, textColor=LEVEL_INK[level], fontSize=8)
                )
            ]
        ],
        colWidths=[width],
    )
    chip.setStyle(
        TableStyle([("BACKGROUND", (0, 0), (-1, -1), LEVEL_FILL[level]), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])
    )
    return chip


def _image(png: bytes, width: float = WIDTH) -> Image:
    pixels_w, pixels_h = ImageReader(BytesIO(png)).getSize()
    return Image(BytesIO(png), width=width, height=width * pixels_h / pixels_w)


def _nothing(text: str) -> Paragraph:
    return Paragraph(f"<b>No data.</b> {escape(text)}", NOTE)


# --- summary text ----------------------------------------------------------------------------------------------


def summary_lines(week: Week) -> list[str]:
    """The synthesis of the week in plain sentences, built from the data only (no model involved)."""
    if not week["has_data"]:
        return ["No data was recorded for this astronaut in the period: no daily reports, sessions or engine assessments."]
    lines: list[str] = []
    latest = week["latest_assessment"]
    if latest is None:
        lines.append("There is no engine assessment in the period, so the risk level is not known (No data).")
    else:
        line = f"The latest engine assessment ({_stamp(latest['as_of'])}) is {LEVEL_LABELS[latest['level']].upper()}"
        line += "." if latest["confidence"] is None else f", with {latest['confidence'] * 100:.0f}% confidence."
        levels = [a["level"] for a in week["assessments"]]
        if len(levels) > 1 and levels[0] != levels[-1]:
            line += f" It was {LEVEL_LABELS[levels[0]].upper()} at the first assessment of the week."
        worst = max((lv for lv in levels if lv in LEVEL_RANK), key=LEVEL_RANK.__getitem__, default=None)
        if worst is not None and worst != latest["level"] and LEVEL_RANK[worst] > LEVEL_RANK.get(latest["level"], -1):
            line += f" The highest level reached was {LEVEL_LABELS[worst].upper()}."
        lines.append(line)
        escalation = latest["escalation"]
        if escalation["required"]:
            urgency = "" if escalation["urgency"] is None else f" (urgency: {escalation['urgency']})"
            lines.append(f"Escalation is required{urgency}. Rules: {', '.join(escalation['reasons']) or 'not specified'}.")
    pain = week["pain"]
    if pain["daily_reports"]:
        lines.append(
            f"Pain was reported on {pain['daily_reports']} of {len(week['days'])} days: mean {pain['mean']:.1f}/10, highest {pain['max']}/10"
            + ("" if pain["max_worst_24h"] is None else f", worst in 24 h up to {pain['max_worst_24h']}/10")
            + "."
        )
    else:
        lines.append("No daily report was submitted in the period: pain and symptoms are unknown.")
    symptoms = week["symptoms"]
    if symptoms["red_flags"] or symptoms["other_symptoms"]:
        names = [humanize(f).lower() for f in symptoms["red_flags"]] + [OTHER_SYMPTOM_LABELS.get(s, humanize(s)).lower() for s in symptoms["other_symptoms"]]
        lines.append("Red flags / urgent symptoms reported: " + ", ".join(names) + ".")
    elif pain["daily_reports"]:
        lines.append("No red flags or urgent symptoms were reported.")
    sessions = week["sessions"]
    if sessions:
        stopped = sum(s["pain_stop"] for s in sessions)
        done = sum(s["end_reason"] == "completed" for s in sessions)
        lines.append(f"{len(sessions)} session(s): {done} completed, {stopped} stopped for pain.")
    else:
        lines.append("No exercise session was recorded in the period.")
    odi = week["odi"]
    if odi is not None:
        lines.append(
            f"Oswestry questionnaire ({odi['date']}, informative only): {odi['percent']:.0f}% of the maximum limitation over {len(odi['items'])} items."
        )
    return lines


# --- sections --------------------------------------------------------------------------------------------------


def _status_block(week: Week) -> list[Flowable]:
    latest = week["latest_assessment"]
    level = "nodata" if latest is None else latest["level"]
    if latest is None:
        detail = "No engine assessment in the period."
    else:
        completeness = latest["completeness"]
        detail = f"Assessment of {_stamp(latest['as_of'])}. Confidence {_confidence(latest)}, data completeness {completeness['score'] * 100:.0f}%."
        escalation = latest["escalation"]
        detail += " Escalation required." if escalation["required"] else " No escalation required."
    big = Paragraph(
        f"<b>{escape(LEVEL_LABELS[level].upper())}</b>", ParagraphStyle("big", parent=BODY, fontSize=19, leading=22, alignment=1, textColor=LEVEL_INK[level])
    )
    table = Table(
        [[big, [Paragraph("<b>Current risk level</b> (latest engine assessment of the week)", BODY), Spacer(1, 2), _p(detail, SMALL)]]],
        colWidths=[3.6 * cm, WIDTH - 3.6 * cm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), LEVEL_FILL[level]),
                ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (1, 0), (1, 0), 12),
            ]
        )
    )
    return [table]


def _kpis(week: Week) -> Table:
    pain, sessions = week["pain"], week["sessions"]
    flags = len(week["symptoms"]["red_flags"]) + len(week["symptoms"]["other_symptoms"])
    cards = [
        (f"{pain['daily_reports']}/{len(week['days'])}", "daily reports submitted"),
        (_num(pain["mean"], "{:.1f}", "/10"), "mean pain (days with report)"),
        (str(len(sessions)), f"sessions ({sum(s['pain_stop'] for s in sessions)} stopped for pain)"),
        (str(flags), "red flags / urgent symptoms"),
    ]
    width = WIDTH / len(cards)
    table = Table([[[Paragraph(value, KPI_VALUE), Paragraph(escape(label), KPI_LABEL)] for value, label in cards]], colWidths=[width] * len(cards))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("LINEBEFORE", (1, 0), (-1, -1), 2, colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def _daily_table(week: Week) -> Table:
    def symptoms(day: Mapping[str, Any]) -> str:
        names = [humanize(f) for f in day["red_flags"]] + [OTHER_SYMPTOM_LABELS.get(s, humanize(s)) for s in day["other_symptoms"]]
        return ", ".join(names) or ("none reported" if day["reports"] else "-")

    rows: list[list[Any]] = [["Day", "Mission", "Level", "Pain now", "Worst 24h", "Stiffness", "Sleep (h)", "Sess.", "Red flags / urgent symptoms"]]
    extra: list[tuple[Any, ...]] = []
    for i, day in enumerate(week["days"], start=1):
        level = day["level"]
        label = Paragraph(f"<b>{escape(LEVEL_LABELS[level])}</b>", ParagraphStyle("lv", parent=CELL, textColor=LEVEL_INK[level]))
        rows.append(
            [
                _day(day["date"]),
                _mission(day["mission_day"]),
                label,
                _num(day["pain_nrs"]),
                _num(day["pain_nrs_worst_24h"]),
                _num(day["stiffness_nrs"]),
                _num(day["sleep_hours"]),
                str(day["sessions"]),
                symptoms(day),
            ]
        )
        extra.append(("BACKGROUND", (2, i), (2, i), LEVEL_FILL[level]))
    return _grid(rows, [2.0 * cm, 1.7 * cm, 1.7 * cm, 1.4 * cm, 1.8 * cm, 1.7 * cm, 1.5 * cm, 1.2 * cm, WIDTH - 13.0 * cm], extra)


def _engine_section(week: Week) -> list[Flowable]:
    latest = week["latest_assessment"]
    out: list[Flowable] = [_heading("Engine assessment")]
    if latest is None:
        return [*out, _nothing("The engine produced no assessment in this period, so there are no drivers, recommendations or rules to show.")]
    domains: list[list[Any]] = [["Domain", "Level", "Score"]]
    extra: list[tuple[Any, ...]] = []
    for i, d in enumerate(latest["domains"], start=1):
        level = d["level"] if d["evaluated"] else "nodata"
        domains.append(
            [
                humanize(d["domain"]),
                Paragraph(f"<b>{escape(LEVEL_LABELS[level])}</b>", ParagraphStyle("dl", parent=CELL, textColor=LEVEL_INK[level])),
                _num(d["score"], "{:.2f}") if d["evaluated"] else "not evaluated",
            ]
        )
        extra.append(("BACKGROUND", (1, i), (1, i), LEVEL_FILL[level]))
    trend = latest["trend"]
    facts = [
        f"Risk score {_num(latest['risk_score'], '{:.2f}')} (0-1); confidence {_confidence(latest)}.",
        f"Data completeness {latest['completeness']['score'] * 100:.0f}%."
        + (
            " Missing: " + "; ".join(f"{m['what']} ({humanize(m['reason']).lower()})" for m in latest["completeness"]["missing"]) + "."
            if latest["completeness"]["missing"]
            else ""
        ),
    ]
    if trend and trend.get("projected_level"):
        days = trend.get("days_to_threshold")
        facts.append(
            f"Projected level: {LEVEL_LABELS.get(trend['projected_level'], trend['projected_level'])}"
            + ("" if days is None else f" in about {days} days")
            + "."
        )
    intro = [out.pop(), _markup("<br/>".join(escape(f) for f in facts), BODY), Spacer(1, 6), _grid(domains, [4 * cm, 3 * cm, 3.5 * cm], extra)]
    out.append(KeepTogether(intro))
    for key in ("risk", "drivers"):
        png = charts.CHARTS[key](week)
        if png is not None:
            out += [Spacer(1, 8), _image(png)]
    out.append(Spacer(1, 6))
    recs = latest["recommendations"]
    out.append(
        KeepTogether(
            [
                Paragraph("<b>Recommendations (codes)</b>", BODY),
                Spacer(1, 3),
                _markup(", ".join(f"<font face='Courier' size=8>{escape(r)}</font>" for r in recs) if recs else "None issued.", BODY),
            ]
        )
    )
    if latest["fired_rules"]:
        rows = [["Rule", "Domain", "Level", "Overrides model"]] + [
            [_code(r["rule_id"]), humanize(r["domain"]), LEVEL_LABELS.get(r["level"], r["level"]), "yes" if r["overrides_model"] else "no"]
            for r in latest["fired_rules"]
        ]
        out += [Spacer(1, 8), Paragraph("<b>Rules fired</b>", BODY), Spacer(1, 3), _grid(rows, [6.5 * cm, 3.5 * cm, 3 * cm, WIDTH - 13 * cm])]
    esc = latest["escalation"]
    out += [
        Spacer(1, 8),
        _markup(
            "<b>Escalation:</b> "
            + (
                "required"
                + ("" if esc["urgency"] is None else f", urgency {escape(str(esc['urgency']))}")
                + (". Reasons: " + ", ".join(f"<font face='Courier' size=8>{escape(r)}</font>" for r in esc["reasons"]) if esc["reasons"] else "")
                if esc["required"]
                else "not required"
            ),
            BODY,
        ),
    ]
    return out


def _sessions_section(week: Week) -> list[Flowable]:
    out: list[Flowable] = [_heading("Exercise sessions")]
    sessions = week["sessions"]
    if not sessions:
        return [*out, _nothing("No exercise session was recorded in this period.")]
    rows: list[list[Any]] = [["Date (UTC)", "Protocol", "Outcome", "Reps / plan", "Rep time (s)", "Elong. (mm)", "ROM (deg)", "EMG %MVC"]]
    extra: list[tuple[Any, ...]] = []
    for i, s in enumerate(sessions, start=1):
        outcome = END_REASON_LABELS.get(s["end_reason"], SESSION_STATE_LABELS.get(s["state"], humanize(s["state"])))
        if s["pain_stop"] and s["pain_stop_rep"] is not None:
            outcome += f" (rep {s['pain_stop_rep']})"
        plan = "" if not s["planned_reps"] else f" / {s['planned_reps']}"
        rows.append(
            [
                f"{s['date']} {s['started_at'][11:16]}",
                _code(s["protocol_id"]),
                outcome,
                _num(s["reps"]) + plan,
                _num(s["rep_duration_s"], "{:.1f}"),
                _num(s["elongation_mm"], "{:.1f}"),
                _num(s["rom_deg"], "{:.1f}"),
                _num(s["emg_pct_mvc"], "{:.0f}"),
            ]
        )
        if s["pain_stop"]:
            extra.append(("TEXTCOLOR", (2, i), (2, i), colors.HexColor(charts.LEVEL_COLORS["red"])))
    out = [KeepTogether([out.pop(), _grid(rows, [2.9 * cm, 4.5 * cm, 2.9 * cm, 1.8 * cm, 1.4 * cm, 1.3 * cm, 1.2 * cm, WIDTH - 16.0 * cm], extra)])]
    out.append(Spacer(1, 3))
    out.append(_p("A dash means that measure is not available for the session (it is never filled with zero).", SMALL))
    png = charts.sessions(week)
    if png is not None:
        out += [Spacer(1, 8), _image(png)]
    return out


def _symptoms_section(week: Week) -> list[Flowable]:
    out: list[Flowable] = [_heading("Symptoms reported by the astronaut")]
    symptoms = week["symptoms"]
    if not week["pain"]["daily_reports"] and not (symptoms["red_flags"] or symptoms["other_symptoms"]):
        return [*out, _nothing("No daily report or urgent symptom report in this period.")]
    if not (symptoms["red_flags"] or symptoms["other_symptoms"]):
        return [*out, _p("No red flags or urgent symptoms were reported this week.", BODY)]
    rows = [["Type", "Symptom", "Days reported"]]
    for kind, codes, label in (
        ("Red flag", symptoms["red_flags"], humanize),
        ("Urgent symptom", symptoms["other_symptoms"], lambda c: OTHER_SYMPTOM_LABELS.get(c, humanize(c))),
    ):
        for code in codes:
            days = [_day(d["date"]) for d in week["days"] if code in d["red_flags"] or code in d["other_symptoms"]]
            rows.append([kind, label(code), ", ".join(days)])
    return [*out, _grid(rows, [3 * cm, 6 * cm, WIDTH - 9 * cm])]


def _odi_section(week: Week) -> list[Flowable]:
    out: list[Flowable] = [_heading("Oswestry questionnaire (informative)")]
    data = week["odi"]
    png = charts.odi(week)
    if data is None or png is None:
        return [*out, _nothing("No weekly Oswestry questionnaire was registered in this period.")]
    text = (
        f"Registered on {data['date']} ({_mission(data['mission_day'])}): {data['percent']:.0f}% of the maximum limitation over the "
        f"{len(data['items'])} items asked. Informative only: it does not change the risk level."
    )
    return [*out, _p(text), Spacer(1, 6), _image(png)]


# --- page ------------------------------------------------------------------------------------------------------


def _cover(canvas: Canvas, week: Week) -> None:
    page_w, page_h = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, page_h - 4.6 * cm, page_w, 4.6 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#4C8DFF"))
    canvas.rect(0, page_h - 4.6 * cm, page_w, 0.18 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(MARGIN, page_h - 1.3 * cm, "ASCLEPIUS ORBIT  |  SPINE HEALTH MONITORING  |  FOR HOUSTON")
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(MARGIN, page_h - 2.45 * cm, "Weekly Spine Health Report")
    canvas.setFont("Helvetica", 10.5)
    mission = week["mission_day"]
    days = "" if mission["start"] is None else f"   |   Mission day {_mission(mission['start'])} to {_mission(mission['end'])}"
    canvas.drawString(MARGIN, page_h - 3.3 * cm, f"Astronaut {week['astronaut_id']}   |   {week['start']} to {week['end']} (UTC){days}")
    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, page_h - 4.05 * cm, f"Generated {_stamp(week['generated_at'])}")


def _running_head(canvas: Canvas, week: Week) -> None:
    page_w, page_h = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, page_h - 1.25 * cm, page_w, 1.25 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(MARGIN, page_h - 0.8 * cm, "Weekly Spine Health Report")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(page_w - MARGIN, page_h - 0.8 * cm, f"{week['astronaut_id']}  |  {week['start']} to {week['end']}")


def _footer(canvas: Canvas, week: Week) -> None:
    page_w, _ = A4
    canvas.setStrokeColor(RULE)
    canvas.line(MARGIN, 1.5 * cm, page_w - MARGIN, 1.5 * cm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(MARGIN, 1.0 * cm, "Provisional content, pending clinical validation. Decision support only; pseudonymous data, no free text.")
    canvas.drawRightString(page_w - MARGIN, 1.0 * cm, f"Page {canvas.getPageNumber()}")


def render_pdf(week: Week, *, compress: bool = True) -> bytes:
    """The report as PDF bytes. `compress=False` leaves the text streams readable (tests)."""
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        pageCompression=int(compress),
        title=f"Weekly spine health report {week['astronaut_id']} {week['start']} to {week['end']}",
        author="Asclepius Orbit",
        subject="Weekly report for Houston",
    )
    first = Frame(MARGIN, 1.9 * cm, WIDTH, A4[1] - 4.6 * cm - 1.9 * cm - 0.5 * cm, id="first", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    later = Frame(MARGIN, 1.9 * cm, WIDTH, A4[1] - 1.25 * cm - 1.9 * cm - 0.3 * cm, id="later", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def on_first(canvas: Canvas, _: Any) -> None:
        _cover(canvas, week)
        _footer(canvas, week)

    def on_later(canvas: Canvas, _: Any) -> None:
        _running_head(canvas, week)
        _footer(canvas, week)

    doc.addPageTemplates([PageTemplate("first", [first], onPage=on_first), PageTemplate("later", [later], onPage=on_later)])
    story: list[Flowable] = [NextPageTemplate("later"), *_status_block(week), Spacer(1, 8), _kpis(week), _heading("Summary of the week")]
    story += [Paragraph(escape(line), BULLET, bulletText="•") for line in summary_lines(week)]
    if not week["has_data"]:
        story += [
            Spacer(1, 6),
            _nothing(
                "Nothing was recorded between " + f"{week['start']} and {week['end']}. This is not a green status: the state of the astronaut is unknown."
            ),
        ]
    png = charts.pain_and_level(week)
    story += [_heading("Pain and risk level by day")]
    story += [_image(png)] if png is not None else [_nothing("No pain report or engine assessment on any day of the week.")]
    story += [KeepTogether([_heading("Day by day"), _daily_table(week)])]
    story += [*_symptoms_section(week), *_odi_section(week), *_engine_section(week), *_sessions_section(week)]
    doc.build(story)
    return buffer.getvalue()
