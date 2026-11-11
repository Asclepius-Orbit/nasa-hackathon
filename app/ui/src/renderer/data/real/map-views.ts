/** Pure mapping from the contract view payloads (snake_case) to the view-models the screens read. Codes and nulls pass through unchanged. */
import type { BaselineView, FirstDayResult, HistoryView, HoustonView, OutboxView, RepStateView, SessionView, StripView, TodayView } from '../../../shared/api';
import type { BaselineData, FirstDayData, GroundLink, HistoryData, HoustonData, RepState, SessionData, StripData, TodayData } from '../types';

const PERCENT = 100;

/** The link as the contract gives it; `null` (not configured or unknown) stays `null`, never "up". */
export function toGroundLink(link: StripView['ground_link']): GroundLink | null {
  if (link === null) return null;
  return link.state === 'up' ? { state: 'up' } : { state: 'down', backInMin: link.back_in_min };
}

export function toStrip(view: StripView): StripData {
  const { harness } = view;
  return {
    missionDay: view.mission.day,
    missionStart: view.mission.start,
    flightPhase: view.mission.phase,
    harness:
      harness === null
        ? null
        : {
            id: harness.device_id,
            batteryPct: harness.battery_pct,
            weakStreams: harness.weak_streams.map((w) => ({ stream: w.stream, state: w.state })),
            degraded: harness.degraded,
            stale: harness.stale,
            lastSeenAt: harness.last_seen_at,
          },
    groundLink: toGroundLink(view.ground_link),
    lastContactMinAgo: view.last_contact_min_ago,
    reportsDue: Number(view.reports_due.daily) + Number(view.reports_due.weekly_odi),
  };
}

export function toToday(view: TodayView): TodayData {
  const { trust, session_in_progress: running } = view;
  return {
    level: view.level,
    recommendations: view.recommendations,
    reasons: view.reasons.map((r) => ({ featureId: r.feature_id, deviationZ: r.deviation_z, contribution: r.contribution })),
    trust:
      trust === null || view.assessed_at === null
        ? null
        : {
            confidencePct: Math.round(trust.confidence * PERCENT),
            dataAvailable: trust.data_available,
            dataTotal: trust.data_total,
            missing: trust.missing.map((m) => ({ what: m.what, reason: m.reason })),
            assessedAt: view.assessed_at,
          },
    alsoToday: view.also_today,
    lumbarLengthMm: view.lumbar_delta_mm,
    lumbarMeasuredAt: view.lumbar_measured_at,
    sessionInProgress:
      running === null
        ? null
        : { sessionId: running.session_id, protocolId: running.protocol_id, startedAt: running.started_at, totalReps: running.total_reps },
  };
}

export function toBaseline(view: BaselineView): BaselineData {
  return {
    recorded: view.recorded,
    required: view.required,
    referenceVersion: view.reference_version,
    builtFromSessions: view.built_from_sessions,
    sessions: view.sessions.map((s) => ({
      sessionId: s.session_id,
      protocolId: s.protocol_id,
      status: s.status,
      offsetDays: s.offset_days,
      date: s.date,
    })),
  };
}

export function toFirstDay(view: FirstDayResult): FirstDayData {
  return { crewFirstName: view.crewFirstName, todo: view.todo, baseline: toBaseline(view.baseline) };
}

export function toHistory(view: HistoryView): HistoryData {
  return {
    from: view.from,
    to: view.to,
    todayDay: view.today_day,
    preflightRangeMm: view.preflight_range_mm === null ? null : { lo: view.preflight_range_mm.lo, hi: view.preflight_range_mm.hi },
    days: view.days.map((d) => ({
      date: d.date,
      day: d.mission_day,
      level: d.level,
      lengthMm: d.length_delta_mm,
      pain: d.pain_nrs,
      session: d.session === null ? null : { sessionId: d.session.session_id, state: d.session.state, protocolId: d.session.protocol_id },
      report: d.report === null ? null : { kind: d.report.kind, redFlags: d.report.red_flags },
    })),
  };
}

export function toSession(view: SessionView): SessionData {
  const { band, calibration } = view;
  return {
    sessionId: view.session_id,
    state: view.state,
    protocolId: view.protocol_id,
    band: band === null ? null : { color: band.color, nominalKg: band.nominal_kg },
    totalReps: view.total_reps,
    restSeconds: view.rest_seconds,
    fitItems: view.fit_items,
    startedAt: view.started_at,
    steps: view.steps.map((s) => ({ id: s.id, status: s.status })),
    sensorChecks: view.sensor_checks.map((c) => ({ stream: c.stream, status: c.status, state: c.state })),
    calibration: calibration === null ? null : { commandId: calibration.command_id, acked: calibration.acked, ackedAt: calibration.acked_at },
    stretchedCm: view.stretched_cm,
    plannedAt: view.planned_at,
  };
}

/** The live counter as the contract gives it: nulls stay null (no total, no phase, no bar on the first rep). */
export function toRepState(view: RepStateView): RepState {
  return {
    rep: view.rep,
    total: view.total,
    phase: view.phase,
    barPercent: view.progress_pct,
    last: view.last === null ? null : { rep: view.last.rep, seconds: view.last.duration_s },
    signal: view.signal,
  };
}

/** The final count when the session ends: the backend's `end` state, flagged `ended`, with no phase or bar (nothing is moving any more). */
export function toEndedRepState(view: RepStateView): RepState {
  return { ...toRepState(view), phase: null, barPercent: null, signal: 'ended' };
}

/** The last known counter when the stream drops: no phase and no bar (they would be stale), flagged `lost`. Before any state there is nothing to keep, so `null`: no rep 0 is invented. */
export function toLostRepState(previous: RepState | null): RepState | null {
  if (previous === null) return null;
  return { rep: previous.rep, total: previous.total, phase: null, barPercent: null, last: previous.last, signal: 'lost' };
}

/** The Houston screen: the summary and link of `/houston` plus the queue of `/outbox`. Codes and nulls pass through. */
export function toHouston(view: HoustonView, outbox: OutboxView): HoustonData {
  const { summary } = view;
  return {
    crewCode: view.astronaut_id,
    summary:
      summary === null
        ? null
        : {
            date: summary.date,
            missionDay: summary.mission_day,
            revision: summary.revision,
            asOf: summary.as_of,
            level: summary.level,
            confidencePct: summary.confidence === null ? null : Math.round(summary.confidence * PERCENT),
            dataAvailable: summary.data_available,
            dataTotal: summary.data_total,
            drivers: summary.drivers.map((d) => ({ featureId: d.feature_id, deviationZ: d.deviation_z })),
            redFlags: summary.red_flags,
            sessions: summary.sessions.map((s) => ({ sessionId: s.session_id, state: s.state, protocolId: s.protocol_id })),
            recommendations: summary.recommendations,
            escalation:
              summary.escalation === null
                ? null
                : { required: summary.escalation.required, urgency: summary.escalation.urgency, reasons: summary.escalation.reasons },
            contacts: null, // in the contract since #63; not shown on the screen yet
          },
    outbox: outbox.items.map((i) => ({
      date: i.date,
      missionDay: i.mission_day,
      revision: i.revision,
      state: i.state,
      enqueuedAt: i.enqueued_at,
      receivedAt: i.received_at,
      attempts: i.attempts,
    })),
    groundLink: toGroundLink(view.ground_link),
    lastContactAt: view.last_contact_at,
    transport: view.transport,
    contentValidated: view.content_validated,
  };
}
