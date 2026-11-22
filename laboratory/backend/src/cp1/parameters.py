"""Single parameter vocabulary for forms, experiments and search materialization."""

from __future__ import annotations
from copy import deepcopy
from typing import Any

REGISTRY = [
    dict(
        id="activation_mode",
        label="Режим активации",
        description="Случайные или периодические обновления",
        kind="category",
        scope="node",
        default="random",
        choices=["random", "periodic"],
    ),
    dict(
        id="activation_probability",
        label="Вероятность активации",
        description="Обязательная активация по предельному интервалу сохраняется",
        kind="float",
        scope="node",
        default=0.5,
        minimum=0,
        maximum=1,
        active_when={"activation_mode": "random"},
    ),
    dict(
        id="activation_period",
        label="Период активаций",
        description="Число виртуальных тактов между обновлениями",
        kind="int",
        scope="node",
        default=2,
        minimum=1,
        maximum_ref="max_gap",
        active_when={"activation_mode": "periodic"},
    ),
    dict(
        id="activation_phase",
        label="Фаза активаций",
        description="Первое периодическое обновление в такт 1 + фаза",
        kind="int",
        scope="node",
        default=0,
        minimum=0,
        maximum_ref="activation_period-1",
        active_when={"activation_mode": "periodic"},
    ),
    dict(
        id="delay_min",
        label="Минимальная задержка",
        description="Нижняя граница сетевой задержки в тактах",
        kind="int",
        scope="edge",
        default=1,
        minimum=1,
        maximum_ref="max_delay",
    ),
    dict(
        id="delay_max",
        label="Максимальная задержка",
        description="Верхняя граница; равные границы задают фиксированную задержку",
        kind="int",
        scope="edge",
        default=5,
        minimum_ref="delay_min",
        maximum_ref="max_delay",
    ),
]
BY_ID = {p["id"]: p for p in REGISTRY}
DEFAULTS = {p["id"]: p["default"] for p in REGISTRY}


def registry() -> list[dict]:
    return [
        {
            **deepcopy(p),
            "searchable": True,
            "group": "control",
            "constraints": ["Configuration validation is mandatory after binding"],
        }
        for p in REGISTRY
    ]


def check_parameters(params: dict, scope: str, max_gap: int, max_delay: int) -> None:
    mode = params.get("activation_mode", "random")
    if scope == "node":
        if mode not in ("random", "periodic"):
            raise ValueError("Неизвестный режим активации")
        p = params["activation_probability"]
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            raise ValueError("Вероятность должна лежать от 0 до 1")
        if mode == "periodic":
            period, phase = params["activation_period"], params["activation_phase"]
            if type(period) is not int or not 1 <= period <= max_gap:
                raise ValueError("Период должен быть целым от 1 до B_max")
            if type(phase) is not int or not 0 <= phase < period:
                raise ValueError("Фаза должна быть целой: 0 <= фаза < период")
    else:
        lo, hi = params["delay_min"], params["delay_max"]
        if type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi <= max_delay:
            raise ValueError(
                "Задержки должны удовлетворять 1 <= min <= max <= Delta_max"
            )


def materialize(base: dict, values: dict[str, Any], spec: dict):
    """Apply a non-overlapping, typed parameter binding through the same validator."""
    if base.get("schema_version") == 2:
        from .event_models import materialize as event_materialize
        return event_materialize(base, values, spec)
    from .models import Configuration
    from .recording import digest

    config = Configuration.model_validate(base)
    from .algorithms import model_version

    if spec.get("model_version") and spec["model_version"] != model_version(config):
        raise ValueError(
            "Пространство поиска относится к другому алгоритму или версии модели"
        )
    if spec.get("schema_version", 1) != 1:
        raise ValueError("Неподдерживаемая версия пространства поиска")
    if spec.get("base_hash") and spec["base_hash"] != digest(config.model_dump()):
        raise ValueError("Пространство поиска относится к другой конфигурации")
    variables = spec.get("variables", [])
    names = [v["name"] for v in variables]
    if len(set(names)) != len(names) or set(values) - set(names):
        raise ValueError("Повторяющиеся или неизвестные переменные")
    data, occupied = config.model_dump(), set()
    for v in variables:
        value = values.get(v["name"], v.get("default"))
        kind = v.get("kind", "float")
        if kind not in ("float", "int", "category"):
            raise ValueError("Неизвестный тип переменной")
        if kind != "category" and (
            not isinstance(v.get("minimum"), (int, float))
            or not isinstance(v.get("maximum"), (int, float))
            or not v["minimum"] <= v["maximum"]
        ):
            raise ValueError("Неверные границы переменной")
        if kind == "category":
            if value not in v.get("choices", []):
                raise ValueError(f"{v['name']}: значение отсутствует среди вариантов")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{v['name']}: требуется число")
            if kind == "int" and type(value) is not int:
                raise ValueError(f"{v['name']}: требуется целое число")
            if not v["minimum"] <= value <= v["maximum"]:
                raise ValueError(f"{v['name']}: значение вне диапазона")
        if not v.get("targets"):
            raise ValueError("Переменная должна иметь хотя бы одну привязку")
        for target in v["targets"]:
            scope, ident, key = target["scope"], target["id"], target["parameter"]
            if (
                key not in BY_ID
                or BY_ID[key]["scope"] != scope
                or BY_ID[key]["kind"] != kind
            ):
                raise ValueError("Тип или область привязки не соответствует реестру")
            if kind == "category" and not set(v.get("choices", [])) <= set(
                BY_ID[key]["choices"]
            ):
                raise ValueError("Варианты переменной отсутствуют в реестре")
            if kind != "category":
                lower = BY_ID[key].get("minimum", 1)
                upper = BY_ID[key].get(
                    "maximum",
                    config.max_delay
                    if scope == "edge"
                    else config.max_gap - 1
                    if key == "activation_phase"
                    else config.max_gap,
                )
                if (
                    v["minimum"] < lower
                    or v["maximum"] > upper
                    or kind == "int"
                    and (type(v["minimum"]) is not int or type(v["maximum"]) is not int)
                ):
                    raise ValueError(
                        f"Диапазон {v['name']} выходит за ограничения {key}"
                    )
            token = (scope, ident, key)
            if token in occupied:
                raise ValueError("Выбранные переменные изменяют одно и то же поле")
            occupied.add(token)
            items = data["nodes"] if scope == "node" else data["edges"]
            obj = next(
                (
                    o
                    for o in items
                    if (o["id"] if scope == "node" else f"{o['source']}>{o['target']}")
                    == ident
                ),
                None,
            )
            if obj is None:
                raise ValueError(f"Объект {ident} не найден")
            obj["overrides"][key] = value
    result = Configuration.model_validate(data)
    for scope, ident, key in occupied:
        if scope == "node":
            p = result.node_parameters(ident)
            required = BY_ID[key].get("active_when", {})
            if any(p[k] != val for k, val in required.items()):
                raise ValueError(f"Параметр {key} неприменим в выбранном режиме")
    return result
