import { setTheme, useTheme } from "../theme.js";
import { IconButton } from "./Button.jsx";
import { Menu } from "./overlays.jsx";

const CHOICES = [
  { value: "system", label: "Match my computer", icon: "monitor" },
  { value: "light", label: "Light", icon: "sun" },
  { value: "dark", label: "Dark", icon: "moon" },
];

// Top-bar button to pick the light or dark theme (or follow the computer).
// The button shows the theme that's showing now.
export default function ThemeMenu({ align = "right" }) {
  const { choice, resolved } = useTheme();
  return (
    <Menu label="Theme" align={align}
      trigger={<IconButton icon={resolved === "light" ? "sun" : "moon"} label="Theme" />}
      items={CHOICES.map((c) => ({ label: c.label, checked: choice === c.value, onSelect: () => setTheme(c.value) }))} />
  );
}
