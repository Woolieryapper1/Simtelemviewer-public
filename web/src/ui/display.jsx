import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";

// Building blocks for showing things: cards, tables, tags, chips, toasts.

// ---------- Card ----------
// A rounded box. With a title (and optional grey `sub` text and `actions` on
// the right) it gets a header row. `as` changes the element (e.g. "section").
export function Card({ title, sub, actions, children, className = "", as: Tag = "div", ...rest }) {
  return (
    <Tag className={`card ${className}`} {...rest}>
      {(title || sub || actions) && (
        <div className="card-head">
          {title && <h3>{title}</h3>}
          {sub && <span className="muted">{sub}</span>}
          {actions && <span className="push">{actions}</span>}
        </div>
      )}
      {children}
    </Tag>
  );
}

// ---------- Table ----------
// A dense data table that scrolls sideways when it's too wide, with its header
// row pinned while scrolling down (give it a maxHeight to scroll down).
// Write the <thead>/<tbody> as usual; cells with numbers get className="num"
// (right-aligned, tabular figures).
//   clickable: rows highlight on hover (for tables where clicking a row does something)
export function Table({ children, className = "", maxHeight, clickable = false, label }) {
  return (
    <div className="table-scroll" style={maxHeight ? { maxHeight, overflowY: "auto" } : undefined}
      tabIndex={maxHeight ? 0 : undefined} role={maxHeight ? "region" : undefined} aria-label={maxHeight ? label : undefined}>
      <table className={`data${clickable ? " clickable" : ""} ${className}`}>{children}</table>
    </div>
  );
}

// ---------- Badge / Tag ----------
// A small label: lap kinds ("out", "invalid"), known/learned, "ref", "est.".
//   tone: "neutral" (default), "accent", "good", "warn", "bad", "info"
// Each tone has its own word too, so colour is never the only signal.
export function Badge({ tone = "neutral", icon, children, className = "", ...rest }) {
  return (
    <span className={`tag ${tone} ${className}`} {...rest}>
      {icon && <Icon name={icon} size={11} strokeWidth={2.5} />}
      {children}
    </span>
  );
}
export const Tag = Badge;

// ---------- Chip ----------
// A rounded toggle button for filters; any number can be on at once.
export function Chip({ pressed, children, className = "", ...rest }) {
  return (
    <button type="button" className={`chip ${className}`} aria-pressed={!!pressed} {...rest}>
      {children}
    </button>
  );
}

// ---------- Toast ----------
// Short messages that pop up in the corner and go away on their own ("Saved").
// Wrap the app in <ToastProvider>, then in any component:
//   const toast = useToast();  toast("Saved the layout");  toast("Couldn't save", { tone: "bad" });
const ToastContext = createContext(() => {});
export const useToast = () => useContext(ToastContext);

const TOAST_ICONS = { good: "check-circle", bad: "alert", warn: "alert", info: "info" };

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);
  const dismiss = useCallback((id) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const toast = useCallback((text, { tone = "neutral", duration = 4000 } = {}) => {
    const id = nextId.current++;
    setToasts((all) => [...all.slice(-3), { id, text, tone, duration }]); // at most four at a time
    return id;
  }, []);
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="ui-toasts" role="status" aria-live="polite">
        {toasts.map((t) => <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />)}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }) {
  // Stay while the mouse is on it, so it can be read
  const [hover, setHover] = useState(false);
  useEffect(() => {
    if (hover || !toast.duration) return;
    const t = setTimeout(onDismiss, toast.duration);
    return () => clearTimeout(t);
  }, [hover]);
  return (
    <div className={`ui-toast ui-toast--${toast.tone}`} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      {TOAST_ICONS[toast.tone] && <Icon name={TOAST_ICONS[toast.tone]} size={16} className="ui-toast-icon" />}
      <span className="ui-toast-text">{toast.text}</span>
      <button type="button" className="ui-toast-close" aria-label="Dismiss" onClick={onDismiss}><Icon name="x" size={14} /></button>
    </div>
  );
}
