import { useEffect, useState } from "react";
import { api } from "../api";
import type { SizeBucket, SymbolSearchResult, WatchlistItem, WatchStatus } from "../types";

export function ManageScreen() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolSearchResult[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<SymbolSearchResult | null>(null);
  const [addStatus, setAddStatus] = useState<WatchStatus>("wishlist");
  const [addBucket, setAddBucket] = useState<SizeBucket>("medium");

  // Which wishlist row is mid-promotion — shows an inline bucket picker in
  // place of the "I bought it" button rather than a window.prompt() (blocks
  // the main thread, can't be styled, and doesn't work in embedded/test
  // contexts).
  const [promotingSymbol, setPromotingSymbol] = useState<string | null>(null);
  const [promoteBucket, setPromoteBucket] = useState<SizeBucket>("medium");

  const refresh = () => {
    setLoading(true);
    api
      .getWatchlist()
      .then(setWatchlist)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  // Debounced symbol autocomplete.
  useEffect(() => {
    if (!query) {
      setResults([]);
      return;
    }
    const handle = setTimeout(() => {
      api.searchSymbols(query).then(setResults).catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [query]);

  async function handleAdd() {
    if (!selectedSymbol) return;
    setError(null);
    try {
      await api.addWatch({
        symbol: selectedSymbol.symbol,
        status: addStatus,
        size_bucket: addStatus === "held" ? addBucket : null,
      });
      setSelectedSymbol(null);
      setQuery("");
      setResults([]);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleConfirmPromote(symbol: string) {
    setError(null);
    try {
      await api.updateWatch(symbol, { status: "held", size_bucket: promoteBucket });
      setPromotingSymbol(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleRemove(symbol: string) {
    setError(null);
    try {
      await api.removeWatch(symbol);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const held = watchlist.filter((w) => w.status === "held");
  const wishlist = watchlist.filter((w) => w.status === "wishlist");

  return (
    <div className="page">
      <h2>Manage symbols</h2>
      {error && <div className="banner banner-error">{error}</div>}

      <section className="card">
        <h3>Add a symbol</h3>
        <div className="add-row">
          <div className="autocomplete">
            <input
              placeholder="Search by symbol or name…"
              value={selectedSymbol ? selectedSymbol.symbol : query}
              onChange={(e) => {
                setSelectedSymbol(null);
                setQuery(e.target.value);
              }}
            />
            {results.length > 0 && !selectedSymbol && (
              <ul className="autocomplete-results">
                {results.map((r) => (
                  <li key={r.symbol} onClick={() => setSelectedSymbol(r)}>
                    <strong>{r.symbol}</strong> — {r.name}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <select value={addStatus} onChange={(e) => setAddStatus(e.target.value as WatchStatus)}>
            <option value="wishlist">Wishlist</option>
            <option value="held">Held</option>
          </select>

          {addStatus === "held" && (
            <select value={addBucket} onChange={(e) => setAddBucket(e.target.value as SizeBucket)}>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Large</option>
            </select>
          )}

          <button onClick={handleAdd} disabled={!selectedSymbol}>
            Add
          </button>
        </div>
      </section>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <div className="two-column">
          <section className="card">
            <h3>Held ({held.length})</h3>
            <ul className="entity-list">
              {held.map((item) => (
                <li key={item.symbol}>
                  <span className="symbol">{item.symbol}</span>
                  <span className="tag">{item.size_bucket}</span>
                  <button className="link-button danger" onClick={() => handleRemove(item.symbol)}>
                    Remove
                  </button>
                </li>
              ))}
              {held.length === 0 && <p className="muted">Nothing held yet.</p>}
            </ul>
          </section>

          <section className="card">
            <h3>Wishlist ({wishlist.length})</h3>
            <ul className="entity-list">
              {wishlist.map((item) =>
                promotingSymbol === item.symbol ? (
                  <li key={item.symbol}>
                    <span className="symbol">{item.symbol}</span>
                    <select value={promoteBucket} onChange={(e) => setPromoteBucket(e.target.value as SizeBucket)}>
                      <option value="small">Small</option>
                      <option value="medium">Medium</option>
                      <option value="large">Large</option>
                    </select>
                    <button className="link-button" onClick={() => handleConfirmPromote(item.symbol)}>
                      Confirm
                    </button>
                    <button className="link-button" onClick={() => setPromotingSymbol(null)}>
                      Cancel
                    </button>
                  </li>
                ) : (
                  <li key={item.symbol}>
                    <span className="symbol">{item.symbol}</span>
                    <button
                      className="link-button"
                      onClick={() => {
                        setPromotingSymbol(item.symbol);
                        setPromoteBucket("medium");
                      }}
                    >
                      I bought it
                    </button>
                    <button className="link-button danger" onClick={() => handleRemove(item.symbol)}>
                      Remove
                    </button>
                  </li>
                ),
              )}
              {wishlist.length === 0 && <p className="muted">Nothing on the wishlist yet.</p>}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}
