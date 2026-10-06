# RAT — Repo Analysis Tool

A web-app dashboard that ingests git repositories and analyses their metrics
(lines added/removed, growth, churn, authorship) per author, file, directory,
and repository.

## Running

```bash
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000

## Ingestion

- **Clone URL** — deep-clones a remote repository (full history).
- **Upload zip** — accepts a zip archive of a repository that includes its
  `.git` directory (or file).

## API

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

### Metrics query parameters

- `path` — file or directory (default: repository root)
- `author` — author email
- `since` / `until` — unix timestamp or ISO date (`until` is exclusive;
  a date-only `until` covers the whole day)
- `commits` — comma-separated commit hashes (manual commit-set selection)

The response carries the object's metrics (added, removed, growth, churn,
modifications, modification frequency, churn rate), its immediate children
(for directories) and per-author churn/modifications/ownership.

### Multiple repositories

Repositories live side by side: the ingestion table doubles as a
multi-repository overview whose commit/churn/author summaries fill in
asynchronously (large repositories parse once, then serve from cache), and
the repository selector in the filter bar switches the analysis target.
Analyses, caches, and author-merge maps are all per repository.

### Dashboard filtering

The dashboard filters metrics by repository (selector), author, date range,
file/directory (drill-down tree), and a manually selected commit list
(searchable, paginated commit browser). Commit search matches subject,
author name/email, and hashes (4+ character prefixes).

### Author merging

- Repositories that ship a `.mailmap` merge identities automatically: history
  is read through git's mailmap-aware placeholders, so aliases collapse into
  their canonical author at no extra cost.
- Manual merges map alias emails onto a canonical email:
  `PUT /api/repos/<r>/merges` with `{"merges": {"alias@x": "canonical@y"}}`
  (an empty map clears them). Mappings persist in `.rat-merges.json` beside
  the repository and re-aggregate author metrics; object-level metrics are
  unaffected. The dashboard exposes the same flow via *Merge authors…*

### Metric semantics

- History is the set of non-merge commits reachable from HEAD; the initial
  commit diffs against an empty tree.
- Rename detection runs at git's 50% similarity threshold; changes are
  attributed to the new path and pure renames change no metrics.
- Binary files are not measured; deletions count as removed lines.
- Directory metrics are recursive rollups over immediate children;
  repository metrics are directory metrics at the root.
- Parsed history is cached per repository and invalidated when HEAD moves
  or the repository's `.mailmap` changes.
- Author identities merge through the repository's `.mailmap` and through
  manual alias→canonical mappings (`.rat-merges.json`).

## Status

- [x] Skeleton dashboard + both ingestion paths
- [x] Metric engine (file / directory / repository / commit set / author)
- [x] Filtering UI (repo, author, path, commit period/selection)
- [x] Author merging (mailmap + manual)
- [x] Multiple repository support (overview + selector)
