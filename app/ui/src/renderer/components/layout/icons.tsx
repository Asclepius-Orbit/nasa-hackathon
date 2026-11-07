/** Inline SVG icons copied from the mock. Decorative: the label next to each one carries the meaning. */

const base = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, 'aria-hidden': true } as const;

export function TodayIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l3 2" />
    </svg>
  );
}

export function ReportIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </svg>
  );
}

export function SessionIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <path d="M4 12h3l2-6 4 12 2-6h5" />
    </svg>
  );
}

export function HistoryIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <path d="M4 19V5M4 19h16M8 15l4-4 3 3 5-6" />
    </svg>
  );
}

export function SettingsIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
    </svg>
  );
}

export function BatteryIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <rect x="3" y="7" width="16" height="10" rx="2" />
      <path d="M21 10v4" />
      <path d="M6 10h6v4H6z" fill="currentColor" />
    </svg>
  );
}

export function GroundLinkIcon(): React.JSX.Element {
  return (
    <svg {...base}>
      <path d="M4 18 20 6" />
      <path d="M8 6a8 8 0 0 1 10 10" />
      <circle cx="6" cy="18" r="2" fill="currentColor" />
    </svg>
  );
}

export function AlertCircleIcon(): React.JSX.Element {
  return (
    <svg {...base} width="26" height="26">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6M12 16v1" />
    </svg>
  );
}
