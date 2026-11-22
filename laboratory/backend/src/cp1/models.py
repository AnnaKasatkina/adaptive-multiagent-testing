from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from .parameters import DEFAULTS, BY_ID, check_parameters


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    initial: float = Field(ge=-1e6, le=1e6, allow_inf_nan=False)
    label: str = ""
    overrides: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    overrides: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self):
        return f"{self.source}>{self.target}"


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    algorithm: str = "raps"
    name: str = "Новый эксперимент"
    nodes: list[Node] = Field(min_length=2, max_length=32)
    edges: list[Edge]
    horizon: int = Field(default=200, ge=1, le=2000)
    max_gap: int = Field(default=4, ge=1, le=100)
    max_delay: int = Field(default=5, ge=1, le=100)
    epsilon: float = Field(default=1e-3, gt=0, le=1, allow_inf_nan=False)
    defaults: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULTS))
    view: dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler):
        data = handler(self)
        # Original RAPS packages retain byte-for-byte configuration semantics.
        # A missing algorithm always means RAPS, never the UI's default choice.
        if self.algorithm == "raps":
            data.pop("algorithm", None)
        return data

    def node_parameters(self, ident: str) -> dict:
        node = next(n for n in self.nodes if n.id == ident)
        return {**DEFAULTS, **self.defaults, **node.overrides}

    def edge_parameters(self, edge: Edge) -> dict:
        return {**DEFAULTS, **self.defaults, **edge.overrides}

    @model_validator(mode="after")
    def validate_model(self):
        if self.algorithm not in ("raps", "homework", "homework-original"):
            raise ValueError("Неизвестный алгоритм консенсуса")
        if self.schema_version != 1:
            raise ValueError("Неподдерживаемая версия конфигурации")
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы агентов должны быть уникальны")
        if set(self.defaults) - set(BY_ID):
            raise ValueError("Неизвестный общий параметр")
        self.defaults = {**DEFAULTS, **self.defaults}
        seen = set()
        for edge in self.edges:
            if (
                edge.source not in ids
                or edge.target not in ids
                or edge.source == edge.target
                or edge.id in seen
            ):
                raise ValueError("Недопустимая, повторная связь или петля")
            seen.add(edge.id)
            if any(
                k not in BY_ID or BY_ID[k]["scope"] != "edge" for k in edge.overrides
            ):
                raise ValueError("Неизвестный параметр связи")
            check_parameters(
                self.edge_parameters(edge), "edge", self.max_gap, self.max_delay
            )
        for node in self.nodes:
            if any(
                k not in BY_ID or BY_ID[k]["scope"] != "node" for k in node.overrides
            ):
                raise ValueError("Неизвестный параметр агента")
            check_parameters(
                self.node_parameters(node.id), "node", self.max_gap, self.max_delay
            )
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
                raise ValueError(
                    "Граф должен быть сильно связным: проверьте пути между агентами"
                )
        return self


def preset(kind="bridge", n=6) -> Configuration:
    if not 2 <= n <= 32:
        raise ValueError("Нужно от 2 до 32 агентов")
    ids = [f"a{i + 1:02}" for i in range(n)]
    pairs = set()

    def both(i, j):
        pairs.update(((i, j), (j, i)))

    if kind == "directed":
        pairs.update((i, (i + 1) % n) for i in range(n))
    elif kind == "complete":
        pairs.update((i, j) for i in range(n) for j in range(n) if i != j)
    elif kind == "star":
        for i in range(1, n):
            both(0, i)
    elif kind == "path":
        for i in range(n - 1):
            both(i, i + 1)
    elif kind == "bridge" and n >= 4:
        cut = n // 2
        for group in (range(cut), range(cut, n)):
            for i in group:
                for j in group:
                    if i != j:
                        pairs.add((i, j))
        both(cut - 1, cut)
    elif kind == "random":
        import random

        r = random.Random(2026 + n)
        pairs.update((i, (i + 1) % n) for i in range(n))
        pairs.update(
            (i, j) for i in range(n) for j in range(n) if i != j and r.random() < 0.18
        )
    else:
        for i in range(n):
            both(i, (i + 1) % n)
    return Configuration(
        name={
            "bridge": "Две группы",
            "ring": "Кольцо",
            "directed": "Ориентированное кольцо",
            "complete": "Полный граф",
            "path": "Цепочка",
            "star": "Звезда",
            "random": "Случайная сеть",
        }.get(kind, kind),
        nodes=[
            Node(id=s, label=str(i + 1), initial=float(i >= n // 2))
            for i, s in enumerate(ids)
        ],
        edges=[Edge(source=ids[i], target=ids[j]) for i, j in sorted(pairs)],
    )
