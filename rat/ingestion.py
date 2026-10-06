"""Repository ingestion: clone from a remote URL or extract an uploaded zip."""
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


class IngestionError(Exception):
    """Raised when a repository cannot be ingested."""


def _run_git(*args: str) -> str:
    """Run a git command, raising IngestionError with stderr on failure."""
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise IngestionError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _safe_name(name: str) -> str:
    """Sanitise a filename/URL tail into a safe directory name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return name or "repo"


def _unique_dir(repos_dir: Path, name: str) -> Path:
    """Return a non-existent target directory, suffixing -2, -3, ... on collision."""
    candidate = repos_dir / name
    counter = 2
    while candidate.exists():
        candidate = repos_dir / f"{name}-{counter}"
        counter += 1
    return candidate


def _head(repo_path: Path) -> str | None:
    """Short HEAD hash of a repository, or None if it has no commits."""
    try:
        return _run_git("-C", str(repo_path), "rev-parse", "--short", "HEAD").strip()
    except IngestionError:
        return None


def clone_repo(url: str, repos_dir: Path) -> dict:
    """Deep-clone a remote repository URL into repos_dir."""
    if not re.match(r"^(https?|git|ssh|file)://", url) and ":" not in url.split("/")[0]:
        raise IngestionError(f"Unsupported repository URL: {url}")
    tail = url.rstrip("/").split("/")[-1]
    name = _safe_name(tail.removesuffix(".git") or tail)
    dest = _unique_dir(repos_dir, name)
    try:
        # Deep clone: full history is required for metric computation.
        _run_git("clone", url, str(dest))
    except IngestionError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestionError(f"Clone failed: {exc}") from exc
    return {"name": dest.name, "path": str(dest), "head": _head(dest)}


def _archive_root(names: list[str]) -> str:
    """Find the archive prefix (possibly '') whose directory directly holds .git."""
    top = {n.split("/", 1)[0] for n in names}
    if ".git" in top:
        return ""
    if len(top) == 1:
        root = f"{next(iter(top))}/"
        if any(n == f"{root}.git" or n.startswith(f"{root}.git/") for n in names):
            return root
    raise IngestionError("Zip archive does not contain a repository (.git not found).")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract an archive, refusing members that escape the destination."""
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not target.is_relative_to(dest):
            raise IngestionError(f"Unsafe path in zip archive: {member.filename}")
        zf.extract(member, dest)


def extract_zip(zip_path: Path, repos_dir: Path, filename: str) -> dict:
    """Extract an uploaded repository zip (which must contain .git) into repos_dir."""
    dest = _unique_dir(repos_dir, _safe_name(Path(filename).stem))
    temp_dest = dest.parent / f".{dest.name}.extracting"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            root = _archive_root(zf.namelist())
            _safe_extract(zf, temp_dest)
        src = temp_dest if root == "" else temp_dest / root
        shutil.move(str(src), str(dest))
    except (zipfile.BadZipFile, IngestionError) as exc:
        shutil.rmtree(temp_dest, ignore_errors=True)
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestionError(f"Zip extraction failed: {exc}") from exc
    finally:
        shutil.rmtree(temp_dest, ignore_errors=True)
    if not (dest / ".git").exists():
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestionError("Zip archive does not contain a repository (.git not found).")
    return {"name": dest.name, "path": str(dest), "head": _head(dest)}


def list_repos(repos_dir: Path) -> list[dict]:
    """List all ingested repositories with their current HEAD."""
    repos = []
    if not repos_dir.exists():
        return repos
    for path in sorted(repos_dir.iterdir()):
        if path.is_dir() and (path / ".git").exists():
            repos.append({"name": path.name, "path": str(path), "head": _head(path)})
    return repos
