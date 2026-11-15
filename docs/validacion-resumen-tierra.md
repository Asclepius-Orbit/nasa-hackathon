# Validation of the ground summary and the PDF report for ground (Houston)

For: **the clinical reviewer**. Review with the maintainer; the result is then applied to the code (issue #62: daily summary to ground; the real urgent send is #63).

How to fill it in: in each table, the box comes **pre-ticked with our proposal**. Change it if you disagree and write your wording or criterion in the note column. Anything not decided here stays as `pending clinical review`; one issue will be opened for each pending point.

Sources (if they differ from this document, the code wins): `spine_monitor/contracts/schemas/v1/app/ground-summary.schema.json`, `spine_monitor/contracts/ui-openapi.yaml` (`getHoustonWeeklyReport`), `spine_monitor/backend/src/monitor/views/ground_summary.py`, `spine_monitor/backend/src/monitor/report/`, `spine_monitor/backend/src/monitor/ground/`, `app/ui/src/renderer/strings/codes.ts` and `app/ui/src/renderer/strings/urgent.ts`.

**Starting principle (applies to the summary and the PDF):** only codes and numbers. No free text (report notes and rule descriptions are not copied), nothing from the astronaut's profile (name, age, etc.), and the only identifier is the pseudonymous `astronaut_id`. Anything unknown is an explicit missing datum. A week or day without an assessment comes out as `nodata`, never with a default green.

## 0. Two different products for ground

| | Daily summary (#62) | Weekly PDF report |
|---|---|---|
| What it is | `ground-summary` JSON message, one revision per change in the day's assessment | PDF document with text, tables and charts |
| Who triggers it | Automatic: outbox and ground link | The astronaut, with the "Generate report for Houston" button on the home screen |
| Window | One UTC day | The last 7 UTC days (up to `end`, today by default). Never the whole history |
| Endpoint | (`ground_outbox` outbox) | `GET /ui/crew/{astronaut_id}/houston-report.pdf?end=YYYY-MM-DD` |
| Status | Provisional content | Provisional content |

The PDF **does not replace** the daily summary or modify it. A weekly PDF send is assumed; saving and transmitting the file to ground are not automated (today the astronaut saves it on the workstation).

## 1. What travels to ground

### 1.1 Daily summary: fields already in the schema

| Field | What it carries today | Decision (reviewer) | Note |
|---|---|---|---|
| `schema_version`, `astronaut_id`, `date`, `mission_day`, `revision`, `as_of` | Technical and identification (pseudonymous id; `mission_day` is `null` without a launch date) | [x] goes | |
| `level`, `assessment_id`, `confidence`, `data_available`, `data_total` | Level `green`/`amber`/`red`/`nodata` and the assessment's confidence | [x] goes | `nodata` if there is no assessment. |
| `drivers[]` | `feature_id` and `deviation_z`, from most to least influential | [x] goes | Catalogue codes. |
| `red_flags` | The day's red flags (closed catalogue) | [x] goes | |
| `other_symptoms` | `very_severe_pain`, `other`; only the code, never the description | [x] goes | Whether they escalate as a red flag: pending clinical review. |
| `sessions[]` | `session_id`, `state`, `protocol_id`, `pain_stop` (repetition and instant, not the intensity) | [x] goes | |
| `recommendations` | `REC_*` codes | [x] goes | |
| `escalation` | `required`, `urgency` (`routine`/`priority`/`immediate`), `reasons` (`RULE_*`) | [x] goes | Texts in section 3. |
| `contacts[]` | `contact_id`, `at`, `contact` (role); the note stays on board | [x] goes | Done in #63. |

### 1.2 Weekly PDF report: proposed content

Data origin: what the astronaut records in the UI (daily report, urgent symptoms, weekly Oswestry), the exercise sessions with their measurements, and the engine's data.

| Block | What the PDF carries | Decision (reviewer) | Note |
|---|---|---|---|
| Header | `astronaut_id`, UTC date range, mission day, generation instant | [x] goes | No name or profile. The profile is only read to number the mission days. |
| Current status | Level of the week's latest assessment, confidence, data completeness, escalation required | [x] goes | No assessment: "NO DATA", grey. |
| Week summary | Template-generated sentences from the data (no AI): level evolution, escalation and rules, days with pain, symptoms, sessions, Oswestry | [x] goes | |
| Pain | Daily pain chart (`pain_nrs`) and worst of 24 h (`pain_nrs_worst_24h`), with a red-flag or urgent-symptom marker | [x] goes | Changes the earlier proposal: **pain does travel**, because they are numbers from the daily report. Confirm the value (current, worst 24 h or both): pending clinical review. |
| Risk level per day | Daily band with the engine's latest level, and risk index and confidence | [x] goes | |
| Daily report | Table per day: pain, worst 24 h, stiffness, sleep, number of sessions, red flags | [x] goes | Numeric values and codes only. Free notes are not included. |
| Symptoms | Table of red flags and urgent symptoms with the days they were reported | [x] goes | Readable label of the code, never the free description of `other`. |
| Oswestry | Latest Oswestry recorded that week, per item and as a percentage | [x] goes | **Informative only**: no role in triage (#107). |
| Exercise sessions | State, protocol, pain stop (repetition) and measurements: repetitions, time per repetition, elongation, range of motion, EMG (%MVC) | [x] goes | Includes lumbar length if the session measures it: **lumbar length does travel** in the PDF. Absolute value or change from the reference: pending clinical review. |
| Engine | Evolution of level and confidence, drivers with z, `REC_*` recommendations, escalation and `RULE_*` rules | [x] goes | Codes only. |
| From the agent | Nothing from MCP or Ollama: no chat, no generated explanations, no agent text | [ ] does not go | Decided: not included. |
| Free text accompanying an escalation | Does not exist and is not proposed | [x] does not go | Pending clinical review: if a readable clinical reason is needed, is a new code from a closed catalogue enough? |

A week without data still generates a PDF, with explicit "no data" messages, never with a default green. The PDF footer says "Provisional content, pending clinical validation" until the clinical reviewer validates.

## 2. When it is sent

**Daily summary (today):** one revision is queued for each change in the day's assessment. If the latest revision is still queued, it is replaced; if it has already been sent, another is opened. A day with a red flag or urgent symptom whose assessment was skipped is queued with level `nodata` and the flag; the same for a pain stop or a medical contact. The sender drains the queue only when the link is up, in chronological order, with retries using exponential backoff. Today **an escalation does not jump ahead of an older routine summary**.

**PDF report:** a **weekly** send is assumed. The astronaut generates it on demand with the button and it always covers the last 7 days.

| Decision | Options | Decision (reviewer) |
|---|---|---|
| PDF cadence | Weekly on demand (proposal) / weekly reminder or automatic generation | pending clinical review |
| How the PDF reaches ground | Today the astronaut saves it. Is it transmitted over the ground link like the summary? | pending clinical review |
| Daily summary send policy | [ ] The current one: chronological. [ ] Priority send of escalations and red flags | pending clinical review |
| What counts as priority | Proposal: `escalation.required = true` or any red flag. Also `other_symptoms`? `pain_stop`? | pending clinical review |

## 3. Urgency texts in the app

Source: `app/ui/src/renderer/strings/codes.ts` (`urgencyText`, `escalationRuleText`). They are shown to the astronaut as is. An unknown code is shown as the code itself.

| Code | Current wording | Reviewer's wording |
|---|---|---|
| `routine` | routine, can wait for the next scheduled contact | pending clinical review |
| `priority` | priority, needs the flight surgeon soon | pending clinical review |
| `immediate` | immediate, needs the flight surgeon now | pending clinical review |
| `RULE_RED_FLAG_NEURO` | warning neurological symptom | pending clinical review |
| `RULE_SYMPTOM_PAIN_TREND` | pain trend rising | pending clinical review |
| `RULE_INCOMPLETE_DATA` | data too incomplete to assess | pending clinical review |
| `RULE_UNKNOWN_RED_FLAG` | unrecognised warning symptom | pending clinical review |

Open question: are there more escalation rules that should have their own text? The others are currently shown as their raw code. Note: the PDF shows the `RULE_*` codes as codes; if the reviewer writes the texts, should they also appear in the PDF? (pending clinical review).

## 4. Urgent in real mode

Until real sending exists (#63) the app cannot send urgent notices and says so, asking the user to tell the medical officer in person. Current texts in `app/ui/src/renderer/strings/urgent.ts`:

- `introNotSent`: "Mark what applies. This app cannot send urgent reports: tell the crew medical officer now, in person or by voice."
- `notRecordedBody`: "The report could not be saved. Tell the crew medical officer now, in person or by voice."
- `noCrew`: "The crew list could not be read, so this report cannot be sent from here. Tell the crew medical officer now, in person or by voice."

| Question | Decision (reviewer) |
|---|---|
| Is this text adequate while sending does not exist? | [ ] yes, as is. [ ] no, own wording: pending clinical review |
| Should another channel also be named (e.g. radio or voice with Houston)? | pending clinical review |

The PDF report **is not an urgent notice**: it does not replace telling the medical officer in person.

## Pending decisions

One issue per point.

- [ ] 1a. Pain in the PDF: confirm the value (current, worst 24 h or both) and whether it also travels in the daily summary (pending clinical review)
- [ ] 1b. Lumbar length: absolute value or change from the reference, in the PDF and in the daily summary (pending clinical review)
- [ ] 1c. Text accompanying an escalation: confirm it does not travel, or define a closed catalogue of reasons (pending clinical review)
- [ ] 1d. `other_symptoms`: confirm they travel only as a code and whether they escalate as a red flag (pending clinical review)
- [ ] 1e. Oswestry: confirm it goes in the PDF only as informative data, with no role in triage (pending clinical review)
- [ ] 2a. PDF cadence and how it reaches ground (pending clinical review)
- [ ] 2b. Daily summary send policy: chronological or prioritised for escalations and red flags, and what is priority (pending clinical review)
- [ ] 3a. Wording of `routine`, `priority`, `immediate` (pending clinical review)
- [ ] 3b. Wording of the four escalation reasons and missing rules, and whether they appear in the PDF (pending clinical review)
- [ ] 4. "Cannot be sent, tell someone in person" text until #63 (pending clinical review)

## How it is applied to the code

The maintainer does it after reviewing this document with the clinical reviewer.

| Decision | Files that change |
|---|---|
| 1a, 1b, 1e: what the PDF carries | `spine_monitor/backend/src/monitor/report/` (`week.py` data, `charts.py` charts, `houston_pdf.py` layout) and `spine_monitor/backend/tests/test_houston_week.py` |
| 1a, 1b, 1c: daily summary fields | Schema `ground-summary.schema.json` (and its "PROVISIONAL" description); examples `contracts/examples/valid/app/ground-summary*.json`; view `ground_summary.py`; tests `test_ground_summary.py` and `contracts/tests/`. Repo rule: contract first (skill `contract-first`), then the generated models |
| 1d: `other_symptoms` | `ground_summary.py` (`_day_other_symptoms`), `domain/self-report.schema.json` if the catalogue changes, view tests |
| 2a: PDF cadence and delivery | `api.py` (endpoint), `app/ui/src/main/views/houston-report-service.ts` and `app/ui/src/renderer/features/today/HoustonReportCard.tsx` |
| 2b: daily summary priority | `db.py` (`next_ground_item`, `upsert_ground_summary`), `ground/enqueue.py`, `ground/sender.py`; tests `test_ground_flow.py`, `test_ground_sender.py` |
| 3a, 3b: urgency texts | `app/ui/src/renderer/strings/codes.ts` and UI tests that quote them (e.g. `TodayView.test.tsx`); `report/labels.py` if they appear in the PDF |
| 4: in-person notice | `app/ui/src/renderer/strings/urgent.ts` and `UrgentDialog.test.tsx` |
| Any validation | Remove the "PROVISIONAL / pending validation" notes from the schema, from `ground_summary.py` and the PDF footer |
