import { useEffect, useState } from "react";
import { api } from "../api";
import type { DownloadsCheckResponse, IngestFileType, IngestLink, IngestRunResult } from "../types";

const FILE_TYPES: IngestFileType[] = ["cash", "delivery", "fo"];

// NSE typically publishes the cash bhavcopy by ~18:30 IST (the digest scheduler's own
// cron time, app/main.py) — F&O has been observed to lag further behind that. Defaulting
// to "today" before files are actually published just produces 404s (see specs/01's
// Changelog), so default to the last *completed* trading day instead; the user can still
// move the date picker forward once they know today's files are out.
const PUBLISH_CUTOFF_HOUR = 19;

function toLocalIso(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultTradeDate(): string {
  const target = new Date();
  if (target.getHours() < PUBLISH_CUTOFF_HOUR) {
    target.setDate(target.getDate() - 1);
  }
  while (target.getDay() === 0 || target.getDay() === 6) {
    target.setDate(target.getDate() - 1);
  }
  return toLocalIso(target);
}

export function IngestScreen() {
  const [tradeDate, setTradeDate] = useState(defaultTradeDate);
  const [links, setLinks] = useState<IngestLink[]>([]);
  const [checkResult, setCheckResult] = useState<DownloadsCheckResponse | null>(null);
  const [selectedPaths, setSelectedPaths] = useState<Partial<Record<IngestFileType, string>>>({});
  const [checking, setChecking] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<IngestRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getIngestLinks(tradeDate).then(setLinks).catch((e) => setError(e.message));
    setCheckResult(null);
    setSelectedPaths({});
    setRunResult(null);
  }, [tradeDate]);

  async function handleCheck() {
    setChecking(true);
    setError(null);
    try {
      const result = await api.checkDownloads(tradeDate);
      setCheckResult(result);
      const initial: Partial<Record<IngestFileType, string>> = {};
      for (const fileType of FILE_TYPES) {
        if (result[fileType].matched_path) initial[fileType] = result[fileType].matched_path!;
      }
      setSelectedPaths(initial);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  async function handleRun() {
    if (!selectedPaths.cash) return;
    setRunning(true);
    setError(null);
    try {
      const result = await api.runLocalIngest(
        tradeDate,
        selectedPaths.cash,
        selectedPaths.delivery,
        selectedPaths.fo,
      );
      setRunResult(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <h2>Update market data</h2>
      <p className="muted">
        NSE doesn't allow automatic downloads, so you'll need to grab today's files yourself:
        open each link below, save the file to your computer, then come back and click{" "}
        <strong>Check downloads folder</strong>.
      </p>
      {error && <div className="banner banner-error">{error}</div>}

      <section className="card">
        <h3>1. Pick a trade date</h3>
        <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} />
        <p className="muted">
          Defaults to the last completed trading day, not today — NSE usually publishes the
          cash bhavcopy by ~6:30pm IST and F&amp;O sometimes later still. If a link 404s,
          the file for that date likely isn't published yet; try again later or pick an
          earlier date.
        </p>
      </section>

      <section className="card">
        <h3>2. Download the files</h3>
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Links</th>
              <th>Expected filename</th>
            </tr>
          </thead>
          <tbody>
            {links.map((link) => (
              <tr key={link.file_type}>
                <td className="symbol">{link.label}</td>
                <td className="link-cell">
                  <a href={link.direct_url} target="_blank" rel="noreferrer">
                    Open direct link
                  </a>
                  <a href={link.hub_url} target="_blank" rel="noreferrer">
                    Browse NSE reports
                  </a>
                </td>
                <td className="muted">{link.expected_filename_hint}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3>3. Check your downloads folder</h3>
        <button onClick={handleCheck} disabled={checking}>
          {checking ? "Checking…" : "Check downloads folder"}
        </button>

        {checkResult && (
          <div className="two-column" style={{ marginTop: "1rem" }}>
            {FILE_TYPES.map((fileType) => {
              const fileCheck = checkResult[fileType];
              return (
                <div key={fileType} className="attention-card">
                  <div className="attention-card-header">
                    <span className="symbol">{fileCheck.label}</span>
                  </div>
                  {fileCheck.matched_path ? (
                    <p>✓ Found: {fileCheck.matched_path.split("/").pop()}</p>
                  ) : (
                    <p className="muted">Not found automatically.</p>
                  )}
                  {fileCheck.candidates.length > 0 && (
                    <select
                      value={selectedPaths[fileType] ?? ""}
                      onChange={(e) =>
                        setSelectedPaths((prev) => ({ ...prev, [fileType]: e.target.value }))
                      }
                    >
                      <option value="">
                        {fileCheck.matched_path ? "Use auto-detected file" : "Pick a file…"}
                      </option>
                      {fileCheck.candidates.map((path) => (
                        <option key={path} value={path}>
                          {path.split("/").pop()}
                        </option>
                      ))}
                    </select>
                  )}
                  <input
                    placeholder="Or paste a full file path"
                    value={selectedPaths[fileType] ?? ""}
                    onChange={(e) =>
                      setSelectedPaths((prev) => ({ ...prev, [fileType]: e.target.value }))
                    }
                  />
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="card">
        <h3>4. Load the data</h3>
        <button onClick={handleRun} disabled={!selectedPaths.cash || running}>
          {running ? "Loading…" : "Load data"}
        </button>
        {!selectedPaths.cash && (
          <p className="muted">The cash market file is required; delivery and F&O are optional.</p>
        )}

        {runResult && (
          <div className={`banner ${runResult.status === "failed" ? "banner-error" : ""}`}>
            {runResult.status === "ok" && (
              <p>
                Loaded {runResult.symbols_loaded} symbols for {runResult.trade_date}.
                {runResult.corporate_actions_flagged.length > 0 &&
                  ` Flagged corporate actions: ${runResult.corporate_actions_flagged.join(", ")}.`}
              </p>
            )}
            {runResult.status === "skipped_already_ingested" && (
              <p>Already loaded for {runResult.trade_date} — refreshed with the latest numbers.</p>
            )}
            {runResult.status === "skipped_non_trading_day" && <p>Not a trading day.</p>}
            {runResult.status === "failed" && <p>Failed: {runResult.error}</p>}
          </div>
        )}
      </section>
    </div>
  );
}
