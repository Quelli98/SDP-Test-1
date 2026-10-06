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
| GET    | `/api/repos/<r>/metrics` | Metrics for a path over a filtered commit set  |
| GET    | `/api/repos/<r>/authors` | All authors with repository totals             |

### Metrics query parameters

- `path` — file or directory (default: repository root)
- `author` — author email
- `since` / `until` — unix timestamp or ISO date (`until` is exclusive;
  a date-only `until` covers the whole day)
- `commits` — comma-separated commit hashes (manual commit-set selection)

The response carries the object's metrics (added, removed, growth, churn,
modifications, modification frequency, churn rate), its immediate children
(for directories) and per-author churn/modifications/ownership.

### Metric semantics

- History is the set of non-merge commits reachable from HEAD; the initial
  commit diffs against an empty tree.
- Rename detection runs at git's 50% similarity threshold; changes are
  attributed to the new path and pure renames change no metrics.
- Binary files are not measured; deletions count as removed lines.
- Directory metrics are recursive rollups over immediate children;
  repository metrics are directory metrics at the root.
- Parsed history is cached per repository and invalidated when HEAD moves.

## Status

- [x] Skeleton dashboard + both ingestion paths
- [x] Metric engine (file / directory / repository / commit set / author)
- [ ] Filtering UI (repo, author, path, commit period/selection)
- [ ] Author merging (mailmap + manual)
