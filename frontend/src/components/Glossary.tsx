const ACTIONS: [string, string][] = [
  ["Hold, no action", "The default, most days — nothing crossed a threshold worth acting on."],
  ["Review the position", "Thesis re-check — the signal is ambiguous or data is incomplete; look closer yourself."],
  ["Stagger an entry / add on dip", "Durable accumulation — consider adding gradually, not all at once."],
  ["Defer entry", "Timing looks crowded or leveraged — wait rather than chase."],
  ["Trim into strength", "Distribution while price is still up — consider reducing while it's not obvious yet."],
  ["Tighten exit discipline", "Liquidity or series-change risk — decide your exit level now, before it's forced."],
  ["Add to watchlist", "Durable accumulation pattern in a stock you don't hold — worth fundamental research."],
];

const CONFIDENCE: [string, string][] = [
  ["high", "Evidence is clear-cut and the signal is strong relative to baseline."],
  ["medium", "Directionally clear, but the evidence is partial or the move is moderate."],
  ["low", "Data is thin or the signal is ambiguous — treat as informational, not a strong call."],
];

const STATUS: [string, string][] = [
  ["pending", "Just generated — not yet compared against what happened afterward."],
  ["strengthening", "Later sessions have reinforced the original signal."],
  ["confirmed", "The signal played out as expected over the following sessions."],
  ["expired", "Enough time passed without the signal resolving either way."],
];

export function Glossary() {
  return (
    <details className="card glossary">
      <summary>What do these mean?</summary>
      <div className="glossary-body">
        <div>
          <h4>Action</h4>
          <dl>
            {ACTIONS.map(([term, desc]) => (
              <div key={term} className="glossary-entry">
                <dt>{term}</dt>
                <dd>{desc}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div>
          <h4>Confidence</h4>
          <dl>
            {CONFIDENCE.map(([term, desc]) => (
              <div key={term} className="glossary-entry">
                <dt>{term}</dt>
                <dd>{desc}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div>
          <h4>Status (Insight tracker)</h4>
          <p className="muted" style={{ marginTop: 0 }}>
            Every new insight starts as "pending." Each time the digest runs, it first
            re-checks prior pending/strengthening insights against fresh price data (no
            model involved — pure arithmetic) and updates status + the "Outcome" column
            accordingly. A fresh insight needs a few sessions of new data before it can
            resolve, so it may sit at "pending" for a bit — that's expected, not stuck.
          </p>
          <dl>
            {STATUS.map(([term, desc]) => (
              <div key={term} className="glossary-entry">
                <dt>{term}</dt>
                <dd>{desc}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </details>
  );
}
