import { useState } from "react";
import { Badge, Button, Checkbox, Input, Popover } from "./ui/index.js";

// "+ Channels" button with a searchable list of every channel.
// Tick a channel to add it as its own row (untick removes it everywhere),
// or drag it onto a row of the chart to overlay it there.
export default function ChannelPicker({ channels, used, onToggle, onDragStart }) {
  const [search, setSearch] = useState("");

  // Every word must appear somewhere, so "temp core" finds "Tire Temp Core FL"
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = channels.filter((c) => words.every((w) => c.name.toLowerCase().includes(w)));

  return (
    <Popover width={360} label="Channels"
      trigger={<Button icon="plus">Channels <span className="muted">{used.size} in use</span></Button>}>
      <Input type="search" autoFocus placeholder="temp core" value={search} className="picker-search"
        onChange={(e) => setSearch(e.target.value)} aria-label="Search channels" />
      <div className="picker-list">
        {shown.map((c) => (
          <label key={c.name} className="picker-row" title={c.description || ""} draggable
            onDragStart={(e) => onDragStart(e, c.name)}>
            <Checkbox checked={used.has(c.name)} onChange={() => onToggle(c.name)} />
            <span className="picker-name">{c.name}</span>
            {c.maths && <Badge>maths</Badge>}
            {c.approximate && <Badge tone="warn">est.</Badge>}
            <span className="picker-unit">{c.unit}</span>
          </label>
        ))}
        {!shown.length && <div className="box-empty">No channels match</div>}
      </div>
    </Popover>
  );
}
