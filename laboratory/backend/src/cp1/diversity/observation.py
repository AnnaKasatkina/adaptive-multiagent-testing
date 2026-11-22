from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from math import ceil, isfinite

SAMPLES = (5000, 10000, 20000, 40000, 60000)
NUMERIC = (.001, .01, .1, 1, 10)
BATCH = (1, 2, 4, 8)
REPEAT = (.1, .25, .5, .75, 1)
AGE = (.2, .8, 1, 2, 4, 8)


def category(value, boundaries):
    """Zero is separate; positive intervals are [boundary, next boundary)."""
    if value is None:
        return -1
    if not isfinite(value) or value < 0:
        raise ValueError("Признак должен быть конечным и неотрицательным")
    return 0 if value == 0 else 1 + bisect_right(boundaries, value)


def quantile(values, fraction):
    if not values:
        return None
    xs = sorted(values)
    position = (len(xs) - 1) * fraction
    left = int(position)
    return xs[left] + (xs[ceil(position)] - xs[left]) * (position - left)


class BehaviorDescriptor:
    def __init__(self, configuration, kind):
        self.kind = kind
        self.degrees = Counter(e['target'] for e in configuration['edges'])
        self.seen = defaultdict(set)
        self.pairs = set()
        self.batches, self.ages, self.waits = [], [], []
        self.used = self.repeated = 0
        self.samples, self.last = {}, None

    def accept(self, events, t):
        if self.kind in ('events', 'senders'):
            for e in events:
                kind = e['kind']
                if kind not in ('delivery', 'cycle_start', 'update'):
                    continue
                receiver = e['agent']
                token = kind
                if self.kind == 'senders':
                    token += ':' + (e['message']['sender'] if kind == 'delivery' else 'self')
                for previous in self.seen[receiver]:
                    self.pairs.add((receiver, previous, token))
                self.seen[receiver].add(token)
        if self.kind not in ('messages', 'combined'):
            return
        for e in events:
            if e['kind'] != 'update':
                continue
            used = e['used']
            self.batches.append(len(used) / self.degrees[e['agent']])
            self.used += len(used)
            self.repeated += len(used) - len({m['sender'] for m in used})
            for m in used:
                self.ages.append((t - m['sent_ms']) / 1000)
                self.waits.append((t - m['delivered_ms']) / 1000)

    def layer(self, t, metrics):
        if self.kind not in ('numeric', 'combined'):
            return
        for at in SAMPLES:
            if at <= t and at not in self.samples:
                source = metrics if at == t or self.last is None else self.last
                self.samples[at] = [source['E'], source['Q']]
        self.last = dict(metrics)

    def result(self):
        numeric = [category(v, NUMERIC) for at in SAMPLES if at in self.samples for v in self.samples[at]]
        messages = [
            category(sum(self.batches) / len(self.batches) if self.batches else None, BATCH),
            category(max(self.batches) if self.batches else None, BATCH),
            category(self.repeated / self.used if self.used else None, REPEAT),
            *[category(quantile(xs, q), AGE) for xs in (self.ages, self.waits) for q in (.5, .95)],
        ] if self.kind in ('messages', 'combined') else []
        return dict(version='behavior-1', kind=self.kind, events=sorted(self.pairs), numeric=numeric, messages=messages)


def distance(a, b):
    if (a['version'], a['kind']) != (b['version'], b['kind']):
        raise ValueError('Несовместимые описания поведения')
    kind = a['kind']
    if kind in ('events', 'senders'):
        x, y = set(map(tuple, a['events'])), set(map(tuple, b['events']))
        return 1 - len(x & y) / len(x | y) if x | y else 0.
    def vector(field, maxima):
        x, y = a[field], b[field]
        if len(x) != len(y):
            raise ValueError('Несовместимые горизонты признаков')
        return sum(0 if u == v else 1 if min(u, v) < 0 else abs(u-v)/maxima[i % len(maxima)] for i, (u, v) in enumerate(zip(x, y))) / max(1, len(x))
    if kind == 'numeric':
        return vector('numeric', [len(NUMERIC)+1])
    if kind == 'messages':
        return vector('messages', [len(BATCH)+1]*2 + [len(REPEAT)+1] + [len(AGE)+1]*4)
    return (vector('numeric', [len(NUMERIC)+1]) + vector('messages', [len(BATCH)+1]*2 + [len(REPEAT)+1] + [len(AGE)+1]*4)) / 2


class OutcomeAssessment:
    """Independent streaming oracle. Never supplies parent-selection weights."""
    def __init__(self, initial, horizon=60000, threshold=.01):
        self.lo, self.hi = min(initial), max(initial)
        self.span = self.hi - self.lo
        if not self.span:
            raise ValueError('Начальное согласие: нормированная поисковая задача неприменима')
        self.mean = sum(initial) / len(initial)
        self.horizon, self.threshold = horizon, threshold
        self.previous = None
        self.precise_since = None
        self.held = self.relapse = self.outside = False
        self.bias = True
        self.signs, self.crossings = {}, Counter()
        self.samples = {}
        self.last_z = None

    def layer(self, t, metrics, z):
        e, q = metrics['E'], metrics['Q']
        if not all(isfinite(v) for v in (e, q, *z.values())):
            raise ArithmeticError('Неконечное состояние наблюдателя')
        if self.previous:
            pt, pe, pq = self.previous
            for at in SAMPLES:
                if pt <= at < t and at not in self.samples:
                    self.samples[at] = pe
            if self.precise_since is not None and t - self.precise_since >= 5000:
                self.held = True
            if t > max(0, self.horizon-10000) and pt < self.horizon:
                self.bias &= pq <= .001 and pe >= self.threshold
        if e <= .001:
            if self.precise_since is None:
                self.precise_since = t
        else:
            self.precise_since = None
        self.relapse |= self.held and e >= self.threshold
        self.outside |= min(z.values()) < self.lo - .01*self.span or max(z.values()) > self.hi + .01*self.span
        if t >= max(0, self.horizon-10000):
            self.bias &= q <= .001 and e >= self.threshold
        if t >= max(0, self.horizon-20000):
            boundary = max(0, self.horizon-20000)
            if self.previous and self.previous[0] < boundary < t and self.last_z:
                for ident, value in self.last_z.items():
                    delta = (value-self.mean)/self.span
                    if abs(delta) > .01:
                        self.signs[ident] = -1 if delta < 0 else 1
            for ident, value in z.items():
                delta = (value-self.mean)/self.span
                sign = -1 if delta < -.01 else 1 if delta > .01 else 0
                if sign:
                    if ident in self.signs and sign != self.signs[ident]:
                        self.crossings[ident] += 1
                    self.signs[ident] = sign
        if t in SAMPLES:
            self.samples[t] = e
        self.previous = (t, e, q)
        self.last_z = dict(z)

    def result(self):
        if self.previous is None:
            return dict(version='outcome-1', adverse=False, signature=None)
        _, e, q = self.previous
        adverse = e >= self.threshold or self.outside or self.relapse
        growth = (self.samples.get(40000, 0) >= 2*max(self.samples.get(20000, 0), .001)
                  and self.samples.get(60000, 0) >= 2*self.samples.get(40000, 0))
        outcome = 'biased_consensus' if self.bias else 'inaccurate' if e >= self.threshold else 'transient'
        flags = dict(growth=growth, oscillation=max(self.crossings.values(), default=0) >= 4, outside=self.outside, relapse=self.relapse)
        signature = '|'.join([outcome, *[k for k, v in flags.items() if v]]) if adverse else None
        return dict(version='outcome-1', threshold=self.threshold, adverse=adverse, signature=signature,
                    outcome=outcome if adverse else 'accurate' if e <= .001 else 'intermediate',
                    precise=e <= .001, E=e, Q=q, **flags)
