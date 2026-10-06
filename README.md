# RAT — Repo Analysis Tool

A web dashboard that ingests git repositories — by remote URL (deep clone) or
zip upload — and computes metrics (lines added/removed, growth, churn,
modifications, frequency, churn rate, author churn and ownership) per file,
directory, repository, commit set, and author.

## Features

- Ingest by remote URL (deep clone) or zip upload containing `.git`
- All five metric categories: file, directory, repository, commit set, author
- Filtering by repository, author, file/directory drill-down, time period,
  and a manually selected commit list (searchable, paginated browser)
- Author merging: automatic via the repository's `.mailmap`, plus manual
  alias→canonical merging through the UI and API
- Multiple repositories side by side with an async overview table
- Per-repository persistent metric cache (`.rat-cache.json`): large histories
  parse once and survive server restarts
- JSON error handling and loading states throughout

## Prerequisites

- Python 3.10+
- The `git` command-line tool
- `pip`

## Install

```bash
python3 -m pip install -r requirements.txt pytest
```

(On Windows use `python` instead of `python3` throughout.)

## Database / setup

None required. There is no database, no configuration file, and no migration
step — cloned/uploaded repositories are stored in `repos/`, which is created
automatically on startup.

## Run (development)

```bash
python3 app.py
```

Then open **http://127.0.0.1:5000** (port 5000).

## Production

There is no build step (pure Python + static assets). Start without debug:

```bash
python3 -m flask --app app run --port 5000
```

## Tests

From the project root:

```bash
python3 -m pytest .fixtures/test_smoke.py .fixtures/test_metrics.py
```

70 tests cover ingestion (clone + zip layouts, error cases), the metric
engine (renames, binaries, deletions, directory rollups, commit sets,
author metrics), filtering, author merging (mailmap + manual), multi-repo
isolation, the persistent cache, and API error handling.

## Docker

Not used — plain Python/Flask; no container setup is required.

## AI Usage (COMS3011A AI Policy)

> Replace `<model>` below with the model name shown in your Qoder settings
> before submitting.

This repository makes use of AI code generation using the following tools: Qoder[Auto (smart routing tier)].

This repository does not use AI in-line editing tools.

This repository does not use AI code review.

This README was generated with the assistance of: Qoder[Auto (smart routing tier)].

## API reference

| Method | Path                     | Description                                    |
|--------|--------------------------|------------------------------------------------|
| GET    | `/api/repos`             | List ingested repositories                     |
| POST   | `/api/repos/clone`       | Clone from `{ "url": "..." }`                  |
| POST   | `/api/repos/upload`      | Upload a zip (multipart `file` field)          |
| GET    | `/api/repos/<r>/summary` | Root metrics + author count (overview)         |
| GET    | `/api/repos/<r>/metrics` | Metrics for a path over a filtered commit set  |
| GET    | `/api/repos/<r>/authors` | All authors with repository totals             |
| GET    | `/api/repos/<r>/commits` | Browsable commit list (search, pagination)     |
| GET / PUT | `/api/repos/<r>/merges` | Manual alias→canonical author merges          |

Metrics query parameters: `path` (file/directory, default root), `author`
(email), `since`/`until` (unix timestamp or ISO date; `until` exclusive,
date-only `until` covers the whole day), `commits` (comma-separated hashes).
Responses carry the object's metrics, its immediate children (directories),
and per-author churn/modifications/ownership.

## Metric semantics

- History is the set of non-merge commits reachable from HEAD; the initial
  commit diffs against an empty tree.
- Rename detection runs at git's 50% similarity threshold; changes are
  attributed to the new path and pure renames change no metrics.
- Binary files are not measured; deletions count as removed lines.
- Directory metrics are recursive rollups over immediate children;
  repository metrics are directory metrics at the root.
- Author identities merge through the repository's `.mailmap` and through
  manual alias→canonical mappings (`.rat-merges.json` beside the repo).

## Performance

Each repository's history is parsed once per HEAD (`git log --no-merges -M
--numstat -z`), then served from an in-memory cache that is also persisted
to `.rat-cache.json` beside the repository — a server restart restores
large histories from disk instead of re-parsing them. The cache is
invalidated automatically when HEAD moves or `.mailmap` changes.

Reference timings: cJSON (~1k commits) parses in ~0.2 s; Redis (~12k
commits, ~216 MB .git) parses in ~9 s once, then serves any query in
~10 ms from cache and restores in ~0.1 s after a restart.
