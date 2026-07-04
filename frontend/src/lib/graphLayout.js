/**
 * @fileoverview Deterministic DFS layout for Graph IR nodes.
 * Pure function: takes a Graph, returns the same graph with node.ui.x and node.ui.y set.
 * Does not mutate node structure beyond the ui fields.
 */

const SPINE_KINDS = new Set(['user-message', 'agent-message'])

/**
 * Compute x/y coordinates for every node in the graph using a DFS traversal
 * in sequence order.  Mutates node.ui.x, node.ui.y, and node.ui.hidden in place
 * on the nodes of the passed-in graph.  Returns the same graph object.
 *
 * @param {object} graph  Graph IR as returned by buildTopicGraph.
 * @param {object} [opts]
 * @param {number} [opts.rowHeight=44]     Vertical pixels per visible row.
 * @param {number} [opts.subtreeGap=8]     Extra vertical gap between sibling subtrees.
 * @param {number} [opts.colWidth=320]     Horizontal pixels per depth level.
 * @param {number} [opts.spineGap=24]      Extra top padding for spine-level nodes.
 * @returns {object} The same graph with node.ui.x / node.ui.y populated.
 */
export function layoutGraph(graph, {
  rowHeight = 44,
  subtreeGap = 8,
  colWidth = 320,
  spineGap = 24,
} = {}) {
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))

  const childrenOf = new Map()
  for (const node of graph.nodes) {
    if (node.parentId !== null) {
      if (!childrenOf.has(node.parentId)) childrenOf.set(node.parentId, [])
      childrenOf.get(node.parentId).push(node)
    }
  }

  for (const [parentId, children] of childrenOf) {
    children.sort((a, b) => a.sequence - b.sequence)
    childrenOf.set(parentId, children)
  }

  let runningY = 0
  const visited = new Set()

  function visit(node, depth, collapsedAncestorY) {
    if (visited.has(node.id)) return false
    visited.add(node.id)
    const isHidden = collapsedAncestorY !== null
    node.ui.hidden = isHidden
    node.ui.x = depth * colWidth

    if (isHidden) {
      node.ui.y = collapsedAncestorY
    } else {
      if (SPINE_KINDS.has(node.kind)) {
        runningY += spineGap
      }
      node.ui.y = runningY
      runningY += rowHeight
    }

    const children = childrenOf.get(node.id) ?? []
    const collapsed = node.ui.collapsed
    const ancestorYForChildren = isHidden
      ? collapsedAncestorY
      : collapsed
        ? node.ui.y
        : null

    let prevChildHadSubtree = false
    for (const child of children) {
      if (!isHidden && !collapsed && prevChildHadSubtree) {
        runningY += subtreeGap
      }
      const childHadSubtree = visit(child, depth + 1, ancestorYForChildren)
      if (!isHidden && !collapsed) {
        prevChildHadSubtree = childHadSubtree || (childrenOf.get(child.id)?.length > 0)
      }
    }

    return children.length > 0
  }

  const roots = graph.nodes.filter(n => n.parentId === null)
  roots.sort((a, b) => a.sequence - b.sequence)
  for (const root of roots) {
    visit(root, 0, null)
  }

  return graph
}
