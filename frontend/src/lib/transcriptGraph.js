/**
 * @fileoverview Pure parser: MessageOut[] → Graph IR.
 * No Vue imports, no DOM, no side effects.
 *
 * @typedef {'topic'|'user-message'|'agent-message'|'result-rollup'|'thinking'|'text'|'tool-use'|'tool-result'|'subagent'|'task-event'|'compaction'|'rate-limit'|'system-init'|'parse-warning'} NodeKind
 * @typedef {'contains'|'invokes'|'follows'|'summarizes'} EdgeKind
 * @typedef {'warn'|'error'} DiagnosticLevel
 */

export const NodeKind = /** @type {const} */ ({
  TOPIC: 'topic',
  USER_MESSAGE: 'user-message',
  AGENT_MESSAGE: 'agent-message',
  RESULT_ROLLUP: 'result-rollup',
  THINKING: 'thinking',
  TEXT: 'text',
  TOOL_USE: 'tool-use',
  TOOL_RESULT: 'tool-result',
  SUBAGENT: 'subagent',
  TASK_EVENT: 'task-event',
  COMPACTION: 'compaction',
  RATE_LIMIT: 'rate-limit',
  SYSTEM_INIT: 'system-init',
  PARSE_WARNING: 'parse-warning',
})

export const DiagnosticCode = /** @type {const} */ ({
  ORPHAN_TOOL_RESULT: 'orphan_tool_result',
  ORPHAN_TOOL_USE: 'orphan_tool_use',
  ORPHAN_TASK_EVENT: 'orphan_task_event',
  UNKNOWN_EVENT_TYPE: 'unknown_event_type',
  PARSE_ERROR: 'parse_error',
  OVER_SIZE: 'over_size',
})

const MCP_PATTERN = /^mcp__([^_]+(?:_[^_]+)*)__(.+)$/

function nodeId(messageId, seq) {
  return `${messageId ?? 'root'}:${seq}`
}

function edgeId(sourceId, targetId, kind) {
  return `${sourceId}->${targetId}${kind ? ':' + kind : ''}`
}

function makeNode(id, kind, parentId, messageId, sequence, ts, data) {
  return {
    id,
    kind,
    parentId,
    messageId,
    sequence,
    ts: ts ?? null,
    data,
    ui: { collapsed: false },
    summaries: [],
  }
}

function makeEdge(sourceId, targetId, kind) {
  return {
    id: edgeId(sourceId, targetId, kind),
    source: sourceId,
    target: targetId,
    kind,
  }
}

function makeDiagnostic(level, code, message, messageId, eventIndex) {
  const d = { level, code, message }
  if (messageId != null) d.messageId = messageId
  if (eventIndex != null) d.eventIndex = eventIndex
  return d
}

function toolUseLabelFor(toolName, input) {
  if (!input) input = {}
  if (toolName === 'Bash')     return `Bash: ${(input.command    || '').slice(0, 80)}`
  if (toolName === 'Read')     return `Read: ${input.file_path   || ''}`
  if (toolName === 'Write')    return `Write: ${input.file_path  || ''}`
  if (toolName === 'Edit')     return `Edit: ${input.file_path   || ''}`
  if (toolName === 'Glob')     return `Glob: ${input.pattern     || ''}`
  if (toolName === 'Grep')     return `Grep: ${input.pattern     || ''}`
  if (toolName === 'Agent')    return `Agent: ${input.description || input.prompt?.slice(0, 60) || ''}`
  if (toolName === 'WebFetch') return `Fetch: ${input.url        || ''}`
  return toolName
}

function extractContentText(content) {
  if (!content) return ''
  if (typeof content === 'string') return content
  if (Array.isArray(content)) return content.map(c => c.text ?? JSON.stringify(c)).join('\n')
  return JSON.stringify(content)
}

/**
 * Parse a raw transcript field from a MessageOut record.
 * The field may be:
 *  - already a parsed array (agent transcript)
 *  - an object (user dispatch payload)
 *  - null (interrupted agent message)
 *  - a JSONL string (each line is one event JSON)
 *  - a JSON string of an array
 * Returns { events: Array|null, dispatch: Object|null, diagnostics: Array }
 * events is null when transcript is null (interrupted message).
 */
function parseTranscriptField(transcript, messageId) {
  const diagnostics = []

  if (transcript === null || transcript === undefined) {
    return { events: null, dispatch: null, diagnostics }
  }

  if (Array.isArray(transcript)) {
    return { events: transcript, dispatch: null, diagnostics }
  }

  if (typeof transcript === 'object') {
    return { events: null, dispatch: transcript, diagnostics }
  }

  if (typeof transcript === 'string') {
    const trimmed = transcript.trim()
    if (trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed)
        return { events: Array.isArray(parsed) ? parsed : [parsed], dispatch: null, diagnostics }
      } catch {
        // fall through to JSONL
      }
    }
    if (trimmed.startsWith('{')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          return { events: parsed, dispatch: null, diagnostics }
        }
        return { events: null, dispatch: parsed, diagnostics }
      } catch {
        // fall through to JSONL
      }
    }
    const events = []
    for (const line of trimmed.split('\n')) {
      const l = line.trim()
      if (!l) continue
      try {
        events.push(JSON.parse(l))
      } catch {
        diagnostics.push(makeDiagnostic('warn', DiagnosticCode.PARSE_ERROR,
          `Unparseable JSONL line: ${l.slice(0, 80)}`, messageId))
      }
    }
    return { events, dispatch: null, diagnostics }
  }

  return { events: null, dispatch: null, diagnostics }
}

/**
 * Parse a user-record transcript (dispatch payload object).
 * Returns a flat object safe to embed in node data.
 */
function parseDispatchPayload(transcript) {
  if (!transcript || typeof transcript !== 'object') {
    return { adapter: null, agent_name: null, subagent: null, model: null,
             session_id: null, is_new_session: false, session_scope: null,
             worktree: null, branch: null, repo_ref: null, base_sha: null,
             system_prompt: null, text: null, attachments: [] }
  }
  return {
    message_id: transcript.message_id ?? null,
    adapter: transcript.adapter ?? null,
    agent_name: transcript.agent_name ?? null,
    subagent: transcript.subagent ?? null,
    model: transcript.model ?? null,
    session_id: transcript.session_id ?? null,
    is_new_session: transcript.is_new_session ?? false,
    session_scope: transcript.session_scope ?? null,
    worktree: transcript.worktree ?? null,
    branch: transcript.branch ?? null,
    repo_ref: transcript.repo_ref ?? null,
    base_sha: transcript.base_sha ?? null,
    system_prompt: transcript.system_prompt ?? null,
    text: transcript.text ?? null,
    attachments: transcript.attachments ?? [],
  }
}

/**
 * Walk one agent message's events array and emit nodes/edges into the
 * provided accumulator arrays.  Updates sequence counter in the passed-in
 * state object and returns it.
 *
 * @param {object[]} events
 * @param {string} messageId
 * @param {string} agentMessageNodeId
 * @param {object} state  mutable: { seq, nodes, edges, diagnostics }
 * @param {number|undefined} maxEvents
 */
function walkEvents(events, messageId, agentMessageNodeId, agentMessageNode, state, maxEvents) {
  const toolUseIndex = new Map()
  const toolResultIndex = new Set()
  const subagentIndex = new Map()
  const taskIndex = new Map()
  let pendingCompactStatus = null
  let eventsCap = maxEvents != null ? maxEvents : Infinity

  if (events.length > eventsCap) {
    state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.OVER_SIZE,
      `Message ${messageId} has ${events.length} events; truncating to ${eventsCap}`, messageId))
    events = events.slice(0, eventsCap)
  }

  function resolveParent(parentToolUseId) {
    if (!parentToolUseId) return agentMessageNodeId
    const sub = subagentIndex.get(parentToolUseId)
    if (sub) return sub.id
    return agentMessageNodeId
  }

  for (let idx = 0; idx < events.length; idx++) {
    const event = events[idx]
    if (!event || typeof event !== 'object') continue

    const evType = event.type
    const evSubtype = event.subtype
    const parentToolUseId = event.parent_tool_use_id ?? null

    if (evType === 'assistant') {
      const content = event.message?.content ?? []
      for (const block of content) {
        if (!block || typeof block !== 'object') continue
        const seq = state.seq++
        const id = nodeId(messageId, seq)
        const parentId = resolveParent(parentToolUseId)

        if (block.type === 'thinking') {
          const node = makeNode(id, NodeKind.THINKING, parentId, messageId, seq, null,
            { text: block.thinking ?? '' })
          state.nodes.push(node)
          state.edges.push(makeEdge(parentId, id, 'contains'))

        } else if (block.type === 'text') {
          const node = makeNode(id, NodeKind.TEXT, parentId, messageId, seq, null,
            { text: block.text ?? '' })
          state.nodes.push(node)
          state.edges.push(makeEdge(parentId, id, 'contains'))

        } else if (block.type === 'tool_use') {
          const toolName = block.name ?? ''
          const toolUseId = block.id ?? id
          const input = block.input ?? {}

          let mcpServer = undefined
          let mcpTool = undefined
          const mcpMatch = MCP_PATTERN.exec(toolName)
          if (mcpMatch) {
            mcpServer = mcpMatch[1]
            mcpTool = mcpMatch[2]
          }

          const label = toolUseLabelFor(toolName, input)
          const toolUseData = { toolName, input, toolUseId, label }
          if (mcpServer !== undefined) {
            toolUseData.mcpServer = mcpServer
            toolUseData.mcpTool = mcpTool
          }

          const tuNode = makeNode(id, NodeKind.TOOL_USE, parentId, messageId, seq, null, toolUseData)
          // tool-uses start collapsed by default per design
          tuNode.ui.collapsed = true
          state.nodes.push(tuNode)
          state.edges.push(makeEdge(parentId, id, 'contains'))
          toolUseIndex.set(toolUseId, tuNode)

          if (toolName === 'Agent') {
            const subSeq = state.seq++
            const subId = nodeId(messageId, subSeq)
            const subNode = makeNode(subId, NodeKind.SUBAGENT, id, messageId, subSeq, null,
              { agentType: input.subagent_type ?? null, prompt: input.prompt ?? null,
                model: null, summary: null })
            state.nodes.push(subNode)
            state.edges.push(makeEdge(id, subId, 'invokes'))
            subagentIndex.set(toolUseId, subNode)
          }
        } else {
          // unknown content block — skip silently (no node, no diagnostic per design
          // "parsers should not crash on unknown content block types")
        }
      }
      continue
    }

    if (evType === 'user') {
      const content = event.message?.content ?? []
      for (const block of content) {
        if (!block || typeof block !== 'object') continue
        if (block.type !== 'tool_result') continue

        const toolUseId = block.tool_use_id ?? ''
        const seq = state.seq++
        const id = nodeId(messageId, seq)
        const isError = block.is_error === true
        const contentText = extractContentText(block.content)

        const pairedToolUse = toolUseIndex.get(toolUseId)
        let parentId
        if (pairedToolUse) {
          parentId = pairedToolUse.id
          toolResultIndex.add(toolUseId)
        } else {
          parentId = agentMessageNodeId
          state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.ORPHAN_TOOL_RESULT,
            `tool_result references unknown tool_use_id ${toolUseId}`,
            messageId, idx))
        }

        const trData = { toolUseId, contentText, isError }

        if (pairedToolUse?.data?.toolName === 'Agent') {
          const toolUseResult = event.tool_use_result ?? {}
          if (toolUseResult.agentType != null) trData.agentType = toolUseResult.agentType
          if (toolUseResult.totalDurationMs != null) trData.totalDurationMs = toolUseResult.totalDurationMs
          if (toolUseResult.totalTokens != null) trData.totalTokens = toolUseResult.totalTokens
          if (toolUseResult.totalToolUseCount != null) trData.totalToolUseCount = toolUseResult.totalToolUseCount

          const subNode = subagentIndex.get(toolUseId)
          if (subNode) {
            if (toolUseResult.agentType != null) subNode.data.agentType = toolUseResult.agentType
            if (toolUseResult.totalDurationMs != null) subNode.data.totalDurationMs = toolUseResult.totalDurationMs
            if (toolUseResult.totalTokens != null) subNode.data.totalTokens = toolUseResult.totalTokens
            if (toolUseResult.totalToolUseCount != null) subNode.data.totalToolUseCount = toolUseResult.totalToolUseCount
          }
        }

        const trNode = makeNode(id, NodeKind.TOOL_RESULT, parentId, messageId, seq, null, trData)
        state.nodes.push(trNode)
        state.edges.push(makeEdge(parentId, id, 'contains'))
      }
      continue
    }

    if (evType === 'system') {
      if (evSubtype === 'task_started') {
        const taskId = event.task_id ?? ''
        const toolUseId = event.tool_use_id ?? ''
        const taskType = event.task_type ?? ''
        const seq = state.seq++
        const id = nodeId(messageId, seq)

        taskIndex.set(taskId, { taskType, toolUseId })

        const taskData = {
          subtype: 'task_started',
          taskId,
          taskType,
          description: event.description ?? null,
          toolUseId,
        }
        if (taskType === 'local_agent') {
          taskData.subagentType = event.subagent_type ?? null
          taskData.prompt = event.prompt ?? null
        }

        let parentId
        if (taskType === 'local_agent') {
          const sub = subagentIndex.get(toolUseId)
          parentId = sub ? sub.id : agentMessageNodeId
          if (!sub) {
            state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.ORPHAN_TASK_EVENT,
              `task_started (local_agent) references unknown tool_use_id ${toolUseId}`,
              messageId, idx))
          }
        } else {
          const tu = toolUseIndex.get(toolUseId)
          parentId = tu ? tu.id : agentMessageNodeId
          if (!tu) {
            state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.ORPHAN_TASK_EVENT,
              `task_started (local_bash) references unknown tool_use_id ${toolUseId}`,
              messageId, idx))
          }
        }

        const node = makeNode(id, NodeKind.TASK_EVENT, parentId, messageId, seq, null, taskData)
        state.nodes.push(node)
        state.edges.push(makeEdge(parentId, id, 'contains'))
        continue
      }

      if (evSubtype === 'task_progress' || evSubtype === 'task_updated' || evSubtype === 'task_notification') {
        const taskId = event.task_id ?? ''
        const entry = taskIndex.get(taskId)
        const seq = state.seq++
        const id = nodeId(messageId, seq)

        const taskData = {
          subtype: evSubtype,
          taskId,
          taskType: entry?.taskType ?? null,
          description: event.description ?? null,
          toolUseId: entry?.toolUseId ?? event.tool_use_id ?? null,
        }

        if (evSubtype === 'task_notification') {
          taskData.status = event.status ?? null
          taskData.usage = event.usage ?? null
        }

        if (evSubtype === 'task_updated') {
          taskData.patch = event.patch ?? null
          const patch = event.patch ?? {}
          if (patch.status !== undefined) taskData.status = patch.status
          if (patch.end_time !== undefined) taskData.endTime = patch.end_time
          if (patch.is_backgrounded !== undefined) taskData.isBackgrounded = patch.is_backgrounded
        }

        let parentId = agentMessageNodeId
        if (entry) {
          const { taskType, toolUseId } = entry
          if (taskType === 'local_agent') {
            const sub = subagentIndex.get(toolUseId)
            parentId = sub ? sub.id : agentMessageNodeId
          } else {
            const tu = toolUseIndex.get(toolUseId)
            parentId = tu ? tu.id : agentMessageNodeId
          }
        } else {
          state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.ORPHAN_TASK_EVENT,
            `${evSubtype} references unknown task_id ${taskId}`,
            messageId, idx))
        }

        const node = makeNode(id, NodeKind.TASK_EVENT, parentId, messageId, seq, null, taskData)
        state.nodes.push(node)
        state.edges.push(makeEdge(parentId, id, 'contains'))
        continue
      }

      if (evSubtype === 'status') {
        if (event.status === 'compacting' || event.compact_result) {
          pendingCompactStatus = event
        }
        continue
      }

      if (evSubtype === 'compact_boundary') {
        const meta = event.compact_metadata ?? {}
        const seq = state.seq++
        const id = nodeId(messageId, seq)
        const compactionData = {
          trigger: meta.trigger ?? null,
          preTokens: meta.pre_tokens ?? null,
          postTokens: meta.post_tokens ?? null,
          durationMs: meta.duration_ms ?? null,
          compactResult: pendingCompactStatus?.compact_result ?? null,
        }
        pendingCompactStatus = null

        const node = makeNode(id, NodeKind.COMPACTION, agentMessageNodeId, messageId, seq, null, compactionData)
        state.nodes.push(node)
        state.edges.push(makeEdge(agentMessageNodeId, id, 'contains'))
        continue
      }

      if (evSubtype === 'init') {
        const seq = state.seq++
        const id = nodeId(messageId, seq)
        const initData = {
          model: event.model ?? null,
          tools: event.tools ?? null,
          cwd: event.cwd ?? null,
          sessionId: event.session_id ?? null,
        }
        const node = makeNode(id, NodeKind.SYSTEM_INIT, agentMessageNodeId, messageId, seq, null, initData)
        state.nodes.push(node)
        state.edges.push(makeEdge(agentMessageNodeId, id, 'contains'))

        if (event.model) {
          agentMessageNode.data.model = event.model
        }
        continue
      }

      state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.UNKNOWN_EVENT_TYPE,
        `Unknown system subtype: ${evSubtype}`, messageId, idx))
      continue
    }

    if (evType === 'rate_limit_event') {
      const seq = state.seq++
      const id = nodeId(messageId, seq)
      const info = event.rate_limit_info ?? {}
      const node = makeNode(id, NodeKind.RATE_LIMIT, agentMessageNodeId, messageId, seq, null,
        { tier: info.tier ?? null, resetsAt: info.resets_at ?? null })
      state.nodes.push(node)
      state.edges.push(makeEdge(agentMessageNodeId, id, 'contains'))
      continue
    }

    if (evType === 'result') {
      if (evSubtype === 'success') {
        const seq = state.seq++
        const id = nodeId(messageId, seq)
        const node = makeNode(id, NodeKind.RESULT_ROLLUP, agentMessageNodeId, messageId, seq, null, {
          isError: event.is_error === true,
          cost: event.total_cost_usd ?? null,
          durationMs: event.duration_ms ?? null,
          numTurns: event.num_turns ?? null,
          usage: event.usage ?? null,
        })
        state.nodes.push(node)
        state.edges.push(makeEdge(agentMessageNodeId, id, 'contains'))
      }
      continue
    }

    state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.UNKNOWN_EVENT_TYPE,
      `Unknown event type: ${evType}`, messageId, idx))
  }

  for (const [toolUseId] of toolUseIndex) {
    if (!toolResultIndex.has(toolUseId)) {
      state.diagnostics.push(makeDiagnostic('warn', DiagnosticCode.ORPHAN_TOOL_USE,
        `tool_use ${toolUseId} has no matching tool_result`, messageId))
    }
  }

  if (pendingCompactStatus) {
    const seq = state.seq++
    const id = nodeId(messageId, seq)
    const compactionData = {
      trigger: null,
      preTokens: null,
      postTokens: null,
      durationMs: null,
      compactResult: pendingCompactStatus.compact_result ?? null,
    }
    const node = makeNode(id, NodeKind.COMPACTION, agentMessageNodeId, messageId, seq, null, compactionData)
    state.nodes.push(node)
    state.edges.push(makeEdge(agentMessageNodeId, id, 'contains'))
  }
}

/**
 * Build a Graph IR from an array of MessageOut records.
 *
 * @param {object} params
 * @param {string} params.workspaceId
 * @param {string} params.topicId
 * @param {string} [params.topicSubject]
 * @param {string} [params.workspaceName]
 * @param {object[]} params.messages  Array of MessageOut records.
 * @param {string} [params.generatedAt]  ISO string; defaults to new Date().toISOString().
 * @param {number} [params.maxEvents]  Per-message event cap. Default: 5000.
 * @returns {object} Graph IR with {version, topicId, workspaceId, generatedAt, nodes, edges, summaries, messageSummaries, diagnostics}
 */
export function buildTopicGraph({
  workspaceId,
  topicId,
  topicSubject = '',
  workspaceName = '',
  messages,
  generatedAt,
  maxEvents = 5000,
}) {
  const at = generatedAt ?? new Date().toISOString()
  const diagnostics = []
  const nodes = []
  const edges = []
  const messageSummaries = {}

  const state = { seq: 0, nodes, edges, diagnostics }

  const topicSeq = state.seq++
  const topicNodeId = nodeId(null, topicSeq)
  const topicNode = makeNode(topicNodeId, NodeKind.TOPIC, null, null, topicSeq, null,
    { subject: topicSubject, workspaceName })
  nodes.push(topicNode)

  for (const msg of messages) {
    if (!msg || typeof msg !== 'object') continue

    // API MessageOut rows carry `sender` (user|agent|event); export logs carry `type`.
    // `event` rows are dispatched prompts, so they sit on the user side of the spine.
    const msgType = msg.type ?? (msg.sender === 'agent' ? 'agent'
      : msg.sender === 'user' || msg.sender === 'event' ? 'user' : undefined)
    const msgId = msg.id ?? msg.message_id ?? String(state.seq)
    const msgTs = msg.timestamp ?? msg.created_at ?? null

    messageSummaries[msgId] = []

    if (msgType === 'user') {
      const { dispatch: dispatchRaw, diagnostics: parseDiags } = parseTranscriptField(msg.transcript, msgId)
      for (const d of parseDiags) diagnostics.push(d)

      const dispatch = parseDispatchPayload(dispatchRaw ?? msg.transcript)
      const seq = state.seq++
      const id = nodeId(msgId, seq)
      const node = makeNode(id, NodeKind.USER_MESSAGE, topicNodeId, msgId, seq, msgTs, {
        text: msg.text ?? '',
        attachments: dispatch.attachments ?? [],
        dispatch,
      })
      nodes.push(node)
      edges.push(makeEdge(topicNodeId, id, 'contains'))
      continue
    }

    if (msgType === 'agent') {
      const seq = state.seq++
      const id = nodeId(msgId, seq)

      const { events, diagnostics: parseDiags } = parseTranscriptField(msg.transcript, msgId)
      for (const d of parseDiags) diagnostics.push(d)

      const interrupted = events === null
      const agentNode = makeNode(id, NodeKind.AGENT_MESSAGE, topicNodeId, msgId, seq, msgTs, {
        agentName: msg.agent_name ?? '',
        text: msg.text ?? '',
        silent: msg.silent === true,
        interrupted,
        interruptReason: interrupted ? (msg.text ?? null) : null,
        model: null,
        hasTranscript: !interrupted,
      })
      nodes.push(agentNode)
      edges.push(makeEdge(topicNodeId, id, 'contains'))

      if (!interrupted && Array.isArray(events)) {
        walkEvents(events, msgId, id, agentNode, state, maxEvents)
      }
      continue
    }

    diagnostics.push(makeDiagnostic('warn', DiagnosticCode.UNKNOWN_EVENT_TYPE,
      `Unknown message sender/type: ${msg.sender ?? msg.type}`, msgId))
  }

  return {
    version: 1,
    topicId: topicId ?? '',
    workspaceId: workspaceId ?? '',
    generatedAt: at,
    nodes,
    edges,
    summaries: [],
    messageSummaries,
    diagnostics,
  }
}
