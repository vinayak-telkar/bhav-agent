# PRD — Bhavcopy Flow Agent (working title)

**Status:** Draft for review · **Author:** capstone team · **Date:** 2026-07-21

---

## 1. Problem

Retail investors in Indian equities cannot tell the difference between a stock that is
*busy* and a stock that is *being accumulated*. Price and volume alone are misleading;
the signal that separates genuine buying from intraday churn — delivery percentage, and
the flow footprints around it — is buried in the NSE end-of-day bhavcopy, which no retail
investor reads daily. As a result they act late (buy leveraged rallies, miss quiet
accumulation) and get no early warning when large holders start exiting.

## 2. What we're building

An **autonomous analyst** that reads the NSE bhavcopy every evening, checks only the
stocks a user holds or watches, and reports — in plain English — what the flow data says
and what the sensible next step is. It reports proactively via a dashboard and can be
interrogated on demand via chat. It **informs; the human decides**.

Explicitly *not* an advisory product. Every output is data-driven information framed as a
"weather report," with a standing disclaimer. (This is also the correct posture w.r.t.
SEBI's line between information and regulated investment advice.)

## 3. Users & the privacy contract

One user type: the **cash-market retail investor**. We collect the absolute minimum:

- A list of held symbols, each with a **size bucket** (small / medium / large) — never
  quantity or value.
- A list of wishlist symbols.
- A random `user_id`. **No name, email, phone, PAN, or broker login — ever.** Symbols are
  typed in manually; there is no broker integration by design.

F&O is an *input data source* (market-wide open-interest sentiment), **not** a user
segment — we never ask whether the user holds derivatives.

## 4. The insight model

The agent's every output resolves to exactly one of seven **next actions**:

1. Hold, no action (the default, most days)
2. Review the position (thesis re-check)
3. Stagger an entry / add on dip toward a zone
4. Defer entry (timing crowded/leveraged)
5. Trim into strength (distribution while price still up)
6. Tighten exit discipline (liquidity/series-change risk)
7. Add to watchlist (durable accumulation; do fundamental work)

Signal → action mapping (sustained signals, not one day):

| Bhavcopy signal | Insight | Action |
|---|---|---|
| Price↑, delivery% rising, deliv-qty rising | Genuine accumulation | Hold; add only near max-put-OI zone |
| Price↑, delivery% falling, futures long-OI ballooning, steep basis | Leveraged rally | Defer entry; trim if overweight |
| Price flat/↓, delivery% high & rising, volume elevated | Hand-change near base OR large holder exiting | Review; resolve over ~5 sessions |
| Price↓, short-OI buildup, basis at discount | Leveraged money positioned against | Review now, before the chart confirms |
| Heavy call writing overhead, put support below | Upside capped near-term | Hold; defer adds |
| Volume spike, low delivery, no news, small/mid cap | Speculative churn | Do nothing on "opportunity"; tighten exit if held |
| Move to BE/T2T series, repeated circuits | Exit liquidity deteriorating | Tighten exit discipline — set level now |
| OI unwinding while price flat | Positional support leaving | Downgrade conviction; review |

**Three design principles that make insights trustworthy:**
- **Portfolio-relative, not market-wide.** Rank by signal delta vs each stock's own
  ~20-day baseline; surface only threshold-crossers. "Nothing changed" is a valid,
  valuable daily output.
- **Every insight carries evidence + confidence.** "Delivery 68% vs 44% baseline over 8
  sessions while price rose 6% — accumulation, high confidence." Never a bare label.
  Ambiguous signatures are *labeled* ambiguous with the tie-breaker stated. This principle
  extends in kind to data-mechanism ambiguity, not just signal ambiguity, if it were ever
  relevant — v1 doesn't currently hit this case in practice (§9's note on SEBI's Closing
  Auction Session), since data collection starts fresh from the current regime rather than
  reconciling across a mechanism change, but the principle is general: any known
  discontinuity in how the underlying data itself is produced would be confidence-
  downgraded and stated, the same way a genuinely ambiguous price/delivery signature is.
- **Horizon translation is explicit.** Each insight states what it affects — entry timing,
  exit discipline, or thesis health — never "long-term worth," which this data cannot judge.

## 5. Product surfaces

1. **Manage screen** (also first-run onboarding): add/remove symbols, set size bucket,
   symbol autocomplete served from our own ingested symbol table. "I bought it" promotes a
   wishlist item to a holding.
2. **Daily digest dashboard** (read-only): market-context strip, "Needs attention" alert
   cards, holdings table with flow signal badges, wishlist panel, and an **insight tracker**
   showing past calls with outcomes (pending / strengthening / confirmed / expired).
3. **Chat** (stretch, dual-model): interrogate the digest — "why did you flag X?" — over the
   same MCP tools, restricted to read-only lookups (no write access to insights from chat).
   Chat runs on a free-tier model (see §9), not the model used for digest generation — this
   is a cost/latency choice for a high-volume, low-stakes surface, not a quality regression
   we're willing to accept silently. The rule in §4 ("evidence + confidence, never a bare
   label") and the anti-fabrication rule in §8 apply identically to chat; a cheaper model
   answering more casually is not an excuse to relax either.

## 6. Success criteria

- **MVP demo:** ingest a real day's cash bhavcopy → generate an evidence-backed digest for
  a demo portfolio → render the dashboard, with at least the "accumulation," "leveraged
  rally," and "quiet exit" signatures firing correctly on real data.
- **Agentic proof:** the agent conditionally calls deeper tools (delivery trend, F&O) only
  where a stock looks off, and reconciles against prior insights — visible in logs.
- **Capstone stretch / evaluation section — backtest:** replay ~6 months of historical
  bhavcopies and measure whether "quiet exit" warnings preceded price drops (e.g. average
  N-session forward return after a flagged distribution signal). Turns "the agent says
  things" into "the agent's warnings were followed by X."

## 7. Model strategy

**Free inference only, everywhere.** No paid API dependency anywhere in the runtime
stack — a deliberate choice so anyone can clone the project from GitHub, get a free API
key, and run it end-to-end at zero cost. This shapes the model strategy more than raw
capability does: the two agents split by role, not by a cost tier one of them is exempt
from.

- **Digest generation (Mode 1 — the analyst)** defaults to the stronger of the project's
  two free-tier options (`gpt-oss-120b` on Groq). This is the high-stakes path: it
  originates every insight that gets persisted and shown as the product's judgment,
  resolves ambiguous multi-signal signatures against the table in §4, and is the surface
  a demo/reviewer judges the product's reasoning quality by. It gets the stronger default
  model precisely because there's no other lever (like falling back to a paid frontier
  model) to lean on if it underperforms.
- **Chat (Mode 2 — interrogation)** defaults to a lighter, more rate-limit-stable free
  model (`llama-3.3-70b-versatile`). Chat is asked *about* insights the digest already
  produced and persisted — it is not the origin of new judgment calls, so the bar is
  "faithfully explain and quote what's already there," not "originate a novel read of
  ambiguous data." That's a materially easier job than the digest's, which is what
  justifies the lighter default without compromising the product's core promise.
- **Both agents are implemented as LangGraph graphs sharing one MCP integration pattern**
  (`langchain-mcp-adapters` bridges the same tool server into both) — the digest as an
  explicit state graph that structurally enforces *when* deeper tools get called, chat as
  a prebuilt reasoning loop plus one added verification step. Swapping either agent's
  model or provider (Groq ↔ Cerebras) is a config change, not a code change.
- **No trust asymmetry between the two paths — both get the same anti-fabrication
  guardrail.** The model never states a number it didn't get from a tool call (§9's
  anti-fabrication mitigation), and both agents independently verify post-hoc that every
  number in their output traces back to that turn's tool calls before it's shown to the
  user or persisted; anything that doesn't trace back is stripped or replaced with a
  templated answer built directly from tool data. There is no longer a "cheaper path" to
  treat more permissively — both agents run on free, open-weight models with no stronger
  fallback behind either of them, so both carry the same check.
- **Model names are configuration, not a fixed choice.** Free-tier model catalogs change
  over time — a model available today may be renamed or retired by the time someone else
  clones this repo. The README documents known-good options with a last-verified date and
  points to each provider's live model list rather than presenting today's choice as
  permanent.

## 8. Explicit non-goals (v1)

- No buy/sell execution, no broker connectivity, no portfolio valuation.
- No F&O user features (F&O is a data input only).
- No multi-user auth or accounts.
- No intraday/real-time data — strictly end-of-day.
- No price-adjustment for corporate actions — detect and flag only.

## 9. Key risks

| Risk | Mitigation |
|---|---|
| NSE data format/access (UDiFF, delivery file, anti-scraping) | Milestone 0 ingest spike before anything else |
| Corporate actions corrupt volume/delivery baselines | Detect splits/bonuses and suppress/flag affected signals |
| Model fabricates numbers | Tools compute all numbers; agent quotes tool output, never estimates |
| Holidays / missing files | Ingest is idempotent, skips non-trading days, backfills gaps |
| SEBI's Closing Auction Session (CAS), live 2026-08-03, changes closing-price computation for F&O-eligible (Category I) stocks only | Not an active risk for v1: ingest starts fresh from the new regime rather than reconciling pre/post-cutover data, so mixed-baseline handling never actually triggers. `closing_price_method` recorded per day for completeness/future-proofing (tech spec §3), not actively used. Only resurfaces if the M4 backtest window is later extended close enough to the cutover date to cross it. |
| A free-tier/open-weight model (either agent) drifts off a quoted number, or rate-limits mid-demo | Post-hoc numeric grounding check on every response, both agents, no exceptions (§7); automatic fallback from Groq to Cerebras on rate limit; chat additionally scoped read-only and treated as the first thing cut if unstable |
| Free-tier model catalogs and rate limits change over time (models renamed, retired, or added between one clone of this repo and the next) | Model names live in `.env` config, never hardcoded; README documents known-good options with a last-verified date and points to each provider's live model list rather than presenting today's choice as permanent (tech spec §11) |
| LangChain/LangGraph/langchain-mcp-adapters API surface is newer and faster-moving than the raw provider SDKs it replaces | Pin exact versions day 1, don't bump mid-week; verify real import paths/signatures in a day-1 spike before either agent is built on top of them (tech spec §8) |
