from __future__ import annotations

import copy
import itertools
import math
from ..event_models import EventConfiguration, event_preset


def context(graph="bridge", algorithm="homework", regime="window", n=6, heldout=False):
    c = event_preset(graph, n, algorithm).model_dump()
    for i, node in enumerate(c["nodes"]):
        node["initial"] = [0, 10, 2, 8][i % 4] if heldout else 0 if i < n // 2 else 10
    c["defaults"].update(interval_jitter_ms=100, delay_ms=800 if regime == "window" else 200,
                         delay_jitter_ms=400 if regime == "window" else 100)
    return EventConfiguration.model_validate(c).model_dump()


def regular(base):
    c = copy.deepcopy(base)
    c["defaults"].update(interval_ms=1000, interval_jitter_ms=0, first_update_ms=1000,
                         delay_ms=200, delay_jitter_ms=0)
    c["episodes"], c["schedule"] = [], None
    for obj in c["nodes"] + c["edges"]:
        obj["overrides"] = {}
    return c


def edge_ids(c):
    return sorted(f"{e['source']}>{e['target']}" for e in c["edges"])


def settings(base, values, variant="individual"):
    c = regular(base)
    agent, edge = values["agent"], values["edge"]
    if variant in ("individual", "phase"):
        next(n for n in c["nodes"] if n["id"] == agent)["overrides"].update(interval_ms=values["interval"], first_update_ms=values.get("first", 1000))
        next(e for e in c["edges"] if f"{e['source']}>{e['target']}" == edge)["overrides"]["delay_ms"] = values["delay"]
    else:
        kinds = ("agent_slowdown", "link_slowdown") if variant == "joint_episode" else ("agent_slowdown",) if variant == "agent_episode" else ("link_slowdown",)
        c["episodes"] = [dict(id=f"search-{kind}", kind=kind, targets=[agent if kind == "agent_slowdown" else edge], start_ms=values["start"], duration_ms=values["duration"], factor=values["factor"]) for kind in kinds]
    return EventConfiguration.model_validate(c).model_dump()


def random_values(base, rng, variant="individual"):
    v = dict(agent=rng.choice([n["id"] for n in base["nodes"]]), edge=rng.choice(edge_ids(base)))
    if variant in ("individual", "phase"):
        v.update(interval=rng.randint(1000, 12000), delay=rng.randint(50, 8000))
        if variant == "phase":
            v["first"] = rng.randint(800, 1000)
    else:
        v.update(start=rng.choice((5000, 20000, 40000)), duration=rng.choice((2000, 10000)), factor=rng.choice((2, 4)))
    return v


def logical_values(base, budget=100, variant="individual"):
    agents, edges = [n["id"] for n in base["nodes"]], edge_ids(base)
    # Frozen before experiments: one slow object, temporal boundaries, extremes.
    values = []
    if variant in ("individual", "phase"):
        for interval, delay, agent, edge in itertools.product((8000, 1000, 4000, 12000, 2000), (200, 799, 800, 801, 8000, 50), agents, edges):
            values.append(dict(agent=agent, edge=edge, interval=interval, delay=delay, **({"first": (800, 801, 999, 1000)[len(values) % 4]} if variant == "phase" else {})))
        # Round-robin offsets avoid spending the whole budget on the first node.
        candidates = [values[(i * 37) % len(values)] for i in range(len(values))]
        first = [dict(agent=a, edge=edges[i % len(edges)], interval=8000, delay=200, **({"first": 1000} if variant == "phase" else {})) for i, a in enumerate(agents)]
        candidates = first + candidates
    else:
        candidates = [dict(agent=a, edge=e, start=s, duration=d, factor=f) for s, d, f, a, e in itertools.product((5000, 20000, 40000), (2000, 10000), (2, 4), agents, edges)]
    unique, seen = [], set()
    for v in candidates:
        key = tuple(sorted(v.items()))
        if key not in seen:
            unique.append(v); seen.add(key)
        if len(unique) == budget:
            return unique
    raise ValueError("Недостаточно уникальных логических условий")


def mutate_values(base, incumbent, rng, variant="individual", guidance=None):
    v = dict(incumbent)
    field = rng.choice(sorted(v))
    if field in ("agent", "edge"):
        choices = [n["id"] for n in base["nodes"]] if field == "agent" else edge_ids(base)
        if guidance and rng.random() < .5:
            stats = guidance["agents" if field == "agent" else "edges"]
            score = lambda ident: stats.get(ident, {}).get("batch_max", 0) if field == "agent" else (stats.get(ident, {}).get("age_sum", 0) + stats.get(ident, {}).get("wait_sum", 0)) / max(1, stats.get(ident, {}).get("used", 0))
            best = max(map(score, choices))
            choices = [x for x in choices if score(x) == best]
        v[field] = rng.choice(choices)
    elif field in ("interval", "delay", "first"):
        lo, hi = {"interval": (1000, 12000), "delay": (50, 8000), "first": (800, 1000)}[field]
        radius = rng.choice((1, math.ceil(.01 * (hi-lo)), math.ceil(.1 * (hi-lo))))
        lower, upper = max(lo, v[field]-radius), min(hi, v[field]+radius)
        value = rng.randrange(lower, upper)
        v[field] = value + (value >= v[field])
    else:
        choices = {"start": (5000, 20000, 40000), "duration": (2000, 10000), "factor": (2, 4)}[field]
        v[field] = rng.choice([x for x in choices if x != v[field]])
    return v


def graph_with_edges(base, pairs, previous=None):
    c = copy.deepcopy(base)
    old = {f"{e['source']}>{e['target']}": e["overrides"] for e in (previous or base)["edges"]}
    c["edges"] = [dict(source=a, target=b, overrides=copy.deepcopy(old.get(f"{a}>{b}", {}))) for a, b in sorted(pairs)]
    return EventConfiguration.model_validate(c).model_dump()


def random_graph(base, rng, directed=False, count=None):
    n = len(base["nodes"])
    count = count or (8 if directed and n == 6 else 7 if n == 6 else 10 if directed else 9)
    ids = [x["id"] for x in base["nodes"]]
    possible = list(itertools.permutations(ids, 2) if directed else itertools.combinations(ids, 2))
    for _ in range(100000):
        chosen = rng.sample(possible, count)
        if not directed:
            chosen += [(b, a) for a, b in chosen]
        try:
            return graph_with_edges(base, chosen)
        except ValueError:
            pass
    raise ValueError("Не удалось получить связный граф в заданной области")


def mutate_graph(c, rng, directed=False):
    ids = [x["id"] for x in c["nodes"]]
    edges = {(e["source"], e["target"]) for e in c["edges"] if directed or e["source"] < e["target"]}
    possible = set(itertools.permutations(ids, 2) if directed else itertools.combinations(ids, 2))
    for _ in range(1000):
        chosen = edges - {rng.choice(sorted(edges))}
        chosen.add(rng.choice(sorted(possible - edges)))
        pairs = chosen if directed else chosen | {(b, a) for a, b in chosen}
        try:
            return graph_with_edges(c, pairs, c)
        except ValueError:
            pass
    raise ValueError("Нет допустимой перестройки")


def effective_values(c):
    nodes = [n for n in c['nodes'] if n['overrides'].get('interval_ms')]
    edges = [e for e in c['edges'] if e['overrides'].get('delay_ms')]
    node, edge = nodes[0] if nodes else c['nodes'][0], edges[0] if edges else c['edges'][0]
    return dict(agent=node['id'], edge=f"{edge['source']}>{edge['target']}", interval=node['overrides'].get('interval_ms',1000), delay=edge['overrides'].get('delay_ms',200))
