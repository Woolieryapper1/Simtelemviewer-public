import { useState } from "react";
import {
  Badge, Button, Card, Checkbox, Chip, Drawer, Field, Icon, ICON_NAMES, IconButton, Input, Menu, Modal,
  Popover, Segmented, Select, Slider, Switch, Table, Tabs, Textarea, ThemeMenu, ToastProvider, Tooltip, useToast,
} from "./ui/index.js";
import "./Design.css";

// Developer page at /design (only in `npm run dev`; it isn't in the built app).
// Shows every shared component and colour, once in the dark theme and once in the
// light theme side by side, so a change to a token or a component can be checked
// at a glance in both.

function Swatches({ names }) {
  return (
    <div className="d-swatches">
      {names.map((n) => (
        <div key={n} className="d-swatch">
          <span style={{ background: `var(${n})` }} />
          <code>{n.replace("--", "")}</code>
        </div>
      ))}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section className="d-section">
      <h3>{title}</h3>
      <div className="d-row">{children}</div>
    </section>
  );
}

// Everything, in whichever theme the wrapper forces
function Gallery({ theme }) {
  const toast = useToast();
  const [seg, setSeg] = useState("zoom");
  const [tab, setTab] = useState("Stint");
  const [slider, setSlider] = useState(40);
  const [on, setOn] = useState(true);
  const [chip, setChip] = useState(false);
  const [modal, setModal] = useState(false);
  const [drawer, setDrawer] = useState(false);

  return (
    <div data-theme={theme} className="d-theme">
      <h2>{theme === "dark" ? "Dark" : "Light"}</h2>

      <Section title="Buttons">
        <Button variant="primary" icon="plus">Primary</Button>
        <Button>Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="danger" icon="trash">Delete</Button>
        <Button size="sm">Small</Button>
        <Button disabled>Disabled</Button>
        <IconButton icon="reset" label="Reset" />
        <IconButton icon="eye" label="Show" pressed />
      </Section>

      <Section title="Inputs">
        <Input placeholder="Text" />
        <Input size="sm" type="number" defaultValue={12} aria-label="Number" />
        <Input aria-invalid="true" defaultValue="Invalid" />
        <Select defaultValue="b" aria-label="Pick"><option value="a">First</option><option value="b">Second</option></Select>
        <Select size="sm" aria-label="Small pick"><option>Small</option></Select>
        <Field label="Field"><Input size="sm" defaultValue="x" /></Field>
        <Textarea rows={2} placeholder="Notes" style={{ width: 220 }} />
      </Section>

      <Section title="Checkbox, switch, slider">
        <Checkbox defaultChecked>Checked</Checkbox>
        <Checkbox>Unchecked</Checkbox>
        <Checkbox disabled>Disabled</Checkbox>
        <Switch checked={on} onChange={(e) => setOn(e.target.checked)}>Switch</Switch>
        <Slider value={slider} onChange={(e) => setSlider(+e.target.value)} aria-label="Slider" />
        <span className="mono">{slider}</span>
      </Section>

      <Section title="Tabs, segmented control, chips">
        <Tabs items={["Telemetry", "Stint", "Metrics"]} value={tab} onChange={setTab} label="Pages" />
        <Segmented options={[{ value: "zoom", label: "Zoom", icon: "zoom-in" }, { value: "pan", label: "Pan", icon: "move" }]}
          value={seg} onChange={setSeg} label="Mode" />
        <Segmented size="sm" options={["a", "b", "c"]} value="b" onChange={() => {}} label="Small" />
        <Chip pressed={chip} onClick={() => setChip(!chip)}>Filter chip</Chip>
        <Chip pressed>On</Chip>
      </Section>

      <Section title="Badges">
        <Badge>neutral</Badge><Badge tone="accent">ref</Badge><Badge tone="good" icon="check">known</Badge>
        <Badge tone="warn">learned</Badge><Badge tone="bad">invalid</Badge><Badge tone="info">est.</Badge>
      </Section>

      <Section title="Tooltip, popover, menu, modal, drawer, toast">
        <Tooltip content="A short hint"><Button>Hover me</Button></Tooltip>
        <Popover trigger={<Button iconRight="chevron-down">Popover</Button>}>
          <p style={{ margin: 0 }}>Anything can go in here.</p>
        </Popover>
        <Menu trigger={<Button iconRight="chevron-down">Menu</Button>}
          items={[{ label: "Edit", icon: "edit" }, { label: "Download", icon: "download", hint: "Ctrl+S" }, "-", { label: "Ticked", checked: true }, { label: "Unticked", checked: false }]} />
        <Button onClick={() => setModal(true)}>Modal</Button>
        <Button onClick={() => setDrawer(true)}>Drawer</Button>
        <Button onClick={() => toast("Saved the layout", { tone: "good" })}>Toast</Button>
        <Button onClick={() => toast("Could not save", { tone: "bad" })}>Error toast</Button>
        <Modal open={modal} onClose={() => setModal(false)} title="A modal"
          footer={<><Button onClick={() => setModal(false)}>Cancel</Button><Button variant="primary" onClick={() => setModal(false)}>OK</Button></>}>
          It closes on Escape or a click outside.
        </Modal>
        <Drawer open={drawer} onClose={() => setDrawer(false)} title="A drawer">Slides in from the right.</Drawer>
      </Section>

      <Section title="Card and table">
        <Card title="Card title" sub="with a sub line" actions={<Button size="sm" variant="ghost" icon="more">More</Button>} className="d-wide">
          <Table maxHeight={150} label="Example laps" clickable>
            <thead><tr><th>Lap</th><th className="num">Time</th><th className="num">Gap</th><th>Kind</th></tr></thead>
            <tbody>
              {Array.from({ length: 12 }, (_, i) => (
                <tr key={i} className={i === 3 ? "best" : ""}>
                  <td>{i + 1}</td><td className="num">1:2{i % 10}.{100 + i * 7}</td>
                  <td className={`num ${i % 2 ? "slower" : "faster"}`}>{i % 2 ? "+" : "-"}0.{i}2</td>
                  <td>{i === 5 ? <Badge tone="bad">invalid</Badge> : "flying"}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <div className="sectors">
          <span className="sector s-best">22.104</span><span className="sector s-pb">31.220</span><span className="sector s-slow">18.940</span>
        </div>
      </Section>

      <Section title="Colours">
        <Swatches names={["--page", "--chrome", "--card", "--raised", "--border", "--border-strong", "--text", "--text-2", "--text-3",
          "--accent", "--accent-text", "--accent-solid", "--bad", "--good", "--warn", "--info",
          "--sector-best", "--sector-pb", "--sector-slow", "--throttle", "--brake"]} />
        <Swatches names={["--lap-1", "--lap-2", "--lap-3", "--lap-4", "--lap-5", "--lap-6",
          "--channel-1", "--channel-2", "--channel-3", "--channel-4", "--channel-5", "--channel-6", "--channel-7", "--channel-8"]} />
      </Section>

      <Section title="Icons">
        <div className="d-icons">
          {ICON_NAMES.map((n) => <span key={n} title={n}><Icon name={n} size={18} /></span>)}
        </div>
      </Section>
    </div>
  );
}

export default function Design() {
  return (
    <ToastProvider>
      <div className="d-page">
        <header className="d-head">
          <h1>Design system</h1>
          <span className="muted">Dev only. The app's own theme choice:</span>
          <ThemeMenu />
        </header>
        <div className="d-cols">
          <Gallery theme="dark" />
          <Gallery theme="light" />
        </div>
      </div>
    </ToastProvider>
  );
}
