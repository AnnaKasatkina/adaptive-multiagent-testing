import { useMemo, useState, useCallback } from "react";
import type { Run, Parameter } from "./types";
import { number } from "./types";
import { Maximize2 } from "lucide-react";
import { Dialog } from "./ResearchPanels";

export function ParameterComparison({
  runs,
  registry,
}: {
  runs: Run[];
  registry: Parameter[];
}) {
  const [onlyDifferent, setOnlyDifferent] = useState(true),
    [query, setQuery] = useState(""),
    [expanded, setExpanded] = useState(false);
  const closeExpanded = useCallback(() => setExpanded(false), []);
  const rows = useMemo(() => {
    const result: {
      label: string;
      values: (string | number)[];
      different: boolean;
    }[] = [];
    const add = (label: string, values: (string | number)[]) =>
      result.push({ label, values, different: new Set(values).size > 1 });
    add(
      "Модель времени",
      runs.map((r) =>
        r.configuration.schema_version === 2 ? "События · секунды" : "Такты",
      ),
    );
    add(
      "Явное расписание",
      runs.map((r) =>
        r.configuration.schedule
          ? `Встроено · ${r.manifest?.configuration_hash?.slice(0, 12) ?? "см. конфигурацию"}`
          : "Не задано",
      ),
    );
    add(
      "Происхождение",
      runs.map((r) => r.parent ? `${r.parent.run_id} · событие ${r.parent.after_tick}` : "С начала"),
    );
    add(
      "Алгоритм",
      runs.map((r) =>
        r.configuration.algorithm?.startsWith("homework")
          ? "Локальный"
          : "RAPS",
      ),
    );
    add(
      "Случайный сценарий · seed",
      runs.map((r) => r.seed),
    );
    add(
      "Исполнение",
      runs.map((r) => (r.runtime === "core" ? "Ядро" : "SPADE")),
    );
    for (const [key, label] of [
      ["horizon", "Горизонт"],
      ["max_gap", "Макс. интервал активаций"],
      ["max_delay", "Предел задержки"],
      ["epsilon", "Порог точности"],
    ] as const)
      add(
        label,
        runs.map((r) =>
          r.configuration.schema_version === 2 && key !== "epsilon"
            ? Number(r.configuration[`${key}_ms` as "horizon_ms"] ?? 0) / 1000
            : r.configuration[key],
        ),
      );
    const ids = [
      ...new Set(runs.flatMap((r) => r.configuration.nodes.map((n) => n.id))),
    ];
    for (const id of ids) {
      const name =
        runs.flatMap((r) => r.configuration.nodes).find((n) => n.id === id)
          ?.label || id;
      add(
        `Исходное число · агент ${name}`,
        runs.map(
          (r) =>
            r.configuration.nodes.find((n) => n.id === id)?.initial ??
            "нет агента",
        ),
      );
      for (const param of registry.filter((p) => p.scope === "node")) {
        const values = runs.map((r) => {
          const n = r.configuration.nodes.find((n) => n.id === id);
          if (!n) return "нет агента";
          const params = { ...r.configuration.defaults, ...n.overrides };
          if (
            param.active_when &&
            Object.entries(param.active_when).some(([k, v]) => params[k] !== v)
          )
            return "не используется";
          return params[param.id] === undefined
            ? "—"
            : r.configuration.schema_version === 2
              ? Number(params[param.id]) / 1000
              : params[param.id];
        });
        add(`${param.label} · агент ${name}`, values);
      }
    }
    const links = [
      ...new Set(
        runs.flatMap((r) =>
          r.configuration.edges.map((e) => e.source + ">" + e.target),
        ),
      ),
    ];
    for (const id of links) {
      for (const param of registry.filter((p) => p.scope === "edge"))
        add(
          `${param.label} · ${id.replaceAll("a0", "").replace(">", " → ")}`,
          runs.map((r) => {
            const e = r.configuration.edges.find(
              (e) => e.source + ">" + e.target === id,
            );
            return e
              ? r.configuration.schema_version === 2
                ? Number(
                    e.overrides[param.id] ?? r.configuration.defaults[param.id],
                  ) / 1000
                : (e.overrides[param.id] ?? r.configuration.defaults[param.id])
              : "нет связи";
          }),
        );
    }
    add(
      "Временные воздействия",
      runs.map((r) => JSON.stringify(r.configuration.episodes ?? [])),
    );
    return result;
  }, [runs, registry]);
  if (runs.length < 2)
    return (
      <div className="chart-empty">
        <strong>Выберите два запуска в истории</strong>
        <span>Здесь появится по одной строке на параметр каждого объекта.</span>
      </div>
    );
  const visible = rows.filter(
    (r) =>
      (!onlyDifferent || r.different) &&
      r.label.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const display = (v: string | number) =>
    typeof v === "number"
      ? number(v)
      : v === "random"
        ? "Случайные"
        : v === "periodic"
          ? "Периодические"
          : v;
  const content = (
    <div className="parameter-comparison">
      <div className="comparison-controls">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={onlyDifferent}
            onChange={(e) => setOnlyDifferent(e.target.checked)}
          />
          Только различия
        </label>
        <input
          aria-label="Найти параметр в сравнении"
          placeholder="Найти параметр или агента"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span>{visible.length} строк</span>
        {!expanded && (
          <button className="button small" onClick={() => setExpanded(true)}>
            <Maximize2 size={13} />
            Развернуть
          </button>
        )}
      </div>
      <div className="comparison-table">
        <table>
          <thead>
            <tr>
              <th>Параметр и объект</th>
              {runs.map((r) => (
                <th key={r.id}>
                  <strong>{r.name}</strong>
                  <span>
                    {r.id.slice(0, 5)} · seed {r.seed}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr key={i}>
                <th>{row.label}</th>
                {row.values.map((v, j) => (
                  <td className={row.different ? "different" : ""} key={j}>
                    {display(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!visible.length && (
          <p className="comparison-empty">
            Настройки совпадают. Можно сравнить траектории или показать все
            параметры.
          </p>
        )}
      </div>
    </div>
  );
  return (
    <>
      {expanded ? (
        <Dialog title="Сравнение параметров" onClose={closeExpanded}>
          {content}
        </Dialog>
      ) : (
        content
      )}
    </>
  );
}
