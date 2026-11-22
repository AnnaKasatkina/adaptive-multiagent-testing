import time
from fastapi.testclient import TestClient
from cp1.event_models import event_preset
from cp1.models import preset
from cp1.service import create_app


def wait(client, ident, status="completed"):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = client.get(f"/api/runs/{ident}").json()
        assert value["status"] != "failed", value
        if value["status"] == status:
            return value
        time.sleep(0.05)
    raise AssertionError(value)


def test_event_api_save_replay_fork_and_time_query(tmp_path):
    c = event_preset("bridge")
    c.horizon_ms = 4000
    with TestClient(create_app(tmp_path)) as client:
        assert (
            client.get("/api/presets").json()[0]["configuration"]["schema_version"] == 2
        )
        made = client.post(
            "/api/runs",
            json=dict(
                configuration=c.model_dump(), seed=1, initial_action="step_update"
            ),
        ).json()
        paused = wait(client, made["id"], "paused")
        assert paused["summary"]["activations"] == 1 and paused["tick"] > 1
        state = client.get(f"/api/runs/{made['id']}/state?time_ms=500").json()
        assert state["time_ms"] <= 500
        branch = client.post(
            f"/api/runs/{made['id']}/fork",
            json=dict(after_event_id=paused["tick"], seed=99),
        ).json()
        wait(client, branch["id"], "paused")
        assert (
            client.get(
                f"/api/runs/{branch['id']}/state?event_id={paused['tick']}"
            ).json()["state_hash"]
            == client.get(
                f"/api/runs/{made['id']}/state?event_id={paused['tick']}"
            ).json()["state_hash"]
        )
        client.post(
            f"/api/runs/{made['id']}/commands", json=dict(action="speed", value=120)
        )
        client.post(f"/api/runs/{made['id']}/commands", json=dict(action="start"))
        complete = wait(client, made["id"])
        replay = client.post(f"/api/runs/{made['id']}/replay", json={}).json()
        wait(client, replay["id"])
        assert (
            client.get(
                f"/api/runs/{replay['id']}/state?event_id={complete['tick']}"
            ).json()["state_hash"]
            == client.get(
                f"/api/runs/{made['id']}/state?event_id={complete['tick']}"
            ).json()["state_hash"]
        )
        blob = client.get(f"/api/runs/{made['id']}/export").content
        imported = client.post(
            "/api/runs/import", files={"file": ("events.zip", blob, "application/zip")}
        )
        assert imported.status_code == 200, imported.text
        assert client.get(f"/api/runs/{imported.json()['id']}/verify").json()["valid"]
        client.post(f"/api/runs/{made['id']}/save", json={"name": "Событийный опыт"})
        saved = client.post(
            "/api/configurations",
            json={"name": "Мой режим", "configuration": c.model_dump()},
        )
        assert saved.status_code == 200
        assert client.get("/api/configurations").json()[0]["name"] == "Мой режим"
        old = preset("ring")
        old.horizon = 1
        legacy = client.post(
            "/api/runs",
            json={
                "configuration": old.model_dump(),
                "initial_action": "start",
                "speed": 120,
            },
        ).json()
        wait(client, legacy["id"])
        mixed = client.post(
            "/api/comparisons",
            json={"name": "Несовместимо", "run_ids": [made["id"], legacy["id"]]},
        )
        assert mixed.status_code == 422
    with TestClient(create_app(tmp_path)) as client:
        assert len(client.get("/api/runs").json()) == 1
        assert client.get(f"/api/runs/{made['id']}/verify").json()["valid"]
