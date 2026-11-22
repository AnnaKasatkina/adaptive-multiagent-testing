import asyncio
import copy
import pytest
from cp1.event_models import event_preset, EventConfiguration
from cp1.event_engine import execute_events, expand_trace
from cp1.recording import pack, unpack
from cp1.validator import validate_trace


def config(**changes):
    data = event_preset("ring", 2).model_dump()
    data["nodes"][0]["initial"] = 0
    data["nodes"][1]["initial"] = 2
    data["horizon_ms"] = 3000
    data.update(changes)
    return EventConfiguration.model_validate(data)


@pytest.mark.parametrize("delay,first", [(799, 2 / 7), (800, 0), (801, 0)])
def test_half_open_window(delay, first):
    async def run():
        c = config()
        c.defaults["delay_ms"] = delay
        r = await execute_events(c)
        states = list(expand_trace(r["trace"]))
        row = next(
            s
            for s in states
            if s["events"]
            and s["events"][0]["kind"] == "update"
            and s["events"][0]["agent"] == "a01"
        )
        assert row["state"]["agents"]["a01"]["z"] == pytest.approx(first)
        assert validate_trace(r)["valid"]

    asyncio.run(run())


def test_replay_branch_sparse_storage_and_corruption():
    async def run():
        c = config(horizon_ms=12000)
        c.defaults.update(interval_jitter_ms=100, delay_ms=800, delay_jitter_ms=400)
        original = await execute_events(c, 8)
        again = await execute_events(c, 8, replay=original["trace"])
        assert [r["state_hash"] for r in original["trace"]] == [
            r["state_hash"] for r in again["trace"]
        ]
        # Delivery while collecting: the branch must retain the partial batch.
        cut = next(
            r["tick"]
            for r in expand_trace(original["trace"])
            if any(a["batch"] for a in r["state"]["agents"].values())
        )
        branch = await execute_events(c, 999, replay=original["trace"], fork_tick=cut)
        assert [r["state_hash"] for r in branch["trace"][: cut + 1]] == [
            r["state_hash"] for r in original["trace"][: cut + 1]
        ]
        assert branch["trace"][-1]["state_hash"] != original["trace"][-1]["state_hash"]
        assert validate_trace(branch)["valid"]
        restored = unpack(pack(branch))
        assert validate_trace(restored)["valid"]
        broken = copy.deepcopy(branch)
        broken["trace"][1]["patch"]["set"]["time_ms"] += 1
        assert not validate_trace(broken)["valid"]

    asyncio.run(run())


def test_mass_and_analytic_dense_graph():
    async def run():
        c = event_preset("complete", 20)
        c.horizon_ms = 1000
        local = await execute_events(c)
        final = list(expand_trace(local["trace"]))[-1]["state"]["agents"]
        assert final["a01"]["z"] - 0.5 == pytest.approx((0 - 0.5) * (-13 / 7))
        c.algorithm = "raps"
        c.horizon_ms = 5000
        robust = await execute_events(c)
        assert validate_trace(robust)["valid"]
        assert max(abs(r["metrics"]["mass_x_residual"]) for r in robust["trace"]) < 1e-8
        assert robust["summary"]["applied"] > 0

    asyncio.run(run())


def test_fifo_after_episode_and_existing_promises():
    async def run():
        c = config(horizon_ms=6000)
        data = c.model_dump()
        data["episodes"] = [
            dict(
                id="slow",
                kind="link_slowdown",
                targets=["a01>a02"],
                start_ms=0,
                duration_ms=500,
                factor=4,
            )
        ]
        data["defaults"]["delay_ms"] = 1000
        r = await execute_events(EventConfiguration.model_validate(data))
        messages = [
            e["message"]
            for row in r["trace"]
            for e in row["events"]
            if e["kind"] == "send" and e["message"]["sender"] == "a01"
        ]
        assert messages[0]["due_ms"] == 4200
        assert messages[1]["transport_ms"] == 1000
        assert messages[1]["due_ms"] == 4200
        assert messages[1]["fifo_wait_ms"] > 0
        assert validate_trace(r)["valid"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"defaults": {"interval_ms": 800}},
        {"defaults": {"interval_ms": 1000, "interval_jitter_ms": 200}},
        {"defaults": {"delay_ms": 100, "delay_jitter_ms": 100}},
        {"defaults": {"first_update_ms": 799}},
        {"defaults": {"interval_ms": True}},
        {
            "episodes": [
                dict(
                    id="a",
                    kind="agent_slowdown",
                    targets=["a01"],
                    start_ms=0,
                    duration_ms=1000,
                    factor=16,
                )
            ]
        },
        {
            "episodes": [
                dict(
                    id="a",
                    kind="agent_slowdown",
                    targets=["a01"],
                    start_ms=0,
                    duration_ms=2000,
                    factor=2,
                ),
                dict(
                    id="b",
                    kind="agent_slowdown",
                    targets=["a01"],
                    start_ms=1000,
                    duration_ms=1000,
                    factor=2,
                ),
            ]
        },
    ],
)
def test_reject_invalid_bounds_without_clipping(patch):
    with pytest.raises(ValueError):
        config(**patch)


def test_sparse_checkpoint_reconstruction():
    async def run():
        c = event_preset("bridge")
        c.horizon_ms = 30000
        r = await execute_events(c)
        assert len(r["trace"]) > 250
        assert "state" not in r["trace"][1] and "state" in r["trace"][250]
        from cp1.store import RunStore

        store = RunStore(None)
        store.create(
            "x", dict(id="x", configuration=c.model_dump(), manifest=r["manifest"])
        )
        for row in r["trace"]:
            store.append("x", row)
        full = list(expand_trace(r["trace"]))
        for idx in (1, 249, 250, len(full) - 1):
            assert store.state("x", idx)["state_hash"] == full[idx]["state_hash"]
            assert (
                store.state("x", idx)["state"]["agents"] == full[idx]["state"]["agents"]
            )
        observations = store.series("x")
        expected = [
            row
            for row in r["trace"]
            if row["tick"] == 0
            or any(e["kind"] in ("update", "finish") for e in row["events"])
        ]
        assert [row["tick"] for row in observations] == [
            row["tick"] for row in expected
        ]
        assert observations[-1]["z"] == full[-1]["z"]

    asyncio.run(run())
