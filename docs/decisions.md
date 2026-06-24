# Decision Log

A running record of *why* each significant design choice was made. In interviews,
defending these reads as far more mature than reciting a feature list.

Format: each decision states the **context**, the **choice**, and the **why**
(including the alternative we rejected).

---

## D1 - Single `asyncio` event loop with bounded queues

**Context.** The pipeline is IO-bound (tailing files, receiving network data,
rendering a TUI) with occasional CPU spikes (clustering a batch).

**Choice.** One `asyncio` event loop. Stages are coroutines connected by bounded
`asyncio.Queue`s. CPU-heavy work is offloaded to a thread/process pool.

**Why.** A single loop fits IO-bound work without thread-synchronization
complexity. Bounded queues give backpressure *for free*: when a queue is full the
producer awaits, so a firehose source throttles itself instead of exhausting
memory. Rejected: thread-per-source (lock complexity, no natural backpressure).

---

## D2 - `LogEvent` is `frozen=True, slots=True`

**Context.** One immutable record flows through every stage and may be shared
across coroutines; we expect millions of them.

**Choice.** A frozen, slotted dataclass. Mutation (e.g. assigning a
`template_id`) returns a new copy via `dataclasses.replace`.

**Why.** `frozen` makes events safe to share across coroutines without locks.
`slots` removes the per-instance `__dict__`, cutting memory at scale. Copy-on-write
for `template_id` keeps immutability intact. Rejected: a mutable dataclass (cheaper
writes, but unsafe sharing and higher memory).

---

## D3 - The data model carries no clock

**Context.** `ingest_ts` records when LogScope first saw an event.

**Choice.** `ingest_ts` is nullable and set explicitly by the ingest layer, not
auto-filled by the model.

**Why.** The model is a dumb, honest container; *when "now" is* is policy that
belongs to the ingest layer, which actually observes the event. Keeping policy out
of the data model is clean-architecture discipline. (Frozen dataclasses also make
`__post_init__` auto-fill awkward, reinforcing the choice.)

---

## D4 - One query AST, two evaluation targets

**Context.** Queries must run against both persisted history *and* the live
stream, and we don't want two diverging query implementations.

**Choice.** A small lexer → parser → AST. Each AST term implements `to_sql()`
(a parameterized fragment for history) and `to_predicate()` (a Python callable
for the live stream). One grammar, two compile targets.

**Why.** A single source of truth for query semantics: a fix or a new term works
everywhere at once. Parameterized SQL fragments also prevent injection. We kept
v1 AND-only (OR/NOT/parens are a documented stretch) - shipping a small correct
language beats a big buggy one. Rejected: substring/regex hacks (no structure,
can't push predicates into the SQL index).

---

## D5 - SQLite + FTS5 with batched writes and idempotent inserts

**Context.** A single-node tool needs indexed full-text search and durable
history without operating an external datastore.

**Choice.** SQLite with an external-content FTS5 index (triggers keep it in
sync). Events buffer and flush in one transaction. Each event has a stable
`event_id` (hash of source+raw+ts) inserted with `INSERT OR IGNORE`.

**Why.** Zero-dependency, transactional, full-text search built in - right-sized
for one node. Batched transactions are the throughput lever (thousands →
tens of thousands of inserts/sec). The stable id makes ingestion idempotent now,
so Phase 4 agent resends de-dup for free. Scaling story if we outgrow SQLite:
time-partitioned append-only segment files. Rejected: Postgres/Elasticsearch
(operational overhead unjustified for a local tool).

---

## D6 - Drain (fixed-depth tree) for clustering, hand-written

**Context.** Collapsing thousands of near-identical lines into ranked templates
is the tool's most valuable feature, and must run online at high line rates.

**Choice.** A hand-written Drain miner: mask variables, group by token length,
descend a fixed number of leading-token layers to a leaf, then match by
similarity (merging differing positions to `<*>`) or create a new template.

**Why.** The fixed depth bounds work per line independent of how many templates
exist (≈O(1) amortized) - the property that makes it viable online. Writing it by
hand (not `drain3`) is the whole point: it's the algorithmic centerpiece and a
data structure we can defend and whiteboard. Known limitation, tested explicitly:
an *unmasked* variable in the prefix tokens over-splits; mitigations are masking
and `depth` tuning. Tuning knobs: `depth`, `sim_threshold`, `max_children`.

---

## D7 - Transparent statistics for anomaly detection, not ML

**Context.** Spike detection must be trustworthy to an on-call engineer mid-incident.

**Choice.** Rolling z-score over fixed time buckets: flag a bucket whose count
exceeds `mean + k·stddev`, with an absolute `min_count` floor. The window is
maintained incrementally (running sum/sum-of-squares) for O(1) per tick.

**Why.** "This bucket is 4σ above the last five minutes" is actionable; "anomaly
score 0.87" is not. Explainability is the senior signal here - we deliberately
chose statistics we can justify over a black-box model. The floor prevents firing
on tiny absolute jumps (0→3). Rejected: ML/forecasting models (opaque,
overkill, harder to defend).

---

## D8 - Length-prefixed JSON frames with at-least-once + idempotent ingest

**Context.** Agents on remote boxes ship event batches to a central server over
TCP, which has no message boundaries and an unreliable connection.

**Choice.** 4-byte big-endian length prefix + JSON payload (with a schema
`version`). Agents batch (N events or T seconds), buffer to a bounded spool while
disconnected (drop-oldest policy), reconnect with exponential backoff + jitter,
and resend unacked batches. The server de-dups via the stable `event_id`.

**Why.** Framing solves the TCP boundary problem explicitly. JSON keeps the wire
debuggable (tcpdump-readable) at a modest size cost; `version` enables
forward/backward compatibility. At-least-once + idempotent ingest yields
effectively-once without the coordination cost of true exactly-once. Backoff
*with jitter* avoids a thundering herd when a downed server recovers. Drop-oldest
favors fresh data and is a documented, deliberate policy. Rejected: msgpack
(smaller but opaque - debuggability won for a portfolio tool); exactly-once
(disproportionate complexity).

---

## D9 - AI is additive, on-demand, cached, and degrades gracefully

**Context.** The deterministic core already answers "what is happening". AI can
add "what might it mean / what to check first" - but must never be load-bearing.

**Choice.** A `Summarizer` interface (OpenAI implementation via httpx; a Null
default) invoked only when the user presses ctrl+s on a cluster. Results are
cached by template fingerprint, bounded by a hard timeout and token cap, and any
failure (disabled/timeout/network/bad response) returns `None`.

**Why.** Pull-not-push controls cost and latency and shows restraint - the single
design choice that most signals judgment. Caching by *template fingerprint* (not
raw lines) makes the cache actually hit across incidents of the same shape.
Graceful degradation is built and tested first-class: with no `OPENAI_API_KEY`
the tool is fully functional and the pane reads "unavailable". The provider sits
behind an interface so the backend is swappable. Rejected: AI on every event
(cost/latency/noise); making any feature depend on the model.
