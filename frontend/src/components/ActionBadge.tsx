import type { Confidence } from "../types";

const ACTION_TONE: Record<string, string> = {
  "Hold, no action": "tone-neutral",
  "Review the position": "tone-warning",
  "Stagger an entry / add on dip": "tone-positive",
  "Defer entry": "tone-warning",
  "Trim into strength": "tone-warning",
  "Tighten exit discipline": "tone-danger",
  "Add to watchlist": "tone-positive",
};

export function toneFor(action: string): string {
  const match = Object.keys(ACTION_TONE).find((key) => action.toLowerCase().includes(key.toLowerCase()));
  return match ? ACTION_TONE[match] : "tone-neutral";
}

export function ActionBadge({
  action,
  confidence,
  compact,
}: {
  action: string;
  confidence?: Confidence;
  /** Omits the confidence suffix — for dense contexts (tables) that already
   * show full detail (action + confidence + narrative) elsewhere, e.g. the
   * Needs Attention cards or Insight Tracker's own row. */
  compact?: boolean;
}) {
  return (
    <span className={`badge ${toneFor(action)}`}>
      {action}
      {confidence && !compact && <span className="badge-confidence"> · {confidence}</span>}
    </span>
  );
}
