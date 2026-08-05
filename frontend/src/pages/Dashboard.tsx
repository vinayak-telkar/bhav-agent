import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ActionBadge } from "../components/ActionBadge";
import { ActionSummaryStrip } from "../components/ActionSummaryStrip";
import { DeliveryGauge } from "../components/DeliveryGauge";
import { Glossary } from "../components/Glossary";
import { PriceChangeBadge } from "../components/PriceChangeBadge";
import type { DigestToday, Holding, Insight, StockSnapshot, WatchlistItem } from "../types";

export function Dashboard() {
  const [digest, setDigest] = useState<DigestToday | null>(null);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [wishlist, setWishlist] = useState<WatchlistItem[]>([]);
  const [history, setHistory] = useState<Insight[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [runningDigest, setRunningDigest] = useState(false);
  const [progressLog, setProgressLog] = useState<string[]>([]);
  const [trackerPage, setTrackerPage] = useState(1);
  const closeStreamRef = useRef<(() => void) | null>(null);

  // Sequential rather than Promise.all — a handful of one-time GETs on page
  // load, so there's no real cost to awaiting them in turn.
  //
  // latestRequestId guards against a stale call clobbering fresher state —
  // React StrictMode's dev-mode double-invocation of effects fires this
  // twice on mount, and without the guard a slower, superseded call's
  // error/result can land after a faster, newer call already succeeded.
  const latestRequestId = useRef(0);

  const refresh = async () => {
    const requestId = ++latestRequestId.current;
    setLoading(true);
    setError(null);
    try {
      const d = await api.getDigestToday();
      const h = await api.getHoldings();
      const w = await api.getWatchlist("wishlist");
      const hist = await api.getInsightsHistory();
      if (requestId !== latestRequestId.current) return; // superseded by a newer call
      setDigest(d);
      setHoldings(h);
      setWishlist(w);
      setHistory(hist);
      setTrackerPage(1); // back to the newest entries after a fresh load or digest run
    } catch (e) {
      if (requestId === latestRequestId.current) setError((e as Error).message);
    } finally {
      if (requestId === latestRequestId.current) setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  function handleRunDigest() {
    closeStreamRef.current?.(); // in case a previous run's stream is still open
    setRunningDigest(true);
    setError(null);
    setProgressLog([]);
    closeStreamRef.current = api.streamDigest(
      (message) => setProgressLog((prev) => [...prev, message]),
      () => {
        setRunningDigest(false);
        refresh();
      },
      (message) => {
        setRunningDigest(false);
        setError(
          `${message} — if this is your first run, it likely needs a live GROQ_API_KEY ` +
            "configured in backend/.env. See TESTING.md.",
        );
      },
    );
  }

  useEffect(() => () => closeStreamRef.current?.(), []); // close the stream if the page unmounts mid-run

  const latestInsightBySymbol = new Map<string, Insight>();
  for (const insight of history) {
    if (!latestInsightBySymbol.has(insight.symbol)) {
      latestInsightBySymbol.set(insight.symbol, insight);
    }
  }

  const needsAttention = (digest?.insights ?? []).filter(
    (i) => !i.action.toLowerCase().startsWith("hold"),
  );

  // Needs Attention cards only carry the LLM's free-form evidence dict (keys
  // vary insight to insight), so the gauge/badge there are sourced from this
  // structured snapshot lookup instead — built from the same holdings +
  // wishlist data already fetched, since digest only ever covers those symbols.
  const snapshotBySymbol = new Map<string, StockSnapshot | null | undefined>();
  for (const h of holdings) snapshotBySymbol.set(h.symbol, h.snapshot);
  for (const w of wishlist) snapshotBySymbol.set(w.symbol, w.snapshot);

  const TRACKER_PAGE_SIZE = 10;
  const trackerPageCount = Math.max(1, Math.ceil(history.length / TRACKER_PAGE_SIZE));
  const trackerPageClamped = Math.min(trackerPage, trackerPageCount);
  const trackerRows = history.slice(
    (trackerPageClamped - 1) * TRACKER_PAGE_SIZE,
    trackerPageClamped * TRACKER_PAGE_SIZE,
  );

  return (
    <div className="page">
      <div className="market-strip">
        <span>
          Data as of: <strong>{digest?.trade_date ?? "not checked yet"}</strong>
        </span>
        <span>
          {needsAttention.length} item{needsAttention.length === 1 ? "" : "s"} need attention
        </span>
        <button onClick={handleRunDigest} disabled={runningDigest}>
          {runningDigest ? "Checking…" : "Check my stocks now"}
        </button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {runningDigest && <DigestProgress log={progressLog} />}

      {!loading && <ActionSummaryStrip insights={digest?.insights ?? []} />}

      <Glossary />

      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
          <section className="card">
            <h3>Needs attention</h3>
            {needsAttention.length === 0 ? (
              <p className="muted">Nothing crossed a threshold today — that's a valid, valuable result.</p>
            ) : (
              <div className="attention-cards">
                {needsAttention.map((insight) => {
                  const snapshot = snapshotBySymbol.get(insight.symbol);
                  return (
                    <div key={insight.id} className="attention-card">
                      <div className="attention-card-header">
                        <span className="symbol">{insight.symbol}</span>
                        <ActionBadge action={insight.action} confidence={insight.confidence} />
                      </div>
                      {snapshot && (
                        <div className="attention-card-glance">
                          <PriceChangeBadge close={snapshot.close} prevClose={snapshot.prev_close} />
                          <DeliveryGauge today={snapshot.deliv_pct} baseline={snapshot.deliv_pct_20d_avg} />
                        </div>
                      )}
                      <p>{insight.narrative}</p>
                      <details>
                        <summary className="muted">Evidence</summary>
                        <EvidenceList evidence={insight.evidence} />
                      </details>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <div className="two-column">
            <section className="card">
              <h3>Holdings</h3>
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Size</th>
                    <th>Close</th>
                    <th>Change</th>
                    <th>Deliv% (vs 20d)</th>
                    <th>Latest signal</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h) => {
                    const latest = latestInsightBySymbol.get(h.symbol);
                    return (
                      <tr key={h.symbol}>
                        <td className="symbol">{h.symbol}</td>
                        <td>{h.size_bucket}</td>
                        <td>{h.snapshot ? h.snapshot.close.toFixed(2) : "—"}</td>
                        <td>
                          <PriceChangeBadge
                            close={h.snapshot?.close ?? null}
                            prevClose={h.snapshot?.prev_close ?? null}
                          />
                        </td>
                        <td>
                          <DeliveryGauge
                            today={h.snapshot?.deliv_pct ?? null}
                            baseline={h.snapshot?.deliv_pct_20d_avg ?? null}
                          />
                        </td>
                        <td>{latest ? <ActionBadge action={latest.action} compact /> : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>

            <section className="card">
              <h3>Wishlist</h3>
              <ul className="entity-list">
                {wishlist.map((w) => {
                  const latest = latestInsightBySymbol.get(w.symbol);
                  return (
                    <li key={w.symbol}>
                      <span className="symbol">{w.symbol}</span>
                      {w.snapshot && (
                        <DeliveryGauge today={w.snapshot.deliv_pct} baseline={w.snapshot.deliv_pct_20d_avg} />
                      )}
                      {latest && <ActionBadge action={latest.action} compact />}
                    </li>
                  );
                })}
                {wishlist.length === 0 && <p className="muted">Nothing on the wishlist.</p>}
              </ul>
            </section>
          </div>

          <section className="card">
            <h3>Insight tracker</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              The historical log, with the reasoning behind each call — not just today's
              snapshot (Needs attention) or a glance-badge (Holdings).
            </p>
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol</th>
                  <th>Signal</th>
                  <th>Why</th>
                  <th>Status</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {trackerRows.map((insight) => (
                  <tr key={insight.id}>
                    <td>{insight.trade_date}</td>
                    <td className="symbol">{insight.symbol}</td>
                    <td>
                      <ActionBadge action={insight.action} compact />
                    </td>
                    <td className="narrative-cell">{insight.narrative}</td>
                    <td>
                      <span className={`status-pill status-${insight.status}`}>{insight.status}</span>
                    </td>
                    <td className={outcomeClass(insight.outcome_pct)}>
                      {insight.outcome_pct != null ? `${insight.outcome_pct > 0 ? "+" : ""}${insight.outcome_pct.toFixed(2)}%` : "—"}
                    </td>
                  </tr>
                ))}
                {history.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Nothing checked yet — click "Check my stocks now" above to get started.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            {history.length > TRACKER_PAGE_SIZE && (
              <div className="tracker-pagination">
                <button
                  className="link-button"
                  onClick={() => setTrackerPage((p) => Math.max(1, p - 1))}
                  disabled={trackerPageClamped === 1}
                >
                  ← Newer
                </button>
                <span className="muted">
                  Page {trackerPageClamped} of {trackerPageCount}
                </span>
                <button
                  className="link-button"
                  onClick={() => setTrackerPage((p) => Math.min(trackerPageCount, p + 1))}
                  disabled={trackerPageClamped === trackerPageCount}
                >
                  Older →
                </button>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function outcomeClass(outcomePct: number | null): string {
  if (outcomePct == null) return "muted";
  return outcomePct > 0 ? "outcome-positive" : outcomePct < 0 ? "outcome-negative" : "";
}

function DigestProgress({ log }: { log: string[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [log]);

  return (
    <section className="card digest-progress">
      <h3>Checking your stocks…</h3>
      <div className="digest-progress-log">
        {log.length === 0 && <p className="muted">Connecting…</p>}
        {log.map((line, i) => (
          <p key={i}>{line}</p>
        ))}
        <div ref={bottomRef} />
      </div>
    </section>
  );
}

// Evidence keys are whatever the model chose to cite (InsightOutput.evidence has no
// fixed schema — see agent/digest_graph.py) — so the unit has to be inferred from the
// key name rather than looked up. Keyword-based, not exhaustive; covers the categories
// that actually show up in practice (delivery/confidence percentages, OHLC/VWAP/strike
// prices, volume/OI/quantity counts, unitless ratios like PCR).
function formatEvidenceValue(key: string, value: number): string {
  const k = key.toLowerCase();
  if (k.includes("pct") || k.includes("percent")) {
    return `${value.toFixed(2)}%`;
  }
  if (k.includes("pcr") || k.includes("ratio")) {
    return value.toFixed(2);
  }
  if (
    k.includes("price") ||
    k.includes("close") ||
    k.includes("open") ||
    k.includes("high") ||
    k.includes("low") ||
    k.includes("vwap") ||
    k.includes("strike") ||
    k.includes("basis")
  ) {
    return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  }
  if (
    k.includes("qty") ||
    k.includes("quantity") ||
    k.includes("volume") ||
    k.includes("shares") ||
    k.includes("oi") ||
    k.includes("size")
  ) {
    return value.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function EvidenceList({ evidence }: { evidence: Record<string, number> }) {
  return (
    <ul className="evidence-list">
      {Object.entries(evidence).map(([key, value]) => (
        <li key={key}>
          {key.replaceAll("_", " ")}: <strong>{formatEvidenceValue(key, value)}</strong>
        </li>
      ))}
    </ul>
  );
}
