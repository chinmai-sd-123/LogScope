# Running the LogScope demo

A realistic log generator (`demo/generate.py`) simulates a web service with
steady traffic and periodic **incidents** (database error storms) so the cluster
panel and error-rate sparkline visibly react.

> Run every command from the project root (`d:\LogScope`) in an **activated venv**
> (`.\.venv\Scripts\Activate.ps1`). Use a real terminal — the TUI is interactive
> and won't render inside an editor's output pane.

## Demo 1 — the live TUI (the headline)

Open **two terminals**.

**Terminal A** — start the log stream:
```
python demo/generate.py demo.log --rate 40
```

**Terminal B** — watch LogScope:
```
logscope tail demo.log
```

What you'll see:
- **Live stream** (left) — colorized by level, type in the box to filter.
- **Clusters** (right) — noisy lines collapse into ranked templates with bars;
  the `Failed to connect to db-<*> ...` cluster climbs during an incident.
- **Error rate** sparkline — spikes every ~30s when the incident window hits.
- **Header status bar** — live ingest rate, queue depth, lag, cluster count.
- Press **ctrl+s** to get an AI root-cause hypothesis for the top cluster
  (needs `OPENAI_API_KEY` in `.env`; without it the pane shows "unavailable").
- **ctrl+c** to quit.

## Demo 2 — history search

After Demo 1 has run for a bit (it writes to `logscope.db`), search it:
```
logscope search 'level:error "Failed to connect"'
logscope search 'level:error last:5m'
logscope search 'source:demo.log level:warn'
```
Each result set prints how long the query took.

## Demo 3 — distributed (agent + server)

**Terminal A** — the server:
```
logscope serve
```
**Terminal B** — generate logs and ship them:
```
python demo/generate.py app.log --rate 30
logscope agent app.log --server 127.0.0.1:9099
```
The server ingests the agent's events. Kill the server (ctrl+c) and restart it —
the agent buffers and reconnects without losing events.

## Quick one-shot (no streaming)

Write a fixed batch and search it immediately:
```
python demo/generate.py demo.log --once 800
logscope search 'level:error "Failed to connect"'
```
