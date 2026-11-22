"""Reproducible parameter search over the existing event engine (checkpoint 2)."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import random
import time
from itertools import product
from pathlib import Path

from .algorithms import model_version
from .event_engine import EventEngine, execute_events, state_hash
from .event_models import EventConfiguration, event_preset, materialize
from .recording import digest, pack
from .validator import validate_trace

VERSION = "score-search-1"
NAMES = ("interval_group_A", "interval_group_B", "delay_internal", "delay_between")
BOUNDS = ((1000, 4000), (1000, 4000), (50, 2000), (50, 2000))
BASE = (1000, 1000, 200, 200)
GRAPHS = ("ring", "bridge", "directed")
METHODS = ("logical", "random", "feedback")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def make_space(graph="bridge", algorithm="homework"):
    c = event_preset(graph, 6, algorithm).model_dump()
    c["horizon_ms"] = 60000
    for i, n in enumerate(c["nodes"]):
        n["initial"] = 0 if i < 3 else 10
    c = EventConfiguration.model_validate(c).model_dump()
    variables = []
    for index, (name, (lo, hi)) in enumerate(zip(NAMES, BOUNDS)):
        if index < 2:
            targets = [dict(scope="node", id=n["id"], parameter="interval_ms") for n in c["nodes"][index * 3:index * 3 + 3]]
        else:
            targets = [dict(scope="edge", id=f"{e['source']}>{e['target']}", parameter="delay_ms") for e in c["edges"] if ((e["source"] <= "a03") == (e["target"] <= "a03")) == (index == 2)]
        variables.append(dict(name=name, kind="int", minimum=lo, maximum=hi, default=BASE[index], targets=targets))
    return dict(schema_version=2, model_version=model_version(EventConfiguration.model_validate(c)), base_hash=digest(c), base_configuration=c, objective=dict(metric="E", direction="maximize", repetitions=[0]), variables=variables)


def logical_points():
    special = [(a, b, di, db) for a, b in ((1000, 1000), (4000, 1000), (1000, 4000)) for di, db in ((200, 200), (799, 799), (801, 801), (799, 801), (801, 799), (200, 800))]
    first = [BASE, (4000, 1000, 200, 200), (1000, 4000, 200, 200), (2000, 1000, 200, 200)]
    points = first + sorted(set(special) - set(first)) + list(product((1000, 2000, 4000), (1000, 2000, 4000), (50, 800, 2000), (50, 800, 2000)))
    assert len(points) == len(set(points)) == 100
    return points


class Strategy:
    """Random streams belong to proposals, never to the simulated network."""

    def __init__(self, method, seed=0, state=None):
        if method not in METHODS:
            raise ValueError("Неизвестный способ поиска")
        self.method, self.seed = method, seed
        self.rng = random.Random(seed)
        self.seen, self.count = set(), 0
        self.best, self.score = None, None
        if state:
            if state["method"] != method or state["seed"] != seed:
                raise ValueError("Настройки продолжения расходятся")
            def tuples(x):
                return tuple(tuples(v) for v in x) if isinstance(x, list) else x
            self.rng.setstate(tuples(state["rng"]))
            self.seen = {tuple(p) for p in state["seen"]}
            self.count, self.best, self.score = state["count"], state["best"], state["score"]

    def snapshot(self):
        return dict(method=self.method, seed=self.seed, rng=self.rng.getstate(), seen=sorted(self.seen), count=self.count, best=self.best, score=self.score)

    def propose(self):
        for _ in range(10000):
            reason = "uniform"
            if self.method == "logical":
                point, reason = logical_points()[self.count], "frozen_logical_plan"
            elif self.count == 0:
                point, reason = BASE, "baseline"
            elif self.method == "feedback" and self.count >= 10 and self.best is not None and self.rng.random() < .75:
                k = self.rng.randrange(4)
                lo, hi = BOUNDS[k]
                radius = self.rng.choice((1, math.ceil(.01 * (hi - lo)), math.ceil(.1 * (hi - lo))))
                lower, upper = max(lo, self.best[k] - radius), min(hi, self.best[k] + radius)
                # Draw uniformly from the interval with the current value removed.
                value = self.rng.randrange(lower, upper)
                if value >= self.best[k]:
                    value += 1
                point = list(self.best)
                point[k] = value
                point, reason = tuple(point), f"local:{NAMES[k]}:radius={radius}"
            else:
                point = tuple(self.rng.randint(lo, hi) for lo, hi in BOUNDS)
            if point not in self.seen:
                self.seen.add(point)
                return point, reason
        raise RuntimeError("Не удалось выбрать новую точку")

    def observe(self, point, score):
        self.count += 1
        improved = score is not None and math.isfinite(score) and (self.score is None or score > self.score)
        if improved:
            self.best, self.score = list(point), score
        return improved


class CompactEngine(EventEngine):
    """Same dispatch and metrics; omit storage deltas during bulk evaluation."""

    def record(self, events, decisions, before):
        self.state.update(time_ms=self.time_ms, event_id=self.tick, control=self.control(), last_due=self.last_due, counters=self.source.counters)
        return dict(time_ms=self.time_ms, metrics=self.metrics(), z={k: a["z"] for k, a in self.state["agents"].items()})


async def evaluate(configuration, keep_curve=False):
    started = time.perf_counter()
    engine = CompactEngine(configuration, seed=0)
    curve = []
    try:
        row = await engine.start()
        while not engine.finished:
            row = await engine.step()
            # Only the last state of a simultaneous time layer is observed.
            if keep_curve and (not engine.heap or engine.heap[0][0] != engine.time_ms):
                curve.append(dict(time_ms=row["time_ms"], E=row["metrics"]["E"], Q=row["metrics"]["Q"], z=row["z"]))
            engine.trace[:] = [row]
        result = dict(status="completed", score=row["metrics"]["E"], metrics=row["metrics"], z=row["z"], final_state_hash=state_hash(engine.state), updates={k: a["count"] for k, a in engine.state["agents"].items()}, curve=curve)
    except ArithmeticError as exc:
        result = dict(status="numerical_failure", score=None, error=str(exc))
    except Exception as exc:
        result = dict(status="technical_failure", score=None, error=f"{type(exc).__name__}: {exc}")
    finally:
        await engine.runtime.close()
    result["execution_seconds"] = time.perf_counter() - started
    return result


def configuration_for(space, point):
    return materialize(space["base_configuration"], dict(zip(NAMES, point)), space).model_dump()


async def save_record(configuration, path, expected=None):
    c = EventConfiguration.model_validate(configuration)
    run = await execute_events(c, seed=0)
    validation = validate_trace(run)
    if not validation["valid"]:
        raise AssertionError(validation)
    if expected is not None and run["summary"]["E"] != expected:
        raise AssertionError("Компактная и полная оценки расходятся")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pack(run))
    return run


async def session(space, method, seed, budget, directory):
    session_started = time.perf_counter()
    previous_seconds = 0.0
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config_hash = digest(dict(version=VERSION, space=space, method=method, seed=seed, budget=budget))
    state_path, log_path = directory / "state.json", directory / "trials.jsonl"
    strategy, rows = Strategy(method, seed), []
    if state_path.exists():
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        if saved["configuration_hash"] != config_hash:
            raise ValueError("Версия, бюджет или пространство сохранённой сессии изменены")
        strategy = Strategy(method, seed, saved["strategy"])
        if log_path.exists():
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()[:strategy.count]]
        if len(rows) != strategy.count:
            raise ValueError("Журнал сессии повреждён")
        previous_seconds = saved.get("elapsed_seconds", 0.0)
        # Discard an uncommitted tail left by interruption after journal append.
        log_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    write_json(directory / "space.json", space)
    while strategy.count < budget:
        point, reason = strategy.propose()
        c = configuration_for(space, point)
        result = await evaluate(c)
        improved = strategy.observe(point, result["score"])
        if improved:
            await save_record(c, directory / "improvements" / f"{strategy.count:03}.zip", result["score"])
        row = dict(trial=strategy.count, values=list(point), reason=reason, configuration=c, configuration_hash=digest(c), improved=improved, best_score=strategy.score, best_values=strategy.best, **result)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        rows.append(row)
        write_json(state_path, dict(configuration_hash=config_hash, strategy=strategy.snapshot(), elapsed_seconds=previous_seconds + time.perf_counter() - session_started))
    summary = dict(version=VERSION, method=method, seed=seed, trials=len(rows), best_score=strategy.score, best_values=strategy.best, failures=sum(r["status"] != "completed" for r in rows), evaluation_seconds=sum(r["execution_seconds"] for r in rows), session_seconds=previous_seconds + time.perf_counter() - session_started)
    write_json(directory / "summary.json", summary)
    return summary


def run_session(job):
    return asyncio.run(session(**job))


def main():
    parser = argparse.ArgumentParser(description="Первый поиск неблагоприятных условий")
    parser.add_argument("--method", choices=METHODS, default="feedback")
    parser.add_argument("--graph", choices=GRAPHS, default="bridge")
    parser.add_argument("--algorithm", choices=("homework", "raps"), default="homework")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--budget", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.budget <= 100:
        parser.error("Бюджет первой версии: 1…100")
    print(json.dumps(run_session(dict(space=make_space(args.graph, args.algorithm), method=args.method, seed=args.seed, budget=args.budget, directory=args.output)), ensure_ascii=False))


if __name__ == "__main__":
    main()
