"""Asynchronous pairwise Max-Sum in the equivalent normalized min-cost form.

Each undirected Potts factor is eliminated into an outgoing three-entry
message. On activation a sensor reads the latest delivered vector per neighbor.
It decodes the lowest-index minimum belief, then sends a cavity message in
each direction. The receiver label is the index of that outgoing vector.
"""
from __future__ import annotations

import copy
import heapq
import itertools
from functools import lru_cache

from cp1.event_engine import EventDecisions
from cp1.recording import digest

CHANNELS = range(3)
VERSION = "async-pairwise-min-sum/1"
PRIORITY = {"update": 1, "delivery": 2, "finish": 4}


def profile(name, n=6, horizon_ms=60000):
    """Deterministic connected examples; tree has a unique alternating optimum."""
    if name not in ("tree", "cycle", "chorded") or n not in (6, 7, 8):
        raise ValueError("Expected tree, cycle or chorded and 6–8 sensors")
    edges = [[i, i + 1, 9] for i in range(n - 1)]
    unary = [[0, 2 if i == 0 else 1, 8] for i in range(n)]
    if name != "tree":
        edges.append([0, n - 1, 9])
        unary = [[0, 1 + int(i % 4 == 0), 4 + i % 3] for i in range(n)]
    if name == "chorded":
        edges.extend([[0, 2, 8], [2, 4, 7]])
    return dict(version=VERSION, name=name, unary=unary, edges=edges,
                first_update_ms=[500 + 37 * i for i in range(n)],
                interval_ms=[1800, 4500], delay_ms=[100, 4200],
                horizon_ms=horizon_ms)


def _problem(config):
    unary = tuple(tuple(row) for row in config["unary"])
    edges = tuple(tuple(edge) for edge in config["edges"])
    n = len(unary)
    if not n or any(len(row) != 3 or any(type(x) is not int for x in row) for row in unary):
        raise ValueError("Expected three integer costs per sensor")
    seen = set()
    for edge in edges:
        if len(edge) != 3 or any(type(x) is not int for x in edge):
            raise ValueError("Expected integer [left, right, weight]")
        left, right, weight = edge
        pair = tuple(sorted((left, right)))
        if not (0 <= left < n and 0 <= right < n) or left == right or weight < 0 or pair in seen:
            raise ValueError("Invalid or duplicate pairwise factor")
        seen.add(pair)
    return unary, edges


@lru_cache(maxsize=128)
def _exhaustive(unary, edges):
    # The verifier enumerates all assignments directly, independent of beliefs,
    # decoding, local delta updates and the scheduler.
    best, first, count = None, None, 0
    for assignment in itertools.product(CHANNELS, repeat=len(unary)):
        total = sum(row[c] for row, c in zip(unary, assignment))
        total += sum(w for i, j, w in edges if assignment[i] == assignment[j])
        if best is None or total < best:
            best, first, count = total, assignment, 1
        elif total == best:
            count += 1
    return best, first, count


def oracle(config):
    unary, edges = _problem(config)
    cost, assignment, n_optima = _exhaustive(unary, edges)
    return dict(cost=cost, assignment=list(assignment), n_optima=n_optima, evaluated=3 ** len(unary))


def _choice(scores):
    return min(CHANNELS, key=scores.__getitem__)


def _project(cavity, penalty):
    raw = [min(value + (penalty if a == b else 0)
               for a, value in enumerate(cavity)) for b in CHANNELS]
    floor = min(raw)
    return [x - floor for x in raw]


def _timing(config, n):
    if config.get("version") != VERSION:
        raise ValueError("Unsupported Max-Sum model version")
    starts = config["first_update_ms"]
    if len(starts) != n or any(type(t) is not int or t < 0 for t in starts):
        raise ValueError("Invalid first activation")
    for key in ("interval_ms", "delay_ms"):
        lo, hi = config[key]
        if type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi:
            raise ValueError(f"Invalid bounds for {key}")
    if type(config["horizon_ms"]) is not int or config["horizon_ms"] < 1:
        raise ValueError("Invalid horizon")


def simulate(config, seed, *, overrides=None, parent=None, cut=None):
    """Replay a prefix inclusively; its pending deliveries and timers stay fixed.

    A full replay uses parent with no cut. A branch sets cut and samples new
    decisions after it. Decisions that have already been assigned cannot be
    overridden. Entity-ordinal addressing keeps unmodified draws paired when
    the same seed is used on both trajectories.
    """
    config = copy.deepcopy(config)
    unary, edges = _problem(config)
    n = len(unary)
    _timing(config, n)
    if type(seed) is not int:
        raise ValueError("Expected an integer seed")
    reference = parent["trace"] if parent is not None else None
    if parent is not None and parent["config"] != config:
        raise ValueError("Configuration changed across replay")
    if cut is not None and (reference is None or type(cut) is not int or not 0 <= cut < len(reference)):
        raise ValueError("Invalid prefix cut")
    if reference is not None and (not reference or (cut is None and reference[-1]["events"][0]["kind"] != "finish")):
        raise ValueError("Incomplete replay")
    last_replay = len(reference) - 1 if reference is not None and cut is None else cut
    overrides = dict(overrides or {})
    source = EventDecisions(seed)
    used_overrides = set()
    def choose(kind, entity):
        key = (kind, entity, source.counters.get(f"{kind}:{entity}", 0))
        if key in overrides:
            if source.recorded is not None:
                raise ValueError("Override targets a decision in the preserved prefix")
            used_overrides.add(key)
        return source.choose(kind, entity, *config[f"{kind}_ms"], explicit=overrides.get(key))

    ids = [f"s{i}" for i in range(n)]
    index = {name: i for i, name in enumerate(ids)}
    neighbors = [[] for _ in ids]
    for left, right, weight in edges:
        neighbors[left].append((right, weight))
        neighbors[right].append((left, weight))
    for adjacent in neighbors:
        adjacent.sort()

    channels = [_choice(row) for row in unary]
    cost = sum(row[c] for row, c in zip(unary, channels))
    cost += sum(w * (channels[i] == channels[j]) for i, j, w in edges)
    optimum = oracle(config)["cost"]
    inbox = [{} for _ in ids]
    queue, last_due = [], {}
    serial = 0
    sent = delivered = updates = 0
    def schedule(t, kind, agent, payload=None):
        nonlocal serial
        serial += 1
        heapq.heappush(queue, (t, PRIORITY[kind], agent, serial, kind, payload))

    for name, first in zip(ids, config["first_update_ms"]):
        schedule(first, "update", name)
    schedule(config["horizon_ms"], "finish", "")
    trace = []
    # Initial configuration plus the authenticated ordered transitions determine
    # all state, including the queue, inboxes, decisions and FIFO watermarks.
    chain = digest(config)
    def record(t, events, decisions):
        nonlocal chain
        tick = len(trace)
        metrics = dict(cost=cost, optimum=optimum, gap=cost - optimum)
        chain = digest([chain, tick, t, events, decisions, channels, cost])
        row = dict(tick=tick, time_ms=t, state_hash=chain, decisions=decisions,
                   events=events, metrics=metrics, channels=list(channels))
        if last_replay is not None and tick <= last_replay and row != reference[tick]:
            raise ValueError(f"Replay mismatch at tick {tick}")
        trace.append(row)

    record(0, [], [])
    while True:
        t, _, agent, _, kind, message = heapq.heappop(queue)
        tick = len(trace)
        replaying = last_replay is not None and tick <= last_replay
        source.begin(reference[tick]["decisions"] if replaying else None)
        event = dict(kind=kind, agent=agent, time_ms=t)
        events = [event]
        if kind == "update":
            updates += 1
            i = index[agent]
            incoming = inbox[i]
            used = [incoming[j] for j, _ in neighbors[i] if j in incoming]
            scores = [v + sum(m["values"][c] for m in used) for c, v in enumerate(unary[i])]
            before, after = channels[i], _choice(scores)
            old_cost = cost
            cost += unary[i][after] - unary[i][before]
            cost += sum(w * ((after == channels[j]) - (before == channels[j])) for j, w in neighbors[i])
            channels[i] = after
            next_t = t + choose("interval", agent)
            schedule(next_t, "update", agent)
            ordered = sorted(scores)
            event.update(used=used, scores=scores, previous_channel=before, channel=after,
                         margin=ordered[1] - ordered[0], cost_before=old_cost,
                         cost_after=cost, next_update_ms=next_t)
            for j, w in neighbors[i]:
                edge = f"{agent}>{ids[j]}"
                excluded = incoming[j]["values"] if j in incoming else (0, 0, 0)
                cavity = [value - excluded[c] for c, value in enumerate(scores)]
                values = _project(cavity, w)
                delay = choose("delay", edge)
                ordinal = source.records[-1]["ordinal"]
                due = max(t + delay, last_due.get(edge, 0))
                last_due[edge] = due
                outgoing = dict(id=f"{edge}#{ordinal}", sender=agent, receiver=ids[j],
                                ordinal=ordinal, values=values, sent_ms=t, due_ms=due,
                                transport_ms=delay, fifo_wait_ms=due - t - delay)
                schedule(due, "delivery", ids[j], outgoing)
                events.append(dict(kind="send", message=outgoing, time_ms=t))
                sent += 1
        elif kind == "delivery":
            arrived = {**message, "delivered_ms": t}
            inbox[index[agent]][index[message["sender"]]] = arrived
            event["message"] = arrived
            delivered += 1
        source.finish()
        record(t, events, source.records)
        if kind == "finish":
            break
    if used_overrides != set(overrides):
        raise ValueError("An override was never consumed")
    if reference is not None and cut is None and len(trace) != len(reference):
        raise ValueError("Replay length mismatch")
    return dict(config=config, seed=seed, trace=trace,
                summary=dict(**trace[-1]["metrics"], tick=len(trace)-1,
                             time_ms=config["horizon_ms"], sent=sent, delivered=delivered,
                             pending=sent-delivered, updates=updates, channels=list(channels),
                             prefix_events=cut or 0))


def replay(result):
    return simulate(result["config"], result["seed"], parent=result)


def audit_transport(result):
    """Independently reconstruct the physical timers, FIFO and current inboxes."""
    config, trace = result["config"], result["trace"]
    n = len(config["unary"])
    bounds = config["delay_ms"]
    latest, last_due, sent, delivered = {}, {}, {}, set()
    expected, serial = [], 0
    def push(t, kind, agent, message_id=""):
        nonlocal serial
        serial += 1
        heapq.heappush(expected, (t, PRIORITY[kind], agent, serial, kind, message_id))
    for i, first in enumerate(config["first_update_ms"]):
        push(first, "update", f"s{i}")
    push(config["horizon_ms"], "finish", "")
    if trace[0]["tick"] != 0 or trace[0]["events"] or trace[0]["time_ms"] != 0:
        raise ValueError("Invalid initial state")
    last_ordinal = {}
    for tick, row in enumerate(trace[1:], 1):
        t, _, agent, _, kind, mid = heapq.heappop(expected)
        event, *sends = row["events"]
        if (row["tick"], row["time_ms"], event["time_ms"], event["agent"], event["kind"]) != (
                tick, t, t, agent, kind):
            raise ValueError("Scheduled event order mismatch")
        if kind == "update":
            used = sorted(event["used"], key=lambda m: m["sender"])
            actual = sorted((m for (s, r), m in latest.items() if r == agent), key=lambda m: m["sender"])
            if actual != used:
                raise ValueError("Update used stale or missing delivery")
            interval, *delays = row["decisions"]
            dt = event["next_update_ms"] - t
            if (interval["kind"], interval["entity"], interval["value"]) != ("interval", agent, dt):
                raise ValueError("Interval decision mismatch")
            if not config["interval_ms"][0] <= dt <= config["interval_ms"][1]:
                raise ValueError("Interval outside bounds")
            push(event["next_update_ms"], "update", agent)
            neighbors = {f"s{j if i == int(agent[1:]) else i}" for i,j,_ in config["edges"]
                         if int(agent[1:]) in (i,j)}
            if len(sends) != len(neighbors) or len(sends) != len(delays):
                raise ValueError("Missing send or delay")
            for send, decision in zip(sends, delays):
                m = send["message"]
                edge = (agent, m["receiver"])
                edge_id = ">".join(edge)
                ordinal = last_ordinal.get(edge, -1) + 1
                delay = m["transport_ms"]
                due = max(last_due.get(edge, 0), t + delay)
                if (send["kind"], send["time_ms"], m["sender"], m["sent_ms"], m["ordinal"],
                    m["due_ms"], m["fifo_wait_ms"], m["id"]) != (
                        "send", t, agent, t, ordinal, due, due - t - delay, f"{edge_id}#{ordinal}"):
                    raise ValueError("FIFO assignment or send identity mismatch")
                if (decision["kind"], decision["entity"], decision["ordinal"], decision["value"]) != (
                        "delay", edge_id, ordinal, delay) or not bounds[0] <= delay <= bounds[1]:
                    raise ValueError("Delay decision mismatch")
                if m["receiver"] not in neighbors or m["id"] in sent:
                    raise ValueError("Invalid send")
                neighbors.remove(m["receiver"])
                last_due[edge], last_ordinal[edge] = due, ordinal
                sent[m["id"]] = m
                push(due, "delivery", m["receiver"], m["id"])
        elif kind == "delivery":
            arrived = {**sent[mid], "delivered_ms": t}
            if mid in delivered or event["message"] != arrived or sends or row["decisions"]:
                raise ValueError("Delivery mismatch")
            latest[arrived["sender"], agent] = arrived
            delivered.add(mid)
        elif tick != len(trace) - 1 or sends or row["decisions"]:
            raise ValueError("Invalid finish")
    if trace[-1]["events"][0]["kind"] != "finish" or result["summary"]["tick"] != len(trace)-1:
        raise ValueError("Truncated trace")
    counts = dict(sent=len(sent), delivered=len(delivered), pending=len(sent)-len(delivered))
    if any(result["summary"].get(k) != v for k,v in counts.items()):
        raise ValueError("Transport summary mismatch")
    return dict(ok=True, **counts)
