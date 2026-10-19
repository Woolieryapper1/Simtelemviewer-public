// The app's one icon set: simple line icons drawn on a 24 x 24 grid with a
// round 2px stroke, written here as SVG so they're bundled (no icon font, no
// download) and take the text colour around them.
//
//   <Icon name="plus" />            decorative: screen readers skip it
//   <Icon name="trash" label="Delete" />  meaningful on its own: read out
//
// To add one, add its SVG shapes to PATHS below (24 x 24, strokes only).

const PATHS = {
  menu: <><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /></>,
  x: <><path d="M6 6l12 12" /><path d="M18 6L6 18" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  minus: <path d="M5 12h14" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  "chevron-down": <path d="M6 9l6 6 6-6" />,
  "chevron-up": <path d="M6 15l6-6 6 6" />,
  "chevron-right": <path d="M9 6l6 6-6 6" />,
  "chevron-left": <path d="M15 6l-6 6 6 6" />,
  grip: <><circle cx="9" cy="6" r="1" /><circle cx="15" cy="6" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="9" cy="18" r="1" /><circle cx="15" cy="18" r="1" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
  monitor: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8" /><path d="M12 16v4" /></>,
  reset: <><path d="M4 12a8 8 0 1 0 2.4-5.7" /><path d="M4 4v4.5h4.5" /></>,
  download: <><path d="M12 4v11" /><path d="M7 10.5l5 5 5-5" /><path d="M5 20h14" /></>,
  upload: <><path d="M12 20V9" /><path d="M7 13.5l5-5 5 5" /><path d="M5 4h14" /></>,
  trash: <><path d="M4 7h16" /><path d="M9.5 7V4.5h5V7" /><path d="M6.5 7l1 13h9l1-13" /><path d="M10 11v5.5M14 11v5.5" /></>,
  edit: <><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M13.5 6.5l4 4" /></>,
  note: <><path d="M6 3.5h8.5L19 8v12.5H6z" /><path d="M14 3.5V8h5" /><path d="M9 12.5h7M9 16h5" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5" /><path d="M12 7.5v.5" /></>,
  alert: <><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5" /><path d="M12 17.5v.5" /></>,
  "check-circle": <><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.8 2.8L16.5 9.5" /></>,
  star: <path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z" />,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="3" /></>,
  "zoom-in": <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /><path d="M11 8.5v5M8.5 11h5" /></>,
  move: <><path d="M12 3v18M3 12h18" /><path d="M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3" /></>,
  "arrows-v": <><path d="M12 4v16" /><path d="M8 8l4-4 4 4" /><path d="M8 16l4 4 4-4" /></>,
  sliders: <><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></>,
  layers: <><path d="M12 3.5l9 5-9 5-9-5z" /><path d="M3 13.5l9 5 9-5" /></>,
  layout: <><rect x="3.5" y="3.5" width="17" height="17" rx="2" /><path d="M3.5 9.5h17M10 9.5v11" /></>,
  more: <><circle cx="5.5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="18.5" cy="12" r="1" /></>,
  flag: <><path d="M5 21V4" /><path d="M5 4.5h11l-2 4 2 4H5" /></>,
  map: <><path d="M3.5 6.5l5.5-2 6 2 5.5-2v13l-5.5 2-6-2-5.5 2z" /><path d="M9 4.5v13M15 6.5v13" /></>,
  chart: <><path d="M4 4v16h16" /><path d="M7.5 15l4-5 3 3 4.5-6" /></>,
};

export const ICON_NAMES = Object.keys(PATHS);

export default function Icon({ name, size = 16, label, className = "", strokeWidth = 2, ...rest }) {
  return (
    <svg className={`ui-icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true} focusable="false" {...rest}>
      {PATHS[name] ?? PATHS.info}
    </svg>
  );
}
