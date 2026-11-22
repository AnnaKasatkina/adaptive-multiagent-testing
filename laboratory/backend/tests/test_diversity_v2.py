import asyncio
import copy
import importlib
import random

import pytest

from cp1.diversity_v2.archive import ArchivePolicy
from cp1.diversity_v2.observation import BehaviorDescriptor, distance
from cp1.diversity_v2.evaluation import evaluate
from cp1.diversity_v2.session import session, rows_at, export_trial
from cp1.diversity.spaces import context
from cp1.diversity.evaluation import evaluate as previous_evaluate
from cp1.adaptive.spaces import regular


def local_update(o, ident='a01', value=1, time=1000, used=None):
    o.accept([dict(kind='update', agent=ident, used=used or [])], time, {ident:value})


def test_local_boundaries_empty_and_frequency():
    o=BehaviorDescriptor(context(), 'local')
    local_update(o, value=0, time=4999)
    local_update(o, value=0, time=5000)
    for _ in range(8):local_update(o, value=0, time=60000)
    h=o.result()['histogram']
    assert [r[0][0] for r in h]==[0,1,4]
    assert [r[1] for r in h]==[1,1,4]
    assert all(r[0][1:4]==[0,-1,-1] for r in h)
    assert all(r[0][-1]=='unchanged' for r in h)


def test_local_direction_rename_and_distance():
    c=context();a=BehaviorDescriptor(c,'local');b=BehaviorDescriptor(c,'local')
    local_update(a,value=4);local_update(a,value=6,time=2000)
    # a01 and a02 have identical initial values and degrees in this graph.
    local_update(b,'a02',4);local_update(b,'a02',6,time=2000)
    assert a.result()==b.result()
    assert {v[0][-1] for v in a.result()['histogram']}=={'toward','cross'}
    assert distance(a.result(),b.result())==0
    blank=BehaviorDescriptor(c,'local').result()
    assert distance(a.result(),blank)==.25
    c['nodes']=[{**n,'initial':5} for n in c['nodes']]
    with pytest.raises(ValueError):BehaviorDescriptor(c,'local')


def test_no_messages_differs_from_zero_age():
    a=BehaviorDescriptor(context(),'local');b=BehaviorDescriptor(context(),'local')
    local_update(a,value=0)
    local_update(b,value=0,used=[dict(sender='a02',sent_ms=1000)])
    assert a.result()!=b.result()


def test_archive_uniform_healthy_and_eviction():
    def d(x):return dict(version='behavior-2',kind='numeric',numeric=[x])
    rows=[dict(status='completed',descriptor=d(x),score=1e9 if i==0 else 0) for i,x in enumerate((1,2,3))]
    p=ArchivePolicy('numeric',2);ids=[];rng=random.Random(3)
    for i in range(3):ids=p.admit(ids,rows[:i+1],i+1,rng,{'1':100})
    assert ids==[2,3]  # no protection for the oldest or highest-error record
    counts=[p.parent([1,2],rows,rng) for _ in range(2000)]
    assert .45 < counts.count(1)/len(counts) < .55
    rows.append({**rows[0],'score':1e30})
    assert p.admit(ids,rows,4,rng)==ids and rows[-1]['novelty']==0


@pytest.mark.parametrize('algorithm',['homework','raps'])
def test_physics_compact_branch_and_extension(algorithm):
    async def check():
        c=context(algorithm=algorithm);c['horizon_ms']=6000
        a=await evaluate(c,9,'local');old=await previous_evaluate(c,9)
        assert a['status']=='completed',a['error']
        assert a['tape']==old['tape'] and a['score']==old['score']
        cut=next(k for t,k in a['layers'] if t>=2500)
        b=await evaluate(c,19,'local',a['tape'],cut)
        assert b['tape'][:cut+1]==a['tape'][:cut+1]
        c=regular(c)
        x,y=await evaluate(c,0,'local'),await evaluate(c,100,'local')
        assert x['final_state_hash']==y['final_state_hash']
    asyncio.run(check())


@pytest.mark.parametrize('method',['independent','reservoir','elite','numeric','local'])
def test_resume_and_score_independence(tmp_path,monkeypatch,method):
    mod=importlib.import_module('cp1.diversity_v2.session')
    async def check():
        c=context();c['horizon_ms']=3000
        job=dict(configuration=c,method=method,seed=73,budget=15,directory=str(tmp_path/'split'))
        await session(job,stop_after=11);await session(job)
        await session({**job,'directory':str(tmp_path/'whole')})
        def logic(folder):
            return [(r['configuration'],r['seed'],r['parent_trial'],r['execution_hash'],r['descriptor'],r['archive_admitted']) for r in rows_at(folder)]
        assert logic(tmp_path/'split')==logic(tmp_path/'whole')
        if method!='elite':
            original=mod.evaluate
            async def changed(*args,**kw):
                r=await original(*args,**kw);r['score']=1e10-r['score'];return r
            monkeypatch.setattr(mod,'evaluate',changed)
            await session({**job,'directory':str(tmp_path/'changed')})
            assert logic(tmp_path/'changed')==logic(tmp_path/'whole')
        if method=='local':
            run=await export_trial(tmp_path/'whole',15)
            assert run['manifest']['search']['version']=='diversity-2'
    asyncio.run(check())


def test_generation_failures_not_duplicates(tmp_path,monkeypatch):
    mod=importlib.import_module('cp1.diversity_v2.session')
    def fail(*args):raise ValueError('1000 rejected')
    monkeypatch.setattr(mod,'graph_candidate',fail)
    job=dict(configuration=regular(context()),method='independent',seed=1,budget=3,
             family='topology',topology_mode='joint',directed=True,edge_count=8,directory=str(tmp_path))
    r=asyncio.run(session(job))
    assert r['generation_failures']==3 and r['duplicates']==0 and r['trials']==3
