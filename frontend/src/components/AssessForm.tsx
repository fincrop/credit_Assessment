import { FormEvent, useMemo, useState } from "react";

type Props = {
  onSubmit: (p: {
    farmerId: string;
    includeHeavy: boolean;
    pmKisanEnrolled: boolean;
    hasCropInsurance: boolean;
  }) => void;
  loading: boolean;
};

export function AssessForm({ onSubmit, loading }: Props) {
  const [mode, setMode] = useState<"existing" | "manual">("existing");
  const [farmerId, setFarmerId] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [cropName, setCropName] = useState("");
  const [sowingDate, setSowingDate] = useState("");
  const [boundary, setBoundary] = useState("");
  const [pmKisanEnrolled, setPmKisanEnrolled] = useState(false);
  const [hasCropInsurance, setHasCropInsurance] = useState(false);
  const [includeHeavy, setIncludeHeavy] = useState(false);
  const canSubmit = useMemo(() => {
    if (!farmerId.trim()) return false;
    if (mode === "existing") return true;
    return latitude.trim().length > 0 && longitude.trim().length > 0;
  }, [farmerId, latitude, longitude, mode]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      farmerId,
      includeHeavy,
      pmKisanEnrolled,
      hasCropInsurance,
    });
  }

  return (
    <form className="card assess-card" onSubmit={handleSubmit}>
      <h2>Start New Assessment</h2>
      <div className="mode-switch">
        <button type="button" className={mode === "existing" ? "active" : ""} onClick={() => setMode("existing")}>
          I already have `farmer_id`
        </button>
        <button type="button" className={mode === "manual" ? "active" : ""} onClick={() => setMode("manual")}>
          Create with full farm details
        </button>
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

      {mode === "manual" && (
        <div className="grid-2">
          <div className="field">
            <label htmlFor="latitude">Latitude</label>
            <input
              id="latitude"
              type="text"
              placeholder="e.g. 27.4938"
              value={latitude}
              onChange={(e) => setLatitude(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label htmlFor="longitude">Longitude</label>
            <input
              id="longitude"
              type="text"
              placeholder="e.g. 78.0452"
              value={longitude}
              onChange={(e) => setLongitude(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label htmlFor="crop-name">Crop Name</label>
            <input
              id="crop-name"
              type="text"
              placeholder="e.g. Wheat"
              value={cropName}
              onChange={(e) => setCropName(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="field">
            <label htmlFor="sowing-date">Current season sowing date (optional)</label>
            <input
              id="sowing-date"
              type="date"
              value={sowingDate}
              onChange={(e) => setSowingDate(e.target.value)}
            />
          </div>
          <div className="field" style={{ gridColumn: "1 / -1" }}>
            <label htmlFor="boundary">Farm boundary coordinates (optional)</label>
            <textarea
              id="boundary"
              className="text-area"
              placeholder="Paste polygon coordinates here; map drawing integration can be attached next."
              value={boundary}
              onChange={(e) => setBoundary(e.target.value)}
            />
          </div>
          <div className="map-placeholder">
            <strong>Map View (interactive)</strong>
            <span>
              Coordinates search and boundary drawing UI placeholder. I’ll wire full map interaction in next pass.
            </span>
          </div>
        </div>
      )}

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={includeHeavy}
          onChange={(e) => setIncludeHeavy(e.target.checked)}
        />
        Include trimmed satellite metadata (larger payload)
      </label>
      <div className="checkbox-row" style={{ marginTop: "0.5rem" }}>
        <input type="checkbox" checked={pmKisanEnrolled} onChange={(e) => setPmKisanEnrolled(e.target.checked)} />
        PM-KISAN enrolled
      </div>
      <div className="checkbox-row" style={{ marginTop: "0.35rem" }}>
        <input type="checkbox" checked={hasCropInsurance} onChange={(e) => setHasCropInsurance(e.target.checked)} />
        Has crop insurance (PMFBY or equivalent)
      </div>

      <button className="btn btn-primary" type="submit" disabled={loading || !canSubmit}>
        {loading ? "Running pipeline…" : "Start analysis"}
      </button>
      {loading && (
        <p style={{ marginTop: "0.75rem", fontSize: "0.8rem", color: "var(--text-muted)" }}>
          This can take several minutes (satellite + weather + scoring). Do not close the tab.
        </p>
      )}
    </form>
  );
}
