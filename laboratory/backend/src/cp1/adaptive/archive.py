from __future__ import annotations

from itertools import combinations


def bounded(xs):
    return [0.0 if v is None else v / (1 + v) for v in xs]


def distance(a, b, kind):
    if kind == "events":
        x, y = set(a[kind]), set(b[kind])
        return 1 - len(x & y) / len(x | y) if x | y else 0.0
    if kind == "combined":
        return (distance(a, b, "numeric") + distance(a, b, "messages")) / 2
    x, y = bounded(a[kind]), bounded(b[kind])
    if len(x) != len(y):
        raise ValueError("Несопоставимые версии или горизонты признаков")
    return sum(abs(u - v) for u, v in zip(x, y)) / max(1, len(x))


def update_archive(ids, rows, candidate, method, rng, capacity=32):
    """Pure deterministic admission except the explicit random reservoir control."""
    ids = list(ids)
    row = rows[candidate - 1]
    if row["status"] != "completed":
        return ids
    if method == "branch_random":
        if len(ids) < capacity:
            return ids + [candidate]
        completed = sum(r["status"] == "completed" for r in rows[:candidate])
        i = rng.randrange(completed)
        if i < capacity:
            ids[i] = candidate
        return sorted(ids)
    key = lambda i: (-rows[i - 1]["score"], i)
    if method in ("independent", "branch_best", "elite"):
        return sorted(ids + [candidate], key=key)[:1 if method != "elite" else capacity]
    for old in ids:
        if distance(rows[old - 1]["descriptors"], row["descriptors"], method) == 0:
            return sorted([i for i in ids if i != old] + [min((old, candidate), key=key)])
    ids.append(candidate)
    if len(ids) > capacity:
        best = min(ids, key=key)
        pairs = sorted(combinations(ids, 2), key=lambda p: (distance(rows[p[0]-1]["descriptors"], rows[p[1]-1]["descriptors"], method), p))
        a, b = pairs[0]
        remove = b if a == best else a if b == best else max((a, b), key=key)
        ids.remove(remove)
    return sorted(ids)
