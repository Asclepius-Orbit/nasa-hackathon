import { useState } from 'react';

import { cx } from '../../components/ui/cx';
import { useSettings } from '../../settings/SettingsProvider';
import { cervicalInfo, coccyxInfo, lumbarInfo, sacrumInfo, thoracicInfo, vertebraLabel, type VertebraInfo } from '../../strings/spine';
import { spine } from '../../strings/today';
import styles from './SpineFigure.module.css';

/** Preflight lumbar vertebra spacing in figure units; today's spacing grows 1 unit per mm of lumbar length. */
const PREFLIGHT_SPACING = 31;
const L1_Y = 306;
const MIN_MM = -6;
const MAX_MM = 5;
/** The cord starts at C3-C4 and ends at L5-S1, just below L5. */
const CORD_TOP_Y = -121;
const CORD_X_TOP = 166;
/** Figure units above y = 0 that the viewBox adds for the cervical spine. */
const TOP_PAD = 190;
const T9_Y = 190;
const IMU_X = 190;
const IMU_SIZE = 18;
const GND_X = 214;
const LUMBAR_X = [174, 165, 158, 155, 156] as const;
/** Cervical vertebrae C1 to C7 (top to bottom): [cx, cy, rx, ry]. Smaller than the thoracic ones, with the forward (left) cervical curve. */
const CERVICAL = [
  [143, -170, 9, 6],
  [140, -151, 9, 6],
  [138, -131, 10, 6],
  [138, -111, 10, 6],
  [140, -91, 10, 7],
  [144, -71, 11, 7],
  [148, -50, 11, 7],
] as const;
/** Thoracic vertebrae T1 to T12 (top to bottom): [cx, cy, rx, ry]. The IMU sits on T9, the ninth. */
const THORACIC = [
  [150, -26, 12, 7],
  [148, 0, 12, 8],
  [146, 26, 13, 8],
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

const TOOLTIP_ID = 'spine-vertebra-tip';
const VIEW_W = 340;
const VIEW_H = 560 + TOP_PAD;
const SACRUM_ANCHOR = { left: 148, y: 500 };
const COCCYX = { cx: 173, cy: 550, rx: 5, ry: 7 };

interface Active {
  info: VertebraInfo;
  /** Figure units: the vertebra's left edge and centre height, where the tooltip anchors. */
  left: number;
  y: number;
}

const n = (value: number): string => String(Math.round(value));

/** Side view of the spine, drawn from the lumbar length: the lumbar vertebrae and the stretch cord stretch with it. */
/** `lumbarLengthMm` null = no measurement: the column is drawn at the preflight spacing and the length reads No data. */
export function SpineFigure({ lumbarLengthMm, measuredNote = null }: { lumbarLengthMm: number | null; measuredNote?: string | null }): React.JSX.Element {
  const { formatLength } = useSettings();
  const length = lumbarLengthMm === null ? spine.noLength : formatLength(lumbarLengthMm);
  const y = lumbarCentres(lumbarLengthMm ?? 0);
  const [, , l3, l4, l5] = y as [number, number, number, number, number];
  const [active, setActive] = useState<Active | null>(null);
  const interactive = (info: VertebraInfo, left: number, cy: number, index: number): Record<string, unknown> => {
    const show = (): void => { setActive({ info, left, y: cy }); };
    const hide = (): void => { setActive((a) => (a?.info.name === info.name ? null : a)); };
    return {
      tabIndex: 0,
      'aria-label': vertebraLabel(info),
      'aria-describedby': active?.info.name === info.name ? TOOLTIP_ID : undefined,
      onMouseEnter: show,
      onMouseLeave: hide,
      onFocus: show,
      onBlur: hide,
      onKeyDown: (e: React.KeyboardEvent) => { if (e.key === 'Escape') setActive(null); },
      style: { '--i': index },
    };
  };
  const cordTop = CORD_TOP_Y;
  const cordBottom = l5 + 8;
  const cordY = (f: number): number => cordTop + (cordBottom - cordTop) * f;
  return (
    <figure className={styles.figure}>
      <svg viewBox={`0 ${String(-TOP_PAD)} ${String(VIEW_W)} ${String(VIEW_H)}`} role="img" aria-label={spine.label(length)}>
        {lumbarLengthMm !== null && (
          <path
            className={styles.today}
          d={`M143 -170 C 137 -120, 140 -70, 148 -30 S 152 18, 150 30 C 141 72, 141 104, 151 136 S 188 248, 177 300 S 150 ${n(l3 + 15)}, 156 ${n(l5 + 3)} S 168 500, 172 534`}
          />
        )}
        <g className={styles.cervical}>
          {CERVICAL.map(([cx, cy, rx, ry], i) => (
            <ellipse key={cy} className={styles.vertebra} data-vertebra={`C${String(i + 1)}`} cx={cx} cy={cy} rx={rx} ry={ry} {...interactive(cervicalInfo(i + 1), cx - rx, cy, i)} />
          ))}
        </g>
        <g className={styles.thoracic}>
          {THORACIC.map(([cx, cy, rx, ry], i) => (
            <ellipse key={cy} className={styles.vertebra} data-vertebra={`T${String(i + 1)}`} cx={cx} cy={cy} rx={rx} ry={ry} {...interactive(thoracicInfo(i + 1), cx - rx, cy, CERVICAL.length + i)} />
          ))}
        </g>
        <g className={styles.lumbar}>
          {y.map((cy, i) => (
            <ellipse
              key={LUMBAR_X[i]}
              className={styles.vertebra}
              data-vertebra={`L${String(i + 1)}`}
              cx={LUMBAR_X[i]}
              cy={cy}
              rx={i === 0 ? 20 : 21}
              ry="12"
              {...interactive(lumbarInfo(i + 1), (LUMBAR_X[i] ?? 0) - 21, cy, CERVICAL.length + THORACIC.length + i)}
            />
          ))}
        </g>
        <path className={cx(styles.sacrum, styles.vertebra)} data-vertebra="Sacrum" d="M158 466 C 165 490, 170 510, 172 534" {...interactive(sacrumInfo, SACRUM_ANCHOR.left, SACRUM_ANCHOR.y, CERVICAL.length + THORACIC.length + 5)} />
        <ellipse className={cx(styles.coccyx, styles.vertebra)} data-vertebra="Coccyx" cx={COCCYX.cx} cy={COCCYX.cy} rx={COCCYX.rx} ry={COCCYX.ry} {...interactive(coccyxInfo, COCCYX.cx - COCCYX.rx, COCCYX.cy, CERVICAL.length + THORACIC.length + 6)} />
        <path className={styles.preflight} d="M143 -170 C 137 -120, 140 -70, 148 -30 S 152 30, 150 44 C 140 84, 140 112, 150 142 S 190 250, 178 296 S 118 372, 132 424 S 164 484, 172 514" />
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
          <path d="M128 -91 H 84" />
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
          <text x="16" y="-88" className={styles.muted}>
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
      {active !== null && (
        <div
          id={TOOLTIP_ID}
          role="tooltip"
          className={styles.tip}
          style={{
            left: `${String((active.left / VIEW_W) * 100)}%`,
            top: `${String(((active.y + TOP_PAD) / VIEW_H) * 100)}%`,
            maxWidth: `calc(${String((active.left / VIEW_W) * 100)}% - 4px)`,
          }}
        >
          <strong>{active.info.name}</strong>
          <span>{active.info.text}</span>
        </div>
      )}
      <figcaption>
        {spine.caption}
        {measuredNote !== null && <span className={styles.measured}>{measuredNote}</span>}
      </figcaption>
    </figure>
  );
}
