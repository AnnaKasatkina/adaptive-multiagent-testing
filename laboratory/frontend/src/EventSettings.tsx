import { useState, useEffect, useCallback } from "react";
import type { SettingsProps } from "./SettingsPanel";
import type { Episode } from "./types";
import { api } from "./types";
import { Dialog } from "./ResearchPanels";

export function SecondsInput({
  value,
  onChange,
  label,
  disabled = false,
}: {
  value: number;
  onChange: (ms: number) => void;
  label: string;
  disabled?: boolean;
}) {
  const [text, setText] = useState(String(value / 1000)),
    [error, setError] = useState("");
  useEffect(() => setText(String(value / 1000)), [value]);
  const commit = () => {
    const n = Number(text.replace(",", ".")),
      ms = Math.round(n * 1000);
    if (!text.trim() || !Number.isFinite(n) || Math.abs(ms - n * 1000) > 1e-6) {
      setError("Введите секунды с точностью до 0,001");
      return;
    }
    setError("");
    if (ms !== value) onChange(ms);
  };
  return (
    <>
      <input
        aria-label={label}
        inputMode="decimal"
        disabled={disabled}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") {
            setText(String(value / 1000));
            setError("");
          }
        }}
      />
      {error && <small className="inline-error">{error}</small>}
    </>
  );
}

export function EventSettings(p: SettingsProps) {
  const c = p.config;
  const [area, setArea] = useState<Record<string, string>>({
    node: "global",
    edge: "global",
  });
  const [dialog, setDialog] = useState<string | null>(null),
    [onlyOverrides, setOnlyOverrides] = useState(false);
  const [mode, setMode] = useState("regular"),
    [graph, setGraph] = useState("bridge"),
    [count, setCount] = useState(c.nodes.length);
  const [savedConfigs, setSavedConfigs] = useState<any[]>([]),
    [configError, setConfigError] = useState("");
  useEffect(() => {
    if (dialog === "configs")
      api<any[]>("/configurations")
        .then(setSavedConfigs)
        .catch((e) => setConfigError(e.message));
  }, [dialog]);
  useEffect(() => setCount(c.nodes.length), [c.nodes.length]);
  const close = useCallback(() => setDialog(null), []);
  const ids = (scope: string) => (scope === "node" ? p.nodes : p.edges);
  const objects = (scope: string) =>
    scope === "node"
      ? c.nodes.map((n) => ({
          id: n.id,
          label: n.label || n.id,
          overrides: n.overrides,
        }))
      : c.edges.map((e) => ({
          id: `${e.source}>${e.target}`,
          label: `${e.source} → ${e.target}`,
          overrides: e.overrides,
        }));
  const edit = (scope: string, key: string, value: number, target?: string) =>
    p.onChange((d) => {
      const selected = target
        ? [target]
        : area[scope] === "selected"
          ? ids(scope)
          : [];
      if (!target && area[scope] === "global") d.defaults[key] = value;
      else
        for (const id of selected) {
          const obj =
            scope === "node"
              ? d.nodes.find((n) => n.id === id)
              : d.edges.find((e) => `${e.source}>${e.target}` === id);
          if (obj) obj.overrides[key] = value;
        }
    });
  const effective = (scope: string, key: string) => {
    const selected =
      area[scope] === "selected"
        ? objects(scope).filter((o) => ids(scope).includes(o.id))
        : [];
    const values = selected.length
      ? selected.map((o) => Number(o.overrides[key] ?? c.defaults[key]))
      : [Number(c.defaults[key])];
    return values.every((v) => v === values[0]) ? values[0] : null;
  };
  const group = p.nodes.length
    ? p.nodes
    : c.nodes.slice(0, Math.floor(c.nodes.length / 2)).map((n) => n.id);
  const links = p.edges.length
    ? p.edges
    : c.edges
        .filter((e) => group.includes(e.source) !== group.includes(e.target))
        .map((e) => `${e.source}>${e.target}`);
  const applyMode = () =>
    p.onChange((d) => {
      d.defaults = {
        interval_ms: 1000,
        interval_jitter_ms: 0,
        first_update_ms: 1000,
        delay_ms: 200,
        delay_jitter_ms: 0,
      };
      for (const obj of [...d.nodes, ...d.edges]) obj.overrides = {};
      d.episodes = [];
      if (mode === "irregular")
        Object.assign(d.defaults, {
          interval_jitter_ms: 100,
          delay_ms: 800,
          delay_jitter_ms: 400,
        });
      if (mode === "slow")
        for (const n of d.nodes)
          if (group.includes(n.id)) n.overrides.interval_ms = 4000;
      if (mode === "episode")
        d.episodes = [
          {
            id: crypto.randomUUID(),
            kind: "link_slowdown",
            targets: links,
            start_ms: 20000,
            duration_ms: 10000,
            factor: 4,
          },
        ];
    });
  const makeEpisode = (kind: Episode["kind"]) =>
    p.onChange((d) => {
      d.episodes ??= [];
      d.episodes.push({
        id: crypto.randomUUID(),
        kind,
        targets: kind === "agent_slowdown" ? [...group] : [...links],
        start_ms: 20000,
        duration_ms: 10000,
        factor: 2,
      });
    });
  const updateEpisode = (id: string, changes: Partial<Episode>) =>
    p.onChange((d) => {
      Object.assign(d.episodes!.find((e) => e.id === id)!, changes);
    });
  const temporal = (
    label: string,
    key: "horizon_ms" | "max_gap_ms" | "max_delay_ms",
  ) => (
    <label>
      {label}
      <SecondsInput
        label={label}
        value={c[key] ?? 0}
        disabled={!p.editable}
        onChange={(v) =>
          p.onChange((d) => {
            d[key] = v;
          })
        }
      />
    </label>
  );
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
        ].map(([id, label]) => (
          <button
            key={id}
            className={p.tab === id ? "active" : ""}
            onClick={() => p.onTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="settings-content">
        {!p.editable && (
          <button className="button" onClick={p.onDraft}>
            Редактировать копию
          </button>
        )}
        {p.tab === "context" ? (
          <>
            <label>
              Алгоритм
              <select
                aria-label="Алгоритм консенсуса"
                disabled={!p.editable}
                value={c.algorithm}
                onChange={(e) =>
                  p.onChange((d) => {
                    d.algorithm = e.target.value as any;
                  })
                }
              >
                <option value="homework">Локальный</option>
                <option value="raps">RAPS</option>
              </select>
            </label>
            <label>
              Название
              <input
                aria-label="Название конфигурации"
                value={c.name}
                disabled={!p.editable}
                onChange={(e) =>
                  p.onChange((d) => {
                    d.name = e.target.value;
                  })
                }
              />
            </label>
            <label>
              {p.editable ? "Готовый граф" : "Топология"}
              <select
                aria-label={p.editable ? "Готовый граф" : "Топология"}
                disabled={!p.editable}
                value={p.editable ? graph : "recorded"}
                onChange={(e) => {
                  setGraph(e.target.value);
                  p.onPreset(e.target.value, count);
                }}
              >
                {!p.editable && <option value="recorded">Граф из записи</option>}
                {p.presets.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.configuration.name}
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
                  value={count}
                  disabled={!p.editable}
                  onChange={(e) => setCount(+e.target.value)}
                />
              </label>
              <button
                className="button small"
                disabled={!p.editable}
                onClick={() => p.onPreset(graph, count)}
              >
                Применить
              </button>
            </div>
            {temporal("Горизонт, с", "horizon_ms")}
            <label>
              Точность
              <input
                aria-label="Точность наблюдения"
                type="number"
                step="0.001"
                value={c.epsilon}
                disabled={!p.editable}
                onChange={(e) =>
                  p.onChange((d) => {
                    d.epsilon = +e.target.value;
                  })
                }
              />
            </label>
            <details>
              <summary>Ограничения и исполнение</summary>
              {temporal("Макс. интервал, с", "max_gap_ms")}
              {temporal("Макс. задержка, с", "max_delay_ms")}
              <label>
                Исполнение
                <select
                  aria-label="Исполнение"
                  value={p.runtime}
                  disabled={!p.editable}
                  onChange={(e) => p.onRuntime(e.target.value)}
                >
                  <option value="core">Вычислительное ядро</option>
                  <option value="spade">SPADE</option>
                </select>
              </label>
              <label>
                Seed
                <input
                  aria-label="Seed"
                  type="number"
                  value={p.seed}
                  disabled={!p.editable}
                  onChange={(e) => p.onSeed(+e.target.value)}
                />
              </label>
              {c.algorithm === "homework" && (
                <dl className="algorithm-constants">
                  <dt>Окно приёма</dt>
                  <dd>0,8 с</dd>
                  <dt>Минимальная пауза</dt>
                  <dd>0,01 с</dd>
                </dl>
              )}
            </details>
            <div className="field-row">
              <button className="button" onClick={p.onImport}>
                Импорт
              </button>
              <button className="button" onClick={() => setDialog("configs")}>
                Конфигурации
              </button>
            </div>
          </>
        ) : (
          <>
            {p.editable && (
              <div className="scenario-preset">
                <label>
                  Режим
                  <select
                    aria-label="Режим условий"
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="regular">Регулярный</option>
                    <option value="irregular">Нерегулярный</option>
                    <option value="slow">Медленная группа</option>
                    <option value="episode">Замедление связи</option>
                  </select>
                </label>
                {mode === "slow" && <small>Агенты: {group.join(", ")}</small>}
                {mode === "episode" && (
                  <small>Направления: {links.join(", ") || "не выбраны"}</small>
                )}
                <button className="button full" onClick={applyMode}>
                  Применить режим
                </button>
              </div>
            )}
            {(["node", "edge"] as const).map((scope) => (
              <section className="parameter-section" key={scope}>
                <h3>{scope === "node" ? "Агенты" : "Связи"}</h3>
                <select
                  aria-label={`Область: ${scope === "node" ? "агенты" : "связи"}`}
                  value={area[scope]}
                  onChange={(e) =>
                    setArea({ ...area, [scope]: e.target.value })
                  }
                >
                  <option value="global">Общие значения</option>
                  <option value="selected">
                    Выбранные объекты ({ids(scope).length})
                  </option>
                </select>
                {p.registry
                  .filter((r) => r.scope === scope)
                  .map((r) => {
                    const value = effective(scope, r.id),
                      selected = area[scope] === "selected",
                      chosen = objects(scope).filter((o) =>
                        ids(scope).includes(o.id),
                      );
                    const count = chosen.filter((o) =>
                      Object.hasOwn(o.overrides, r.id),
                    ).length;
                    return (
                      <label key={r.id}>
                        {r.label}, с
                        {value === null ? (
                          <input
                            aria-label={r.label + ", с"}
                            placeholder="Разные значения"
                            disabled={!p.editable || !ids(scope).length}
                            onBlur={(e) => {
                              const n = Number(
                                e.target.value.replace(",", "."),
                              );
                              if (e.target.value.trim() && Number.isFinite(n))
                                edit(scope, r.id, Math.round(n * 1000));
                            }}
                          />
                        ) : (
                          <SecondsInput
                            label={r.label + ", с"}
                            value={value}
                            disabled={
                              !p.editable || (selected && !ids(scope).length)
                            }
                            onChange={(v) => edit(scope, r.id, v)}
                          />
                        )}
                        <small className="field-help">
                          {selected
                            ? count
                              ? `Переопределено: ${count}`
                              : "Из общей настройки"
                            : "По умолчанию"}
                        </small>
                      </label>
                    );
                  })}
                {(() => {
                  const a = effective(
                      scope,
                      scope === "node" ? "interval_ms" : "delay_ms",
                    ),
                    b = effective(
                      scope,
                      scope === "node"
                        ? "interval_jitter_ms"
                        : "delay_jitter_ms",
                    );
                  return a !== null && b !== null ? (
                    <div className="effective-range">
                      Диапазон: {(a - b) / 1000}–{(a + b) / 1000} с
                      {(a - b <
                        (scope === "node" && c.algorithm === "homework"
                          ? 810
                          : 1) ||
                        a + b >
                          (scope === "node"
                            ? c.max_gap_ms!
                            : c.max_delay_ms!)) && (
                        <span className="inline-error">
                          {" "}
                          Вне допустимых границ
                        </span>
                      )}
                    </div>
                  ) : null;
                })()}
                <button
                  className="text-button"
                  disabled={!p.editable || !ids(scope).length}
                  onClick={() =>
                    p.onChange((d) => {
                      for (const o of scope === "node" ? d.nodes : d.edges) {
                        const id = "id" in o ? o.id : `${o.source}>${o.target}`;
                        if (ids(scope).includes(id)) o.overrides = {};
                      }
                    })
                  }
                >
                  Сбросить выбранные к общим
                </button>
              </section>
            ))}
            <button className="button full" onClick={() => setDialog("table")}>
              Таблица параметров
            </button>
            <section className="parameter-section">
              <h3>Временные воздействия</h3>
              {(c.episodes ?? []).map((ep) => (
                <div className="episode-card" key={ep.id}>
                  <strong>
                    {ep.kind === "agent_slowdown" ? "Агенты" : "Связи"}: ×
                    {ep.factor}
                  </strong>
                  <small>{ep.targets.join(", ")}</small>
                  <div className="field-row">
                    <label>
                      Начало, с
                      <SecondsInput
                        label={`Начало ${ep.id}`}
                        value={ep.start_ms}
                        disabled={!p.editable}
                        onChange={(v) => updateEpisode(ep.id, { start_ms: v })}
                      />
                    </label>
                    <label>
                      Длительность, с
                      <SecondsInput
                        label={`Длительность ${ep.id}`}
                        value={ep.duration_ms}
                        disabled={!p.editable}
                        onChange={(v) =>
                          updateEpisode(ep.id, { duration_ms: v })
                        }
                      />
                    </label>
                  </div>
                  <label>
                    Множитель
                    <input
                      aria-label={`Множитель ${ep.id}`}
                      type="number"
                      min={2}
                      max={16}
                      value={ep.factor}
                      disabled={!p.editable}
                      onChange={(e) =>
                        updateEpisode(ep.id, { factor: +e.target.value })
                      }
                    />
                  </label>
                  {p.editable && (
                    <>
                      <button
                        className="text-button"
                        disabled={
                          !(ep.kind === "agent_slowdown" ? p.nodes : p.edges)
                            .length
                        }
                        onClick={() =>
                          updateEpisode(ep.id, {
                            targets:
                              ep.kind === "agent_slowdown"
                                ? [...p.nodes]
                                : [...p.edges],
                          })
                        }
                      >
                        Назначить выбранным
                      </button>
                      <button
                        className="text-button danger"
                        onClick={() =>
                          p.onChange((d) => {
                            d.episodes = d.episodes!.filter(
                              (e) => e.id !== ep.id,
                            );
                          })
                        }
                      >
                        Удалить
                      </button>
                    </>
                  )}
                </div>
              ))}
              {p.editable && (
                <div className="field-row">
                  <button
                    className="button small"
                    onClick={() => makeEpisode("agent_slowdown")}
                  >
                    + Агенты
                  </button>
                  <button
                    className="button small"
                    onClick={() => makeEpisode("link_slowdown")}
                  >
                    + Связи
                  </button>
                </div>
              )}
            </section>
          </>
        )}
      </div>
      {dialog === "table" && (
        <Dialog title="Параметры агентов и связей · секунды" onClose={close}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={onlyOverrides}
              onChange={(e) => setOnlyOverrides(e.target.checked)}
            />
            Только переопределения
          </label>
          {(["node", "edge"] as const).map((scope) => (
            <div className="parameter-table" key={scope}>
              <h3>{scope === "node" ? "Агенты" : "Связи"}</h3>
              <table>
                <thead>
                  <tr>
                    <th>Объект</th>
                    {p.registry
                      .filter((r) => r.scope === scope)
                      .map((r) => (
                        <th key={r.id}>{r.label}</th>
                      ))}
                  </tr>
                </thead>
                <tbody>
                  {objects(scope)
                    .filter(
                      (o) => !onlyOverrides || Object.keys(o.overrides).length,
                    )
                    .map((o) => (
                      <tr key={o.id}>
                        <th>{o.label}</th>
                        {p.registry
                          .filter((r) => r.scope === scope)
                          .map((r) => (
                            <td key={r.id}>
                              <SecondsInput
                                label={`${r.label} ${o.id}`}
                                value={Number(
                                  o.overrides[r.id] ?? c.defaults[r.id],
                                )}
                                disabled={!p.editable}
                                onChange={(v) => edit(scope, r.id, v, o.id)}
                              />
                              <small>
                                {Object.hasOwn(o.overrides, r.id)
                                  ? "Индивидуально"
                                  : "Общее"}
                              </small>
                            </td>
                          ))}
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ))}
        </Dialog>
      )}
      {dialog === "configs" && (
        <Dialog title="Конфигурации" onClose={close}>
          {configError && <p role="alert">{configError}</p>}
          <button
            className="button primary"
            disabled={!p.editable}
            onClick={async () => {
              try {
                await api("/configurations", {
                  configuration: c,
                  name: c.name,
                });
                setSavedConfigs(await api("/configurations"));
              } catch (e) {
                setConfigError((e as Error).message);
              }
            }}
          >
            Сохранить текущую конфигурацию
          </button>
          {savedConfigs.map((v) => (
            <div className="config-library-row" key={v.id}>
              <strong>{v.name}</strong>
              <button
                className="button small"
                disabled={!p.editable}
                onClick={() => {
                  p.onChange((d) => {
                    for (const key of Object.keys(d)) delete (d as any)[key];
                    Object.assign(d, structuredClone(v.configuration));
                  });
                  close();
                }}
              >
                Открыть
              </button>
            </div>
          ))}
        </Dialog>
      )}
    </aside>
  );
}
