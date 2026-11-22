import { useEffect, useState } from "react";
import { ArrowUpRight, RotateCcw } from "lucide-react";
import type { Configuration, Parameter } from "./types";
import { EventSettings } from "./EventSettings";

export type SettingsProps = {
  config: Configuration;
  registry: Parameter[];
  editable: boolean;
  tab: string;
  onTab: (v: string) => void;
  presets: any[];
  onPreset: (kind: string, n: number) => void;
  onChange: (fn: (c: Configuration) => void) => void;
  nodes: string[];
  edges: string[];
  onImport: () => void;
  onDraft: () => void;
  onValues: () => void;
  runtime: string;
  onRuntime: (v: string) => void;
  seed: number;
  onSeed: (v: number) => void;
};

export function SettingsPanel(p: SettingsProps) {
  return p.config.schema_version === 2 ? (
    <EventSettings {...p} />
  ) : (
    <LegacySettings {...p} />
  );
}
function LegacySettings(p: SettingsProps) {
  const { config: c } = p;
  const [count, setCount] = useState(c.nodes.length),
    [preset, setPreset] = useState("bridge");
  useEffect(() => setCount(c.nodes.length), [c.nodes.length]);
  const idsFor = (scope: "node" | "edge") =>
    scope === "node" ? p.nodes : p.edges;
  const object = (
    configuration: Configuration,
    scope: "node" | "edge",
    id: string,
  ): any =>
    scope === "node"
      ? configuration.nodes.find((n) => n.id === id)
      : configuration.edges.find((e) => `${e.source}>${e.target}` === id);
  const values = (key: string, scope: "node" | "edge") => {
    const ids = idsFor(scope);
    return ids.length
      ? ids.map((id) => object(c, scope, id)?.overrides[key] ?? c.defaults[key])
      : [c.defaults[key]];
  };
  const effective = (key: string, scope: "node" | "edge") => {
    const found = values(key, scope);
    return found.every((v) => v === found[0]) ? found[0] : "";
  };
  const write = (key: string, value: string | number, scope: "node" | "edge") =>
    p.onChange((d) => {
      const ids = idsFor(scope);
      if (!ids.length) d.defaults[key] = value;
      else
        for (const id of ids) {
          const item = object(d, scope, id);
          if (item) item.overrides[key] = value;
        }
    });
  const upper = (r: Parameter) =>
    r.maximum ??
    (r.scope === "edge"
      ? c.max_delay
      : r.id === "activation_phase"
        ? Math.min(...values("activation_period", "node").map(Number)) - 1
        : c.max_gap);
  const origin = (key: string, scope: "node" | "edge") => {
    const ids = idsFor(scope);
    if (!ids.length) return "Общее значение";
    if (effective(key, scope) === "") return "Разные значения";
    const n = ids.filter((id) =>
      Object.hasOwn(object(c, scope, id)?.overrides ?? {}, key),
    ).length;
    return n === 0
      ? "Из общей настройки"
      : n === ids.length
        ? "Индивидуально"
        : "Смешанные источники";
  };
  return (
    <aside className="settings-panel panel">
      <div className="panel-title">
        <strong>Параметры среды</strong>
        <span className="small-tag">{p.editable ? "Редактор" : "Запись"}</span>
      </div>
      <div className="segmented">
        {[
          ["context", "Контекст"],
          ["control", "Управление"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={p.tab === key ? "active" : ""}
            onClick={() => p.onTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="settings-content">
        {!p.editable && (
          <div className="info-note">
            <button onClick={p.onDraft}>
              Изменить конфигурацию <ArrowUpRight size={13} />
            </button>
          </div>
        )}
        {p.tab === "context" && (
          <>
            <label>
              Алгоритм
              <select
                aria-label="Алгоритм консенсуса"
                disabled={!p.editable}
                value={c.algorithm ?? "raps"}
                onChange={(e) =>
                  p.onChange(
                    (d) =>
                      (d.algorithm = e.target.value as
                        | "raps"
                        | "homework"
                        | "homework-original"),
                  )
                }
              >
                <option value="homework">Локальный</option>
                <option value="raps">RAPS</option>
              </select>
            </label>
            <label>
              Название конфигурации
              <input
                aria-label="Название конфигурации"
                disabled={!p.editable}
                value={c.name}
                onChange={(e) => p.onChange((d) => (d.name = e.target.value))}
              />
            </label>
            <label>
              Готовый граф
              <select
                aria-label="Готовый граф"
                disabled={!p.editable}
                value={preset}
                onChange={(e) => {
                  setPreset(e.target.value);
                  p.onPreset(e.target.value, count);
                }}
              >
                {p.presets.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.configuration.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="field-row">
              <label>
                Число агентов
                <input
                  aria-label="Число агентов"
                  type="number"
                  min={2}
                  max={32}
                  disabled={!p.editable}
                  value={count}
                  onChange={(e) => setCount(+e.target.value)}
                />
              </label>
              <button
                className="button small"
                disabled={!p.editable}
                onClick={() => p.onPreset(preset, count)}
              >
                Применить
              </button>
            </div>
            <button className="button full" onClick={p.onValues}>
              Начальные значения на графе
            </button>

            <div className="section-label">НАБЛЮДЕНИЕ</div>
            <label>
              Горизонт, такты
              <input
                aria-label="Горизонт, такты"
                type="number"
                min={1}
                max={2000}
                disabled={!p.editable}
                value={c.horizon}
                onChange={(e) =>
                  p.onChange((d) => (d.horizon = +e.target.value))
                }
              />
            </label>
            <label>
              Точность наблюдения
              <input
                aria-label="Точность наблюдения"
                type="number"
                step=".001"
                min={0.000001}
                max={1}
                disabled={!p.editable}
                value={c.epsilon}
                onChange={(e) =>
                  p.onChange((d) => (d.epsilon = +e.target.value))
                }
              />
            </label>
            <div className="section-label">ГРАНИЦЫ ДОПУСТИМЫХ УСЛОВИЙ</div>
            <label>
              Макс. интервал активаций
              <input
                aria-label="Макс. интервал активаций"
                type="number"
                min={1}
                max={100}
                disabled={!p.editable}
                value={c.max_gap}
                onChange={(e) =>
                  p.onChange((d) => (d.max_gap = +e.target.value))
                }
              />
            </label>
            <label>
              Макс. сетевая задержка
              <input
                aria-label="Макс. сетевая задержка"
                type="number"
                min={1}
                max={100}
                disabled={!p.editable}
                value={c.max_delay}
                onChange={(e) =>
                  p.onChange((d) => (d.max_delay = +e.target.value))
                }
              />
            </label>
            <button
              className="button full"
              disabled={!p.editable}
              onClick={p.onImport}
            >
              Импорт конфигурации
            </button>
          </>
        )}
        {p.tab === "control" && (
          <>
            {(["node", "edge"] as const).map((scope) => (
              <section
                className="parameter-section"
                key={scope}
                aria-label={
                  scope === "node" ? "Параметры агентов" : "Параметры связей"
                }
              >
                <div className="parameter-section-heading">
                  <h3>{scope === "node" ? "Агенты" : "Связи"}</h3>
                  <span>
                    {idsFor(scope).length
                      ? `Выбрано: ${idsFor(scope).length}`
                      : "Для всей сети"}
                  </span>
                </div>
                {p.registry
                  .filter((r) => r.scope === scope)
                  .filter(
                    (r) =>
                      !r.active_when ||
                      Object.entries(r.active_when).every(
                        ([key, value]) => effective(key, scope) === value,
                      ),
                  )
                  .map((r) => (
                    <label key={r.id}>
                      <span className="field-caption">
                        {r.label}
                        <small>{origin(r.id, scope)}</small>
                      </span>
                      {r.kind === "category" ? (
                        <select
                          aria-label={r.label}
                          disabled={!p.editable}
                          value={effective(r.id, scope)}
                          onChange={(e) => write(r.id, e.target.value, scope)}
                        >
                          {effective(r.id, scope) === "" && (
                            <option value="" disabled>
                              Разные режимы
                            </option>
                          )}
                          {r.choices?.map((v) => (
                            <option value={v} key={v}>
                              {v === "random" ? "Случайные" : "Периодические"}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          aria-label={r.label}
                          type="number"
                          step={r.kind === "float" ? ".05" : "1"}
                          min={r.minimum ?? 1}
                          max={upper(r)}
                          disabled={!p.editable}
                          placeholder="Разные"
                          value={effective(r.id, scope)}
                          onChange={(e) => write(r.id, +e.target.value, scope)}
                        />
                      )}
                    </label>
                  ))}
                {!!idsFor(scope).length && (
                  <button
                    className="button full"
                    disabled={!p.editable}
                    onClick={() =>
                      p.onChange((d) => {
                        for (const id of idsFor(scope)) {
                          const item = object(d, scope, id);
                          if (item) item.overrides = {};
                        }
                      })
                    }
                  >
                    <RotateCcw size={14} />
                    {scope === "node"
                      ? "Сбросить настройки агентов"
                      : "Сбросить настройки связей"}
                  </button>
                )}
              </section>
            ))}
          </>
        )}
      </div>
      <details className="execution-settings">
        <summary>
          Исполнение и случайность{" "}
          <span>
            {p.runtime === "core" ? "Ядро" : "SPADE"} · seed {p.seed}
          </span>
        </summary>
        <label>
          Исполнение
          <select
            aria-label="Способ исполнения"
            disabled={!p.editable}
            value={p.runtime}
            onChange={(e) => p.onRuntime(e.target.value)}
          >
            <option value="core">Вычислительное ядро</option>
            <option value="spade">SPADE · настоящие агенты</option>
          </select>
        </label>
        <label>
          Случайный сценарий (seed)
          <input
            aria-label="Seed сценария"
            type="number"
            min={0}
            disabled={!p.editable}
            value={p.seed}
            onChange={(e) => p.onSeed(+e.target.value)}
          />
        </label>
      </details>
    </aside>
  );
}
