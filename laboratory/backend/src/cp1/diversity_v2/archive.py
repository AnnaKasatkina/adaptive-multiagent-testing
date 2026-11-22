from itertools import combinations
from ..diversity.archive import ArchivePolicy as PreviousPolicy
from .observation import distance


class ArchivePolicy:
    def __init__(self, method, capacity=32):
        self.method, self.capacity = method, capacity
        self.control = PreviousPolicy(method, capacity)
        self.distances = {}

    def admit(self, ids, rows, candidate, rng, uses=None):
        if self.method in ('independent', 'reservoir', 'elite'):
            result = self.control.admit(ids, rows, candidate, rng)
            rows[-1]['archive_reason'] = 'control_policy'
            return result
        ids, uses = list(ids), uses or {}
        row = rows[candidate-1]
        if row['status'] != 'completed':
            row['archive_reason'] = 'not_completed'
            return ids
        def between(a, b):
            key = tuple(sorted((a, b)))
            if key not in self.distances:
                self.distances[key] = distance(rows[a-1]['descriptor'], rows[b-1]['descriptor'])
            return self.distances[key]
        previous = [i+1 for i,r in enumerate(rows[:-1]) if r['status'] == 'completed']
        row['novelty'] = min((between(candidate, i) for i in previous), default=1.)
        if not row['novelty']:
            row['archive_reason'] = 'seen_description'
            return ids
        ids.append(candidate)
        row['archive_reason'] = 'new_description'
        if len(ids) > self.capacity:
            pair = min(combinations(ids, 2), key=lambda p: (between(*p), p))
            remove = max(pair, key=lambda i: (uses.get(str(i), 0), i))
            ids.remove(remove)
            row['archive_removed'] = remove
            row['archive_reason'] = 'closest_pair_eviction'
        return sorted(ids)

    def parent(self, ids, rows, rng):
        if not ids:
            return None
        if self.method == 'elite':
            return self.control.parent(ids, rows, rng)
        return rng.choice(ids)
