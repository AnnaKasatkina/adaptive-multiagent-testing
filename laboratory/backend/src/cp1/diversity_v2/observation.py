from bisect import bisect_right
from collections import Counter
from statistics import median

from ..diversity.observation import (
    BehaviorDescriptor as PreviousDescriptor, OutcomeAssessment, category,
    distance as previous_distance, BATCH, REPEAT, AGE, NUMERIC,
)


class BehaviorDescriptor:
    """Sparse histogram of local updates; no object identities or outcome labels."""
    def __init__(self, configuration, kind):
        if kind not in ('numeric', 'local'):
            raise ValueError(kind)
        self.kind = kind
        self.numeric = PreviousDescriptor(configuration, 'numeric') if kind == 'numeric' else None
        initial = [n['initial'] for n in configuration['nodes']]
        self.span = max(initial) - min(initial)
        if not self.span:
            raise ValueError('Начальное согласие исключено из нормированного поиска')
        self.mean = sum(initial) / len(initial)
        self.values = {n['id']: n['initial'] for n in configuration['nodes']}
        self.degrees = Counter(e['target'] for e in configuration['edges'])
        self.counts = Counter()

    def accept(self, events, t, values=None):
        if self.numeric:
            return
        for event in events:
            if event['kind'] != 'update':
                continue
            ident, used = event['agent'], event['used']
            before, after = self.values[ident], values[ident]
            direction = ('cross' if (before-self.mean)*(after-self.mean) < 0 else
                         'toward' if abs(after-self.mean) < abs(before-self.mean) else
                         'away' if abs(after-self.mean) > abs(before-self.mean) else 'unchanged')
            batch = len(used) / self.degrees[ident]
            repeat = (len(used)-len({m['sender'] for m in used}))/len(used) if used else None
            age = median((t-m['sent_ms'])/1000 for m in used) if used else None
            window = min(4, bisect_right((5000, 10000, 20000, 40000), t))
            token = (window, category(batch, BATCH), category(repeat, REPEAT),
                     category(age, AGE), category(abs(after-before)/self.span, NUMERIC), direction)
            self.counts[token] += 1
            self.values[ident] = after

    def layer(self, t, metrics):
        if self.numeric:
            self.numeric.layer(t, metrics)

    def result(self):
        if self.numeric:
            return {**self.numeric.result(), 'version': 'behavior-2'}
        return dict(version='behavior-2', kind='local', histogram=[
            [list(token), 1 if count == 1 else 2 if count <= 3 else 3 if count <= 7 else 4]
            for token, count in sorted(self.counts.items())])


def distance(a, b):
    if (a['version'], a['kind']) != (b['version'], b['kind']):
        raise ValueError('Несовместимые описания')
    if a['kind'] == 'numeric':
        return previous_distance({**a, 'version': 'behavior-1'}, {**b, 'version': 'behavior-1'})
    x = {tuple(k): v for k, v in a['histogram']}
    y = {tuple(k): v for k, v in b['histogram']}
    keys = x.keys() | y.keys()
    return sum(abs(x.get(k, 0)-y.get(k, 0))/4 for k in keys)/len(keys) if keys else 0.
