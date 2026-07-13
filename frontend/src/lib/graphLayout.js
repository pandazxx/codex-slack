/**
 * @fileoverview Deterministic DFS layout for Graph IR nodes.
 * Pure function: takes a Graph, returns the same graph with node.ui.x and node.ui.y set.
 * Does not mutate node structure beyond the ui fields.
 */

const SPINE_KINDS = new Set(['user-message', 'agent-message'])

const HEADER_HEIGHT = 34
const PREVIEW_LINE_HEIGHT = 15
const PREVIEW_CHARS_PER_LINE = 44
const MAX_PREVIEW_LINES = 3

const PREVIEW_TEXT_BY_KIND = {
  'user-message': d => d.text,
  'agent-message': d => d.text,
  'thinking': d => d.text,
  'text': d => d.text,
  'tool-use': d => d.label,
  'tool-result': d => d.contentText,
  'subagent': d => d.prompt,
}

function previewLines(text, cap = MAX_PREVIEW_LINES) {
  if (!text) return 0
  const len = Math.min(String(text).length, cap * PREVIEW_CHARS_PER_LINE)
  return Math.min(cap, Math.ceil(len / PREVIEW_CHARS_PER_LINE))
}

/**
 * Estimate the rendered pixel height of a node card so rows don't overlap.
 * Mirrors the CSS line clamps in graph-node.css (3-line previews, 2-line
 * inline tool results).
 *
 * @param {object} node  Graph IR node.
 * @returns {number} Estimated height in pixels.
 */
export function estimateNodeHeight(node) {
  const getter = PREVIEW_TEXT_BY_KIND[node.kind]
  const text = getter ? getter(node.data ?? {}) ?? '' : ''
  let height = HEADER_HEIGHT + previewLines(text) * PREVIEW_LINE_HEIGHT
  if (node.kind === 'tool-use' && node.data?.result) {
    const resultText = node.data.result.contentText ?? ''
    height += Math.max(1, previewLines(resultText, 2)) * PREVIEW_LINE_HEIGHT + 4
  }
  return height
}

/**
 * Compute x/y coordinates for every node in the graph using a DFS traversal
 * in sequence order.  Mutates node.ui.x, node.ui.y, and node.ui.hidden in place
 * on the nodes of the passed-in graph.  Returns the same graph object.
 *
 * @param {object} graph  Graph IR as returned by buildTopicGraph.
 * @param {object} [opts]
 * @param {number} [opts.rowGap=10]        Vertical gap between consecutive rows.
 * @param {number} [opts.subtreeGap=8]     Extra vertical gap between sibling subtrees.
 * @param {number} [opts.colWidth=320]     Horizontal pixels per depth level.
 * @param {number} [opts.spineGap=24]      Extra top padding for spine-level nodes.
 * @param {function} [opts.nodeHeight]     Node → pixel height. Defaults to estimateNodeHeight.
 * @returns {object} The same graph with node.ui.x / node.ui.y populated.
 */
export function layoutGraph(graph, {
  rowGap = 10,
  subtreeGap = 8,
  colWidth = 320,
  spineGap = 24,
  nodeHeight = estimateNodeHeight,
} = {}) {
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
      runningY += nodeHeight(node) + rowGap
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
