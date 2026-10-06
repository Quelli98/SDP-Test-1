"""RAT — Repo Analysis Tool.

Web dashboard that ingests git repositories (remote URL or zip upload)
and analyses their metrics.
"""
import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

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


if __name__ == "__main__":
    # use_reloader=False: the reloader watches the whole tree and restarts the
    # server mid-request when ingestion writes files into repos/ and uploads/.
    app.run(debug=True, use_reloader=False)
