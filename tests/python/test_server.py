from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wireforge import cli
from wireforge.server.app import create_app
from wireforge.server.storage import StorageError, Workspace, check_match, etag_of

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics")
    shutil.copytree(EXAMPLES / "01_basic_telemetry", root / "basic")
    (root / "not_a_project").mkdir()
    return root


@pytest.fixture
def client(workspace: Path) -> TestClient:
    return TestClient(create_app(workspace))


def _etag(client: TestClient, project: str) -> str:
    response = client.get(f"/api/projects/{project}/state")
    assert response.status_code == 200
    return str(response.json()["etag"])


def test_projects_and_files(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    listed = client.get("/api/projects").json()
    assert [item["id"] for item in listed] == ["avionics", "basic"]
    assert listed[0]["name"] == "avionics_demo"
    project = client.get("/api/projects/avionics").json()
    assert project["ok"]
    assert "NavSolution" in project["sections"]["messages"]
    assert "messages/nav.yaml" in project["files"]
    assert client.get("/api/projects/nope").status_code == 404
    file = client.get("/api/projects/avionics/files/messages/nav.yaml")
    assert file.status_code == 200
    assert file.headers["etag"] == f'"{file.json()["etag"]}"'
    assert "NavSolution" in file.json()["text"]
    assert client.get("/api/projects/avionics/files/../basic/project.yaml").status_code in {
        400,
        404,
    }
    assert client.get("/api/projects/avionics/files/missing.yaml").status_code == 404
    assert client.post("/api/projects/avionics/validate").json()["ok"]


def test_file_writes_need_the_current_etag(client: TestClient) -> None:
    url = "/api/projects/basic/files/project.yaml"
    current = client.get(url).json()
    text = current["text"].replace("max: 32", "max: 30")
    assert client.put(url, json={"text": text}).status_code == 428
    assert client.put(url, json={"text": text}, headers={"If-Match": "stale"}).status_code == 412
    written = client.put(url, json={"text": text}, headers={"If-Match": f'"{current["etag"]}"'})
    assert written.status_code == 200
    assert written.json()["ok"]
    assert written.json()["etag"] == etag_of(text.encode())
    second = client.put(url, json={"text": text}, headers={"If-Match": current["etag"]})
    assert second.status_code == 412, "a second writer with the old ETag loses"
    created = client.put(
        "/api/projects/basic/files/extra.yaml",
        json={"text": "schema_version: 1\n"},
        headers={"If-Match": "*"},
    )
    assert created.status_code == 200
    again = client.put(
        "/api/projects/basic/files/extra.yaml", json={"text": ""}, headers={"If-Match": "*"}
    )
    assert again.status_code == 412
    assert (
        client.put(
            "/api/projects/basic/files/notes.txt", json={"text": ""}, headers={"If-Match": "*"}
        ).status_code
        == 400
    )


def test_entries_and_values(client: TestClient, workspace: Path) -> None:
    entry = client.get("/api/projects/avionics/entries/messages/Command").json()
    assert entry["data"]["value"]["id"] == 0x0301
    etag = _etag(client, "avionics")
    definition = {"fields": [{"name": "a", "type": "u8"}, {"name": "b", "type": "u16"}]}
    put = client.put(
        "/api/projects/avionics/entries/types/Pair",
        json={"definition": definition},
        headers={"If-Match": etag},
    )
    assert put.status_code == 200, put.text
    assert put.json()["data"]["file"].endswith("common.yaml")
    assert put.json()["etag"] != etag
    stale = client.put(
        "/api/projects/avionics/entries/types/Pair",
        json={"definition": definition},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412
    etag = put.json()["etag"]
    value = client.put(
        "/api/projects/avionics/value",
        params={"pointer": "/types/Pair/fields/1/max"},
        json={"value": 9, "dry_run": True},
        headers={"If-Match": etag},
    )
    assert "max: 9" in value.json()["data"]["diff"]
    assert _etag(client, "avionics") == etag, "dry run writes nothing"
    value = client.put(
        "/api/projects/avionics/value",
        params={"pointer": "/types/Pair/fields/1/max"},
        json={"value": 9},
        headers={"If-Match": etag},
    )
    etag = value.json()["etag"]
    got = client.get("/api/projects/avionics/value", params={"pointer": "/types/Pair/fields/1"})
    assert got.json()["data"]["value"] == {"name": "b", "type": "u16", "max": 9}
    strict = client.put(
        "/api/projects/avionics/value",
        params={"pointer": "/types/Pair/fields/1/default"},
        json={"value": 99, "strict": True},
        headers={"If-Match": etag},
    )
    assert strict.status_code == 200
    assert not strict.json()["ok"]
    deleted = client.delete(
        "/api/projects/avionics/value",
        params={"pointer": "/types/Pair/fields/1"},
        headers={"If-Match": etag},
    )
    assert deleted.json()["ok"]
    removed = client.request(
        "DELETE",
        "/api/projects/avionics/entries/types/Pair",
        headers={"If-Match": deleted.json()["etag"]},
    )
    assert removed.status_code == 200
    missing = client.get("/api/projects/avionics/entries/types/Pair")
    assert missing.status_code == 404
    bad = client.put(
        "/api/projects/avionics/entries/nope/X",
        json={"definition": {}},
        headers={"If-Match": removed.json()["etag"]},
    )
    assert bad.status_code == 400
    assert "Pair" not in (workspace / "avionics" / "types" / "common.yaml").read_text()


def test_concurrent_edits_conflict(client: TestClient) -> None:
    etag = _etag(client, "avionics")
    results: list[int] = []

    def edit(name: str) -> None:
        response = client.put(
            f"/api/projects/avionics/entries/types/{name}",
            json={"definition": {"fields": [{"name": "v", "type": "u8"}]}},
            headers={"If-Match": etag},
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=edit, args=(f"T{index}",)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 412, 412, 412], "exactly one writer wins"


def test_external_edits_are_seen(client: TestClient, workspace: Path) -> None:
    etag = _etag(client, "basic")
    assert (
        client.get("/api/projects/basic/wait", params={"etag": etag, "timeout": 0}).status_code
        == 304
    )
    path = workspace / "basic" / "project.yaml"

    def hand_edit() -> None:
        time.sleep(0.3)
        # bytes, so that Windows does not turn the line end into CR LF
        path.write_bytes(path.read_bytes() + b"# edited by hand\n")

    thread = threading.Thread(target=hand_edit)
    thread.start()
    changed = client.get("/api/projects/basic/wait", params={"etag": etag, "timeout": 5})
    thread.join()
    assert changed.status_code == 200
    assert changed.json()["etag"] != etag
    text = client.get("/api/projects/basic/files/project.yaml").json()["text"]
    assert text.endswith("# edited by hand\n"), "reads come from disk"


def test_create_project(client: TestClient) -> None:
    created = client.post("/api/projects", json={"id": "fresh", "name": "fresh_link"})
    assert created.status_code == 201
    assert client.post("/api/projects", json={"id": "fresh", "name": "x"}).status_code == 409
    assert client.post("/api/projects", json={"id": "../x", "name": "x"}).status_code == 422
    assert client.get("/api/projects/fresh").json()["ok"]


def test_token(workspace: Path) -> None:
    token = secrets.token_hex(8)
    client = TestClient(create_app(workspace, token=token))
    assert client.get("/api/health").status_code == 200, "health needs no token"
    assert client.get("/api/projects").status_code == 401
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/projects", headers=headers).status_code == 200
    assert client.get("/api/projects", headers={"Authorization": "Bearer x"}).status_code == 401


def test_openapi_and_cli(workspace: Path, tmp_path: Path) -> None:
    schema = TestClient(create_app(workspace)).get("/openapi.json").json()
    assert "/api/projects/{project}/entries/{section}/{name}" in schema["paths"]
    output = tmp_path / "openapi.json"
    assert cli.main(["serve", "--workspace", str(workspace), "--openapi", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["info"]["title"] == "WireForge"


def test_storage_helpers(workspace: Path) -> None:
    store = Workspace(workspace)
    with pytest.raises(StorageError) as raised:
        check_match("a", None)
    assert raised.value.status == 428
    check_match(None, "*")
    check_match("abc", '"abc"')
    state = store.state("avionics")
    assert store.wait("avionics", state.etag, 0.0) is None
    assert store.wait("avionics", "other", 0.0) == state
    assert store.lock("avionics") is store.lock("avionics")
