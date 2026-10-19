import { cloneElement, useEffect, useId, useRef, useState } from "react";
import Icon from "./Icon.jsx";

// Things that float over the page: tooltips, popovers and menus, modals and drawers.

// ---------- Tooltip ----------
// A short hint shown when the mouse rests on (or the keyboard focuses) the child.
// The child must be one element that can take focus or a hover, e.g. a Button.
export function Tooltip({ content, children, side = "top" }) {
  const [open, setOpen] = useState(false);
  const timer = useRef(null);
  const id = useId();
  const show = () => { clearTimeout(timer.current); timer.current = setTimeout(() => setOpen(true), 350); };
  const hide = () => { clearTimeout(timer.current); setOpen(false); };
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <span className="ui-tip-wrap" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
      onKeyDown={(e) => e.key === "Escape" && hide()}>
      {cloneElement(children, { "aria-describedby": open ? id : undefined })}
      {open && <span role="tooltip" id={id} className={`ui-tip ui-tip--${side}`}>{content}</span>}
    </span>
  );
}

// ---------- Popover ----------
// A box that opens under a button and closes on a click outside or Escape
// (which puts the keyboard focus back on the button).
//   trigger:  the button element; it gets the click handler and aria-expanded
//   children: the box's contents, or a function ({ close }) => contents
//   align:    "left" (default) or "right" edge of the button
//   role:     "dialog" (default) or "menu"
export function Popover({ trigger, children, align = "left", width, className = "", role = "dialog", label, open: openProp, onOpenChange }) {
  const [openState, setOpenState] = useState(false);
  const open = openProp ?? openState;
  const setOpen = (v) => { setOpenState(v); onOpenChange?.(v); };
  const boxRef = useRef(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const outside = (e) => !boxRef.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => { setOpen(false); buttonRef.current?.focus(); };
  return (
    <div className="ui-pop-wrap" ref={boxRef}>
      {cloneElement(trigger, {
        ref: buttonRef, "aria-expanded": open, "aria-haspopup": role,
        onClick: (e) => { trigger.props.onClick?.(e); setOpen(!open); },
      })}
      {open && (
        <div className={`ui-pop ui-pop--${align} ${className}`} role={role} aria-label={label} style={width ? { width } : undefined}>
          {typeof children === "function" ? children({ close }) : children}
        </div>
      )}
    </div>
  );
}

// ---------- Menu ----------
// A list of actions under a button. Arrow keys move, Enter picks, Escape closes.
//   items: [{ label, icon, onSelect, checked, disabled, hint }] or "-" for a divider.
//   An item with `checked` (true/false) shows a tick: use it for a choice of options.
export function Menu({ trigger, items, align = "left", label, width }) {
  return (
    <Popover trigger={trigger} align={align} role="menu" label={label} width={width} className="ui-menu">
      {({ close }) => <MenuItems items={items} close={close} />}
    </Popover>
  );
}

function MenuItems({ items, close }) {
  const listRef = useRef(null);
  const buttons = () => [...listRef.current.querySelectorAll("[role^=menuitem]:not(:disabled)")];
  // Focus the ticked item (or the first) when the menu opens
  useEffect(() => {
    const all = buttons();
    (all.find((b) => b.getAttribute("aria-checked") === "true") ?? all[0])?.focus();
  }, []);
  const onKey = (e) => {
    const all = buttons(), i = all.indexOf(document.activeElement);
    const next = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: all.length - 1 }[e.key];
    if (next == null) return;
    e.preventDefault();
    all[(next + all.length) % all.length]?.focus();
  };
  return (
    <div ref={listRef} onKeyDown={onKey}>
      {items.map((it, i) => (it === "-" ? <div key={i} className="ui-menu-sep" role="separator" /> : (
        <button key={i} type="button" className="ui-menu-item" disabled={it.disabled}
          role={it.checked == null ? "menuitem" : "menuitemradio"} aria-checked={it.checked ?? undefined}
          onClick={() => { it.onSelect?.(); close(); }}>
          <span className="ui-menu-icon">{it.checked ? <Icon name="check" size={14} /> : it.icon && <Icon name={it.icon} size={15} />}</span>
          <span className="ui-menu-label">{it.label}</span>
          {it.hint && <span className="ui-menu-hint">{it.hint}</span>}
        </button>
      )))}
    </div>
  );
}

// ---------- Modal and Drawer ----------
// A window over the page that has to be dealt with before going back to it.
// Uses the browser's own <dialog>, which keeps the keyboard inside it and
// closes on Escape. Clicking the dimmed area outside closes it too.
//   side: "center" (a modal) or "right" (a drawer sliding in from the right)
export function Modal({ open, onClose, title, children, footer, side = "center", width, className = "" }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className={`ui-modal ui-modal--${side} ${className}`} style={width ? { width } : undefined}
      aria-label={typeof title === "string" ? title : undefined}
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      onClick={(e) => e.target === ref.current && onClose()}>
      {open && (
        <div className="ui-modal-body">
          <div className="ui-modal-head">
            <h2>{title}</h2>
            <button type="button" className="ui-btn ui-btn--ghost ui-btn--md ui-btn--icon" aria-label="Close" title="Close" onClick={onClose}>
              <Icon name="x" />
            </button>
          </div>
          <div className="ui-modal-content">{children}</div>
          {footer && <div className="ui-modal-foot">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}

export const Drawer = (props) => <Modal side="right" {...props} />;
