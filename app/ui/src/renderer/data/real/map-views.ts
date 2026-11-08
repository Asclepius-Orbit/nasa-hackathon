/** Pure mapping from the contract view payloads (snake_case) to the view-models the screens read. Codes and nulls pass through unchanged. */
import type { BaselineView, FirstDayResult, HistoryView, SessionView, StripView, TodayView } from '../../../shared/api';
import type { BaselineData, FirstDayData, HistoryData, SessionData, StripData, TodayData } from '../types';

const PERCENT = 100;

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
          },
    // Reserved by the backend (issue #62): the ground link is unknown, never "up" by default.
    groundLink: null,
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
