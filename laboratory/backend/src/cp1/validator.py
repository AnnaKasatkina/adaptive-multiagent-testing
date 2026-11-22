"""An event-audit validator. Does not call the scheduler or RAPS transition."""

from __future__ import annotations
from math import fsum, isfinite
from .models import Configuration
from .recording import digest


def validate_trace(run: dict) -> dict:
    if run["configuration"].get("schema_version") == 2:
        from .event_validator import validate_event_trace
        return validate_event_trace(run)
    errors = []
    config = Configuration.model_validate(run["configuration"])
    trace = run["trace"]
    sent = {}
    delivered = set()
    active = {n.id: 0 for n in config.nodes}
    last_due = {}
    inboxes = {n.id: [] for n in config.nodes}
    activation_count = {n.id: 0 for n in config.nodes}
    initial_sum = fsum(n.initial for n in config.nodes)
    tol = 1e-10 * max(1, fsum(abs(n.initial) for n in config.nodes))
    edges = {(e.source, e.target): e for e in config.edges}

    def check(condition, message, t):
        if not condition:
            errors.append({"tick": t, "message": message})

    for expected, rec in enumerate(trace):
        t = rec["tick"]
        check(t == expected, "Непоследовательные такты", t)
        check(
            digest(rec["state"]) == rec["state_hash"],
            "Отпечаток состояния не совпадает",
            t,
        )
        this_active = set()
        for event in rec["events"]:
            kind = event["kind"]
            if kind == "send":
                m = event["message"]
                key = (m["sender"], m["receiver"])
                check(key in edges, "Отправка по отсутствующей связи", t)
                check(m["id"] not in sent, "Повторный идентификатор", t)
                edge = edges.get(key)
                p = (
                    config.edge_parameters(edge)
                    if edge
                    else {"delay_min": 1, "delay_max": config.max_delay}
                )
                check(m["sent"] == t and m["stamp"] == t, "Неверная метка отправки", t)
                check(
                    p["delay_min"] <= m["due"] - t <= p["delay_max"],
                    "Нарушены границы задержки",
                    t,
                )
                check(m["due"] > last_due.get(key, 0), "Нарушен FIFO", t)
                last_due[key] = m["due"]
                sent[m["id"]] = m
            elif kind == "delivery":
                m = event["message"]
                check(
                    sent.get(m["id"]) == m,
                    "Доставка неизвестного или изменённого сообщения",
                    t,
                )
                check(m["id"] not in delivered, "Повторная доставка", t)
                check(m["due"] == t, "Неверное время доставки", t)
                delivered.add(m["id"])
                inboxes[m["receiver"]].append(m["id"])
            elif kind == "activation":
                ident = event["agent"]
                check(ident not in this_active, "Двойная активация", t)
                this_active.add(ident)
                check(
                    t - active[ident] <= config.max_gap,
                    "Превышен интервал активаций",
                    t,
                )
                active[ident] = t
                activation_count[ident] += 1
                check(
                    event["inbox_ids"] == inboxes[ident], "Неверный входящий набор", t
                )
                inboxes[ident] = []
            else:
                check(False, "Неизвестное событие", t)
        for ident in active:
            check(
                t - active[ident] < config.max_gap or t == 0,
                "Пропущена обязательная активация",
                t,
            )
        state = rec["state"]
        pending = {m["id"]: m for m in state["pending"]}
        check(
            pending == {k: m for k, m in sent.items() if k not in delivered},
            "Сообщения в пути потеряны или добавлены",
            t,
        )
        check(
            all(m["due"] > t for m in pending.values()),
            "Просроченное сообщение в очереди",
            t,
        )
        for ident, items in state["inboxes"].items():
            check(
                [m["id"] for m in items] == inboxes[ident],
                "Очередь получателя расходится",
                t,
            )
        a = state["agents"]
        if config.algorithm == "raps":
            mx = fsum(s["x"] for s in a.values()) + fsum(
                a[e.source]["phi_x"] - a[e.target]["rho"][e.source]["x"]
                for e in config.edges
            )
            my = fsum(s["y"] for s in a.values()) + fsum(
                a[e.source]["phi_y"] - a[e.target]["rho"][e.source]["y"]
                for e in config.edges
            )
            check(abs(mx - initial_sum) <= tol, "Масса x не сохраняется", t)
            check(abs(my - len(a)) <= 1e-10 * len(a), "Масса y не сохраняется", t)
            check(all(s["y"] > 0 for s in a.values()), "Неположительная масса y", t)
        else:
            check(
                all(isfinite(value["z"]) for value in a.values()),
                "Неконечная численная оценка",
                t,
            )
    complete = bool(trace) and trace[-1]["tick"] == config.horizon
    return {
        "valid": not errors,
        "algorithm": config.algorithm,
        "scope": "Допустимость расписания и конечность чисел; верность среднего оценивается отдельно"
        if config.algorithm.startswith("homework")
        else "Расписание и инварианты RAPS",
        "complete": complete,
        "checked_ticks": len(trace),
        "sent": len(sent),
        "delivered": len(delivered),
        "errors": errors[:30],
    }
