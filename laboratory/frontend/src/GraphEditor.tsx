import { useCallback, useEffect, useState } from "react";
import {
  Plus,
  Trash2,
  ArrowRight,
  X,
  Table2,
  GitBranch,
  PenLine,
  Network as LayoutIcon,
  Dices,
} from "lucide-react";
import { Network } from "./Network";
import { number, type Configuration, type Tick } from "./types";
import { groupConnections } from "./graphTopology";
import { SecondsInput } from "./EventSettings";

type Props = {
  config: Configuration;
  snapshot: Tick | null;
  running: boolean;
  editable: boolean;
  selected: string | null;
  nodes: string[];
  edges: string[];
  onSelection: (n: string[], e: string[]) => void;
  onConfig: (c: Configuration) => void;
  onChange: (fn: (c: Configuration) => void) => void;
  onAdd: () => void;
  onDraft: () => void;
  panel: string | null;
  onPanel: (v: string | null) => void;
};

export function GraphEditor(p: Props) {
  const c = p.config;
  const [source, setSource] = useState(c.nodes[0]?.id || ""),
    [target, setTarget] = useState(c.nodes[1]?.id || ""),
    [both, setBoth] = useState(false),
    [edgeError, setEdgeError] = useState("");
  const [randomMin, setRandomMin] = useState("0"),
    [randomMax, setRandomMax] = useState("100"),
    [randomError, setRandomError] = useState("");
  const randomizeInitials = () => {
    const min = Number(randomMin),
      max = Number(randomMax);
    if (
      !randomMin.trim() ||
      !randomMax.trim() ||
      !Number.isSafeInteger(min) ||
      !Number.isSafeInteger(max)
    ) {
      setRandomError("Укажите целые числа в обеих границах диапазона.");
      return;
    }
    if (min > max) {
      setRandomError("Минимум должен быть не больше максимума.");
      return;
    }
    const size = max - min + 1;
    if (!Number.isSafeInteger(size)) {
      setRandomError("Диапазон слишком большой. Уменьшите его границы.");
      return;
    }
    const values = c.nodes.map(() => min + Math.floor(Math.random() * size));
    setRandomError("");
    p.onChange((d) => {
      d.nodes.forEach((node, index) => {
        node.initial = values[index];
      });
    });
  };
  useEffect(() => {
    if (!c.nodes.some((n) => n.id === source)) setSource(c.nodes[0]?.id || "");
    if (!c.nodes.some((n) => n.id === target))
      setTarget(c.nodes[1]?.id || c.nodes[0]?.id || "");
  }, [c.nodes, source, target]);
  const initials = useCallback(
    (id: string, value: number) =>
      p.onChange((d) => {
        const n = d.nodes.find((n) => n.id === id);
        if (n) n.initial = value;
      }),
    [p.onChange],
  );
  const selectedCount = p.nodes.length + p.edges.length;
  const remove = () =>
    p.onChange((d) => {
      d.nodes = d.nodes.filter((n) => !p.nodes.includes(n.id));
      d.edges = d.edges.filter(
        (e) =>
          !p.edges.includes(e.source + ">" + e.target) &&
          !p.nodes.includes(e.source) &&
          !p.nodes.includes(e.target),
      );
      p.onSelection([], []);
    });
  const addEdge = () => {
    if (!source || !target || source === target) {
      setEdgeError("Выберите двух разных агентов.");
      return;
    }
    if (
      c.edges.some((e) => e.source === source && e.target === target) &&
      (!both || c.edges.some((e) => e.source === target && e.target === source))
    ) {
      setEdgeError("Такая связь уже есть.");
      return;
    }
    setEdgeError("");
    p.onChange((d) => {
      for (const [a, b] of both
        ? [
            [source, target],
            [target, source],
          ]
        : [[source, target]])
        if (!d.edges.some((e) => e.source === a && e.target === b))
          d.edges.push({ source: a, target: b, overrides: {} });
    });
  };
  const label = (id: string) => c.nodes.find((n) => n.id === id)?.label || id;
  const metric = p.snapshot?.metrics;
  const pairs = groupConnections(c.edges).length;
  const count = (n: number, words: string[]) =>
    `${n} ${words[n % 100 > 10 && n % 100 < 20 ? 2 : n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2]}`;
  return (
    <main className="canvas-panel panel">
      <div className="canvas-toolbar">
        <strong>Сеть взаимодействий</strong>
        <div className="network-counts">
          <span>{count(c.nodes.length, ["агент", "агента", "агентов"])}</span>
          <span>{count(pairs, ["ребро", "ребра", "рёбер"])}</span>
          <span>
            {count(c.edges.length, [
              "направление",
              "направления",
              "направлений",
            ])}
          </span>
        </div>
        <span className="canvas-state">
          {p.editable
            ? "Редактор"
            : c.schema_version === 2
              ? `${number((p.snapshot?.time_ms ?? 0) / 1000)} с`
              : `Такт ${p.snapshot?.tick ?? 0}`}
        </span>
      </div>
      <div className="graph-editor-tools">
        <label className="graph-layout-control">
          <LayoutIcon size={14} />
          <select
            aria-label="Расположение графа"
            value={
              Object.keys(c.view.positions ?? {}).length
                ? "manual"
                : (c.view.layout ?? "structure")
            }
            onChange={(e) => {
              const next = structuredClone(c);
              next.view = {
                ...next.view,
                layout: e.target.value,
                positions: {},
                layout_revision: (next.view.layout_revision ?? 0) + 1,
              };
              p.onConfig(next);
            }}
          >
            <option value="structure">По структуре</option>
            <option value="circle">По кругу</option>
            {(c.view.layout === "manual" ||
              Object.keys(c.view.positions ?? {}).length > 0) && (
              <option value="manual">Вручную</option>
            )}
          </select>
        </label>
        {p.editable ? (
          <>
            <button
              className="button small"
              disabled={c.nodes.length >= 32}
              onClick={p.onAdd}
              aria-label="Добавить агента"
            >
              <Plus size={14} />
              Агент
            </button>
            <button
              className={`button small ${p.panel === "edges" ? "active" : ""}`}
              onClick={() => p.onPanel(p.panel === "edges" ? null : "edges")}
            >
              <GitBranch size={14} />
              Связи
            </button>
            <button
              className={`button small ${p.panel === "values" ? "active" : ""}`}
              onClick={() => p.onPanel(p.panel === "values" ? null : "values")}
            >
              <Table2 size={14} />
              Значения агентов
            </button>
            <button
              className="button small danger"
              disabled={!selectedCount}
              onClick={remove}
              aria-label="Удалить выбранное"
            >
              <Trash2 size={14} />
              {selectedCount
                ? `Удалить (${selectedCount})`
                : "Удалить выбранное"}
            </button>
          </>
        ) : (
          <>
            <button className="button small" onClick={p.onDraft}>
              <PenLine size={14} />
              Редактировать копию
            </button>
            {p.nodes.length === 1 && c.schema_version === 2 && (
              <button
                className="button small"
                onClick={() => p.onPanel(p.panel === "state" ? null : "state")}
              >
                Состояние агента
              </button>
            )}
          </>
        )}
      </div>
      <div className="graph-wrap" data-state-tick={p.snapshot?.tick ?? -1}>
        <Network
          key={`${p.selected || "draft"}:${c.view.layout_revision ?? 0}:${c.nodes.map((n) => n.id).join(",")}:${c.edges.map((e) => e.source + ">" + e.target).join(",")}`}
          config={c}
          snapshot={p.snapshot}
          editable={p.editable}
          running={p.running}
          onConfig={p.onConfig}
          onSelection={p.onSelection}
          focusNodes={p.nodes}
          focusEdges={p.edges}
          onInitial={initials}
        />
        {p.panel && (
          <div
            className="graph-inspector"
            role="region"
            aria-label={
              p.panel === "state"
                ? "Состояние агента"
                : p.panel === "edges"
                  ? "Редактор связей"
                  : "Редактор значений"
            }
          >
            <div className="inspector-heading">
              <strong>
                {p.panel === "state"
                  ? "Состояние агента"
                  : p.panel === "edges"
                    ? "Связи и задержки"
                    : "Значения агентов"}
              </strong>
              <button
                className="icon-button"
                aria-label="Закрыть редактор графа"
                onClick={() => p.onPanel(null)}
              >
                <X size={16} />
              </button>
            </div>
            {p.panel === "state" && p.nodes.length === 1 && p.snapshot ? (
              (() => {
                const a = p.snapshot!.state.agents[p.nodes[0]];
                return (
                  <div className="agent-state-details">
                    <strong>Агент {label(p.nodes[0])}</strong>
                    <dl>
                      <dt>Фаза</dt>
                      <dd>
                        {a.phase === "collecting"
                          ? "Сбор сообщений"
                          : "Ожидание"}
                      </dd>
                      <dt>Следующее обновление</dt>
                      <dd>{number(a.next_update_ms / 1000)} с</dd>
                      <dt>Очередь</dt>
                      <dd>{a.inbox?.length ?? 0}</dd>
                      <dt>Текущая пачка</dt>
                      <dd>{a.batch?.length ?? 0}</dd>
                      <dt>Обновлений</dt>
                      <dd>{a.count}</dd>
                    </dl>
                    {[...(a.batch ?? []), ...(a.inbox ?? [])].map((m: any) => (
                      <p key={m.id}>
                        {m.sender}:{" "}
                        {m.value === undefined
                          ? "Накопленная масса"
                          : number(m.value)}{" "}
                        · отправлено {number(m.sent_ms / 1000)} с
                      </p>
                    ))}
                  </div>
                );
              })()
            ) : p.panel === "edges" ? (
              <>
                <p>
                  Стрелка задаёт, кому отправляются сообщения. Каждое
                  направление настраивается отдельно.
                </p>
                <div className="connection-form">
                  <label>
                    Отправитель
                    <select
                      aria-label="Отправитель связи"
                      disabled={!p.editable}
                      value={source}
                      onChange={(e) => setSource(e.target.value)}
                    >
                      {c.nodes.map((n) => (
                        <option key={n.id} value={n.id}>
                          Агент {n.label || n.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <ArrowRight size={16} />
                  <label>
                    Получатель
                    <select
                      aria-label="Получатель связи"
                      disabled={!p.editable}
                      value={target}
                      onChange={(e) => setTarget(e.target.value)}
                    >
                      {c.nodes.map((n) => (
                        <option key={n.id} value={n.id}>
                          Агент {n.label || n.id}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={both}
                    disabled={!p.editable}
                    onChange={(e) => setBoth(e.target.checked)}
                  />
                  Добавить и обратное направление
                </label>
                <button
                  className="button primary full"
                  disabled={!p.editable}
                  onClick={addEdge}
                >
                  <Plus size={14} />
                  Добавить связь
                </button>
                {edgeError && (
                  <p className="inline-error" role="alert">
                    {edgeError}
                  </p>
                )}
                <div className="edge-table-heading">
                  <span>Направление</span>
                  <span>
                    {c.schema_version === 2
                      ? "Задержка / разброс, с"
                      : "Задержка min / max"}
                  </span>
                  <span />
                </div>
                <div className="edge-table">
                  {c.edges.map((e) => {
                    const id = e.source + ">" + e.target;
                    return (
                      <div
                        className={p.edges.includes(id) ? "selected" : ""}
                        key={id}
                      >
                        <button
                          className="edge-name"
                          onClick={() => p.onSelection([], [id])}
                        >
                          {label(e.source)} → {label(e.target)}
                        </button>
                        <div className="delay-pair">
                          {(c.schema_version === 2
                            ? ["delay_ms", "delay_jitter_ms"]
                            : ["delay_min", "delay_max"]
                          ).map((key) =>
                            c.schema_version === 2 ? (
                              <SecondsInput
                                key={key}
                                label={`${key} ${id}`}
                                value={Number(
                                  e.overrides[key] ?? c.defaults[key],
                                )}
                                disabled={!p.editable}
                                onChange={(v) =>
                                  p.onChange((d) => {
                                    d.edges.find(
                                      (x) =>
                                        x.source === e.source &&
                                        x.target === e.target,
                                    )!.overrides[key] = v;
                                  })
                                }
                              />
                            ) : (
                              <input
                                key={key}
                                aria-label={`${key === "delay_min" ? "Минимальная" : "Максимальная"} задержка ${id}`}
                                type="number"
                                min={1}
                                max={c.max_delay}
                                disabled={!p.editable}
                                value={e.overrides[key] ?? c.defaults[key]}
                                onChange={(event) =>
                                  p.onChange((d) => {
                                    d.edges.find(
                                      (x) =>
                                        x.source === e.source &&
                                        x.target === e.target,
                                    )!.overrides[key] = +event.target.value;
                                  })
                                }
                              />
                            ),
                          )}
                        </div>
                        <button
                          className="icon-button danger"
                          aria-label={`Удалить связь ${id}`}
                          disabled={!p.editable}
                          onClick={() =>
                            p.onChange(
                              (d) =>
                                (d.edges = d.edges.filter(
                                  (x) =>
                                    x.source !== e.source ||
                                    x.target !== e.target,
                                )),
                            )
                          }
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </>
            ) : (
              <>
                {p.editable && (
                  <div
                    className="random-initials"
                    role="group"
                    aria-label="Случайное заполнение агентов"
                  >
                    <strong>Случайные целые</strong>
                    <div className="random-initials-range">
                      <label>
                        От
                        <input
                          aria-label="Минимум случайного значения"
                          type="number"
                          step="1"
                          value={randomMin}
                          onChange={(e) => {
                            setRandomMin(e.target.value);
                            setRandomError("");
                          }}
                        />
                      </label>
                      <label>
                        До
                        <input
                          aria-label="Максимум случайного значения"
                          type="number"
                          step="1"
                          value={randomMax}
                          onChange={(e) => {
                            setRandomMax(e.target.value);
                            setRandomError("");
                          }}
                        />
                      </label>
                    </div>
                    <button
                      className="button full"
                      onClick={randomizeInitials}
                      title="Заполнить всех агентов. Обе границы диапазона включены."
                    >
                      <Dices size={15} />
                      Заполнить случайно
                    </button>
                    {randomError && (
                      <p className="inline-error" role="alert">
                        {randomError}
                      </p>
                    )}
                  </div>
                )}
                <div className="node-value-table">
                  {c.nodes.map((n) => (
                    <label key={n.id}>
                      <span>Агент {n.label || n.id}</span>
                      <input
                        aria-label={`Таблица: начальное значение ${n.id}`}
                        type="number"
                        step="any"
                        disabled={!p.editable}
                        value={n.initial}
                        onChange={(e) => initials(n.id, +e.target.value)}
                      />
                      <span className="field-help">
                        {p.snapshot
                          ? `Сейчас: ${number(p.snapshot.state.agents[n.id]?.z)}`
                          : "Исходное значение"}
                      </span>
                    </label>
                  ))}
                </div>
                <div className="target-average">
                  Целевое среднее{" "}
                  <strong>
                    {number(
                      c.nodes.reduce((sum, n) => sum + n.initial, 0) /
                        c.nodes.length,
                    )}
                  </strong>
                </div>
              </>
            )}
          </div>
        )}
      </div>
      <div className="metrics-strip">
        {[
          ["Разброс оценок D", number(metric?.D)],
          ["Ошибка среднего", number(metric?.error_mean)],
          ["Обновлений", metric?.activations ?? "—"],
          ["Сообщений в пути", metric?.pending ?? "—"],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
    </main>
  );
}
