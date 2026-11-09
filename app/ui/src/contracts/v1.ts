/* eslint-disable */
/**
 * Generated from spine_monitor/contracts/schemas/v1 by scripts/generate-contract-types.mjs.
 * Do not edit by hand: run `pnpm run gen:types`.
 */

/**
 * What the astronaut tells the app. Three kinds, by effort: 'daily' (quick, under 30 s, usable with gloves), 'weekly_odi' (reduced Oswestry Disability Index, once a week) and 'red_flag' (neurological or emergency symptoms, available at any time, escalates immediately). The daily report also asks the red flags, so a red_flag report is only needed outside the daily routine.
 */
export type SelfReport = DailySelfReport | WeeklyOdiReport | RedFlagReport;
export type DailySelfReport = ReportBase & {
  kind: "daily";
  /**
   * Numeric Rating Scale, integer 0 (none) to 10 (worst imaginable).
   */
  pain_nrs: number;
  /**
   * Numeric Rating Scale, integer 0 (none) to 10 (worst imaginable).
   */
  pain_nrs_worst_24h?: number;
  pain_locations?: PainLocation[];
  /**
   * Numeric Rating Scale, integer 0 (none) to 10 (worst imaginable).
   */
  stiffness_nrs: number;
  sleep?: {
    hours: number;
    /**
     * Numeric Rating Scale, integer 0 (none) to 10 (worst imaginable).
     */
    quality_nrs?: number;
  };
  /**
   * Red flags present today. Empty array = the question was asked and the answer was none; absent = not asked.
   *
   * Items: Symptoms that suggest nerve involvement or an emergency. Any one of them escalates, regardless of the model.
   */
  red_flags?: (
    | "radiating_leg_pain"
    | "numbness_or_tingling"
    | "leg_weakness"
    | "bladder_or_bowel_dysfunction"
    | "saddle_numbness"
    | "severe_night_pain"
    | "fever_with_back_pain"
    | "trauma_event"
  )[];
};
/**
 * Oswestry Disability Index items, each scored 0 (no limitation) to 5 (maximum). The set of items asked (the 'reduced' version) is chosen by the clinical model; the ids follow the standard ODI sections: pain_intensity, personal_care, lifting, walking, sitting, standing, sleeping, sex_life, social_life, travelling.
 */
export type WeeklyOdiReport = ReportBase & {
  kind: "weekly_odi";
  /**
   * @minItems 1
   * @maxItems 10
   */
  items:
    | [
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ]
    | [
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        },
        {
          item_id: string;
          score: number;
        }
      ];
};
export type RedFlagReport = ReportBase & {
  kind: "red_flag";
  /**
   * @minItems 1
   *
   * Items: Symptoms that suggest nerve involvement or an emergency. Any one of them escalates, regardless of the model.
   */
  red_flags: [
    (
      | "radiating_leg_pain"
      | "numbness_or_tingling"
      | "leg_weakness"
      | "bladder_or_bowel_dysfunction"
      | "saddle_numbness"
      | "severe_night_pain"
      | "fever_with_back_pain"
      | "trauma_event"
    ),
    ...(
      | "radiating_leg_pain"
      | "numbness_or_tingling"
      | "leg_weakness"
      | "bladder_or_bowel_dysfunction"
      | "saddle_numbness"
      | "severe_night_pain"
      | "fever_with_back_pain"
      | "trauma_event"
    )[]
  ];
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  onset?: string;
  note?: string;
};
export type StreamDeclaration = {
  /**
   * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream_id: string;
  stream_kind: "emg" | "imu" | "stretch";
  placement: Placement;
  sample_rate_hz: number;
  /**
   * RFC 4122 UUID, lowercase.
   */
  calibration_id?: string;
} & {
  /**
   * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream_id: string;
  stream_kind: "emg" | "imu" | "stretch";
  placement: Placement;
  sample_rate_hz: number;
  /**
   * RFC 4122 UUID, lowercase.
   */
  calibration_id?: string;
};
/**
 * Where a sensor sits on the body. EMG needs muscle + side, IMU needs spine_level, stretch cord needs spine_region (and optionally the span it covers). The session schema enforces this per stream kind.
 */
export type Placement = (
  | {
      [k: string]: unknown | undefined;
    }
  | {
      [k: string]: unknown | undefined;
    }
  | {
      [k: string]: unknown | undefined;
    }
) & {
  /**
   * Muscle under an EMG electrode pair.
   */
  muscle?:
    | "latissimus_dorsi"
    | "trapezius"
    | "rhomboids"
    | "erector_spinae"
    | "multifidus"
    | "biceps_brachii"
    | "rectus_abdominis";
  side?: "left" | "right" | "midline";
  /**
   * Vertebral level, lowercase so it can be embedded in ids (imu.l3.*).
   */
  spine_level?:
    | "c7"
    | "t1"
    | "t2"
    | "t3"
    | "t4"
    | "t5"
    | "t6"
    | "t7"
    | "t8"
    | "t9"
    | "t10"
    | "t11"
    | "t12"
    | "l1"
    | "l2"
    | "l3"
    | "l4"
    | "l5"
    | "s1";
  spine_region?: "cervical" | "thoracic" | "thoracolumbar" | "lumbar" | "sacral";
  /**
   * Vertebral span covered by a stretch cord.
   */
  span?: {
    /**
     * Vertebral level, lowercase so it can be embedded in ids (imu.l3.*).
     */
    from_level:
      | "c7"
      | "t1"
      | "t2"
      | "t3"
      | "t4"
      | "t5"
      | "t6"
      | "t7"
      | "t8"
      | "t9"
      | "t10"
      | "t11"
      | "t12"
      | "l1"
      | "l2"
      | "l3"
      | "l4"
      | "l5"
      | "s1";
    /**
     * Vertebral level, lowercase so it can be embedded in ids (imu.l3.*).
     */
    to_level:
      | "c7"
      | "t1"
      | "t2"
      | "t3"
      | "t4"
      | "t5"
      | "t6"
      | "t7"
      | "t8"
      | "t9"
      | "t10"
      | "t11"
      | "t12"
      | "l1"
      | "l2"
      | "l3"
      | "l4"
      | "l5"
      | "s1";
  };
  description?: string;
};
/**
 * Something the astronaut still has to do. TASK_DAILY_REPORT: daily self-report. TASK_ODI: weekly Oswestry report. TASK_BASELINE_SESSION: a pre-flight reference session. TASK_SESSION: the planned exercise session.
 */
export type TaskCode = "TASK_DAILY_REPORT" | "TASK_ODI" | "TASK_BASELINE_SESSION" | "TASK_SESSION";
/**
 * Traffic-light level of a day or of today, or 'nodata' when there is no assessment. Never a green by default.
 */
export type DayLevel = "green" | "amber" | "red" | "nodata";
/**
 * Lifecycle of a session in the app: starting (start command sent), running (acked by the gateway), rejected (refused, timed out or bus down), ended, processing (features and assessment being computed), processed, processing_failed.
 */
export type SessionState =
  "starting" | "running" | "rejected" | "ended" | "processing" | "processed" | "processing_failed";
/**
 * Id of a fit item of a protocol, as in catalog/protocol-catalog.json.
 */
export type FitItemId = string;
/**
 * Response of GET /ui/crew/{astronaut_id}/views/today: the Today screen of the desktop UI, built from the latest assessment. Codes and data only (recommendation codes, feature ids, missing reasons, task codes); the UI writes the words. Without an assessment the level is 'nodata' and every assessment field is null or empty, never a reassuring default.
 */
export type TodayView = {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  level: DayLevel;
  assessment_id: string | null;
  /**
   * as_of of the assessment.
   */
  assessed_at: string | null;
  /**
   * recommendations of the assessment, in order.
   */
  recommendations: string[];
  /**
   * drivers of the assessment, most influential first.
   */
  reasons: TodayReason[];
  /**
   * How much to trust the level: confidence and completeness of the assessment. Null without an assessment.
   */
  trust: TrustFacts | null;
  /**
   * Latest lumbar elongation (stretch.lumbar.peak_elongation_mm) minus its baseline mean, in mm. Null when either is missing; never 0 by default.
   */
  lumbar_delta_mm: number | null;
  /**
   * Instant of the measure behind lumbar_delta_mm: start of the window of the feature set (computed_at when it has none). Null exactly when lumbar_delta_mm is null.
   */
  lumbar_measured_at: string | null;
  /**
   * Session currently starting or running; null when there is none or the latest one is older than the staleness threshold (settings stale_session_hours), so a session that never ended is not shown as live.
   */
  session_in_progress: SessionInProgress | null;
  /**
   * Other things still to do today.
   */
  also_today: TaskCode[];
};
/**
 * A calibration is an entity with its own id and date; sessions and chunks reference it by calibration_id. Three kinds: stretch (cord rest resistance and resistance->length curve), emg_mvc (maximum voluntary contraction, to express EMG as %MVC and compare days/sessions despite electrode re-placement and skin changes) and imu (bias, configured ranges and mounting on the body).
 */
export type Calibration = StretchCalibration | EmgMvcCalibration | ImuCalibration;
/**
 * Adafruit 519 cord. The datasheet gives ~140-160 ohm/cm but the response is non-linear and the cord needs 1-2 minutes to recover after stretching, so the curve is measured, not assumed, and rest_resistance_ohm is taken after a recovery wait.
 */
export type StretchCalibration = CalibrationBase & {
  calibration_type: "stretch";
  rest_resistance_ohm: number;
  /**
   * Length of cord between the two clamps at rest (the reference for elongation).
   */
  rest_length_mm: number;
  recovery_wait_s?: number;
  temperature_c?: number;
  /**
   * Piecewise-linear resistance -> length. Points sorted by resistance. Elongation = length_mm - rest_length_mm.
   */
  curve: {
    model: "piecewise_linear";
    /**
     * @minItems 2
     */
    points: [
      {
        resistance_ohm: number;
        length_mm: number;
      },
      {
        resistance_ohm: number;
        length_mm: number;
      },
      ...{
        resistance_ohm: number;
        length_mm: number;
      }[]
    ];
    fit_rmse_mm?: number;
  };
};
/**
 * Reference amplitude of a muscle during a maximum voluntary contraction. Features expressed as %MVC divide by mvc_rms_mv. Redo it whenever the electrodes are re-placed.
 */
export type EmgMvcCalibration = CalibrationBase & {
  calibration_type: "emg_mvc";
  /**
   * Muscle under an EMG electrode pair.
   */
  muscle:
    | "latissimus_dorsi"
    | "trapezius"
    | "rhomboids"
    | "erector_spinae"
    | "multifidus"
    | "biceps_brachii"
    | "rectus_abdominis";
  side: "left" | "right" | "midline";
  mvc_rms_mv: number;
  /**
   * RMS window used.
   */
  window_ms: number;
  n_trials?: number;
  signal_kind: "raw" | "rectified" | "envelope";
  /**
   * RMS with the muscle relaxed, for noise-floor subtraction.
   */
  resting_baseline_mv?: number;
};
/**
 * Static bias, configured ranges and mounting of one IMU. mounting maps each SENSOR axis to the BODY axis it points along when the astronaut stands in the reference posture (in microgravity: the posture defined by the protocol).
 */
export type ImuCalibration = CalibrationBase & {
  calibration_type: "imu";
  /**
   * Vertebral level, lowercase so it can be embedded in ids (imu.l3.*).
   */
  spine_level:
    | "c7"
    | "t1"
    | "t2"
    | "t3"
    | "t4"
    | "t5"
    | "t6"
    | "t7"
    | "t8"
    | "t9"
    | "t10"
    | "t11"
    | "t12"
    | "l1"
    | "l2"
    | "l3"
    | "l4"
    | "l5"
    | "s1";
  accel_range_g: 2 | 4 | 8 | 16;
  gyro_range_dps: 250 | 500 | 1000 | 2000;
  gyro_bias_dps: Xyz;
  accel_bias_g?: Xyz;
  mounting: {
    /**
     * Anatomical direction a sensor axis points to: cranial/caudal (head/feet), anterior/posterior (front/back), left/right.
     */
    sensor_x: "cranial" | "caudal" | "anterior" | "posterior" | "left" | "right";
    /**
     * Anatomical direction a sensor axis points to: cranial/caudal (head/feet), anterior/posterior (front/back), left/right.
     */
    sensor_y: "cranial" | "caudal" | "anterior" | "posterior" | "left" | "right";
    /**
     * Anatomical direction a sensor axis points to: cranial/caudal (head/feet), anterior/posterior (front/back), left/right.
     */
    sensor_z: "cranial" | "caudal" | "anterior" | "posterior" | "left" | "right";
  };
  /**
   * How long the sensor was held still to estimate the bias.
   */
  static_duration_s?: number;
};
/**
 * A block of consecutive samples of ONE sensor stream, published by the gateway (or the simulator) on asclepius/v1/crew/{astronaut_id}/device/{device_id}/stream/{stream_id}. Data is columnar: one array per channel, all arrays of a chunk have n_samples entries. Streams have independent sample rates (EMG 1000 Hz, IMU 100 Hz, stretch 20 Hz by default); the schema accepts any rate. Time of sample i is t0_device_ms + i * 1000 / sample_rate_hz on the device monotonic clock; use the clock-sync message to map it to UTC.
 */
export type StreamChunk = EmgChunk | ImuChunk | StretchChunk;
/**
 * Surface EMG, one differential channel, millivolts. Raw signal is needed for spectral features (median frequency, fatigue); if the analog module only provides a rectified/envelope signal, say so in signal_kind and the engine will skip spectral features.
 */
export type EmgChunk = ChunkBase & {
  stream_kind: "emg";
  signal_kind: "raw" | "rectified" | "envelope";
  units: {
    emg: "mV";
  };
  data: {
    /**
     * @minItems 1
     */
    emg: [number, ...number[]];
  };
  quality: {
    emg: Quality;
  };
  /**
   * How the voltage was digitised, so mV can be audited.
   */
  acquisition?: {
    adc_bits?: number;
    vref_v?: number;
    gain?: number;
    input_pin?: string;
  };
  /**
   * Filtering applied before the samples were sent (analog module or firmware). Null/absent field = no filter.
   */
  filter?: {
    highpass_hz?: number;
    lowpass_hz?: number;
    notch_hz?: 50 | 60;
  };
};
/**
 * 6-axis IMU (MPU-6050). Axes are the SENSOR axes; the mapping to body axes lives in the imu calibration. In microgravity accel does not contain gravity.
 */
export type ImuChunk = ChunkBase & {
  stream_kind: "imu";
  units: {
    accel_x: "g";
    accel_y: "g";
    accel_z: "g";
    gyro_x: "deg/s";
    gyro_y: "deg/s";
    gyro_z: "deg/s";
  };
  /**
   * Six equally long arrays.
   */
  data: {
    accel_x: NumberArray;
    accel_y: NumberArray;
    accel_z: NumberArray;
    gyro_x: NumberArray;
    gyro_y: NumberArray;
    gyro_z: NumberArray;
  };
  quality: {
    accel: Quality;
    gyro: Quality;
  };
  config?: {
    accel_range_g?: 2 | 4 | 8 | 16;
    gyro_range_dps?: 250 | 500 | 1000 | 2000;
    dlpf_hz?: number;
  };
};
/**
 * @minItems 1
 */
export type NumberArray = [number, ...number[]];
/**
 * Conductive rubber cord (Adafruit 519). adc_counts is the ground truth; resistance_ohm is derived from the divider circuit; length_mm is derived through a stretch calibration (non-linear, with 1-2 min recovery hysteresis) and requires calibration_id. Never send only derived values.
 */
export type StretchChunk = ChunkBase & {
  stream_kind: "stretch";
  units: {
    adc_counts: "count";
    resistance_ohm?: "ohm";
    length_mm?: "mm";
  };
  data: {
    /**
     * @minItems 1
     */
    adc_counts: [number, ...number[]];
    resistance_ohm?: NumberArray;
    length_mm?: NumberArray;
  };
  /**
   * RFC 4122 UUID, lowercase.
   */
  calibration_id?: string;
  quality: {
    adc_counts: Quality;
    length_mm?: Quality;
  };
  /**
   * Divider and ADC used to read the cord.
   */
  circuit?: {
    adc_bits?: number;
    vref_v?: number;
    series_resistor_ohm?: number;
    input_pin?: string;
  };
};
/**
 * Control message from the app to the gateway, published QoS 1 on .../device/{device_id}/cmd. The gateway answers with a command-ack on .../ack. The UI drives the standardised protocol step by step with these commands.
 */
export type Command = StartSessionCommand | StopSessionCommand | MarkEventCommand | CalibrateCommand;
/**
 * Begin recording. Every chunk published until stop_session carries this session_id. The full session record (streams, placement, load) is stored by the app; the gateway only needs what is below.
 */
export type StartSessionCommand = CommandBase & {
  type: "start_session";
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  session_type: "exercise" | "spine_check";
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  /**
   * Streams to enable. Empty = all the device has.
   *
   * Items: Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream_ids?: string[];
  max_duration_s?: number;
};
export type StopSessionCommand = CommandBase & {
  type: "stop_session";
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  reason: "completed" | "aborted_by_user" | "aborted_pain" | "aborted_other";
};
/**
 * Timestamp an event inside a running session (start/end of a repetition, MVC effort...). The device stamps the event on its monotonic clock when it receives the command.
 */
export type MarkEventCommand = CommandBase & {
  type: "mark_event";
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  event_type:
    | "rep_start"
    | "rep_end"
    | "mvc_start"
    | "mvc_end"
    | "rest_start"
    | "rest_end"
    | "pain_reported"
    | "artifact"
    | "note";
  rep_index?: number;
  note?: string;
};
/**
 * Ask the device to run a calibration routine on some streams. The result is a calibration entity referenced by calibration_id in the ack.
 */
export type CalibrateCommand = CommandBase & {
  type: "calibrate";
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  calibration_type: "stretch" | "emg_mvc" | "imu";
  /**
   * @minItems 1
   *
   * Items: Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream_ids: [string, ...string[]];
  duration_s?: number;
};

/**
 * Index of every v1 contract, keyed by schema name. Only used to generate the types.
 */
export interface ContractsV1 {
  "app/assessment-list"?: AssessmentList;
  "app/error"?: AppErrorResponse;
  "app/explanation"?: Explanation;
  "app/history"?: History;
  "app/self-report-list"?: SelfReportList;
  "app/session-list"?: SessionList;
  "app/status"?: Status;
  "app/ui-baseline"?: BaselineView;
  "app/ui-common"?: UIViewDefinitions;
  "app/ui-first-day"?: FirstDayView;
  "app/ui-history"?: HistoryView;
  "app/ui-session"?: SessionView;
  "app/ui-strip"?: StripView;
  "app/ui-today"?: TodayView;
  common?: CommonDefinitions;
  "domain/assessment"?: Assessment;
  "domain/astronaut-profile"?: AstronautProfile;
  "domain/baseline"?: Baseline;
  "domain/calibration"?: Calibration;
  "domain/daily-context"?: DailyContext;
  "domain/feature-set"?: FeatureSet;
  "domain/self-report"?: SelfReport;
  "domain/session"?: Session;
  "engine/assess-request"?: AssessRequest;
  "engine/baseline-request"?: BaselineRequest;
  "engine/error"?: ErrorResponse;
  "engine/features-request"?: FeaturesRequest;
  "engine/simulate-request"?: SimulateRequest;
  "engine/simulate-response"?: SimulateResponse;
  "mcp/compare-periods"?: ComparePeriods;
  "telemetry/clock-sync"?: ClockSync;
  "telemetry/command-ack"?: CommandAck;
  "telemetry/command"?: Command;
  "telemetry/device-status"?: DeviceStatus;
  "telemetry/stream-chunk"?: StreamChunk;
}
/**
 * Response of GET /crew/{astronaut_id}/assessments: assessments with as_of inside the window, oldest first. limit caps items; truncated says there were more, and the most recent items are kept.
 */
export interface AssessmentList {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  from: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  to: string;
  limit: number;
  truncated: boolean;
  items: Assessment[];
}
/**
 * Output of the engine (POST /assess). The overall level is decided by the deterministic clinical rules, which can override the statistical model; fired_rules makes that visible. confidence and completeness exist so that missing or poor data can never produce a reassuring green by default. The engine does not diagnose: it flags deviations from the astronaut's own baseline and recommends actions from a fixed catalogue.
 */
export interface Assessment {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  assessment_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  as_of: string;
  mission_day?: number;
  /**
   * Traffic-light level. Final level is decided by the deterministic clinical rules, not by the model alone.
   */
  level: "green" | "amber" | "red";
  /**
   * Number in [0, 1].
   */
  risk_score: number;
  /**
   * Number in [0, 1].
   */
  confidence: number;
  completeness: {
    /**
     * Number in [0, 1].
     */
    score: number;
    missing: {
      /**
       * A feature_id, a domain name, or one of: self_report, daily_context, baseline.
       */
      what: string;
      reason: "no_data" | "low_quality" | "no_baseline" | "stale" | "sensor_offline" | "signal_not_raw";
    }[];
  };
  /**
   * Traffic light per subdomain. The overall level is at least the worst domain level.
   */
  domains: {
    domain: "spine_length" | "muscle" | "kinematics" | "symptoms";
    /**
     * Traffic-light level. Final level is decided by the deterministic clinical rules, not by the model alone.
     */
    level: "green" | "amber" | "red";
    /**
     * Number in [0, 1].
     */
    score?: number;
    /**
     * False when there was no data for the domain; level is then not a real green.
     */
    evaluated: boolean;
  }[];
  /**
   * Features that pushed the risk, most influential first (explainability).
   */
  drivers: {
    /**
     * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
     */
    feature_id: string;
    value: number;
    unit?: string;
    /**
     * Robust z-score against the matching baseline entry.
     */
    deviation_z?: number;
    /**
     * Share of the risk score explained by this feature.
     */
    contribution: number;
  }[];
  trend?: {
    /**
     * Traffic-light level. Final level is decided by the deterministic clinical rules, not by the model alone.
     */
    projected_level: "green" | "amber" | "red";
    days_to_threshold?: number | null;
  };
  /**
   * Clinical rules that triggered, for traceability (explain_assessment).
   */
  fired_rules: {
    rule_id: string;
    domain?: "spine_length" | "muscle" | "kinematics" | "symptoms" | "safety";
    /**
     * Traffic-light level. Final level is decided by the deterministic clinical rules, not by the model alone.
     */
    level: "green" | "amber" | "red";
    /**
     * True when the rule set a level higher than the statistical model alone would have.
     */
    overrides_model: boolean;
    description?: string;
    /**
     * The datum, observed value and threshold comparison that fired the rule.
     */
    evidence?: {
      datum: string;
      value: number;
      operator: ">" | ">=" | "<" | "<=" | "==" | "!=";
      threshold: number;
    };
  }[];
  recommendations: string[];
  escalation: {
    required: boolean;
    urgency?: "routine" | "priority" | "immediate";
    reasons: string[];
  };
  /**
   * Inputs the assessment used, so it can be audited and reproduced.
   */
  based_on?: {
    /**
     * RFC 4122 UUID, lowercase.
     */
    baseline_id?: string;
    baseline_version?: number;
    /**
     * Items: RFC 4122 UUID, lowercase.
     */
    session_ids?: string[];
    /**
     * Items: RFC 4122 UUID, lowercase.
     */
    self_report_ids?: string[];
    engine_version?: string;
  };
}
/**
 * Error body of every endpoint of the read-only API of the app, shaped after RFC 9457 problem details (served as application/problem+json). Same shape as the engine error, with the problems a read API can have.
 */
export interface AppErrorResponse {
  type: "invalid_request" | "invalid_range" | "not_found" | "unsupported_schema_version" | "internal";
  title: string;
  status: number;
  detail?: string;
  /**
   * Field-level problems, as JSON Pointer (or query parameter name) + message.
   */
  errors?: {
    pointer: string;
    message: string;
  }[];
}
/**
 * Response of GET /assessments/{assessment_id}/explanation; backs the MCP tool explain_assessment. The stored assessment (drivers, fired_rules, based_on) plus what based_on points to, already resolved, so the model does not have to chase ids. Anything cited by based_on that no longer exists is listed in unresolved instead of being dropped silently.
 */
export interface Explanation {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  assessment: Assessment;
  /**
   * Baseline used by the assessment; null when the assessment did not cite one.
   */
  baseline: {
    /**
     * RFC 4122 UUID, lowercase.
     */
    baseline_id: string;
    version: number;
  } | null;
  /**
   * Baseline entries of the assessment drivers only (reference median and MAD to read each deviation_z).
   */
  baseline_entries: Entry[];
  /**
   * Feature sets of the sessions listed in based_on.
   */
  feature_sets: FeatureSet[];
  /**
   * Self-reports listed in based_on.
   */
  self_reports: SelfReport[];
  unresolved: {
    kind: "baseline" | "session" | "self_report";
    /**
     * RFC 4122 UUID, lowercase.
     */
    id: string;
  }[];
}
export interface Entry {
  /**
   * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
   */
  feature_id: string;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id?: string;
  /**
   * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
   */
  gravity_environment: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
  /**
   * Number of sessions that contributed.
   */
  n: number;
  mean: number;
  std: number;
  median: number;
  /**
   * Median absolute deviation.
   */
  mad: number;
  min?: number;
  max?: number;
}
/**
 * Derived metrics computed by the engine (POST /features) from the raw chunks of one session, or aggregated over a rolling window for symptom metrics that do not belong to a session. Each feature_id must exist in the feature catalog. The app stores feature sets next to the raw data and sends them back in /baseline and /assess, so the engine stays stateless.
 */
export interface FeatureSet {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Null for rolling-window features (e.g. 7-day mean pain).
   */
  session_id: string | null;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id?: string;
  /**
   * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
   */
  gravity_environment?: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
  /**
   * Where the data really comes from. device: real hardware, or real input typed by the astronaut. simulator: synthetic. replay: a recording played back. A demo mixing real and simulated data must say so; consumers must never hide it.
   */
  source?: "device" | "simulator" | "replay";
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  computed_at: string;
  engine_version?: string;
  /**
   * Time span of the data the features were computed from.
   */
  window?: {
    /**
     * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
     */
    start: string;
    /**
     * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
     */
    end: string;
  };
  features: {
    /**
     * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
     */
    feature_id: string;
    value: number;
    unit: string;
    /**
     * How many repetitions, samples or days the value summarises.
     */
    n?: number;
    quality: Quality;
  }[];
  /**
   * Features that could not be computed and why. Feeds the completeness of the assessment, so missing data is never read as 'all good'.
   */
  skipped?: {
    /**
     * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
     */
    feature_id: string;
    reason:
      | "no_data"
      | "low_quality"
      | "no_calibration"
      | "calibration_expired"
      | "signal_not_raw"
      | "too_few_repetitions"
      | "stream_missing"
      | "insufficient_sample_rate";
  }[];
}
/**
 * Signal quality for one channel of one chunk (or one feature). score: 0 unusable, 1 perfect. flags say why it is not 1. assessed_by tells who computed it; the engine may recompute and return its own.
 */
export interface Quality {
  /**
   * Number in [0, 1].
   */
  score: number;
  flags?: (
    | "clipping"
    | "lead_off"
    | "motion_artifact"
    | "powerline_noise"
    | "packet_gap"
    | "out_of_calibration"
    | "low_snr"
    | "sensor_warmup"
    | "band_recovering"
    | "saturated_adc"
    | "clock_uncertain"
  )[];
  assessed_by: "device" | "gateway" | "engine";
}
export interface ReportBase {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  report_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  timestamp: string;
  mission_day?: number;
  /**
   * Where the data really comes from. device: real hardware, or real input typed by the astronaut. simulator: synthetic. replay: a recording played back. A demo mixing real and simulated data must say so; consumers must never hide it.
   */
  source: "device" | "simulator" | "replay";
}
export interface PainLocation {
  region: "cervical" | "thoracic" | "thoracolumbar" | "lumbar" | "sacral";
  side?: "left" | "right" | "midline" | "bilateral";
}
/**
 * Response of GET /crew/{astronaut_id}/history; backs the MCP tool get_history (and, with two calls, compare_periods, which the MCP server computes). Feature sets and assessments of a time window. Feature sets are filtered by the start of their session, assessments by as_of. limit caps each collection; truncated says some collection had more, and the most recent items are kept.
 */
export interface History {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  from: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  to: string;
  limit: number;
  truncated: boolean;
  feature_sets: FeatureSet[];
  assessments: Assessment[];
}
/**
 * Response of GET /crew/{astronaut_id}/self-reports: self-reports with timestamp inside the window, oldest first. limit caps items; truncated says there were more, and the most recent items are kept.
 */
export interface SelfReportList {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  from: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  to: string;
  limit: number;
  truncated: boolean;
  items: SelfReport[];
}
/**
 * Response of GET /crew/{astronaut_id}/sessions: session metadata (never raw chunks) with started_at inside the window, oldest first. limit caps items; truncated says there were more, and the most recent items are kept.
 */
export interface SessionList {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  from: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  to: string;
  limit: number;
  truncated: boolean;
  items: Session[];
}
/**
 * One capture session. Every chunk, feature set and calibration belongs to a session. Two types: 'exercise' (standardised protocol such as the standing lat pulldown, with repetitions and a declared load) and 'spine_check' (short passive measurement of spine length, e.g. on waking or before sleeping). Comparing Earth and space only makes sense for sessions with the same protocol_id, so the protocol is part of the comparison key.
 */
export interface Session {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  session_type: "exercise" | "spine_check";
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  /**
   * Where the data really comes from. device: real hardware, or real input typed by the astronaut. simulator: synthetic. replay: a recording played back. A demo mixing real and simulated data must say so; consumers must never hide it.
   */
  source: "device" | "simulator" | "replay";
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  started_at: string;
  /**
   * Null while the session is running.
   */
  ended_at?: string | null;
  end_reason?: "completed" | "aborted_by_user" | "aborted_pain" | "aborted_other";
  mission_context: {
    mission_day?: number;
    phase: "preflight" | "inflight" | "postflight";
    /**
     * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
     */
    gravity_environment: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
  };
  /**
   * Resistance used in an exercise session. There is no force sensor, so the load is declared, not measured: that is why the protocol must be very standardised. Required for exercise sessions.
   */
  load_declared?: {
    type: "elastic_band" | "cable" | "free_weight" | "none";
    nominal_kg?: number;
    /**
     * Colour or serial of the band.
     */
    band_id?: string;
    /**
     * Band stretch at the start position, to standardise tension.
     */
    band_stretch_cm?: number;
    description?: string;
  };
  planned_reps?: number;
  /**
   * Streams enabled for this session, with their placement and calibration.
   *
   * @minItems 1
   */
  streams: [StreamDeclaration, ...StreamDeclaration[]];
  events?: Event[];
  notes?: string;
}
export interface Event {
  event_type:
    | "rep_start"
    | "rep_end"
    | "mvc_start"
    | "mvc_end"
    | "rest_start"
    | "rest_end"
    | "pain_reported"
    | "artifact"
    | "note";
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  timestamp: string;
  /**
   * Device monotonic clock at the event, if the device stamped it.
   */
  device_ms?: number;
  boot_id?: number;
  rep_index?: number;
  note?: string;
}
/**
 * Response of GET /crew/{astronaut_id}/status; backs the MCP tool get_current_status. Everything needed to answer how the astronaut is now in one call. Missing data is null with stale=true in freshness: absence is never reported as a reassuring state.
 */
export interface Status {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  generated_at: string;
  /**
   * Mission context of the current day, from the daily context; null when none is recorded.
   */
  today: {
    /**
     * Calendar date, ISO 8601.
     */
    date: string;
    mission_day?: number;
    phase: "preflight" | "inflight" | "postflight";
    /**
     * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
     */
    gravity_environment: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
  } | null;
  latest_assessment: Assessment | null;
  latest_session: Session | null;
  latest_self_report: SelfReport | null;
  /**
   * Last known status of each device of the astronaut.
   */
  devices: DeviceStatus[];
  /**
   * Baseline the latest assessment is compared against; null when none was built yet.
   */
  active_baseline: {
    /**
     * RFC 4122 UUID, lowercase.
     */
    baseline_id: string;
    version: number;
    /**
     * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
     */
    created_at: string;
  } | null;
  /**
   * How old each piece of data is. stale is decided by the app with its configured thresholds; the contract does not fix them.
   */
  freshness: {
    what: "assessment" | "session" | "self_report" | "device";
    /**
     * Wearable or simulator identifier, unique per physical device.
     */
    device_id?: string;
    /**
     * Timestamp of the latest item; null when there is none.
     */
    last_at: string | null;
    age_hours: number | null;
    stale: boolean;
  }[];
}
/**
 * Health of a wearable as seen by the gateway. Published RETAINED on .../device/{device_id}/status. The gateway also registers it as MQTT Last Will with status=offline so the broker announces a dead gateway.
 */
export interface DeviceStatus {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  timestamp: string;
  /**
   * ok: everything streams. degraded: something works but a stream is stalled/low battery. offline: no data.
   */
  status: "ok" | "degraded" | "offline";
  /**
   * Where the data really comes from. device: real hardware, or real input typed by the astronaut. simulator: synthetic. replay: a recording played back. A demo mixing real and simulated data must say so; consumers must never hide it.
   */
  source: "device" | "simulator" | "replay";
  boot_id?: number;
  uptime_ms?: number;
  firmware_version?: string;
  battery_pct?: number;
  temperature_c?: number;
  link?: {
    type: "ble" | "usb_serial" | "simulated";
    rssi_dbm?: number;
  };
  streams?: {
    /**
     * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
     */
    stream_id: string;
    stream_kind: "emg" | "imu" | "stretch";
    state: "active" | "stalled" | "disabled";
    sample_rate_hz?: number;
    last_seq?: number;
  }[];
}
/**
 * Response of GET /ui/crew/{astronaut_id}/views/baseline: progress of the pre-flight reference sessions and of the baseline built from them, for the Baseline screen of the desktop UI.
 */
export interface BaselineView {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Reference sessions already recorded.
   */
  recorded: number;
  /**
   * Reference sessions the baseline needs.
   */
  required: number;
  /**
   * version of the active baseline; null while none was built.
   */
  reference_version: number | null;
  /**
   * Number of sessions in built_from_session_ids of the active baseline; 0 while none was built.
   */
  built_from_sessions: number;
  /**
   * Reference sessions, recorded and planned, in date order.
   */
  sessions: BaselineViewSession[];
}
export interface BaselineViewSession {
  /**
   * Null for a planned session.
   */
  session_id: string | null;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  status: "complete" | "planned";
  /**
   * Days relative to launch: L-30 is -30.
   */
  offset_days: number;
  /**
   * Calendar date; null when it cannot be derived (no launch date).
   */
  date: string | null;
}
/**
 * Building blocks shared by the UI view schemas (app/ui-*.schema.json). Nothing here is a message by itself. The views carry codes and data, never prose: the desktop UI writes the words. Absence is an explicit null or 'nodata', never a default.
 */
export interface UIViewDefinitions {
  [k: string]: unknown | undefined;
}
/**
 * Response of GET /ui/crew/{astronaut_id}/views/first-day: what is left to set up before the first assessment, for the first-day screen of the desktop UI. Carries no name of the astronaut: the desktop app adds it locally.
 */
export interface FirstDayView {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Tasks still to do, in the order the UI shows them.
   */
  todo: TaskCode[];
  baseline: BaselineView;
}
/**
 * Response of GET /ui/crew/{astronaut_id}/views/history: one row per day of a window, for the History screen of the desktop UI. Days are ordered oldest first. A day without data has level 'nodata' and null values, never a green or a 0 by default.
 */
export interface HistoryView {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Calendar date, ISO 8601.
   */
  from: string;
  /**
   * Calendar date, ISO 8601.
   */
  to: string;
  /**
   * Mission day of today; null without a launch date.
   */
  today_day: number | null;
  /**
   * Own pre-flight range of lumbar length, relative to the baseline mean: lo = min - mean, hi = max - mean of the stretch.lumbar.peak_elongation_mm baseline entry. Null without that entry.
   */
  preflight_range_mm: LengthRange | null;
  days: HistoryDay[];
}
export interface LengthRange {
  lo: number;
  hi: number;
}
export interface HistoryDay {
  /**
   * Calendar date, ISO 8601.
   */
  date: string;
  mission_day: number | null;
  level: DayLevel;
  /**
   * Lumbar elongation of the day minus its baseline mean, in mm; null without data (never 0).
   */
  length_delta_mm: number | null;
  /**
   * pain_nrs of the daily self-report of the day; null without a report.
   */
  pain_nrs: number | null;
  /**
   * Session of the day; null when there was none.
   */
  session: HistoryDaySession | null;
  /**
   * Self-report of the day; null when there was none.
   */
  report: HistoryDayReport | null;
}
export interface HistoryDaySession {
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  state: SessionState;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
}
export interface HistoryDayReport {
  kind: "daily" | "weekly_odi" | "red_flag";
  /**
   * Red flags reported that day; empty when none.
   *
   * Items: Symptoms that suggest nerve involvement or an emergency. Any one of them escalates, regardless of the model.
   */
  red_flags: (
    | "radiating_leg_pain"
    | "numbness_or_tingling"
    | "leg_weakness"
    | "bladder_or_bowel_dysfunction"
    | "saddle_numbness"
    | "severe_night_pain"
    | "fever_with_back_pain"
    | "trauma_event"
  )[];
}
/**
 * Response of GET /ui/sessions/{session_id}/view: the guided session screen of the desktop UI. Protocol facts (band, reps, rest, fit items) come from catalog/protocol-catalog.json; the UI writes the instructions. Live repetitions are not part of this view.
 */
export interface SessionView {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  state: SessionState;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  /**
   * Band of the protocol; null when it uses none.
   */
  band: Band | null;
  /**
   * Repetitions of the protocol; null when it has none.
   */
  total_reps: number | null;
  /**
   * Rest of the protocol; null when it has none.
   */
  rest_seconds: number | null;
  /**
   * Ids of the fit items of the protocol, in order.
   */
  fit_items: FitItemId[];
  /**
   * started_at of the session; null before the gateway started it.
   */
  started_at: string | null;
  /**
   * The five steps of a guided session, always in this order: fit, check, mvc, reps, finish.
   *
   * @minItems 5
   * @maxItems 5
   */
  steps: [SessionStep, SessionStep, SessionStep, SessionStep, SessionStep];
  /**
   * Latest check of each stream of the session.
   */
  sensor_checks: SensorCheck[];
  /**
   * Latest calibrate command of the session; null when none was sent.
   */
  calibration: SessionCalibration | null;
  /**
   * Band stretch at the start position. Reserved: always null for now.
   */
  stretched_cm: number | null;
  /**
   * When the session was planned. Reserved: always null for now.
   */
  planned_at: string | null;
}
/**
 * Elastic band of a protocol, as in catalog/protocol-catalog.json.
 */
export interface Band {
  color: string;
  nominal_kg: number;
}
export interface SessionStep {
  id: "fit" | "check" | "mvc" | "reps" | "finish";
  status: "done" | "now" | "todo";
}
export interface SensorCheck {
  /**
   * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream: string;
  status: "good" | "weak";
  state: "active" | "stalled" | "disabled";
}
export interface SessionCalibration {
  /**
   * RFC 4122 UUID, lowercase.
   */
  command_id: string;
  /**
   * True when the gateway acknowledged the command as completed.
   */
  acked: boolean;
  acked_at: string | null;
}
/**
 * Response of GET /ui/crew/{astronaut_id}/views/strip: facts of the always-visible strip of the desktop UI (mission day, harness, ground link, reports due). Codes and data only; every unknown is an explicit null.
 */
export interface StripView {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  mission: StripMission;
  /**
   * Last known status of the paired harness; null while no harness is paired or its last status is offline. A device whose last status is old is still listed, with stale true.
   */
  harness: StripHarness | null;
  /**
   * State of the link with the ground station. Reserved: always null until the ground link is modelled (issue #62).
   */
  ground_link: null;
  /**
   * Minutes since the last contact with the ground station; null when unknown (always null until issue #62).
   */
  last_contact_min_ago: number | null;
  reports_due: ReportsDue;
}
/**
 * Where the astronaut is in the mission, derived from launch_date and landing_date of the astronaut profile. Each field is null when it cannot be derived.
 */
export interface StripMission {
  /**
   * Mission day counted from the launch date (same numbering as mission_day of the daily context). Null without a launch date.
   */
  day: number | null;
  /**
   * Launch date of the astronaut profile; the UI measures mission elapsed time from it.
   */
  start: string | null;
  phase: ("preflight" | "inflight" | "postflight") | null;
}
export interface StripHarness {
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  /**
   * Null when the device did not report it.
   */
  battery_pct: number | null;
  /**
   * Streams that are not sending (device-status stream state stalled or disabled). Empty when every stream is active.
   */
  weak_streams: WeakStream[];
  /**
   * True when the device status is degraded.
   */
  degraded: boolean;
  /**
   * True when the last device status is older than the app's staleness threshold (settings stale_device_hours): the other fields are then the last known ones, not the current state.
   */
  stale: boolean;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  last_seen_at: string;
}
export interface WeakStream {
  /**
   * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream: string;
  state: "stalled" | "disabled";
}
/**
 * Self-reports still due today.
 */
export interface ReportsDue {
  daily: boolean;
  weekly_odi: boolean;
}
export interface TodayReason {
  /**
   * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
   */
  feature_id: string;
  deviation_z: number | null;
  contribution: number | null;
}
export interface TrustFacts {
  /**
   * Number in [0, 1].
   */
  confidence: number;
  /**
   * Expected inputs that were available.
   */
  data_available: number;
  /**
   * Expected inputs; data_available is never above it.
   */
  data_total: number;
  /**
   * completeness.missing of the assessment.
   */
  missing: TrustMissing[];
}
export interface TrustMissing {
  /**
   * A feature_id, a domain name, or one of: self_report, daily_context, baseline.
   */
  what: string;
  reason: "no_data" | "low_quality" | "no_baseline" | "stale" | "sensor_offline" | "signal_not_raw";
}
export interface SessionInProgress {
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  started_at: string;
  /**
   * Planned repetitions; null for protocols without them.
   */
  total_reps: number | null;
}
/**
 * Shared building blocks for every Asclepius Orbit contract. Nothing here is a message by itself: other schemas $ref these definitions. Canonical units are fixed per channel type and never converted on reception: g (standard gravity), deg/s, mV, ohm, mm, cm, kg, Hz, ms, s.
 */
export interface CommonDefinitions {
  [k: string]: unknown | undefined;
}
/**
 * Slow-changing facts about an astronaut, filled in by the flight surgeon or the app before the mission and updated in flight (weight above all). The engine uses them to select reference ranges and to adjust risk; they are personal data and never travel over the MQTT bus. Age is derived from date_of_birth, never stored.
 */
export interface AstronautProfile {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  updated_at: string;
  /**
   * Calendar date, ISO 8601.
   */
  date_of_birth: string;
  /**
   * Used for physiology (muscle mass, EMG amplitude and spine reference ranges). The engine reads only this field.
   */
  sex_at_birth: "female" | "male" | "intersex" | "undisclosed";
  /**
   * Optional, free text, only for how the UI addresses the person. Never used by the engine.
   */
  gender_identity?: string;
  /**
   * Launch date of the current mission; the mission day and phase are derived from it. Null or absent when not planned yet.
   */
  launch_date?: string | null;
  /**
   * Landing date of the current mission; postflight starts on it. Null or absent while not known.
   */
  landing_date?: string | null;
  anthropometry: {
    height_standing_cm: number;
    /**
     * Sitting height: proxy of trunk and spine length.
     */
    height_sitting_cm?: number;
    body_mass_kg: number;
    /**
     * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
     */
    body_mass_measured_at?: string;
    /**
     * Subcutaneous fat thickness over the EMG electrode sites. Thicker fat attenuates the EMG amplitude.
     */
    skinfold_mm?: {
      /**
       * Muscle under an EMG electrode pair.
       */
      muscle:
        | "latissimus_dorsi"
        | "trapezius"
        | "rhomboids"
        | "erector_spinae"
        | "multifidus"
        | "biceps_brachii"
        | "rectus_abdominis";
      side: "left" | "right" | "midline";
      thickness_mm: number;
    }[];
  };
  /**
   * Pre-flight spine history. Which of these factors weigh in the risk model, and how much, is decided by the clinical model (Aguado), not by this contract.
   */
  spine_history?: {
    low_back_pain_history?: boolean;
    disc_herniation?: {
      has_history: boolean;
      /**
       * Items: Vertebral level, lowercase so it can be embedded in ids (imu.l3.*).
       */
      levels?: (
        | "c7"
        | "t1"
        | "t2"
        | "t3"
        | "t4"
        | "t5"
        | "t6"
        | "t7"
        | "t8"
        | "t9"
        | "t10"
        | "t11"
        | "t12"
        | "l1"
        | "l2"
        | "l3"
        | "l4"
        | "l5"
        | "s1"
      )[];
      /**
       * Calendar date, ISO 8601.
       */
      date?: string;
    };
    spine_surgery?: {
      has_history: boolean;
      description?: string;
      /**
       * Calendar date, ISO 8601.
       */
      date?: string;
    };
    scoliosis?: {
      present: boolean;
      cobb_angle_deg?: number;
    };
    degenerative_discs_on_preflight_mri?: {
      present: boolean;
      /**
       * Items: Vertebral level, lowercase so it can be embedded in ids (imu.l3.*).
       */
      levels?: (
        | "c7"
        | "t1"
        | "t2"
        | "t3"
        | "t4"
        | "t5"
        | "t6"
        | "t7"
        | "t8"
        | "t9"
        | "t10"
        | "t11"
        | "t12"
        | "l1"
        | "l2"
        | "l3"
        | "l4"
        | "l5"
        | "s1"
      )[];
    };
    notes?: string;
  };
  space_history?: {
    previous_missions: number;
    cumulative_microgravity_days?: number;
    /**
     * Calendar date, ISO 8601.
     */
    last_mission_end_date?: string;
  };
  fitness?: {
    dominant_side?: "left" | "right" | "ambidextrous";
    training_level?: "low" | "moderate" | "high" | "elite";
    /**
     * Reference Earth load of the standard lat pulldown (e.g. 1RM or working load). Muscle MVC references live in emg_mvc calibrations.
     */
    lat_pulldown_reference_load_kg?: number;
  };
}
/**
 * Personal reference of an astronaut, built by the engine (POST /baseline) from reference sessions, normally pre-flight in 1 g. The app persists it and sends it with every /assess. An entry is keyed by feature + protocol + gravity environment: the same exercise in 1 g and in microgravity are different populations and are never mixed in one entry. A session is compared against the entry of its own gravity environment when there is one (drift during the mission) and against the earth_1g entry to measure the effect of the environment itself (the Earth vs microgravity experiment). Robust statistics (median, MAD) sit next to mean and std because sessions have outliers.
 */
export interface Baseline {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  baseline_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Increments every time the baseline is rebuilt.
   */
  version: number;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  created_at: string;
  /**
   * @minItems 1
   *
   * Items: RFC 4122 UUID, lowercase.
   */
  built_from_session_ids: [string, ...string[]];
  entries: Entry[];
}
export interface CalibrationBase {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  calibration_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  /**
   * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream_id: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id?: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  created_at: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  valid_until?: string;
  /**
   * Where the data really comes from. device: real hardware, or real input typed by the astronaut. simulator: synthetic. replay: a recording played back. A demo mixing real and simulated data must say so; consumers must never hide it.
   */
  source: "device" | "simulator" | "replay";
  quality?: Quality;
}
export interface Xyz {
  x: number;
  y: number;
  z: number;
}
/**
 * What explains the signals on a given day: where in the mission the astronaut is, the gravity environment, what medication was taken and which exercise or countermeasures were done. Filled from the mission plan or manually. Medication matters because analgesics mask pain and some drugs alter muscle signals.
 */
export interface DailyContext {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Calendar date, ISO 8601.
   */
  date: string;
  mission_day?: number;
  phase: "preflight" | "inflight" | "postflight";
  /**
   * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
   */
  gravity_environment: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
  medication?: {
    name: string;
    category: "analgesic" | "anti_inflammatory" | "muscle_relaxant" | "sleep_aid" | "other";
    dose_mg?: number;
    /**
     * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
     */
    taken_at: string;
  }[];
  /**
   * General exercise of the day (not the standardised protocol session, which is a session).
   */
  exercise?: {
    modality: "ared" | "cevis" | "t2" | "other";
    duration_min: number;
    load_kg?: number;
    /**
     * Numeric Rating Scale, integer 0 (none) to 10 (worst imaginable).
     */
    rpe?: number;
  }[];
  countermeasures?: {
    type: "axial_loading_suit" | "stretching" | "other";
    duration_min: number;
  }[];
}
/**
 * POST /assess. Everything the engine needs to judge the current state, in one stateless call. Assessment out. feature_sets holds the recent history too (not just today) because trend projection and rolling-window metrics need it; the app decides how many days to send.
 */
export interface AssessRequest {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  as_of: string;
  mission_day?: number;
  profile: AstronautProfile;
  baseline: Baseline;
  /**
   * @minItems 1
   */
  feature_sets: [FeatureSet, ...FeatureSet[]];
  self_reports?: SelfReport[];
  daily_context?: DailyContext[];
}
/**
 * POST /baseline. Feature sets of the reference sessions (normally pre-flight, 1 g) in, Baseline out. The engine groups by feature + protocol + gravity environment. The app stores the returned baseline and sends it with every /assess; the engine keeps nothing.
 */
export interface BaselineRequest {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * @minItems 1
   */
  feature_sets: [FeatureSet, ...FeatureSet[]];
  self_reports?: SelfReport[];
  /**
   * Version of the baseline being replaced; the new one gets version + 1. Absent for the first baseline.
   */
  previous_baseline_version?: number;
}
/**
 * Error body of every engine endpoint, shaped after RFC 9457 problem details.
 */
export interface ErrorResponse {
  type:
    | "invalid_request"
    | "unknown_feature"
    | "missing_calibration"
    | "insufficient_data"
    | "unsupported_schema_version"
    | "internal";
  title: string;
  status: number;
  detail?: string;
  /**
   * Field-level problems, as JSON Pointer + message.
   */
  errors?: {
    pointer: string;
    message: string;
  }[];
}
/**
 * POST /features. Raw data of ONE session in, FeatureSet out. The engine reads the stream declarations in 'session' (placement, rate) and the calibrations to turn raw chunks into derived metrics (EMG RMS and %MVC, median frequency, IMU range of motion, cord elongation...). Stateless: the app sends everything the computation needs.
 */
export interface FeaturesRequest {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  session: Session;
  /**
   * All chunks of the session, any stream, in any order (the engine sorts by stream, boot_id and seq).
   *
   * @minItems 1
   */
  chunks: [StreamChunk, ...StreamChunk[]];
  /**
   * Every calibration referenced by session.streams[].calibration_id or by a chunk.
   */
  calibrations?: Calibration[];
  /**
   * Clock anchors of the session, to map device time to UTC.
   */
  clock_syncs?: ClockSync[];
  profile?: AstronautProfile;
}
export interface ChunkBase {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  /**
   * Identifier of one sensor stream inside a device. Declared in the session, repeated in every chunk and in the MQTT topic.
   */
  stream_id: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  session_id: string;
  /**
   * Where the data really comes from. device: real hardware, or real input typed by the astronaut. simulator: synthetic. replay: a recording played back. A demo mixing real and simulated data must say so; consumers must never hide it.
   */
  source: "device" | "simulator" | "replay";
  /**
   * Random 32-bit value chosen by the device at every boot. seq and t0_device_ms only make sense inside one boot_id.
   */
  boot_id: number;
  /**
   * Chunk counter per stream and boot_id, starting at 0. A jump means lost chunks.
   */
  seq: number;
  /**
   * Device monotonic clock (ms since boot) at the first sample of the chunk.
   */
  t0_device_ms: number;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  received_at: string;
  sample_rate_hz: number;
  n_samples: number;
  /**
   * Samples the device knows it lost inside this chunk (buffer overrun).
   */
  dropped_samples?: number;
}
/**
 * Anchor between the device monotonic clock and UTC, published by the gateway on .../device/{device_id}/clock at session start and then periodically (suggested every 60 s). The ESP32 has no real-time clock, so t0_device_ms of every chunk is mapped to UTC with the latest anchor of the same boot_id. Drift between anchors is the clock error.
 */
export interface ClockSync {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  boot_id: number;
  /**
   * Device monotonic clock reading (ms since boot) at the anchor.
   */
  device_ms: number;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  utc: string;
  /**
   * Estimated error of the anchor (half round-trip time of the exchange). BLE links are usually tens of ms.
   */
  uncertainty_ms: number;
  method: "round_trip" | "gateway_receive_time" | "simulated";
}
/**
 * POST /simulate. A what-if: take a normal assess request, change some features (or none: an empty overrides array projects the base trajectory as it is), and see how the assessment would move. Read-only: nothing is stored and no real assessment is produced. Backs the MCP tool simulate_scenario.
 */
export interface SimulateRequest {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  base: AssessRequest;
  /**
   * Feature changes to apply. Required, but may be empty: an empty array means no changes, so the base trajectory is projected as it is.
   */
  overrides: {
    /**
     * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
     */
    feature_id: string;
    /**
     * set: replace the value. add: add value to it. scale: multiply it by value.
     */
    mode: "set" | "add" | "scale";
    value: number;
  }[];
  /**
   * Optional horizon for the trend of the scenario.
   */
  project_days?: number;
}
/**
 * Result of POST /simulate: the assessment of the unchanged input next to the assessment of the scenario, so the difference is explicit. Both are hypothetical and must be shown as such.
 */
export interface SimulateResponse {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  reference: Assessment;
  scenario: Assessment;
  hypothetical: true;
}
/**
 * Output of the MCP tool compare_periods. The MCP server calls GET /crew/{astronaut_id}/history twice (period A, period B) and computes this comparison; there is no HTTP endpoint for it. Every delta is B minus A (positive means B is higher than A; whether higher is better or worse depends on the feature). Absence is never good news: a missing value is null, never omitted and never 0. Check warnings before interpreting the numbers.
 */
export interface ComparePeriods {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  period_a: Period;
  period_b: Period;
  /**
   * Features present in BOTH periods, one entry per (feature_id, protocol_id, gravity_environment). Only the same protocol and the same gravity are comparable, so they are part of the key.
   */
  features: FeatureComparison[];
  /**
   * Keys with data in period A only: the feature disappeared or was not measured in period B. Not a comparison.
   */
  only_in_a: FeatureKey[];
  /**
   * Keys with data in period B only: the feature is new or was not measured in period A. Not a comparison.
   */
  only_in_b: FeatureKey[];
  /**
   * Assessment levels per period.
   */
  levels: {
    a: LevelCounts;
    b: LevelCounts;
  };
  /**
   * Mean of each assessment score per period, on [0, 1], and delta = b - a. A value is null when the period has no assessments (and delta is null if either side is null).
   */
  scores: {
    risk_score: ScoreComparison;
    confidence: ScoreComparison;
    completeness: ScoreComparison;
  };
  /**
   * Reasons the comparison may be incomplete or misleading. period_a_empty / period_b_empty: the period has no data. period_a_truncated / period_b_truncated: the history had more items than the limit and only the most recent were used, so the period is partly missing. unit_mismatch: the same key had different units in the two periods and was left out of features.
   */
  warnings: ("period_a_empty" | "period_b_empty" | "period_a_truncated" | "period_b_truncated" | "unit_mismatch")[];
}
/**
 * One compared time window and how much data it had.
 */
export interface Period {
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  from: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  to: string;
  /**
   * Assessments found in the window.
   */
  assessments_count: number;
  /**
   * Feature sets found in the window.
   */
  feature_sets_count: number;
  /**
   * True if the history was cut at its limit and only the most recent items were kept.
   */
  truncated: boolean;
}
/**
 * One feature measured in both periods. delta = b - a, in the feature unit.
 */
export interface FeatureComparison {
  /**
   * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
   */
  feature_id: string;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  /**
   * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
   */
  gravity_environment: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
  /**
   * Unit of every number of this entry (e.g. %, deg, mm, Hz).
   */
  unit: string;
  a: FeatureStats;
  b: FeatureStats;
  /**
   * b.mean - a.mean, in the feature unit.
   */
  delta_mean: number;
  /**
   * b.median - a.median, in the feature unit.
   */
  delta_median: number;
}
/**
 * Statistics of one feature in one period, in the unit of the feature.
 */
export interface FeatureStats {
  /**
   * Number of feature values aggregated.
   */
  n: number;
  /**
   * Mean of the values, in the feature unit.
   */
  mean: number;
  /**
   * Median of the values, in the feature unit.
   */
  median: number;
}
/**
 * Comparison key of a feature: values are comparable only with the same feature, protocol and gravity.
 */
export interface FeatureKey {
  /**
   * Dotted identifier of a derived metric. Must exist in the feature catalog (catalog/feature-catalog.json), after expanding its placeholders.
   */
  feature_id: string;
  /**
   * Standardised exercise or measurement protocol. The protocol is part of the comparison key: only data from the same protocol is comparable.
   */
  protocol_id: string;
  /**
   * Gravity the astronaut is exposed to while the data is recorded. Mandatory context: in microgravity an accelerometer does not see gravity, so Earth and space IMU data are not directly comparable.
   */
  gravity_environment: "earth_1g" | "microgravity" | "moon_0_16g" | "mars_0_38g";
}
/**
 * Number of assessments per level in one period.
 */
export interface LevelCounts {
  green: number;
  amber: number;
  red: number;
  /**
   * Level of the most recent assessment of the period, or null when the period has no assessments.
   */
  latest: ("green" | "amber" | "red") | null;
}
/**
 * Mean of a score in each period and delta = b - a. null when a period has no assessments.
 */
export interface ScoreComparison {
  a: number | null;
  b: number | null;
  /**
   * b - a, in [-1, 1]; null if a or b is null.
   */
  delta: number | null;
}
/**
 * Gateway answer to a command, published QoS 1 on .../device/{device_id}/ack. A command normally gets 'accepted' first and 'completed' (or 'failed') later; rejected is final.
 */
export interface CommandAck {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  command_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  timestamp: string;
  status: "accepted" | "rejected" | "completed" | "failed";
  error?: {
    code:
      | "busy"
      | "unknown_stream"
      | "no_session"
      | "device_offline"
      | "calibration_failed"
      | "invalid_command"
      | "internal";
    message?: string;
  };
  /**
   * Present on completed calibrations.
   */
  result?: {
    /**
     * RFC 4122 UUID, lowercase.
     */
    calibration_id?: string;
  };
}
export interface CommandBase {
  /**
   * Semantic version of the contract the message was produced against. Major must be 1 for this schema set.
   */
  schema_version: string;
  /**
   * RFC 4122 UUID, lowercase.
   */
  command_id: string;
  /**
   * Pseudonymous crew identifier. Never a real name: this id travels over the MQTT bus.
   */
  astronaut_id: string;
  /**
   * Wearable or simulator identifier, unique per physical device.
   */
  device_id: string;
  /**
   * RFC 3339 date-time. The UTC offset is mandatory, so naive timestamps are invalid; validators must have format checking enabled for date-time.
   */
  issued_at: string;
}
