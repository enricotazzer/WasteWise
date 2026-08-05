/**
 * A small Fruchterman-Reingold layout, run to completion in one synchronous pass.
 *
 * Deliberately not a dependency: the fridge graph is ~30 nodes and ~100 edges, which this
 * settles in a couple of milliseconds. Pulling in d3-force would add ~30 KB to ship a
 * simulation loop and an animation scheduler neither of which this needs.
 *
 * Seeded from the node ids rather than Math.random, so the same fridge always draws the same
 * shape. A graph that rearranges itself on every render is unreadable — you'd lose the
 * spatial memory that makes "the cheese cluster is bottom-left" useful.
 */

export interface LayoutNode {
  id: number
  x: number
  y: number
}

export interface LayoutEdge {
  source: number
  target: number
  weight: number
}

interface Options {
  width: number
  height: number
  iterations?: number
}

/** Deterministic [0,1) from an integer — a cheap hash, not a real PRNG. */
function seeded(n: number): number {
  const x = Math.sin(n * 12.9898) * 43758.5453
  return x - Math.floor(x)
}

export function layout(
  ids: number[],
  edges: LayoutEdge[],
  { width, height, iterations = 320 }: Options,
): Map<number, LayoutNode> {
  const nodes = new Map<number, LayoutNode>()
  const n = ids.length
  if (n === 0) return nodes

  // Start on a circle rather than at random: a circle has no clumps to escape, so the
  // simulation converges faster and more predictably.
  ids.forEach((id, i) => {
    const angle = (i / n) * Math.PI * 2
    const jitter = 0.85 + seeded(id) * 0.3
    nodes.set(id, {
      id,
      x: width / 2 + Math.cos(angle) * (width / 3) * jitter,
      y: height / 2 + Math.sin(angle) * (height / 3) * jitter,
    })
  })

  if (n === 1) {
    const only = nodes.get(ids[0])!
    only.x = width / 2
    only.y = height / 2
    return nodes
  }

  const area = width * height
  const k = Math.sqrt(area / n) * 0.72 // ideal edge length
  let temperature = width / 6

  const disp = new Map<number, { x: number; y: number }>()

  for (let step = 0; step < iterations; step++) {
    for (const id of ids) disp.set(id, { x: 0, y: 0 })

    // Repulsion: every pair pushes apart. O(n²) is fine at this size.
    for (let i = 0; i < n; i++) {
      const a = nodes.get(ids[i])!
      for (let j = i + 1; j < n; j++) {
        const b = nodes.get(ids[j])!
        let dx = a.x - b.x
        let dy = a.y - b.y
        let dist = Math.hypot(dx, dy)
        if (dist < 0.01) {
          // Coincident nodes have no direction to separate along; nudge them deterministically.
          dx = seeded(a.id + b.id) - 0.5
          dy = seeded(a.id * 2 + b.id) - 0.5
          dist = 0.01
        }
        const force = (k * k) / dist
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        const da = disp.get(a.id)!
        const db = disp.get(b.id)!
        da.x += fx
        da.y += fy
        db.x -= fx
        db.y -= fy
      }
    }

    // Attraction along edges, scaled by weight — products that share many recipes sit closer,
    // which is what makes clusters mean something.
    for (const edge of edges) {
      const a = nodes.get(edge.source)
      const b = nodes.get(edge.target)
      if (!a || !b) continue
      const dx = a.x - b.x
      const dy = a.y - b.y
      const dist = Math.max(0.01, Math.hypot(dx, dy))
      const force = ((dist * dist) / k) * (1 + Math.min(edge.weight, 6) * 0.16)
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      const da = disp.get(a.id)!
      const db = disp.get(b.id)!
      da.x -= fx
      da.y -= fy
      db.x += fx
      db.y += fy
    }

    // Apply, capped by temperature, and keep everything on canvas.
    for (const id of ids) {
      const node = nodes.get(id)!
      const d = disp.get(id)!
      const dist = Math.max(0.01, Math.hypot(d.x, d.y))
      node.x += (d.x / dist) * Math.min(dist, temperature)
      node.y += (d.y / dist) * Math.min(dist, temperature)
      node.x = Math.max(24, Math.min(width - 24, node.x))
      node.y = Math.max(24, Math.min(height - 24, node.y))
    }

    temperature *= 0.975 // cool down so late steps only fine-tune
  }

  return fitToBox(nodes, width, height)
}

/**
 * Rescale the settled layout to fill the canvas.
 *
 * Without this the simulation reliably parks everything in one corner and leaves half the
 * box empty: repulsion pushes the cluster off-centre and nothing ever recentres it. The
 * clamp to the canvas edges then pins it there. Fitting afterwards is much simpler than
 * trying to tune the forces into producing a centred result.
 */
function fitToBox(
  nodes: Map<number, LayoutNode>,
  width: number,
  height: number,
): Map<number, LayoutNode> {
  const pts = [...nodes.values()]
  if (pts.length < 2) return nodes

  const pad = 34 // room for the labels drawn above each node
  const minX = Math.min(...pts.map((p) => p.x))
  const maxX = Math.max(...pts.map((p) => p.x))
  const minY = Math.min(...pts.map((p) => p.y))
  const maxY = Math.max(...pts.map((p) => p.y))

  const spanX = Math.max(1, maxX - minX)
  const spanY = Math.max(1, maxY - minY)
  // One scale for both axes — scaling them independently would stretch the graph and
  // destroy the distance relationship that makes clusters mean anything.
  const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY)

  const offsetX = (width - spanX * scale) / 2
  const offsetY = (height - spanY * scale) / 2
  for (const p of pts) {
    p.x = (p.x - minX) * scale + offsetX
    p.y = (p.y - minY) * scale + offsetY
  }
  return nodes
}
