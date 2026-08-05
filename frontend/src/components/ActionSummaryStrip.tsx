import type { Insight } from "../types";
import { toneFor } from "./ActionBadge";

// A stacked bar + legend showing today's action mix across everything
// checked (held + wishlist) — the overall read on portfolio health before
// opening a single card, reusing ActionBadge's tone colors so the strip and
// the badges agree visually.
export function ActionSummaryStrip({ insights }: { insights: Insight[] }) {
  if (insights.length === 0) {
    return null;
  }

  const counts = new Map<string, number>();
  for (const insight of insights) {
    counts.set(insight.action, (counts.get(insight.action) ?? 0) + 1);
  }
  const total = insights.length;
  const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  return (
    <div className="action-summary">
      <div className="action-summary-bar">
        {entries.map(([action, count]) => (
          <div
            key={action}
            className={`action-summary-segment ${toneFor(action)}`}
            style={{ width: `${(count / total) * 100}%` }}
            title={`${action}: ${count} of ${total}`}
          />
        ))}
      </div>
      <div className="action-summary-legend">
        {entries.map(([action, count]) => (
          <span key={action} className="action-summary-legend-item">
            <span className={`action-summary-swatch ${toneFor(action)}`} />
            {action} ({count})
          </span>
        ))}
      </div>
    </div>
  );
}
