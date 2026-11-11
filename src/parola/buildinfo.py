"""Which code is actually running (decision 0045: visible versioning).

A process older than the code on disk and anonymous caches cost three wasted diagnoses in
two days: every surface now says which commit it is. The engine reads the repository's HEAD
with plain file reads (no git subprocess: the check rides the status poll), remembers the
commit it booted from, and declares both — the UI shows a sober "restart" hint when the
repo has moved on.
"""

from __future__ import annotations

from pathlib import Path

SHORT = 7  # a short hash, like git's default abbreviation


def repo_head(repo: Path) -> str:
    """The repository's current commit (short hash), or "" when it cannot be read.

    Pure file reads: ``.git/HEAD``, the ref file it points to, or ``packed-refs`` when the
    ref is packed. A detached HEAD holds the hash directly.
    """
    try:
        head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref:"):
        return head[:SHORT] if head else ""
    ref = head.removeprefix("ref:").strip()
    try:
        return (repo / ".git" / ref).read_text(encoding="utf-8").strip()[:SHORT]
    except OSError:
        pass
    try:
        packed = (repo / ".git" / "packed-refs").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in packed.splitlines():
        if line.endswith(" " + ref):
            return line.split(" ", 1)[0][:SHORT]
    return ""


class BuildInfo:
    """The commit the engine booted from, against the repository's current one."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.booted = repo_head(repo)

    def status(self) -> dict:
        """For the status poll: booted commit, current commit, and whether a restart helps."""
        now = repo_head(self.repo)
        return {"engine": self.booted, "repo": now,
                "stale": bool(now) and bool(self.booted) and now != self.booted}  # fmt: skip
