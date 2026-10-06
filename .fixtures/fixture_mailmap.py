"""Builds a fixture where one human committed under two identities and the
repository later ships a .mailmap that unifies them.

Timeline (committer dates 2026-02-01..03):
  c1 Old Name <same@old.com>: +f.txt (1 line)
  c2 New Name <same@new.com>: f.txt +1
  c3 New Name <same@new.com>: +.mailmap (maps the old identity onto the new)

With the .mailmap present all three commits resolve to New Name
<same@new.com>; without it the two identities stay separate.
"""
import os
import subprocess
from pathlib import Path

OLD = ("Old Name", "same@old.com")
NEW = ("New Name", "same@new.com")


def build_fixture(repo: Path) -> None:
    def git(*args, author, date):
        env = dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
        subprocess.run(
            ["git", "-C", str(repo), "-c", f"user.name={author[0]}",
             "-c", f"user.email={author[1]}", *args],
            check=True, capture_output=True, env=env, text=True,
        )

    repo.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", author=OLD, date="2026-02-01T10:00:00")

    # c1 under the old identity
    (repo / "f.txt").write_text("one\n")
    git("add", "-A", author=OLD, date="2026-02-01T10:00:00")
    git("commit", "-qm", "c1", author=OLD, date="2026-02-01T10:00:00")

    # c2 under the new identity
    (repo / "f.txt").write_text("one\ntwo\n")
    git("add", "-A", author=NEW, date="2026-02-02T10:00:00")
    git("commit", "-qm", "c2", author=NEW, date="2026-02-02T10:00:00")

    # c3 ships the .mailmap that folds the old identity into the new one
    (repo / ".mailmap").write_text(
        "New Name <same@new.com> Old Name <same@old.com>\n")
    git("add", "-A", author=NEW, date="2026-02-03T10:00:00")
    git("commit", "-qm", "c3 add mailmap", author=NEW,
        date="2026-02-03T10:00:00")
