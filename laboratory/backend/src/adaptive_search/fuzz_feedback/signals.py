"""Pure local signals for legal point mutations. No scheduler or oracle imports."""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isfinite


Token = tuple
IMPACT_THRESHOLDS = (1e-3, 1e-2, 1e-1)


@dataclass(frozen=True)
class Tolerance:
    scale: float
    absolute: float = 1e-12
    relative: float = 1e-10

    def __post_init__(self):
        if (not all(map(isfinite, (self.scale, self.absolute, self.relative, self.tau)))
                or self.scale <= 0 or min(self.absolute, self.relative) < 0):
            raise ValueError('Require positive finite scale and nonnegative finite tolerances')

    @property
    def tau(self) -> float:
        return self.absolute + self.relative * self.scale

    def sign(self, value: float) -> int:
        return int(value > self.tau) - int(value < -self.tau)

    def impact_bin(self, value: float) -> int:
        if not isfinite(value):
            raise ValueError('Nonfinite effect')
        magnitude = abs(value)
        if magnitude <= self.tau:
            return 0
        # Values within tau above a boundary stay in the lower bin.
        return 1 + sum(magnitude - self.tau > self.scale * cut for cut in IMPACT_THRESHOLDS)


def local_update_token(sender_count: int, before: float, after: float, *,
                       mean: float, tolerance: Tolerance) -> Token:
    """A batch-count and dynamics category usable for historical one-window data."""
    if sender_count < 0 or not all(map(isfinite, (before, after, mean))):
        raise ValueError('Invalid local update')
    delta = after - before
    sign = tolerance.sign(delta)
    if sign == 0:
        return (sender_count, 0, 0, 'neutral')
    a, b = before - mean, after - mean
    move = tolerance.sign(abs(a) - abs(b))
    direction = ('cross' if tolerance.sign(a) * tolerance.sign(b) < 0 else
                 {1: 'toward', -1: 'away', 0: 'unchanged'}[move])
    return (sender_count, sign, tolerance.impact_bin(delta), direction)


@dataclass(frozen=True)
class Message:
    id: str
    sender: str
    value: float


@dataclass(frozen=True)
class LocalWindow:
    time_ms: int
    before: float
    batch: tuple[Message, ...]


def local_increment(before: float, batch: Sequence[Message]) -> float:
    """Algebraic homework increment using a sum of centered contributions."""
    if not isfinite(before) or any(not isfinite(message.value) for message in batch):
        raise ValueError('Nonfinite local input')
    return fsum(message.value - before for message in batch) / 7


def usage_index(windows: Sequence[LocalWindow], due_ms: int) -> int:
    """Receiver consumes at first update strictly after due. See EventEngine priority."""
    index = bisect_right(windows, due_ms, key=lambda window: window.time_ms)
    if index == len(windows):
        raise ValueError('Delivery has no observed usage window')
    return index


@dataclass(frozen=True)
class WindowEffect:
    time_ms: int
    old_before: float
    new_before: float
    old_batch_ids: tuple[str, ...]
    new_batch_ids: tuple[str, ...]
    old_sender_count: int
    new_sender_count: int
    old_increment: float
    new_increment: float
    old_message_contribution: float
    new_message_contribution: float
    direct_effect: float
    increment_effect: float
    state_effect: float
    old_token: Token
    new_token: Token


@dataclass(frozen=True)
class LocalSignal:
    source_ms: int
    destination_ms: int
    transferred: bool
    effects: tuple[WindowEffect, ...]  # source, destination
    peak_state_effect: float
    impact_bin: int
    token: Token | None


def _local_signal(source: WindowEffect, destination: WindowEffect, peak: float,
                  tolerance: Tolerance) -> LocalSignal:
    impact = tolerance.impact_bin(peak)
    pair = source, destination
    token = None
    if impact:
        direction = 'later' if destination.time_ms > source.time_ms else 'earlier'
        token = ('local-transfer-v1', direction, *(
            (effect.old_token, effect.new_token, tolerance.sign(effect.state_effect),
             tolerance.impact_bin(effect.state_effect)) for effect in pair
        ))
    return LocalSignal(source.time_ms, destination.time_ms, True, pair, peak, impact, token)


def predict_local_transfer(windows: Sequence[LocalWindow], message: Message, *,
                           old_due_ms: int, new_due_ms: int, mean: float,
                           tolerance: Tolerance) -> LocalSignal:
    """Forecast the receiver recurrence with all other parent batch payloads fixed.

    Pass ordered, consecutive receiver updates covering both FIFO-adjusted dues.
    The caller is responsible for legal scheduling. A missing window is unscorable.
    """
    times = [window.time_ms for window in windows]
    if not times or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('Receiver windows must be nonempty and strictly ordered')
    source = usage_index(windows, old_due_ms)
    destination = usage_index(windows, new_due_ms)
    occurrences = [(i, item) for i, window in enumerate(windows) for item in window.batch
                   if item.id == message.id]
    if occurrences != [(source, message)]:
        raise ValueError('Message must occur exactly once in its original usage window')
    if source == destination:
        return LocalSignal(times[source], times[destination], False, (), 0.0, 0, None)

    displacement = 0.0
    peak = 0.0
    effects = {}
    for i in range(min(source, destination), max(source, destination) + 1):
        window = windows[i]
        old_before, old_batch = window.before, window.batch
        new_before = old_before + displacement
        new_batch = old_batch
        if i == source:
            new_batch = tuple(item for item in old_batch if item.id != message.id)
        elif i == destination:
            new_batch = (*old_batch, message)

        # If x' = x + d, the modified recurrence subtracts n' * d / 7.
        # This avoids subtracting two nearly equal full post-update values.
        old_increment = local_increment(old_before, old_batch)
        new_increment = local_increment(new_before, new_batch)
        old_contribution = (message.value - old_before) / 7 if i == source else 0.0
        new_contribution = (message.value - new_before) / 7 if i == destination else 0.0
        direct = ((message.value - old_before) / 7 if i == destination else
                  -old_contribution)
        increment_effect = fsum((direct, -len(new_batch) * displacement / 7))
        displacement = fsum((displacement, increment_effect))
        peak = max(peak, abs(displacement))
        if i in (source, destination):
            old_count = sum(item.sender == message.sender for item in old_batch)
            new_count = sum(item.sender == message.sender for item in new_batch)
            effects[i] = WindowEffect(
                window.time_ms, old_before, new_before,
                tuple(item.id for item in old_batch), tuple(item.id for item in new_batch),
                old_count, new_count, old_increment, new_increment, old_contribution,
                new_contribution, direct, increment_effect, displacement,
                local_update_token(old_count, old_before, old_before + old_increment,
                                   mean=mean, tolerance=tolerance),
                local_update_token(new_count, new_before, new_before + new_increment,
                                   mean=mean, tolerance=tolerance),
            )
    return _local_signal(effects[source], effects[destination], peak, tolerance)


def observe_local_transfer(old: Sequence[LocalWindow], new: Sequence[LocalWindow],
                           old_afters: Sequence[float], new_afters: Sequence[float], *,
                           message_id: str, mean: float, tolerance: Tolerance) -> LocalSignal:
    """Compare actual completed traces with aligned receiver clocks.

    Afters are observed receiver states, not values recomputed from the batches.
    Use the returned token when adding an observed transfer to the archive.
    """
    if (len(old) != len(new) or len(old_afters) != len(old) or len(new_afters) != len(old)
            or [w.time_ms for w in old] != [w.time_ms for w in new]):
        raise ValueError('Observed windows must align by receiver update time')

    def find(windows):
        hits = [(i, item) for i, window in enumerate(windows) for item in window.batch
                if item.id == message_id]
        if len(hits) != 1:
            raise ValueError('Message must be used exactly once in each observed trace')
        return hits[0]

    source, old_message = find(old)
    destination, new_message = find(new)
    if old_message.sender != new_message.sender:
        raise ValueError('Message sender differs between traces')
    if source == destination:
        return LocalSignal(old[source].time_ms, old[source].time_ms, False, (), 0.0, 0, None)

    effects = []
    for i in (source, destination):
        a, b = old[i], new[i]
        old_count = sum(item.sender == old_message.sender for item in a.batch)
        new_count = sum(item.sender == old_message.sender for item in b.batch)
        old_increment = old_afters[i] - a.before
        new_increment = new_afters[i] - b.before
        old_contribution = (old_message.value - a.before) / 7 if i == source else 0.0
        new_contribution = (new_message.value - b.before) / 7 if i == destination else 0.0
        direct = (new_message.value - a.before) / 7 if i == destination else -old_contribution
        effects.append(WindowEffect(
            a.time_ms, a.before, b.before, tuple(item.id for item in a.batch),
            tuple(item.id for item in b.batch), old_count, new_count, old_increment,
            new_increment, old_contribution, new_contribution, direct,
            new_increment - old_increment, new_afters[i] - old_afters[i],
            local_update_token(old_count, a.before, old_afters[i], mean=mean, tolerance=tolerance),
            local_update_token(new_count, b.before, new_afters[i], mean=mean, tolerance=tolerance),
        ))
    peak = max(abs(new_afters[i] - old_afters[i])
               for i in range(min(source, destination), max(source, destination) + 1))
    return _local_signal(*effects, peak, tolerance)


@dataclass(frozen=True)
class MaxSumSignal:
    old_relative_scores: tuple[tuple[str, float], ...]
    new_relative_scores: tuple[tuple[str, float], ...]
    old_choice: str
    new_choice: str
    old_tolerant_choice: str
    new_tolerant_choice: str
    old_near_best: tuple[str, ...]
    new_near_best: tuple[str, ...]
    old_margin: float
    new_margin: float
    choice_changed: bool
    robust_flip: bool
    perturbation_span: float
    stable_certificate: bool
    impact_bin: int
    token: Token | None


def maxsum_signal(base_scores: Mapping[str, float], old_contribution: Mapping[str, float],
                  new_contribution: Mapping[str, float], *, contribution_bound: float,
                  tolerance: Tolerance) -> MaxSumSignal:
    """Compare scores at one decision with its effective incoming vector replaced.

    All mappings have identical string options (at least two). Contributions are
    bounded in supplied coordinates by |c[a]| <= contribution_bound. Use a zero
    vector only when the changed message truly had no effective contribution.
    """
    if (len(base_scores) < 2 or set(base_scores) != set(old_contribution)
            or set(base_scores) != set(new_contribution)
            or any(type(k) is not str for k in base_scores)):
        raise ValueError('Require identical domains of at least two string options')
    options = tuple(sorted(base_scores))
    if not isfinite(contribution_bound) or contribution_bound < 0:
        raise ValueError('Require a finite nonnegative contribution bound')
    if any(not isfinite(x) for x in base_scores.values()):
        raise ValueError('Nonfinite base score')
    if any(not isfinite(x) or abs(x) > contribution_bound
           for vector in (old_contribution, new_contribution) for x in vector.values()):
        raise ValueError('Contribution outside its finite bound')

    anchor = options[0]

    def decision(vector):
        scores = {a: fsum((base_scores[a], -base_scores[anchor],
                          vector[a], -vector[anchor])) for a in options}
        order = sorted(options, key=lambda a: (-scores[a], a))
        best, second = order[:2]
        near = tuple(a for a in options if scores[best] - scores[a] <= tolerance.tau)
        return tuple(scores.items()), best, near, scores[best] - scores[second]

    old_scores, old_choice, old_near, old_margin = decision(old_contribution)
    new_scores, new_choice, new_near, new_margin = decision(new_contribution)
    if not all(map(isfinite, (old_margin, new_margin))):
        raise ValueError('Nonfinite score margin')
    shifts = [fsum((new_contribution[a], -old_contribution[a],
                    -new_contribution[anchor], old_contribution[anchor])) for a in options]
    span = max(shifts) - min(shifts)
    impact = tolerance.impact_bin(span)
    flip = bool(impact and old_choice != new_choice
                and len(old_near) == len(new_near) == 1)
    certificate = old_margin - span > tolerance.tau
    token = None
    if impact:
        margin_change = tolerance.sign(new_margin - old_margin)
        token = ('maxsum-v1', 'flip' if flip else 'tie' if max(len(old_near), len(new_near)) > 1 else 'same',
                 len(old_near), len(new_near), margin_change, impact)
    return MaxSumSignal(old_scores, new_scores, old_choice, new_choice, old_near[0], new_near[0], old_near,
                        new_near, old_margin, new_margin, old_choice != new_choice,
                        flip, span, certificate, impact, token)


def selection_key(signal: LocalSignal | MaxSumSignal, archive: Mapping[Token, int],
                  pool_index: int) -> tuple:
    """Smaller sorts first. Archive stores completed observations, excluding None."""
    if signal.token is None:
        return (1, 1, 0, 0, pool_index)
    frequency = archive.get(signal.token, 0)
    return (0, frequency != 0, -signal.impact_bin, frequency, pool_index)
