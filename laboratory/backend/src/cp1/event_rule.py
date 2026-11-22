"""Local event handlers, shared by direct and SPADE runtimes."""

from copy import deepcopy
from dataclasses import asdict
from math import isfinite
from . import algorithm as raps


class EventState:
    def __init__(self, value):
        self.data = value

    def snapshot(self):
        return deepcopy(self.data)


class EventRule:
    def __init__(self, config):
        self.config = config

    def initial_states(self, config):
        masses = raps.initial_states(config) if config.algorithm == "raps" else {}
        return {
            n.id: EventState(
                dict(
                    z=n.initial,
                    value=n.initial,
                    count=0,
                    phase="waiting",
                    inbox=[],
                    batch=[],
                    value_time_ms=0,
                    next_update_ms=config.node_parameters(n.id)["first_update_ms"],
                    mass=masses[n.id].snapshot() if n.id in masses else None,
                    received=0,
                    used=0,
                    last_batch=0,
                    max_batch=0,
                )
            )
            for n in config.nodes
        }

    def dispatch(self, state, ident, neighbours, event):
        s, kind, t = state.data, event["kind"], event["time_ms"]
        outgoing, used = [], []
        if kind == "delivery":
            message = event["message"]
            s["batch" if s["phase"] == "collecting" else "inbox"].append(message)
            s["received"] += 1
        elif kind == "cycle_start":
            s["phase"] = "collecting"
            s["batch"], s["inbox"] = s["inbox"], []
            version = s["count"] + 1
            outgoing = [
                dict(
                    id=f"{ident}:{version}:{v}",
                    sender=ident,
                    receiver=v,
                    stamp=version,
                    sent=t,
                    sent_ms=t,
                    value=s["z"],
                    value_time_ms=s["value_time_ms"],
                )
                for v in neighbours
            ]
        elif kind == "update":
            s["count"] += 1
            if self.config.algorithm == "homework":
                used = s["batch"]
                if used:
                    s["value"] += (
                        sum(m["value"] for m in used) - len(used) * s["value"]
                    ) / 7
                s["z"], s["batch"], s["phase"] = s["value"], [], "waiting"
            else:
                mass = deepcopy(s["mass"])
                mass.pop("z", None)
                local = raps.AgentState(**mass)
                old_stamps = {k: v["stamp"] for k, v in mass["rho"].items()}
                inbox = [
                    raps.Message(
                        **{
                            k: m[k]
                            for k in (
                                "id",
                                "sender",
                                "receiver",
                                "stamp",
                                "sent",
                                "phi_x",
                                "phi_y",
                                "due",
                            )
                        }
                    )
                    for m in s["inbox"]
                ]
                messages, _ = raps.activate(local, ident, neighbours, inbox, s["count"])
                latest = {}
                for m in s["inbox"]:
                    if m["stamp"] > old_stamps[m["sender"]]:
                        latest[m["sender"]] = m
                used = list(latest.values())
                outgoing = [
                    {
                        **asdict(m),
                        "stamp": s["count"],
                        "sent": t,
                        "sent_ms": t,
                        "value_time_ms": s["value_time_ms"],
                    }
                    for m in messages
                ]
                s["mass"], s["z"], s["inbox"] = local.snapshot(), local.x / local.y, []
            s["value_time_ms"] = t
            s["used"] += len(used)
            s["last_batch"] = len(used)
            s["max_batch"] = max(s["max_batch"], len(used))
            s["next_update_ms"] = event["next_update_ms"]
            if not isfinite(s["z"]):
                raise ArithmeticError("Численное переполнение алгоритма")
        else:
            raise ValueError(f"Неизвестное локальное событие {kind}")
        return dict(outgoing=outgoing, used=used)


class EventRuntime:
    name = "core"

    async def start(self, config):
        self.rule = EventRule(config)
        self.states = self.rule.initial_states(config)
        self.neighbours = {
            n.id: sorted(e.target for e in config.edges if e.source == n.id)
            for n in config.nodes
        }

    async def dispatch(self, ident, event):
        return self.rule.dispatch(
            self.states[ident], ident, self.neighbours[ident], event
        )

    def snapshot(self):
        return {k: v.snapshot() for k, v in self.states.items()}

    def agent_snapshot(self, ident):
        return self.states[ident].snapshot()

    async def close(self):
        pass
