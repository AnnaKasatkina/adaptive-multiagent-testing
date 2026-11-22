from __future__ import annotations

import asyncio
import copy
import gzip
import hashlib
import json
import random
import time
from pathlib import Path

from . import VERSION
from .archive import update_archive
from .evaluation import evaluate
from .spaces import regular, random_values, logical_values, settings, mutate_values, random_graph, mutate_graph, effective_values
from ..event_engine import execute_events, expand_trace
from ..event_models import EventConfiguration
from ..recording import digest, fingerprint, pack
from ..search import write_json
from ..validator import validate_trace


def derived(seed, *parts):
    return int.from_bytes(hashlib.sha256('|'.join(map(str, (VERSION, seed, *parts))).encode()).digest()[:8], 'big') % (2**63)


def read_tape(directory, trial):
    data = (Path(directory) / "decisions" / f"{trial:03}.json.gz").read_bytes()
    return json.loads(gzip.decompress(data))


def best_id(rows):
    valid = [r for r in rows if r["status"] == "completed"]
    return min(valid, key=lambda r: (-r["score"], r["trial"]))["trial"] if valid else None


async def session(job, stop_after=None):
    directory = Path(job["directory"])
    directory.mkdir(parents=True, exist_ok=True)
    budget, method, seed = job["budget"], job["method"], job["seed"]
    family, variant = job.get("family", "schedule"), job.get("variant", "individual")
    identity = {k: v for k, v in job.items() if k != "directory"}
    identity.update(version=VERSION, source=fingerprint()["source"])
    identity_hash = digest(identity)
    state_path, log_path = directory / "state.json", directory / "trials.jsonl"
    saved = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    if saved and saved["identity_hash"] != identity_hash:
        raise ValueError("Версия исходников, протокол или бюджет сессии изменились; требуется новая папка")
    rows = [json.loads(s) for s in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    rows = rows[:saved["count"]] if saved else []
    if saved and len(rows) != saved["count"]:
        raise ValueError("Журнал сессии оборван")
    if saved and rows and saved.get('last_row_hash') != digest(rows[-1]):
        raise ValueError("Последняя запись журнала повреждена")
    log_path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding="utf-8")
    streams = {name: random.Random(derived(seed, name)) for name in ("proposal", "parent", "cut", "archive")}
    def tuples(x): return tuple(tuples(v) for v in x) if isinstance(x, list) else x
    if saved:
        for name, rng in streams.items(): rng.setstate(tuples(saved["rng"][name]))
    archive = saved["archive"] if saved else []
    elapsed = saved.get("elapsed_seconds", 0) if saved else 0
    started = time.perf_counter()
    base = job["configuration"]
    logical = logical_values(base, budget, variant) if method == "logical" else None
    execution_seen = {r["execution_hash"] for r in rows if r.get("execution_hash")}
    configuration_seen = {r["configuration_hash"] for r in rows}
    write_json(directory / "spec.json", identity)
    for index in range(len(rows), budget):
        if stop_after is not None and index >= stop_after:
            break
        trial, environment_seed = index + 1, derived(seed, "environment", index)
        rng = streams["proposal"]
        parent = cut = None
        parent_row = None
        c, values, operation = copy.deepcopy(base), None, "independent"
        best = best_id(rows)
        if family == "schedule" and index >= 10 and method != "independent" and archive and rng.random() < .75:
            if method == "branch_best":
                parent_id = best
            elif method == "branch_random":
                parent_id = streams["parent"].choice(archive)
            else:
                parent_id = best if streams["parent"].random() < .5 else streams["parent"].choice(archive)
            parent_row = rows[parent_id - 1]
            parent = read_tape(directory, parent_id)
            moment = streams["cut"].randint(0, base["horizon_ms"] * 9 // 10)
            cut = next(tick for t, tick in reversed(parent_row["layers"]) if t <= moment)
            operation = "branch"
        elif family == "parameters":
            operation = "parameters_restart"
            if method == "logical":
                values = logical[index]
            elif method in ("feedback", "guided") and index >= 10 and best and rng.random() < .75:
                leader = rows[best - 1]
                values = mutate_values(base, leader["values"], rng, variant, leader["descriptors"] if method == "guided" else None)
            else:
                values = random_values(base, rng, variant)
            c = settings(base, values, variant)
        elif family == "topology":
            operation = "topology_restart"
            directed, count = job.get("directed", False), job.get("edge_count")
            if method != "independent" and index >= 10 and best and rng.random() < .75:
                c = copy.deepcopy(rows[best - 1]["configuration"])
                if job.get("topology_mode") == "joint" and rng.random() < .5:
                    values = mutate_values(c, effective_values(c), rng)
                    c = settings(c, values)
                    operation = "parameters_restart"
                else:
                    c = mutate_graph(c, rng, directed)
                    # A moved selected edge may disappear: derive values from the materialized configuration.
            else:
                c = random_graph(regular(base), rng, directed, count)
                if job.get("topology_mode") == "joint":
                    values = random_values(c, rng)
                    c = settings(c, values)
        result = await evaluate(c, environment_seed, parent, cut)
        tape = result.pop("tape")
        decision_file = directory / "decisions" / f"{trial:03}.json.gz"
        decision_file.parent.mkdir(exist_ok=True)
        decision_file.write_bytes(gzip.compress(json.dumps(tape, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
        provenance = dict(run_id=f"trial-{parent_row['trial']:03}", after_tick=cut, time_ms=parent[cut]["time_ms"], kind="search_branch") if parent_row else None
        if family == "topology" and job.get('topology_mode') == 'joint':
            # Reconstruct values if a previous graph mutation removed their target.
            values = effective_values(c)
        row = dict(trial=trial, seed=environment_seed, configuration=c, configuration_hash=digest(c),
                   parent=provenance, operation=operation, values=values,
                   duplicate_execution=result.get("execution_hash") in execution_seen,
                   duplicate_configuration=digest(c) in configuration_seen, **result)
        rows.append(row)
        current = best_id(rows)
        row["best_trial"] = current
        row["best_score"] = rows[current-1]["score"] if current else None
        row["improved"] = current == trial
        archive_method = method if family == "schedule" else "branch_best"
        archive = update_archive(archive, rows, trial, archive_method, streams["archive"])
        execution_seen.add(result.get("execution_hash")); configuration_seen.add(digest(c))
        with log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n'); f.flush()
        write_json(state_path, dict(identity_hash=identity_hash, count=trial, archive=archive, last_row_hash=digest(row),
                                   rng={n: r.getstate() for n, r in streams.items()}, elapsed_seconds=elapsed + time.perf_counter()-started))
    best = best_id(rows)
    summary = dict(version=VERSION, family=family, method=method, seed=seed, trials=len(rows), complete=len(rows)==budget,
                   best_trial=best, best_score=rows[best-1]["score"] if best else None, archive=archive,
                   failures=sum(r["status"]!="completed" for r in rows), duplicates=sum(r["duplicate_execution"] for r in rows),
                   processed_events=sum(r["processed_events"] for r in rows), prefix_events=sum(r["prefix_events"] for r in rows),
                   evaluation_seconds=sum(r["execution_seconds"] for r in rows), descriptor_seconds=sum(r["descriptor_seconds"] for r in rows),
                   session_seconds=elapsed+time.perf_counter()-started)
    write_json(directory / "summary.json", summary)
    return summary


def run_session(job):
    return asyncio.run(session(job))


async def export_trial(directory, trial=None, output=None, runtime=None):
    directory = Path(directory)
    rows = [json.loads(s) for s in (directory / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    trial = trial or best_id(rows)
    row = rows[trial-1]
    if row["status"] != "completed":
        raise ValueError("Незавершённое испытание нельзя экспортировать как успешное")
    tape = read_tape(directory, trial)
    run = await execute_events(EventConfiguration.model_validate(row["configuration"]), row["seed"], runtime=runtime, replay=tape)
    assert run["summary"]["E"] == row["score"]
    assert validate_trace(run)["valid"]
    run["manifest"].update(parent=row["parent"], search=dict(version=VERSION, trial=trial, operation=row["operation"], values=row["values"]))
    run["configuration"]["name"] = f"Поиск · {directory.name} · испытание {trial}"
    run["manifest"]["configuration_hash"] = digest(run["configuration"])
    path = Path(output) if output else directory / "records" / f"{trial:03}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pack(run))
    return run
