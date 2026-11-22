import { useEffect, useState, useCallback, useMemo, memo } from "react";
import {
  ReactFlow,
  Controls,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  MarkerType,
  useInternalNode,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type NodeProps,
  type Edge,
  type EdgeProps,
  type NodeChange,
  type EdgeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Configuration, Tick } from "./types";
import { number, colors } from "./types";
import { circleLine, groupConnections, layoutGraph } from "./graphTopology";

type AgentNode = Node<{
  label: string;
  z: number;
  initial: number;
  active: boolean;
  color: string;
  editable: boolean;
  id: string;
  onInitial: (id: string, value: number) => void;
}>;
type ChannelEdge = Edge<{
  directions: string[];
  forward: boolean;
  reverse: boolean;
  count: number;
  forwardCount: number;
  reverseCount: number;
  running: boolean;
  muted: boolean;
  showCount: boolean;
}>;
function InitialValue({
  id,
  value,
  onCommit,
}: {
  id: string;
  value: number;
  onCommit: (id: string, value: number) => void;
}) {
  const [text, setText] = useState(String(value)),
    [invalid, setInvalid] = useState(false);
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const number = Number(text.replace(",", "."));
    if (!text.trim() || !Number.isFinite(number)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    onCommit(id, number);
  };
  return (
    <input
      className="nodrag nopan node-initial"
      style={{
        fontSize: Math.max(12, Math.min(23, 100 / Math.max(text.length, 1))),
      }}
      aria-label={`Начальное значение ${id}`}
      aria-invalid={invalid}
      inputMode="decimal"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setText(String(value));
          setInvalid(false);
          e.currentTarget.blur();
        }
      }}
    />
  );
}
const AgentView = memo(({ data, selected }: NodeProps<AgentNode>) => (
  <div
    className={`graph-agent ${selected ? "selected" : ""} ${data.active ? "is-active" : ""}`}
    style={{ "--agent-color": data.color } as React.CSSProperties}
    title={`Агент ${data.label} · ${data.editable ? "исходное значение" : "оценка"}: ${number(data.z)}`}
  >
    <Handle
      className="graph-anchor"
      type="target"
      position={Position.Left}
      isConnectable={false}
    />
    <span className="graph-agent-id">{data.label}</span>
    {data.editable ? (
      <InitialValue
        id={data.id}
        value={data.initial}
        onCommit={data.onInitial}
      />
    ) : (
      <strong
        className="graph-agent-value"
        style={{
          fontSize: Math.max(
            12,
            Math.min(23, 100 / Math.max(number(data.z).length, 1)),
          ),
        }}
      >
        {number(data.z)}
      </strong>
    )}
    <Handle
      className="graph-anchor"
      type="source"
      position={Position.Right}
      isConnectable={false}
    />
  </div>
));
const nodeTypes = { agent: AgentView };

function CommunicationEdge(p: EdgeProps<ChannelEdge>) {
  const a = useInternalNode(p.source),
    b = useInternalNode(p.target);
  if (!a || !b || !p.data) return null;
  const aw = a.measured.width ?? 72,
    ah = a.measured.height ?? 72;
  const bw = b.measured.width ?? 72,
    bh = b.measured.height ?? 72;
  const line = circleLine(
    {
      x: a.internals.positionAbsolute.x + aw / 2,
      y: a.internals.positionAbsolute.y + ah / 2,
    },
    {
      x: b.internals.positionAbsolute.x + bw / 2,
      y: b.internals.positionAbsolute.y + bh / 2,
    },
    aw / 2 + 8,
    bw / 2 + 8,
  );
  const path = `M ${line.sx},${line.sy} L ${line.tx},${line.ty}`;
  return (
    <>
      <BaseEdge
        id={p.id}
        path={path}
        markerStart={p.markerStart}
        markerEnd={p.markerEnd}
        style={p.style}
        interactionWidth={20}
      />
      {p.data.count > 0 && !p.data.muted && p.data.showCount && (
        <EdgeLabelRenderer>
          <div
            className="edge-message-count"
            style={{
              transform: `translate(-50%, -50%) translate(${(line.sx + line.tx) / 2}px,${(line.sy + line.ty) / 2}px)`,
            }}
            title={`${p.source} → ${p.target}: ${p.data.forwardCount}; ${p.target} → ${p.source}: ${p.data.reverseCount}`}
          >
            {p.data.count}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
const edgeTypes = { communication: CommunicationEdge };

export function Network({
  config,
  snapshot,
  editable,
  onConfig,
  onSelection,
  focusNodes,
  focusEdges,
  onInitial,
}: {
  config: Configuration;
  snapshot: Tick | null;
  editable: boolean;
  running: boolean;
  onConfig: (c: Configuration) => void;
  onSelection: (nodes: string[], edges: string[]) => void;
  focusNodes: string[];
  focusEdges: string[];
  onInitial: (id: string, value: number) => void;
}) {
  const [nodes, setNodes] = useState<AgentNode[]>([]),
    [edges, setEdges] = useState<ChannelEdge[]>([]);
  const topology =
    config.nodes.map((n) => n.id).join(",") +
    "|" +
    config.edges.map((e) => `${e.source}>${e.target}`).join(",");
  const positions = useMemo(
    () =>
      layoutGraph(
        config.nodes.map((n) => n.id),
        config.edges,
        config.view.layout ?? "structure",
      ),
    [topology, config.view.layout],
  );
  useEffect(() => {
    setNodes((old) =>
      config.nodes.map((n, i) => ({
        id: n.id,
        type: "agent",
        position:
          old.find((v) => v.id === n.id)?.position ??
          config.view.positions?.[n.id] ??
          positions[n.id],
        selected: focusNodes.includes(n.id),
        ariaLabel: `Агент ${n.label || i + 1}`,
        data: {
          label: n.label || String(i + 1),
          id: n.id,
          editable,
          onInitial,
          initial: n.initial,
          z: snapshot?.state.agents[n.id]?.z ?? n.initial,
          active:
            !!snapshot &&
            snapshot.tick > 0 &&
            (config.schema_version === 2
              ? snapshot.events.some(
                  (e) => e.kind === "update" && e.agent === n.id,
                )
              : snapshot.state.last_active?.[n.id] === snapshot.tick),
          color: colors[i % colors.length],
        },
      })),
    );
    const pending = new Map<string, number>();
    for (const message of snapshot?.state.pending ?? []) {
      const key = `${message.sender}>${message.receiver}`;
      pending.set(key, (pending.get(key) ?? 0) + 1);
    }
    setEdges(
      groupConnections(config.edges).map((group) => {
        const forwardCount =
          pending.get(`${group.source}>${group.target}`) ?? 0;
        const reverseCount =
          pending.get(`${group.target}>${group.source}`) ?? 0;
        const count = forwardCount + reverseCount,
          selected = group.directions.some((id) => focusEdges.includes(id));
        const related =
          focusNodes.includes(group.source) ||
          focusNodes.includes(group.target);
        const muted = focusNodes.length > 0 && !related && !selected;
        const stroke =
          selected || related ? "#356fae" : count ? "#5386c2" : "#8292a6";
        const marker = {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: stroke,
          orient: "auto-start-reverse",
        };
        return {
          id: group.id,
          source: group.source,
          target: group.target,
          type: "communication",
          selected,
          ariaLabel: `Связь ${group.source} ${group.forward && group.reverse ? "↔" : group.forward ? "→" : "←"} ${group.target}`,
          data: {
            ...group,
            count,
            forwardCount,
            reverseCount,
            running: false,
            muted,
            showCount: config.edges.length <= 96 || related || selected,
          },
          markerStart: group.reverse ? marker : undefined,
          markerEnd: group.forward ? marker : undefined,
          style: {
            stroke,
            strokeWidth: selected || related ? 2 : 1.5,
            opacity: muted ? 0.18 : 1,
          },
        };
      }),
    );
  }, [
    config,
    snapshot,
    editable,
    onInitial,
    focusNodes,
    focusEdges,
    positions,
  ]);
  const selection = useCallback(
    (s: { nodes: AgentNode[]; edges: ChannelEdge[] }) =>
      onSelection(
        s.nodes.map((n) => n.id),
        s.edges.flatMap((e) => e.data?.directions ?? []),
      ),
    [onSelection],
  );
  const changeNodes = useCallback(
    (changes: NodeChange<AgentNode>[]) =>
      setNodes((ns) => applyNodeChanges(changes, ns)),
    [],
  );
  const changeEdges = useCallback(
    (changes: EdgeChange<ChannelEdge>[]) =>
      setEdges((es) => applyEdgeChanges(changes, es)),
    [],
  );
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={changeNodes}
      onEdgesChange={changeEdges}
      onSelectionChange={selection}
      nodesConnectable={false}
      nodesDraggable
      elementsSelectable
      onDelete={({ nodes: removedNodes, edges: removedEdges }) => {
        if (!editable) return;
        const ids = new Set(removedNodes.map((n) => n.id));
        const channels = new Set(
          removedEdges.flatMap((e) => e.data?.directions ?? []),
        );
        const c = structuredClone(config);
        c.nodes = c.nodes.filter((n) => !ids.has(n.id));
        c.edges = c.edges.filter(
          (e) =>
            !ids.has(e.source) &&
            !ids.has(e.target) &&
            !channels.has(`${e.source}>${e.target}`),
        );
        onSelection([], []);
        onConfig(c);
      }}
      deleteKeyCode={editable ? ["Backspace", "Delete"] : null}
      onNodeDragStop={(_, node) => {
        if (!editable) return;
        const c = structuredClone(config);
        c.view.layout = "manual";
        c.view.positions = { ...c.view.positions, [node.id]: node.position };
        onConfig(c);
      }}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1.5 }}
      minZoom={0.12}
      maxZoom={2.5}
      proOptions={{ hideAttribution: true }}
      aria-label="Сеть агентов"
      multiSelectionKeyCode="Control"
    >
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
