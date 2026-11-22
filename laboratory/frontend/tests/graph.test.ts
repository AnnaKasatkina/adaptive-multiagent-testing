import { describe, it, expect } from "vitest";
import {
  groupConnections,
  layoutGraph,
  circleLine,
} from "../src/graphTopology";

const triangleBridge = [
  ["1", "2"],
  ["2", "3"],
  ["3", "1"],
  ["3", "4"],
  ["4", "5"],
  ["5", "6"],
  ["6", "4"],
].flatMap(([source, target]) => [
  { source, target },
  { source: target, target: source },
]);

describe("представление направленного графа", () => {
  it("объединяет встречные каналы без потери направлений", () => {
    const groups = groupConnections(triangleBridge);
    expect(groups).toHaveLength(7);
    expect(groups.flatMap((e) => e.directions)).toHaveLength(14);
    expect(groups.every((e) => e.forward && e.reverse)).toBe(true);
  });
  it("сохраняет единственное обратное направление", () => {
    const [pair] = groupConnections([{ source: "2", target: "1" }]);
    expect(pair).toMatchObject({
      source: "1",
      target: "2",
      forward: false,
      reverse: true,
      directions: ["2>1"],
    });
  });
  it("изменение одного направления оставляет одно ребро", () => {
    const edges = groupConnections(
      triangleBridge.filter((e) => e.source !== "3" || e.target !== "4"),
    );
    expect(edges).toHaveLength(7);
    expect(edges.find((e) => e.id === "3~4")).toMatchObject({
      forward: false,
      reverse: true,
    });
  });
});

describe("раскладка и геометрия", () => {
  it("выделяет мост между группами и направляет его горизонтально", () => {
    const positions = layoutGraph(
      ["1", "2", "3", "4", "5", "6"],
      triangleBridge,
    );
    expect(positions["3"].y).toBeCloseTo(positions["4"].y);
    expect(
      Math.max(...["1", "2", "3"].map((id) => positions[id].x)),
    ).toBeLessThan(Math.min(...["4", "5", "6"].map((id) => positions[id].x)));
    expect(positions).toEqual(
      layoutGraph(["1", "2", "3", "4", "5", "6"], triangleBridge),
    );
  });
  it("строит путь по соседству независимо от порядка узлов", () => {
    const positions = layoutGraph(
      ["3", "1", "4", "2"],
      [
        { source: "1", target: "2" },
        { source: "2", target: "3" },
        { source: "3", target: "4" },
      ],
    );
    expect(positions["1"].x).toBeLessThan(positions["2"].x);
    expect(positions["2"].x).toBeLessThan(positions["3"].x);
    expect(positions["3"].x).toBeLessThan(positions["4"].x);
    expect(new Set(Object.values(positions).map((p) => p.y)).size).toBe(1);
  });
  it("размещает произвольный граф, включая незавершённый черновик", () => {
    const links = [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "a", target: "c" },
    ];
    const points = layoutGraph(["a", "b", "c", "d"], links);
    expect(Object.keys(points).sort()).toEqual(["a", "b", "c", "d"]);
    expect(
      Object.values(points).every(
        (p) => Number.isFinite(p.x) && Number.isFinite(p.y),
      ),
    ).toBe(true);
    for (const [i, a] of Object.values(points).entries())
      for (const b of Object.values(points).slice(i + 1))
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(80);
  });
  it("заканчивает линию на границах кругов, а не внутри узлов", () => {
    const line = circleLine({ x: 0, y: 0 }, { x: 200, y: 0 }, 36, 36);
    expect(line).toEqual({ sx: 36, sy: 0, tx: 164, ty: 0 });
  });
});
