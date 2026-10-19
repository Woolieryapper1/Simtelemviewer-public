import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme.js"; // puts the saved light/dark choice on the page before anything draws
import "./ui/tokens.css";
import "./theme.css"; // the page layouts; the components' own styles (ui/ui.css) load after it and win ties
import App from "./App.jsx";

function show(Page) {
  createRoot(document.getElementById("root")).render(
    <StrictMode>
      <Page />
    </StrictMode>,
  );
}

// The /design page lists every shared component. It only exists while developing:
// the build drops it because `import.meta.env.DEV` is false there.
if (import.meta.env.DEV && location.pathname === "/design") {
  import("./Design.jsx").then((m) => show(m.default));
} else {
  show(App);
}
