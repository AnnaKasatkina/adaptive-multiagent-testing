from __future__ import annotations

import copy
import time

from ..adaptive.evaluation import TapeEngine
from ..event_models import EventConfiguration
from ..recording import digest
from .observation import BehaviorDescriptor, OutcomeAssessment


async def evaluate(configuration, seed, kind=None, parent=None, cut=None):
    started = time.perf_counter()
    c = EventConfiguration.model_validate(configuration)
    monitors = [OutcomeAssessment([n.initial for n in c.nodes], c.horizon_ms, threshold) for threshold in (.005, .01, .02)]
    descriptor = BehaviorDescriptor(configuration, kind) if kind else None
    engine = TapeEngine(c, seed)
    tape, layers = [], []
    status, error, prefix = 'completed', None, 0
    feature_seconds = outcome_seconds = 0.
    try:
        row = await engine.start()
        if parent and row['state_hash'] != parent[0]['state_hash']:
            raise ValueError('Исходное состояние префикса расходится')
        while True:
            tape.append({k: row[k] for k in ('tick','time_ms','decisions','state_hash')})
            layer = not engine.heap or engine.heap[0][0] != engine.time_ms
            t = time.perf_counter()
            if descriptor:
                descriptor.accept(row['events'], row['time_ms'], {k:a['z'] for k,a in engine.state['agents'].items()})
                if layer:
                    descriptor.layer(row['time_ms'], row['metrics'])
            feature_seconds += time.perf_counter()-t
            if layer:
                t = time.perf_counter()
                z = {k: a['z'] for k,a in engine.state['agents'].items()}
                for monitor in monitors:
                    monitor.layer(row['time_ms'], row['metrics'], z)
                outcome_seconds += time.perf_counter()-t
                layers.append([engine.time_ms, engine.tick])
            if engine.finished:
                break
            original = parent[engine.tick+1] if parent and engine.tick+1 <= cut else None
            row = await engine.step(original['decisions'] if original else None)
            if original:
                prefix += 1
                if row['state_hash'] != original['state_hash']:
                    raise ValueError(f'Префикс расходится: событие {engine.tick}')
            engine.trace[:] = [row]
    except ArithmeticError as exc:
        status, error = 'numerical_failure', str(exc)
    except Exception as exc:
        status, error = 'technical_failure', f'{type(exc).__name__}: {exc}'
    finally:
        await engine.runtime.close()
    t = time.perf_counter()
    description = descriptor.result() if descriptor and status == 'completed' else None
    feature_seconds += time.perf_counter()-t
    outcomes = {str(m.threshold):m.result() for m in monitors} if status=='completed' else {}
    return dict(status=status, error=error, score=row['metrics']['E'] if status=='completed' else None,
                metrics=copy.deepcopy(row['metrics']), descriptor=description,
                outcome=outcomes.get('0.01'), threshold_outcomes=outcomes, tape=tape, layers=layers,
                processed_events=engine.tick, prefix_events=prefix,
                descriptor_seconds=feature_seconds, outcome_seconds=outcome_seconds,
                execution_seconds=time.perf_counter()-started,
                execution_hash=digest([(r['time_ms'],r['decisions']) for r in tape]),
                final_state_hash=tape[-1]['state_hash'] if tape else None)
