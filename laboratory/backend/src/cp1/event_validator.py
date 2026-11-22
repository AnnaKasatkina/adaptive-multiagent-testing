"""Independent audit of event causality, windows, FIFO, bounds and sparse states."""

from math import fsum, isfinite
from .event_models import EventConfiguration, WINDOW
from .event_engine import apply_patch, MAPS
from .recording import digest
from copy import deepcopy


def validate_event_trace(run):
    c = EventConfiguration.model_validate(run["configuration"])
    errors, sent, delivered, active, open_windows = [], {}, set(), {}, {}
    last_due, updates, previous_t, last_event = {}, {}, 0, 0
    minimum = WINDOW + 10 if c.algorithm == "homework" else 1

    def check(ok, message, seq):
        if not ok and len(errors) < 50:
            errors.append(dict(event_id=seq, message=message))

    state = None
    hashes = {k: {} for k in MAPS}
    for row in run["trace"]:
        if state is None:
            state = deepcopy(row["state"])
            changes = {k: state[k] for k in MAPS}
        else:
            apply_patch(state, row["patch"])
            changes = row["patch"]["maps"]
            if "state" in row:
                check(
                    digest(state) == digest(row["state"]),
                    "Полный снимок расходится с изменениями",
                    row["tick"],
                )
        for key, entries in changes.items():
            for ident, value in entries.items():
                if value is None:
                    hashes[key].pop(ident, None)
                else:
                    hashes[key][ident] = digest(value)
        seq, t = row["tick"], row["time_ms"]
        check(seq == last_event + (seq != 0), "Нарушен порядок событий", seq)
        check(t >= previous_t, "Время движется назад", seq)
        check(
            digest({k: hashes[k] if k in MAPS else v for k, v in state.items()})
            == row["state_hash"],
            "Отпечаток состояния расходится",
            seq,
        )
        for event in row["events"]:
            kind, ident = event["kind"], event.get("agent")
            if kind == "send":
                m = event["message"]
                edge = f"{m['sender']}>{m['receiver']}"
                check(
                    any(e.id == edge for e in c.edges),
                    "Отправка по неизвестной связи",
                    seq,
                )
                check(m["id"] not in sent, "Повторное сообщение", seq)
                check(
                    m["sent_ms"] == t
                    and 1 <= m["transport_ms"] <= m["due_ms"] - t <= c.max_delay_ms,
                    "Нарушена граница задержки",
                    seq,
                )
                check(m["due_ms"] >= last_due.get(edge, 0), "Нарушен FIFO", seq)
                last_due[edge], sent[m["id"]] = m["due_ms"], m
            elif kind == "delivery":
                m = event["message"]
                check(
                    m["id"] in sent
                    and all(m[k] == v for k, v in sent.get(m["id"], {}).items()),
                    "Изменённое или неизвестное сообщение",
                    seq,
                )
                check(
                    m["id"] not in delivered and m["due_ms"] == t,
                    "Неверная или повторная доставка",
                    seq,
                )
                delivered.add(m["id"])
            elif kind == "cycle_start":
                check(ident not in open_windows, "Перекрывающиеся окна", seq)
                open_windows[ident] = t
            elif kind == "update":
                if ident in updates:
                    check(
                        minimum <= t - updates[ident] <= c.max_gap_ms,
                        "Нарушен интервал обновлений",
                        seq,
                    )
                else:
                    check(
                        t == c.node_parameters(ident)["first_update_ms"],
                        "Неверное первое обновление",
                        seq,
                    )
                updates[ident] = t
                if c.algorithm == "homework":
                    check(
                        t - open_windows.pop(ident, -WINDOW) == WINDOW,
                        "Неверное окно сбора",
                        seq,
                    )
                for m in event["used"]:
                    check(
                        m["id"] in delivered and m["delivered_ms"] < t,
                        "Использование недоставленного сообщения или нарушение границы окна",
                        seq,
                    )
            elif kind == "episode_start":
                active[ident] = True
            elif kind == "episode_end":
                active.pop(ident, None)
            elif kind != "finish":
                check(False, "Неизвестное событие", seq)
        check(
            set(state["pending"]) == set(sent) - delivered,
            "Потеряно сообщение в пути",
            seq,
        )
        for ident, agent in state["agents"].items():
            check(isfinite(agent["z"]), "Неконечная оценка", seq)
            check(
                t - updates.get(ident, 0) <= c.max_gap_ms,
                "Пропущено обязательное обновление",
                seq,
            )
            check(
                all(m["id"] in delivered for m in agent["inbox"] + agent["batch"]),
                "Неизвестное сообщение в очереди",
                seq,
            )
        if c.algorithm == "raps":
            a = {k: s["mass"] for k, s in state["agents"].items()}
            rx = (
                fsum(s["x"] for s in a.values())
                + fsum(
                    a[e.source]["phi_x"] - a[e.target]["rho"][e.source]["x"]
                    for e in c.edges
                )
                - fsum(n.initial for n in c.nodes)
            )
            ry = (
                fsum(s["y"] for s in a.values())
                + fsum(
                    a[e.source]["phi_y"] - a[e.target]["rho"][e.source]["y"]
                    for e in c.edges
                )
                - len(a)
            )
            check(
                abs(rx) < 1e-8 * max(1, sum(abs(n.initial) for n in c.nodes))
                and abs(ry) < 1e-8 * len(a)
                and all(s["y"] > 0 for s in a.values()),
                "Нарушен инвариант массы",
                seq,
            )
        previous_t, last_event = t, seq
    complete = (
        bool(run["trace"])
        and run["trace"][-1]["time_ms"] == c.horizon_ms
        and any(e["kind"] == "finish" for e in run["trace"][-1]["events"])
    )
    return dict(
        valid=not errors,
        complete=complete,
        checked_events=len(run["trace"]),
        sent=len(sent),
        delivered=len(delivered),
        errors=errors,
        algorithm=c.algorithm,
    )
