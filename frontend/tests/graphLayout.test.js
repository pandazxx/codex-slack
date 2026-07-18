/**
 * Unit tests for frontend/src/lib/graphLayout.js
 * Covers LY-01 through LY-04 from docs/test-plans/topic-graph-view.md.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { buildTopicGraph } from '../src/lib/transcriptGraph.js'
import { layoutGraph } from '../src/lib/graphLayout.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const FIXTURE_DIR = join(__dirname, '../../tests/fixtures/topic-graph')

function loadFixture(filename) {
  const raw = readFileSync(join(FIXTURE_DIR, filename), 'utf8')
  return raw
    .split('\n')
    .filter(line => line.trim())
    .map(line => JSON.parse(line))
}

function buildAndLayout(messages, layoutOpts = {}) {
  const graph = buildTopicGraph({
    workspaceId: 'ws-test',
    topicId: 'topic-test',
    messages,
    generatedAt: '2026-07-04T00:00:00.000Z',
  })
  return layoutGraph(graph, layoutOpts)
}

// ---------------------------------------------------------------------------
// LY-01 Depth-based x coordinates (colWidth=320 per level)
// The topic root is at depth 0 (x=0); user/agent-message are at depth 1
// (x=320, children of topic); inner nodes are at depth 2 (x=640).
// ---------------------------------------------------------------------------
describe('LY-01: x coordinates by depth (colWidth=320)', () => {
  let graph

  beforeAll(() => {
    graph = buildAndLayout(loadFixture('simple-text.jsonl'))
  })

  it('topic node has x=0 (depth 0)', () => {
    const topicNode = graph.nodes.find(n => n.kind === 'topic')
    expect(topicNode.ui.x).toBe(0)
  })

  it('user-message nodes have x=320 (depth 1 — children of topic)', () => {
    const userMsgs = graph.nodes.filter(n => n.kind === 'user-message')
    for (const n of userMsgs) {
      expect(n.ui.x).toBe(320)
    }
  })

  it('agent-message nodes have x=320 (depth 1 — children of topic)', () => {
    const agentMsgs = graph.nodes.filter(n => n.kind === 'agent-message')
    for (const n of agentMsgs) {
      expect(n.ui.x).toBe(320)
    }
  })

  it('thinking/text/system-init/rate-limit nodes have x=640 (depth 2 — children of agent-message)', () => {
    const innerKinds = ['thinking', 'text', 'system-init', 'rate-limit', 'result-rollup']
    const innerNodes = graph.nodes.filter(n => innerKinds.includes(n.kind))
    for (const n of innerNodes) {
      expect(n.ui.x).toBe(640)
    }
  })
})

describe('LY-01: subagent nesting depth', () => {
  let graph

  beforeAll(() => {
    graph = buildAndLayout(loadFixture('tools-and-subagent.jsonl'))
  })

  it('main-thread tool-use nodes (children of agent-message) have x=640 (depth 2)', () => {
    const agentMsg = graph.nodes.find(n => n.kind === 'agent-message')
    const mainThreadToolUse = graph.nodes.filter(n => n.kind === 'tool-use' && n.parentId === agentMsg.id)
    expect(mainThreadToolUse.length).toBeGreaterThan(0)
    for (const n of mainThreadToolUse) {
      expect(n.ui.x).toBe(640)
    }
  })

  it('subagent node inside Agent tool-use has x=960 (depth 3)', () => {
    const subagent = graph.nodes.find(n => n.kind === 'subagent')
    expect(subagent).toBeDefined()
    expect(subagent.ui.x).toBe(960)
  })
})

// ---------------------------------------------------------------------------
// LY-02 y values monotonically increasing for visible nodes
// ---------------------------------------------------------------------------
describe('LY-02: y values monotonically non-decreasing for visible nodes', () => {
  it('visible nodes sorted by sequence have non-decreasing y', () => {
    const graph = buildAndLayout(loadFixture('simple-text.jsonl'))
    const visible = graph.nodes.filter(n => !n.ui.hidden).sort((a, b) => a.sequence - b.sequence)
    for (let i = 1; i < visible.length; i++) {
      expect(visible[i].ui.y).toBeGreaterThanOrEqual(visible[i - 1].ui.y)
    }
  })
})

// ---------------------------------------------------------------------------
// LY-03 Collapsed subtree hides inner nodes
// ---------------------------------------------------------------------------
describe('LY-03: collapsed subtree hides inner nodes', () => {
  it('children of a collapsed agent-message have ui.hidden=true after layout', () => {
    const messages = loadFixture('tools-and-subagent.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws-test',
      topicId: 'topic-test',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })

    // Collapse the agent-message node
    const agentMsg = graph.nodes.find(n => n.kind === 'agent-message')
    agentMsg.ui.collapsed = true

    layoutGraph(graph)

    // All children of the collapsed agent-message should be hidden
    const children = graph.nodes.filter(n => n.parentId === agentMsg.id)
    for (const child of children) {
      expect(child.ui.hidden).toBe(true)
    }

    // The collapsed agent-message itself remains visible
    expect(agentMsg.ui.hidden).toBe(false)
  })

  it('grandchildren of a collapsed agent-message are also hidden', () => {
    const messages = loadFixture('tools-and-subagent.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws-test',
      topicId: 'topic-test',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })

    const agentMsg = graph.nodes.find(n => n.kind === 'agent-message')
    agentMsg.ui.collapsed = true
    layoutGraph(graph)

    // Descendants at any depth should be hidden
    const agentMsgId = agentMsg.id
    const descendants = graph.nodes.filter(n => {
      // Walk parent chain
      let node = n
      while (node.parentId !== null) {
        if (node.parentId === agentMsgId) return true
        const parent = graph.nodes.find(p => p.id === node.parentId)
        if (!parent) break
        node = parent
      }
      return false
    })
    for (const d of descendants) {
      expect(d.ui.hidden).toBe(true)
    }
  })
})

// ---------------------------------------------------------------------------
// LY-04 Deterministic layout
// ---------------------------------------------------------------------------
describe('LY-04: deterministic layout', () => {
  it('two runs on same graph produce identical ui.x and ui.y', () => {
    const messages = loadFixture('tools-and-subagent.jsonl')
    const g1 = buildAndLayout(messages)
    const g2 = buildAndLayout(messages)

    for (let i = 0; i < g1.nodes.length; i++) {
      expect(g1.nodes[i].ui.x).toBe(g2.nodes[i].ui.x)
      expect(g1.nodes[i].ui.y).toBe(g2.nodes[i].ui.y)
    }
  })

  it('layoutGraph returns the same graph object (mutates in place)', () => {
    const messages = loadFixture('simple-text.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws-test',
      topicId: 'topic-test',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const returned = layoutGraph(graph)
    expect(returned).toBe(graph)
  })
})
