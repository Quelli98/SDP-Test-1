"""Smoke tests for the RAT skeleton: both ingestion paths + API contract.

Run from the project root:
    python3 -m pytest .fixtures/test_smoke.py -v
"""
import io
import subprocess
from pathlib import Path

import pytest

from app import app

FIXTURES = Path(__file__).resolve().parent
REPO_URL = f"file://{FIXTURES / 'fixture_repo'}"


@pytest.fixture(scope="session", autouse=True)
def fixture_repo():
    """Ensure the local clone-source repository exists (2 commits, 2 authors).

    Built on demand so the shipped test suite needs no binary fixture
    beyond the zip archives.
    """
    repo = FIXTURES / "fixture_repo"
    if (repo / ".git").exists():
        return repo
    repo.mkdir(parents=True, exist_ok=True)

    def git(*args, author):
        subprocess.run(
            ["git", "-C", str(repo), "-c", f"user.name={author[0]}",
             "-c", f"user.email={author[1]}", *args],
            check=True, capture_output=True, text=True,
        )

    first = ("Dev One", "one@example.com")
    second = ("Dev Two", "two@example.com")
    git("init", "-q", "-b", "main", author=first)
    (repo / "README.md").write_text("fixture\n")
    git("add", "-A", author=first)
    git("commit", "-qm", "first", author=first)
    (repo / "README.md").write_text("fixture\nsecond\n")
    git("add", "-A", author=second)
    git("commit", "-qm", "second", author=second)
    return repo


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client with repos/uploads pointed at a temp dir."""
    import app as app_module

    monkeypatch.setattr(app_module, "REPOS_DIR", tmp_path / "repos")
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path / "uploads")
    app_module.REPOS_DIR.mkdir(exist_ok=True)
    app_module.UPLOADS_DIR.mkdir(exist_ok=True)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def upload(client, zip_name):
    """POST a fixture zip as a multipart upload."""
    data = {"file": (io.BytesIO((FIXTURES / zip_name).read_bytes()), zip_name)}
    return client.post("/api/repos/upload", data=data, content_type="multipart/form-data")


# --- dashboard + repo listing -------------------------------------------------

def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"RAT" in res.data


def test_list_repos_empty(client):
    res = client.get("/api/repos")
    assert res.status_code == 200
    assert res.get_json() == []


def test_list_repos_after_ingestion(client):
    assert upload(client, "fixture.zip").status_code == 201
    repos = client.get("/api/repos").get_json()
    assert [r["name"] for r in repos] == ["fixture"]
    assert repos[0]["head"]  # HEAD hash present


# --- clone ingestion -----------------------------------------------------------

def test_clone_from_url(client):
    res = client.post("/api/repos/clone", json={"url": REPO_URL})
    assert res.status_code == 201
    body = res.get_json()
    assert body["name"] == "fixture_repo"
    assert body["head"]
    # deep clone: full history present (fixture has 2 commits)
    count = subprocess.run(
        ["git", "-C", body["path"], "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert count == "2"


def test_clone_missing_url(client):
    res = client.post("/api/repos/clone", json={})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_clone_bad_url(client):
    res = client.post("/api/repos/clone", json={"url": "file:///nonexistent/repo"})
    assert res.status_code == 400
    assert "error" in res.get_json()


# --- zip ingestion --------------------------------------------------------------

def test_upload_zip_with_top_level_dir(client):
    res = upload(client, "fixture.zip")  # .git inside fixture_repo/
    assert res.status_code == 201
    assert res.get_json()["name"] == "fixture"


def test_upload_zip_flat_layout(client):
    res = upload(client, "bare.zip")  # .git at archive root
    assert res.status_code == 201
    assert res.get_json()["name"] == "bare"


def test_upload_zip_without_git(client):
    res = upload(client, "nogit.zip")
    assert res.status_code == 400
    assert ".git" in res.get_json()["error"]


def test_upload_no_file(client):
    res = client.post("/api/repos/upload", data={})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_upload_non_zip_rejected(client):
    data = {"file": (io.BytesIO(b"not a zip"), "evil.txt")}
    res = client.post("/api/repos/upload", data=data, content_type="multipart/form-data")
    assert res.status_code == 400


def test_upload_name_collision_suffixes(client):
    assert upload(client, "fixture.zip").status_code == 201
    assert upload(client, "fixture.zip").status_code == 201
    names = sorted(r["name"] for r in client.get("/api/repos").get_json())
    assert names == ["fixture", "fixture-2"]
