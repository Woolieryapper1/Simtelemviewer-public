import { useState } from "react";
import { Icon, IconButton, Select } from "./ui/index.js";

// A row/grid of boxes the user can rearrange (Stint and Metrics pages). In edit
// mode every box gets a drag handle and a remove button, and a dashed "add" box
// lists what's hidden.
//   catalogue: { id: { label or title, has(data), wide? } } - everything that can be shown
//   data:      what has() is asked about, to mark boxes with nothing to show
//   wide:      a box that spans the whole row of a grid
export default function Arrange({ className, kind, ids, editing, catalogue, data, onChange, render, noData = "no data in this run" }) {
  const [over, setOver] = useState(null);
  const type = `text/x-arrange-${kind}`; // tiles only drop on tiles, charts on charts
  const move = (from, to) => {
    if (!from || from === to) return;
    const next = ids.filter((x) => x !== from);
    next.splice(to == null ? next.length : next.indexOf(to), 0, from);
    onChange(next);
  };
  const hidden = Object.keys(catalogue).filter((id) => !ids.includes(id));
  const label = (id) => catalogue[id].label ?? catalogue[id].title;
  const dropTarget = (key, to) => ({
    onDragOver: (e) => { if (editing && e.dataTransfer.types.includes(type)) { e.preventDefault(); setOver(key); } },
    onDragLeave: () => setOver(null),
    onDrop: (e) => { e.preventDefault(); setOver(null); move(e.dataTransfer.getData(type), to); },
  });
  return (
    <div className={`${className}${editing ? " editing" : ""}`}>
      {ids.map((id) => (
        <div key={id} className={`editable${catalogue[id].wide ? " wide" : ""}${over === id ? " over" : ""}`} {...dropTarget(id, id)}>
          {render(id)}
          {editing && (
            <span className="item-tools">
              <span className="drag-handle" draggable title="Drag to reorder"
                onDragStart={(e) => e.dataTransfer.setData(type, id)}><Icon name="grip" /></span>
              <IconButton icon="x" size="sm" label={`Remove ${label(id)}`} onClick={() => onChange(ids.filter((x) => x !== id))} />
            </span>
          )}
        </div>
      ))}
      {editing && (
        <div className={`add-slot${over === "__end" ? " over" : ""}`} {...dropTarget("__end", null)}>
          <Select value="" disabled={!hidden.length} aria-label={`Add a ${kind}`}
            onChange={(e) => e.target.value && onChange([...ids, e.target.value])}>
            <option value="">{hidden.length ? `+ Add ${kind}` : `Every ${kind} is shown`}</option>
            {hidden.map((id) => (
              <option key={id} value={id}>{label(id)}{catalogue[id].has(data) ? "" : ` (${noData})`}</option>
            ))}
          </Select>
        </div>
      )}
    </div>
  );
}
