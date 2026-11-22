import asyncio
import json

import pytest

from cp1.event_engine import execute_events
from cp1.event_models import EventConfiguration
from cp1.search import BASE, Strategy, configuration_for, evaluate, logical_points, make_space, session


@pytest.mark.parametrize("algorithm", ["homework", "raps"])
@pytest.mark.parametrize("point", [BASE, (1249, 3701, 799, 801)])
def test_compact_evaluation_is_the_same_execution(algorithm, point):
    async def check():
        c = configuration_for(make_space("bridge", algorithm), point)
        c["horizon_ms"] = 6000
        small = await evaluate(c, True)
        full = await execute_events(EventConfiguration.model_validate(c))
        assert small["score"] == full["summary"]["E"]
        assert small["final_state_hash"] == full["trace"][-1]["state_hash"]
        layers = {r["time_ms"]: r for r in full["trace"]}
        for row in small["curve"]:
            assert row["z"] == layers[row["time_ms"]]["z"]
        assert small["metrics"]["error_integral"] == full["summary"]["error_integral"]
    asyncio.run(check())


def test_fixed_plan_and_rng_resume_and_feedback():
    assert len(set(logical_points())) == 100
    histories = {}
    for method in ("random", "feedback"):
        a, b = Strategy(method, 12), Strategy(method, 12)
        points = []
        for i in range(100):
            pa, _ = a.propose()
            pb, _ = b.propose()
            points.append(pa)
            a.observe(pa, pa[0])
            b.observe(pb, -pb[0])
            if i == 32:
                a = Strategy(method, 12, json.loads(json.dumps(a.snapshot())))
            if method == "random" or i < 10:
                assert pa == pb
        assert len(set(points)) == 100
        histories[method] = points
        if method == "feedback":
            assert a.best != b.best
    assert histories["random"][:10] == histories["feedback"][:10]


def test_session_recovers_uncommitted_tail(tmp_path, monkeypatch):
    import cp1.search as search
    space = make_space()
    space["base_configuration"]["horizon_ms"] = 2500
    from cp1.recording import digest
    space["base_hash"] = digest(space["base_configuration"])
    original = search.evaluate
    calls = 0
    async def interrupt(c, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise KeyboardInterrupt()
        return await original(c, *args, **kwargs)
    monkeypatch.setattr(search, "evaluate", interrupt)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(session(space, "feedback", 7, 12, tmp_path / "resume"))
    monkeypatch.setattr(search, "evaluate", original)
    with (tmp_path / "resume/trials.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"uncommitted": true}\n')
    asyncio.run(session(space, "feedback", 7, 12, tmp_path / "resume"))
    asyncio.run(session(space, "feedback", 7, 12, tmp_path / "whole"))
    def read(name):
        return [(r["values"], r["score"], r["best_values"]) for r in map(json.loads, (tmp_path / name / "trials.jsonl").read_text(encoding="utf-8").splitlines())]
    assert read("resume") == read("whole")
