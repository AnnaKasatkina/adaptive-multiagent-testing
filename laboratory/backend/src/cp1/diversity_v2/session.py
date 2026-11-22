from __future__ import annotations

import asyncio
import copy
import gzip
import hashlib
import json
import random
import time
from pathlib import Path

from ..adaptive.session import read_tape
from ..adaptive.spaces import regular
from ..event_engine import execute_events
from ..event_models import EventConfiguration
from ..recording import digest, fingerprint, pack
from ..search import write_json
from ..validator import validate_trace
from . import VERSION
from .archive import ArchivePolicy
from .evaluation import evaluate
from ..diversity.spaces import random_values, mutate_values, materialize, graph_candidate


def derived(seed,*parts):
    return int.from_bytes(hashlib.sha256('|'.join(map(str,(VERSION,seed,*parts))).encode()).digest()[:8],'big')%(2**63)


def rows_at(directory):
    path=Path(directory)/'trials.jsonl'
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


async def session(job, stop_after=None):
    directory=Path(job['directory']);directory.mkdir(parents=True,exist_ok=True)
    identity={k:v for k,v in job.items() if k!='directory'}
    identity.update(version=VERSION,source=fingerprint()['source'])
    identity_hash=digest(identity)
    state_path=directory/'state.json'
    saved=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else None
    if saved and saved['identity_hash']!=identity_hash:
        raise ValueError('Исходники или протокол изменились: используйте новую папку результатов')
    rows=rows_at(directory)[:saved['count']] if saved else []
    if saved and (len(rows)!=saved['count'] or rows and digest(rows[-1])!=saved['last_row_hash']):
        raise ValueError('Повреждён подтверждённый журнал')
    log=directory/'trials.jsonl'
    log.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    streams={k:random.Random(derived(job['seed'],k)) for k in ('proposal','parent','cut','archive')}
    def tuples(x):return tuple(tuples(y) for y in x) if isinstance(x,list) else x
    if saved:
        for k,rng in streams.items():rng.setstate(tuples(saved['rng'][k]))
    archive=saved['archive'] if saved else []
    uses=saved.get('parent_uses', {}) if saved else {}
    elapsed=saved['elapsed_seconds'] if saved else 0.
    method=job['method'];kind=method.split('_')[0] if method not in ('independent','elite','reservoir') else None
    policy=ArchivePolicy(method)
    base=job['configuration'];family=job.get('family','schedule');variant=job.get('variant','individual')
    write_json(directory/'spec.json',identity)
    signatures={r['outcome']['signature'] for r in rows if r.get('outcome') and r['outcome']['adverse']}
    hashes={r.get('execution_hash') for r in rows}
    started=time.perf_counter()
    for index in range(len(rows),job['budget']):
        if stop_after is not None and index>=stop_after:break
        trial=index+1;rng=streams['proposal'];seed=job['environment_seed_start']+index if 'environment_seed_start' in job else derived(job['seed'],'environment',index)
        c=copy.deepcopy(base);parent_id=None;cut=None;tape_parent=None;values=None;attempts=1
        proposal_start=time.perf_counter()
        if index>=10 and method!='independent' and archive and rng.random()<.75:
            parent_id=policy.parent(archive,rows,streams['parent'])
            if parent_id: uses[str(parent_id)]=uses.get(str(parent_id),0)+1
        provenance=None;generation_error=None
        operation='independent'
        try:
            parent_row=rows[parent_id-1] if parent_id else None
            if family=='schedule' and parent_id:
                tape_parent=read_tape(directory,parent_id)
                moment=streams['cut'].randint(0,base['horizon_ms']*9//10)
                cut=next(k for t,k in reversed(parent_row['layers']) if t<=moment)
                provenance=dict(run_id=f'trial-{parent_id:03}',after_tick=cut,time_ms=tape_parent[cut]['time_ms'],kind='search_branch')
                operation='branch'
            elif family=='parameters':
                values=mutate_values(base,parent_row['values'],rng,variant) if parent_id else random_values(base,rng,variant)
                c=materialize(base,values,variant);operation='parameters_restart'
            elif family=='topology':
                if parent_id:
                    c=copy.deepcopy(parent_row['configuration']);values=copy.deepcopy(parent_row['values'])
                    if job['topology_mode']=='joint' and rng.random()<.5:
                        values=mutate_values(c,values,rng);c=materialize(c,values)
                        operation='parameters_restart'
                    else:
                        c,attempts=graph_candidate(c,rng,job['directed'],job['edge_count'],True)
                        operation='topology_restart'
                else:
                    c,attempts=graph_candidate(regular(base),rng,job['directed'],job['edge_count'])
                    if job['topology_mode']=='joint':
                        values=random_values(c,rng);c=materialize(c,values)
                    operation='topology_restart'
        except ValueError as exc:
            generation_error=str(exc);attempts=1000
        proposal_seconds=time.perf_counter()-proposal_start
        if generation_error:
            result=dict(status='generation_failure',error=generation_error,score=None,descriptor=None,outcome=None,
                        tape=[],layers=[],processed_events=0,prefix_events=0,execution_seconds=0.,descriptor_seconds=0.,outcome_seconds=0.,execution_hash=None)
        else:
            result=await evaluate(c,seed,kind,tape_parent,cut)
        tape=result.pop('tape')
        file=directory/'decisions'/f'{trial:03}.json.gz';file.parent.mkdir(exist_ok=True)
        io_start=time.perf_counter()
        file.write_bytes(gzip.compress(json.dumps(tape,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
        write_seconds=time.perf_counter()-io_start
        row=dict(trial=trial,seed=seed,configuration=c,configuration_hash=digest(c),parent=provenance,
                 parent_trial=parent_id,parent_adverse=rows[parent_id-1]['outcome']['adverse'] if parent_id else None,
                 operation=operation,values=values,generation_attempts=attempts,proposal_seconds=proposal_seconds,
                 write_seconds=write_seconds,duplicate_execution=result['status']=='completed' and result['execution_hash'] in hashes,
                 novelty=None,**result)
        rows.append(row)
        arch_start=time.perf_counter();archive=policy.admit(archive,rows,trial,streams['archive'],uses)
        row['archive_seconds']=time.perf_counter()-arch_start
        row['archive_admitted']=trial in archive
        outcome=row['outcome']
        row['new_type']=bool(outcome and outcome['adverse'] and outcome['signature'] not in signatures)
        if outcome and outcome['adverse']:signatures.add(outcome['signature'])
        row['types_found']=len(signatures)
        row['cumulative_events']=sum(r['processed_events'] for r in rows)
        row['cumulative_seconds']=sum(r['execution_seconds']+r['proposal_seconds']+r['archive_seconds']+r['write_seconds'] for r in rows)
        if row['status']=='completed': hashes.add(row['execution_hash'])
        with log.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');f.flush()
        write_json(state_path,dict(identity_hash=identity_hash,count=trial,archive=archive,last_row_hash=digest(row),
                                  parent_uses=uses,rng={k:r.getstate() for k,r in streams.items()},elapsed_seconds=elapsed+time.perf_counter()-started))
    summary=dict(version=VERSION,method=method,trials=len(rows),complete=len(rows)==job['budget'],archive=archive,parent_uses=uses,
                 signatures=sorted(signatures),types_found=len(signatures),failures=sum(r['status']!='completed' for r in rows),
                 generation_failures=sum(r['status']=='generation_failure' for r in rows),
                 technical_failures=sum(r['status']=='technical_failure' for r in rows),
                 numerical_failures=sum(r['status']=='numerical_failure' for r in rows),
                 completed_evaluations=sum(r['status']=='completed' for r in rows),
                 processed_events=sum(r['processed_events'] for r in rows),prefix_events=sum(r['prefix_events'] for r in rows),
                 descriptor_seconds=sum(r['descriptor_seconds'] for r in rows),
                 session_seconds=elapsed+time.perf_counter()-started,
                 duplicates=sum(r['duplicate_execution'] for r in rows),
                 curve=[r['types_found'] for r in rows])
    write_json(directory/'summary.json',summary)
    return summary


def run_session(job):return asyncio.run(session(job))


async def export_trial(directory,trial,output=None,runtime=None):
    directory=Path(directory);row=rows_at(directory)[trial-1]
    if row['status']!='completed':raise ValueError('Экспорт требует завершённого исполнения')
    tape=read_tape(directory,trial)
    run=await execute_events(EventConfiguration.model_validate(row['configuration']),row['seed'],runtime,replay=tape)
    if [r['state_hash'] for r in run['trace']] != [r['state_hash'] for r in tape]:
        raise ValueError('Повтор не совпадает с компактной записью')
    audit=validate_trace(run)
    if not audit['valid']:raise ValueError(str(audit))
    run['manifest'].update(parent=row['parent'] or (dict(run_id=f"trial-{row['parent_trial']:03}",kind=row['operation']) if row['parent_trial'] else None),search=dict(version=VERSION,trial=trial,operation=row['operation'],values=row['values'],outcome=row['outcome'],novelty=row['novelty'],parent_trial=row['parent_trial'],archive_reason=row.get('archive_reason')))
    run['configuration']['name']=f"Разнообразие · {trial} · {row['outcome']['signature'] or 'без нарушения'}"
    run['manifest']['configuration_hash']=digest(run['configuration'])
    path=Path(output) if output else directory/'records'/f'{trial:03}.zip';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(pack(run));return run
