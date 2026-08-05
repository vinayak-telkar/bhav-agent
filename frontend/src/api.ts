import type {
  DigestToday,
  DownloadsCheckResponse,
  Holding,
  IngestLink,
  IngestRunResult,
  Insight,
  SizeBucket,
  SymbolSearchResult,
  WatchlistItem,
  WatchStatus,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed with status ${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export const api = {
  searchSymbols: (q: string) =>
    request<SymbolSearchResult[]>(`/symbols?q=${encodeURIComponent(q)}`),

  getWatchlist: (status?: WatchStatus) =>
    request<WatchlistItem[]>(`/watchlist${status ? `?status=${status}` : ""}`),

  addWatch: (body: { symbol: string; status: WatchStatus; size_bucket?: SizeBucket | null }) =>
    request<WatchlistItem>("/watch", { method: "POST", body: JSON.stringify(body) }),

  updateWatch: (symbol: string, body: { status?: WatchStatus; size_bucket?: SizeBucket | null }) =>
    request<WatchlistItem>(`/watch/${symbol}`, { method: "PATCH", body: JSON.stringify(body) }),

  removeWatch: (symbol: string) =>
    request<void>(`/watch/${symbol}`, { method: "DELETE" }),

  getHoldings: () => request<Holding[]>("/holdings"),

  getDigestToday: () => request<DigestToday>("/digest/today"),

  getInsightsHistory: (symbol?: string) =>
    request<Insight[]>(`/insights/history${symbol ? `?symbol=${symbol}` : ""}`),

  runDigest: () => request<Insight[]>("/digest/run", { method: "POST" }),

  /** Live progress via Server-Sent Events (GET /digest/run-stream) — one
   * human-readable status line per graph step, not a blank spinner for
   * however long a multi-symbol run takes. Returns a cleanup function that
   * closes the connection (call it on unmount / when a new run starts). */
  streamDigest: (
    onMessage: (message: string) => void,
    onDone: () => void,
    onError: (message: string) => void,
  ) => {
    const es = new EventSource(`${BASE_URL}/digest/run-stream`);
    es.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.message) {
        onMessage(data.message);
      } else if (data.done) {
        es.close();
        onDone();
      } else if (data.error) {
        es.close();
        onError(data.error);
      }
    };
    es.onerror = () => {
      es.close();
      onError("Lost connection to the digest stream.");
    };
    return () => es.close();
  },

  getIngestLinks: (tradeDate: string) =>
    request<IngestLink[]>(`/ingest/links?trade_date=${tradeDate}`),

  checkDownloads: (tradeDate: string, downloadsDir?: string) =>
    request<DownloadsCheckResponse>("/ingest/check", {
      method: "POST",
      body: JSON.stringify({ trade_date: tradeDate, downloads_dir: downloadsDir ?? null }),
    }),

  runLocalIngest: (tradeDate: string, cashFile: string, deliveryFile?: string, foFile?: string) =>
    request<IngestRunResult>("/ingest/run-local", {
      method: "POST",
      body: JSON.stringify({
        trade_date: tradeDate,
        cash_file: cashFile,
        delivery_file: deliveryFile ?? null,
        fo_file: foFile ?? null,
      }),
    }),

  /** Live progress via Server-Sent Events (GET /chat/stream) — same UX
   * pattern as streamDigest: {"progress": "..."} lines while the agent
   * reasons/calls tools, then a final {"answer": "..."}. Returns a cleanup
   * function that closes the connection. */
  streamChat: (
    question: string,
    onProgress: (message: string) => void,
    onAnswer: (answer: string) => void,
    onError: (message: string) => void,
  ) => {
    const es = new EventSource(`${BASE_URL}/chat/stream?question=${encodeURIComponent(question)}`);
    es.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.progress) {
        onProgress(data.progress);
      } else if (data.answer) {
        es.close();
        onAnswer(data.answer);
      } else if (data.error) {
        es.close();
        onError(data.error);
      }
    };
    es.onerror = () => {
      es.close();
      onError("Lost connection to the chat stream.");
    };
    return () => es.close();
  },
};
