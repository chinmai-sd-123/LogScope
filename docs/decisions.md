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
