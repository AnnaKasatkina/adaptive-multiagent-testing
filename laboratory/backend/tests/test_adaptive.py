import asyncio
import copy
import json
import random

import pytest

from cp1.adaptive.archive import distance, update_archive
from cp1.adaptive.evaluation import evaluate
from cp1.adaptive.session import session, export_trial, read_tape
from cp1.adaptive.spaces import context, regular, random_graph, mutate_graph, settings, logical_values
from cp1.event_engine import execute_events
from cp1.event_models import EventConfiguration
from cp1.recording import unpack


@pytest.mark.parametrize("algorithm", ["homework", "raps"])
def test_tape_matches_full_and_branch_preserves_decisions(algorithm):
    async def check():
        c = context(algorithm=algorithm); c["horizon_ms"] = 6000
        a = await evaluate(c, 8)
        assert a["status"] == "completed", a.get("error")
        full = await execute_events(EventConfiguration.model_validate(c), 8)
        assert [r["state_hash"] for r in a["tape"]] == [r["state_hash"] for r in full["trace"]]
        assert a["score"] == full["summary"]["E"]
        cut = next(tick for t, tick in a["layers"] if t > 2000)
        b = await evaluate(c, 999, a["tape"], cut)
        assert a["tape"][:cut+1] == b["tape"][:cut+1]
        assert b["final_state_hash"] != a["final_state_hash"]
        c = regular(c)
        a, b = await evaluate(c, 8), await evaluate(c, 999)
        assert a["tape"] == b["tape"]
    asyncio.run(check())


def test_completed_time_layer_features():
    async def check():
        c = context(); c["horizon_ms"] = 6000
        small = await evaluate(c, 3)
        full = await execute_events(EventConfiguration.model_validate(c), 3)
        last = next(r for r in reversed(full["trace"]) if r["time_ms"] <= 5000)
        assert small["descriptors"]["samples"]["5000"] == {k:last["metrics"][k] for k in ("E", "Q")}
        assert small["descriptors"]["agents"]
    asyncio.run(check())


def test_archive_preserves_best_and_deterministic_ties():
    rows = [dict(trial=i+1, status="completed", score=float(i % 7), descriptors=dict(events=[str(i)], numeric=[i], messages=[i])) for i in range(50)]
    for method in ("elite", "events", "numeric", "messages", "combined"):
        ids = []
        for i in range(1, 51):
            ids = update_archive(ids, rows, i, method, random.Random(0), capacity=4)
            assert len(ids) <= 4
            assert min(range(1,i+1), key=lambda j:(-rows[j-1]["score"],j)) in ids
    assert distance(rows[0]["descriptors"],rows[0]["descriptors"],"combined") == 0


def test_resume_committed_session_and_self_contained_export(tmp_path):
    async def check():
        c = context(); c["horizon_ms"] = 6000
        job = dict(configuration=c, family="schedule", method="numeric", seed=9, budget=13, directory=str(tmp_path / 'resumed'))
        await session(job, stop_after=11)
        with (tmp_path / 'resumed/trials.jsonl').open('a') as f: f.write('{"uncommitted":true}\n')
        await session(job)
        await session({**job,"directory":str(tmp_path / 'whole')})
        def logical_rows(folder):
            return [(r['configuration'],r['parent'],r['seed'],r['score'],r['best_trial'],r['execution_hash']) for r in map(json.loads,(tmp_path/folder/'trials.jsonl').read_text(encoding='utf-8').splitlines())]
        assert logical_rows('resumed') == logical_rows('whole')
        run = await export_trial(tmp_path / 'resumed', trial=13)
        loaded = unpack((tmp_path / 'resumed/records/013.zip').read_bytes())
        assert run['manifest']['parent'] == loaded['manifest']['parent']
        assert loaded['trace'][-1]['state_hash'] == read_tape(tmp_path/'resumed',13)[-1]['state_hash']
        with pytest.raises(ValueError,match='изменились'):
            await session({**job,'budget':14})
    asyncio.run(check())


@pytest.mark.parametrize("directed",[False,True])
def test_topology_and_parameter_operators(directed):
    base = regular(context())
    rng=random.Random(11)
    c=random_graph(base,rng,directed)
    for _ in range(15):
        c=mutate_graph(c,rng,directed)
        assert len(c['edges']) == (8 if directed else 14)
        assert [(n['id'],n['initial']) for n in c['nodes']] == [(n['id'],n['initial']) for n in base['nodes']]
        EventConfiguration.model_validate(c)
    plan=logical_values(base)
    assert len({json.dumps(v,sort_keys=True) for v in plan})==100
    c=settings(base,plan[2])
    assert c['nodes'][2]['overrides']['interval_ms']==8000
    for variant in ('agent_episode','link_episode','joint_episode'):
        v=dict(agent='a03',edge='a03>a04',start=20000,duration=10000,factor=4)
        EventConfiguration.model_validate(settings(base,v,variant))


def test_independent_proposals_do_not_depend_on_scores(tmp_path,monkeypatch):
    import cp1.adaptive.session as module
    original=module.evaluate
    async def check():
        c=context();c['horizon_ms']=2500
        job=dict(configuration=c,family='schedule',method='independent',seed=3,budget=12,directory=str(tmp_path/'a'))
        await module.session(job)
        async def different(*a,**kw):
            r=await original(*a,**kw);r['score']=100-r['score'];return r
        monkeypatch.setattr(module,'evaluate',different)
        await module.session({**job,'directory':str(tmp_path/'b')})
        rows=lambda name:[json.loads(s) for s in (tmp_path/name/'trials.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [(r['seed'],r['parent'],r['execution_hash']) for r in rows('a')]==[(r['seed'],r['parent'],r['execution_hash']) for r in rows('b')]
    asyncio.run(check())
