from itertools import combinations

from .observation import distance


class ArchivePolicy:
    def __init__(self, method, capacity=32):
        self.method, self.capacity = method, capacity

    def admit(self, ids, rows, candidate, rng):
        ids = list(ids)
        row = rows[candidate-1]
        if row['status'] != 'completed':
            return ids
        method = self.method
        if method == 'independent':
            return []
        if method == 'reservoir':
            if len(ids) < self.capacity:
                return ids + [candidate]
            slot = rng.randrange(sum(r['status']=='completed' for r in rows))
            if slot < self.capacity:
                ids[slot] = candidate
            return sorted(ids)
        if method == 'elite':
            return sorted(ids+[candidate], key=lambda i: (-rows[i-1]['score'], i))[:self.capacity]
        previous = [r['descriptor'] for r in rows[:-1] if r['status']=='completed']
        novelty = min((distance(row['descriptor'], d) for d in previous), default=1.)
        row['novelty'] = novelty
        if not novelty or method.endswith('_bad') and not row['outcome']['adverse']:
            return ids
        ids.append(candidate)
        if len(ids) > self.capacity:
            a, b = min(combinations(ids, 2), key=lambda p: (distance(rows[p[0]-1]['descriptor'], rows[p[1]-1]['descriptor']), p))
            remove = min((a,b), key=lambda i: (rows[i-1]['novelty'], -i))
            ids.remove(remove)
        return sorted(ids)

    def parent(self, ids, rows, rng):
        if not ids:
            return None
        if self.method == 'elite':
            return min(ids, key=lambda i: (-rows[i-1]['score'], i)) if rng.random() < .5 else rng.choice(ids)
        if self.method == 'reservoir' or self.method.endswith('_uniform'):
            return rng.choice(ids)
        return rng.choices(ids, weights=[rows[i-1]['novelty'] for i in ids], k=1)[0]
