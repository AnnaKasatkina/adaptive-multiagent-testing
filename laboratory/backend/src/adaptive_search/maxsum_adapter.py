"""Channel-allocation observations and bounded one-delay mutations for the shared search."""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict

from . import maxsum
from .fuzz_feedback.signals import Tolerance, maxsum_signal


def maxsum_profile(name="development"):
    if name == "development":
        return maxsum.profile("chorded", 6)
    if name in ("heldout", "holdout"):
        return maxsum.profile("chorded", 7)
    return maxsum.profile(name)


def _cost(config, channels):
    return (sum(row[c] for row, c in zip(config["unary"], channels)) +
            sum(weight for i, j, weight in config["edges"] if channels[i] == channels[j]))


def outcome(config, channels):
    total = _cost(config, channels)
    optimum = maxsum.oracle(config)["cost"]
    delta = total - optimum
    conflicts = sum(channels[i] == channels[j] for i, j, _ in config["edges"])
    best_neighbor = min((_cost(config, [*channels[:i], c, *channels[i+1:]])
                         for i, old in enumerate(channels) for c in maxsum.CHANNELS if c != old))
    signature = None
    if delta > 0:
        structure = "conflict" if conflicts else "preferences"
        landscape = "one-step-improvable" if best_neighbor < total else "one-step-minimum"
        signature = f"{structure}/{landscape}"
    return dict(signature=signature, severity=delta, cost=total, optimum=optimum,
                conflicts=conflicts, best_one_sensor_gain=max(0, total-best_neighbor))


class MaxSumProblem:
    def __init__(self, config):
        self.config = config

    def _annotate(self, run):
        run["outcome"] = outcome(self.config, run["summary"]["channels"])
        run["prefix_events"] = run["summary"]["prefix_events"]
        return run

    def simulate(self, seed, *, overrides=None, parent=None, cut=None):
        changes = dict(overrides or {})
        if parent is not None and cut is not None:
            for row in parent["trace"][:cut+1]:
                for decision in row["decisions"]:
                    key = decision["kind"], decision["entity"], decision["ordinal"]
                    if key in changes and changes.pop(key) != decision["value"]:
                        raise ValueError("Attempted to modify an assigned decision")
        return self._annotate(maxsum.simulate(self.config, seed, overrides=changes, parent=parent, cut=cut))

    def replay(self, run):
        return self._annotate(maxsum.replay(run))

    def validate(self, run):
        audit = maxsum.audit_transport(run)
        for row in run["trace"]:
            exact_cost = _cost(self.config, row["channels"])
            if exact_cost != row["metrics"]["cost"]:
                raise ValueError("Incorrect trajectory cost")
        verified = outcome(self.config, run["summary"]["channels"])
        return {**audit, "valid": verified == run["outcome"] and verified["severity"] == run["summary"]["gap"]}

    def pool(self, run):
        """Straddle a receiver activation with one admissible transport draw.

        With intervals keyed per sensor, each candidate leaves all activation
        times unchanged. FIFO can push several later deliveries on the same
        edge. At the inspected activation their latest vector is read from the
        parent, keeping other inputs fixed for the forecast.
        """
        updates, streams = defaultdict(list), defaultdict(list)
        for row in run["trace"][1:]:
            head, *sends = row["events"]
            if head["kind"] == "update":
                updates[head["agent"]].append(head)
            for send in sends:
                m = send["message"]
                streams[(m["sender"], m["receiver"])].append(m)
        result = []
        lo, hi = self.config["delay_ms"]
        for (source, receiver), messages in streams.items():
            target_updates = updates[receiver]
            update_times = [u["time_ms"] for u in target_updates]
            dues = [m["due_ms"] for m in messages]
            for i, m in enumerate(messages):
                sent = m["sent_ms"]
                watermark = dues[i-1] if i else 0
                earliest, latest = max(watermark, sent + lo), max(watermark, sent + hi)
                left = bisect_left(update_times, earliest + 1)
                right = bisect_left(update_times, latest + 1)
                for update in target_updates[left:right]:
                    at = update["time_ms"]
                    value = at - sent if dues[i] < at else at - 1 - sent
                    if not lo <= value <= hi or value == m["transport_ms"]:
                        continue
                    # Once raw due_i blocks the activation, every later FIFO
                    # arrival is also blocked. Once unblocked, scan until the
                    # next send whose unqueued arrival misses the activation.
                    before_i = bisect_left(dues, at) - 1
                    if value + sent >= at:
                        after_i = i-1
                    else:
                        after_i = i
                        while after_i+1 < len(messages):
                            following = messages[after_i+1]
                            if following["sent_ms"] + following["transport_ms"] >= at:
                                break
                            after_i += 1
                    if before_i == after_i:
                        continue
                    old = messages[before_i]["values"] if before_i >= 0 else [0, 0, 0]
                    new = messages[after_i]["values"] if after_i >= 0 else [0, 0, 0]
                    scores = update["scores"]
                    # The reusable signal uses maximized utilities.
                    base = {str(c): old[c] - scores[c] for c in maxsum.CHANNELS}
                    signal = maxsum_signal(base, {str(c): -old[c] for c in maxsum.CHANNELS},
                                           {str(c): -new[c] for c in maxsum.CHANNELS},
                                           contribution_bound=max(*old, *new),
                                           tolerance=Tolerance(scale=max(1, *scores)))
                    new_scores = [scores[c] + new[c] - old[c] for c in maxsum.CHANNELS]
                    baseline = 1 / (1 + abs(dues[i] - at))
                    best_change = abs(min(scores) - min(new_scores))
                    legacy_bin = "zero" if best_change == 0 else "one" if best_change == 1 else "many"
                    result.append(dict(
                        key=("delay", f"{source}>{receiver}", i), value=value,
                        feature="/".join(map(str, signal.token or ("neutral",))),
                        legacy_feature=f"{'flip' if signal.choice_changed else 'same'}/{legacy_bin}",
                        baseline_score=baseline,
                        feedback_score=(4 * int(signal.choice_changed) + int(signal.robust_flip)
                                        + signal.perturbation_span / (1 + max(1,*scores))),
                        agent=receiver, update_ms=at, old_channel=int(signal.old_choice),
                        new_channel=int(signal.new_choice), old_scores=scores, new_scores=new_scores,
                        old_margin=signal.old_margin, new_margin=signal.new_margin,
                        old_message=messages[before_i]["id"] if before_i >= 0 else None,
                        new_message=messages[after_i]["id"] if after_i >= 0 else None,
                        source_message=m["id"], original_delay=m["transport_ms"],
                        predicted_flip=signal.choice_changed, span=signal.perturbation_span))
        return result
