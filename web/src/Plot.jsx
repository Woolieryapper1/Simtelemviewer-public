import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Plotly } from "./chartUtils.js";
import { onThemeChange } from "./theme.js";

// A box that Plotly draws into. `draw(el)` runs when anything in `deps`
// changes (the data the chart shows), not on every render of the page -
// redrawing all the charts on every little change made the app slow.
// It also runs when the light/dark theme changes, since charts take their
// colours from the theme when they draw.
// The parent can get the Plotly element through a ref (to zoom it, move a cursor...).
const Plot = forwardRef(function Plot({ draw, deps, className = "" }, ref) {
  const el = useRef(null);
  const latestDraw = useRef(draw);
  latestDraw.current = draw;
  useImperativeHandle(ref, () => el.current, []);
  useEffect(() => { if (el.current) draw(el.current); }, deps);
  useEffect(() => onThemeChange(() => el.current && latestDraw.current(el.current)), []);
  useEffect(() => () => el.current && Plotly.purge(el.current), []); // free memory when it goes
  return <div ref={el} className={className} />;
});
export default Plot;
