import time
import asyncio
import pytest
from cp1.engine import execute
from fastapi.testclient import TestClient
from cp1.models import preset
from cp1.service import create_app


def wait(client, ident, predicate, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        meta = client.get(f"/api/runs/{ident}").json()
        assert meta["status"] != "failed", meta
        if predicate(meta):
            return meta
        time.sleep(0.05)
    raise AssertionError(f"Таймаут: {meta}")


def test_live_lifecycle_import_replay_and_reopen(tmp_path):
    config = preset("bridge").model_dump()
    config["horizon"] = 20
    with TestClient(create_app(tmp_path)) as client:
        checked = client.post("/api/configurations/validate", json=config).json()
        assert checked["configuration_hash"]
        run = client.post(
            "/api/runs", json={"configuration": config, "seed": 19}
        ).json()
        ident = run["id"]
        wait(client, ident, lambda r: r["status"] == "paused")
        client.post(f"/api/runs/{ident}/commands", json={"action": "step"})
        wait(client, ident, lambda r: r["tick"] == 1)
        time.sleep(0.15)
        assert client.get(f"/api/runs/{ident}").json()["tick"] == 1
        client.post(
            f"/api/runs/{ident}/commands", json={"action": "speed", "value": 120}
        )
        client.post(f"/api/runs/{ident}/commands", json={"action": "start"})
        wait(client, ident, lambda r: r["tick"] >= 4)
        client.post(f"/api/runs/{ident}/commands", json={"action": "pause"})
        paused = wait(client, ident, lambda r: r["status"] == "paused")
        tick = paused["tick"]
        state = client.get(f"/api/runs/{ident}/state?tick={tick}").json()
        branch = client.post(
            f"/api/runs/{ident}/fork", json={"after_tick": tick, "seed": 42}
        ).json()
        wait(client, branch["id"], lambda r: r["status"] == "paused")
        assert (
            client.get(f"/api/runs/{branch['id']}/state?tick={tick}").json()[
                "state_hash"
            ]
            == state["state_hash"]
        )
        assert client.get(f"/api/runs/{ident}").json()["tick"] == tick
        client.post(f"/api/runs/{ident}/commands", json={"action": "start"})
        wait(client, ident, lambda r: r["status"] == "completed")
        replay = client.post(f"/api/runs/{ident}/replay", json={}).json()
        wait(client, replay["id"], lambda r: r["status"] == "completed")
        assert (
            client.get(f"/api/runs/{replay['id']}/state?tick=20").json()["state_hash"]
            == client.get(f"/api/runs/{ident}/state?tick=20").json()["state_hash"]
        )
        blob = client.get(f"/api/runs/{ident}/export").content
        imported = client.post(
            "/api/runs/import", files={"file": ("record.zip", blob, "application/zip")}
        )
        assert imported.status_code == 200, imported.text
        assert client.get(f"/api/runs/{imported.json()['id']}/verify").json()["valid"]
        assert (
            client.post(
                f"/api/runs/{ident}/save", json={"name": "Сохранённый опыт"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/runs/{branch['id']}/save", json={"name": "Продолжение"}
            ).status_code
            == 200
        )
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/api/runs/{ident}/state?tick=20").json()["tick"] == 20
        assert client.get(f"/api/runs/{branch['id']}").json()["status"] == "stopped"
        assert client.get(f"/api/runs/{imported.json()['id']}").status_code == 404


def test_configuration_errors_and_search_space_roundtrip(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        c = preset("ring").model_dump()
        retired = {**c, "algorithm": "homework-original"}
        assert (
            client.post("/api/configurations/validate", json=retired).status_code == 422
        )
        assert (
            client.post("/api/runs", json={"configuration": retired}).status_code == 422
        )
        v = dict(
            name="p_all",
            kind="float",
            minimum=0,
            maximum=1,
            default=0.5,
            targets=[
                dict(scope="node", id=n["id"], parameter="activation_probability")
                for n in c["nodes"]
            ],
        )
        spec = dict(schema_version=1, base_configuration=c, variables=[v])
        assert client.post("/api/search-spaces", json={"spec": spec}).status_code == 200
        assert len(client.get("/api/search-spaces").json()) == 1
        result = client.post(
            "/api/search-spaces/materialize",
            json={"configuration": c, "spec": spec, "values": {"p_all": 0.2}},
        ).json()["configuration"]
        assert all(
            n["overrides"]["activation_probability"] == 0.2 for n in result["nodes"]
        )
        c["defaults"]["delay_min"] = 10
        error = client.post("/api/configurations/validate", json=c)
        assert error.status_code == 422 and "Задержки" in error.text


@pytest.mark.parametrize("action", ["start", "step"])
def test_initial_action_does_not_require_a_second_command(tmp_path, action):
    config = preset("ring", 3)
    config.horizon = 12
    config.algorithm = "homework"
    expected = asyncio.run(execute(config, 9))
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/runs",
            json={
                "configuration": config.model_dump(),
                "seed": 9,
                "initial_action": action,
                "speed": 120,
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["initial_action"] == action
        if action == "start":
            wait(client, run["id"], lambda r: r["status"] == "completed")
            tick = config.horizon
        else:
            wait(
                client, run["id"], lambda r: r["status"] == "paused" and r["tick"] == 1
            )
            time.sleep(0.05)
            assert client.get(f"/api/runs/{run['id']}").json()["tick"] == 1
            tick = 1
        actual = client.get(f"/api/runs/{run['id']}/state?tick={tick}").json()
        assert actual["state_hash"] == expected["trace"][tick]["state_hash"]
        assert (
            client.post(
                "/api/runs",
                json={
                    "configuration": config.model_dump(),
                    "initial_action": "unknown",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/runs", json={"configuration": config.model_dump(), "speed": 0}
            ).status_code
            == 422
        )
