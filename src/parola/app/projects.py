"""Projects: folders of calls in the sidebar (decision 0059).

Kept in ``<calls folder>/projects.json``, apart from the transcripts: organising calls never
rewrites a transcript. A call is in one project or in none, keyed by its name (the file name,
which never changes; the title does). Deleting a project never deletes a call: its calls go
back to the list without a project. A call moved to the trash keeps its project, so restoring
it puts it back in its folder.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import unicodedata
from pathlib import Path

FILE = "projects.json"
MAX_NAME = 60


class ProjectError(ValueError):
    pass


def sort_key(name: str) -> str:
    """Alphabetical as a person reads it: case and accents ignored ("Èlite" next to "elite")."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def clean_name(name: str) -> str:
    name = " ".join(name.split())[:MAX_NAME].strip()
    if not name:
        raise ProjectError("a project needs a name")
    return name


class Projects:
    def __init__(self, folder: Path) -> None:
        self.path = folder / FILE
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- file

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"projects": [], "calls": {}}
        except (OSError, ValueError) as e:
            # Never overwrite a file that cannot be read: the folders would be lost silently.
            raise ProjectError(f"{FILE} is not readable: {e}") from e
        return {"projects": list(data.get("projects", [])), "calls": dict(data.get("calls", {}))}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{FILE}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)  # a crash mid-write leaves the old file whole

    # ---------------------------------------------------------------- reading

    def list(self) -> list[dict]:
        """The projects, alphabetical; an unreadable file reads as none (the calls still list)."""
        try:
            projects = self._read()["projects"]
        except ProjectError:
            return []
        return sorted(({"id": p["id"], "name": p["name"]} for p in projects),
                      key=lambda p: sort_key(p["name"]))  # fmt: skip

    def membership(self) -> dict[str, str]:
        """Call name → project id, for the projects that exist."""
        try:
            data = self._read()
        except ProjectError:
            return {}
        ids = {p["id"] for p in data["projects"]}
        return {call: pid for call, pid in data["calls"].items() if pid in ids}

    def name_of(self, pid: str) -> str:
        for p in self.list():
            if p["id"] == pid:
                return p["name"]
        raise ProjectError(f"no project {pid!r}")

    def calls_of(self, pid: str) -> list[str]:
        self.name_of(pid)
        return sorted(call for call, p in self.membership().items() if p == pid)

    # ---------------------------------------------------------------- changing

    def _check_free(self, data: dict, name: str, but: str | None = None) -> None:
        if any(sort_key(p["name"]) == sort_key(name) and p["id"] != but for p in data["projects"]):
            raise ProjectError(f"a project named {name!r} already exists")

    def create(self, name: str) -> dict:
        name = clean_name(name)
        with self._lock:
            data = self._read()
            self._check_free(data, name)
            project = {"id": f"p{secrets.token_hex(4)}", "name": name}
            data["projects"].append(project)
            self._write(data)
        return project

    def rename(self, pid: str, name: str) -> dict:
        name = clean_name(name)
        with self._lock:
            data = self._read()
            project = next((p for p in data["projects"] if p["id"] == pid), None)
            if project is None:
                raise ProjectError(f"no project {pid!r}")
            self._check_free(data, name, but=pid)
            project["name"] = name
            self._write(data)
        return {"id": pid, "name": name}

    def delete(self, pid: str) -> None:
        """Remove the folder; its calls stay, without a project."""
        with self._lock:
            data = self._read()
            if not any(p["id"] == pid for p in data["projects"]):
                raise ProjectError(f"no project {pid!r}")
            data["projects"] = [p for p in data["projects"] if p["id"] != pid]
            data["calls"] = {c: p for c, p in data["calls"].items() if p != pid}
            self._write(data)

    def assign(self, call: str, pid: str | None) -> None:
        """Put ``call`` in project ``pid``, or in none with ``None``."""
        with self._lock:
            data = self._read()
            if pid is None:
                data["calls"].pop(call, None)
            elif any(p["id"] == pid for p in data["projects"]):
                data["calls"][call] = pid
            else:
                raise ProjectError(f"no project {pid!r}")
            self._write(data)
