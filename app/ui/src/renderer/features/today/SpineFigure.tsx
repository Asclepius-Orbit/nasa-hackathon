import { useSettings } from '../../settings/SettingsProvider';
import { spine } from '../../strings/today';
import styles from './SpineFigure.module.css';

/** Preflight lumbar vertebra spacing in figure units; today's spacing grows 1 unit per mm of lumbar length. */
const PREFLIGHT_SPACING = 31;
const L1_Y = 306;
const MIN_MM = -6;
const MAX_MM = 5;
const LUMBAR_X = [174, 165, 158, 155, 156] as const;
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

/** Vertical centres of L1 to L5 for a lumbar length change, clamped so the column never leaves the figure. */
export function lumbarCentres(lumbarLengthMm: number): number[] {
  const mm = Math.min(MAX_MM, Math.max(MIN_MM, lumbarLengthMm));
  return LUMBAR_X.map((_, i) => L1_Y + i * (PREFLIGHT_SPACING + mm));
}

const n = (value: number): string => String(Math.round(value));

/** Side view of the spine, drawn from the lumbar length: the lumbar vertebrae and the stretch cord stretch with it. */
export function SpineFigure({ lumbarLengthMm }: { lumbarLengthMm: number }): React.JSX.Element {
  const { formatLength } = useSettings();
  const length = formatLength(lumbarLengthMm);
  const y = lumbarCentres(lumbarLengthMm);
  const [l1, , l3, l4, l5] = y as [number, number, number, number, number];
  const cordTop = l1 - 6;
  const cordBottom = l5 + 5;
  const cordY = (f: number): number => cordTop + (cordBottom - cordTop) * f;
  return (
    <figure className={styles.figure}>
      <svg viewBox="0 0 340 560" role="img" aria-label={spine.label(length)}>
        <path
          className={styles.today}
          d={`M150 30 C 141 72, 141 104, 151 136 S 188 248, 177 300 S 150 ${n(l3 + 15)}, 156 ${n(l5 + 3)} S 168 500, 172 534`}
        />
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
        <path className={styles.cord} d={`M206 ${n(cordTop)} C 196 ${n(cordY(0.32))}, 186 ${n(cordY(0.65))}, 188 ${n(cordBottom)}`} />
        <rect className={styles.imu} x="176" y={n(l3 - 9)} width="18" height="18" rx="3" />
        <circle className={styles.emg} cx="214" cy="232" r="9" />
        <g className={styles.leader}>
          <path d="M223 232 H 250" />
          <path d={`M200 ${n(cordY(0.2))} L 250 ${n(cordTop + 18)}`} />
          <path d={`M194 ${n(l3)} H 250`} />
          <path d="M134 52 H 84" />
          <path d={`M132 ${n(l4)} H 92`} />
        </g>
        <g className={styles.labels}>
          <text x="254" y="228" className={styles.strong}>
            {spine.emg}
          </text>
          <text x="254" y="244" className={styles.muted}>
            {spine.emgWhere1}
          </text>
          <text x="254" y="258" className={styles.muted}>
            {spine.emgWhere2}
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
          <text x="254" y={n(l3 - 4)} className={styles.strong}>
            {spine.imu}
          </text>
          <text x="254" y={n(l3 + 12)} className={styles.muted}>
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
          <line x1="140" y1="526" x2="166" y2="526" className={styles.today} />
          <text x="172" y="531">
            {spine.today}
          </text>
        </g>
      </svg>
      <figcaption>{spine.caption}</figcaption>
    </figure>
  );
}
