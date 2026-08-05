# Spec: Digest graph (`agent/digest_graph.py`) + system prompt (`agent/prompts.py`)

**Owner:** implementation team · **Milestone:** M2 · **Status:** done
**Depends on:** 02 (LangGraph/MCP spike), 06 (MCP server)
**Consumed by:** 08 (FastAPI `/digest/today` reads persisted output), scheduler

## Purpose
Runs the daily analyst: for each of a user's held + wishlist symbols, decide whether to
drill down, synthesize an evidence-backed insight via LLM, verify it's grounded in this
turn's tool output, and persist it (or a deterministic template fallback).

## Interface / contract
```python
async def run_daily_digest(user_id: str, db_path: str | None = None) -> list[dict]:
    """Returns one summary dict per symbol processed:
    {"symbol", "insight_id", "signal_type", "action", "confidence", "narrative", "grounded"}"""

async def stream_daily_digest(user_id: str, db_path: str | None = None):
    """Same run, but an async generator yielding a human-readable progress
    string after each graph step (see _progress_message) instead of only the
    final result — powers GET /digest/run-stream (SSE) for the dashboard's
    live progress view. Added in response to user feedback: a blocking call
    with no visibility into multi-symbol runs read as a dead spinner, and
    raw MCP 'ListToolsRequest'/'CallToolRequest' log lines (printed by the
    mcp SDK to the MCP subprocess's inherited stderr, not sent to the
    frontend) aren't meaningful progress to a user watching the console."""

def build_digest_graph(tools: dict[str, BaseTool], llm: BaseChatModel) -> CompiledStateGraph:
    """Dependency-injected graph builder — tests pass a fake LLM double against
    real MCP tools; run_daily_digest/stream_daily_digest wire up the real
    ChatGroq/ChatCerebras pair via the shared _compile_graph_for_run() helper."""
```

**Graph shape** (implementation detail vs. tech spec §6's diagram): processes the
portfolio+wishlist as a queue with a loop-back edge (`save_insight`/`skip_no_data` ->
`snapshot_symbol`) rather than fanning out a subgraph per symbol via `Send`. Simpler to
read and log at this project's symbol counts; sequential, not parallel — revisit only if
a real portfolio's size makes that too slow.

```
fetch_portfolio -> [queue empty? END : snapshot_symbol]
snapshot_symbol -> [error? skip_no_data | looks_off? drill_down : compare_prior]
drill_down -> compare_prior -> write_insight -> verify_insight_grounded
verify_insight_grounded -> [grounded? save_insight : template_fallback -> save_insight]
save_insight / skip_no_data -> [queue empty? END : snapshot_symbol]
```

**"Looks off" thresholds** (`_looks_off`): corporate_action_flag=1, series != 'EQ', delivery%
deviating >15% (relative) from its 20-session baseline, or volume outside 0.6x-1.5x its
20-session baseline. Deliberately simple/tunable constants, not a statistical model —
matches the DAL's `_trend_label` philosophy (spec 04).

**Grounding check** (`_is_grounded`): every number in the LLM's `evidence` dict, plus every
decimal number (pattern `-?\d+\.\d+`) in its `narrative`, must be within tolerance of some
number appearing in this turn's snapshot/drill-down/prior-insights tool output. Integer
numbers (session counts, "7 actions") are deliberately excluded from narrative scanning —
they're structural language, not cited figures. This is a real, working simplification of
tech spec §6/§6b's grounding requirement, not the full requirement verbatim — documented
here since it's a deliberate v1 scope decision, not an oversight.

## Acceptance criteria
- [x] Full demo portfolio (3 held + 2 wishlist) processed end to end against the MCP
      server + seeded DB, using a fake LLM double (no live API key available in this
      build environment — see specs/02's Changelog) — `tests/test_digest_graph.py`.
- [x] DEMOACCUM (accumulation) and DEMORALLY (leveraged rally) both correctly trigger
      `drill_down` via the delivery-deviation threshold.
- [x] A deliberately fabricated evidence number is caught by `verify_insight_grounded`
      and routed to `template_fallback`, which saves a deterministic, tool-data-only
      insight instead of the ungrounded LLM prose.
- [x] `save_insight` always receives `trade_date`/`price_at_insight` from the current
      symbol's own snapshot (not a global "today"), since different symbols can in
      principle have different latest trade dates.
- [x] **Live Groq call verified** (2026-08-03, real `GROQ_API_KEY`, real portfolio of
      ingested NSE symbols) — see Changelog for two real bugs this surfaced and fixed.

## Out of scope
- Parallel/fan-out symbol processing (`Send` API) — sequential queue is sufficient at
  demo scale; see the graph-shape note above.
- Chat's read-only tool allowlist / `verify_grounded` node — separate agent, out of
  scope this iteration (spec 12).

## Assumptions / open questions
- `_looks_off` thresholds (15% delivery deviation, 0.6x-1.5x volume band) are a
  reasonable starting point tuned against the synthetic seed dataset, not validated
  against real NSE data distributions — revisit once real ingest is verified (spec 01).

## Changelog
| Date | Change | Why |
|---|---|---|
| 2026-08-02 | Initial spec, written after implementation | M2 — written just-in-time per TECH_SPEC.md §10, once the queue-based graph shape and grounding-check scope were actually decided while building |
| 2026-08-03 | Live Groq run surfaced and fixed two real bugs, once a real `GROQ_API_KEY` was available: **(1)** `max_tokens=16000` (tech spec §6's original estimate) exceeded this account's actual Groq free-tier cap for `openai/gpt-oss-120b` (observed: 8000 TPM per request) — every single call failed outright with a 413, not just under load. Lowered to `max_tokens=2000` in `run_daily_digest` (`write_insight`'s actual output is one short `InsightOutput` object; 2000 is generous headroom, not tight). **(2)** Even at 2000, processing 5+ symbols back-to-back can exceed the *cumulative* per-minute cap (a 429, not a 413) — the Groq→Cerebras fallback (tech spec §6) only helps if a Cerebras key is configured, which isn't required by this project's design. Added `.with_retry(stop_after_attempt=5, wait_exponential_jitter=True)` around `structured_llm` instead: since TPM limits reset every minute, waiting out Groq's own suggested retry delay and retrying the same call succeeds without needing a second provider at all. Confirmed working end to end against a real ingested portfolio (13 real NSE symbols) after both fixes — some insights correctly went through grounded LLM prose, others correctly routed to `template_fallback` (deliv_pct genuinely missing for those symbols, so the model had nothing groundable to say — the guardrail did its job). |
| 2026-08-03 | User UX feedback, four items addressed: **(1)** `stream_daily_digest()` + `GET /digest/run-stream` (SSE) added — a blocking multi-symbol run with no progress visibility read as a dead spinner; also quieted the `mcp` SDK's per-call `"Processing request of type X"` INFO logging (`mcp_server/server.py`) since that was the actual noise the user was seeing in the backend console while waiting, easily mistaken for "the app's progress" when it isn't. **(2)** Found and fixed a real gap: `InsightOutput.narrative` (the plain-English "why" — the actual thing PRD §4 means by "never a bare label") was generated by the LLM every run but never persisted — `insights` table had no column for it. Added `narrative TEXT NOT NULL` to the schema, threaded through `save_insight` (DAL, MCP tool, digest graph). This was also the root cause of a separate complaint ("Needs Attention/Holdings/Insight Tracker all show the same repeated info") — without narrative, all three sections could only ever repeat the same three raw fields (action/confidence/signal_type); with it, each surface now shows something the others don't (Needs Attention: narrative + evidence; Holdings/Wishlist: compact badge only, via `ActionBadge`'s new `compact` prop; Insight Tracker: narrative as a "Why" column, status made the differentiator it was always meant to be). **(3)** Added a collapsible glossary (`frontend/src/components/Glossary.tsx`) explaining all 7 actions, 3 confidence levels, and 4 statuses — including an explicit note that `status` stays `'pending'` for every insight in this iteration by design (M4 resolution job out of scope), not a bug. |
| 2026-08-05 | User asked why a specific Needs Attention card showed the `template_fallback` ("could not be verified against tool data") message, which led to discovering `verify_insight_grounded`'s discarded narrative/evidence were never logged — added a `logger.warning(...)` there (model, action, confidence, narrative, evidence, and the reference-number set, all in one line) so a fallback is diagnosable after the fact instead of a dead end. That logging then surfaced a major systemic bug: **the grounding check's reference set was built only from raw tool-output numbers**, but models routinely (and correctly) cite *derived* figures in their narrative/evidence that never appear as literal fields — percent changes ("up 2.1%"), point differences ("up 19.68 points" of delivery%, "down 22.5 points" of price), ratios ("2.66x its average"), and million-abbreviated large counts ("52.7M" for a 6-7 digit volume/OI number). None of these are fabrications; all are correct arithmetic on real numbers from that turn's actual tool calls — but the "every cited number must match a raw tool number" check had no way to know that, and was discarding the vast majority of real insights over it (one full live run: essentially every symbol fell back). Fixed in stages, each confirmed against a live Groq run (`openai/gpt-oss-120b`) before moving to the next: **(1)** `_derived_percent_changes(snapshot)` — signed+unsigned percent-change for the close/prev_close, volume/volume_20d_avg, and deliv_pct/deliv_pct_20d_avg pairs. **(2)** A live re-run still had one failure (ASIANPAINT) even though its cited number was mathematically within tolerance of the derived reference — root cause was pure float64 noise at the tolerance boundary (`abs(1.25 - 1.2)` evaluates to `0.050000000000000044`, not `0.05`, tripping a `<= 0.05` check that should have passed). Fixed generically by adding a `+ 1e-9` epsilon to `_is_grounded`'s comparison rather than special-casing the one value. **(3)** A further live re-run surfaced the point-difference/ratio/millions-abbreviation gap described above (CELLO, LICI, PAYTM, PIDILITIND, RELIANCE, TVSMOTOR all fell back on exactly this). Added `_derived_point_differences` and `_derived_ratios` (same three snapshot pairs as `_derived_percent_changes`) plus `_scaled_millions` (applied to the *entire* unioned reference set — snapshot, drill-down, and prior-insight numbers alike — rather than named fields, since any sufficiently large raw count, including drill-down-only ones like `fut_oi_change`, is a candidate for "M" abbreviation in prose). One case in that same run (BANKBEES) correctly still fell back: the model stated "down 1.58%" but 1.58 was actually the raw rupee price difference mistaken for a percentage (true change was ‑0.26%) — a genuine model arithmetic error, not a grounding-check gap, so the guardrail catching it is working as intended. Six new unit tests added, each built from the exact real numbers in the live failures (not synthetic examples), all passing (`test_digest_graph.py`). A fourth live re-run to confirm zero remaining false-fallbacks end-to-end was cut off partway through by Groq's free-tier *daily* token cap (200,000 TPD; three consecutive full-portfolio verification runs in one sitting exhausted it) — confidence in the fix rests on the unit tests reproducing the exact live failure shapes plus the clean progression across the first three live runs (all→1→0-except-one-genuine-model-error), not on a fourth clean live run, which is still pending quota reset. |
