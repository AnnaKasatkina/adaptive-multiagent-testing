from __future__ import annotations

import copy
import math
import time
from collections import Counter, defaultdict

from ..event_engine import EventEngine, MAPS
from ..event_models import EventConfiguration
from ..recording import digest

SAMPLES = (5000, 10000, 20000, 40000, 60000)


class Observation:
    """Streaming descriptors. Observe numerical values only at completed time layers."""

    def __init__(self):
        self.seen = defaultdict(set)
        self.events = set()
        self.batches, self.ages, self.waits = [], [], []
        self.repeat, self.used = 0, 0
        self.agents = defaultdict(lambda: dict(batch_max=0, batch_sum=0, updates=0))
        self.edges = defaultdict(lambda: dict(age_sum=0, wait_sum=0, used=0))
        self.samples, self.last = {}, None

    def accept(self, events, t):
        for e in events:
            kind, ident = e["kind"], e.get("agent", e.get("message", {}).get("sender", ""))
            if kind not in ("delivery", "cycle_start", "update", "send"):
                continue
            for previous in self.seen[ident]:
                self.events.add(f"{ident}|{previous}|{kind}")
            self.seen[ident].add(kind)
            if kind != "update":
                continue
            used = e["used"]
            counts = Counter(m["sender"] for m in used)
            self.batches.append(len(used))
            self.repeat += len(used) - len(counts)
            self.used += len(used)
            a = self.agents[ident]
            a["batch_max"] = max(a["batch_max"], len(used))
            a["batch_sum"] += len(used)
            a["updates"] += 1
            for m in used:
                age, wait = t - m["sent_ms"], t - m["delivered_ms"]
                self.ages.append(age / 1000)
                self.waits.append(wait / 1000)
                edge = self.edges[f"{m['sender']}>{ident}"]
                edge["age_sum"] += age
                edge["wait_sum"] += wait
                edge["used"] += 1

    def layer(self, t, metrics):
        for at in SAMPLES:
            if at <= t and str(at) not in self.samples:
                source = metrics if at == t or self.last is None else self.last
                self.samples[str(at)] = {k: source[k] for k in ("E", "Q")}
        self.last = {k: metrics[k] for k in ("E", "Q")}

    def result(self):
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
        return dict(
            version="descriptors-1", events=sorted(self.events),
            numeric=[self.samples[str(t)][k] for t in SAMPLES if str(t) in self.samples for k in ("E", "Q")],
            messages=[max(self.batches, default=0), mean(self.batches), self.repeat / max(1, self.used), mean(self.ages), max(self.ages, default=0), mean(self.waits)],
            samples=self.samples, agents=dict(self.agents), edges=dict(self.edges),
        )


class TapeEngine(EventEngine):
    """Original transitions and hashes, without copying state deltas for every trial."""

    def record(self, events, decisions, before):
        self.state.update(time_ms=self.time_ms, event_id=self.tick, control=self.control(),
                          last_due=dict(self.last_due), counters=dict(self.source.counters))
        for key in MAPS:
            changes = self.state[key] if before is None else {
                k: self.state[key].get(k) for k in set(before[key]) | set(self.state[key])
                if before[key].get(k) is not self.state[key].get(k)
            }
            for ident, value in changes.items():
                if value is None:
                    self.hash_maps[key].pop(ident, None)
                else:
                    self.hash_maps[key][ident] = digest(value)
        return dict(tick=self.tick, time_ms=self.time_ms, decisions=copy.deepcopy(decisions),
                    state_hash=digest({k: self.hash_maps[k] if k in MAPS else v for k, v in self.state.items()}),
                    metrics=self.metrics(), events=events)


async def evaluate(configuration, seed, parent=None, cut=None):
    started = time.perf_counter()
    c = EventConfiguration.model_validate(configuration)
    engine, obs, tape, layers = TapeEngine(c, seed), Observation(), [], []
    prefix_events = 0
    descriptor_seconds = 0.0
    status, error = "completed", None
    try:
        row = await engine.start()
        if parent and row["state_hash"] != parent[0]["state_hash"]:
            raise ValueError("Исходное состояние префикса расходится")
        while True:
            tape.append({k: row[k] for k in ("tick", "time_ms", "decisions", "state_hash")})
            feature_start = time.perf_counter()
            obs.accept(row["events"], row["time_ms"])
            if not engine.heap or engine.heap[0][0] != engine.time_ms:
                obs.layer(engine.time_ms, row["metrics"])
                layers.append([engine.time_ms, engine.tick])
            descriptor_seconds += time.perf_counter() - feature_start
            if engine.finished:
                break
            original = parent[engine.tick + 1] if parent and engine.tick + 1 <= cut else None
            row = await engine.step(original["decisions"] if original else None)
            if original:
                prefix_events += 1
                if row["state_hash"] != original["state_hash"]:
                    raise ValueError(f"Префикс расходится на событии {engine.tick}")
            engine.trace[:] = [row]
        score = row["metrics"]["E"]
        if score is None or not math.isfinite(score):
            raise ValueError("Для поиска требуется ненулевой исходный диапазон и конечная оценка")
    except ArithmeticError as exc:
        status, error, score = "numerical_failure", str(exc), None
    except Exception as exc:
        status, error, score = "technical_failure", f"{type(exc).__name__}: {exc}", None
    finally:
        await engine.runtime.close()
    return dict(status=status, error=error, score=score, metrics=row["metrics"] if tape else {},
                descriptors=obs.result(), tape=tape, layers=layers, prefix_events=prefix_events,
                processed_events=engine.tick, execution_seconds=time.perf_counter() - started,
                descriptor_seconds=descriptor_seconds, final_state_hash=tape[-1]["state_hash"] if tape else None,
                execution_hash=digest([(r["time_ms"], r["decisions"]) for r in tape]))
