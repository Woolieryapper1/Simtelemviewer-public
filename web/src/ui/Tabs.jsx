import Icon from "./Icon.jsx";

// Arrow keys move between the buttons of a group (and pick the one moved to),
// Home/End jump to the ends - the usual keyboard behaviour for tabs and
// segmented controls. Only the picked button is in the Tab order.
function arrowKeys(e, values, value, onChange) {
  const i = values.indexOf(value);
  const next = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: values.length - 1 }[e.key];
  if (next == null) return;
  e.preventDefault();
  const v = values[(next + values.length) % values.length];
  onChange(v);
  // focus follows the choice
  e.currentTarget.querySelector(`[data-value="${CSS.escape(String(v))}"]`)?.focus();
}

// Items are strings, or { value, label, icon, title } objects
const norm = (o) => (typeof o === "object" ? o : { value: o, label: o });

// Tabs that switch between views (the pages in the top bar).
//   items: ["Telemetry", ...] or [{ value, label, icon }]
export function Tabs({ items, value, onChange, label, className = "" }) {
  const list = items.map(norm);
  return (
    <div role="tablist" aria-label={label} className={`ui-tabs ${className}`}
      onKeyDown={(e) => arrowKeys(e, list.map((o) => o.value), value, onChange)}>
      {list.map((o) => (
        <button key={o.value} type="button" role="tab" className="ui-tab" data-value={o.value}
          aria-selected={o.value === value} tabIndex={o.value === value ? 0 : -1} title={o.title}
          onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} size={15} />}{o.label}
        </button>
      ))}
    </div>
  );
}

// Two or more buttons joined together, exactly one of them on (Zoom | Pan).
//   options: ["zoom", "pan"] or [{ value, label, icon, title }]
export function Segmented({ options, value, onChange, label, size = "md", className = "" }) {
  const list = options.map(norm);
  return (
    <div role="radiogroup" aria-label={label} className={`ui-seg ui-seg--${size} ${className}`}
      onKeyDown={(e) => arrowKeys(e, list.map((o) => o.value), value, onChange)}>
      {list.map((o) => (
        <button key={o.value} type="button" role="radio" className="ui-seg-btn" data-value={o.value}
          aria-checked={o.value === value} tabIndex={o.value === value ? 0 : -1}
          title={o.title} aria-label={o.label ? undefined : o.title} onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} size={size === "sm" ? 14 : 15} />}{o.label}
        </button>
      ))}
    </div>
  );
}
