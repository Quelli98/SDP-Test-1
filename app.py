"""RAT — Repo Analysis Tool.

Web dashboard that ingests git repositories (remote URL or zip upload)
and analyses their metrics.
"""
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from rat import metrics
from rat.ingestion import IngestionError, clone_repo, extract_zip, list_repos

BASE_DIR = Path(__file__).resolve().parent
REPOS_DIR = BASE_DIR / "repos"
UPLOADS_DIR = BASE_DIR / "uploads"
for directory in (REPOS_DIR, UPLOADS_DIR):
    directory.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # accept large repo zips


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/repos")
def get_repos():
    return jsonify(list_repos(REPOS_DIR))


@app.post("/api/repos/clone")
def api_clone():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not url:
        return jsonify(error="A repository URL is required."), 400
    try:
        repo = clone_repo(url, REPOS_DIR)
    except IngestionError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(repo), 201


@app.post("/api/repos/upload")
def api_upload():
    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        return jsonify(error="No zip file was uploaded."), 400
    if not upload.filename.lower().endswith(".zip"):
        return jsonify(error="Only .zip archives are accepted."), 400

    fd, tmp_name = tempfile.mkstemp(suffix=".zip", dir=UPLOADS_DIR)
    os.close(fd)
    upload.save(tmp_name)
    try:
        repo = extract_zip(Path(tmp_name), REPOS_DIR, upload.filename)
    except IngestionError as exc:
        return jsonify(error=str(exc)), 400
    finally:
        os.unlink(tmp_name)
    return jsonify(repo), 201


def _repo_path_or_error(name):
    """Resolve an ingested repository directory or return (None, error)."""
    if not name or "/" in name or name in (".", ".."):
        return None, (jsonify(error="Invalid repository name."), 400)
    path = REPOS_DIR / name
    if not (path / ".git").exists():
        return None, (jsonify(error=f"Unknown repository: {name}"), 404)
    return path, None


def _parse_timestamp(value, is_end=False):
    """Accept a unix timestamp or ISO date/datetime; None when absent.

    Date-only values cover the whole day: 'since' starts at 00:00, 'until'
    is exclusive at the following midnight (H_{i,j} has i inclusive, j
    exclusive).
    """
    if not value:
        return None
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if is_end and len(value) <= 10:  # date-only end: exclusive next midnight
        moment += timedelta(days=1)
    return int(moment.timestamp())


@app.get("/api/repos/<name>/metrics")
def api_metrics(name):
    repo_path, error = _repo_path_or_error(name)
    if error:
        return error
    path = (request.args.get("path") or "").strip().strip("/")
    if ".." in path.split("/"):
        return jsonify(error="Invalid path."), 400
    author = request.args.get("author") or None
    since = _parse_timestamp(request.args.get("since"))
    until = _parse_timestamp(request.args.get("until"), is_end=True)
    if request.args.get("since") and since is None:
        return jsonify(error="Invalid 'since' timestamp."), 400
    if request.args.get("until") and until is None:
        return jsonify(error="Invalid 'until' timestamp."), 400
    hashes = [h for h in request.args.get("commits", "").split(",") if h] or None
    try:
        result = metrics.query(repo_path, path=path, author=author, since=since,
                               until=until, commit_hashes=hashes)
    except metrics.MetricsError as exc:
        return jsonify(error=str(exc)), 400
    result["repo"] = name
    result["head"] = metrics.get_analysis(repo_path).head
    return jsonify(result)


@app.get("/api/repos/<name>/authors")
def api_authors(name):
    repo_path, error = _repo_path_or_error(name)
    if error:
        return error
    try:
        authors = metrics.list_authors(repo_path)
    except metrics.MetricsError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify({"repo": name, "authors": authors})


@app.get("/api/repos/<name>/commits")
def api_commits(name):
    repo_path, error = _repo_path_or_error(name)
    if error:
        return error
    try:
        limit = min(int(request.args.get("limit", 50)), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify(error="'limit' and 'offset' must be integers."), 400
    try:
        return jsonify(metrics.list_commits(
            repo_path, search=request.args.get("search") or None,
            limit=limit, offset=offset))
    except metrics.MetricsError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/repos/<name>/merges")
def api_get_merges(name):
    repo_path, error = _repo_path_or_error(name)
    if error:
        return error
    return jsonify({
        "repo": name,
        "merges": metrics.load_merges(repo_path),
        "mailmap": (repo_path / ".mailmap").exists(),
    })


@app.put("/api/repos/<name>/merges")
def api_put_merges(name):
    repo_path, error = _repo_path_or_error(name)
    if error:
        return error
    merges = (request.get_json(silent=True) or {}).get("merges")
    if not isinstance(merges, dict) or not all(
            isinstance(alias, str) and isinstance(canonical, str)
            for alias, canonical in merges.items()):
        return jsonify(error="'merges' must map alias email to canonical email."), 400
    try:
        known = set(metrics.get_analysis(repo_path).author_commits)
    except metrics.MetricsError as exc:
        return jsonify(error=str(exc)), 400
    for alias, canonical in merges.items():
        if alias == canonical:
            return jsonify(error=f"Cannot merge {alias} into itself."), 400
        if alias not in known:
            return jsonify(error=f"Unknown author: {alias}"), 400
        if canonical not in known:
            return jsonify(error=f"Unknown author: {canonical}"), 400
    try:
        saved = metrics.save_merges(repo_path, merges)
    except OSError as exc:
        return jsonify(error=f"Could not save merges: {exc}"), 500
    return jsonify({"repo": name, "merges": saved})


if __name__ == "__main__":
    # use_reloader=False: the reloader watches the whole tree and restarts the
    # server mid-request when ingestion writes files into repos/ and uploads/.
    app.run(debug=True, use_reloader=False)
