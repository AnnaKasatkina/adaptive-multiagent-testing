"""Versioned event-time configuration. Legacy tick configurations stay unchanged."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import Node, Edge, preset

WINDOW = 800
MIN_PAUSE = 10
DEFAULTS = dict(
    interval_ms=1000,
    interval_jitter_ms=0,
    first_update_ms=1000,
    delay_ms=200,
    delay_jitter_ms=0,
)
REGISTRY = [
    dict(
        id=k,
        label=label,
        kind="int",
        scope=scope,
        default=DEFAULTS[k],
        unit="ms",
        display_unit="s",
        minimum=0 if "jitter" in k else 1,
        searchable=True,
        group="control",
    )
    for k, label, scope in [
        ("interval_ms", "Интервал обновлений", "node"),
        ("interval_jitter_ms", "Разброс интервала ±", "node"),
        ("first_update_ms", "Первое обновление через", "node"),
        ("delay_ms", "Задержка", "edge"),
        ("delay_jitter_ms", "Разброс задержки ±", "edge"),
    ]
]
BY_ID = {p["id"]: p for p in REGISTRY}


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["agent_slowdown", "link_slowdown"]
    targets: list[str] = Field(min_length=1)
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    factor: int = Field(ge=2, le=16)


class EventConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    time_model: Literal["events-1"] = "events-1"
    algorithm: Literal["homework", "raps"] = "homework"
    name: str = "Новый эксперимент"
    nodes: list[Node] = Field(min_length=2, max_length=32)
    edges: list[Edge]
    horizon_ms: int = Field(default=60000, ge=1, le=2000000)
    max_gap_ms: int = Field(default=12000, ge=1)
    max_delay_ms: int = Field(default=8000, ge=1)
    epsilon: float = Field(default=0.001, gt=0, le=1, allow_inf_nan=False)
    defaults: dict[str, int] = Field(default_factory=lambda: dict(DEFAULTS))
    episodes: list[Episode] = Field(default_factory=list)
    # Explicit scientific controls, not a second hidden UI scheduling policy.
    schedule: dict[str, Any] | None = None
    view: dict[str, Any] = Field(default_factory=dict)

    def node_parameters(self, ident):
        n = next(n for n in self.nodes if n.id == ident)
        return {**self.defaults, **n.overrides}

    def edge_parameters(self, edge):
        return {**self.defaults, **edge.overrides}

    @model_validator(mode="before")
    @classmethod
    def integers(cls, data):
        if isinstance(data, dict):
            values = list(data.get("defaults", {}).values())
            values += [
                v
                for obj in data.get("nodes", []) + data.get("edges", [])
                if isinstance(obj, dict)
                for v in obj.get("overrides", {}).values()
            ]
            if any(type(v) is not int for v in values):
                raise ValueError("Времена задаются целым числом миллисекунд")
        return data

    @model_validator(mode="after")
    def validate_all(self):
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы агентов повторяются")
        if set(self.defaults) - set(DEFAULTS):
            raise ValueError("Неизвестный параметр событийной модели")
        self.defaults = {**DEFAULTS, **self.defaults}
        seen = set()
        for edge in self.edges:
            if (
                edge.source not in ids
                or edge.target not in ids
                or edge.source == edge.target
                or edge.id in seen
            ):
                raise ValueError("Недопустимая или повторная связь")
            seen.add(edge.id)
        for reverse in (False, True):
            visited, todo = set(), [ids[0]]
            while todo:
                u = todo.pop()
                if u in visited:
                    continue
                visited.add(u)
                todo.extend(
                    e.source if reverse else e.target
                    for e in self.edges
                    if (e.target if reverse else e.source) == u
                )
            if visited != set(ids):
                raise ValueError("Граф должен быть сильно связным")
        for scope, objects in (("node", self.nodes), ("edge", self.edges)):
            for obj in objects:
                if any(
                    k not in BY_ID or BY_ID[k]["scope"] != scope for k in obj.overrides
                ):
                    raise ValueError(f"Неизвестный параметр объекта {obj.id}")
                p = (
                    self.node_parameters(obj.id)
                    if scope == "node"
                    else self.edge_parameters(obj)
                )
                if scope == "node":
                    minimum = WINDOW + MIN_PAUSE if self.algorithm == "homework" else 1
                    lo, hi = (
                        p["interval_ms"] - p["interval_jitter_ms"],
                        p["interval_ms"] + p["interval_jitter_ms"],
                    )
                    if (
                        p["interval_jitter_ms"] < 0
                        or not minimum <= lo <= hi <= self.max_gap_ms
                    ):
                        raise ValueError(
                            f"{obj.id}: интервал с разбросом должен быть {minimum / 1000:g}…{self.max_gap_ms / 1000:g} с"
                        )
                    first_min = WINDOW if self.algorithm == "homework" else 1
                    if not first_min <= p["first_update_ms"] <= self.max_gap_ms:
                        raise ValueError(
                            f"{obj.id}: первое обновление должно быть {first_min / 1000:g}…{self.max_gap_ms / 1000:g} с"
                        )
                elif (
                    p["delay_jitter_ms"] < 0
                    or not 1
                    <= p["delay_ms"] - p["delay_jitter_ms"]
                    <= p["delay_ms"] + p["delay_jitter_ms"]
                    <= self.max_delay_ms
                ):
                    raise ValueError(
                        f"{obj.id}: задержка с разбросом должна быть 0.001…{self.max_delay_ms / 1000:g} с"
                    )
        if len({e.id for e in self.episodes}) != len(self.episodes):
            raise ValueError("Идентификаторы воздействий повторяются")
        for i, ep in enumerate(self.episodes):
            scope = "node" if ep.kind == "agent_slowdown" else "edge"
            valid = set(ids) if scope == "node" else seen
            if len(set(ep.targets)) != len(ep.targets) or not set(ep.targets) <= valid:
                raise ValueError(
                    f"{ep.id}: неизвестные или повторные объекты воздействия"
                )
            if (
                ep.start_ms >= self.horizon_ms
                or ep.start_ms + ep.duration_ms > self.horizon_ms
            ):
                raise ValueError(f"{ep.id}: воздействие выходит за горизонт")
            for target in ep.targets:
                if scope == "node":
                    p = self.node_parameters(target)
                    upper = (p["interval_ms"] + p["interval_jitter_ms"]) * ep.factor
                    limit = self.max_gap_ms
                else:
                    p = self.edge_parameters(
                        next(e for e in self.edges if e.id == target)
                    )
                    upper = (p["delay_ms"] + p["delay_jitter_ms"]) * ep.factor
                    limit = self.max_delay_ms
                if upper > limit:
                    raise ValueError(
                        f"{ep.id}: воздействие на {target} превышает жёсткую границу {limit / 1000:g} с"
                    )
            for other in self.episodes[:i]:
                if (
                    ep.kind == other.kind
                    and set(ep.targets) & set(other.targets)
                    and max(ep.start_ms, other.start_ms)
                    < min(
                        ep.start_ms + ep.duration_ms, other.start_ms + other.duration_ms
                    )
                ):
                    raise ValueError(f"Пересекаются воздействия {other.id} и {ep.id}")
        if self.schedule:
            if self.episodes:
                raise ValueError("Явное расписание не совмещается с воздействиями")
            if set(self.schedule) - {"updates", "delays"}:
                raise ValueError("Неизвестное поле явного расписания")
            for ident, times in self.schedule.get("updates", {}).items():
                if (
                    ident not in ids
                    or not times
                    or any(type(t) is not int for t in times)
                ):
                    raise ValueError("Недопустимое расписание обновлений")
                p = self.node_parameters(ident)
                if times[0] != p["first_update_ms"]:
                    raise ValueError("Первое обновление расходится с расписанием")
                minimum = WINDOW + MIN_PAUSE if self.algorithm == "homework" else 1
                if (
                    any(
                        not minimum <= b - a <= self.max_gap_ms
                        for a, b in zip(times, times[1:])
                    )
                    or times[-1] < self.horizon_ms
                ):
                    raise ValueError(
                        "Расписание должно покрывать горизонт и соблюдать интервалы"
                    )
            for ident, delays in self.schedule.get("delays", {}).items():
                if ident not in seen or any(
                    type(d) is not int or not 1 <= d <= self.max_delay_ms
                    for d in delays
                ):
                    raise ValueError("Недопустимое расписание задержек")
        return self


def event_preset(kind="bridge", n=6, algorithm="homework"):
    old = preset(kind, n)
    return EventConfiguration(
        name=old.name, algorithm=algorithm, nodes=old.nodes, edges=old.edges
    )


def parse_configuration(value):
    from .models import Configuration

    if isinstance(value, (EventConfiguration, Configuration)):
        return value
    return (
        EventConfiguration.model_validate(value)
        if value.get("schema_version", 1) == 2
        else Configuration.model_validate(value)
    )


def materialize(base, values, spec):
    from .recording import digest
    from .algorithms import model_version

    c = EventConfiguration.model_validate(base)
    if spec.get("schema_version") != 2 or spec.get("model_version") != model_version(c):
        raise ValueError("Несовместимая версия пространства поиска")
    if spec.get("base_hash") != digest(c.model_dump()):
        raise ValueError("Пространство относится к другой конфигурации")
    names = [v["name"] for v in spec["variables"]]
    if len(set(names)) != len(names) or set(values) - set(names):
        raise ValueError("Повторные или неизвестные переменные")
    data, occupied = c.model_dump(), set()
    for variable in spec["variables"]:
        value = values.get(variable["name"], variable["default"])
        if (
            type(value) is not int
            or not variable["minimum"] <= value <= variable["maximum"]
        ):
            raise ValueError(
                f"{variable['name']}: значение вне целочисленного диапазона"
            )
        for target in variable["targets"]:
            scope, ident, key = target["scope"], target["id"], target["parameter"]
            token = (scope, ident, key)
            if token in occupied or key not in BY_ID or BY_ID[key]["scope"] != scope:
                raise ValueError("Конфликтующие или неверные привязки")
            occupied.add(token)
            objects = data["nodes" if scope == "node" else "edges"]
            obj = next(
                (
                    o
                    for o in objects
                    if (o["id"] if scope == "node" else f"{o['source']}>{o['target']}")
                    == ident
                ),
                None,
            )
            if obj is None:
                raise ValueError("Объект привязки не найден")
            obj["overrides"][key] = value
    return EventConfiguration.model_validate(data)
