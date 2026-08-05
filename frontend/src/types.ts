// Mirrors backend/specs/08_fastapi_routes.md's response shapes exactly —
// keep these two in sync if the route contract changes.

export type SizeBucket = "small" | "medium" | "large";
export type WatchStatus = "held" | "wishlist";
export type Confidence = "low" | "medium" | "high";
export type InsightStatus = "pending" | "strengthening" | "confirmed" | "expired";

export interface WatchlistItem {
  user_id: string;
  symbol: string;
  status: WatchStatus;
  size_bucket: SizeBucket | null;
  added_at: string;
  // Present on GET /watchlist responses; absent on the POST/PATCH /watch
  // responses, which return the bare watchlist row only.
  snapshot?: StockSnapshot | null;
}

export interface StockSnapshot {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  prev_close: number | null;
  vwap: number | null;
  volume: number;
  turnover: number | null;
  deliv_qty: number | null;
  deliv_pct: number | null;
  series: string;
  corporate_action_flag: number;
  closing_price_method: string;
  volume_20d_avg: number | null;
  deliv_pct_20d_avg: number | null;
  turnover_rank_today: number | null;
  error?: string;
}

export interface Holding extends WatchlistItem {
  snapshot: StockSnapshot | null;
}

export interface Insight {
  id: number;
  user_id: string;
  symbol: string;
  trade_date: string;
  signal_type: string;
  action: string;
  confidence: Confidence;
  narrative: string;
  evidence: Record<string, number>;
  status: InsightStatus;
  price_at_insight: number;
  outcome_pct: number | null;
  created_at: string;
  resolved_at: string | null;
}

export interface DigestToday {
  trade_date: string | null;
  insights: Insight[];
}

export interface SymbolSearchResult {
  symbol: string;
  name: string;
  series: string;
}

export type IngestFileType = "cash" | "delivery" | "fo";

export interface IngestLink {
  file_type: IngestFileType;
  label: string;
  direct_url: string;
  hub_url: string;
  expected_filename_hint: string;
}

export interface FileCheckResult {
  label: string;
  matched_path: string | null;
  candidates: string[];
}

export type DownloadsCheckResponse = Record<IngestFileType, FileCheckResult>;

export interface IngestRunResult {
  trade_date: string;
  status: "ok" | "skipped_non_trading_day" | "skipped_already_ingested" | "failed";
  symbols_loaded: number;
  corporate_actions_flagged: string[];
  error: string | null;
}
