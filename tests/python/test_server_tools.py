from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wireforge.server.app import create_app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "workspace"
    root.mkdir()
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics")
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics_next")
    shutil.copytree(EXAMPLES / "03_edge_cases", root / "edge")
    (root / "broken").mkdir()
    (root / "broken" / "project.yaml").write_text(
        "schema_version: 1\nproject: p\nmessages:\n  M:\n    fields:\n"
        "      - { name: a, type: u8, default: 300 }\n",
        encoding="utf-8",
    )
    return TestClient(create_app(root))


def test_generate_zip_is_self_contained(client: TestClient) -> None:
    response = client.get("/api/projects/avionics/generate")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "avionics_demo-generated.zip" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    for name in (
        "avionics_demo/README.md",
        "avionics_demo/include/avionics_demo/model.hpp",
        "avionics_demo/include/avionics_demo/interfaces/our_to_nodeA.hpp",
        "avionics_demo/include/avionics_demo/generated_tests/CMakeLists.txt",
        "avionics_demo/include/avionics_demo/generated_tests/selftest.cpp",
        "avionics_demo/runtime/include/wireforge/link.hpp",
        "avionics_demo/model/app/include/vendor_sum.hpp",
    ):
        assert name in names, name
    cmake = archive.read("avionics_demo/include/avionics_demo/generated_tests/CMakeLists.txt")
    assert b'"${CMAKE_CURRENT_SOURCE_DIR}/../../../runtime/include"' in cmake
    assert b'"${CMAKE_CURRENT_SOURCE_DIR}/../../../model"' in cmake
    without = client.get("/api/projects/avionics/generate", params={"tests": False})
    names = set(zipfile.ZipFile(io.BytesIO(without.content)).namelist())
    assert not any("generated_tests" in name for name in names)
    failed = client.get("/api/projects/broken/generate")
    assert failed.status_code == 422
    assert "SR-6" in failed.json()["issues"][0]["message"]


def test_preview_and_analyze(client: TestClient) -> None:
    preview = client.get("/api/projects/edge/preview").json()
    assert preview["ok"]
    assert "edge_cases/model.hpp" in preview["data"]["files"]
    assert not client.get("/api/projects/broken/preview").json()["ok"]
    report = client.get("/api/projects/avionics/analyze", params={"profile": "platformB"}).json()
    assert report["data"]["profiles"][0]["profile"] == "platformB"
    assert report["text"].startswith("# ICD avionics_demo")
    csv = client.get("/api/projects/avionics/analyze", params={"export": "csv"}).json()
    assert csv["text"].startswith("profile,owner")
    assert client.get("/api/projects/avionics/analyze", params={"export": "pdf"}).status_code == 400
    unknown = client.get("/api/projects/avionics/analyze", params={"profile": "nope"})
    assert unknown.status_code == 400


def test_compat(client: TestClient) -> None:
    same = client.post("/api/compat", json={"old": "avionics", "new": "avionics_next"}).json()
    assert same["ok"]
    assert {item["interface"] for item in same["data"]["interfaces"]} == {
        "nodeB_to_our",
        "our_to_nodeA",
        "our_to_nodeC",
    }
    etag = client.get("/api/projects/avionics_next/state").json()["etag"]
    changed = client.put(
        "/api/projects/avionics_next/value",
        params={"pointer": "/messages/Command/fields/0/max"},
        json={"value": 8},
        headers={"If-Match": etag},
    )
    assert changed.json()["ok"]
    report = client.post(
        "/api/compat",
        json={"old": "avionics", "new": "avionics_next", "interfaces": ["our_to_nodeC"]},
    ).json()
    assert not report["ok"], "a range change needs a major version bump"
    assert report["data"]["interfaces"][0]["required_bump"] == "major"


def test_playground(client: TestClient) -> None:
    body = {
        "message": "Command",
        "interface": "our_to_nodeC",
        "profile": "platformA",
        "value": {"target": 2, "opcode": 7},
    }
    encoded = client.post("/api/projects/avionics/encode", json=body).json()
    assert encoded["ok"], "the custom checksum plugin is found in plugins/"
    frame = encoded["data"]["frame"]
    decoded = client.post(
        "/api/projects/avionics/decode",
        json={"hex": frame, "interface": "our_to_nodeC", "profile": "platformA"},
    ).json()
    assert decoded["data"]["value"] == {"target": 2, "opcode": 7, "sum": 9}
    payload = client.post(
        "/api/projects/avionics/decode",
        json={"hex": encoded["data"]["payload"], "message": "Command", "profile": "platformA"},
    ).json()
    assert payload["ok"]
    bad = client.post(
        "/api/projects/avionics/decode",
        json={"hex": frame[:-2] + "00", "interface": "our_to_nodeC", "profile": "platformA"},
    ).json()
    assert not bad["ok"]
    assert bad["data"]["status"] == "ChecksumMismatch"
    assert client.post("/api/projects/avionics/decode", json={"hex": "xyz"}).status_code == 400
    assert client.post("/api/projects/avionics/decode", json={"hex": "00"}).status_code == 400
    out_of_range = client.post(
        "/api/projects/avionics/encode", json={**body, "value": {"target": 9}}
    ).json()
    assert out_of_range["data"]["status"] == "OutOfRange"
    edge = client.post(
        "/api/projects/edge/encode", json={"message": "Everything", "value": {"big": 1}}
    ).json()
    assert edge["ok"]
    assert (
        client.post(
            "/api/projects/avionics/encode", json={"message": "Command", "value": {}}
        ).status_code
        == 400
    ), "a project with profiles needs one"


def _git(directory: Path, *args: str) -> None:
    git = shutil.which("git") or "git"
    subprocess.run(
        [git, "-C", str(directory), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def test_history_and_compat_with_revision(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics")
    client = TestClient(create_app(root))
    assert client.get("/api/projects/avionics/history").json()["data"]["commits"] == []
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    commits = client.get("/api/projects/avionics/history").json()["data"]["commits"]
    assert [commit["subject"] for commit in commits] == ["baseline"]
    etag = client.get("/api/projects/avionics/state").json()["etag"]
    client.put(
        "/api/projects/avionics/value",
        params={"pointer": "/messages/Command/fields/0/max"},
        json={"value": 8},
        headers={"If-Match": etag},
    )
    report = client.post(
        "/api/projects/avionics/compat", json={"revision": commits[0]["commit"]}
    ).json()
    assert not report["ok"]
    assert report["data"]["revision"] == commits[0]["commit"]
    assert "our_to_nodeC" in report["text"]
    assert (
        client.post("/api/projects/avionics/compat", json={"revision": "nope"}).status_code == 400
    )
    bad = client.post("/api/projects/avionics/compat", json={"revision": "--output=x"})
    assert bad.status_code == 422


def test_new_interfaces_get_their_own_file(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    shutil.copytree(EXAMPLES / "02_multi_interface_avionics", root / "avionics")
    client = TestClient(create_app(root))
    definition = {
        "version": "1.0.0",
        "direction": "tx",
        "header": "none",
        "messages": ["Command"],
    }
    etag = client.get("/api/projects/avionics/state").json()["etag"]
    dry = client.put(
        "/api/projects/avionics/entries/interfaces/our_to_nodeD",
        json={"definition": definition, "dry_run": True},
        headers={"If-Match": etag},
    ).json()
    target = root / "avionics" / "interfaces" / "our_to_nodeD.yaml"
    assert dry["data"]["file"] == str(target)
    assert not target.exists(), "a dry run creates nothing"
    written = client.put(
        "/api/projects/avionics/entries/interfaces/our_to_nodeD",
        json={"definition": definition},
        headers={"If-Match": etag},
    ).json()
    assert written["ok"], written["text"]
    assert target.read_text(encoding="utf-8").startswith("schema_version: 1\ninterfaces:\n")
    strict = client.put(
        "/api/projects/avionics/entries/interfaces/our_to_nodeE",
        json={"definition": {**definition, "messages": ["Missing"]}, "strict": True},
        headers={"If-Match": written["etag"]},
    ).json()
    assert not strict["ok"]
    assert not (root / "avionics" / "interfaces" / "our_to_nodeE.yaml").exists()


def test_wireshark_plugin_download(client: TestClient) -> None:
    response = client.get("/api/projects/avionics/wireshark")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-lua")
    assert 'filename="avionics_demo.lua"' in response.headers["content-disposition"]
    assert response.text.startswith("-- Wireshark dissector for avionics_demo 2.1.0.")
    broken = client.get("/api/projects/broken/wireshark")
    assert broken.status_code == 422
    assert not broken.json()["ok"]


def test_docs_download(client: TestClient) -> None:
    response = client.get("/api/projects/avionics/docs")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="avionics-docs.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "index.md" in archive.namelist()
    broken = client.get("/api/projects/broken/docs")
    assert broken.status_code == 422
    assert not broken.json()["ok"]
