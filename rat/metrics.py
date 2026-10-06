"""Metric engine: one `git log` pass feeds all five metric categories.

File, directory, repository, commit-set and author metrics are all derived
from a single `git log --numstat -M --no-merges` parse:
- renames are detected at the 50% threshold and attributed to the new path,
- binary files (numstat "-") are not measured,
- deleted files appear as all-lines-removed on their path,
- directory metrics are recursive rollups over immediate children,
- repository metrics are directory metrics at the root ("").

Parsed history is cached per repository and invalidated when HEAD moves.
"""
import json
import re
import subprocess
import threading
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

# numstat entry: "<added>\t<removed>\t<rest>"; with -z a rename emits an
# empty <rest> followed by two NUL-separated fields: old path, new path.
_COUNTS = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$", re.DOTALL)

# %aN/%aE are the mailmap-aware placeholders: a repo's .mailmap merges
# author identities automatically, with no extra git invocation.
_LOG_ARGS = [
    "log", "--no-merges", "-M", "--numstat", "-z",
    "--format=%x1e%H%x1f%aN%x1f%aE%x1f%ct%x1f%s",
]

_MERGES_FILE = ".rat-merges.json"


class MetricsError(Exception):
    """Raised when a repository's history cannot be analysed."""


@dataclass
class FileChange:
    path: str
    added: int
    removed: int


@dataclass
class Commit:
    hash: str
    author_name: str
    author_email: str
    committer_ts: int
    subject: str
    files: list  # list[FileChange]


@dataclass
class Analysis:
    head: str | None
    mailmap_mtime: int | None
    commits: list
    agg: dict          # path -> {"added", "removed", "mods", "authors"}
    dirs: set          # every directory path that ever held a changed file
    author_names: dict  # email -> display name (latest wins)
    author_commits: dict  # email -> commit count


def parse_log(raw: str) -> list[Commit]:
    """Parse `git log --numstat -z` output into commits (oldest first)."""
    commits = []
    for chunk in raw.split("\x1e"):
        if not chunk.strip("\n\x00"):
            continue
        fields = chunk.split("\0")
        parts = fields[0].split("\x1f")
        if len(parts) != 5:  # defensive: malformed header (e.g. odd author name)
            continue
        commit_hash, name, email, ts, subject = parts
        try:
            commits.append(Commit(commit_hash, name, email, int(ts), subject,
                                  _parse_numstat(fields[1:])))
        except ValueError:
            continue
    commits.reverse()  # git log emits newest first
    return commits


def _parse_numstat(fields: list[str]) -> list[FileChange]:
    """Parse NUL-separated numstat fields of one commit."""
    files = []
    i = 0
    while i < len(fields):
        field = fields[i].strip("\n")
        match = _COUNTS.match(field)
        if not match:
            i += 1  # defensive: stray separator
            continue
        added_s, removed_s, rest = match.groups()
        if rest == "":  # rename: old path and new path follow as two fields
            path = fields[i + 2] if i + 2 < len(fields) else ""
            i += 3
        else:
            path = rest
            i += 1
        if added_s == "-" or removed_s == "-":
            continue  # binary files are not measured
        files.append(FileChange(path, int(added_s), int(removed_s)))
    return files


def aggregate(commits, *, author=None, since=None, until=None,
              commit_hashes=None, merge_map=None):
    """Single pass over a commit subset -> (agg, commit_count).

    agg maps every touched object path (files, their ancestor directories,
    and the root "") to per-object totals; per-author totals ride along.
    merge_map translates alias author emails to canonical ones (manual
    author merging); it never changes object-level totals.
    """
    selected = set(commit_hashes) if commit_hashes else None
    agg = {}
    count = 0
    for commit in commits:
        email = (_resolve_author(commit.author_email, merge_map)
                 if merge_map else commit.author_email)
        if since is not None and commit.committer_ts < since:
            continue
        if until is not None and commit.committer_ts >= until:
            continue
        if author is not None and email != author:
            continue
        if selected is not None and not any(
            commit.hash == h or commit.hash.startswith(h) for h in selected
        ):
            continue
        count += 1
        # roll this commit's file changes up through ancestor directories
        touched = {}
        for change in commit.files:
            path = change.path
            while True:
                entry = touched.get(path)
                if entry is None:
                    entry = touched[path] = [0, 0]
                entry[0] += change.added
                entry[1] += change.removed
                slash = path.rfind("/")
                if slash <= 0:
                    break
                path = path[:slash]
            root = touched.setdefault("", [0, 0])
            root[0] += change.added
            root[1] += change.removed
        for path, (added, removed) in touched.items():
            obj = agg.get(path)
            if obj is None:
                obj = agg[path] = {"added": 0, "removed": 0, "mods": 0,
                                   "authors": {}}
            obj["added"] += added
            obj["removed"] += removed
            auth = obj["authors"].get(email)
            if auth is None:
                auth = obj["authors"][email] = [0, 0, 0]
            auth[0] += added
            auth[1] += removed
            if added + removed > 0:  # modifications require non-zero churn
                obj["mods"] += 1
                auth[2] += 1
    return agg, count


def _dir_set(agg) -> set:
    dirs = {""}
    for path in agg:
        while "/" in path:
            path = path.rsplit("/", 1)[0]
            dirs.add(path)
    return dirs


def _summary(entry, count):
    if entry is None:
        entry = {"added": 0, "removed": 0, "mods": 0}
    added, removed, mods = entry["added"], entry["removed"], entry["mods"]
    return {
        "added": added,
        "removed": removed,
        "growth": added - removed,
        "churn": added + removed,
        "modifications": mods,
        "frequency": mods / count if count else 0,
        "churn_rate": (added + removed) / count if count else 0,
    }


def _author_slice(agg, email):
    """Fast path: slice a full aggregation down to one author's totals."""
    sliced = {}
    for path, obj in agg.items():
        auth = obj["authors"].get(email)
        if auth is None:
            continue
        sliced[path] = {"added": auth[0], "removed": auth[1], "mods": auth[2],
                        "authors": {email: list(auth)}}
    return sliced


def _build_response(analysis, agg, count, path):
    entry = agg.get(path)
    is_dir = path == "" or path in analysis.dirs
    children = []
    if is_dir:
        prefix = path + "/" if path else ""
        kids = {}
        for key in agg:
            if key == path or not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            kids.setdefault(prefix + rest.split("/", 1)[0])
        for kid in sorted(kids, key=lambda k: (k not in analysis.dirs,
                                               k.lower())):
            children.append({
                "path": kid,
                "name": kid[len(prefix):],
                "type": "dir" if kid in analysis.dirs else "file",
                **_summary(agg.get(kid), count),
            })
    authors = []
    if entry:
        total_churn = entry["added"] + entry["removed"]
        for email, (added, removed, mods) in entry["authors"].items():
            churn = added + removed
            authors.append({
                "name": analysis.author_names.get(email, email),
                "email": email,
                "churn": churn,
                "modifications": mods,
                "ownership": churn / total_churn if total_churn else 0,
            })
        authors.sort(key=lambda a: -a["churn"])
    return {
        "path": path,
        "type": "dir" if is_dir else "file",
        "commit_count": count,
        "object": _summary(entry, count),
        "children": children,
        "authors": authors,
    }


def _head(repo_path: str) -> str | None:
    proc = subprocess.run(["git", "-C", repo_path, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _mailmap_mtime(repo_path: str) -> int | None:
    """Mtime of a repo's .mailmap, to invalidate the parse cache."""
    try:
        return (Path(repo_path) / ".mailmap").stat().st_mtime_ns
    except OSError:
        return None


def load_merges(repo_path) -> dict:
    """Manual alias -> canonical author merges persisted beside the repo."""
    try:
        data = json.loads((Path(repo_path) / _MERGES_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {alias: canonical for alias, canonical in data.items()
            if isinstance(alias, str) and isinstance(canonical, str)}


def save_merges(repo_path, merges: dict) -> dict:
    """Persist the manual merge map beside the repo and return it."""
    (Path(repo_path) / _MERGES_FILE).write_text(
        json.dumps(merges, indent=2, sort_keys=True))
    return merges


def _resolve_author(email: str, mapping: dict) -> str:
    """Follow alias -> canonical chains to a fixed point (cycle-safe)."""
    seen = set()
    while email in mapping and email not in seen:
        seen.add(email)
        email = mapping[email]
    return email


def _parse_repo(repo_path: str) -> list[Commit]:
    proc = subprocess.run(["git", "-C", repo_path, *_LOG_ARGS],
                          capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        if "does not have any commits" in stderr:
            return []
        raise MetricsError(stderr or f"git log failed in {repo_path}")
    return parse_log(proc.stdout.decode("utf-8", "replace"))


_ANALYSIS_CACHE: dict[str, Analysis] = {}
_CACHE_LOCK = threading.Lock()


def get_analysis(repo_path) -> Analysis:
    """Parse (or reuse) a repository's full history analysis.

    The cache key is (HEAD, .mailmap mtime): editing the mailmap changes
    author identities without moving HEAD.
    """
    key = str(repo_path)
    with _CACHE_LOCK:
        head = _head(key)
        mailmap_mtime = _mailmap_mtime(key)
        cached = _ANALYSIS_CACHE.get(key)
        if (cached is not None and cached.head == head
                and cached.mailmap_mtime == mailmap_mtime):
            return cached
        commits = _parse_repo(key)
        agg, _ = aggregate(commits)
        names, counts = {}, {}
        for commit in commits:
            names[commit.author_email] = commit.author_name
            counts[commit.author_email] = counts.get(commit.author_email, 0) + 1
        analysis = Analysis(head, mailmap_mtime, commits, agg,
                            _dir_set(agg), names, counts)
        _ANALYSIS_CACHE[key] = analysis
        return analysis


def query(repo_path, *, path="", author=None, since=None, until=None,
          commit_hashes=None) -> dict:
    """Metrics for one object over a filtered commit set."""
    analysis = get_analysis(repo_path)
    merges = load_merges(repo_path)
    if not merges and since is None and until is None and not commit_hashes:
        if author is None:
            agg, count = analysis.agg, len(analysis.commits)
        else:  # fast path: slice the cached full aggregation
            agg = _author_slice(analysis.agg, author)
            count = analysis.author_commits.get(author, 0)
    else:
        agg, count = aggregate(analysis.commits, author=author, since=since,
                               until=until, commit_hashes=commit_hashes,
                               merge_map=merges or None)
    return _build_response(analysis, agg, count, path)


def list_authors(repo_path) -> list[dict]:
    """All authors with repository-root totals (for filter dropdowns).

    Applies manual merges: aliases disappear into their canonical author.
    """
    analysis = get_analysis(repo_path)
    merges = load_merges(repo_path)
    if merges:
        agg, _ = aggregate(analysis.commits, merge_map=merges)
        root = agg.get("", {"added": 0, "removed": 0, "mods": 0,
                            "authors": {}})
        counts = {}
        for commit in analysis.commits:
            email = _resolve_author(commit.author_email, merges)
            counts[email] = counts.get(email, 0) + 1
    else:
        root = analysis.agg.get("", {"added": 0, "removed": 0, "mods": 0,
                                     "authors": {}})
        counts = analysis.author_commits
    total_churn = root["added"] + root["removed"]
    authors = []
    for email, (added, removed, mods) in root["authors"].items():
        churn = added + removed
        authors.append({
            "name": analysis.author_names.get(email, email),
            "email": email,
            "commits": counts.get(email, 0),
            "churn": churn,
            "modifications": mods,
            "ownership": churn / total_churn if total_churn else 0,
        })
    authors.sort(key=lambda a: -a["churn"])
    return authors


def list_commits(repo_path, *, search=None, limit=50, offset=0):
    """Browseable commit list (newest first) for manual commit-set selection."""
    analysis = get_analysis(repo_path)
    commits = analysis.commits
    if search:
        needle = search.lower()
        # short needles match subject/author only; >= 4 chars also match
        # hashes (git short-hash convention), avoiding spurious hex hits
        use_hash = len(needle) >= 4
        commits = [c for c in commits
                   if needle in c.subject.lower()
                   or needle in c.author_name.lower()
                   or needle in c.author_email.lower()
                   or (use_hash and needle in c.hash)]
    window = list(islice(reversed(commits), offset, offset + limit))
    return {
        "total": len(commits),
        "commits": [{
            "hash": c.hash,
            "short": c.hash[:7],
            "author_name": c.author_name,
            "author_email": c.author_email,
            "committer_ts": c.committer_ts,
            "subject": c.subject,
        } for c in window],
    }
