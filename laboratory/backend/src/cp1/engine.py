from __future__ import annotations
from dataclasses import asdict, replace
from math import fsum, isfinite
import hashlib
import random
import time
import uuid
from .algorithm import CoreRuntime
from .models import Configuration
from .recording import digest, fingerprint
from .algorithms import model_version, metadata


class DecisionSource:
    def __init__(self, seed):
        self.seed = seed
        self.records = []
        self.replay = None
        self.index = 0

    def begin(self, tick, recorded=None):
        self.tick = tick
        self.records = []
        self.replay = recorded
        self.index = 0

    def choose(
        self, kind, entity, lo, hi, reason="random", probability=None, required=None
    ):
        context = {"kind": kind, "entity": entity, "lo": lo, "hi": hi, "reason": reason}
        if probability is not None:
            context["probability"] = probability
        if self.replay is not None:
            if self.index >= len(self.replay):
                raise ValueError(f"Запись решений оборвалась в такт {self.tick}")
            r = self.replay[self.index]
            self.index += 1
            if any(r.get(k) != v for k, v in context.items()):
                raise ValueError(
                    f"Контекст решения расходится в такт {self.tick}: {entity}"
                )
            value = r["value"]
            if kind == "activation":
                if (
                    type(value) is not bool
                    or required is not None
                    and value != required
                ):
                    raise ValueError("Недопустимое решение активации")
            elif type(value) is not int or not lo <= value <= hi:
                raise ValueError("Недопустимый срок доставки")
        else:
            key = f"{self.seed}|{kind}|{entity}|{self.tick}"
            rng = random.Random(
                int.from_bytes(hashlib.sha256(key.encode()).digest(), "big")
            )
            value = (
                (required if required is not None else rng.random() < probability)
                if kind == "activation"
                else rng.randint(lo, hi)
            )
        self.records.append({**context, "value": value})
        return value

    def finish(self):
        if self.replay is not None and self.index != len(self.replay):
            raise ValueError("Лишние решения в записи")


class ExecutionEngine:
    def __init__(self, config: Configuration, seed=0, runtime=None):
        self.config = config.model_copy(deep=True)
        self.seed = int(seed)
        self.runtime = runtime or CoreRuntime()
        self.source = DecisionSource(seed)
        self.tick = 0
        self.pending = []
        self.inboxes = {n.id: [] for n in config.nodes}
        self.last_active = {n.id: 0 for n in config.nodes}
        self.last_due = {e.id: 0 for e in config.edges}
        self.trace = []
        self.auc = (
            0.0
            if max(n.initial for n in config.nodes)
            != min(n.initial for n in config.nodes)
            else None
        )
        self.activations = 0
        self.sent = 0
        self.delivered = 0
        self.applied = 0
        self.range_violations = 0
        self.ids = sorted(self.inboxes)
        self.edges = {(e.source, e.target): e for e in config.edges}
        self.initial_range = max(n.initial for n in config.nodes) - min(
            n.initial for n in config.nodes
        )
        self.mean = fsum(n.initial for n in config.nodes) / len(config.nodes)

    async def start(self):
        await self.runtime.start(self.config)
        self.trace = [self._record([], [])]
        return self.trace[0]

    def _snapshot(self):
        return {
            "tick": self.tick,
            "agents": self.runtime.snapshot(),
            "pending": [
                asdict(m) for m in sorted(self.pending, key=lambda m: (m.due, m.id))
            ],
            "inboxes": {
                k: [asdict(m) for m in v] for k, v in sorted(self.inboxes.items())
            },
            "last_active": dict(self.last_active),
            "last_due": dict(self.last_due),
        }

    def _record(self, events, decisions):
        state = self._snapshot()
        agents = state["agents"]
        zs = [a["z"] for a in agents.values()]
        d = max(zs) - min(zs)
        scale = max(1.0, fsum(abs(n.initial) for n in self.config.nodes))
        tol = 1e-10 * scale
        lo = min(n.initial for n in self.config.nodes)
        hi = max(n.initial for n in self.config.nodes)
        in_range = min(zs) >= lo - tol and max(zs) <= hi + tol
        if not in_range:
            self.range_violations += 1
        residual_x = residual_y = None
        if self.config.algorithm == "raps":
            mx = fsum(a["x"] for a in agents.values()) + fsum(
                agents[e.source]["phi_x"] - agents[e.target]["rho"][e.source]["x"]
                for e in self.config.edges
            )
            my = fsum(a["y"] for a in agents.values()) + fsum(
                agents[e.source]["phi_y"] - agents[e.target]["rho"][e.source]["y"]
                for e in self.config.edges
            )
            residual_x = mx - fsum(n.initial for n in self.config.nodes)
            residual_y = my - len(agents)
            good = all(
                isfinite(v) for a in agents.values() for v in (a["x"], a["y"], a["z"])
            ) and all(a["y"] > 0 for a in agents.values())
            good = (
                good
                and abs(residual_x) <= tol
                and abs(residual_y) <= 1e-10 * len(agents)
                and in_range
            )
            if not good:
                raise ArithmeticError(
                    f"Нарушен численный инвариант RAPS в такт {self.tick}"
                )
        else:
            good = all(isfinite(z) for z in zs)
            if not good:
                raise ArithmeticError(
                    f"Домашний алгоритм: переполнение чисел в такт {self.tick}. Сохранена вся предшествующая конечная история."
                )
        metrics = {
            "D": d,
            "Q": d / self.initial_range if self.initial_range else None,
            "error_mean": max(abs(z - self.mean) for z in zs),
            "auc": self.auc,
            "mass_x_residual": residual_x,
            "mass_y_residual": residual_y,
            "activations": self.activations,
            "sent": self.sent,
            "delivered": self.delivered,
            "applied": self.applied,
            "pending": len(self.pending),
            "inbox": sum(map(len, self.inboxes.values())),
            "invariants_ok": good,
            "mass_invariant_applicable": self.config.algorithm == "raps",
            "initial_range_preserved": in_range,
            "range_violations": self.range_violations,
            "relative_mean_error": max(abs(z - self.mean) for z in zs)
            / self.initial_range
            if self.initial_range
            else None,
            "algorithm_warning": "Сообщения доставлены, но исходный обработчик их отбросил: msg.metadata отсутствует в SPADE 4.1.4. Можно сравнить вариант с исправленным чтением сообщений."
            if self.config.algorithm == "homework-original"
            and any(a.get("discarded", 0) for a in agents.values())
            else "Оценки вышли за исходный диапазон. Это результат алгоритма; допустимость расписания проверяется отдельно."
            if self.range_violations
            else None,
        }
        return {
            "tick": self.tick,
            "events": events,
            "decisions": list(decisions),
            "state": state,
            "state_hash": digest(state),
            "metrics": metrics,
        }

    async def step(self, recorded=None):
        if self.tick >= self.config.horizon:
            raise ValueError("Горизонт уже достигнут")
        if self.initial_range:
            self.auc += self.trace[-1]["metrics"]["D"] / self.initial_range
        self.tick += 1
        t = self.tick
        events = []
        self.source.begin(t, recorded)
        ready = sorted(
            (m for m in self.pending if m.due == t),
            key=lambda m: (m.sender, m.receiver, m.stamp),
        )
        self.pending = [m for m in self.pending if m.due != t]
        for msg in ready:
            self.inboxes[msg.receiver].append(msg)
            self.delivered += 1
            events.append({"kind": "delivery", "message": asdict(msg)})
        for ident in self.ids:
            p = self.config.node_parameters(ident)
            forced = t - self.last_active[ident] >= self.config.max_gap
            periodic = p["activation_mode"] == "periodic"
            required = (
                True
                if forced
                else (
                    (t - 1 - p["activation_phase"]) % p["activation_period"] == 0
                    if periodic
                    else (
                        False
                        if p["activation_probability"] == 0
                        else True
                        if p["activation_probability"] == 1
                        else None
                    )
                )
            )
            active = self.source.choose(
                "activation",
                ident,
                0,
                1,
                "forced" if forced else "periodic" if periodic else "random",
                probability=p["activation_probability"] if not periodic else None,
                required=required,
            )
            if not active:
                continue
            incoming = self.inboxes[ident]
            self.inboxes[ident] = []
            outgoing, applied = await self.runtime.activate(ident, incoming, t)
            self.last_active[ident] = t
            self.activations += 1
            self.applied += applied
            events.append(
                {
                    "kind": "activation",
                    "agent": ident,
                    "inbox_ids": [m.id for m in incoming],
                    "applied": applied,
                }
            )
            for msg in outgoing:
                edge = self.edges[(msg.sender, msg.receiver)]
                pedge = self.config.edge_parameters(edge)
                lower = max(t + pedge["delay_min"], self.last_due[edge.id] + 1)
                upper = t + pedge["delay_max"]
                if lower > upper:
                    raise ValueError("Несовместимые ограничения FIFO и задержек")
                due = self.source.choose("delivery_time", edge.id, lower, upper)
                self.last_due[edge.id] = due
                msg = replace(msg, due=due)
                self.pending.append(msg)
                self.sent += 1
                events.append({"kind": "send", "message": asdict(msg)})
        self.source.finish()
        rec = self._record(events, self.source.records)
        self.trace.append(rec)
        return rec

    def result(self, status="completed", parent=None):
        metrics = dict(self.trace[-1]["metrics"])
        qs = [t["metrics"]["Q"] for t in self.trace]
        reached = None
        for i in range(len(qs) - 1, -1, -1):
            if self.initial_range == 0:
                break
            if qs[i] is not None and qs[i] > self.config.epsilon:
                break
            reached = i
        metrics.update(
            {
                "tick": self.tick,
                "complete": self.tick == self.config.horizon,
                "sustained_accuracy_tick": reached,
            }
        )
        return {
            "manifest": {
                "schema_version": 1,
                "model_version": model_version(self.config),
                "algorithm": metadata(self.config),
                "id": str(uuid.uuid4()),
                "seed": self.seed,
                "runtime": self.runtime.name,
                "runtime_diagnostics": getattr(self.runtime, "diagnostics", {}),
                "status": status,
                "configuration_hash": digest(self.config.model_dump()),
                "parent": parent,
                "environment": {
                    **fingerprint(),
                    "model_version": model_version(self.config),
                },
            },
            "configuration": self.config.model_dump(),
            "trace": self.trace,
            "summary": metrics,
        }


async def execute(config, seed=0, runtime=None, replay=None, fork_tick=None):
    if config.schema_version == 2:
        from .event_engine import execute_events
        return await execute_events(config, seed, runtime, replay, fork_tick)
    engine = ExecutionEngine(config, seed, runtime)
    started = time.perf_counter()
    await engine.start()
    ready = time.perf_counter()
    try:
        if replay is not None:
            if replay[0]["state_hash"] != engine.trace[0]["state_hash"]:
                raise ValueError("Исходное состояние расходится")
        target = config.horizon if fork_tick is None else min(fork_tick, config.horizon)
        for t in range(1, config.horizon + 1):
            original = replay[t] if replay is not None and t <= target else None
            rec = await engine.step(
                original["decisions"] if original is not None else None
            )
            if original is not None and rec["state_hash"] != original["state_hash"]:
                raise ValueError(f"Повтор расходится в такт {t}")
        result = engine.result()
        result["summary"].update(
            {
                "startup_seconds": ready - started,
                "execution_seconds": time.perf_counter() - ready,
            }
        )
        return result
    finally:
        await engine.runtime.close()
