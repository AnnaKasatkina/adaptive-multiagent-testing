"""The sole RAPS transition, executed locally and inside SPADE behaviours."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from math import fsum


@dataclass(frozen=True)
class Message:
    id: str
    sender: str
    receiver: str
    stamp: int
    sent: int
    phi_x: float
    phi_y: float
    due: int = 0


@dataclass
class AgentState:
    x: float
    y: float = 1.0
    phi_x: float = 0.0
    phi_y: float = 0.0
    count: int = 0
    rho: dict = field(default_factory=dict)

    def snapshot(self):
        return {**asdict(self), "z": self.x / self.y}


def initial_states(config):
    return {
        n.id: AgentState(
            n.initial,
            rho={
                e.source: {"x": 0.0, "y": 0.0, "stamp": 0}
                for e in config.edges
                if e.target == n.id
            },
        )
        for n in config.nodes
    }


def activate(
    state: AgentState,
    ident: str,
    neighbours: list[str],
    inbox: list[Message],
    tick: int,
):
    """Split and emit BEFORE processing the latest cumulative values in the inbox."""
    state.count += 1
    divisor = len(neighbours) + 1
    state.x /= divisor
    state.y /= divisor
    state.phi_x += state.x
    state.phi_y += state.y
    outgoing = [
        Message(
            f"{ident}:{state.count}:{v}", ident, v, tick, tick, state.phi_x, state.phi_y
        )
        for v in neighbours
    ]
    latest = {}
    for message in sorted(inbox, key=lambda m: (m.sender, m.stamp, m.id)):
        if message.sender not in state.rho:
            raise ValueError("Сообщение от неизвестного входящего соседа")
        if message.stamp > state.rho[message.sender]["stamp"]:
            latest[message.sender] = message
    dx, dy = [], []
    for sender, message in sorted(latest.items()):
        old = state.rho[sender]
        dx.append(message.phi_x - old["x"])
        dy.append(message.phi_y - old["y"])
        state.rho[sender] = {
            "x": message.phi_x,
            "y": message.phi_y,
            "stamp": message.stamp,
        }
    state.x += fsum(dx)
    state.y += fsum(dy)
    return outgoing, len(latest)


class CoreRuntime:
    name = "core"

    async def start(self, config):
        from .algorithms import rule

        self.rule = rule(config)
        self.states = self.rule.initial_states(config)
        self.neighbours = {
            n.id: sorted(e.target for e in config.edges if e.source == n.id)
            for n in config.nodes
        }

    async def activate(self, ident, inbox, tick):
        return self.rule.activate(
            self.states[ident], ident, self.neighbours[ident], inbox, tick
        )

    def snapshot(self):
        return {k: s.snapshot() for k, s in sorted(self.states.items())}

    async def close(self):
        pass
