# LogScope - Build Handbook

*A terminal-based log-intelligence and incident-triage platform, built in Python.*

This handbook is a complete, in-depth guide to designing and building the project from an empty folder to a portfolio-grade tool. It is written so you can follow it linearly while coding, and return to any section as a reference. Read the Orientation first, then build phase by phase.

---

## Table of Contents

1. [Orientation](#1-orientation)
2. [The Problem](#2-the-problem)
3. [What You Are Building](#3-what-you-are-building)
4. [Architecture Overview](#4-architecture-overview)
5. [Technology Choices](#5-technology-choices)
6. [The Data Model](#6-the-data-model)
7. [Project Layout](#7-project-layout)
8. [Phase 1 - Core: Tail, Parse, Display](#8-phase-1--core-tail-parse-display)
9. [Phase 2 - Search & Storage](#9-phase-2--search--storage)
10. [Phase 3 - Intelligence: Clustering & Anomaly Detection](#10-phase-3--intelligence-clustering--anomaly-detection)
11. [Phase 4 - Distributed: Agent & Server](#11-phase-4--distributed-agent--server)
12. [Phase 5 - AI Enrichment (Optional, Additive)](#12-phase-5--ai-enrichment-optional-additive)
13. [Testing Strategy](#13-testing-strategy)
14. [Observability of the Tool Itself](#14-observability-of-the-tool-itself)
15. [Performance & Resilience Notes](#15-performance--resilience-notes)
16. [The Query Language Spec](#16-the-query-language-spec)
17. [Interview Talking Points](#17-interview-talking-points)
18. [README Template](#18-readme-template)
19. [Milestones & Time Budget](#19-milestones--time-budget)
20. [Stretch Goals](#20-stretch-goals)
21. [Glossary](#21-glossary)

---

## 1. Orientation

The single most important idea in this project: **the engineering is the product, not the AI.** Most candidates build a thin wrapper around an LLM API. You are building a real systems tool - efficient IO, concurrency, indexing, clustering, a network protocol - and treating AI as one small, optional component that earns its place only where deterministic methods fall short.

Read this before writing code:

- Build **phase by phase**. Each phase produces a working, demoable tool. You can stop after Phase 3 and still have an impressive project. Phases 4 and 5 are how you flex distributed systems and judicious AI.
- **Scope discipline is the whole game.** A clean tool that nails Phases 1–3 beats a sprawling, half-finished five-phase thing. Resist feature creep.
- Write tests as you go, not at the end. The parser, the query engine, and the clustering miner are highly unit-testable and are your strongest "I think about correctness" signal.
- Keep a running architecture diagram and decision log. In interviews, being able to explain *why* you chose each design beats the feature list.

---

## 2. The Problem

When a production service misbehaves, debugging usually looks like this: SSH into one or more machines, `grep` through enormous log files, manually line up timestamps across services, and eyeball the stream for error spikes. It is slow, error-prone, and miserable. Existing terminal tools (`tail -f`, `grep`, `lnav`) help with viewing and filtering but do not:

- **Collapse noise.** A single failing code path can emit ten thousand near-identical lines. You want to see *"this error pattern, 10,432 times"* - one row, not ten thousand.
- **Detect spikes.** A sudden jump in error rate is the signal you actually care about, and no plain pager surfaces it.
- **Correlate across sources.** Tailing five services at once, merged on a single timeline, is awkward with stock tools.

LogScope targets exactly this gap: a fast, terminal-native tool that ingests logs from multiple sources, makes them searchable, collapses repetitive noise into ranked clusters, flags anomalies, and - optionally - explains a cluster's likely root cause in plain English.

Honest framing for interviews: tools like Grafana Loki, Datadog, and `lnav` exist and are more capable. You built this to *understand how such systems work* and to solve the specific local-debugging gap, not to displace them. That framing reads as mature; overclaiming reads as naïve.

---

## 3. What You Are Building

A command-line application with several subcommands:

- `logscope tail <paths...>` - live multi-source tailing in a full-screen TUI with filtering.
- `logscope search "<query>"` - run a query against indexed history and print results.
- `logscope agent --server <addr>` - run a lightweight collector on a remote box that ships events to a server.
- `logscope serve` - run the central server that accepts events from agents and serves the TUI/queries.

The TUI has multiple panes: a live log stream, a cluster panel (ranked repetitive patterns with counts), an error-rate sparkline, and a detail panel that shows the lines and (optionally) an AI summary for a selected cluster.

---

## 4. Architecture Overview

The system is a pipeline. Each stage is independent and testable in isolation, which is both good engineering and a good interview story.

```
+-----------+     +---------+     +-----------+     +--------------+     +-----------+
|  Sources  | --> | Ingest  | --> |  Parser   | --> |  Processing  | --> |   Sinks   |
| files,    |     | tail,   |     | structured|     | index,       |     | TUI,      |
| agents,   |     | batch,  |     | LogEvent  |     | cluster,     |     | query,    |
| stdin     |     | backpr. |     |           |     | anomaly      |     | storage   |
+-----------+     +---------+     +-----------+     +--------------+     +-----------+
                                                          |
                                                          v
                                                   +--------------+
                                                   | AI enrich    |
                                                   | (optional,   |
                                                   |  cached)     |
                                                   +--------------+
```

**Flow in words.** Sources produce raw lines. The ingest layer tails or receives them, batches, and applies backpressure so a fast source cannot overwhelm the pipeline. The parser turns a raw line into a structured `LogEvent`. The processing layer fans the event into three consumers: the inverted index (for search), the clustering engine (for noise collapse), and the anomaly detector (for spike detection). Sinks present results: the TUI renders live, the query engine answers searches, and storage persists events for later. AI enrichment hangs off the side, invoked on demand for a selected cluster and always cached.

**Concurrency model.** A single `asyncio` event loop. Ingestion, processing, and the TUI render loop are coroutines communicating through bounded `asyncio.Queue`s. Bounded queues are what give you backpressure for free: when a queue is full, the producer awaits, naturally throttling a firehose source. CPU-heavy work (clustering on a large batch) can be offloaded to a thread or process pool so it does not stall the render loop.

---

## 5. Technology Choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Async maturity, batteries included, fast to iterate |
| CLI structure | Typer | Clean subcommands, type-hint based, minimal boilerplate |
| TUI framework | Textual | Modern async TUI, CSS-like styling, multi-pane layouts |
| Terminal styling | Rich | Tables, syntax highlighting, sparklines (bundled with Textual) |
| Concurrency | asyncio (stdlib) | Single-loop model fits IO-bound ingestion + TUI |
| File tailing | watchfiles or manual seek | Efficient change notification; manual seek for rotation control |
| Storage | SQLite + FTS5 (stdlib `sqlite3`) | Zero-dependency, full-text search built in, transactional |
| Network protocol | asyncio streams + length-prefixed JSON/msgpack | Simple, debuggable, demonstrates protocol design |
| HTTP / AI calls | httpx | Async, streaming support |
| Testing | pytest + pytest-asyncio | Standard, async fixtures |
| Packaging | pyproject.toml + pip install -e . | Modern, installable |

A note on dependencies: lean on the standard library where you reasonably can (`socket`/`asyncio`, `sqlite3`, `struct`, `re`, `collections`). Fewer third-party dependencies means more real engineering on display and less framework-chasing. The exceptions above (Typer, Textual, httpx) each earn their place by removing genuinely tedious work.

---

## 6. The Data Model

The `LogEvent` is the spine of the system. Everything downstream operates on it. Design it carefully and immutably.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Optional

class Level(IntEnum):
    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARN = 3
    ERROR = 4
    FATAL = 5

@dataclass(frozen=True, slots=True)
class LogEvent:
    timestamp: datetime          # parsed event time (UTC); ingest time if unknown
    level: Level                 # normalized severity
    source: str                  # which file/service/agent produced it
    message: str                 # the human-readable message body
    raw: str                     # the original unparsed line (always keep this)
    fields: dict = field(default_factory=dict)   # extracted key/values (request_id, etc.)
    ingest_ts: datetime = None   # when LogScope saw it (for lag measurement)
    template_id: Optional[int] = None  # set later by the clustering engine
```

Design decisions worth being able to defend:

- **`frozen=True, slots=True`** - events are immutable (safe to share across coroutines) and memory-efficient (`slots` removes the per-instance `__dict__`). With millions of events, this matters.
- **Always keep `raw`.** Parsing is lossy and heuristic. Keeping the original line means a parser bug never destroys data, and the user can always see ground truth.
- **`template_id` is nullable and set later.** Parsing and clustering are separate stages; the event flows through parsing before the clusterer assigns it a template.
- **`ingest_ts` separate from `timestamp`.** The gap between them is *ingestion lag*, a metric you will expose. Distinguishing event time from processing time is a core stream-processing concept.

---

## 7. Project Layout

```
logscope/
├── pyproject.toml
├── README.md
├── logscope-handbook.md
├── logscope/
│   ├── __init__.py
│   ├── cli.py                 # Typer entrypoint, subcommands
│   ├── model.py               # LogEvent, Level
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── tailer.py          # file tailing with rotation handling
│   │   └── source.py          # Source abstraction (file, stdin, network)
│   ├── parse/
│   │   ├── __init__.py
│   │   ├── parser.py          # line -> LogEvent
│   │   └── formats.py         # known formats (json, logfmt, common patterns)
│   ├── index/
│   │   ├── __init__.py
│   │   ├── inverted.py        # in-memory inverted index
│   │   └── store.py           # SQLite/FTS5 persistence
│   ├── cluster/
│   │   ├── __init__.py
│   │   └── drain.py           # Drain-style log template miner
│   ├── anomaly/
│   │   ├── __init__.py
│   │   └── detector.py        # sliding-window spike detection
│   ├── query/
│   │   ├── __init__.py
│   │   ├── lexer.py           # tokenizer for the query language
│   │   └── parser.py          # query AST + evaluation
│   ├── net/
│   │   ├── __init__.py
│   │   ├── protocol.py        # framing, encode/decode
│   │   ├── agent.py           # client: ship events
│   │   └── server.py          # accept events from agents
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── summarizer.py      # cluster -> root-cause hypothesis
│   │   └── cache.py           # fingerprint -> cached summary
│   ├── tui/
│   │   ├── __init__.py
│   │   ├── app.py             # Textual App, layout, key bindings
│   │   └── widgets.py         # stream pane, cluster pane, sparkline
│   └── metrics.py             # self-instrumentation
└── tests/
    ├── test_parser.py
    ├── test_drain.py
    ├── test_query.py
    ├── test_anomaly.py
    └── test_protocol.py
```

---

## 8. Phase 1 - Core: Tail, Parse, Display

**Goal.** Live-tail one or more files in a TUI, parse each line into a `LogEvent`, and display a filterable stream. This phase alone is a useful tool.

### 8.1 The Tailer

Tailing correctly is harder than it looks, and getting it right is a good signal. The naïve approach (`open` and `readline` in a loop) breaks on log rotation. A real tailer must:

- Open the file, seek to the end (or start, configurable), and read new lines as they arrive.
- Detect **rotation**: when a log hits a size cap, the OS renames it and creates a fresh file at the same path. You detect this when the file's inode changes, or when its size shrinks below your current offset (truncation). On rotation, finish reading the old handle, then reopen the path.
- Handle **partial lines**: a write may land mid-line. Buffer until you see a newline; never emit a half-line.

```python
import os, asyncio
from pathlib import Path

async def tail(path: Path, from_start: bool = False):
    """Yield complete lines from a file, surviving rotation and truncation."""
    while not path.exists():
        await asyncio.sleep(0.2)
    fh = open(path, "r", encoding="utf-8", errors="replace")
    inode = os.fstat(fh.fileno()).st_ino
    if not from_start:
        fh.seek(0, os.SEEK_END)
    buffer = ""
    while True:
        chunk = fh.read()
        if chunk:
            buffer += chunk
            *lines, buffer = buffer.split("\n")   # last element is the partial remainder
            for line in lines:
                yield line
            continue
        # No new data: check for rotation/truncation before sleeping.
        try:
            st = os.stat(path)
        except FileNotFoundError:
            await asyncio.sleep(0.2)
            continue
        if st.st_ino != inode or st.st_size < fh.tell():
            fh.close()
            fh = open(path, "r", encoding="utf-8", errors="replace")
            inode = os.fstat(fh.fileno()).st_ino
            buffer = ""
        await asyncio.sleep(0.1)
```

Talking point: the inode check is what separates a toy `tail` from one that survives `logrotate`. Mentioning that unprompted in an interview signals you have run real services.

### 8.2 The Parser

Logs come in many shapes. Build a small pipeline of format detectors tried in order, falling back to "unstructured."

- **JSON lines** - `json.loads` succeeds; pull `level`, `msg`/`message`, `time`/`ts`, and treat the rest as `fields`.
- **logfmt** - `key=value key2="value two"` pairs; common in Go services.
- **Common/combined patterns** - a handful of regexes for syslog, nginx/apache access logs, and `LEVEL timestamp message` shapes.
- **Fallback** - wrap the whole line as `message`, set level to INFO (or scan for the words ERROR/WARN), timestamp = ingest time.

Key design points to articulate:

- **Level normalization.** Map `err`, `error`, `ERROR`, `E`, severity numbers, etc. to your `Level` enum so downstream code never deals with format quirks. Normalization at the boundary is a clean-architecture principle.
- **Timestamp parsing is best-effort.** Try a few known formats; if all fail, use ingest time and record that you did. Never crash on an unparseable date.
- **The parser returns a `LogEvent` and never raises on bad input.** Robustness against malformed lines is non-negotiable for a log tool - logs are where malformed data *lives*.

### 8.3 The TUI (Phase 1 version)

A minimal Textual app: a scrolling log view plus a filter input box at the bottom. Color lines by level (red ERROR, yellow WARN, dim DEBUG). The filter box does live substring/regex filtering as you type.

Architecture note: the TUI never reads files directly. A producer coroutine tails files and pushes `LogEvent`s onto a bounded `asyncio.Queue`; the TUI consumes from the queue on a timer and updates widgets. This decoupling is what lets you later swap the file source for a network source without touching the UI - a clean seam worth pointing at.

**Phase 1 demo:** `logscope tail /var/log/app.log` shows a live, colorized, filterable stream that survives log rotation. Done.

---

## 9. Phase 2 - Search & Storage

**Goal.** Persist events and make history searchable with a real query language, not just substring grep.

### 9.1 Persistence with SQLite + FTS5

SQLite ships with Python and includes FTS5, a full-text search engine. This gives you indexed search with zero external services - a pragmatic choice you can defend as "right-sized for a single-node tool."

Schema sketch:

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,          -- epoch millis, for range scans
  level INTEGER NOT NULL,
  source TEXT NOT NULL,
  message TEXT NOT NULL,
  raw TEXT NOT NULL,
  fields TEXT,                  -- JSON blob
  template_id INTEGER
);
CREATE INDEX idx_events_ts ON events(ts);
CREATE INDEX idx_events_level ON events(level);

CREATE VIRTUAL TABLE events_fts USING fts5(
  message, source, content='events', content_rowid='id'
);
```

Design points:

- **Batch writes in a transaction.** Inserting one row per event with autocommit is slow. Buffer events and flush every N events or every T milliseconds inside a single transaction. This is a throughput lever you can benchmark and talk about.
- **Time-bucketing.** Storing `ts` as an indexed integer lets `last:5m` style queries become fast range scans. If you outgrow SQLite, the natural next step is segment files partitioned by time bucket - mention this as your scaling story.
- **`content='events'`** makes the FTS table an external-content index, avoiding duplicate storage of the message text.

### 9.2 The Query Engine

This is a genuine compiler-lite exercise and a strong DSA/parsing talking point. Build a tiny query language (full grammar in [Section 16](#16-the-query-language-spec)):

```
level:error source:api last:5m "connection timeout"
```

Pipeline: **lexer** (string → tokens) → **parser** (tokens → AST) → **evaluator** (AST → SQL or in-memory predicate). Two evaluation targets:

- Against **history**, compile the AST to a parameterized SQL `WHERE` clause plus an FTS `MATCH` for free-text terms.
- Against the **live stream**, compile the AST to a Python predicate function applied to each incoming `LogEvent`.

Sharing one AST across both targets (historical SQL and live predicate) is an elegant design - point at it.

**Phase 2 demo:** `logscope search 'level:error last:1h "timeout"'` returns ranked matches from history; the same query typed in the TUI filters the live stream.

---

## 10. Phase 3 - Intelligence: Clustering & Anomaly Detection

This is the **systems/algorithms centerpiece** and the part that makes the project memorable. No AI here - deterministic algorithms and statistics do the heavy lifting.

### 10.1 Why Clustering

A failing code path emits the same line over and over, varying only in IDs, timestamps, and numbers:

```
Failed to connect to db-7 after 3 retries (request_id=a1b2)
Failed to connect to db-3 after 3 retries (request_id=c4d5)
Failed to connect to db-9 after 3 retries (request_id=e6f7)
```

These are one *template*:

```
Failed to connect to db-<*> after <*> retries (request_id=<*>)   ×3
```

Collapsing thousands of lines into ranked templates with counts is the single most useful thing the tool does. The algorithm to do it well is **Drain**.

### 10.2 The Drain Algorithm (explained)

Drain is an online log-template miner that runs in near-constant time per line using a fixed-depth parse tree. Intuition:

1. **Preprocess.** Optionally mask obvious variables first (numbers, hex IDs, IPs, UUIDs) with a placeholder.
2. **Group by length.** Split the message into tokens; the token count is the first tree key. Messages of different lengths are different templates. (Layer 1.)
3. **Descend by leading tokens.** Walk a fixed number of levels down the tree, branching on the first token, then the second, etc. The fixed depth is what bounds the work per line. (Layers 2…depth.)
4. **Match within a leaf.** A leaf holds a small list of candidate templates. Compare the message to each by *similarity* - the fraction of positions where tokens match (treating `<*>` as a wildcard). If the best similarity exceeds a threshold, it is a match.
5. **Merge or create.** On a match, update the template: any position where the tokens now differ becomes `<*>`. On no match, create a new template in that leaf.

```python
# Sketch of the core similarity step (not the whole tree).
def seq_distance(template_tokens, message_tokens):
    """Fraction of matching positions; <*> in template is a wildcard."""
    if len(template_tokens) != len(message_tokens):
        return 0.0
    matches = sum(
        1 for t, m in zip(template_tokens, message_tokens)
        if t == "<*>" or t == m
    )
    return matches / len(template_tokens)

def merge(template_tokens, message_tokens):
    """Generalize the template: differing positions become wildcards."""
    return [
        t if (t == m) else "<*>"
        for t, m in zip(template_tokens, message_tokens)
    ]
```

Why interviewers like this: it is a real algorithm with a clear complexity argument (fixed-depth tree ⇒ roughly O(1) amortized per line, independent of the number of templates seen), it involves a tree data structure you designed, and you can discuss the tuning knobs (depth, similarity threshold, max children per node) and their trade-offs. Be ready to whiteboard the tree.

### 10.3 Anomaly Detection

Spike detection over a rolling window, kept deliberately simple and explainable:

- Bucket events into fixed time windows (e.g., 10-second buckets) per level and/or per template.
- Maintain a rolling baseline - mean and standard deviation of the last N buckets.
- Flag a bucket when its count exceeds `mean + k·stddev` (a z-score threshold, k≈3), with a floor to avoid firing on tiny absolute numbers.

This is a sliding-window statistics exercise: maintain the window incrementally (a `collections.deque` of bucket counts; update running sums on push/pop) rather than recomputing from scratch each tick. Render the per-bucket counts as a Rich sparkline so spikes are visible at a glance.

Talking point: you deliberately chose transparent statistics over a black-box ML model because the result must be *explainable* to an on-call engineer at 3 a.m. - "this is 4 standard deviations above the last five minutes" is actionable; "the model says anomaly 0.87" is not. That judgment is the senior signal.

**Phase 3 demo:** the TUI shows a cluster panel - ranked templates with live counts - and an error-rate sparkline that visibly jumps when you replay a log with an incident in it. This is a complete, impressive project even if you stop here.

---

## 11. Phase 4 - Distributed: Agent & Server

**Goal.** Ingest from multiple remote machines. A lightweight **agent** runs on each box, tails local logs, and ships events to a central **server** running the TUI/query engine.

### 11.1 The Protocol

Design a simple, debuggable wire protocol - protocol design is a strong SDE talking point.

- **Framing.** Length-prefixed messages: a 4-byte big-endian unsigned length, then that many bytes of payload. This solves the fundamental TCP problem that the stream has no message boundaries. Use `struct.pack(">I", len(payload))`.
- **Payload.** JSON for debuggability, or msgpack for compactness (mention the trade-off: human-readable vs. smaller/faster). Each frame carries a batch of events plus metadata (agent id, schema version).
- **Batching.** Agents accumulate events and flush every N events or T milliseconds, whichever comes first. Batching amortizes per-message overhead and is a throughput lever.
- **Versioning.** Include a schema version in every frame so old agents and new servers can negotiate. Forward/backward compatibility is exactly the kind of thing senior engineers think about.

```python
import struct

async def send_frame(writer, payload: bytes):
    writer.write(struct.pack(">I", len(payload)))
    writer.write(payload)
    await writer.drain()           # respect backpressure from the socket

async def read_frame(reader) -> bytes:
    header = await reader.readexactly(4)
    (length,) = struct.unpack(">I", header)
    return await reader.readexactly(length)
```

### 11.2 Resilience

This is where you earn the "distributed systems" line on your resume. Handle the unhappy paths explicitly:

- **Reconnection.** When the server is unreachable, the agent retries with exponential backoff and jitter (cap the delay; add randomness to avoid thundering-herd reconnects).
- **Local buffering.** While disconnected, the agent buffers events to a bounded local spool (a capped deque or a small on-disk file). When the buffer is full, decide and document the policy: drop oldest, drop newest, or block. State that choosing a drop policy is itself a design decision with consequences.
- **At-least-once delivery.** A batch may be sent but its ack lost; the agent resends, so the server may see duplicates. Make ingestion **idempotent** by giving each event a stable id (e.g., a hash of `source + raw + timestamp`) and de-duplicating on the server. Articulating at-least-once vs. exactly-once, and why exactly-once is expensive, is a high-value talking point.
- **Graceful shutdown.** On SIGTERM, flush buffers before exiting so you do not lose in-flight events.

**Phase 4 demo:** start a server, run two agents on different terminals (or containers) tailing different logs, and watch a single merged, clustered timeline. Kill the server mid-stream; agents buffer and reconnect; no events lost.

---

## 12. Phase 5 - AI Enrichment (Optional, Additive)

This phase exists to demonstrate **judgment**, not AI skill. The whole tool works without it. AI is a thin enrichment that, for a *selected* cluster, produces a plain-English root-cause hypothesis and suggested next steps.

### 12.1 Where AI Earns Its Place

The deterministic core already answers "what is happening" (this template, this many times, spiking now). AI adds "what might it mean and what would I check first" - synthesis a human would otherwise do. It runs **on demand** (when the user selects a cluster and presses a key), never on every event. That single design choice - pull, not push - controls cost and latency and shows restraint.

### 12.2 The Design Rules (the senior signal)

- **Additive, never required.** If the AI provider is down, slow, or unconfigured, the tool is fully functional; the summary pane simply shows "summary unavailable." Build and demo this offline path deliberately.
- **Cached by fingerprint.** Key the cache on the cluster's *template fingerprint* (the template string plus salient fields), not on raw lines. Identical clusters never trigger a second call. This is a semantic cache and a great thing to point at.
- **Bounded.** Hard timeout on the call; a per-session call budget; truncate the context you send (a sample of representative lines, the template, the count, the time range) rather than dumping thousands of lines.
- **Grounded.** Send the actual cluster context and ask for a hypothesis with explicit uncertainty; instruct it to say "insufficient information" rather than invent. Show the user it is a hypothesis, not a verdict.

```python
async def summarize_cluster(cluster, client, cache, timeout=8.0):
    key = cluster.fingerprint()
    if (hit := cache.get(key)) is not None:
        return hit
    if not client.enabled:
        return None                      # graceful degradation
    context = cluster.sample_context(max_lines=20)
    try:
        summary = await asyncio.wait_for(client.summarize(context), timeout)
    except (asyncio.TimeoutError, Exception):
        return None                      # never let AI failure break the tool
    cache.set(key, summary)
    return summary
```

**Phase 5 demo:** select a spiking cluster, press a key, and a cached-or-fresh root-cause hypothesis appears in the detail pane - then disable the provider and show the tool still works perfectly.

---

## 13. Testing Strategy

Tests are a core part of the impression this project makes. Prioritize the pure-logic units, which are easy to test and where bugs hide.

- **Parser tests.** Feed known lines of each format and assert the resulting `LogEvent`. Include deliberately malformed lines and assert it never raises and always returns *something*. This is your robustness proof.
- **Drain tests.** Feed a crafted set of lines with known variable positions and assert the templates and counts. Assert that varying only IDs collapses to one template, and that genuinely different messages stay separate.
- **Query tests.** Lexer and parser unit tests (token streams, AST shapes), plus end-to-end tests that run a query against a small in-memory dataset and assert the result set.
- **Anomaly tests.** Construct a series with an injected spike and assert it fires; construct a flat series and assert it does not. Test the boundary around the threshold.
- **Protocol tests.** Round-trip encode/decode; framing with split reads (simulate a partial `readexactly`); duplicate-delivery de-duplication.
- **Property-based tests (bonus).** Use Hypothesis to assert invariants: any line parses without raising; encode-then-decode is identity; merging a template only ever generalizes (wildcards never decrease).

Aim for meaningful coverage of the logic modules rather than a coverage percentage on the TUI (UI is better tested manually). State that distinction in your README - it shows you test the *right* things.

---

## 14. Observability of the Tool Itself

A tool that watches logs should watch itself. Expose internal metrics - a strong, often-overlooked signal that you think about operability.

Track and surface (in a status bar or a `--stats` flag):

- **Ingest rate** - events/second in.
- **Ingestion lag** - `now − event.timestamp`, the gap between event time and processing time.
- **Queue depth** - how full the bounded queues are (a proxy for backpressure).
- **Index size** - events stored, on-disk bytes.
- **Query latency** - p50/p95 for searches.
- **Cluster count** - number of active templates.
- **AI cache hit rate** and **call count** (if Phase 5 is built).

Implement a tiny metrics registry (counters, gauges, a rolling histogram for latencies). Mentioning p50/p95 rather than just an average shows you understand that tail latency is what users feel.

---

## 15. Performance & Resilience Notes

- **Bounded queues everywhere.** Unbounded queues turn a traffic spike into an out-of-memory crash. Every queue between stages has a max size; full means the producer awaits. This *is* your backpressure mechanism.
- **Offload CPU-bound work.** Clustering a large backfill batch can block the event loop. Run it in a `ThreadPoolExecutor` (or `ProcessPoolExecutor` to dodge the GIL for genuinely CPU-bound parsing/clustering) via `loop.run_in_executor`, keeping the TUI responsive.
- **Batch all IO.** Disk writes and network sends are batched, never per-event. This is the difference between thousands and tens of thousands of events/second.
- **Degrade, don't die.** Every external dependency (file vanished, socket dropped, AI down, disk full) has a defined fallback. The tool's job is to keep showing logs no matter what; losing a feature is acceptable, crashing is not.
- **Memory ceilings.** The live view keeps only a bounded ring buffer of recent events in memory (a `collections.deque(maxlen=...)`); history lives on disk. You can tail a 50 GB log on a laptop because you never hold it all in RAM.

---

## 16. The Query Language Spec

A small, regular grammar - enough to be useful, simple enough to hand-write a parser for.

```
query      := term (WS term)*
term       := field_term | time_term | level_term | free_text
field_term := IDENT ":" value          # source:api  request_id:a1b2
level_term := "level" ":" level        # level:error  (>= that level)
time_term  := "last" ":" duration      # last:5m  last:2h  last:1d
free_text  := STRING | WORD            # "connection timeout"  timeout
value      := WORD | STRING
level      := trace|debug|info|warn|error|fatal
duration   := INT ("s"|"m"|"h"|"d")
```

Semantics: terms are ANDed. `level:error` means severity ≥ ERROR. Free-text terms become FTS `MATCH` against the message (or a substring/regex predicate on the live stream). `last:5m` becomes a `ts >=` range filter. Keep v1 to AND-only; add OR/NOT/parentheses as a stretch goal - and note in interviews that you *chose* to ship a small correct grammar over a big buggy one.

Worked example: `level:error source:api last:15m "timeout"` compiles to

```sql
SELECT * FROM events
WHERE level >= 4
  AND source = 'api'
  AND ts >= :now_minus_15m
  AND id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH 'timeout')
ORDER BY ts DESC;
```

---

## 17. Interview Talking Points

Rehearse these; they are where the project converts into offers.

- **"Why not just use Grafana/Datadog?"** I built this to understand how observability systems work internally and to solve the local-debugging gap where heavyweight tools are overkill. I am not claiming to beat them.
- **"What was the hardest part?"** The Drain clustering tree - designing a fixed-depth structure that bounds per-line work regardless of how many templates exist, and tuning the similarity threshold to avoid both over- and under-merging.
- **"Where did you use AI and why so little?"** Deterministic clustering plus statistical anomaly detection already answer *what* is happening. AI only synthesizes a root-cause *hypothesis* on demand, cached and with graceful degradation. Using statistics where they suffice and AI only where additive is the engineering judgment I most want to show.
- **"How does it not run out of memory on huge logs?"** Bounded ring buffer in memory, history on disk, bounded queues for backpressure, batched IO. I can tail a 50 GB file on a laptop.
- **"How do you handle the server going down?"** Agents buffer locally with a bounded spool, reconnect with exponential backoff and jitter, and resend; ingestion is idempotent via stable event ids, so at-least-once delivery with server-side de-dup gives effectively-once semantics.
- **"How did you test it?"** Heavy unit tests on the pure logic (parser, query, Drain, anomaly, protocol), property-based tests for invariants, and manual testing for the TUI - I test the right layer for each component.

Have the architecture diagram from Section 4 ready to sketch from memory.

---

## 18. README Template

Your README is the first thing anyone sees. Structure it like this:

```markdown
# LogScope
One-line pitch: a terminal-native log-intelligence tool that tails, searches,
clusters, and triages logs - with optional AI root-cause hypotheses.

![demo gif here]

## Why
The problem (scattered logs, noisy streams, no spike detection) in 3 sentences.

## Features
- Live multi-source tailing that survives log rotation
- Full-text search with a small query language
- Drain-based clustering: collapse 10k noisy lines into ranked templates
- Statistical spike detection with live sparklines
- Distributed agents shipping to a central server (at-least-once, idempotent)
- Optional, cached, gracefully-degrading AI root-cause summaries

## Architecture
[the diagram] + 2 paragraphs on the pipeline and concurrency model.

## Design decisions
Short bullets on the choices you can defend: why SQLite/FTS5, why a single
asyncio loop, why statistics over ML for anomalies, why AI is additive-only.

## Quickstart
Install + the three headline commands.

## Testing
What is tested and why (logic-heavy units; UI manual).

## Roadmap / known limitations
Honest list. Shows maturity.
```

The "Design decisions" and "known limitations" sections matter more than the feature list - they are what make a reviewer think *this person reasons about engineering*.

---

## 19. Milestones & Time Budget

Rough budget for a focused build. Treat Phases 1–3 as the real project; 4–5 as bonuses.

| Phase | Deliverable | Rough effort |
|---|---|---|
| 1 | Tailer + parser + filterable TUI | 3–5 days |
| 2 | SQLite/FTS5 store + query language + search | 4–6 days |
| 3 | Drain clustering + anomaly detection + sparkline | 5–8 days |
| 4 | Agent/server protocol + resilience | 4–7 days |
| 5 | AI enrichment (cached, degrading) | 2–3 days |
| - | Tests, README, demo gif, polish | 3–5 days |

If this is a hackathon, compress to Phases 1–3 plus a thin Phase 5 for the wow moment, and skip Phase 4. If it is a resume centerpiece, finish 1–3 *cleanly* before touching 4.

---

## 20. Stretch Goals

Only after 1–3 are clean. Each is a self-contained extension:

- **Multi-source correlation by trace/request id** - follow one request across services on a single timeline.
- **OR/NOT/parentheses in the query language** - turn the parser into a proper recursive-descent expression parser.
- **Live alert rules** - fire a desktop/terminal alert when a query's match rate crosses a threshold.
- **Segment-file storage** - replace SQLite with time-partitioned append-only segments to demonstrate a real storage engine.
- **Replay mode** - feed a historical log through the live pipeline at adjustable speed for demos and testing.
- **Plugin parsers** - a small entry-point system so users add formats without editing core code.

---

## 21. Glossary

- **Backpressure** - slowing producers when consumers fall behind, here via bounded queues that make producers await.
- **Drain** - an online, fixed-depth-tree algorithm for mining log templates in near-constant time per line.
- **FTS5** - SQLite's built-in full-text search engine.
- **Idempotent ingestion** - processing the same event twice has no extra effect, enabling safe retries.
- **At-least-once delivery** - every event arrives, possibly more than once; combined with idempotency it yields effectively-once.
- **Inverted index** - a map from term to the events containing it; the core structure behind fast text search.
- **Template / cluster** - a generalized log line with variables replaced by `<*>`, representing many concrete lines.
- **Z-score threshold** - flagging values more than k standard deviations above a rolling mean.
- **Tail latency (p95/p99)** - the slow end of the latency distribution; what users actually feel.
- **Event time vs. processing time** - when an event happened vs. when the system handled it; their gap is lag.

---

*Build Phases 1–3 cleanly. Everything after is a bonus. Depth beats breadth - be able to defend every decision in here, and this project will carry an SDE interview.*
