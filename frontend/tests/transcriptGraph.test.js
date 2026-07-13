/**
 * Unit tests for frontend/src/lib/transcriptGraph.js
 * Covers automated cases in docs/test-plans/topic-graph-view.md
 *
 * Test IDs in comments map to the plan: HP-01, HP-02, etc.
 * Do not duplicate cases already in transcriptGraph.smoke.test.js.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { buildTopicGraph, NodeKind, DiagnosticCode } from '../src/lib/transcriptGraph.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const FIXTURE_DIR = join(__dirname, '../../tests/fixtures/topic-graph')

function loadFixture(filename) {
  const raw = readFileSync(join(FIXTURE_DIR, filename), 'utf8')
  return raw
    .split('\n')
    .filter(line => line.trim())
    .map(line => JSON.parse(line))
}

function build(messages, extra = {}) {
  return buildTopicGraph({
    workspaceId: 'ws-test',
    topicId: 'topic-test',
    topicSubject: 'Test topic',
    workspaceName: 'Test workspace',
    messages,
    generatedAt: '2026-07-04T00:00:00.000Z',
    ...extra,
  })
}

// ---------------------------------------------------------------------------
// HP-01 Empty topic
// ---------------------------------------------------------------------------
describe('HP-01: empty topic', () => {
  it('graph contains exactly 1 topic node, no edges, no diagnostics', () => {
    const graph = build([])
    expect(graph.nodes).toHaveLength(1)
    expect(graph.nodes[0].kind).toBe(NodeKind.TOPIC)
    expect(graph.edges).toHaveLength(0)
    expect(graph.summaries).toEqual([])
    expect(graph.messageSummaries).toEqual({})
    expect(graph.diagnostics).toEqual([])
    expect(graph.version).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// HP-02 Text-only topic — spine and inner nodes
// ---------------------------------------------------------------------------
describe('HP-02: simple-text.jsonl spine and inner nodes', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('simple-text.jsonl'))
  })

  it('has exactly 5 spine nodes (1 topic + 2 user-message + 2 agent-message)', () => {
    const spineKinds = [NodeKind.TOPIC, NodeKind.USER_MESSAGE, NodeKind.AGENT_MESSAGE]
    const spineNodes = graph.nodes.filter(n => spineKinds.includes(n.kind))
    expect(spineNodes).toHaveLength(5)
  })

  it('first user-message has is_new_session=true and model=claude-opus-4-7', () => {
    const userMsgs = graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE)
    expect(userMsgs[0].data.dispatch.is_new_session).toBe(true)
    expect(userMsgs[0].data.dispatch.model).toBe('claude-opus-4-7')
  })

  it('second user-message has is_new_session=false', () => {
    const userMsgs = graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE)
    expect(userMsgs[1].data.dispatch.is_new_session).toBe(false)
  })

  it('first agent-message subtree: system-init + thinking + text + result-rollup (no rate-limit node, #249)', () => {
    const agentMsgs = graph.nodes.filter(n => n.kind === NodeKind.AGENT_MESSAGE)
    const firstMsgId = agentMsgs[0].messageId
    const children = graph.nodes.filter(n => n.messageId === firstMsgId && n.kind !== NodeKind.AGENT_MESSAGE)

    const kinds = children.map(n => n.kind)
    expect(kinds).toContain(NodeKind.SYSTEM_INIT)
    expect(kinds).toContain(NodeKind.THINKING)
    expect(kinds).toContain(NodeKind.TEXT)
    expect(kinds).toContain(NodeKind.RESULT_ROLLUP)
    expect(children).toHaveLength(4)
  })

  it('second agent-message subtree: thinking + text + result-rollup (no system-init, no rate-limit)', () => {
    const agentMsgs = graph.nodes.filter(n => n.kind === NodeKind.AGENT_MESSAGE)
    const secondMsgId = agentMsgs[1].messageId
    const children = graph.nodes.filter(n => n.messageId === secondMsgId && n.kind !== NodeKind.AGENT_MESSAGE)

    const kinds = children.map(n => n.kind)
    expect(kinds).not.toContain(NodeKind.SYSTEM_INIT)
    expect(kinds).toContain(NodeKind.THINKING)
    expect(kinds).toContain(NodeKind.TEXT)
    expect(kinds).toContain(NodeKind.RESULT_ROLLUP)
    expect(children).toHaveLength(3)
  })

  it('system-init node data.model = claude-opus-4-7', () => {
    const initNode = graph.nodes.find(n => n.kind === NodeKind.SYSTEM_INIT)
    expect(initNode.data.model).toBe('claude-opus-4-7')
  })

  it('first agent-message node data.model extracted from system/init', () => {
    const agentMsgs = graph.nodes.filter(n => n.kind === NodeKind.AGENT_MESSAGE)
    expect(agentMsgs[0].data.model).toBe('claude-opus-4-7')
  })

  it('all node ids are non-null and unique', () => {
    const ids = graph.nodes.map(n => n.id)
    expect(ids.every(id => id != null)).toBe(true)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('sequence values strictly increase across all nodes in DFS order', () => {
    const seqs = graph.nodes.map(n => n.sequence)
    for (let i = 1; i < seqs.length; i++) {
      expect(seqs[i]).toBeGreaterThan(seqs[i - 1])
    }
  })

  it('all summaries arrays are empty, no diagnostics', () => {
    for (const n of graph.nodes) {
      expect(n.summaries).toEqual([])
    }
    expect(graph.diagnostics).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// HP-03 Tool use / tool result pairing — Glob
// ---------------------------------------------------------------------------
describe('HP-03: tool_use/tool_result pairing (Glob)', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('tools-and-subagent.jsonl'))
  })

  it('tool-use node exists with toolName=Glob and toolUseId=toolu_glob_001', () => {
    const tuNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_glob_001')
    expect(tuNode).toBeDefined()
    expect(tuNode.data.toolName).toBe('Glob')
  })

  it('sole-child tool-result is folded into the tool-use node (#249)', () => {
    const tuNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_glob_001')
    const trNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT && n.data.toolUseId === 'toolu_glob_001')
    expect(trNode).toBeUndefined()
    expect(tuNode.data.result).toBeDefined()
    expect(tuNode.data.result.toolUseId).toBe('toolu_glob_001')
    expect(tuNode.data.result.isError).toBe(false)
  })

  it('no dangling edges reference the folded tool-result', () => {
    const nodeIds = new Set(graph.nodes.map(n => n.id))
    for (const e of graph.edges) {
      expect(nodeIds.has(e.source)).toBe(true)
      expect(nodeIds.has(e.target)).toBe(true)
    }
  })
})

// ---------------------------------------------------------------------------
// HP-04 Subagent subtree attachment
// ---------------------------------------------------------------------------
describe('HP-04: subagent subtree attachment (tools-and-subagent.jsonl)', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('tools-and-subagent.jsonl'))
  })

  it('Agent tool-use node exists', () => {
    const agentTu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolName === 'Agent')
    expect(agentTu).toBeDefined()
    expect(agentTu.data.toolUseId).toBe('toolu_agent_001')
  })

  it('subagent node exists with parentId = Agent tool-use id', () => {
    const agentTu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_agent_001')
    const subagent = graph.nodes.find(n => n.kind === NodeKind.SUBAGENT)
    expect(subagent).toBeDefined()
    expect(subagent.parentId).toBe(agentTu.id)
  })

  it('invokes edge from Agent tool-use to subagent node', () => {
    const agentTu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_agent_001')
    const subagent = graph.nodes.find(n => n.kind === NodeKind.SUBAGENT)
    const edge = graph.edges.find(e => e.source === agentTu.id && e.target === subagent.id && e.kind === 'invokes')
    expect(edge).toBeDefined()
  })

  it('subagent-scoped assistant content blocks are children of the subagent node', () => {
    const subagent = graph.nodes.find(n => n.kind === NodeKind.SUBAGENT)
    // Read tool-uses from the subagent scope
    const subToolUses = graph.nodes.filter(n => n.kind === NodeKind.TOOL_USE && n.parentId === subagent.id)
    expect(subToolUses.length).toBeGreaterThanOrEqual(1)
    expect(subToolUses.every(n => n.data.toolName === 'Read')).toBe(true)
  })

  it('task-event nodes (task_started, task_progress, task_notification) are children of subagent', () => {
    const subagent = graph.nodes.find(n => n.kind === NodeKind.SUBAGENT)
    const taskEvents = graph.nodes.filter(n => n.kind === NodeKind.TASK_EVENT && n.parentId === subagent.id)
    const subtypes = taskEvents.map(n => n.data.subtype)
    expect(subtypes).toContain('task_started')
    expect(subtypes).toContain('task_progress')
    expect(subtypes).toContain('task_notification')
  })

  it('Agent tool-result (main-thread rollup) is a child of the tool-use node, not subagent', () => {
    const agentTu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_agent_001')
    const agentTr = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT && n.data.toolUseId === 'toolu_agent_001')
    expect(agentTr).toBeDefined()
    expect(agentTr.parentId).toBe(agentTu.id)
  })

  it('subagent data.agentType populated from task_started.subagent_type', () => {
    const subagent = graph.nodes.find(n => n.kind === NodeKind.SUBAGENT)
    expect(subagent.data.agentType).toBe('Explore')
  })

  it('no diagnostics emitted for valid subagent fixture', () => {
    expect(graph.diagnostics).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// HP-05 result/success rollup node
// ---------------------------------------------------------------------------
describe('HP-05: result/success rollup', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('simple-text.jsonl'))
  })

  it('each agent-message subtree has exactly 1 result-rollup node', () => {
    const agentMsgs = graph.nodes.filter(n => n.kind === NodeKind.AGENT_MESSAGE)
    for (const am of agentMsgs) {
      const rollups = graph.nodes.filter(n => n.kind === NodeKind.RESULT_ROLLUP && n.messageId === am.messageId)
      expect(rollups).toHaveLength(1)
    }
  })

  it('result-rollup data.isError === false', () => {
    const rollups = graph.nodes.filter(n => n.kind === NodeKind.RESULT_ROLLUP)
    for (const r of rollups) {
      expect(r.data.isError).toBe(false)
    }
  })

  it('result-rollup data.cost, durationMs, numTurns match source event', () => {
    const rollups = graph.nodes.filter(n => n.kind === NodeKind.RESULT_ROLLUP)
    // first agent message: total_cost_usd=0.0042, duration_ms=3820, num_turns=1
    expect(rollups[0].data.cost).toBe(0.0042)
    expect(rollups[0].data.durationMs).toBe(3820)
    expect(rollups[0].data.numTurns).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// HP-06 system/init node and model extraction
// ---------------------------------------------------------------------------
describe('HP-06: system/init node and model extraction', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('simple-text.jsonl'))
  })

  it('system-init node exists as child of the first agent-message', () => {
    const agentMsgs = graph.nodes.filter(n => n.kind === NodeKind.AGENT_MESSAGE)
    const firstMsgId = agentMsgs[0].messageId
    const initNode = graph.nodes.find(n => n.kind === NodeKind.SYSTEM_INIT && n.messageId === firstMsgId)
    expect(initNode).toBeDefined()
  })

  it('system-init data.model matches source model field', () => {
    const initNode = graph.nodes.find(n => n.kind === NodeKind.SYSTEM_INIT)
    expect(initNode.data.model).toBe('claude-opus-4-7')
  })

  it('parent agent-message data.model equals system-init model', () => {
    const initNode = graph.nodes.find(n => n.kind === NodeKind.SYSTEM_INIT)
    const agentNode = graph.nodes.find(n => n.id === initNode.parentId)
    expect(agentNode.data.model).toBe(initNode.data.model)
  })
})

// ---------------------------------------------------------------------------
// HP-07 Determinism
// ---------------------------------------------------------------------------
describe('HP-07: determinism', () => {
  it('two runs on the same messages produce identical JSON', () => {
    const messages = loadFixture('simple-text.jsonl')
    const g1 = build(messages)
    const g2 = build(messages)
    expect(JSON.stringify(g1)).toBe(JSON.stringify(g2))
  })

  it('all node ids are identical across two runs', () => {
    const messages = loadFixture('simple-text.jsonl')
    const g1 = build(messages)
    const g2 = build(messages)
    expect(g1.nodes.map(n => n.id)).toEqual(g2.nodes.map(n => n.id))
  })

  it('generatedAt is taken from caller, not Date.now()', () => {
    const messages = loadFixture('simple-text.jsonl')
    const g = build(messages, { generatedAt: '2000-01-01T00:00:00.000Z' })
    expect(g.generatedAt).toBe('2000-01-01T00:00:00.000Z')
  })
})

// ---------------------------------------------------------------------------
// BC-01 MCP tool call
// ---------------------------------------------------------------------------
describe('BC-01: MCP tool call', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('background-and-compaction.jsonl'))
  })

  it('tool-use node with toolName=mcp__notes__list_workspace_notes exists', () => {
    const mcpNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolName === 'mcp__notes__list_workspace_notes')
    expect(mcpNode).toBeDefined()
  })

  it('MCP result is folded into the tool-use node (#249)', () => {
    const mcpNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolName === 'mcp__notes__list_workspace_notes')
    const trNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT && n.data.toolUseId === mcpNode.data.toolUseId)
    expect(trNode).toBeUndefined()
    expect(mcpNode.data.result).toBeDefined()
    expect(mcpNode.data.result.toolUseId).toBe(mcpNode.data.toolUseId)
  })

  it('no diagnostic emitted for MCP tool_use', () => {
    const mcpNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolName === 'mcp__notes__list_workspace_notes')
    const diags = graph.diagnostics.filter(d => d.code === DiagnosticCode.ORPHAN_TOOL_USE && d.message?.includes(mcpNode.data.toolUseId))
    expect(diags).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// BC-02 Background Bash task lifecycle — task_updated patches
// ---------------------------------------------------------------------------
describe('BC-02: background Bash task lifecycle', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('background-and-compaction.jsonl'))
  })

  it('two task-event nodes with subtype=task_updated exist', () => {
    const taskUpdated = graph.nodes.filter(n => n.kind === NodeKind.TASK_EVENT && n.data.subtype === 'task_updated')
    expect(taskUpdated).toHaveLength(2)
  })

  it('task_updated nodes attach to the Bash tool-use subtree', () => {
    const bashTu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolName === 'Bash')
    expect(bashTu).toBeDefined()
    const taskUpdated = graph.nodes.filter(n => n.kind === NodeKind.TASK_EVENT && n.data.subtype === 'task_updated')
    for (const te of taskUpdated) {
      expect(te.parentId).toBe(bashTu.id)
    }
  })

  it('no diagnostic for task_updated events', () => {
    const orphanDiags = graph.diagnostics.filter(d => d.code === DiagnosticCode.ORPHAN_TASK_EVENT)
    expect(orphanDiags).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// BC-03 is_error tool_result
// ---------------------------------------------------------------------------
describe('BC-03: is_error tool_result', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('background-and-compaction.jsonl'))
  })

  it('Bash tool-result has data.isError === true', () => {
    const bashTu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolName === 'Bash')
    expect(bashTu).toBeDefined()
    const bashTr = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT && n.parentId === bashTu.id)
    expect(bashTr).toBeDefined()
    expect(bashTr.data.isError).toBe(true)
  })

  it('no diagnostic emitted for is_error tool_result', () => {
    const diags = graph.diagnostics.filter(d => d.code === DiagnosticCode.ORPHAN_TOOL_RESULT)
    expect(diags).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// BC-04 Context compaction events
// ---------------------------------------------------------------------------
describe('BC-04: context compaction events', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('background-and-compaction.jsonl'))
  })

  it('parser does not throw on compaction events (fixture parses successfully)', () => {
    // If we get here, it did not throw
    expect(graph).toBeDefined()
  })

  it('compaction node exists with correct metadata', () => {
    const compaction = graph.nodes.find(n => n.kind === NodeKind.COMPACTION)
    expect(compaction).toBeDefined()
    expect(compaction.data.trigger).toBe('token_threshold')
    expect(compaction.data.preTokens).toBe(48200)
    expect(compaction.data.postTokens).toBe(12300)
    expect(compaction.data.durationMs).toBe(2140)
    expect(compaction.data.compactResult).toBe('success')
  })

  it('no spurious diagnostics from compaction events', () => {
    expect(graph.diagnostics).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// IE-01 Interrupted agent message — transcript null
// ---------------------------------------------------------------------------
describe('IE-01: interrupted agent message', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('interrupted-and-edge.jsonl'))
  })

  it('agent-message node exists for the interrupted record', () => {
    const interrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && n.data.interrupted)
    expect(interrupted).toBeDefined()
  })

  it('interrupted agent-message has hasTranscript=false', () => {
    const interrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && n.data.interrupted)
    expect(interrupted.data.hasTranscript).toBe(false)
  })

  it('interrupted agent-message has non-null interruptReason', () => {
    const interrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && n.data.interrupted)
    expect(interrupted.data.interruptReason).toBeTruthy()
  })

  it('no result-rollup child for the interrupted agent-message', () => {
    const interrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && n.data.interrupted)
    const rollups = graph.nodes.filter(n => n.kind === NodeKind.RESULT_ROLLUP && n.messageId === interrupted.messageId)
    expect(rollups).toHaveLength(0)
  })

  it('no diagnostic emitted for null transcript', () => {
    // null transcript is expected state; no parse_error or unknown_event_type for it
    const parseDiags = graph.diagnostics.filter(d =>
      d.code === DiagnosticCode.PARSE_ERROR || d.code === DiagnosticCode.UNKNOWN_EVENT_TYPE
    )
    expect(parseDiags).toHaveLength(0)
  })

  it('subsequent records are still parsed (non-interrupted agent-message exists)', () => {
    const nonInterrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && !n.data.interrupted)
    expect(nonInterrupted).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// IE-02 Consecutive user records
// ---------------------------------------------------------------------------
describe('IE-02: consecutive user records', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('interrupted-and-edge.jsonl'))
  })

  it('two consecutive user-message nodes appear in the spine', () => {
    const topicNode = graph.nodes.find(n => n.kind === NodeKind.TOPIC)
    const userMsgs = graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE && n.parentId === topicNode.id)
    expect(userMsgs.length).toBeGreaterThanOrEqual(2)
    // Check that two of them are consecutive by sequence
    const sorted = [...userMsgs].sort((a, b) => a.sequence - b.sequence)
    // find the "stop" user message (text: "stop") followed by "Actually, ..."
    const stopMsg = sorted.find(n => n.data.text === 'stop')
    const continueMsg = sorted.find(n => n.data.text?.startsWith('Actually,'))
    expect(stopMsg).toBeDefined()
    expect(continueMsg).toBeDefined()
    expect(continueMsg.sequence).toBeGreaterThan(stopMsg.sequence)
  })

  it('each consecutive user-message has a distinct id', () => {
    const userMsgs = graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE)
    const ids = userMsgs.map(n => n.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('sequence values are monotonically increasing across all nodes', () => {
    const seqs = graph.nodes.map(n => n.sequence)
    for (let i = 1; i < seqs.length; i++) {
      expect(seqs[i]).toBeGreaterThan(seqs[i - 1])
    }
  })
})

// ---------------------------------------------------------------------------
// IE-03 Double system/init in one agent message (session restart)
// ---------------------------------------------------------------------------
describe('IE-03: double system/init in one agent message', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('interrupted-and-edge.jsonl'))
  })

  it('two system-init nodes in the non-interrupted agent-message', () => {
    const nonInterrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && !n.data.interrupted)
    const initNodes = graph.nodes.filter(n => n.kind === NodeKind.SYSTEM_INIT && n.messageId === nonInterrupted.messageId)
    expect(initNodes).toHaveLength(2)
  })

  it('both system-init nodes have distinct ids', () => {
    const initNodes = graph.nodes.filter(n => n.kind === NodeKind.SYSTEM_INIT)
    const ids = initNodes.map(n => n.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('agent-message data.model is set from system/init (last wins)', () => {
    const nonInterrupted = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE && !n.data.interrupted)
    // Both inits have the same model in the fixture
    expect(nonInterrupted.data.model).toBe('claude-sonnet-4-6')
  })
})

// ---------------------------------------------------------------------------
// NS-01 Two-level subagent chain
// ---------------------------------------------------------------------------
describe('NS-01: two-level nested subagent chain', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('nested-subagent.jsonl'))
  })

  it('two subagent nodes exist', () => {
    const subagents = graph.nodes.filter(n => n.kind === NodeKind.SUBAGENT)
    expect(subagents).toHaveLength(2)
  })

  it('second subagent is a child of the first subagent (two-level nesting)', () => {
    const subagents = graph.nodes.filter(n => n.kind === NodeKind.SUBAGENT).sort((a, b) => a.sequence - b.sequence)
    const outer = subagents[0]
    const inner = subagents[1]
    // inner subagent's parent chain must reach the outer subagent
    // inner subagent's parentId = inner Agent tool-use's id
    // inner Agent tool-use's parentId = outer subagent's id
    const innerAgentTu = graph.nodes.find(n => n.id === inner.parentId)
    expect(innerAgentTu).toBeDefined()
    expect(innerAgentTu.kind).toBe(NodeKind.TOOL_USE)
    expect(innerAgentTu.parentId).toBe(outer.id)
  })

  it('task-event for task-coord-001 attaches to first (outer) subagent', () => {
    const subagents = graph.nodes.filter(n => n.kind === NodeKind.SUBAGENT).sort((a, b) => a.sequence - b.sequence)
    const outerSubagent = subagents[0]
    const coordTaskEvents = graph.nodes.filter(
      n => n.kind === NodeKind.TASK_EVENT && n.data.taskId === 'task-coord-001'
    )
    expect(coordTaskEvents.length).toBeGreaterThan(0)
    for (const te of coordTaskEvents) {
      expect(te.parentId).toBe(outerSubagent.id)
    }
  })

  it('task-event for task-api-audit-001 attaches to second (inner) subagent', () => {
    const subagents = graph.nodes.filter(n => n.kind === NodeKind.SUBAGENT).sort((a, b) => a.sequence - b.sequence)
    const innerSubagent = subagents[1]
    const apiTaskEvents = graph.nodes.filter(
      n => n.kind === NodeKind.TASK_EVENT && n.data.taskId === 'task-api-audit-001'
    )
    expect(apiTaskEvents.length).toBeGreaterThan(0)
    for (const te of apiTaskEvents) {
      expect(te.parentId).toBe(innerSubagent.id)
    }
  })

  it('result-rollup for the outer message attaches to agent-message spine node', () => {
    const agentMsg = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE)
    const rollup = graph.nodes.find(n => n.kind === NodeKind.RESULT_ROLLUP && n.messageId === agentMsg.messageId)
    expect(rollup).toBeDefined()
    expect(rollup.parentId).toBe(agentMsg.id)
  })

  it('no diagnostic emitted for nested-subagent fixture', () => {
    expect(graph.diagnostics).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// FM-01 Orphan tool_result
// ---------------------------------------------------------------------------
describe('FM-01: orphan tool_result', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('malformed.jsonl'))
  })

  it('diagnostic with code=orphan_tool_result exists', () => {
    const diag = graph.diagnostics.find(d => d.code === DiagnosticCode.ORPHAN_TOOL_RESULT)
    expect(diag).toBeDefined()
  })

  it('orphan tool_result diagnostic has messageId set', () => {
    const diag = graph.diagnostics.find(d => d.code === DiagnosticCode.ORPHAN_TOOL_RESULT)
    expect(diag.messageId).toBeDefined()
    expect(diag.messageId).not.toBeNull()
  })

  it('tool-result node is still emitted (not discarded)', () => {
    // The orphan result references toolu_unknown_999
    const trNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT && n.data.toolUseId === 'toolu_unknown_999')
    expect(trNode).toBeDefined()
  })

  it('result-rollup and text nodes in the same message are still produced', () => {
    const rollup = graph.nodes.find(n => n.kind === NodeKind.RESULT_ROLLUP)
    expect(rollup).toBeDefined()
    const textNodes = graph.nodes.filter(n => n.kind === NodeKind.TEXT)
    expect(textNodes.length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// FM-02 Orphan tool_use
// ---------------------------------------------------------------------------
describe('FM-02: orphan tool_use (toolu_orphan_001)', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('malformed.jsonl'))
  })

  it('tool-use node for toolu_orphan_001 is produced', () => {
    const tuNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_orphan_001')
    expect(tuNode).toBeDefined()
  })

  it('no invokes edge from toolu_orphan_001 to any tool-result', () => {
    const tuNode = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE && n.data.toolUseId === 'toolu_orphan_001')
    const outboundEdge = graph.edges.find(e => e.source === tuNode.id && e.kind === 'invokes')
    expect(outboundEdge).toBeUndefined()
  })

  it('diagnostic with code=orphan_tool_use is emitted', () => {
    const diag = graph.diagnostics.find(d => d.code === DiagnosticCode.ORPHAN_TOOL_USE && d.message?.includes('toolu_orphan_001'))
    expect(diag).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// FM-03 Unknown event type
// ---------------------------------------------------------------------------
describe('FM-03: unknown event type (future_event)', () => {
  let graph

  beforeAll(() => {
    graph = build(loadFixture('malformed.jsonl'))
  })

  it('diagnostic with code=unknown_event_type exists', () => {
    const diag = graph.diagnostics.find(d => d.code === DiagnosticCode.UNKNOWN_EVENT_TYPE && d.message?.includes('future_event'))
    expect(diag).toBeDefined()
  })

  it('unknown_event_type diagnostic has eventIndex set', () => {
    const diag = graph.diagnostics.find(d => d.code === DiagnosticCode.UNKNOWN_EVENT_TYPE && d.message?.includes('future_event'))
    expect(diag.eventIndex).toBeDefined()
    expect(typeof diag.eventIndex).toBe('number')
  })

  it('parsing continues after unknown event: result-rollup exists in same message', () => {
    const rollup = graph.nodes.find(n => n.kind === NodeKind.RESULT_ROLLUP)
    expect(rollup).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// FM-04 Unknown assistant content block type
// ---------------------------------------------------------------------------
describe('FM-04: unknown assistant content block type', () => {
  it('parser does not throw on unknown_content_block_type', () => {
    const messages = loadFixture('malformed.jsonl')
    expect(() => build(messages)).not.toThrow()
  })

  it('known blocks (thinking, tool_use) in the same assistant event are still processed', () => {
    const graph = build(loadFixture('malformed.jsonl'))
    const thinkingNodes = graph.nodes.filter(n => n.kind === NodeKind.THINKING)
    const toolUseNodes = graph.nodes.filter(n => n.kind === NodeKind.TOOL_USE)
    expect(thinkingNodes.length).toBeGreaterThan(0)
    expect(toolUseNodes.length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// FM-05 task_started with no matching subagent in index
// ---------------------------------------------------------------------------
describe('FM-05: orphan task_started (no matching Agent tool_use)', () => {
  it('emits orphan_task_event diagnostic and attaches task-event to agent-message', () => {
    const messages = [{
      type: 'user',
      text: 'test',
      transcript: { is_new_session: true, text: 'test', attachments: [] },
    }, {
      type: 'agent',
      id: 'msg-orphan-task',
      agent_name: 'claude',
      text: 'done',
      transcript: [
        {
          type: 'system',
          subtype: 'task_started',
          task_id: 'task-no-subagent-001',
          tool_use_id: 'toolu_nonexistent_agent',
          task_type: 'local_agent',
          subagent_type: 'Explore',
          description: 'orphan test',
          prompt: 'do something',
        },
        {
          type: 'result',
          subtype: 'success',
          is_error: false,
          duration_ms: 100,
          num_turns: 1,
          total_cost_usd: 0.001,
        },
      ],
    }]

    const graph = build(messages)
    const orphanDiag = graph.diagnostics.find(d => d.code === DiagnosticCode.ORPHAN_TASK_EVENT)
    expect(orphanDiag).toBeDefined()

    const agentMsg = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE)
    const taskEvent = graph.nodes.find(n => n.kind === NodeKind.TASK_EVENT)
    expect(taskEvent).toBeDefined()
    expect(taskEvent.parentId).toBe(agentMsg.id)
  })
})

// ---------------------------------------------------------------------------
// FM-06 topic exceeds maxEvents hard cap
// ---------------------------------------------------------------------------
describe('FM-06: over-size event cap', () => {
  it('emits over_size diagnostic when events exceed maxEvents', () => {
    // Build a synthetic agent message with 6 events, cap at 3
    const events = []
    for (let i = 0; i < 6; i++) {
      events.push({
        type: 'assistant',
        parent_tool_use_id: null,
        message: {
          content: [{ type: 'text', text: `text ${i}` }],
        },
      })
    }
    events.push({ type: 'result', subtype: 'success', is_error: false, duration_ms: 100, num_turns: 1, total_cost_usd: 0 })

    const messages = [
      { type: 'user', id: 'msg-user-1', text: 'go', transcript: { is_new_session: true, text: 'go', attachments: [] } },
      { type: 'agent', id: 'msg-agent-1', agent_name: 'claude', text: 'done', transcript: events },
    ]

    const graph = build(messages, { maxEvents: 3 })
    const overSizeDiag = graph.diagnostics.find(d => d.code === DiagnosticCode.OVER_SIZE)
    expect(overSizeDiag).toBeDefined()

    // Spine nodes still present
    const userMsgs = graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE)
    const agentMsgs = graph.nodes.filter(n => n.kind === NodeKind.AGENT_MESSAGE)
    expect(userMsgs).toHaveLength(1)
    expect(agentMsgs).toHaveLength(1)

    // Only 3 events were processed (capped), so at most 3 text nodes
    const textNodes = graph.nodes.filter(n => n.kind === NodeKind.TEXT)
    expect(textNodes.length).toBeLessThanOrEqual(3)
  })
})

// ---------------------------------------------------------------------------
// JSONL string transcript input
// ---------------------------------------------------------------------------
describe('JSONL string transcript input', () => {
  it('parses a JSONL string transcript the same as an array', () => {
    const messages = loadFixture('simple-text.jsonl')
    // Re-encode the agent message transcript as a JSONL string
    const messagesWithJsonl = messages.map(m => {
      if (m.type === 'agent' && Array.isArray(m.transcript)) {
        return { ...m, transcript: m.transcript.map(e => JSON.stringify(e)).join('\n') }
      }
      return m
    })

    const g1 = build(messages)
    const g2 = build(messagesWithJsonl)

    // Same node kinds in same order
    expect(g1.nodes.map(n => n.kind)).toEqual(g2.nodes.map(n => n.kind))
    expect(g1.diagnostics).toHaveLength(0)
    expect(g2.diagnostics).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// NF-01 Performance
// ---------------------------------------------------------------------------
describe('NF-01: performance — 2000 events within 100ms', () => {
  it('parses ~2000 events in under 100ms', () => {
    const events = []
    // 20 agent messages × 100 events each = 2000 events
    for (let t = 0; t < 100; t++) {
      events.push({
        type: 'assistant',
        parent_tool_use_id: null,
        message: { content: [{ type: 'text', text: `text ${t}` }] },
      })
    }
    events.push({ type: 'result', subtype: 'success', is_error: false, duration_ms: 100, num_turns: 1, total_cost_usd: 0 })

    const messages = []
    for (let i = 0; i < 20; i++) {
      messages.push({ type: 'user', id: `u-${i}`, text: 'hi', transcript: { is_new_session: i === 0, text: 'hi', attachments: [] } })
      messages.push({ type: 'agent', id: `a-${i}`, agent_name: 'claude', text: 'hi', transcript: events })
    }

    const start = performance.now()
    build(messages)
    const elapsed = performance.now() - start
    expect(elapsed).toBeLessThan(100)
  })
})

// ---------------------------------------------------------------------------
// NF-02 No Vue imports
// ---------------------------------------------------------------------------
describe('NF-02: no Vue imports in parser', () => {
  it('transcriptGraph.js source does not import from vue', () => {
    const src = readFileSync(
      join(__dirname, '../src/lib/transcriptGraph.js'),
      'utf8'
    )
    expect(src).not.toMatch(/^import\s+.*from\s+['"]vue['"]/m)
  })
})

// ---------------------------------------------------------------------------
// NF-03 Summary slots always empty in v1 parser
// ---------------------------------------------------------------------------
describe('NF-03: summary slots empty in v1', () => {
  it('graph.summaries is empty', () => {
    const graph = build(loadFixture('tools-and-subagent.jsonl'))
    expect(graph.summaries).toEqual([])
  })

  it('graph.messageSummaries values are all empty arrays', () => {
    const graph = build(loadFixture('tools-and-subagent.jsonl'))
    for (const v of Object.values(graph.messageSummaries)) {
      expect(v).toEqual([])
    }
  })

  it('every node.summaries is an empty array', () => {
    const graph = build(loadFixture('tools-and-subagent.jsonl'))
    for (const n of graph.nodes) {
      expect(n.summaries).toEqual([])
    }
  })
})

// ---------------------------------------------------------------------------
// NF-04 All node IDs stable and unique
// ---------------------------------------------------------------------------
describe('NF-04: stable and unique node IDs', () => {
  it('no duplicate node ids', () => {
    const graph = build(loadFixture('nested-subagent.jsonl'))
    const ids = graph.nodes.map(n => n.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('ids are stable across two runs', () => {
    const messages = loadFixture('nested-subagent.jsonl')
    const g1 = build(messages)
    const g2 = build(messages)
    expect(g1.nodes.map(n => n.id)).toEqual(g2.nodes.map(n => n.id))
  })
})

// ---------------------------------------------------------------------------
// RG-01 Regression: MessageOut API rows use `sender`, not `type`
// (bug: graph showed only the topic node for live topics)
// ---------------------------------------------------------------------------
describe('RG-01: MessageOut API row shape with sender field', () => {
  const dispatchPayload = JSON.stringify({
    message_id: 'm1', agent_name: 'claude', adapter: 'claude-code', subagent: null,
    worktree: '/w', branch: 'topic/x', repo_ref: 'master', base_sha: '0'.repeat(40),
    session_id: 'sess-1', is_new_session: true, session_scope: 'topic',
    model: null, system_prompt: null, text: 'hello', attachments: [],
  })
  const agentTranscript = [
    '{"type":"system","subtype":"init","cwd":"/w","session_id":"sess-1","tools":[],"model":"claude-sonnet-4-6"}',
    '{"type":"result","subtype":"success","is_error":false,"duration_ms":10,"num_turns":1,"total_cost_usd":0.01,"usage":{}}',
  ].join('\n')
  const apiRows = [
    { id: 'm1', sender: 'user', agent_name: null, text: 'hello', transcript: dispatchPayload, created_at: '2026-01-01T00:00:00Z', attachments: [] },
    { id: 'm2', sender: 'agent', agent_name: 'claude', text: 'done', transcript: agentTranscript, created_at: '2026-01-01T00:01:00Z', attachments: [] },
    { id: 'm3', sender: 'event', agent_name: null, text: 'cron fired', transcript: dispatchPayload, created_at: '2026-01-01T00:02:00Z', attachments: [] },
  ]

  it('sender=user produces a user-message node on the spine', () => {
    const graph = build(apiRows)
    expect(graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE).length).toBeGreaterThanOrEqual(1)
  })

  it('sender=agent produces an agent-message node with transcript children', () => {
    const graph = build(apiRows)
    const agent = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE)
    expect(agent).toBeDefined()
    expect(graph.nodes.some(n => n.kind === NodeKind.SYSTEM_INIT && n.messageId === 'm2')).toBe(true)
    expect(graph.nodes.some(n => n.kind === NodeKind.RESULT_ROLLUP && n.messageId === 'm2')).toBe(true)
  })

  it('sender=event maps to a user-message node (dispatch prompt)', () => {
    const graph = build(apiRows)
    const users = graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE)
    expect(users.some(n => n.messageId === 'm3')).toBe(true)
  })

  it('unknown sender is not dropped silently: emits diagnostic', () => {
    const graph = build([{ id: 'mX', sender: 'mystery', text: 'x', transcript: null, created_at: '2026-01-01T00:03:00Z' }])
    expect(graph.diagnostics.length).toBeGreaterThanOrEqual(1)
  })

  it('export-log shape with type field still works', () => {
    const graph = build([{ type: 'user', timestamp: '2026-01-01T00:00:00Z', text: 'hi', transcript: { message_id: 'x', text: 'hi', attachments: [] } }])
    expect(graph.nodes.filter(n => n.kind === NodeKind.USER_MESSAGE)).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// RG-02 Compact view regressions (#249)
// ---------------------------------------------------------------------------
describe('RG-02: compact view (#249)', () => {
  function agentRow(events) {
    return {
      id: 'm2', sender: 'agent', agent_name: 'claude', text: 'done',
      created_at: '2026-01-01T00:01:00Z',
      transcript: JSON.stringify(events),
    }
  }

  it('empty thinking and text blocks produce no nodes', () => {
    const graph = build([agentRow([
      { type: 'assistant', parent_tool_use_id: null, message: { role: 'assistant', content: [
        { type: 'thinking', thinking: '' },
        { type: 'thinking', thinking: '   ' },
        { type: 'text', text: '' },
        { type: 'text', text: 'real text' },
      ] } },
    ])])
    expect(graph.nodes.filter(n => n.kind === NodeKind.THINKING)).toHaveLength(0)
    const texts = graph.nodes.filter(n => n.kind === NodeKind.TEXT)
    expect(texts).toHaveLength(1)
    expect(texts[0].data.text).toBe('real text')
  })

  it('rate_limit_event produces no node and no diagnostic', () => {
    const graph = build([agentRow([
      { type: 'rate_limit_event', rate_limit_info: { tier: 'x' } },
    ])])
    expect(graph.nodes.every(n => n.kind !== 'rate-limit')).toBe(true)
    expect(graph.diagnostics).toHaveLength(0)
  })

  it('tool-use with sole tool-result child is folded into one node', () => {
    const graph = build([agentRow([
      { type: 'assistant', parent_tool_use_id: null, message: { role: 'assistant', content: [
        { type: 'tool_use', id: 'toolu_x', name: 'Bash', input: { command: 'true' } },
      ] } },
      { type: 'user', parent_tool_use_id: null, message: { role: 'user', content: [
        { type: 'tool_result', tool_use_id: 'toolu_x', content: 'ok', is_error: false },
      ] } },
    ])])
    expect(graph.nodes.filter(n => n.kind === NodeKind.TOOL_RESULT)).toHaveLength(0)
    const tu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE)
    expect(tu.data.result.contentText).toBe('ok')
    expect(tu.ui.collapsed).toBe(false)
  })

  it('error results are folded too and keep isError', () => {
    const graph = build([agentRow([
      { type: 'assistant', parent_tool_use_id: null, message: { role: 'assistant', content: [
        { type: 'tool_use', id: 'toolu_e', name: 'Bash', input: { command: 'false' } },
      ] } },
      { type: 'user', parent_tool_use_id: null, message: { role: 'user', content: [
        { type: 'tool_result', tool_use_id: 'toolu_e', content: 'boom', is_error: true },
      ] } },
    ])])
    const tu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE)
    expect(tu.data.result.isError).toBe(true)
  })

  it('tool-use with additional children keeps its tool-result as a separate node', () => {
    const graph = build([agentRow([
      { type: 'assistant', parent_tool_use_id: null, message: { role: 'assistant', content: [
        { type: 'tool_use', id: 'toolu_bg', name: 'Bash', input: { command: 'sleep 1', run_in_background: true } },
      ] } },
      { type: 'system', subtype: 'task_started', task_id: 't1', tool_use_id: 'toolu_bg', task_type: 'local_bash', description: 'bg' },
      { type: 'user', parent_tool_use_id: null, message: { role: 'user', content: [
        { type: 'tool_result', tool_use_id: 'toolu_bg', content: 'started', is_error: false },
      ] } },
    ])])
    const tu = graph.nodes.find(n => n.kind === NodeKind.TOOL_USE)
    const tr = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT)
    expect(tr).toBeDefined()
    expect(tr.parentId).toBe(tu.id)
    expect(tu.data.result).toBeUndefined()
  })

  it('orphan tool-result (no matching tool-use) stays a node under the agent message', () => {
    const graph = build([agentRow([
      { type: 'user', parent_tool_use_id: null, message: { role: 'user', content: [
        { type: 'tool_result', tool_use_id: 'toolu_missing', content: 'orphan', is_error: false },
      ] } },
    ])])
    const tr = graph.nodes.find(n => n.kind === NodeKind.TOOL_RESULT)
    expect(tr).toBeDefined()
    const agent = graph.nodes.find(n => n.kind === NodeKind.AGENT_MESSAGE)
    expect(tr.parentId).toBe(agent.id)
  })
})
