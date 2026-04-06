import { FormEvent, useState } from "react";
import { defaultApiBase } from "../api/client";

type Props = {
  onSubmit: (p: {
    baseUrl: string;
    farmerId: string;
    apiKey: string;
    includeHeavy: boolean;
  }) => void;
  loading: boolean;
};

export function AssessForm({ onSubmit, loading }: Props) {
  const [baseUrl, setBaseUrl] = useState(defaultApiBase());
  const [farmerId, setFarmerId] = useState("");
  const [apiKey, setApiKey] = useState(import.meta.env.VITE_API_KEY ?? "");
  const [includeHeavy, setIncludeHeavy] = useState(false);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const b = baseUrl.trim() || defaultApiBase();
    onSubmit({
      baseUrl: b,
      farmerId,
      apiKey,
      includeHeavy,
    });
  }

  return (
    <form className="card" onSubmit={handleSubmit}>
      <h2>Run assessment</h2>
      <p className="card-subtitle">
        Requires a farmer record in MongoDB <code style={{ color: "var(--accent)" }}>farm_info</code> and a
        running FastAPI service (<code>/v1/assess</code>).
      </p>

      <div className="field">
        <label htmlFor="api-base">API base URL</label>
        <input
          id="api-base"
          type="url"
          placeholder="https://your-api.onrender.com"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          autoComplete="off"
        />
      </div>
      <div className="field">
        <label htmlFor="farmer-id">Farmer ID</label>
        <input
          id="farmer-id"
          type="text"
          placeholder="e.g. potato_05"
          value={farmerId}
          onChange={(e) => setFarmerId(e.target.value)}
          required
          autoComplete="off"
        />
      </div>
      <div className="field">
        <label htmlFor="api-key">X-API-Key (optional)</label>
        <input
          id="api-key"
          type="password"
          placeholder="If API_SERVICE_KEY is set on the server"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          autoComplete="off"
        />
      </div>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={includeHeavy}
          onChange={(e) => setIncludeHeavy(e.target.checked)}
        />
        Include trimmed satellite metadata (larger payload)
      </label>

      <button className="btn btn-primary" type="submit" disabled={loading || !farmerId.trim()}>
        {loading ? "Running pipeline…" : "Run full pipeline"}
      </button>
      {loading && (
        <p style={{ marginTop: "0.75rem", fontSize: "0.8rem", color: "var(--text-muted)" }}>
          This can take several minutes (satellite + weather + scoring). Do not close the tab.
        </p>
      )}
    </form>
  );
}
