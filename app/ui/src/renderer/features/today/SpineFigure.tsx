import { useSettings } from '../../settings/SettingsProvider';
import { spine } from '../../strings/today';
import styles from './SpineFigure.module.css';

/** Preflight lumbar vertebra spacing in figure units; today's spacing grows 1 unit per mm of lumbar length. */
const PREFLIGHT_SPACING = 31;
const L1_Y = 306;
const MIN_MM = -6;
const MAX_MM = 5;
/** The cord starts at C3-C4, above the first drawn thoracic vertebra, and ends at L5-S1, just below L5. */
const CORD_TOP_Y = 38;
const CORD_X_TOP = 172;
const T9_Y = 190;
const IMU_X = 190;
const IMU_SIZE = 18;
const GND_X = 214;
const LUMBAR_X = [174, 165, 158, 155, 156] as const;
/** Thoracic vertebrae T4 to T12 (top to bottom): [cx, cy, rx, ry]. The IMU sits on T9, the sixth. */
const THORACIC = [
  [146, 52, 13, 8],
  [143, 78, 13, 8],
  [143, 104, 14, 8],
  [150, 134, 15, 9],
  [160, 162, 16, 9],
  [170, 190, 16, 9],
  [177, 218, 17, 10],
  [181, 246, 17, 10],
  [180, 274, 18, 10],
] as const;

/** Vertical centres of the drawn lumbar vertebrae (L1 to L5) for a lumbar length change, clamped so the column never leaves the figure. */
export function lumbarCentres(lumbarLengthMm: number): number[] {
  const mm = Math.min(MAX_MM, Math.max(MIN_MM, lumbarLengthMm));
  return LUMBAR_X.map((_, i) => L1_Y + i * (PREFLIGHT_SPACING + mm));
}

const n = (value: number): string => String(Math.round(value));

/** Side view of the spine, drawn from the lumbar length: the lumbar vertebrae and the stretch cord stretch with it. */
/** `lumbarLengthMm` null = no measurement: the column is drawn at the preflight spacing and the length reads No data. */
export function SpineFigure({ lumbarLengthMm, measuredNote = null }: { lumbarLengthMm: number | null; measuredNote?: string | null }): React.JSX.Element {
  const { formatLength } = useSettings();
  const length = lumbarLengthMm === null ? spine.noLength : formatLength(lumbarLengthMm);
  const y = lumbarCentres(lumbarLengthMm ?? 0);
  const [, , l3, l4, l5] = y as [number, number, number, number, number];
  const cordTop = CORD_TOP_Y;
  const cordBottom = l5 + 8;
  const cordY = (f: number): number => cordTop + (cordBottom - cordTop) * f;
  return (
    <figure className={styles.figure}>
      <svg viewBox="0 0 340 560" role="img" aria-label={spine.label(length)}>
        {lumbarLengthMm !== null && (
          <path
            className={styles.today}
          d={`M150 30 C 141 72, 141 104, 151 136 S 188 248, 177 300 S 150 ${n(l3 + 15)}, 156 ${n(l5 + 3)} S 168 500, 172 534`}
          />
        )}
        <g className={styles.thoracic}>
          {THORACIC.map(([cx, cy, rx, ry]) => (
            <ellipse key={cy} cx={cx} cy={cy} rx={rx} ry={ry} />
          ))}
        </g>
        <g className={styles.lumbar}>
          {y.map((cy, i) => (
            <ellipse key={LUMBAR_X[i]} data-vertebra={`L${String(i + 1)}`} cx={LUMBAR_X[i]} cy={cy} rx={i === 0 ? 20 : 21} ry="12" />
          ))}
        </g>
        <path className={styles.sacrum} d="M158 466 C 165 490, 170 510, 172 534" />
        <path className={styles.preflight} d="M150 44 C 140 84, 140 112, 150 142 S 190 250, 178 296 S 118 372, 132 424 S 164 484, 172 514" />
        <path
          className={styles.cord}
          data-sensor="cord"
          d={`M${String(CORD_X_TOP)} ${n(cordTop)} C 166 ${n(cordY(0.2))}, 172 ${n(cordY(0.32))}, 190 ${n(cordY(0.42))} S 214 ${n(cordY(0.62))}, 206 ${n(cordY(0.76))} S 188 ${n(l3 + 30)}, 190 ${n(cordBottom)}`}
        />
        <rect className={styles.imu} data-sensor="imu" x={IMU_X} y={T9_Y - IMU_SIZE / 2} width={IMU_SIZE} height={IMU_SIZE} rx="3" />
        <circle className={styles.emg} cx="214" cy="270" r="9" />
        <circle className={styles.gnd} data-sensor="gnd" cx={GND_X} cy={n(l4 + 6)} r="6" />
        <g className={styles.leader}>
          <path d="M223 270 H 250" />
          <path d={`M${String(GND_X + 6)} ${n(l4 + 6)} H 250`} />
          <path d={`M176 ${n(cordY(0.04))} L 250 ${n(cordTop + 18)}`} />
          <path d={`M${String(IMU_X + IMU_SIZE)} ${String(T9_Y)} H 250`} />
          <path d="M134 52 H 84" />
          <path d={`M132 ${n(l4)} H 92`} />
        </g>
        <g className={styles.labels}>
          <text x="254" y="266" className={styles.strong}>
            {spine.emg}
          </text>
          <text x="254" y="282" className={styles.muted}>
            {spine.emgWhere1}
          </text>
          <text x="254" y="296" className={styles.muted}>
            {spine.emgWhere2}
          </text>
          <text x="254" y={n(l4 + 2)} className={styles.strong}>
            {spine.gnd}
          </text>
          <text x="254" y={n(l4 + 18)} className={styles.muted}>
            {spine.gndWhere}
          </text>
          <text x="254" y={n(cordTop + 14)} className={styles.strong}>
            {spine.cord}
          </text>
          <text x="254" y={n(cordTop + 30)} className={styles.muted}>
            {spine.cordWhere}
          </text>
          <text x="254" y={n(cordTop + 46)} className={styles.value}>
            {length}
          </text>
          <text x="254" y={String(T9_Y - 4)} className={styles.strong}>
            {spine.imu}
          </text>
          <text x="254" y={String(T9_Y + 12)} className={styles.muted}>
            {spine.imuWhere}
          </text>
          <text x="16" y="56" className={styles.muted}>
            {spine.cervical}
          </text>
          <text x="16" y={n(l4 - 4)} className={styles.muted}>
            {spine.lumbar}
          </text>
        </g>
        <g className={styles.legend}>
          <line x1="18" y1="526" x2="44" y2="526" className={styles.preflight} />
          <text x="50" y="531">
            {spine.preflight}
          </text>
          {lumbarLengthMm !== null && (
            <>
              <line x1="140" y1="526" x2="166" y2="526" className={styles.today} />
              <text x="172" y="531">
                {spine.today}
              </text>
            </>
          )}
        </g>
      </svg>
      <figcaption>
        {spine.caption}
        {measuredNote !== null && <span className={styles.measured}>{measuredNote}</span>}
      </figcaption>
    </figure>
  );
}
