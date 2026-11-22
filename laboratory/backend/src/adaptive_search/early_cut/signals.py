"""Prefix-only signals for completed event layers. No simulator dependencies."""
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Sequence

WINDOW_MS = 800
TARGET = .01


def excursion(value, lower, upper):
    return max(0., lower - value, value - upper) / (upper - lower)


def batch_update(value, messages):
    return value + (sum(messages) - len(messages) * value) / 7


@dataclass(frozen=True)
class LocalSignal:
    """Prepare fixed graph/timing parameters once per configuration."""

    configuration: dict

    def __post_init__(self):
        c = self.configuration
        if c.get('algorithm') != 'homework':
            raise ValueError('Only corrected homework is supported')
        values = [float(n['initial']) for n in c['nodes']]
        if min(values) >= max(values):
            raise ValueError('Positive initial range required')
        incoming = defaultdict(list)
        for edge in c['edges']:
            p = {**c['defaults'], **edge.get('overrides', {})}
            incoming[edge['target']].append((edge['source'], p['delay_ms'], p['delay_jitter_ms']))
        object.__setattr__(self, 'initial_range', (min(values), max(values)))
        object.__setattr__(self, 'incoming', incoming)
        object.__setattr__(self, 'horizon', c['horizon_ms'])
        minimum_intervals = {}
        for node in c['nodes']:
            p = {**c['defaults'], **node.get('overrides', {})}
            minimum_intervals[node['id']] = p['interval_ms'] - p['interval_jitter_ms']
        object.__setattr__(self, 'minimum_intervals', minimum_intervals)
        object.__setattr__(self, 'explicit_updates', (c.get('schedule') or {}).get('updates', {}))
        delay_explicit = (c.get('schedule') or {}).get('delays', {})
        object.__setattr__(self, 'explicit_delays', delay_explicit)
        episodes = defaultdict(list)
        for ep in c.get('episodes', []):
            if ep['kind'] == 'link_slowdown':
                for edge_id in ep['targets']:
                    episodes[edge_id].append(ep)
        object.__setattr__(self, 'episodes', episodes)

    def observe(self, state, *, threshold=TARGET):
        """Requires a full state at end of a layer. Returns one record per next update.

        Already scheduled arrivals are immutable. Optional messages are future sends
        with some valid delays arriving before the update and others arriving too late.
        The interval estimates the known inputs and the next scheduled sends only.
        """
        now = state['time_ms']
        if not isfinite(threshold) or threshold < 0:
            raise ValueError('Threshold must be finite and nonnegative')
        lower, upper = self.initial_range
        agents = state['agents']
        timers = state['timers']
        if any(timer['time_ms'] <= now for timer in timers.values()):
            raise ValueError('A partially processed time layer cannot be observed')
        pending_by_receiver = defaultdict(list)
        for message in state['pending'].values():
            pending_by_receiver[message['receiver']].append(message)
        nearest_send = {timer['entity']: timer['time_ms'] for timer in timers.values()
                        if timer['kind'] == 'cycle_start'}
        possible = []
        for agent, a in agents.items():
            update_at = a['next_update_ms']
            if not now < update_at <= self.horizon:
                continue
            # In waiting phase the inbox rolls into the next batch at cycle_start.
            already = a['batch'] if a['phase'] == 'collecting' else a['inbox']
            pending = [m for m in pending_by_receiver.get(agent, ()) if m['due_ms'] < update_at]
            known = [m['value'] for m in (*already, *pending)]
            value = a['z']
            guaranteed, optional, unmodeled = [], [], []
            for sender, base, jitter in self.incoming[agent]:
                send_at = nearest_send.get(sender)
                edge = f'{sender}>{agent}'
                ordinal = state['counters'].get(f'delay:{edge}', 0)
                last_due = state['last_due'].get(edge, 0)
                source_update = agents[sender]['next_update_ms']
                explicit_updates = self.explicit_updates.get(sender)
                if explicit_updates:
                    next_update = next((time for time in explicit_updates if time > source_update), None)
                else:
                    # Slowdown can only increase this lower bound.
                    next_update = source_update + self.minimum_intervals[sender]
                if next_update is not None:
                    next_send = next_update - WINDOW_MS
                    # Admissible explicit delays are >=1, and episodes only increase delays.
                    min_delay = 1 if edge in self.explicit_delays else base-jitter
                    earliest_extra = max(last_due, next_send+min_delay)
                    if earliest_extra < update_at:
                        unmodeled.append(dict(edge=edge, earliest_send_ms=next_send, earliest_due_ms=earliest_extra))
                if send_at is None or send_at >= update_at:
                    continue
                if edge in self.explicit_delays:
                    explicit = self.explicit_delays[edge][ordinal]
                    lo = hi = explicit
                else:
                    factor = next((ep['factor'] for ep in self.episodes[edge]
                                   if ep['start_ms'] <= send_at < ep['start_ms'] + ep['duration_ms']), 1)
                    lo, hi = (base - jitter) * factor, (base + jitter) * factor
                earliest = max(last_due, send_at + lo)
                latest = max(last_due, send_at + hi)
                if earliest >= update_at:
                    continue
                # An already scheduled next cycle start precedes the sender's
                # next update. Its payload is the current, known sender value.
                if send_at >= source_update:
                    raise ValueError('Scheduled cycle must precede the sender update')
                payload = agents[sender]['z']
                decision = dict(edge=edge, ordinal=ordinal, send_ms=send_at,
                                decision_lo_ms=lo, decision_hi_ms=hi,
                                earliest_due_ms=earliest, latest_due_ms=latest, value=payload)
                if latest < update_at:
                    guaranteed.append(decision)
                else:
                    optional.append(dict(**decision, last_on_time_delay_ms=update_at-send_at-1,
                                         effect=(payload-value)/7))
            batch_projection = batch_update(value, [m['value'] for m in already])
            committed_projection = batch_update(value, known)
            fixed_projection = batch_update(value, [*known, *(m['value'] for m in guaranteed)])
            lower_projection = fixed_projection + sum(min(0., m['effect']) for m in optional)
            upper_projection = fixed_projection + sum(max(0., m['effect']) for m in optional)
            # Any optional subset is a possible assignment of these next delays.
            # Require an explicit safe subset and a bad endpoint, not an interior
            # point that may be unreachable for discrete message membership.
            possibilities = [fixed_projection, lower_projection, upper_projection]
            if optional:
                possibilities.extend(fixed_projection + m['effect'] for m in optional)
            projected_d = max(excursion(x, lower, upper) for x in possibilities[:3])
            actionable = bool(optional) and projected_d > threshold and any(
                excursion(x, lower, upper) <= threshold for x in possibilities)
            possible.append(dict(agent=agent, time_ms=now, update_ms=update_at,
                                 lead_ms=update_at-now, batch_projection=batch_projection,
                                 committed_projection=committed_projection, fixed_projection=fixed_projection,
                                 projection_range=[lower_projection, upper_projection],
                                 projected_D=projected_d, warning=projected_d > threshold,
                                 batch_count=len(already), committed_count=len(known),
                                 committed_pending_ids=[m['id'] for m in pending],
                                 guaranteed_future_count=len(guaranteed),
                                 guaranteed_future_sends=guaranteed,
                                 intervention_opportunities=optional,
                                 unmodeled_future_sends=unmodeled,
                                 prediction_closed=not unmodeled,
                                 candidate_actionable=actionable,
                                 actionable=actionable and not unmodeled))
        return possible


def local_warning(configuration: dict, state: dict, *, threshold=TARGET) -> list[dict]:
    return LocalSignal(configuration).observe(state, threshold=threshold)


@dataclass(frozen=True)
class PossibleMessage:
    """New minus currently accepted contribution, and admissible arrival bounds."""
    delta: Sequence[float]
    earliest_due_ms: int
    latest_due_ms: int
    decision_pending: bool = True


def maxsum_warning(scores: Sequence[float], possible_message: PossibleMessage | None,
                   *, now_ms: int, decision_ms: int, tie_atol: float = 0., maximize: bool = False) -> dict:
    """Normalized min-cost by default. A decision precedes equal-time arrivals."""
    if len(scores) < 2 or not all(isfinite(v) for v in scores):
        raise ValueError('At least two finite channel scores required')
    if not isfinite(tie_atol) or tie_atol < 0:
        raise ValueError('Invalid tie tolerance')
    if decision_ms <= now_ms:
        raise ValueError('Decision must lie after the completed layer')

    def choose(values):
        best = max(values) if maximize else min(values)
        return next(i for i, value in enumerate(values) if abs(best-value) <= tie_atol)

    best, second, *_ = sorted(scores, reverse=maximize)
    winner = choose(scores)
    gap = abs(best-second)
    if not isfinite(gap):
        raise ValueError('Nonfinite channel gap')
    result = dict(gap=gap, winner=winner, winner_if_received=None,
                  could_arrive_before=False, timing_can_change_membership=False,
                  winner_changes_if_received=False, actionable=False)
    if possible_message is None:
        return result
    message = possible_message
    if len(message.delta) != len(scores) or not all(isfinite(v) for v in message.delta):
        raise ValueError('Message delta must match finite channel scores')
    if now_ms >= message.earliest_due_ms or message.earliest_due_ms > message.latest_due_ms:
        raise ValueError('Message arrival bounds must lie after the completed layer')
    if not message.decision_pending and message.earliest_due_ms != message.latest_due_ms:
        raise ValueError('An already scheduled delivery requires an exact arrival time')
    arrived = [s+d for s,d in zip(scores,message.delta)]
    if not all(isfinite(x) for x in arrived):
        raise ValueError('Nonfinite updated channel score')
    new_winner = choose(arrived)
    could_arrive = message.earliest_due_ms < decision_ms
    timing = message.decision_pending and message.earliest_due_ms < decision_ms <= message.latest_due_ms
    result.update(winner_if_received=new_winner, could_arrive_before=could_arrive,
                  timing_can_change_membership=timing, winner_changes_if_received=new_winner!=winner,
                  actionable=timing and new_winner!=winner)
    return result
