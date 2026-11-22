import copy
import itertools
import math

from ..adaptive.spaces import context as old_context, regular, edge_ids, graph_with_edges
from ..event_models import EventConfiguration


def context(graph='bridge', algorithm='homework', regime='window', n=6, heldout=False):
    c = old_context(graph, algorithm, regime, n)
    c['horizon_ms'] = 60000
    if heldout:
        for i,node in enumerate(c['nodes']):
            node['initial'] = [0,3,10,7,2][i%5]
    return c


def domains(c, variant):
    result = dict(agent=[n['id'] for n in c['nodes']], edge=edge_ids(c))
    if variant=='individual':
        result.update(interval=(1000,4000), delay=(50,2000))
    else:
        result.update(mode=['agent','link','joint'], start=[5000,20000,40000], duration=[2000,10000], factor=[2,4])
    return result


def active(values, variant):
    if variant=='individual':
        return sorted(values)
    return sorted(k for k in values if k!='agent' or values['mode']!='link' if k!='edge' or values['mode']!='agent')


def random_values(c, rng, variant='individual'):
    return {k: rng.randint(*v) if isinstance(v,tuple) else rng.choice(v) for k,v in domains(c,variant).items()}


def mutate_values(c, values, rng, variant='individual'):
    v = dict(values)
    field = rng.choice(active(v,variant))
    domain = domains(c,variant)[field]
    if isinstance(domain,tuple):
        lo,hi = domain
        radius = rng.choice([1, math.ceil((hi-lo)*.01), math.ceil((hi-lo)*.1)])
        lower,upper = max(lo,v[field]-radius),min(hi,v[field]+radius)
        value = rng.randrange(lower,upper)
        v[field] = value + (value >= v[field])
    else:
        v[field] = rng.choice([x for x in domain if x!=v[field]])
    return v


def materialize(c, values, variant='individual'):
    result = regular(c)
    ds = domains(c,variant)
    for k,v in values.items():
        domain = ds[k]
        if not (domain[0]<=v<=domain[1] if isinstance(domain,tuple) else v in domain):
            raise ValueError(f'Параметр {k} вне умеренного профиля')
    if variant=='individual':
        next(n for n in result['nodes'] if n['id']==values['agent'])['overrides']['interval_ms']=values['interval']
        next(e for e in result['edges'] if f"{e['source']}>{e['target']}"==values['edge'])['overrides']['delay_ms']=values['delay']
    else:
        mode = values['mode']
        for kind in (['agent','link'] if mode=='joint' else [mode]):
            result['episodes'].append(dict(id=f'diversity-{kind}',kind=kind+'_slowdown',
                targets=[values['agent'] if kind=='agent' else values['edge']],
                start_ms=values['start'],duration_ms=values['duration'],factor=values['factor']))
    return EventConfiguration.model_validate(result).model_dump()


def graph_candidate(c,rng,directed,count,parent=False):
    ids=[n['id'] for n in c['nodes']]
    possible=set(itertools.permutations(ids,2) if directed else itertools.combinations(ids,2))
    current={(e['source'],e['target']) for e in c['edges'] if directed or e['source']<e['target']}
    protected={(e['source'],e['target']) if directed else tuple(sorted((e['source'],e['target']))) for e in c['edges'] if e['overrides']}
    for attempts in range(1,1001):
        if parent:
            removable=sorted(current-protected)
            if not removable:
                raise ValueError('Нет изменяемых рёбер без переопределения')
            chosen=current-{rng.choice(removable)}|{rng.choice(sorted(possible-current))}
        else:
            chosen=set(rng.sample(sorted(possible),count))
        pairs=chosen if directed else chosen|{(b,a) for a,b in chosen}
        try:
            return graph_with_edges(c,pairs,c),attempts
        except ValueError:
            continue
    raise ValueError('1000 недопустимых предложений графа')
