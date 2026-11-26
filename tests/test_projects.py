"""Projects: folders of calls, and a project downloaded as a zip (decision 0059)."""

import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from parola.app.projects import ProjectError, Projects, sort_key
from parola.app.server import create_app
from parola.schema import Metadata, Segment, Transcript, Word


def call(folder, name, title, started_at, text="buongiorno"):
    t = Transcript(
        metadata=Metadata(audio_file=f"{name}.*.wav", duration=5.0, backend="mlx-whisper",
                          model="turbo", created_at=datetime(2026, 10, 16, tzinfo=UTC),
                          parola_version="0", title=title),
        segments=[Segment(start=0, end=2, text=text, lang="it", channel="system",
                          words=[Word(text=w, start=0.1, end=0.5) for w in text.split()])],
    )  # fmt: skip
    t.save(folder / f"{name}.json")
    (folder / f"{name}.system.wav").write_bytes(b"RIFF" + b"\0" * 1000)
    (folder / f"{name}.capture.json").write_text(json.dumps({"started_at": started_at}))


@pytest.fixture
def folder(tmp_path):
    call(tmp_path, "a", "Standup", "2026-11-24T08:30:00Z", "primo giorno")
    call(tmp_path, "b", "Standup", "2026-11-23T08:30:00Z", "giorno prima")
    call(tmp_path, "c", "Cliente: offerta/2", "2026-11-25T14:00:00Z", "offerta")
    return tmp_path


@pytest.fixture
def client(folder):
    return TestClient(create_app(folder))


def test_names_are_clean_unique_and_alphabetical(tmp_path):
    p = Projects(tmp_path)
    assert p.list() == [] and p.membership() == {}  # no file yet
    zeta = p.create("  Zeta   progetto ")
    assert zeta["name"] == "Zeta progetto"
    p.create("Èlite")
    p.create("alfa")
    assert [x["name"] for x in p.list()] == ["alfa", "Èlite", "Zeta progetto"]
    for bad in ("", "   ", "ALFA", "elite"):  # empty, or the same name with other case or accents
        with pytest.raises(ProjectError):
            p.create(bad)
    assert p.rename(zeta["id"], "Zeta")["name"] == "Zeta"
    assert p.rename(zeta["id"], "zeta")["name"] == "zeta"  # its own name in another case
    assert len(p.create("x" * 200)["name"]) == 60
    assert sort_key("Perché") == "perche"


def test_a_call_is_in_one_project_and_deleting_a_project_keeps_the_calls(tmp_path):
    p = Projects(tmp_path)
    one, two = p.create("Uno"), p.create("Due")
    p.assign("a", one["id"])
    p.assign("a", two["id"])  # moved, not copied
    p.assign("b", two["id"])
    assert p.membership() == {"a": two["id"], "b": two["id"]}
    assert p.calls_of(two["id"]) == ["a", "b"] and p.calls_of(one["id"]) == []
    p.assign("b", None)
    with pytest.raises(ProjectError):
        p.assign("b", "p-missing")
    p.delete(two["id"])
    assert p.membership() == {} and [x["name"] for x in p.list()] == ["Uno"]
    with pytest.raises(ProjectError):
        p.delete(two["id"])


def test_an_unreadable_file_is_never_overwritten(tmp_path):
    (tmp_path / "projects.json").write_text("{not json")
    p = Projects(tmp_path)
    assert p.list() == [] and p.membership() == {}  # the calls still list
    with pytest.raises(ProjectError):
        p.create("Nuovo")
    assert (tmp_path / "projects.json").read_text() == "{not json"


def test_projects_through_the_api(client, folder):
    pid = client.post("/api/projects", json={"name": "Cliente"}).json()["id"]
    assert client.post("/api/projects", json={"name": "cliente"}).status_code == 400
    assert client.post("/api/recordings/c/project", json={"project": pid}).json()["project"] == pid
    assert client.post("/api/recordings/zzz/project", json={"project": pid}).status_code == 404
    assert client.post("/api/recordings/a/project", json={"project": "nope"}).status_code == 404
    rows = {r["name"]: r["project"] for r in client.get("/api/recordings").json()}
    assert rows == {"a": None, "b": None, "c": pid}
    assert client.post(f"/api/projects/{pid}/rename", json={"name": "Clienti"}).json() == {
        "id": pid, "name": "Clienti"}  # fmt: skip
    assert client.delete(f"/api/projects/{pid}").json() == []
    assert {r["project"] for r in client.get("/api/recordings").json()} == {None}
    assert (folder / "c.json").exists()  # the call is still there
    assert client.delete(f"/api/projects/{pid}").status_code == 404


def test_a_call_keeps_its_project_through_the_trash(client, folder):
    pid = client.post("/api/projects", json={"name": "Standup"}).json()["id"]
    client.post("/api/recordings/a/project", json={"project": pid})
    trashed = client.delete("/api/recordings/a").json()["trashed"]
    assert json.loads((folder / "projects.json").read_text())["calls"] == {}
    client.post(f"/api/trash/{trashed}/restore")
    assert not (folder / ".project").exists()  # the mark stays out of the calls folder
    rows = {r["name"]: r["project"] for r in client.get("/api/recordings").json()}
    assert rows["a"] == pid


def test_a_project_downloads_as_a_zip_one_file_per_call(client, folder):
    pid = client.post("/api/projects", json={"name": "Team: settimana"}).json()["id"]
    for name in ("a", "b", "c"):
        client.post(f"/api/recordings/{name}/project", json={"project": pid})
    r = client.get(f"/export/project/{pid}.zip?format=txt")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "filename*=UTF-8''Team%20settimana.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    # Oldest first, local time, unsafe characters dropped, same title and minute kept apart.
    assert [n[17:] for n in names] == ["Standup.txt", "Standup.txt", "Cliente offerta2.txt"]
    assert names[0].startswith("2026-11-23 ") and names[1].startswith("2026-11-24 ")
    assert "giorno prima" in z.read(names[0]).decode()
    md = zipfile.ZipFile(io.BytesIO(client.get(f"/export/project/{pid}.zip?format=md").content))
    assert md.read(md.namelist()[2]).decode().startswith("# Cliente: offerta/2")
    docx = client.get(f"/export/project/{pid}.zip").content  # Word by default
    assert all(n.endswith(".docx") for n in zipfile.ZipFile(io.BytesIO(docx)).namelist())
    assert client.get(f"/export/project/{pid}.zip?format=pdf").status_code == 404
    assert client.get("/export/project/nope.zip").status_code == 404


def test_two_calls_with_the_same_title_and_minute_get_distinct_files(client, folder):
    call(folder, "d", "Standup", "2026-11-24T08:30:30Z", "di nuovo")
    pid = client.post("/api/projects", json={"name": "S"}).json()["id"]
    for name in ("a", "d"):
        client.post(f"/api/recordings/{name}/project", json={"project": pid})
    names = zipfile.ZipFile(io.BytesIO(
        client.get(f"/export/project/{pid}.zip?format=txt").content)).namelist()  # fmt: skip
    assert len(set(names)) == 2 and names[1].endswith("Standup (2).txt")


def test_an_empty_project_has_nothing_to_download(client):
    pid = client.post("/api/projects", json={"name": "Vuoto"}).json()["id"]
    assert client.get(f"/export/project/{pid}.zip").status_code == 400
