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

| Method | Path                | Description                          |
|--------|---------------------|--------------------------------------|
| GET    | `/api/repos`        | List ingested repositories           |
| POST   | `/api/repos/clone`  | Clone from `{ "url": "..." }`        |
| POST   | `/api/repos/upload` | Upload a zip (multipart `file` field)|

## Status

- [x] Skeleton dashboard + both ingestion paths
- [ ] Metric engine (file / directory / repository / commit set / author)
- [ ] Filtering (repo, author, path, commit period/selection)
- [ ] Author merging (mailmap + manual)
