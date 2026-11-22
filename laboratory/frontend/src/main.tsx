import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  ReferenceArea,
  Legend,
} from "recharts";
import {
  Play,
  Pause,
  SkipForward,
  GitBranch,
  Download,
  Plus,
  Save,
  FlaskConical,
  Check,
  Layers,
  X,
} from "lucide-react";
import { GraphEditor } from "./GraphEditor";
import { ResizableWorkspace } from "./ResizableWorkspace";
import { SavedHistory } from "./SavedHistory";
import { SettingsPanel } from "./SettingsPanel";
import { ParameterComparison } from "./ParameterComparison";
import { Dialog } from "./ResearchPanels";
import {
  api,
  colors,
  download,
  number,
  statuses,
  type Configuration,
  type Parameter,
  type Run,
  type Tick,
  type SavedComparison,
} from "./types";
import "./style.css";

function App() {
  const [config, setConfig] = useState<Configuration | null>(null),
    [presets, setPresets] = useState<any[]>([]),
    [registry, setRegistry] = useState<Parameter[]>([]),
    [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string | null>(null),
    [snapshot, setSnapshot] = useState<Tick | null>(null),
    [series, setSeries] = useState<any[]>([]),
    [cursor, setCursor] = useState(0),
    [follow, setFollow] = useState(true);
  const [tab, setTab] = useState("context"),
    [plot, setPlot] = useState("values"),
    [error, setError] = useState(""),
    [validation, setValidation] = useState(""),
    [busy, setBusy] = useState(false);
  const [runtime, setRuntime] = useState("core"),
    [seed, setSeed] = useState(7),
    [speed, setSpeed] = useState(10),
    [nodes, setNodes] = useState<string[]>([]),
    [edges, setEdges] = useState<string[]>([]);
  const [stepMode, setStepMode] = useState("step");
  const [compare, setCompare] = useState<string[]>([]),
    [comparisons, setComparisons] = useState<Record<string, any[]>>({}),
    [savedComparisons, setSavedComparisons] = useState<SavedComparison[]>([]);
  const [saveName, setSaveName] = useState("");
  const [dialog, setDialog] = useState<string | null>(null),
    [editorPanel, setEditorPanel] = useState<string | null>(null),
    [forkSeed, setForkSeed] = useState(1000),
    [toast, setToast] = useState("");
  const importRef = useRef<HTMLInputElement>(null),
    configImport = useRef<HTMLInputElement>(null),
    followRef = useRef(true);
  const run = runs.find((r) => r.id === selected),
    editable = !selected,
    running = run?.status === "running";
  const eventTime = config?.schema_version === 2;
  const refresh = useCallback(async () => {
    const [items, pairs] = await Promise.all([
      api<Run[]>("/runs"),
      api<SavedComparison[]>("/comparisons"),
    ]);
    setRuns(items);
    setSavedComparisons(pairs);
  }, []);
  const notify = (text: string) => {
    setToast(text);
    setTimeout(() => setToast(""), 4000);
  };
  const attempt = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    Promise.all([api<any[]>("/presets"), api<Parameter[]>("/parameters")])
      .then(([p, r]) => {
        setPresets(p);
        setRegistry(r);
        setConfig({
          ...p[0].configuration,
          algorithm: "homework",
          horizon_ms: 60000,
        });
      })
      .catch((e) => setError(e.message));
    refresh().catch((e) => setError(e.message));
    const timer = setInterval(() => refresh().catch(() => {}), 2500);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    followRef.current = follow;
  }, [follow]);
  useEffect(() => {
    if (config)
      api<Parameter[]>(`/parameters?version=${config.schema_version}`)
        .then(setRegistry)
        .catch(() => {});
  }, [config?.schema_version]);
  useEffect(() => {
    if (!config) return;
    const timer = setTimeout(
      () =>
        api("/configurations/validate", config)
          .then(() => setValidation(""))
          .catch((e) => setValidation(e.message)),
      250,
    );
    return () => clearTimeout(timer);
  }, [config]);
  useEffect(() => {
    if (!selected) {
      setSnapshot(null);
      setSeries([]);
      setCursor(0);
      return;
    }
    let active = true;
    const load = () =>
      api<any[]>(`/runs/${selected}/series`)
        .then((rows) => {
          if (active) setSeries(rows);
        })
        .catch(() => {});
    load();
    const events = new EventSource(`/api/runs/${selected}/events`);
    events.onmessage = (event) => {
      const value = JSON.parse(event.data);
      if (value.kind === "tick") {
        load();
        setRuns((prev) =>
          prev.map((r) => (r.id === selected ? { ...r, tick: value.tick } : r)),
        );
        if (followRef.current) setCursor(value.tick);
      } else refresh().catch(() => {});
    };
    return () => {
      active = false;
      events.close();
    };
  }, [selected, refresh]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    fetch(`/api/runs/${selected}/state?tick=${cursor}`, {
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((row) => {
        if (row && !controller.signal.aborted) setSnapshot(row);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [selected, cursor, run?.status]);
  const [comparisonMetric, setComparisonMetric] = useState("error_mean");
  const [logScale, setLogScale] = useState(false);
  const comparisonRevision = compare
    .map((id) => `${id}:${runs.find((r) => r.id === id)?.tick}`)
    .join("|");
  useEffect(() => {
    let active = true;
    Promise.all(
      compare.map(async (id) => [id, await api(`/runs/${id}/series`)] as const),
    )
      .then((values) => {
        if (active) setComparisons(Object.fromEntries(values));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [comparisonRevision]);
  const updateConfig = useCallback(
    (fn: (c: Configuration) => void) => {
      if (selected) return;
      setConfig((previous) => {
        if (!previous) return previous;
        const next = structuredClone(previous);
        fn(next);
        return next;
      });
    },
    [selected],
  );
  const onSelection = useCallback((n: string[], e: string[]) => {
    setNodes((old) => (old.join("|") === n.join("|") ? old : n));
    setEdges((old) => (old.join("|") === e.join("|") ? old : e));
  }, []);
  const openRun = (r: Run) => {
    setDialog(null);
    setConfig(structuredClone(r.configuration));
    setSelected(r.id);
    setRuntime(r.runtime);
    setSeed(r.seed);
    setCursor(r.tick);
    setFollow(true);
    if (selected !== r.id) {
      setSnapshot(null);
      setSeries([]);
    }
    setNodes([]);
    setEdges([]);
    setEditorPanel(null);
  };
  const draft = () => {
    if (config?.schema_version === 1) {
      changePreset("bridge", config.nodes.length);
      return;
    }
    setSelected(null);
    setSnapshot(null);
    setFollow(true);
    setNodes([]);
    setEdges([]);
    notify("Конфигурация доступна для редактирования");
  };
  const changePreset = (kind: string, n: number) =>
    attempt(async () => {
      const list = await api<any[]>(`/presets?n=${n}`);
      setPresets(list);
      setConfig((previous) => {
        const next: Configuration = structuredClone(
          list.find((p) => p.id === kind).configuration,
        );
        const initials = new Map(
          previous?.nodes.map((node) => [node.id, node.initial]),
        );
        next.algorithm = previous?.algorithm ?? "homework";
        next.horizon_ms =
          previous?.schema_version === 2
            ? (previous.horizon_ms ?? 60000)
            : 60000;
        for (const node of next.nodes)
          node.initial = initials.get(node.id) ?? node.initial;
        return next;
      });
      setSelected(null);
      setNodes([]);
      setEdges([]);
      setEditorPanel(null);
    });
  const createRun = (action: "start" | "step") =>
    attempt(async () => {
      if (!config) return;
      if (!Number.isSafeInteger(seed) || seed < 0)
        throw new Error("Seed должен быть неотрицательным целым числом.");
      await api("/configurations/validate", config);
      const r = await api<Run>("/runs", {
        configuration: config,
        seed,
        runtime,
        initial_action:
          action === "step" && config.schema_version === 2 ? stepMode : action,
        speed,
      });
      await refresh();
      openRun(r);
      // Older running servers prepare a paused worker. Keep a single-click UI
      // without restarting the user's current in-memory experiments.
      if (r.initial_action === undefined) {
        let ready = r;
        const deadline = Date.now() + 90000;
        while (ready.status === "preparing" && Date.now() < deadline) {
          await new Promise((resolve) => setTimeout(resolve, 100));
          ready = await api<Run>(`/runs/${r.id}`);
        }
        if (ready.status !== "paused")
          throw new Error(ready.error || "Исполнитель не подготовлен");
        await api(`/runs/${r.id}/commands`, { action: "speed", value: speed });
        await api(`/runs/${r.id}/commands`, { action });
        await refresh();
      }
    });
  const command = (action: string, value?: number) =>
    attempt(async () => {
      if (!selected) return;
      await api(`/runs/${selected}/commands`, {
        action,
        ...(value === undefined ? {} : { value }),
      });
      if (action !== "speed") setFollow(true);
      await refresh();
    });
  const addAgent = () =>
    updateConfig((c) => {
      if (c.nodes.length >= 32) return;
      let i = 1;
      while (c.nodes.some((n) => n.id === `a${String(i).padStart(2, "0")}`))
        i++;
      c.nodes.push({
        id: `a${String(i).padStart(2, "0")}`,
        label: String(i),
        initial: 0.5,
        overrides: {},
      });
    });
  const importFile = (file: File, kind: string) =>
    attempt(async () => {
      if (kind === "run") {
        const body = new FormData();
        body.append("file", file);
        const response = await fetch("/api/runs/import", {
          method: "POST",
          body,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail);
        await refresh();
        openRun(data);
      } else {
        const data = JSON.parse(await file.text());
        const checked = await api("/configurations/validate", data);
        draft();
        setConfig(checked.configuration);
      }
    });

  const plotData = useMemo(
    () =>
      series.map((row) => ({ ...row, time: row.time ?? row.tick, ...row.z })),
    [series],
  );
  const compareData = useMemo(() => {
    const map = new Map<number, any>();
    for (const [id, rows] of Object.entries(comparisons))
      for (const row of rows) {
        const point = map.get(row.time ?? row.tick) || {
          time: row.time ?? row.tick,
        };
        point[id] = row[comparisonMetric];
        map.set(row.time ?? row.tick, point);
      }
    const previous: Record<string, number> = {};
    return [...map.values()]
      .sort((a, b) => a.time - b.time)
      .map((row) => {
        for (const id of compare)
          if (row[id] !== undefined) previous[id] = row[id];
        return { ...previous, ...row };
      });
  }, [comparisons, comparisonMetric]);
  const closeDialog = useCallback(() => setDialog(null), []);
  const logarithmic = logScale && plot !== "values";
  const rawChartData = plot === "compare" ? compareData : plotData;
  const numericKeys = plot === "compare" ? compare : ["D", "error_mean"];
  const chartData = logarithmic
    ? rawChartData.map((row) => ({
        ...row,
        _raw: row,
        ...Object.fromEntries(
          numericKeys
            .filter((k) => typeof row[k] === "number")
            .map((k) => [k, Math.max(1e-12, row[k])]),
        ),
      }))
    : rawChartData;
  const positive = chartData.flatMap((row) =>
    numericKeys
      .map((k) => row[k])
      .filter((v) => typeof v === "number" && v > 0),
  );
  const lower = positive.length
    ? 10 **
      Math.floor(
        Math.log10(positive.reduce((m, v) => Math.min(m, v), Infinity)),
      )
    : 1e-12;
  const upper = positive.length
    ? Math.max(
        lower * 10,
        10 **
          Math.ceil(Math.log10(positive.reduce((m, v) => Math.max(m, v), 0))),
      )
    : 1;
  if (!config)
    return (
      <div className="boot">
        <FlaskConical size={30} />
        <p>{error || "Подключаем лабораторию…"}</p>
      </div>
    );
  const latest = run?.tick ?? 0,
    mean = config.nodes.length
      ? config.nodes.reduce((s, n) => s + n.initial, 0) / config.nodes.length
      : 0;
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-mark">
          <FlaskConical size={22} />
        </div>
        <h1>Исследование асинхронного консенсуса</h1>
        <div className="header-spacer" />
        <nav className="header-links">
          <button
            onClick={() => setDialog("history")}
            className={selected ? "has-active-run" : ""}
          >
            <Layers size={15} />
            История исследования
            <span className="history-count">
              {runs.filter((r) => r.saved).length + savedComparisons.length}
            </span>
          </button>
        </nav>
        {run && (
          <button
            className="button"
            disabled={busy || !snapshot || !!run.saved}
            onClick={() => {
              setSaveName(run.name);
              setDialog("save-run");
            }}
          >
            <Save size={15} />
            {run.saved ? "Сохранено" : "Сохранить эксперимент"}
          </button>
        )}
        <button
          className="icon-button"
          title="Скачать конфигурацию"
          aria-label="Скачать конфигурацию"
          onClick={() => download("configuration.json", config)}
        >
          <Download size={17} />
        </button>
        {!editable && (
          <button className="button primary" disabled={busy} onClick={draft}>
            <Plus size={15} />
            Новый эксперимент
          </button>
        )}
      </header>
      {run?.manifest?.search && (
        <details className="search-provenance" style={{padding: "6px 16px", fontSize: "0.85rem"}}>
          <summary>Происхождение · испытание {run.manifest.search.trial}</summary>
          <p>{run.parent && run.parent.after_tick !== undefined
            ? `Продолжение ${run.parent.run_id} после события ${run.parent.after_tick}${run.parent.time_ms !== undefined ? ` (${run.parent.time_ms / 1000} с)` : ""}`
            : run.manifest.search.parent_trial
              ? `Изменение конфигурации испытания ${run.manifest.search.parent_trial}; новое исполнение с начала`
              : "Независимый запуск с начала"}</p>
          <p>Операция: {({branch: "продолжение общего начала", parameters_restart: "изменение параметров", topology_restart: "изменение графа", independent: "новое исполнение"} as Record<string, string>)[run.manifest.search.operation] ?? run.manifest.search.operation}</p>
          {run.manifest.search.outcome && <p data-testid="search-outcome">
            {run.manifest.search.outcome.adverse ? "Наблюдаемое ухудшение: " : "Критерии ухудшения не выполнены"}
            {run.manifest.search.outcome.signature?.split("|").map(k => ({biased_consensus:"согласие на неправильном числе",inaccurate:"неточность к концу наблюдения",transient:"временное нарушение",growth:"рост отклонений",oscillation:"колебания вокруг среднего",outside:"выход за исходный диапазон",relapse:"потеря достигнутой точности"} as Record<string,string>)[k] ?? k).join(" · ")}
          </p>}
          {run.manifest.search.values && <dl>{Object.entries(run.manifest.search.values).map(([key, value]) => (
            <div key={key}><dt style={{display: "inline"}}>{({agent: "Агент", edge: "Направление", interval: "Интервал, мс", delay: "Задержка, мс", first: "Первое обновление, мс", start: "Начало воздействия, мс", duration: "Длительность, мс", factor: "Множитель"} as Record<string,string>)[key] ?? key}: </dt><dd style={{display: "inline", margin: 0}}>{String(value)}</dd></div>
          ))}</dl>}
        </details>
      )}
      {(error || validation || run?.error) && (
        <div className="error-banner" role="alert">
          <span>{error || validation || run?.error}</span>
          {error && (
            <button aria-label="Закрыть ошибку" onClick={() => setError("")}>
              <X size={15} />
            </button>
          )}
        </div>
      )}
      <ResizableWorkspace>
        <SettingsPanel
          config={config}
          registry={registry}
          editable={editable}
          tab={tab}
          onTab={setTab}
          presets={presets}
          onPreset={changePreset}
          onChange={updateConfig}
          nodes={nodes}
          edges={edges}
          onImport={() => configImport.current?.click()}
          onDraft={draft}
          onValues={() => setEditorPanel("values")}
          runtime={runtime}
          onRuntime={setRuntime}
          seed={seed}
          onSeed={setSeed}
        />
        <GraphEditor
          config={config}
          snapshot={snapshot}
          running={!!running}
          editable={editable}
          selected={selected}
          nodes={nodes}
          edges={edges}
          onSelection={onSelection}
          onConfig={setConfig}
          onChange={updateConfig}
          onAdd={addAgent}
          onDraft={draft}
          panel={editorPanel}
          onPanel={setEditorPanel}
        />
      </ResizableWorkspace>
      {!!snapshot?.metrics.algorithm_warning && (
        <div className="finding-banner">
          {snapshot.metrics.algorithm_warning}
        </div>
      )}
      <section className="timeline-panel panel">
        <div className="playback-controls">
          <button
            className="play-button"
            aria-label={running ? "Пауза" : "Старт"}
            disabled={
              busy ||
              (editable
                ? !!validation
                : !run ||
                  !["paused", "running"].includes(run.status) ||
                  !follow)
            }
            onClick={() =>
              editable
                ? createRun("start")
                : command(running ? "pause" : "start")
            }
          >
            {running ? <Pause size={16} /> : <Play size={16} />}
            <span>{running ? "Пауза" : "Старт"}</span>
          </button>
          <button
            className="button small"
            aria-label="Один шаг"
            disabled={
              busy ||
              (editable
                ? !!validation
                : !run || run.status !== "paused" || !follow)
            }
            onClick={() => (editable ? createRun("step") : command(stepMode))}
          >
            <SkipForward size={15} />
            Шаг
          </button>
          {eventTime && (
            <select
              aria-label="Режим шага"
              value={stepMode}
              onChange={(e) => setStepMode(e.target.value)}
            >
              <option value="step">Событие</option>
              <option value="step_update">До обновления</option>
            </select>
          )}
          <button
            className="button small"
            disabled={!run || !["paused", "running"].includes(run.status)}
            onClick={() => command("stop")}
          >
            Остановить
          </button>
          <label className="speed-control">
            Темп
            <select
              aria-label="Темп выполнения"
              value={speed}
              onChange={(e) => {
                setSpeed(+e.target.value);
                if (
                  selected &&
                  run &&
                  ["paused", "running"].includes(run.status)
                )
                  command("speed", +e.target.value);
              }}
            >
              {[1, 5, 10, 30, 120].map((s) => (
                <option key={s} value={s}>
                  {eventTime ? `×${s}` : `${s} такт/с`}
                </option>
              ))}
            </select>
          </label>
          <span className="playback-status">
            <span className={`dot ${running ? "green" : "blue"}`} />
            {run ? statuses[run.status] : "Готов к запуску"}
          </span>
          <span className="playback-spacer" />
          <button
            className="button small"
            disabled={!selected}
            onClick={() => {
              setCursor(latest);
              setFollow(true);
            }}
          >
            К текущему
          </button>
          <button
            className="button branch"
            disabled={!selected || busy}
            onClick={() => {
              setForkSeed(seed + 1000);
              setDialog("fork");
            }}
          >
            <GitBranch size={14} />
            Создать продолжение
          </button>
        </div>
        <div className="timeline-row">
          <span className="tick-label">
            {eventTime ? (
              <>
                Время{" "}
                <strong>{number((snapshot?.time_ms ?? 0) / 1000)} с</strong> /{" "}
                {(config.horizon_ms ?? 0) / 1000} · событие {cursor}
              </>
            ) : (
              <>
                Такт <strong>{cursor}</strong> / {config.horizon}
              </>
            )}
          </span>
          <input
            aria-label="Временная шкала"
            type="range"
            min={0}
            max={Math.max(latest, 1)}
            value={cursor}
            disabled={!selected}
            onChange={(e) => {
              setCursor(+e.target.value);
              setFollow(false);
              if (running)
                api(`/runs/${selected}/commands`, { action: "pause" }).catch(
                  (e) => setError(e.message),
                );
            }}
          />
        </div>
      </section>
      <section className="chart-panel panel">
        <div className="chart-heading">
          <div className="chart-tabs">
            {[
              ["values", "Значения агентов"],
              ["disagreement", "Рассогласование"],
              ["compare", "Сравнение"],
              ["parameters", "Различия параметров"],
            ].map(([key, label]) => (
              <button
                key={key}
                className={plot === key ? "active" : ""}
                onClick={() => setPlot(key)}
              >
                {label}
              </button>
            ))}
          </div>
          {plot === "compare" && (
            <label className="comparison-metric">
              <select
                aria-label="Показатель сравнения"
                value={comparisonMetric}
                onChange={(e) => setComparisonMetric(e.target.value)}
              >
                <option value="error_mean">Ошибка среднего</option>
                <option value="E">Нормированная ошибка E</option>
                <option value="Q">Разброс Q</option>
              </select>
            </label>
          )}
          {(plot === "compare" || plot === "disagreement") && (
            <select
              aria-label="Шкала графика"
              title="В логарифмической шкале значения ниже 10⁻¹² показаны на нижней границе; подсказка сохраняет исходное число."
              value={logScale ? "log" : "linear"}
              onChange={(e) => setLogScale(e.target.value === "log")}
            >
              <option value="linear">Линейная шкала</option>
              <option value="log">Логарифмическая</option>
            </select>
          )}
          {(plot === "compare" || plot === "parameters") &&
            compare.length === 2 && (
              <button
                className="button small"
                onClick={() => {
                  setSaveName("Сравнение");
                  setDialog("save-comparison");
                }}
              >
                Сохранить сравнение
              </button>
            )}
          <span className="chart-meta">
            {plot === "compare"
              ? `${compare.length} записей`
              : selected
                ? `${series.length} наблюдений`
                : "—"}
          </span>
        </div>
        <div className="chart-body">
          {plot === "parameters" ? (
            <ParameterComparison
              runs={runs.filter((r) => compare.includes(r.id))}
              registry={registry}
            />
          ) : (plot === "compare" ? compareData.length : series.length) > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={chartData}
                margin={{ top: 8, right: 25, left: 5, bottom: 0 }}
              >
                <CartesianGrid
                  stroke="#e7ebf2"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                {eventTime &&
                  (config.episodes ?? []).map((ep) => (
                    <ReferenceArea
                      key={ep.id}
                      x1={ep.start_ms / 1000}
                      x2={(ep.start_ms + ep.duration_ms) / 1000}
                      fill="#dbe6f6"
                      fillOpacity={0.35}
                    />
                  ))}
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={[0, "dataMax"]}
                  minTickGap={35}
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  scale={logarithmic ? "log" : "auto"}
                  domain={logarithmic ? [lower, upper] : ["auto", "auto"]}
                  width={62}
                  tickFormatter={number}
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  formatter={(value: any, _name: any, item: any) =>
                    number(item.payload?._raw?.[item.dataKey] ?? value)
                  }
                  contentStyle={{ fontSize: 12, borderRadius: 8 }}
                />
                {plot === "values" && (
                  <ReferenceLine
                    y={mean}
                    stroke="#6c7b8e"
                    strokeDasharray="5 4"
                    label={{
                      value: `Среднее ${number(mean)}`,
                      position: "insideTopRight",
                      fontSize: 11,
                    }}
                  />
                )}
                {plot === "compare" && comparisonMetric === "E" && (
                  <ReferenceLine
                    y={config.epsilon}
                    stroke="#8494a8"
                    strokeDasharray="4 4"
                    label={{
                      value: `Порог ${config.epsilon}`,
                      position: "insideTopRight",
                      fontSize: 10,
                    }}
                  />
                )}
                {plot === "values" ? (
                  config.nodes.map((n, i) => (
                    <Line
                      key={n.id}
                      type="stepAfter"
                      dataKey={n.id}
                      name={`Агент ${n.label || n.id}`}
                      stroke={colors[i % colors.length]}
                      dot={false}
                      strokeWidth={1.8}
                      isAnimationActive={false}
                    />
                  ))
                ) : plot === "compare" ? (
                  compare.map((id, i) => (
                    <Line
                      key={id}
                      type="stepAfter"
                      dataKey={id}
                      name={`${runs.find((r) => r.id === id)?.name || id.slice(0, 5)} · ${id.slice(0, 5)}`}
                      stroke={colors[i % colors.length]}
                      dot={false}
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                  ))
                ) : (
                  <>
                    <Line
                      dataKey="D"
                      type="stepAfter"
                      name="Разброс D"
                      stroke="#3577dd"
                      dot={false}
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                    <Line
                      dataKey="error_mean"
                      type="stepAfter"
                      name="Ошибка среднего"
                      stroke="#cf795f"
                      dot={false}
                      strokeWidth={1.7}
                      isAnimationActive={false}
                    />
                  </>
                )}
                {plot !== "values" && (
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                )}
                <ReferenceLine
                  x={eventTime ? (snapshot?.time_ms ?? 0) / 1000 : cursor}
                  stroke="#bec8d5"
                  strokeDasharray="3 3"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="chart-empty">
              <strong>Нет данных</strong>

              <button
                className="text-button"
                onClick={() => setDialog("history")}
              >
                Выбрать сохранённые запуски
              </button>
            </div>
          )}
        </div>
      </section>
      {dialog === "history" && (
        <Dialog title="История исследования" onClose={closeDialog}>
          <SavedHistory
            runs={runs}
            comparisons={savedComparisons}
            selected={selected}
            compare={compare}
            onOpen={openRun}
            onCompare={(ids) => {
              if (
                new Set(
                  ids.map(
                    (id) =>
                      runs.find((r) => r.id === id)?.configuration
                        .schema_version,
                  ),
                ).size > 1
              ) {
                setError(
                  "Такты и секунды относятся к разным моделям времени. Выберите два запуска одной версии.",
                );
                return;
              }
              setCompare(ids);
              setPlot("compare");
            }}
            onOpenComparison={(value) => {
              const source = runs.find((r) => r.id === value.run_ids[1]);
              if (source) openRun(source);
              setCompare(value.run_ids);
              setComparisonMetric(value.metric);
              setPlot("compare");
              setDialog(null);
              requestAnimationFrame(() =>
                document
                  .querySelector(".chart-panel")
                  ?.scrollIntoView({ block: "start" }),
              );
            }}
            onRemoveRun={(id) =>
              attempt(async () => {
                await api(`/runs/${id}/unsave`, {});
                await refresh();
              })
            }
            onRemoveComparison={(id) =>
              attempt(async () => {
                await api(`/comparisons/${id}/remove`, {});
                await refresh();
              })
            }
            onImport={() => importRef.current?.click()}
            onReplay={() =>
              attempt(async () => {
                const value = await api<Run>(`/runs/${selected}/replay`, {
                  runtime,
                });
                await refresh();
                openRun(value);
              })
            }
            onShowComparison={() => {
              setPlot("compare");
              setDialog(null);
              requestAnimationFrame(() =>
                document
                  .querySelector(".chart-panel")
                  ?.scrollIntoView({ block: "start" }),
              );
            }}
          />
        </Dialog>
      )}
      {(dialog === "save-run" || dialog === "save-comparison") && (
        <Dialog
          title={
            dialog === "save-run"
              ? "Сохранить эксперимент"
              : "Сохранить сравнение"
          }
          onClose={closeDialog}
        >
          <form
            className="save-form"
            onSubmit={(e) => {
              e.preventDefault();
              attempt(async () => {
                if (dialog === "save-run")
                  await api(`/runs/${selected}/save`, { name: saveName });
                else
                  await api("/comparisons", {
                    name: saveName,
                    run_ids: compare,
                    metric: comparisonMetric,
                  });
                await refresh();
                setDialog(null);
              });
            }}
          >
            <label>
              Название
              <input
                aria-label="Название сохраняемого результата"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                required
                maxLength={120}
              />
            </label>
            <button
              type="submit"
              className="button primary"
              disabled={busy || !saveName.trim()}
            >
              Сохранить
            </button>
          </form>
        </Dialog>
      )}
      {dialog === "fork" && (
        <Dialog title="Новое продолжение" onClose={closeDialog}>
          <p>
            Первые {cursor} {eventTime ? "событий" : "тактов"} будут повторены
            точно. Уже назначенные доставки сохранятся. Другой seed изменит
            только ещё не принятые случайные решения.
          </p>
          <label className="fork-seed">
            Seed продолжения
            <input
              aria-label="Seed продолжения"
              type="number"
              value={forkSeed}
              onChange={(e) => setForkSeed(+e.target.value)}
            />
          </label>
          <button
            className="button primary"
            onClick={() =>
              attempt(async () => {
                const r = await api<Run>(`/runs/${selected}/fork`, {
                  after_tick: cursor,
                  seed: forkSeed,
                  runtime,
                });
                await refresh();
                openRun(r);
                setDialog(null);
              })
            }
          >
            Создать ветвь
          </button>
        </Dialog>
      )}
      <input
        hidden
        ref={importRef}
        type="file"
        accept=".zip"
        onChange={(e) => {
          if (e.target.files?.[0]) importFile(e.target.files[0], "run");
          e.target.value = "";
        }}
      />
      <input
        hidden
        ref={configImport}
        type="file"
        accept=".json"
        onChange={(e) => {
          if (e.target.files?.[0]) importFile(e.target.files[0], "config");
          e.target.value = "";
        }}
      />
      {toast && (
        <div className="toast" role="status">
          <Check size={15} />
          {toast}
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
