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
import re
import subprocess
import threading
from dataclasses import dataclass

# numstat entry: "<added>\t<removed>\t<rest>"; with -z a rename emits an
# empty <rest> followed by two NUL-separated fields: old path, new path.
_COUNTS = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$", re.DOTALL)

_LOG_ARGS = [
    "log", "--no-merges", "-M", "--numstat", "-z",
    "--format=%x1e%H%x1f%an%x1f%ae%x1f%ct",
]


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
    files: list  # list[FileChange]


@dataclass
class Analysis:
    head: str | None
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
        if len(parts) != 4:  # defensive: malformed header (e.g. odd author name)
            continue
        commit_hash, name, email, ts = parts
        try:
            commits.append(Commit(commit_hash, name, email, int(ts),
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


def aggregate(commits, *, author=None, since=None, until=None, commit_hashes=None):
    """Single pass over a commit subset -> (agg, commit_count).

    agg maps every touched object path (files, their ancestor directories,
    and the root "") to per-object totals; per-author totals ride along.
    """
    selected = set(commit_hashes) if commit_hashes else None
    agg = {}
    count = 0
    for commit in commits:
        if since is not None and commit.committer_ts < since:
            continue
        if until is not None and commit.committer_ts >= until:
            continue
        if author is not None and commit.author_email != author:
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
            auth = obj["authors"].get(commit.author_email)
            if auth is None:
                auth = obj["authors"][commit.author_email] = [0, 0, 0]
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
    """Parse (or reuse) a repository's full history analysis."""
    key = str(repo_path)
    with _CACHE_LOCK:
        head = _head(key)
        cached = _ANALYSIS_CACHE.get(key)
        if cached is not None and cached.head == head:
            return cached
        commits = _parse_repo(key)
        agg, _ = aggregate(commits)
        names, counts = {}, {}
        for commit in commits:
            names[commit.author_email] = commit.author_name
            counts[commit.author_email] = counts.get(commit.author_email, 0) + 1
        analysis = Analysis(head, commits, agg, _dir_set(agg), names, counts)
        _ANALYSIS_CACHE[key] = analysis
        return analysis


def query(repo_path, *, path="", author=None, since=None, until=None,
          commit_hashes=None) -> dict:
    """Metrics for one object over a filtered commit set."""
    analysis = get_analysis(repo_path)
    if since is None and until is None and not commit_hashes:
        if author is None:
            agg, count = analysis.agg, len(analysis.commits)
        else:  # fast path: slice the cached full aggregation
            agg = _author_slice(analysis.agg, author)
            count = analysis.author_commits.get(author, 0)
    else:
        agg, count = aggregate(analysis.commits, author=author, since=since,
                               until=until, commit_hashes=commit_hashes)
    return _build_response(analysis, agg, count, path)


def list_authors(repo_path) -> list[dict]:
    """All authors with repository-root totals (for filter dropdowns)."""
    analysis = get_analysis(repo_path)
    root = analysis.agg.get("", {"added": 0, "removed": 0, "mods": 0,
                                 "authors": {}})
    total_churn = root["added"] + root["removed"]
    authors = []
    for email, (added, removed, mods) in root["authors"].items():
        churn = added + removed
        authors.append({
            "name": analysis.author_names.get(email, email),
            "email": email,
            "commits": analysis.author_commits.get(email, 0),
            "churn": churn,
            "modifications": mods,
            "ownership": churn / total_churn if total_churn else 0,
        })
    authors.sort(key=lambda a: -a["churn"])
    return authors
