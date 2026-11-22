import asyncio

from fastapi.testclient import TestClient

from cp1.engine import execute
from cp1.library import SessionStore
from cp1.models import preset
from cp1.recording import pack
from cp1.service import create_app
from cp1.store import RunStore


def records():
    async def prepare():
        config = preset("ring", 3)
        config.horizon = 5
        results = []
        for algorithm in ("raps", "homework"):
            config.algorithm = algorithm
            results.append(pack(await execute(config, 4)))
        return results

    return asyncio.run(prepare())


def import_record(client, blob):
    response = client.post(
        "/api/runs/import", files={"file": ("run.zip", blob, "application/zip")}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_only_explicit_save_survives_restart(tmp_path):
    first, second = records()
    with TestClient(create_app(tmp_path)) as client:
        saved = import_record(client, first)
        temporary = import_record(client, second)
        assert RunStore(tmp_path / "laboratory.sqlite").list() == []
        assert not client.get(f"/api/runs/{saved}").json()["saved"]
        result = client.post(f"/api/runs/{saved}/save", json={"name": "Кольцо"})
        assert result.status_code == 200 and result.json()["saved"]
        assert (
            client.post(f"/api/runs/{saved}/save", json={"name": ""}).status_code == 422
        )
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/api/runs/{saved}").json()["name"] == "Кольцо"
        assert client.get(f"/api/runs/{saved}/verify").json()["valid"]
        assert client.get(f"/api/runs/{temporary}").status_code == 404
        assert len(client.get("/api/runs").json()) == 1


def test_comparison_retains_sources_without_adding_experiments(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        ids = [import_record(client, blob) for blob in records()]
        payload = {"name": "Локальный и RAPS", "run_ids": ids, "metric": "error_mean"}
        comparison = client.post("/api/comparisons", json=payload).json()
        assert comparison["run_ids"] == ids
        assert len(client.get("/api/comparisons").json()) == 1
        assert all(not r["saved"] for r in client.get("/api/runs").json())
        assert (
            client.post("/api/comparisons", json=payload).json()["id"]
            == comparison["id"]
        )
        for invalid in ([ids[0], ids[0]], [ids[0]], [ids[0], None]):
            assert (
                client.post(
                    "/api/comparisons", json={**payload, "run_ids": invalid}
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/api/comparisons", json={**payload, "run_ids": [ids[0], "missing"]}
            ).status_code
            == 404
        )
    with TestClient(create_app(tmp_path)) as client:
        assert len(client.get("/api/comparisons").json()) == 1
        for ident in ids:
            assert client.get(f"/api/runs/{ident}/verify").json()["valid"]
        client.post(f"/api/runs/{ids[0]}/save", json={"name": "Оставить"})
        client.post(f"/api/comparisons/{comparison['id']}/remove", json={})
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/comparisons").json() == []
        assert [r["id"] for r in client.get("/api/runs").json()] == [ids[0]]
        client.post(f"/api/runs/{ids[0]}/unsave", json={})
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/runs").json() == []


def test_legacy_automatic_history_is_backed_up_before_cleanup(tmp_path):
    path = tmp_path / "laboratory.sqlite"
    legacy = RunStore(path)
    legacy.create(
        "old", {"id": "old", "status": "completed", "name": "Автоматическая запись"}
    )
    legacy.append("old", {"tick": 0, "state_hash": "original"})
    current = SessionStore(path)
    assert current.list() == [] and current.library.list() == []
    backups = list((tmp_path / "archive").glob("automatic-history-*.sqlite"))
    assert len(backups) == 1
    backup = RunStore(backups[0])
    assert backup.metadata("old")["name"] == "Автоматическая запись"
    assert backup.state("old", 0)["state_hash"] == "original"
    SessionStore(path)
    assert len(list((tmp_path / "archive").glob("*.sqlite"))) == 1


def test_saved_working_run_keeps_later_ticks_and_metadata(tmp_path):
    store = SessionStore(tmp_path / "library.sqlite")
    store.create("run", {"id": "run", "name": "Кольцо", "status": "paused", "tick": 0})
    store.append("run", {"tick": 0})
    store.save_run("run", "Опыт")
    store.append("run", {"tick": 1})
    store.update("run", {"status": "completed"})
    reopened = SessionStore(tmp_path / "library.sqlite")
    assert reopened.state("run", 1)["tick"] == 1
    assert reopened.metadata("run")["status"] == "completed"
