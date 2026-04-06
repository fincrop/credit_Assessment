import { useState } from "react";
import type { AssessmentPayload } from "../types/assessment";

export function JsonInspector({ data }: { data: AssessmentPayload }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card">
      <h2>Raw response</h2>
      <p className="card-subtitle">Full JSON as returned by the API (for audit / integrations).</p>
      <button
        type="button"
        className="btn"
        style={{
          background: "var(--bg-elevated)",
          color: "var(--text)",
          border: "1px solid var(--border)",
          marginBottom: "0.75rem",
        }}
        onClick={() => setOpen(!open)}
      >
        {open ? "Hide" : "Show"} JSON
      </button>
      {open && <pre className="json-pre">{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}
