"""Builds a small git fixture exercising every metric edge case:
normal edits, binary files, pure rename, rename+edit, mode-only change,
deletion, a side-branch commit merged in (merge commit excluded), and
two authors with controlled committer dates.
"""
import os
import subprocess
from pathlib import Path

ALICE = ("Alice A", "alice@x.com")
BOB = ("Bob B", "bob@x.com")


def build_fixture(repo: Path) -> None:
    def git(*args, author, date):
        env = dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
        subprocess.run(
            ["git", "-C", str(repo), "-c", f"user.name={author[0]}",
             "-c", f"user.email={author[1]}", *args],
            check=True, capture_output=True, env=env, text=True,
        )

    repo.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", author=ALICE, date="2026-01-01T09:00:00")

    # c1 (alice): create a.txt (3 lines) and dir1/b.txt (5 lines)
    (repo / "a.txt").write_text("l1\nl2\nl3\n")
    (repo / "dir1").mkdir()
    (repo / "dir1" / "b.txt").write_text("m1\nm2\nm3\nm4\nm5\n")
    git("add", "-A", author=ALICE, date="2026-01-01T10:00:00")
    git("commit", "-qm", "c1", author=ALICE, date="2026-01-01T10:00:00")

    # c2 (bob): a.txt +2/-1
    (repo / "a.txt").write_text("l1\nl2\nn1\nn2\n")
    git("add", "-A", author=BOB, date="2026-01-02T10:00:00")
    git("commit", "-qm", "c2", author=BOB, date="2026-01-02T10:00:00")

    # c3 (alice): binary file (excluded from metrics) + b.txt +1
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02\xff\xfe")
    (repo / "dir1" / "b.txt").write_text("m1\nm2\nm3\nm4\nm5\nm6\n")
    git("add", "-A", author=ALICE, date="2026-01-03T10:00:00")
    git("commit", "-qm", "c3", author=ALICE, date="2026-01-03T10:00:00")

    # c4 (bob): pure rename a.txt -> c.txt (no metric change)
    git("mv", "a.txt", "c.txt", author=BOB, date="2026-01-04T10:00:00")
    git("commit", "-qm", "c4", author=BOB, date="2026-01-04T10:00:00")

    # c5 (alice): rename + edit c.txt -> d.txt (+2 lines, 66% similar)
    git("mv", "c.txt", "d.txt", author=ALICE, date="2026-01-05T10:00:00")
    (repo / "d.txt").write_text("l1\nl2\nn1\nn2\nn3\nn4\n")
    git("add", "-A", author=ALICE, date="2026-01-05T10:00:00")
    git("commit", "-qm", "c5", author=ALICE, date="2026-01-05T10:00:00")

    # s1 (bob): side branch adds side.txt (+4)
    git("checkout", "-qb", "side", author=BOB, date="2026-01-06T10:00:00")
    (repo / "side.txt").write_text("s1\ns2\ns3\ns4\n")
    git("add", "-A", author=BOB, date="2026-01-06T10:00:00")
    git("commit", "-qm", "s1", author=BOB, date="2026-01-06T10:00:00")
    git("checkout", "-q", "main", author=BOB, date="2026-01-06T10:00:00")

    # merge (alice): merge side into main — merge commit excluded by --no-merges
    git("merge", "--no-ff", "-m", "merge side", "side",
        author=ALICE, date="2026-01-07T10:00:00")

    # c7 (bob): mode-only change on d.txt (0 added / 0 removed)
    (repo / "d.txt").chmod(0o755)
    git("add", "d.txt", author=BOB, date="2026-01-08T10:00:00")
    git("commit", "-qm", "c7", author=BOB, date="2026-01-08T10:00:00")

    # c8 (alice): delete dir1/b.txt (6 lines removed)
    git("rm", "-q", "dir1/b.txt", author=ALICE, date="2026-01-09T10:00:00")
    git("commit", "-qm", "c8", author=ALICE, date="2026-01-09T10:00:00")
