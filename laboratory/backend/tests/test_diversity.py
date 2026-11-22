import asyncio
import copy
import json
import random

import pytest

from cp1.diversity.observation import BehaviorDescriptor, OutcomeAssessment, category, distance, NUMERIC
from cp1.diversity.archive import ArchivePolicy
from cp1.diversity.evaluation import evaluate
from cp1.diversity.session import session, rows_at, export_trial
from cp1.diversity.spaces import context, random_values, mutate_values, materialize, graph_candidate
from cp1.adaptive.evaluation import evaluate as old_evaluate
from cp1.adaptive.spaces import regular


def test_categories_and_distances():
    assert [category(x,NUMERIC) for x in (0,.0001,.001,.01,10,1000)]==[0,1,2,3,6,6]
    assert category(None,NUMERIC)==-1
    with pytest.raises(ValueError):category(float('inf'),NUMERIC)
    a=dict(version='behavior-1',kind='numeric',numeric=[1,2])
    assert distance(a,a)==0
    assert distance(a,{**a,'numeric':[2,2]})==pytest.approx(1/12)
    with pytest.raises(ValueError):distance(a,{**a,'numeric':[1]})


def test_event_pairs_receiver_and_all_predecessors():
    c=context();o=BehaviorDescriptor(c,'events')
    o.accept([dict(kind='cycle_start',agent='a01'),dict(kind='delivery',agent='a02'),dict(kind='send',message={}),dict(kind='delivery',agent='a01'),dict(kind='update',agent='a01')],1000)
    assert set(o.result()['events'])=={('a01','cycle_start','delivery'),('a01','cycle_start','update'),('a01','delivery','update')}
    a=BehaviorDescriptor(c,'messages').result()
    assert a['messages']==[-1]*7


def assessment(points,initial=(0,10),threshold=.01):
    o=OutcomeAssessment(initial,60000,threshold)
    for t,e,q,z in points:o.layer(t,dict(E=e,Q=q),z)
    return o.result()


def test_outcome_bias_growth_oscillation_relapse_and_edges():
    z={'a':5.2,'b':5.2}
    r=assessment([(0,.5,1,{'a':0,'b':10}),(20000,.02,0,z),(40000,.02,0,z),(50000,.02,0,z),(60000,.02,0,z)])
    assert r['signature']=='biased_consensus'
    assert assessment([(0,.5,1,{'a':0,'b':10}),(20000,.1,.2,z),(40000,.2,.4,z),(60000,.4,.8,z)])['growth']
    points=[(0,.5,1,{'a':0,'b':10})]+[(t,.02,.04,{'a':5+(-1)**i*.2,'b':5}) for i,t in enumerate((40000,44000,48000,52000,56000,60000))]
    assert assessment(points)['oscillation']
    r=assessment([(0,0,0,{'a':5,'b':5}),(5000,0,0,{'a':5,'b':5}),(6000,.02,0,z),(60000,0,0,{'a':5,'b':5})])
    assert r['relapse'] and r['outcome']=='transient' and r['precise']
    assert not assessment([(0,.5,1,{'a':0,'b':10}),(60000,.009,.01,z)])['adverse']
    assert assessment([(0,.5,1,{'a':0,'b':10}),(60000,.01,.01,z)])['adverse']
    with pytest.raises(ValueError):OutcomeAssessment([5,5])


def test_time_holding_boundary_and_small_oscillations():
    accurate={'a':5,'b':5};shift={'a':5.2,'b':5.2}
    for at,expected in ((4999,False),(5000,True)):
        r=assessment([(0,0,0,accurate),(at,.02,0,shift),(60000,.02,0,shift)])
        assert r['relapse']==expected
    points=[(t,.001,.002,{'a':5+(-1)**i*.009,'b':5}) for i,t in enumerate(range(0,60001,1000))]
    assert not assessment(points)['oscillation']
    # Growth is a separate flag and does not require the terminal adverse threshold.
    r=assessment([(0,.6,1.2,{'a':-1,'b':11}),(20000,.001,.002,accurate),(40000,.002,.004,accurate),(60000,.004,.008,accurate)])
    assert r['growth'] and r['outside'] and r['outcome']=='transient'


def test_descriptor_rename_covariance():
    c=context();a=BehaviorDescriptor(c,'senders');b=BehaviorDescriptor(c,'senders')
    events=[dict(kind='delivery',agent='a01',message=dict(sender='a02')),dict(kind='update',agent='a01')]
    a.accept(events,1000)
    b.accept([dict(kind='delivery',agent='a02',message=dict(sender='a01')),dict(kind='update',agent='a02')],1000)
    mapping={'a01':'a02','a02':'a01'}
    mapped=[]
    for receiver,first,last in a.result()['events']:
        def rename(token):
            kind,sender=token.split(':');return kind+':'+mapping.get(sender,sender)
        mapped.append((mapping[receiver],rename(first),rename(last)))
    assert sorted(mapped)==b.result()['events']


def test_all_preceding_pairs_saturate_after_two_complete_blocks():
    descriptions=[]
    for word in (('delivery','update','cycle_start')*2, ('cycle_start','update','delivery')*2):
        o=BehaviorDescriptor(context(),'events')
        for kind in word:o.accept([dict(kind=kind,agent='a01')],0)
        descriptions.append(o.result())
    assert len(descriptions[0]['events'])==9
    assert distance(*descriptions)==0


def test_archive_healthy_admission_and_score_independence():
    rows=[dict(status='completed',score=x,descriptor=dict(version='behavior-1',kind='events',events=[('a',str(i),'update')]),outcome=dict(adverse=False)) for i,x in enumerate((1,1000,2))]
    def run(rs):
        ids=[];policy=ArchivePolicy('events',2)
        for i in range(len(rs)):ids=policy.admit(ids,rs[:i+1],i+1,random.Random(1))
        return ids,policy.parent(ids,rs,random.Random(2))
    assert run(copy.deepcopy(rows))==run([{**r,'score':-r['score']} for r in rows])
    assert run(rows)[0]
    duplicate={**copy.deepcopy(rows[0]),'score':1e20}
    rs=[rows[0],duplicate];p=ArchivePolicy('events')
    assert p.admit([1],rs,2,random.Random(0))==[1]
    assert rs[-1]['novelty']==0
    assert ArchivePolicy('events_bad').admit([],rows[:1],1,random.Random(0))==[]


@pytest.mark.parametrize('algorithm',['homework','raps'])
def test_evaluator_matches_original_and_branches(algorithm):
    async def check():
        c=context(algorithm=algorithm);c['horizon_ms']=6000
        a=await evaluate(c,7,'events');b=await old_evaluate(c,7)
        assert a['status']=='completed',a['error']
        assert a['tape']==b['tape'] and a['score']==b['score']
        cut=next(k for t,k in a['layers'] if t>=2500)
        branch=await evaluate(c,100,'events',a['tape'],cut)
        assert branch['tape'][:cut+1]==a['tape'][:cut+1]
        c=regular(c)
        x,y=await evaluate(c,0,'events'),await evaluate(c,99,'events')
        assert x['final_state_hash']==y['final_state_hash']
    asyncio.run(check())


def test_profiles_and_graph_protection():
    c=regular(context(n=8));rng=random.Random(10)
    for variant in ('individual','episodes'):
        v=random_values(c,rng,variant)
        for _ in range(50):
            v=mutate_values(c,v,rng,variant);materialize(c,v,variant)
    c,_=graph_candidate(c,rng,False,9)
    v=random_values(c,rng);c=materialize(c,v)
    for _ in range(20):
        c,_=graph_candidate(c,rng,False,9,True)
        assert len(c['edges'])==18
        assert any(f"{e['source']}>{e['target']}"==v['edge'] for e in c['edges'])


def test_exact_resume_export_and_score_substitution(tmp_path,monkeypatch):
    import importlib
    mod=importlib.import_module('cp1.diversity.session')
    async def check():
        c=context();c['horizon_ms']=3000
        job=dict(configuration=c,method='events',seed=3,budget=14,directory=str(tmp_path/'resumed'))
        await session(job,stop_after=11)
        with (tmp_path/'resumed/trials.jsonl').open('a') as f:f.write('{"uncommitted":true}\n')
        await session(job)
        await session({**job,'directory':str(tmp_path/'whole')})
        def logical(path):
            return [(r['configuration'],r['seed'],r['parent'],r['score'],r['execution_hash'],r['novelty'],r['archive_admitted']) for r in rows_at(path)]
        assert logical(tmp_path/'resumed')==logical(tmp_path/'whole')
        original=mod.evaluate
        async def substituted(*a,**kw):
            r=await original(*a,**kw);r['score']=-r['score'];return r
        monkeypatch.setattr(mod,'evaluate',substituted)
        await session({**job,'directory':str(tmp_path/'substituted')})
        assert [(a[0],a[1],a[2],a[4],a[5],a[6]) for a in logical(tmp_path/'whole')]==[(a[0],a[1],a[2],a[4],a[5],a[6]) for a in logical(tmp_path/'substituted')]
        monkeypatch.setattr(mod,'evaluate',original)
        run=await export_trial(tmp_path/'whole',14)
        assert run['manifest']['search']['version']=='diversity-1'
        with pytest.raises(ValueError):await session({**job,'budget':15})
    asyncio.run(check())


def test_generation_failure_consumes_budget(tmp_path,monkeypatch):
    import importlib
    mod=importlib.import_module('cp1.diversity.session')
    def unavailable(*a,**kw):raise ValueError('1000 недопустимых предложений графа')
    monkeypatch.setattr(mod,'graph_candidate',unavailable)
    job=dict(configuration=regular(context(n=8)),method='independent',seed=6000,budget=12,
             family='topology',topology_mode='graph',directed=True,edge_count=10,directory=str(tmp_path/'generation'))
    result=asyncio.run(session(job))
    assert result['trials']==12 and result['complete'] and result['generation_failures']==12
    assert result['technical_failures']==0 and result['types_found']==0
    assert all(r['generation_attempts']==1000 for r in rows_at(job['directory']))
