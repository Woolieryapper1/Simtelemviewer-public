import Icon from "./Icon.jsx";

// Form controls. They wrap the browser's own <input>/<select>, so keyboard use,
// screen readers and form behaviour all work as normal; only the look is ours.
// Every extra prop goes to the real input (value, onChange, disabled, ...).

// Text or number box. size "md" (default) or "sm" (inline in a sentence, tables).
export function Input({ size = "md", className = "", ref, ...rest }) {
  return <input ref={ref} className={`ui-input ui-input--${size} ${className}`} {...rest} />;
}

export function Textarea({ className = "", ref, ...rest }) {
  return <textarea ref={ref} className={`ui-input ui-textarea ${className}`} {...rest} />;
}

// Drop-down list: pass <option>s (and <optgroup>s) as children, like a <select>.
export function Select({ size = "md", className = "", children, ref, ...rest }) {
  return (
    <span className={`ui-select ui-select--${size} ${className}`}>
      <select ref={ref} {...rest}>{children}</select>
      <Icon name="chevron-down" size={14} className="ui-select-arrow" />
    </span>
  );
}

// Tick box. With children it's a label you can click; without, just the box
// (for use inside a row that's already a <label>).
export function Checkbox({ children, className = "", ref, ...rest }) {
  const Wrap = children ? "label" : "span";
  return (
    <Wrap className={`ui-check ${className}`}>
      <span className="ui-check-ctl">
        <input ref={ref} type="checkbox" {...rest} />
        <span className="ui-check-box" aria-hidden="true"><Icon name="check" size={12} strokeWidth={3} /></span>
      </span>
      {children && <span className="ui-check-text">{children}</span>}
    </Wrap>
  );
}

// On/off switch: for settings that take effect straight away
export function Switch({ children, className = "", ref, ...rest }) {
  const Wrap = children ? "label" : "span";
  return (
    <Wrap className={`ui-check ui-switch ${className}`}>
      <span className="ui-check-ctl">
        <input ref={ref} type="checkbox" role="switch" {...rest} />
        <span className="ui-switch-track" aria-hidden="true"><span className="ui-switch-thumb" /></span>
      </span>
      {children && <span className="ui-check-text">{children}</span>}
    </Wrap>
  );
}

// Slider for a number in a range; the filled part of the track shows the value
export function Slider({ value, min = 0, max = 100, className = "", style, ref, ...rest }) {
  const pct = ((Number(value) - min) / (max - min || 1)) * 100;
  return (
    <input ref={ref} type="range" className={`ui-slider ${className}`} value={value} min={min} max={max}
      style={{ ...style, "--fill": `${Math.min(100, Math.max(0, pct))}%` }} {...rest} />
  );
}

// A control with its name beside it: "Layout [select]"
export function Field({ label, children, className = "", ...rest }) {
  return (
    <label className={`field ${className}`} {...rest}>
      <span>{label}</span>
      {children}
    </label>
  );
}
