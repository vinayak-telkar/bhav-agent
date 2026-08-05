// A compact glance-able alternative to reading "64.1% (44.4%)" as text: a
// bar showing today's delivery% against the 20-day baseline as a marker
// line, colored by whether today's reading is above or below it — the
// single most-repeated comparison in every insight's narrative.
export function DeliveryGauge({ today, baseline }: { today: number | null; baseline: number | null }) {
  if (today == null) {
    return <span className="muted">—</span>;
  }

  const fillPct = Math.min(100, Math.max(0, today));
  const baselinePct = baseline != null ? Math.min(100, Math.max(0, baseline)) : null;
  const aboveBaseline = baseline != null && today >= baseline;

  const title =
    baseline != null
      ? `${today.toFixed(1)}% today vs ${baseline.toFixed(1)}% 20-day average`
      : `${today.toFixed(1)}% today (no 20-day average yet)`;

  return (
    <div className="gauge" title={title}>
      <div className="gauge-track">
        <div
          className={`gauge-fill ${aboveBaseline ? "gauge-fill-up" : "gauge-fill-down"}`}
          style={{ width: `${fillPct}%` }}
        />
        {baselinePct != null && <div className="gauge-baseline" style={{ left: `${baselinePct}%` }} />}
      </div>
      <span className="gauge-label">{today.toFixed(1)}%</span>
    </div>
  );
}
