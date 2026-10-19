import Icon from "./Icon.jsx";

// Buttons. Every clickable action in the app is one of these.
//   variant: "secondary" (default, outlined), "primary" (filled indigo, the main
//            action of a box), "ghost" (no border, for toolbars and rows),
//            "danger" (deleting things)
//   size:    "md" (default) or "sm" (inside rows and dense tables)
//   icon:    an icon name (see Icon.jsx) shown before the text
//   as:      render as another element, e.g. "a" for a download link or
//            "label" around a hidden file input, so it still looks like a button
export function Button({ variant = "secondary", size = "md", icon, iconRight, as: Tag = "button", className = "", type, children, ref, ...rest }) {
  const iconSize = size === "sm" ? 14 : 16;
  return (
    <Tag ref={ref} className={`ui-btn ui-btn--${variant} ui-btn--${size} ${className}`}
      type={Tag === "button" ? type ?? "button" : type} {...rest}>
      {icon && <Icon name={icon} size={iconSize} />}
      {children}
      {iconRight && <Icon name={iconRight} size={iconSize} />}
    </Tag>
  );
}

// A button that's just an icon. `label` is required: it's read out by screen
// readers and shown as the hover hint. `pressed` makes it a toggle.
export function IconButton({ icon, label, size = "md", variant = "ghost", pressed, className = "", title, ref, ...rest }) {
  return (
    <Button ref={ref} variant={variant} size={size} className={`ui-btn--icon ${className}`}
      aria-label={label} title={title ?? label} aria-pressed={pressed} {...rest}>
      <Icon name={icon} size={size === "sm" ? 14 : 16} />
    </Button>
  );
}
