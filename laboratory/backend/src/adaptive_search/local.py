"""CP1 homework execution and trace-derived point-delay proposals."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from math import fsum

from cp1.diversity.observation import OutcomeAssessment
from cp1.event_engine import EventDecisions, EventEngine
from cp1.event_models import EventConfiguration
from cp1.event_validator import validate_event_trace
from cp1.recording import digest


Key = tuple[str, str, int]


def local_profile(name: str) -> dict:
    """Return a fresh configuration for one of the two named Local profiles."""
    if name == "development":
        values = [0, 3, 10, 7, 2, 0]
        links = [(0, 1), (1, 2), (0, 2), (2, 3), (3, 4), (4, 5), (3, 5)]
        links += [(b, a) for a, b in links]
        slow, interval, jitter = 2, 2520, 170
        delay, delay_jitter = 720, 280
        slow_edge, slow_delay = (3, 2), 1110
    elif name == "heldout":
        values = [-2, 5, 1, 8, 0, 4, 9, -1]
        links = [(0, 1), (1, 2), (2, 0), (2, 3), (3, 4), (4, 5), (5, 6),
                 (6, 7), (7, 0), (7, 3), (6, 3), (5, 2), (3, 6), (1, 7)]
        slow, interval, jitter = 3, 2670, 240
        delay, delay_jitter = 640, 330
        slow_edge, slow_delay = (7, 3), 1350
    else:
        raise ValueError(f"Unknown Local profile {name!r}")

    return {
        "name": f"local-{name}",
        "algorithm": "homework",
        "horizon_ms": 60_000,
        "max_gap_ms": 4_000,
        "max_delay_ms": 2_000,
        "defaults": {"interval_ms": 1000, "interval_jitter_ms": 120,
                     "delay_ms": delay, "delay_jitter_ms": delay_jitter},
        "nodes": [
            {"id": f"a{i}", "initial": value,
             "overrides": {"first_update_ms": 880 + 65 * i, **(
                 {"interval_ms": interval, "interval_jitter_ms": jitter}
                 if i == slow else {}
             )}}
            for i, value in enumerate(values)
        ],
        "edges": [
            {"source": f"a{a}", "target": f"a{b}",
             "overrides": {"delay_ms": slow_delay} if (a, b) == slow_edge else {}}
            for a, b in sorted(links)
        ],
    }


def _run_core(coroutine):
    # EventRuntime dispatches in memory. This also works when Windows cannot
    # create the socketpair asyncio would otherwise open for its event loop.
    try:
        yielded = coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise RuntimeError(f"Core runtime suspended for asynchronous work: {yielded!r}")


class _OverrideRejected(ValueError):
    pass


class _PrefixMismatch(ValueError):
    pass


class _Overrides(EventDecisions):
    def __init__(self, seed: int, overrides: dict[Key, int]):
        super().__init__(seed)
        self.overrides = overrides
        self.hits: set[Key] = set()

    def choose(self, kind, entity, lo, hi, explicit=None):
        key = kind, entity, self.counters.get(f"{kind}:{entity}", 0)
        value = super().choose(kind, entity, lo, hi, explicit)
        if key not in self.overrides:
            return value
        new_value = self.overrides[key]
        if explicit is not None or not lo <= new_value <= hi:
            raise _OverrideRejected(f"Override for {key!r} is outside its seeded range")
        if self.recorded is not None and value != new_value:
            raise _OverrideRejected(f"Override for {key!r} changes a recorded decision")
        self.hits.add(key)
        self.records[-1]["value"] = new_value
        return new_value


def _outcome(observer):
    result = observer.result()
    return {**result, "severity": result.get("E", 0.0)}


def _recorded_overrides(run: dict) -> dict[Key, int]:
    return {tuple(item["key"]): item["value"] for item in run.get("overrides", ())}


class LocalProblem:
    """Native keyed Local execution with an inclusive, re-executed prefix cut."""

    def __init__(self, config: dict):
        self._config = EventConfiguration.model_validate(config)
        if self._config.algorithm != "homework":
            raise ValueError("LocalProblem needs the CP1 homework algorithm")
        self.configuration = self._config.model_dump()
        self._values = [node.initial for node in self._config.nodes]
        self.span = max(self._values) - min(self._values)
        if not self.span:
            raise ValueError("LocalProblem needs a positive initial range")
        self._entities = {"delay": {edge.id for edge in self._config.edges},
                          "interval": {node.id for node in self._config.nodes}}

    def simulate(self, seed: int, overrides: dict[Key, int] | None = None,
                 parent: dict | None = None, cut: int | None = None) -> dict:
        changes = dict(overrides or {})
        if parent is None and cut is None:
            return _run_core(self._execute(seed, changes))
        if (parent is None or parent.get("status", "completed") != "completed"
                or parent.get("configuration") != self.configuration or type(cut) is not int
                or not 0 <= cut < len(parent["trace"]) - 1):
            return self._failure(seed, "rejected_prefix", "Invalid parent or inclusive prefix cut")
        inherited = _recorded_overrides(parent) | changes
        provenance = {"id": parent["manifest"]["id"], "tick": cut,
                      "state_hash": parent["trace"][cut]["state_hash"]}
        return _run_core(self._execute(seed, inherited, parent["trace"], cut, provenance))

    def _failure(self, seed, status, error):
        return {"status": status, "error": error, "seed": seed, "processed_events": 0,
                "prefix_events": 0, "trace": [], "configuration": self.configuration,
                "missing_overrides": [], "outcome": {"signature": None, "severity": 0.0}}

    async def _execute(self, seed, overrides, recorded=None, replay_cut=None, provenance=None):
        for key, value in overrides.items():
            if (not isinstance(key, tuple) or len(key) != 3
                    or not isinstance(key[0], str) or not isinstance(key[1], str)
                    or key[1] not in self._entities.get(key[0], ())
                    or type(key[2]) is not int or key[2] < 0 or type(value) is not int):
                return self._failure(seed, "rejected_override", f"Invalid override {key!r}={value!r}")

        engine = EventEngine(self._config, seed)
        source = engine.source = _Overrides(seed, overrides)
        observer = OutcomeAssessment(self._values, self._config.horizon_ms)
        prefix_events = 0
        status, error = "completed", None
        try:
            row = await engine.start()
            while True:
                if replay_cut is not None and engine.tick <= replay_cut:
                    if row != recorded[engine.tick]:
                        raise _PrefixMismatch(f"Recorded state differs at tick {engine.tick}")
                    prefix_events = engine.tick
                if not engine.heap or engine.heap[0][0] != engine.time_ms:
                    observer.layer(row["time_ms"], row["metrics"], row["z"])
                if engine.finished:
                    break
                next_tick = engine.tick + 1
                decisions = (recorded[next_tick]["decisions"]
                             if replay_cut is not None and next_tick <= replay_cut else None)
                row = await engine.step(decisions)
            if replay_cut is not None and engine.tick < replay_cut:
                raise _PrefixMismatch("Execution ended before the recorded cut")
        except _OverrideRejected as exc:
            status, error = "rejected_override", str(exc)
        except _PrefixMismatch as exc:
            status, error = "rejected_prefix", str(exc)
        except ArithmeticError as exc:
            status, error = "numerical_failure", str(exc)
        except ValueError as exc:
            status, error = "technical_failure", str(exc)
        finally:
            await engine.runtime.close()

        missing = sorted(overrides.keys() - source.hits)
        if missing and status == "completed":
            status, error = "missing_override", f"Unreached decision keys: {missing!r}"
        result = (engine.result(status=status, parent=provenance) if engine.trace
                  else self._failure(seed, status, error))
        result.update(
            seed=seed, status=status, error=error, processed_events=engine.tick,
            prefix_events=prefix_events, trace_hash=digest(result["trace"]),
            overrides=[{"key": list(key), "value": value} for key, value in sorted(overrides.items())],
            missing_overrides=[list(key) for key in missing], outcome=_outcome(observer),
        )
        return result

    def replay(self, run: dict) -> dict:
        if (run.get("status", "completed") != "completed" or not run.get("trace")
                or run.get("configuration") != self.configuration):
            raise ValueError("Exact replay needs a completed run from this Local configuration")
        repeated = _run_core(self._execute(
            run["manifest"]["seed"], _recorded_overrides(run), run["trace"],
            len(run["trace"]) - 1, run["manifest"].get("parent"),
        ))
        if (repeated["status"] != "completed" or repeated["trace"] != run["trace"]
                or repeated["summary"] != run["summary"] or repeated["outcome"] != run["outcome"]):
            raise ValueError(f"Local replay differs: {repeated['error'] or 'summary/outcome'}")
        return repeated

    def validate(self, run: dict) -> dict:
        if run.get("status", "completed") != "completed" or run.get("configuration") != self.configuration:
            return {"valid": False, "complete": False, "errors": ["Incomplete or foreign Local run"]}
        audit = validate_event_trace(run)
        trace = run["trace"]
        decisions = {}
        observer = OutcomeAssessment(self._values, self._config.horizon_ms)
        for i, row in enumerate(trace):
            decisions.update({(d["kind"], d["entity"], d["ordinal"]): d["value"]
                              for d in row["decisions"]})
            if i + 1 == len(trace) or row["time_ms"] != trace[i + 1]["time_ms"]:
                observer.layer(row["time_ms"], row["metrics"], row["z"])
        for valid, message in (
            (audit["complete"], "Missing final event"),
            (run.get("trace_hash") == digest(trace), "Trace digest differs"),
            (run.get("processed_events") == len(trace) - 1, "Event count differs"),
            (all(decisions.get(key) == value for key, value in _recorded_overrides(run).items()),
             "A planned override is absent from the decision records"),
            (run.get("outcome") == _outcome(observer), "Outcome differs from trace assessment"),
        ):
            if not valid:
                audit["valid"] = False
                audit["errors"].append(message)
        return audit

    def pool(self, run: dict) -> list[dict]:
        if run.get("status", "completed") != "completed":
            return []
        updates, sends = _read_windows(run)
        explicit_edges = ((self._config.schedule or {}).get("delays") or {})
        changed = _recorded_overrides(run)
        proposals = []
        for edge, edge_sends in sorted(sends.items()):
            if explicit_edges.get(edge):
                continue
            windows = updates[edge_sends[0]["message"]["receiver"]]
            times = [window["time_ms"] for window in windows]
            for i, send in enumerate(edge_sends):
                if send["key"] in changed:
                    continue
                proposals.extend(_proposals(edge_sends, i, windows, times, self.span))
        return sorted(proposals, key=lambda item: (item["send_tick"], item["key"], item["value"]))


def _read_windows(run):
    """Index actual receiver updates and transport choices in the parent trace."""
    updates, sends = defaultdict(list), defaultdict(list)
    previous = run["trace"][0]["z"]
    for row in run["trace"][1:]:
        delay = {d["entity"]: d for d in row["decisions"] if d["kind"] == "delay"}
        for event in row["events"]:
            if event["kind"] == "update":
                agent = event["agent"]
                updates[agent].append({"time_ms": row["time_ms"], "before": previous[agent],
                                       "batch": event["used"]})
            elif event["kind"] == "send":
                message = event["message"]
                edge = f"{message['sender']}>{message['receiver']}"
                decision = delay[edge]
                sends[edge].append({"message": message, "key": ("delay", edge, decision["ordinal"]),
                                    "tick": row["tick"], "lo": decision["lo"], "hi": decision["hi"]})
        previous = row["z"]
    return updates, sends


def _fifo_movements(edge_sends, index, due, times):
    """Propagate the changed FIFO floor over fixed later parent send decisions."""
    shifts = []
    for i in range(index, len(edge_sends)):
        message = edge_sends[i]["message"]
        if i != index:
            due = max(due, message["sent_ms"] + message["transport_ms"])
        if due == message["due_ms"]:
            break
        old, new = (bisect_right(times, at) for at in (message["due_ms"], due))
        if old != new:
            if max(old, new) == len(times):
                return []  # The complete parent lacks one affected update.
            shifts.append((old, new, message))
    return shifts


def _effects(windows, shifts, span):
    """Forecast affected updates with frozen parent payloads and the Local recurrence."""
    removed, added = defaultdict(list), defaultdict(list)
    for old, new, message in shifts:
        removed[old].append(message)
        added[new].append(message)
    old, new, message = shifts[0]
    epsilon = 1e-12 + 1e-10 * span
    peak = displacement = new_contribution = 0.0
    for index in range(min(removed.keys() | added.keys()), max(removed.keys() | added.keys()) + 1):
        window = windows[index]
        before, batch = window["before"], window["batch"]
        if index == new:
            new_contribution = fsum((message["value"], -before, -displacement)) / 7
        entering, leaving = added[index], removed[index]
        count = len(batch) + len(entering) - len(leaving)
        direct = fsum([m["value"] - before for m in entering]
                      + [before - m["value"] for m in leaving]) / 7
        displacement = fsum((displacement, direct, -count * displacement / 7))
        peak = max(peak, abs(displacement))

    old_contribution = (message["value"] - windows[old]["before"]) / 7
    direction = "earlier" if new < old else "later"
    sign = lambda value: int(value > epsilon) - int(value < -epsilon)
    if peak <= epsilon:
        return old_contribution, new_contribution, 0.0, "local:neutral"
    impact = 1 + sum(peak - epsilon > span * threshold for threshold in (0.001, 0.01, 0.1))
    feature = f"local:{direction}:{sign(old_contribution)}:{sign(new_contribution)}:{impact}"
    return old_contribution, new_contribution, peak / span, feature


def _proposals(edge_sends, index, windows, times, span):
    send = edge_sends[index]
    message, lo, hi = send["message"], send["lo"], send["hi"]
    if lo == hi:
        return
    previous_due = edge_sends[index - 1]["message"]["due_ms"] if index else 0
    source = bisect_right(times, message["due_ms"])
    if source == len(times):
        return
    if not any(item["id"] == message["id"] for item in windows[source]["batch"]):
        raise ValueError("Parent message usage differs from its recorded FIFO delivery")

    sent = message["sent_ms"]
    # At each neighboring update, equality is late because updates precede deliveries.
    directions = []
    if source:
        directions.append((times[source - 1], -1))
    if source + 1 < len(times):
        directions.append((times[source], 1))
    for boundary, direction in directions:
        low_side = boundary - sent - 1
        if previous_due >= boundary or not lo <= low_side < hi:
            continue
        value = low_side if direction == -1 else low_side + 1
        due = max(previous_due, sent + value)
        shifts = _fifo_movements(edge_sends, index, due, times)
        if not shifts:
            continue
        old, new, _ = shifts[0]
        old_contribution, new_contribution, score, feature = _effects(windows, shifts, span)
        # A boundary with legal delay mass on both sides is sensitive to timing alone.
        baseline = min(low_side - lo + 1, hi - low_side) / (hi - lo + 1)
        yield {
            "key": send["key"], "value": value, "cut": send["tick"] - 1,
            "send_tick": send["tick"], "sent_ms": sent, "message_id": message["id"],
            "old_value": message["transport_ms"], "old_due_ms": message["due_ms"],
            "new_due_ms": due, "previous_due_ms": previous_due, "boundary_ms": boundary,
            "old_window_ms": times[old], "new_window_ms": times[new],
            "old_contribution": old_contribution, "new_contribution": new_contribution,
            "fifo_transfers": len(shifts) - 1, "baseline_score": baseline,
            "feedback_score": score, "feature": feature,
        }
