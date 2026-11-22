export type Link = { source: string; target: string };
export type Point = { x: number; y: number };
export type ConnectionGroup = {
  id: string;
  source: string;
  target: string;
  forward: boolean;
  reverse: boolean;
  directions: string[];
};

/** One drawn edge per pair; the two directed channels remain independent. */
export function groupConnections(links: Link[]): ConnectionGroup[] {
  const groups = new Map<string, ConnectionGroup>();
  for (const link of links) {
    const [source, target] = [link.source, link.target].sort();
    const id = `${source}~${target}`;
    const group = groups.get(id) ?? {
      id,
      source,
      target,
      forward: false,
      reverse: false,
      directions: [],
    };
    if (link.source === source) group.forward = true;
    else group.reverse = true;
    group.directions.push(`${link.source}>${link.target}`);
    groups.set(id, group);
  }
  return [...groups.values()];
}

function circle(
  ids: string[],
  radius: number,
  cx: number,
  cy: number,
  phase = -Math.PI / 2,
) {
  return Object.fromEntries(
    ids.map((id, i) => [
      id,
      {
        x: cx + radius * Math.cos(phase + (2 * Math.PI * i) / ids.length),
        y: cy + radius * Math.sin(phase + (2 * Math.PI * i) / ids.length),
      },
    ]),
  );
}

/** Layout uses topology, never the experiment name or numerical parameters. */
export function layoutGraph(
  ids: string[],
  links: Link[],
  mode = "structure",
): Record<string, Point> {
  if (!ids.length) return {};
  const radius = Math.max(165, ids.length * 20);
  if (mode === "circle") return circle(ids, radius, radius + 60, radius + 60);
  const pairs = groupConnections(links);
  const neighbours = Object.fromEntries(
    ids.map((id) => [id, new Set<string>()]),
  );
  for (const e of pairs) {
    neighbours[e.source]?.add(e.target);
    neighbours[e.target]?.add(e.source);
  }
  const component = (start: string, excluded?: string) => {
    const found = new Set([start]),
      queue = [start];
    for (let i = 0; i < queue.length; i++)
      for (const next of neighbours[queue[i]]) {
        if ([queue[i], next].sort().join("~") === excluded || found.has(next))
          continue;
        found.add(next);
        queue.push(next);
      }
    return [...found];
  };
  // A path is drawn as a path; a cycle follows its actual adjacency order.
  const ends = ids.filter((id) => neighbours[id].size === 1);
  if (
    ids.every((id) => neighbours[id].size <= 2) &&
    component(ids[0]).length === ids.length
  ) {
    const order: string[] = [],
      seen = new Set<string>();
    let next: string | undefined = ends[0] ?? ids[0];
    while (next && !seen.has(next)) {
      order.push(next);
      seen.add(next);
      next = [...neighbours[next]].find((id) => !seen.has(id));
    }
    if (ends.length === 2)
      return Object.fromEntries(
        order.map((id, i) => [id, { x: 50 + i * 150, y: 180 }]),
      );
    return circle(order, radius, radius + 60, radius + 60);
  }
  const center = ids.find(
    (id) =>
      neighbours[id].size === ids.length - 1 &&
      ids.every((other) => other === id || neighbours[other].size === 1),
  );
  if (center)
    return {
      ...circle(
        ids.filter((id) => id !== center),
        radius,
        radius + 60,
        radius + 60,
      ),
      [center]: { x: radius + 60, y: radius + 60 },
    };
  // Reveal a genuine bridge between two groups, with its endpoints facing each other.
  const cuts = pairs
    .map((edge) => ({ edge, left: component(edge.source, edge.id) }))
    .filter(
      (c) =>
        !c.left.includes(c.edge.target) &&
        c.left.length >= 2 &&
        ids.length - c.left.length >= 2,
    )
    .sort(
      (a, b) =>
        Math.abs(ids.length - 2 * a.left.length) -
        Math.abs(ids.length - 2 * b.left.length),
    );
  if (cuts.length) {
    const { edge, left } = cuts[0],
      right = ids.filter((id) => !left.includes(id));
    const a = [edge.source, ...left.filter((id) => id !== edge.source)];
    const b = [edge.target, ...right.filter((id) => id !== edge.target)];
    const r1 = Math.max(145, a.length * 19),
      r2 = Math.max(145, b.length * 19),
      y = Math.max(r1, r2) + 60;
    return {
      ...circle(a, r1, r1 + 50, y, 0),
      ...circle(b, r2, 2 * r1 + r2 + 270, y, Math.PI),
    };
  }
  if (pairs.length === (ids.length * (ids.length - 1)) / 2)
    return circle(ids, radius, radius + 60, radius + 60);
  // Deterministic spring layout for custom networks.
  const points = circle(ids, radius, 0, 0),
    spacing = 180;
  for (let step = 0; step < 320; step++) {
    const forces = Object.fromEntries(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let i = 0; i < ids.length; i++)
      for (let j = i + 1; j < ids.length; j++) {
        const a = ids[i],
          b = ids[j],
          dx = points[a].x - points[b].x,
          dy = points[a].y - points[b].y;
        const distance = Math.max(1, Math.hypot(dx, dy)),
          strength = (spacing * spacing) / distance;
        const fx = (dx / distance) * strength,
          fy = (dy / distance) * strength;
        forces[a].x += fx;
        forces[a].y += fy;
        forces[b].x -= fx;
        forces[b].y -= fy;
      }
    for (const e of pairs) {
      const dx = points[e.source].x - points[e.target].x,
        dy = points[e.source].y - points[e.target].y;
      const distance = Math.max(1, Math.hypot(dx, dy)),
        strength = (distance * distance) / spacing;
      const fx = (dx / distance) * strength,
        fy = (dy / distance) * strength;
      forces[e.source].x -= fx;
      forces[e.source].y -= fy;
      forces[e.target].x += fx;
      forces[e.target].y += fy;
    }
    const temperature = 22 * (1 - step / 320) + 0.1;
    for (const id of ids) {
      const f = forces[id],
        length = Math.max(1, Math.hypot(f.x, f.y));
      points[id].x += (f.x / length) * Math.min(length, temperature);
      points[id].y += (f.y / length) * Math.min(length, temperature);
    }
  }
  const minX = Math.min(...ids.map((id) => points[id].x)),
    minY = Math.min(...ids.map((id) => points[id].y));
  return Object.fromEntries(
    ids.map((id) => [
      id,
      { x: points[id].x - minX + 50, y: points[id].y - minY + 50 },
    ]),
  );
}

export function circleLine(
  a: Point,
  b: Point,
  sourceRadius: number,
  targetRadius: number,
) {
  const length = Math.max(1, Math.hypot(b.x - a.x, b.y - a.y));
  const x = (b.x - a.x) / length,
    y = (b.y - a.y) / length;
  return {
    sx: a.x + x * sourceRadius,
    sy: a.y + y * sourceRadius,
    tx: b.x - x * targetRadius,
    ty: b.y - y * targetRadius,
  };
}
