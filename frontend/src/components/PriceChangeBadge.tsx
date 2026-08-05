// Direction + magnitude of today's price move, at a glance — an arrow and a
// colored percentage instead of having to find "close X, prev close Y"
// inside a paragraph and do the subtraction yourself.
export function PriceChangeBadge({ close, prevClose }: { close: number | null; prevClose: number | null }) {
  if (close == null || prevClose == null || prevClose === 0) {
    return <span className="muted">—</span>;
  }

  const pct = ((close - prevClose) / prevClose) * 100;
  const flat = Math.abs(pct) < 0.005;
  const up = pct > 0;

  return (
    <span className={`price-change ${flat ? "" : up ? "price-change-up" : "price-change-down"}`}>
      {flat ? "→" : up ? "▲" : "▼"} {Math.abs(pct).toFixed(2)}%
    </span>
  );
}
