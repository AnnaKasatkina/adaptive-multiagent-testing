"""Controlled rule adapter for the pinned, unmodified upstream agent.py.

Preserved: weight=1/7 (including the ignored agent.weight defect), send before
update, no per-sender deduplication or mass correction. The original handler
discards values under SPADE 4.1.4; the read-compatible variant counts every
queued value and preserves the mathematical rule, including its defects.
Wall-clock receive windows / start_delay / local stop conditions are replaced by
the laboratory's explicit activation and delivery schedule, not silently emulated.
"""

from dataclasses import dataclass, asdict

COMMIT = "a776db124ace86059a77bc640c5e7a606b97bbb5"
MODEL_VERSION = "homework-fixed-weight-1.tick-1.parameters-1"


@dataclass(frozen=True)
class ValueMessage:
    id: str
    sender: str
    receiver: str
    stamp: int
    sent: int
    value: float
    due: int = 0


@dataclass
class HomeworkState:
    value: float
    count: int = 0
    received: int = 0
    last_batch: int = 0
    max_batch: int = 0
    accepts_values: bool = True
    discarded: int = 0

    def snapshot(self):
        return {**asdict(self), "z": self.value, "weight": 1 / 7}


def initial_states(config):
    return {
        n.id: HomeworkState(
            n.initial, accepts_values=config.algorithm != "homework-original"
        )
        for n in config.nodes
    }


def activate(state, ident, neighbours, inbox, tick):
    state.count += 1
    outgoing = [
        ValueMessage(f"{ident}:{state.count}:{v}", ident, v, tick, tick, state.value)
        for v in neighbours
    ]
    # agent.py checks msg.metadata before get_metadata(). SPADE 4.1.4 has only
    # _metadata/get_metadata, so the original catches AttributeError and drops
    # every value. The rule variant makes ONLY that read compatible.
    if not state.accepts_values:
        state.discarded += len(inbox)
    received_vals = [message.value for message in inbox] if state.accepts_values else []
    deg = len(received_vals)
    if deg > 0:
        sum_neighbors = sum(received_vals)
        state.value = state.value + (1 / 7) * (sum_neighbors - deg * state.value)
    state.received += deg
    state.last_batch = deg
    state.max_batch = max(state.max_batch, deg)
    return outgoing, deg
