export type Values = Record<string, string | number>;
export interface Agent {
  id: string;
  label: string;
  initial: number;
  overrides: Values;
}
export interface Link {
  source: string;
  target: string;
  overrides: Values;
}
export interface Configuration {
  algorithm?: "raps" | "homework" | "homework-original";
  schema_version: number;
  name: string;
  nodes: Agent[];
  edges: Link[];
  horizon: number;
  max_gap: number;
  max_delay: number;
  epsilon: number;
  defaults: Values;
  view: Record<string, any>;
  time_model?: string;
  horizon_ms?: number;
  max_gap_ms?: number;
  max_delay_ms?: number;
  episodes?: Episode[];
  schedule?: {
    updates?: Record<string, number[]>;
    delays?: Record<string, number[]>;
  } | null;
}
export interface Episode {
  id: string;
  kind: "agent_slowdown" | "link_slowdown";
  targets: string[];
  start_ms: number;
  duration_ms: number;
  factor: number;
}
export interface Parameter {
  id: string;
  label: string;
  description: string;
  kind: "category" | "float" | "int";
  scope: "node" | "edge";
  default: string | number;
  choices?: string[];
  minimum?: number;
  maximum?: number;
  maximum_ref?: string;
  active_when?: Values;
}
export interface Run {
  manifest?: { configuration_hash?: string; model_version?: string; search?: { trial: number; operation: string; parent_trial?: number | null; values?: Record<string, unknown> | null; outcome?: { adverse: boolean; signature: string | null; outcome: string; E: number; Q: number }; novelty?: number | null } };
  saved?: boolean;
  initial_action?: string;
  id: string;
  name: string;
  status: string;
  tick: number;
  runtime: string;
  seed: number;
  configuration: Configuration;
  parent?: { run_id: string; after_tick: number; kind: string; time_ms?: number };
  summary: Record<string, any>;
  error?: string;
}
export interface SavedComparison {
  id: string;
  name: string;
  run_ids: string[];
  metric: string;
  created: number;
}
export interface Tick {
  tick: number;
  time_ms?: number;
  time?: number;
  state_hash: string;
  state: {
    agents: Record<string, any>;
    pending: any[];
    inboxes: Record<string, any[]>;
    last_active: Record<string, number>;
  };
  events: any[];
  metrics: Record<string, any>;
}
export interface Variable {
  name: string;
  kind: string;
  minimum?: number;
  maximum?: number;
  choices?: string[];
  default: string | number;
  targets: { scope: string; id: string; parameter: string }[];
}
export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    "/api" + path,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
export function download(name: string, data: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const colors = [
  "#3577dd",
  "#e39338",
  "#24a28a",
  "#9b73d2",
  "#d5759b",
  "#559bb6",
  "#719b4c",
  "#cf795f",
];
export function number(v: unknown) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return n === 0
    ? "0"
    : Math.abs(n) < 0.001 || Math.abs(n) >= 100000
      ? n.toExponential(2)
      : n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}
export const statuses: Record<string, string> = {
  preparing: "Подготовка",
  paused: "На паузе",
  running: "Выполняется",
  completed: "Завершён",
  stopped: "Остановлен",
  failed: "Ошибка",
  interrupted: "Прерван",
  replaying: "Повтор префикса",
};
