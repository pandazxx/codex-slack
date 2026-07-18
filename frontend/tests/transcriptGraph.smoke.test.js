import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { buildTopicGraph } from '../src/lib/transcriptGraph.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const FIXTURE_DIR = join(__dirname, '../../tests/fixtures/topic-graph')

function loadFixture(filename) {
  const raw = readFileSync(join(FIXTURE_DIR, filename), 'utf8')
  return raw
    .split('\n')
    .filter(line => line.trim())
    .map(line => JSON.parse(line))
}

const FIXTURES = [
  'simple-text.jsonl',
  'tools-and-subagent.jsonl',
  'background-and-compaction.jsonl',
  'interrupted-and-edge.jsonl',
  'nested-subagent.jsonl',
  'malformed.jsonl',
]

describe('buildTopicGraph smoke tests', () => {
  for (const filename of FIXTURES) {
    it(`does not throw and returns a valid graph for ${filename}`, () => {
      const messages = loadFixture(filename)
      let graph
      expect(() => {
        graph = buildTopicGraph({
          workspaceId: 'ws-test',
          topicId: 'topic-test',
          topicSubject: 'Test topic',
          workspaceName: 'Test workspace',
          messages,
          generatedAt: '2026-07-04T00:00:00.000Z',
        })
      }).not.toThrow()

      expect(graph).toBeDefined()
      expect(Array.isArray(graph.nodes)).toBe(true)
      expect(Array.isArray(graph.diagnostics)).toBe(true)
      expect(graph.nodes.length).toBeGreaterThan(0)
      expect(graph.version).toBe(1)
    })
  }

  it('returns empty nodes array (only topic root) for empty messages', () => {
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages: [],
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    expect(graph.nodes).toHaveLength(1)
    expect(graph.nodes[0].kind).toBe('topic')
    expect(graph.diagnostics).toHaveLength(0)
  })

  it('handles interrupted agent message with null transcript', () => {
    const messages = loadFixture('interrupted-and-edge.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const interrupted = graph.nodes.find(n => n.kind === 'agent-message' && n.data.interrupted)
    expect(interrupted).toBeDefined()
    expect(interrupted.data.hasTranscript).toBe(false)
  })

  it('emits diagnostics for orphan tool results in malformed fixture', () => {
    const messages = loadFixture('malformed.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const orphanResult = graph.diagnostics.find(d => d.code === 'orphan_tool_result')
    expect(orphanResult).toBeDefined()
  })

  it('emits diagnostics for unknown event types in malformed fixture', () => {
    const messages = loadFixture('malformed.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const unknownEvent = graph.diagnostics.find(d => d.code === 'unknown_event_type')
    expect(unknownEvent).toBeDefined()
  })

  it('emits diagnostics for orphan tool_use (toolu_orphan_001) in malformed fixture', () => {
    const messages = loadFixture('malformed.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const orphanUse = graph.diagnostics.find(d => d.code === 'orphan_tool_use')
    expect(orphanUse).toBeDefined()
  })

  it('parses MCP tool names and splits server/tool', () => {
    const messages = loadFixture('background-and-compaction.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const mcpNode = graph.nodes.find(n => n.kind === 'tool-use' && n.data.mcpServer)
    expect(mcpNode).toBeDefined()
    expect(mcpNode.data.mcpServer).toBe('notes')
    expect(mcpNode.data.mcpTool).toBe('list_workspace_notes')
    expect(mcpNode.data.toolName).toBe('mcp__notes__list_workspace_notes')
  })

  it('last system/init wins for agent-message model', () => {
    const messages = loadFixture('interrupted-and-edge.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const agentMsg = graph.nodes.find(n => n.kind === 'agent-message' && !n.data.interrupted)
    expect(agentMsg).toBeDefined()
    expect(agentMsg.data.model).toBe('claude-sonnet-4-6')
    const initNodes = graph.nodes.filter(n => n.kind === 'system-init' && n.messageId === agentMsg.messageId)
    expect(initNodes.length).toBe(2)
  })

  it('creates subagent container for Agent tool_use', () => {
    const messages = loadFixture('tools-and-subagent.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const subagent = graph.nodes.find(n => n.kind === 'subagent')
    expect(subagent).toBeDefined()
    const invokesEdge = graph.edges.find(e => e.target === subagent.id && e.kind === 'invokes')
    expect(invokesEdge).toBeDefined()
  })

  it('creates nested subagent containers for nested Agent tool_uses', () => {
    const messages = loadFixture('nested-subagent.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const subagents = graph.nodes.filter(n => n.kind === 'subagent')
    expect(subagents.length).toBeGreaterThanOrEqual(2)
  })

  it('emits compaction node and folds status into it', () => {
    const messages = loadFixture('background-and-compaction.jsonl')
    const graph = buildTopicGraph({
      workspaceId: 'ws',
      topicId: 'topic',
      messages,
      generatedAt: '2026-07-04T00:00:00.000Z',
    })
    const compaction = graph.nodes.find(n => n.kind === 'compaction')
    expect(compaction).toBeDefined()
    expect(compaction.data.trigger).toBe('token_threshold')
    expect(compaction.data.compactResult).toBe('success')
  })

  it('deterministic: same input produces same node count and ids', () => {
    const messages = loadFixture('tools-and-subagent.jsonl')
    const g1 = buildTopicGraph({ workspaceId: 'ws', topicId: 't', messages, generatedAt: 'X' })
    const g2 = buildTopicGraph({ workspaceId: 'ws', topicId: 't', messages, generatedAt: 'X' })
    expect(g1.nodes.length).toBe(g2.nodes.length)
    expect(g1.nodes.map(n => n.id)).toEqual(g2.nodes.map(n => n.id))
  })
})
