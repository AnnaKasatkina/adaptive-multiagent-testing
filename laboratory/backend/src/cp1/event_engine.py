"""Discrete-event execution with bounded intervals, FIFO, replay and sparse state."""

from __future__ import annotations
import copy
import hashlib
import heapq
import random
import time
import uuid
from math import fsum
from .event_models import EventConfiguration, WINDOW
from .event_rule import EventRuntime
from .recording import digest, fingerprint

MAPS = ("agents", "pending", "timers", "last_due", "counters")


def state_hash(state):
    return digest(
        {
            k: {ident: digest(v) for ident, v in value.items()} if k in MAPS else value
            for k, value in state.items()
        }
    )


def apply_patch(state, patch):
    state.update(copy.deepcopy(patch["set"]))
    for key, changes in patch["maps"].items():
        for ident, value in changes.items():
            if value is None:
                state[key].pop(ident, None)
            else:
                state[key][ident] = copy.deepcopy(value)
    return state


def expand_trace(trace, copy_state=True):
    state = None
    for row in trace:
        if "state" in row:
            state = copy.deepcopy(row["state"])
        elif state is not None:
            apply_patch(state, row["patch"])
        else:
            raise ValueError("Отсутствует начальный снимок")
        yield {**row, "state": copy.deepcopy(state) if copy_state else state}


def display_record(row):
    """Read-only UI projection; storage retains maps for cheap deltas."""
    result = copy.deepcopy(row)
    result["state"]["pending"] = list(row["state"]["pending"].values())
    result["state"]["inboxes"] = {
        k: a["inbox"] for k, a in row["state"]["agents"].items()
    }
    return result


class EventDecisions:
    def __init__(self, seed):
        self.seed, self.counters = seed, {}

    def begin(self, recorded=None):
        self.recorded, self.records, self.index = recorded, [], 0

    def choose(self, kind, entity, lo, hi, explicit=None):
        key = f"{kind}:{entity}"
        ordinal = self.counters.get(key, 0)
        context = dict(kind=kind, entity=entity, ordinal=ordinal, lo=lo, hi=hi)
        if self.recorded is not None:
            if self.index >= len(self.recorded):
                raise ValueError("Запись решений оборвалась")
            row = self.recorded[self.index]
            if any(row.get(k) != v for k, v in context.items()):
                raise ValueError("Контекст решения расходится")
            value = row["value"]
        else:
            seed = int.from_bytes(
                hashlib.sha256(f"{self.seed}|{key}|{ordinal}".encode()).digest(), "big"
            )
            value = (
                explicit
                if explicit is not None
                else random.Random(seed).randint(lo, hi)
            )
        if (
            type(value) is not int
            or not lo <= value <= hi
            or explicit is not None
            and value != explicit
        ):
            raise ValueError("Недопустимое записанное решение")
        self.records.append({**context, "value": value})
        self.counters[key] = ordinal + 1
        self.index += 1
        return value

    def finish(self):
        if self.recorded is not None and self.index != len(self.recorded):
            raise ValueError("Лишние решения в записи")


class EventEngine:
    def __init__(self, config, seed=0, runtime=None):
        self.config = (
            EventConfiguration.model_validate(config)
            if isinstance(config, dict)
            else config.model_copy(deep=True)
        )
        self.seed, self.runtime = int(seed), runtime or EventRuntime()
        self.source = EventDecisions(seed)
        self.tick = self.time_ms = 0
        self.heap, self.serial = [], 0
        self.state = dict(
            time_ms=0,
            event_id=0,
            agents={},
            pending={},
            timers={},
            last_due={},
            counters={},
            control={},
        )
        self.trace, self.last_due = [], {}
        self.edges = {e.id: e for e in self.config.edges}
        self.stats = dict(
            activations=0,
            sent=0,
            delivered=0,
            applied=0,
            large_batches=0,
            transport_sum_ms=0,
            fifo_wait_sum_ms=0,
            processing_wait_sum_ms=0,
            message_age_sum_ms=0,
            value_age_sum_ms=0,
            used_messages=0,
        )
        xs = [n.initial for n in self.config.nodes]
        self.initial_range, self.mean = max(xs) - min(xs), fsum(xs) / len(xs)
        self.integral = self.auc = 0.0
        self.peak_error, self.sustained = 0.0, None
        self.finished = False
        self.hash_maps = {k: {} for k in MAPS}

    def schedule(self, t, kind, ident="", payload=None):
        self.serial += 1
        priority = {
            "episode_end": 0,
            "episode_start": 0,
            "update": 1,
            "delivery": 2,
            "cycle_start": 3,
            "finish": 4,
        }[kind]
        event = dict(
            id=str(self.serial), time_ms=t, kind=kind, entity=ident, payload=payload
        )
        self.state["timers"][event["id"]] = event
        heapq.heappush(self.heap, (t, priority, ident, self.serial, event))

    def factor(self, kind, ident):
        return next(
            (
                e.factor
                for e in self.config.episodes
                if e.kind == kind
                and ident in e.targets
                and e.start_ms <= self.time_ms < e.start_ms + e.duration_ms
            ),
            1,
        )

    def next_update(self, ident):
        schedule = (self.config.schedule or {}).get("updates", {}).get(ident)
        if schedule:
            return next(t for t in schedule if t > self.time_ms)
        p = self.config.node_parameters(ident)
        f = self.factor("agent_slowdown", ident)
        interval = self.source.choose(
            "interval",
            ident,
            (p["interval_ms"] - p["interval_jitter_ms"]) * f,
            (p["interval_ms"] + p["interval_jitter_ms"]) * f,
        )
        return self.time_ms + interval

    async def start(self):
        await self.runtime.start(self.config)
        self.state["agents"] = self.runtime.snapshot()
        for node in sorted(self.config.nodes, key=lambda n: n.id):
            t = self.config.node_parameters(node.id)["first_update_ms"]
            if self.config.algorithm == "homework":
                self.schedule(t - WINDOW, "cycle_start", node.id)
            self.schedule(t, "update", node.id)
        for ep in sorted(self.config.episodes, key=lambda e: e.id):
            self.schedule(ep.start_ms, "episode_start", ep.id)
            self.schedule(ep.start_ms + ep.duration_ms, "episode_end", ep.id)
        self.schedule(self.config.horizon_ms, "finish")
        self.state["control"] = self.control()
        row = self.record([], [], None)
        self.trace.append(row)
        return row

    def control(self):
        return dict(serial=self.serial, statistics=dict(self.stats))

    def metrics(self):
        agents = self.state["agents"]
        zs = [a["z"] for a in agents.values()]
        d, err = max(zs) - min(zs), max(abs(z - self.mean) for z in zs)
        e = err / self.initial_range if self.initial_range else None
        q = d / self.initial_range if self.initial_range else None
        self.peak_error = max(self.peak_error, err)
        accurate = (e if e is not None else err) <= self.config.epsilon
        self.sustained = (
            (self.time_ms if self.sustained is None else self.sustained)
            if accurate
            else None
        )
        rx = ry = None
        if self.config.algorithm == "raps":
            masses = {k: a["mass"] for k, a in agents.items()}
            rx = (
                fsum(a["x"] for a in masses.values())
                + fsum(
                    masses[e.source]["phi_x"] - masses[e.target]["rho"][e.source]["x"]
                    for e in self.config.edges
                )
                - self.mean * len(agents)
            )
            ry = (
                fsum(a["y"] for a in masses.values())
                + fsum(
                    masses[e.source]["phi_y"] - masses[e.target]["rho"][e.source]["y"]
                    for e in self.config.edges
                )
                - len(agents)
            )
            if (
                abs(rx) > 1e-9 * max(1, sum(abs(n.initial) for n in self.config.nodes))
                or abs(ry) > 1e-9 * len(agents)
                or any(a["y"] <= 0 for a in masses.values())
            ):
                raise ArithmeticError("Нарушен численный инвариант RAPS")
        lo, hi = (
            min(n.initial for n in self.config.nodes),
            max(n.initial for n in self.config.nodes),
        )
        return dict(
            self.stats,
            D=d,
            Q=q,
            E=e,
            error_mean=err,
            auc=self.auc,
            error_integral=self.integral,
            peak_error=self.peak_error,
            sustained_accuracy_ms=self.sustained,
            pending=len(self.state["pending"]),
            inbox=sum(len(a["inbox"]) + len(a["batch"]) for a in agents.values()),
            mass_x_residual=rx,
            mass_y_residual=ry,
            invariants_ok=True,
            initial_range_preserved=min(zs) >= lo - 1e-10 and max(zs) <= hi + 1e-10,
            algorithm_warning=None,
            complete=self.finished,
        )

    def record(self, events, decisions, before):
        self.state.update(
            time_ms=self.time_ms, event_id=self.tick, control=self.control()
        )
        self.state["last_due"], self.state["counters"] = (
            dict(self.last_due),
            dict(self.source.counters),
        )
        patch = dict(
            set={k: copy.deepcopy(v) for k, v in self.state.items() if k not in MAPS},
            maps={},
        )
        if before is not None:
            for key in MAPS:
                patch["maps"][key] = {
                    k: copy.deepcopy(self.state[key].get(k))
                    for k in set(before[key]) | set(self.state[key])
                    if before[key].get(k) is not self.state[key].get(k)
                }
        for key in MAPS:
            changes = self.state[key] if before is None else patch["maps"][key]
            for ident, value in changes.items():
                if value is None:
                    self.hash_maps[key].pop(ident, None)
                else:
                    self.hash_maps[key][ident] = digest(value)
        row = dict(
            tick=self.tick,
            event_id=self.tick,
            time_ms=self.time_ms,
            time=self.time_ms / 1000,
            events=events,
            decisions=copy.deepcopy(decisions),
            patch=patch,
            metrics=self.metrics(),
            z={k: a["z"] for k, a in self.state["agents"].items()},
            state_hash=digest(
                {
                    k: self.hash_maps[k] if k in MAPS else v
                    for k, v in self.state.items()
                }
            ),
        )
        if self.tick % 250 == 0 or self.finished:
            row["state"] = copy.deepcopy(self.state)
        return row

    async def step(self, recorded=None):
        if self.finished:
            raise ValueError("Горизонт уже достигнут")
        before = {k: dict(self.state[k]) for k in MAPS}
        t, _, _, _, event = heapq.heappop(self.heap)
        previous = self.trace[-1]["metrics"]
        self.integral += (
            (t - self.time_ms)
            / 1000
            * (previous["E"] if previous["E"] is not None else previous["error_mean"])
        )
        if previous["Q"] is not None:
            self.auc += (t - self.time_ms) / 1000 * previous["Q"]
        self.time_ms, self.tick = t, self.tick + 1
        self.state["timers"].pop(event["id"])
        self.source.begin(recorded)
        kind, ident = event["kind"], event["entity"]
        events = [dict(kind=kind, agent=ident, time_ms=t)]
        if kind in ("delivery", "cycle_start", "update"):
            command = dict(kind=kind, time_ms=t)
            if kind == "delivery":
                message = self.state["pending"].pop(event["payload"])
                command["message"] = {**message, "delivered_ms": t}
                events[0]["message"] = command["message"]
                self.stats["delivered"] += 1
            if kind == "update":
                next_t = self.next_update(ident)
                command["next_update_ms"] = next_t
                if self.config.algorithm == "homework":
                    self.schedule(next_t - WINDOW, "cycle_start", ident)
                self.schedule(next_t, "update", ident)
                self.stats["activations"] += 1
            response = await self.runtime.dispatch(ident, command)
            self.state["agents"][ident] = (
                self.runtime.agent_snapshot(ident)
                if hasattr(self.runtime, "agent_snapshot")
                else self.runtime.snapshot()[ident]
            )
            used = response["used"]
            if kind == "update":
                events[0]["used"] = used
                self.stats["applied"] += len(used)
                self.stats["large_batches"] += int(
                    self.config.algorithm == "homework" and len(used) > 7
                )
                self.stats["used_messages"] += len(used)
                for m in used:
                    self.stats["processing_wait_sum_ms"] += t - m["delivered_ms"]
                    self.stats["message_age_sum_ms"] += t - m["sent_ms"]
                    self.stats["value_age_sum_ms"] += t - m["value_time_ms"]
            for message in response["outgoing"]:
                edge = self.edges[f"{message['sender']}>{message['receiver']}"]
                p, f = (
                    self.config.edge_parameters(edge),
                    self.factor("link_slowdown", edge.id),
                )
                explicit = (self.config.schedule or {}).get("delays", {}).get(edge.id)
                ordinal = self.source.counters.get(f"delay:{edge.id}", 0)
                if explicit and ordinal >= len(explicit):
                    raise ValueError("Явная запись задержек оборвалась")
                delay = self.source.choose(
                    "delay",
                    edge.id,
                    1 if explicit else (p["delay_ms"] - p["delay_jitter_ms"]) * f,
                    self.config.max_delay_ms
                    if explicit
                    else (p["delay_ms"] + p["delay_jitter_ms"]) * f,
                    explicit[ordinal] if explicit else None,
                )
                due = max(t + delay, self.last_due.get(edge.id, 0))
                self.last_due[edge.id] = due
                message = {
                    **message,
                    "due": due,
                    "due_ms": due,
                    "transport_ms": delay,
                    "fifo_wait_ms": due - t - delay,
                }
                self.state["pending"][message["id"]] = message
                self.schedule(due, "delivery", message["receiver"], message["id"])
                self.stats["sent"] += 1
                self.stats["transport_sum_ms"] += delay
                self.stats["fifo_wait_sum_ms"] += due - t - delay
                events.append(dict(kind="send", message=message, time_ms=t))
        elif kind == "finish":
            self.finished = True
        self.source.finish()
        rec = self.record(events, self.source.records, before)
        self.trace.append(rec)
        return rec

    def result(self, status="completed", parent=None):
        from .algorithms import model_version, metadata

        return dict(
            manifest=dict(
                schema_version=2,
                model_version=model_version(self.config),
                algorithm=metadata(self.config),
                id=uuid.uuid4().hex,
                seed=self.seed,
                runtime=self.runtime.name,
                runtime_diagnostics=getattr(self.runtime, "diagnostics", {}),
                status=status,
                configuration_hash=digest(self.config.model_dump()),
                parent=parent,
                environment={
                    **fingerprint(),
                    "model_version": model_version(self.config),
                },
            ),
            configuration=self.config.model_dump(),
            trace=self.trace,
            summary={
                **self.trace[-1]["metrics"],
                "tick": self.tick,
                "event_id": self.tick,
                "time_ms": self.time_ms,
                "complete": self.finished,
            },
        )


async def execute_events(config, seed=0, runtime=None, replay=None, fork_tick=None):
    engine = EventEngine(config, seed, runtime)
    started = time.perf_counter()
    try:
        await engine.start()
        ready = time.perf_counter()
        if replay and engine.trace[0]["state_hash"] != replay[0]["state_hash"]:
            raise ValueError("Исходное состояние расходится")
        while not engine.finished:
            original = (
                replay[engine.tick + 1]
                if replay
                and engine.tick + 1 < len(replay)
                and (fork_tick is None or engine.tick + 1 <= fork_tick)
                else None
            )
            row = await engine.step(original["decisions"] if original else None)
            if original and row["state_hash"] != original["state_hash"]:
                raise ValueError(f"Повтор расходится на событии {engine.tick}")
        result = engine.result()
        result["summary"].update(
            startup_seconds=ready - started,
            execution_seconds=time.perf_counter() - ready,
        )
        return result
    finally:
        await engine.runtime.close()
