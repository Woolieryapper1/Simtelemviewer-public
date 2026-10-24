import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Button } from "./ui/index.js";

// Sharing the open run in the team library (see backend shared.py): a folder the
// whole team syncs with OneDrive, so teammates see the run in their own run list
// without it being sent to them. Hidden when no team folder is set.
export default function ShareRun({ team, session, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => setError(null), [session.id]);
  if (!team?.enabled) return null;

  const act = (call) => {
    setBusy(true);
    setError(null);
    call(session.id).then(onChange).catch((e) => setError(e.message)).finally(() => setBusy(false));
  };
  const unshare = () => {
    // A run shared from another PC isn't on this one: unsharing takes it out of the list here too
    if (session.id.includes("~") && !window.confirm("Take this run out of the team library? It isn't on this PC, so it will go from your list too.")) return;
    act(api.unshare);
  };

  return (
    <div className="box share-run">
      {!session.mine ? <span>Shared by <b>{session.owner}</b></span>
        : session.shared ? <>
          <span>Shared with the team</span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={unshare}
            title="Take it out of the team library (your copy on this PC stays)">Unshare</Button>
        </>
        : <Button size="sm" icon="upload" disabled={busy} onClick={() => act(api.share)}
          title={`Copy it into your folder of the team library (${team.folder}) for the team to see`}>
          {busy ? "Sharing…" : "Share with the team"}
        </Button>}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
