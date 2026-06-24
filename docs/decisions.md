# Decision Log

A running record of *why* each significant design choice was made. In interviews,
defending these reads as far more mature than reciting a feature list.

Format: each decision states the **context**, the **choice**, and the **why**
(including the alternative we rejected).

---

## D1 — Single `asyncio` event loop with bounded queues

**Context.** The pipeline is IO-bound (tailing files, receiving network data,
rendering a TUI) with occasional CPU spikes (clustering a batch).

**Choice.** One `asyncio` event loop. Stages are coroutines connected by bounded
`asyncio.Queue`s. CPU-heavy work is offloaded to a thread/process pool.

**Why.** A single loop fits IO-bound work without thread-synchronization
complexity. Bounded queues give backpressure *for free*: when a queue is full the
producer awaits, so a firehose source throttles itself instead of exhausting
memory. Rejected: thread-per-source (lock complexity, no natural backpressure).

---

## D2 — `LogEvent` is `frozen=True, slots=True`

**Context.** One immutable record flows through every stage and may be shared
across coroutines; we expect millions of them.

**Choice.** A frozen, slotted dataclass. Mutation (e.g. assigning a
`template_id`) returns a new copy via `dataclasses.replace`.

**Why.** `frozen` makes events safe to share across coroutines without locks.
`slots` removes the per-instance `__dict__`, cutting memory at scale. Copy-on-write
for `template_id` keeps immutability intact. Rejected: a mutable dataclass (cheaper
writes, but unsafe sharing and higher memory).

---

## D3 — The data model carries no clock

**Context.** `ingest_ts` records when LogScope first saw an event.

**Choice.** `ingest_ts` is nullable and set explicitly by the ingest layer, not
auto-filled by the model.

**Why.** The model is a dumb, honest container; *when "now" is* is policy that
belongs to the ingest layer, which actually observes the event. Keeping policy out
of the data model is clean-architecture discipline. (Frozen dataclasses also make
`__post_init__` auto-fill awkward, reinforcing the choice.)

---

## D4 — One query AST, two evaluation targets

**Context.** Queries must run against both persisted history *and* the live
stream, and we don't want two diverging query implementations.

**Choice.** A small lexer → parser → AST. Each AST term implements `to_sql()`
(a parameterized fragment for history) and `to_predicate()` (a Python callable
for the live stream). One grammar, two compile targets.

**Why.** A single source of truth for query semantics: a fix or a new term works
everywhere at once. Parameterized SQL fragments also prevent injection. We kept
v1 AND-only (OR/NOT/parens are a documented stretch) — shipping a small correct
language beats a big buggy one. Rejected: substring/regex hacks (no structure,
can't push predicates into the SQL index).

---

## D5 — SQLite + FTS5 with batched writes and idempotent inserts

**Context.** A single-node tool needs indexed full-text search and durable
history without operating an external datastore.

**Choice.** SQLite with an external-content FTS5 index (triggers keep it in
sync). Events buffer and flush in one transaction. Each event has a stable
`event_id` (hash of source+raw+ts) inserted with `INSERT OR IGNORE`.

**Why.** Zero-dependency, transactional, full-text search built in — right-sized
for one node. Batched transactions are the throughput lever (thousands →
tens of thousands of inserts/sec). The stable id makes ingestion idempotent now,
so Phase 4 agent resends de-dup for free. Scaling story if we outgrow SQLite:
time-partitioned append-only segment files. Rejected: Postgres/Elasticsearch
(operational overhead unjustified for a local tool).
