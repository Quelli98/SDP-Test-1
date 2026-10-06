"""Metric engine tests against a purpose-built fixture repo.

Fixture timeline (committer dates 2026-01-01..09, all 10:00 local):
  c1 alice: +a.txt(3) +dir1/b.txt(5)         root +8
  c2 bob:   a.txt +2/-1
  c3 alice: +bin.dat(binary, excluded) b.txt +1
  c4 bob:   pure rename a.txt -> c.txt        (no metric change)
  c5 alice: rename+edit c.txt -> d.txt (+2)
  s1 bob:   +side.txt(4)          (side branch, merged in)
  merge alice: merge commit       (excluded by --no-merges)
  c7 bob:   mode-only change d.txt (0/0)
  c8 alice: delete dir1/b.txt (-6)
Non-merge commit set |H| = 8.
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent
sys.path.insert(0, str(FIXTURES))
sys.path.insert(0, str(FIXTURES.parent))

from fixture_mailmap import build_fixture as build_mailmap  # noqa: E402
from fixture_metrics import build_fixture  # noqa: E402
from rat import metrics  # noqa: E402


@pytest.fixture(scope="session")
def repo_path(tmp_path_factory):
    repo = tmp_path_factory.mktemp("metrics") / "metrics_repo"
    build_fixture(repo)
    return repo


def ts(day, hour=10):
    return int(datetime(2026, 1, day, hour).timestamp())


# --- parsing -------------------------------------------------------------------

def test_merge_commit_excluded(repo_path):
    analysis = metrics.get_analysis(repo_path)
    assert len(analysis.commits) == 8


def test_rename_attributed_to_new_path(repo_path):
    analysis = metrics.get_analysis(repo_path)
    c4 = analysis.commits[3]  # pure rename a.txt -> c.txt
    assert [(f.path, f.added, f.removed) for f in c4.files] == [("c.txt", 0, 0)]
    c5 = analysis.commits[4]  # rename + edit c.txt -> d.txt
    assert [(f.path, f.added, f.removed) for f in c5.files] == [("d.txt", 2, 0)]


def test_binary_files_not_measured(repo_path):
    analysis = metrics.get_analysis(repo_path)
    assert all("bin.dat" not in f.path for c in analysis.commits for f in c.files)
    assert "bin.dat" not in analysis.agg


def test_mode_only_change_is_zero_churn(repo_path):
    analysis = metrics.get_analysis(repo_path)
    c7 = analysis.commits[6]
    assert [(f.path, f.added, f.removed) for f in c7.files] == [("d.txt", 0, 0)]


def test_deleted_file_counts_removed_lines(repo_path):
    analysis = metrics.get_analysis(repo_path)
    c8 = analysis.commits[7]
    assert [(f.path, f.added, f.removed) for f in c8.files] == [("dir1/b.txt", 0, 6)]


# --- file / directory / repository metrics --------------------------------------

def test_file_metrics(repo_path):
    agg, count = metrics.aggregate(metrics.get_analysis(repo_path).commits)
    assert count == 8
    assert (agg["a.txt"]["added"], agg["a.txt"]["removed"]) == (5, 1)
    assert (agg["d.txt"]["added"], agg["d.txt"]["removed"]) == (2, 0)
    assert agg["c.txt"]["mods"] == 0  # pure rename touched it with zero churn


def test_directory_rollup_recursive(repo_path):
    agg, _ = metrics.aggregate(metrics.get_analysis(repo_path).commits)
    # directory metrics equal the recursive totals of their contents
    assert (agg["dir1"]["added"], agg["dir1"]["removed"], agg["dir1"]["mods"]) == (6, 6, 3)


def test_repository_metrics_are_root_metrics(repo_path):
    agg, _ = metrics.aggregate(metrics.get_analysis(repo_path).commits)
    root = agg[""]
    assert (root["added"], root["removed"], root["mods"]) == (17, 7, 6)
    # churn = added + removed; growth = added - removed
    assert root["added"] + root["removed"] == 24
    assert root["added"] - root["removed"] == 10


# --- commit set metrics ----------------------------------------------------------

def test_commit_set_metrics_and_ratios(repo_path):
    result = metrics.query(repo_path)
    obj = result["object"]
    assert result["commit_count"] == 8
    assert obj["added"] == 17 and obj["removed"] == 7
    assert obj["churn"] == 24 and obj["growth"] == 10
    assert obj["modifications"] == 6
    assert obj["frequency"] == pytest.approx(6 / 8)
    assert obj["churn_rate"] == pytest.approx(24 / 8)


def test_time_period_filter(repo_path):
    # H_t from 2026-01-04: {c4, c5, s1, c7, c8}
    result = metrics.query(repo_path, since=ts(4, 0))
    assert result["commit_count"] == 5
    assert result["object"]["added"] == 6
    assert result["object"]["removed"] == 6
    assert result["object"]["churn"] == 12
    assert result["object"]["modifications"] == 3


def test_time_window_filter(repo_path):
    # H_{i,j} = 01-02 inclusive .. 01-05 exclusive: {c2, c3, c4}
    result = metrics.query(repo_path, since=ts(2, 0), until=ts(5, 0))
    assert result["commit_count"] == 3
    assert result["object"]["added"] == 3
    assert result["object"]["removed"] == 1
    assert result["object"]["modifications"] == 2


def test_commit_subset_filter(repo_path):
    analysis = metrics.get_analysis(repo_path)
    c2 = analysis.commits[1]
    result = metrics.query(repo_path, commit_hashes=[c2.hash])
    assert result["commit_count"] == 1
    assert result["object"]["added"] == 2
    assert result["object"]["removed"] == 1


# --- author metrics ---------------------------------------------------------------

def test_author_metrics(repo_path):
    result = metrics.query(repo_path)
    by_email = {a["email"]: a for a in result["authors"]}
    alice = by_email["alice@x.com"]
    bob = by_email["bob@x.com"]
    assert alice["churn"] == 17 and alice["modifications"] == 4
    assert bob["churn"] == 7 and bob["modifications"] == 2
    assert alice["ownership"] == pytest.approx(17 / 24)
    assert bob["ownership"] == pytest.approx(7 / 24)
    assert alice["ownership"] + bob["ownership"] == pytest.approx(1.0)


def test_author_filter(repo_path):
    result = metrics.query(repo_path, author="bob@x.com")
    assert result["commit_count"] == 4  # c2, c4, s1, c7
    assert result["object"]["added"] == 6
    assert result["object"]["removed"] == 1
    assert result["object"]["churn"] == 7
    assert result["object"]["modifications"] == 2


def test_author_and_time_filter(repo_path):
    # bob since 01-04: {c4, s1, c7} -> only s1 has churn
    result = metrics.query(repo_path, author="bob@x.com", since=ts(4, 0))
    assert result["commit_count"] == 3
    assert result["object"]["added"] == 4
    assert result["object"]["churn"] == 4
    assert result["object"]["modifications"] == 1


def test_list_authors(repo_path):
    authors = {a["email"]: a for a in metrics.list_authors(repo_path)}
    assert set(authors) == {"alice@x.com", "bob@x.com"}
    assert authors["alice@x.com"]["commits"] == 4
    assert authors["bob@x.com"]["commits"] == 4
    assert authors["alice@x.com"]["churn"] == 17


# --- query shape -------------------------------------------------------------------

def test_children_of_root(repo_path):
    result = metrics.query(repo_path)
    kids = {c["path"]: c for c in result["children"]}
    assert set(kids) == {"dir1", "a.txt", "c.txt", "d.txt", "side.txt"}
    assert kids["dir1"]["type"] == "dir"
    assert kids["dir1"]["added"] == 6
    assert kids["side.txt"]["added"] == 4


def test_children_of_subdirectory(repo_path):
    result = metrics.query(repo_path, path="dir1")
    assert result["type"] == "dir"
    assert [c["path"] for c in result["children"]] == ["dir1/b.txt"]
    assert result["children"][0]["churn"] == 12


def test_file_query_has_no_children(repo_path):
    result = metrics.query(repo_path, path="d.txt")
    assert result["type"] == "file"
    assert result["children"] == []
    assert result["object"]["churn"] == 2
    owners = {a["email"]: a["ownership"] for a in result["authors"]}
    assert owners["alice@x.com"] == pytest.approx(1.0)
    assert owners["bob@x.com"] == 0


def test_analysis_cached(repo_path):
    first = metrics.get_analysis(repo_path)
    second = metrics.get_analysis(repo_path)
    assert first is second


# --- API endpoints ------------------------------------------------------------------

@pytest.fixture()
def client(repo_path, tmp_path, monkeypatch):
    import app as app_module
    repos = tmp_path / "repos"
    repos.mkdir()
    shutil.copytree(repo_path, repos / "metrics_repo")
    monkeypatch.setattr(app_module, "REPOS_DIR", repos)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_api_root_metrics(client):
    res = client.get("/api/repos/metrics_repo/metrics")
    assert res.status_code == 200
    body = res.get_json()
    assert body["commit_count"] == 8
    assert body["object"]["added"] == 17
    assert body["object"]["churn"] == 24
    assert {c["path"] for c in body["children"]} == {
        "dir1", "a.txt", "c.txt", "d.txt", "side.txt"}


def test_api_filters(client):
    res = client.get("/api/repos/metrics_repo/metrics",
                     query_string={"since": "2026-01-04"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["commit_count"] == 5
    assert body["object"]["churn"] == 12

    res = client.get("/api/repos/metrics_repo/metrics",
                     query_string={"author": "bob@x.com"})
    body = res.get_json()
    assert body["commit_count"] == 4
    assert body["object"]["churn"] == 7


def test_api_authors(client):
    res = client.get("/api/repos/metrics_repo/authors")
    assert res.status_code == 200
    authors = {a["email"]: a for a in res.get_json()["authors"]}
    assert authors["alice@x.com"]["churn"] == 17
    assert authors["bob@x.com"]["ownership"] == pytest.approx(7 / 24)


def test_api_unknown_repo(client):
    res = client.get("/api/repos/nope/metrics")
    assert res.status_code == 404
    assert "error" in res.get_json()


# --- commit browsing / manual commit-set selection ------------------------------

def test_list_commits_newest_first_with_subjects(repo_path):
    page = metrics.list_commits(repo_path)
    assert page["total"] == 8
    subjects = [c["subject"] for c in page["commits"]]
    assert subjects == ["c8", "c7", "s1", "c5", "c4", "c3", "c2", "c1"]
    assert page["commits"][0]["author_name"] == "Alice A"  # c8 is alice's
    assert page["commits"][0]["short"] == page["commits"][0]["hash"][:7]


def test_list_commits_pagination(repo_path):
    page = metrics.list_commits(repo_path, limit=3, offset=0)
    assert [c["subject"] for c in page["commits"]] == ["c8", "c7", "s1"]
    page = metrics.list_commits(repo_path, limit=3, offset=3)
    assert [c["subject"] for c in page["commits"]] == ["c5", "c4", "c3"]
    page = metrics.list_commits(repo_path, limit=3, offset=6)
    assert [c["subject"] for c in page["commits"]] == ["c2", "c1"]


def test_list_commits_search(repo_path):
    page = metrics.list_commits(repo_path, search="c5")
    assert page["total"] == 1
    assert page["commits"][0]["subject"] == "c5"
    # by author name
    assert metrics.list_commits(repo_path, search="alice")["total"] == 4
    # by author email
    assert metrics.list_commits(repo_path, search="bob@x.com")["total"] == 4
    # no match
    assert metrics.list_commits(repo_path, search="zzz-nope")["total"] == 0
    assert metrics.list_commits(repo_path, search="zzz-nope")["commits"] == []


def test_api_commits_endpoint(client):
    res = client.get("/api/repos/metrics_repo/commits")
    assert res.status_code == 200
    body = res.get_json()
    assert body["total"] == 8
    assert len(body["commits"]) == 8  # default limit 50 covers all
    assert body["commits"][0]["subject"] == "c8"


def test_api_commits_search_and_paging(client):
    res = client.get("/api/repos/metrics_repo/commits",
                     query_string={"search": "alice", "limit": 2})
    body = res.get_json()
    assert body["total"] == 4
    assert [c["subject"] for c in body["commits"]] == ["c8", "c5"]
    res = client.get("/api/repos/metrics_repo/commits",
                     query_string={"limit": 2, "offset": 2})
    body = res.get_json()
    assert body["total"] == 8
    assert [c["subject"] for c in body["commits"]] == ["s1", "c5"]


def test_list_commits_hash_search(repo_path):
    analysis = metrics.get_analysis(repo_path)
    c5 = analysis.commits[4]
    # a >= 4-char hash prefix finds its commit
    page = metrics.list_commits(repo_path, search=c5.hash[:7])
    assert page["total"] == 1
    assert page["commits"][0]["hash"] == c5.hash
    # a short hex fragment (e.g. "c5") must NOT match hashes, only subjects
    page = metrics.list_commits(repo_path, search="c5")
    assert [c["subject"] for c in page["commits"]] == ["c5"]


def test_api_commits_bad_params(client):
    res = client.get("/api/repos/metrics_repo/commits", query_string={"limit": "x"})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_api_commits_unknown_repo(client):
    res = client.get("/api/repos/nope/commits")
    assert res.status_code == 404


def test_api_metrics_with_commit_selection(client):
    # select exactly {c2} by hash -> its known metrics (a.txt +2/-1)
    page = client.get("/api/repos/metrics_repo/commits",
                      query_string={"search": "c2"}).get_json()
    c2_hash = page["commits"][0]["hash"]
    res = client.get("/api/repos/metrics_repo/metrics",
                     query_string={"commits": c2_hash})
    body = res.get_json()
    assert body["commit_count"] == 1
    assert body["object"]["added"] == 2
    assert body["object"]["removed"] == 1
    assert body["object"]["churn"] == 3


# --- author merging (mailmap + manual) ------------------------------------------

@pytest.fixture()
def merge_repo(repo_path, tmp_path):
    """Fresh copy of the metrics fixture so merge files never leak across tests."""
    copy = tmp_path / "merge_repo"
    shutil.copytree(repo_path, copy)
    return copy


@pytest.fixture()
def mailmap_repo(tmp_path):
    repo = tmp_path / "mailmap_repo"
    build_mailmap(repo)
    return repo


def test_mailmap_merges_identities(mailmap_repo):
    analysis = metrics.get_analysis(mailmap_repo)
    assert analysis.author_commits == {"same@new.com": 3}
    assert analysis.author_names == {"same@new.com": "New Name"}
    # identity merging never changes object-level metrics
    assert (analysis.agg["f.txt"]["added"], analysis.agg["f.txt"]["removed"]) == (2, 0)


def test_mailmap_cache_invalidated_when_file_changes(mailmap_repo):
    first = metrics.get_analysis(mailmap_repo)
    assert first.author_commits == {"same@new.com": 3}
    os.remove(mailmap_repo / ".mailmap")  # git reads .mailmap from the worktree
    second = metrics.get_analysis(mailmap_repo)
    assert second is not first
    assert second.author_commits == {"same@old.com": 1, "same@new.com": 2}


def test_manual_merge_combines_author_metrics(merge_repo):
    before = metrics.query(merge_repo)
    assert {a["email"] for a in before["authors"]} == {"alice@x.com", "bob@x.com"}
    metrics.save_merges(merge_repo, {"bob@x.com": "alice@x.com"})
    after = metrics.query(merge_repo)
    # author identities fold into the canonical author...
    assert [a["email"] for a in after["authors"]] == ["alice@x.com"]
    merged = after["authors"][0]
    assert merged["churn"] == 24 and merged["modifications"] == 6
    assert merged["ownership"] == pytest.approx(1.0)
    # ...while object-level metrics are untouched
    assert after["commit_count"] == 8
    assert (after["object"]["added"], after["object"]["removed"]) == (17, 7)


def test_manual_merge_author_filter_uses_canonical(merge_repo):
    metrics.save_merges(merge_repo, {"bob@x.com": "alice@x.com"})
    assert metrics.query(merge_repo, author="bob@x.com")["commit_count"] == 0
    alice = metrics.query(merge_repo, author="alice@x.com")
    assert alice["commit_count"] == 8
    assert alice["object"]["churn"] == 24


def test_manual_merge_with_time_filter(merge_repo):
    metrics.save_merges(merge_repo, {"bob@x.com": "alice@x.com"})
    result = metrics.query(merge_repo, since=ts(4, 0))
    assert result["commit_count"] == 5
    assert result["object"]["churn"] == 12
    assert [a["email"] for a in result["authors"]] == ["alice@x.com"]
    assert result["authors"][0]["churn"] == 12


def test_manual_merge_list_authors(merge_repo):
    metrics.save_merges(merge_repo, {"bob@x.com": "alice@x.com"})
    authors = metrics.list_authors(merge_repo)
    assert [(a["email"], a["commits"], a["churn"]) for a in authors] == \
        [("alice@x.com", 8, 24)]


def test_resolve_author_follows_chains_and_survives_cycles():
    chain = {"a@x.com": "b@x.com", "b@x.com": "c@x.com"}
    assert metrics._resolve_author("a@x.com", chain) == "c@x.com"
    assert metrics._resolve_author("c@x.com", chain) == "c@x.com"
    cycle = {"a@x.com": "b@x.com", "b@x.com": "a@x.com"}
    assert metrics._resolve_author("a@x.com", cycle) == "a@x.com"
    assert metrics._resolve_author("zz@x.com", {}) == "zz@x.com"


def test_merge_persistence_roundtrip(merge_repo):
    merges = {"bob@x.com": "alice@x.com"}
    metrics.save_merges(merge_repo, merges)
    assert metrics.load_merges(merge_repo) == merges
    os.remove(merge_repo / ".rat-merges.json")
    assert metrics.load_merges(merge_repo) == {}
    (merge_repo / ".rat-merges.json").write_text("not json")
    assert metrics.load_merges(merge_repo) == {}


def test_api_merges_empty(client):
    res = client.get("/api/repos/metrics_repo/merges")
    assert res.status_code == 200
    body = res.get_json()
    assert body["repo"] == "metrics_repo"
    assert body["merges"] == {}
    assert body["mailmap"] is False


def test_api_merges_put_reaggregates(client):
    res = client.put("/api/repos/metrics_repo/merges",
                     json={"merges": {"bob@x.com": "alice@x.com"}})
    assert res.status_code == 200
    assert res.get_json()["merges"] == {"bob@x.com": "alice@x.com"}
    # persisted...
    assert client.get("/api/repos/metrics_repo/merges").get_json()["merges"] == \
        {"bob@x.com": "alice@x.com"}
    # ...and metrics re-aggregate to the merged author
    body = client.get("/api/repos/metrics_repo/metrics").get_json()
    assert [a["email"] for a in body["authors"]] == ["alice@x.com"]
    assert body["authors"][0]["churn"] == 24
    authors = client.get("/api/repos/metrics_repo/authors").get_json()["authors"]
    assert [(a["email"], a["commits"]) for a in authors] == [("alice@x.com", 8)]


def test_api_merges_validation(client):
    checks = [
        ({"merges": {"alice@x.com": "alice@x.com"}}, "self-merge"),
        ({"merges": {"ghost@x.com": "alice@x.com"}}, "unknown alias"),
        ({"merges": {"bob@x.com": "ghost@x.com"}}, "unknown canonical"),
        ({"merges": ["not", "a", "dict"]}, "not a mapping"),
        ({}, "missing merges key"),
    ]
    for payload, label in checks:
        res = client.put("/api/repos/metrics_repo/merges", json=payload)
        assert res.status_code == 400, label
    assert client.put("/api/repos/nope/merges",
                      json={"merges": {}}).status_code == 404


def test_api_merges_empty_map_clears(client):
    client.put("/api/repos/metrics_repo/merges",
               json={"merges": {"bob@x.com": "alice@x.com"}})
    res = client.put("/api/repos/metrics_repo/merges", json={"merges": {}})
    assert res.status_code == 200
    assert client.get("/api/repos/metrics_repo/merges").get_json()["merges"] == {}
    authors = client.get("/api/repos/metrics_repo/authors").get_json()["authors"]
    assert {a["email"] for a in authors} == {"alice@x.com", "bob@x.com"}


@pytest.fixture()
def mailmap_client(tmp_path, monkeypatch):
    import app as app_module
    repos = tmp_path / "repos"
    repos.mkdir()
    build_mailmap(repos / "mailmap_repo")
    monkeypatch.setattr(app_module, "REPOS_DIR", repos)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_api_mailmap_repo_reports_and_merges(mailmap_client):
    res = mailmap_client.get("/api/repos/mailmap_repo/merges")
    assert res.status_code == 200
    assert res.get_json()["mailmap"] is True
    authors = mailmap_client.get("/api/repos/mailmap_repo/authors").get_json()["authors"]
    assert [(a["email"], a["commits"], a["churn"]) for a in authors] == \
        [("same@new.com", 3, 3)]


# --- multi-repo overview / summaries ---------------------------------------------

def test_api_summary(client):
    res = client.get("/api/repos/metrics_repo/summary")
    assert res.status_code == 200
    body = res.get_json()
    assert body["repo"] == "metrics_repo"
    assert body["head"]
    assert body["commits"] == 8
    assert body["added"] == 17 and body["removed"] == 7
    assert body["growth"] == 10 and body["churn"] == 24
    assert body["modifications"] == 6
    assert body["authors"] == 2


def test_api_summary_applies_merges(client):
    client.put("/api/repos/metrics_repo/merges",
               json={"merges": {"bob@x.com": "alice@x.com"}})
    body = client.get("/api/repos/metrics_repo/summary").get_json()
    assert body["authors"] == 1  # identities folded
    assert body["churn"] == 24   # object metrics unchanged by merging


def test_api_summary_unknown_repo(client):
    assert client.get("/api/repos/nope/summary").status_code == 404


@pytest.fixture()
def multi_client(repo_path, tmp_path, monkeypatch):
    """Two ingested repositories side by side: the metrics fixture and the
    mailmap fixture (different sizes, authors, and merge mechanisms)."""
    import app as app_module
    repos = tmp_path / "repos"
    repos.mkdir()
    shutil.copytree(repo_path, repos / "metrics_repo")
    build_mailmap(repos / "mailmap_repo")
    monkeypatch.setattr(app_module, "REPOS_DIR", repos)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def test_multi_repo_listing_and_summaries(multi_client):
    repos = multi_client.get("/api/repos").get_json()
    assert {r["name"] for r in repos} == {"metrics_repo", "mailmap_repo"}
    m = multi_client.get("/api/repos/metrics_repo/summary").get_json()
    assert (m["commits"], m["churn"], m["authors"]) == (8, 24, 2)
    mm = multi_client.get("/api/repos/mailmap_repo/summary").get_json()
    assert (mm["commits"], mm["churn"], mm["authors"]) == (3, 3, 1)
    # independent analyses: the same root object carries different totals
    assert m["head"] != mm["head"]


def test_multi_repo_merge_isolation(multi_client):
    multi_client.put("/api/repos/metrics_repo/merges",
                     json={"merges": {"bob@x.com": "alice@x.com"}})
    # the merge applies to metrics_repo...
    assert multi_client.get("/api/repos/metrics_repo/summary").get_json()["authors"] == 1
    # ...and never leaks into the sibling repository
    sibling = multi_client.get("/api/repos/mailmap_repo/merges").get_json()
    assert sibling["merges"] == {} and sibling["mailmap"] is True
    authors = multi_client.get("/api/repos/mailmap_repo/authors").get_json()["authors"]
    assert [a["email"] for a in authors] == ["same@new.com"]
    assert multi_client.get("/api/repos/mailmap_repo/summary").get_json()["churn"] == 3


def test_multi_repo_metrics_independent(multi_client):
    # drill into the same-named path in both repos: values differ per repo
    a = multi_client.get("/api/repos/metrics_repo/metrics",
                         query_string={"path": "a.txt"}).get_json()
    b = multi_client.get("/api/repos/mailmap_repo/metrics",
                         query_string={"path": "f.txt"}).get_json()
    assert a["object"]["added"] == 5 and a["object"]["removed"] == 1
    assert b["object"]["added"] == 2 and b["object"]["removed"] == 0


# --- persistent metric cache (.rat-cache.json) ------------------------------------

def test_disk_cache_restores_after_restart(repo_path, monkeypatch):
    first = metrics.get_analysis(repo_path)
    assert (repo_path / ".rat-cache.json").exists()
    # simulate a server restart: the in-memory cache is gone
    del metrics._ANALYSIS_CACHE[str(repo_path)]

    def no_parse(*args, **kwargs):
        raise AssertionError("git re-parsed despite a valid disk cache")

    monkeypatch.setattr(metrics, "_parse_repo", no_parse)
    second = metrics.get_analysis(repo_path)
    assert second is not first
    # the restored analysis is faithful to the original
    assert [c.hash for c in second.commits] == [c.hash for c in first.commits]
    assert [(c.author_name, c.author_email, c.committer_ts, c.subject,
             [(f.path, f.added, f.removed) for f in c.files])
            for c in second.commits] == \
           [(c.author_name, c.author_email, c.committer_ts, c.subject,
             [(f.path, f.added, f.removed) for f in c.files])
            for c in first.commits]
    assert second.agg == first.agg
    assert second.author_commits == first.author_commits
    assert second.author_names == first.author_names


def test_disk_cache_stale_after_head_moves(repo_path, tmp_path):
    copy = tmp_path / "head_repo"
    shutil.copytree(repo_path, copy)
    first = metrics.get_analysis(copy)
    assert (copy / ".rat-cache.json").exists()
    # advance HEAD; the saved cache must not be trusted afterwards
    subprocess.run(
        ["git", "-C", str(copy), "-c", "user.name=A", "-c", "user.email=a@x.com",
         "commit", "--allow-empty", "-qm", "advance"],
        check=True, capture_output=True, text=True)
    second = metrics.get_analysis(copy)
    assert second is not first
    assert second.head != first.head
    assert len(second.commits) == len(first.commits) + 1
    # and the refreshed cache is valid again
    del metrics._ANALYSIS_CACHE[str(copy)]
    third = metrics.get_analysis(copy)
    assert third.head == second.head
    assert len(third.commits) == len(second.commits)


def test_disk_cache_corrupt_falls_back_to_git(repo_path):
    metrics.get_analysis(repo_path)
    (repo_path / ".rat-cache.json").write_text("{not json at all")
    del metrics._ANALYSIS_CACHE[str(repo_path)]
    analysis = metrics.get_analysis(repo_path)  # must not raise
    assert len(analysis.commits) == 8  # fresh git parse, not the corrupt file
    assert (repo_path / ".rat-cache.json").exists()  # healthy cache re-saved


def test_disk_cache_wrong_types_fall_back_to_git(repo_path):
    metrics.get_analysis(repo_path)
    (repo_path / ".rat-cache.json").write_text(
        '{"head": "x", "mailmap_mtime": null, "commits": [[1, 2, 3]]}')
    del metrics._ANALYSIS_CACHE[str(repo_path)]
    analysis = metrics.get_analysis(repo_path)
    assert len(analysis.commits) == 8


# --- JSON error handling -----------------------------------------------------------

def test_api_error_handlers_return_json(client):
    res = client.get("/api/nonexistent")
    assert res.status_code == 404
    assert res.get_json()["error"].startswith("Unknown endpoint")
    res = client.post("/api/repos/metrics_repo/metrics")
    assert res.status_code == 405
    assert res.get_json()["error"] == "Method not allowed on this endpoint."


def test_api_internal_error_returns_json(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(metrics, "query", boom)
    res = client.get("/api/repos/metrics_repo/metrics")
    assert res.status_code == 500
    assert res.get_json()["error"] == "Internal server error."
