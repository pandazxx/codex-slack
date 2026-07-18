<template>
  <div v-if="node" class="detail-panel">
    <div class="detail-header">
      <span class="detail-kind">{{ node.kind }}</span>
      <button class="detail-close" @click="$emit('close')">✕</button>
    </div>

    <div class="detail-body">

      <template v-if="node.kind === 'topic'">
        <div class="detail-field"><span class="detail-label">Subject</span><span>{{ node.data.subject || '(none)' }}</span></div>
        <div class="detail-field"><span class="detail-label">Workspace</span><span>{{ node.data.workspaceName || '(none)' }}</span></div>
        <SummaryOverlay :summaries="graph.summaries" />
      </template>

      <template v-else-if="node.kind === 'user-message'">
        <div class="detail-actions">
          <a :href="chatLink" target="_blank" class="detail-link">→ chat</a>
        </div>
        <div v-if="node.data.dispatch?.agent_name" class="detail-field">
          <span class="detail-label">Agent</span>
          <span>@{{ node.data.dispatch.agent_name }}</span>
        </div>
        <div v-if="node.data.dispatch?.adapter" class="detail-field">
          <span class="detail-label">Adapter</span><span>{{ node.data.dispatch.adapter }}</span>
        </div>
        <div v-if="node.data.dispatch?.model" class="detail-field">
          <span class="detail-label">Model</span><span>{{ node.data.dispatch.model }}</span>
        </div>
        <div v-if="node.data.dispatch?.session_id" class="detail-field">
          <span class="detail-label">Session</span>
          <span>{{ node.data.dispatch.session_id }} ({{ node.data.dispatch.is_new_session ? 'new' : 'resumed' }})</span>
        </div>
        <div class="detail-section-title">Message</div>
        <MarkdownMessage :text="node.data.text || ''" />
        <SummaryOverlay :summaries="messageSummaries" />
      </template>

      <template v-else-if="node.kind === 'agent-message'">
        <div class="detail-actions">
          <a :href="chatLink" target="_blank" class="detail-link">→ chat</a>
        </div>
        <div v-if="node.data.interrupted" class="detail-interrupted">
          <span class="badge-interrupted">interrupted</span>
          <span v-if="node.data.interruptReason" class="detail-meta">{{ node.data.interruptReason }}</span>
        </div>
        <div v-if="node.data.model" class="detail-field"><span class="detail-label">Model</span><span>{{ node.data.model }}</span></div>
        <div v-if="rollupNode" class="detail-rollup">
          <span v-if="rollupNode.data.cost != null" class="detail-stat">${{ rollupNode.data.cost.toFixed(4) }}</span>
          <span v-if="rollupNode.data.durationMs != null" class="detail-stat">{{ (rollupNode.data.durationMs / 1000).toFixed(1) }}s</span>
          <span v-if="rollupNode.data.numTurns != null" class="detail-stat">{{ rollupNode.data.numTurns }} turns</span>
        </div>
        <div class="detail-section-title">Response</div>
        <MarkdownMessage :text="node.data.text || '(message interrupted)'" />
        <SummaryOverlay :summaries="messageSummaries" />
      </template>

      <template v-else-if="node.kind === 'thinking'">
        <pre class="detail-mono detail-thinking-body">{{ node.data.text }}</pre>
      </template>

      <template v-else-if="node.kind === 'text'">
        <MarkdownMessage :text="node.data.text || ''" />
      </template>

      <template v-else-if="node.kind === 'tool-use'">
        <div class="detail-field"><span class="detail-label">Tool</span><span>{{ node.data.toolName }}</span></div>
        <template v-if="node.data.mcpServer">
          <div class="detail-field"><span class="detail-label">MCP Server</span><span>{{ node.data.mcpServer }}</span></div>
          <div class="detail-field"><span class="detail-label">MCP Tool</span><span>{{ node.data.mcpTool }}</span></div>
        </template>
        <div class="detail-section-title">Input</div>
        <div class="detail-table"><JsonTable :value="node.data.input" /></div>
        <template v-if="node.data.result">
          <div class="detail-section-title">Result</div>
          <div v-if="node.data.result.isError" class="detail-field">
            <span class="badge-error">error result</span>
          </div>
          <pre class="detail-json detail-result-body">{{ node.data.result.contentText || '(empty)' }}</pre>
        </template>
        <SummaryOverlay :summaries="node.summaries" />
      </template>

      <template v-else-if="node.kind === 'tool-result'">
        <div v-if="node.data.isError" class="detail-field">
          <span class="badge-error">error result</span>
        </div>
        <div v-if="node.data.agentType" class="detail-field">
          <span class="detail-label">Agent type</span><span>{{ node.data.agentType }}</span>
        </div>
        <div v-if="node.data.totalDurationMs != null" class="detail-field">
          <span class="detail-label">Duration</span><span>{{ (node.data.totalDurationMs / 1000).toFixed(1) }}s</span>
        </div>
        <div v-if="node.data.totalTokens != null" class="detail-field">
          <span class="detail-label">Tokens</span><span>{{ node.data.totalTokens.toLocaleString() }}</span>
        </div>
        <div class="detail-section-title">Content</div>
        <pre class="detail-json detail-result-body">{{ node.data.contentText }}</pre>
        <SummaryOverlay :summaries="node.summaries" />
      </template>

      <template v-else-if="node.kind === 'subagent'">
        <div v-if="node.data.agentType" class="detail-field"><span class="detail-label">Type</span><span>{{ node.data.agentType }}</span></div>
        <div v-if="node.data.model" class="detail-field"><span class="detail-label">Model</span><span>{{ node.data.model }}</span></div>
        <div v-if="node.data.totalDurationMs != null" class="detail-field">
          <span class="detail-label">Duration</span><span>{{ (node.data.totalDurationMs / 1000).toFixed(1) }}s</span>
        </div>
        <div v-if="node.data.totalTokens != null" class="detail-field">
          <span class="detail-label">Tokens</span><span>{{ node.data.totalTokens.toLocaleString() }}</span>
        </div>
        <div v-if="node.data.totalToolUseCount != null" class="detail-field">
          <span class="detail-label">Tool calls</span><span>{{ node.data.totalToolUseCount }}</span>
        </div>
        <div v-if="node.data.prompt" class="detail-section-title">Prompt</div>
        <pre v-if="node.data.prompt" class="detail-mono">{{ node.data.prompt }}</pre>
        <div class="detail-subtree-actions">
          <button class="detail-btn" @click="$emit('expand-toggle', node)">
            {{ node.ui.collapsed ? 'Expand subtree' : 'Collapse subtree' }}
          </button>
        </div>
        <SummaryOverlay :summaries="node.summaries" />
      </template>

      <template v-else-if="node.kind === 'result-rollup'">
        <div v-if="node.data.isError" class="detail-field"><span class="badge-error">error</span></div>
        <div v-if="node.data.cost != null" class="detail-field">
          <span class="detail-label">Cost</span><span>${{ node.data.cost.toFixed(4) }}</span>
        </div>
        <div v-if="node.data.durationMs != null" class="detail-field">
          <span class="detail-label">Duration</span><span>{{ (node.data.durationMs / 1000).toFixed(1) }}s</span>
        </div>
        <div v-if="node.data.numTurns != null" class="detail-field">
          <span class="detail-label">Turns</span><span>{{ node.data.numTurns }}</span>
        </div>
        <template v-if="node.data.usage">
          <div class="detail-section-title">Usage</div>
          <div class="detail-usage-table">
            <div class="detail-field"><span class="detail-label">Input tokens</span><span>{{ (node.data.usage.input_tokens ?? 0).toLocaleString() }}</span></div>
            <div class="detail-field"><span class="detail-label">Output tokens</span><span>{{ (node.data.usage.output_tokens ?? 0).toLocaleString() }}</span></div>
            <div v-if="node.data.usage.cache_read_input_tokens" class="detail-field">
              <span class="detail-label">Cache read</span><span>{{ node.data.usage.cache_read_input_tokens.toLocaleString() }}</span>
            </div>
            <div v-if="node.data.usage.cache_creation_input_tokens" class="detail-field">
              <span class="detail-label">Cache write</span><span>{{ node.data.usage.cache_creation_input_tokens.toLocaleString() }}</span>
            </div>
          </div>
        </template>
      </template>

      <template v-else-if="node.kind === 'task-event'">
        <div class="detail-field"><span class="detail-label">Subtype</span><span>{{ node.data.subtype }}</span></div>
        <div v-if="node.data.taskType" class="detail-field"><span class="detail-label">Task type</span><span>{{ node.data.taskType }}</span></div>
        <div v-if="node.data.subagentType" class="detail-field"><span class="detail-label">Agent type</span><span>{{ node.data.subagentType }}</span></div>
        <div v-if="node.data.description" class="detail-field"><span class="detail-label">Description</span><span>{{ node.data.description }}</span></div>
        <div v-if="node.data.status" class="detail-field"><span class="detail-label">Status</span><span>{{ node.data.status }}</span></div>
        <div v-if="node.data.endTime" class="detail-field"><span class="detail-label">End time</span><span>{{ node.data.endTime }}</span></div>
        <div v-if="node.data.isBackgrounded != null" class="detail-field">
          <span class="detail-label">Backgrounded</span><span>{{ node.data.isBackgrounded }}</span>
        </div>
        <template v-if="node.data.patch">
          <div class="detail-section-title">Patch</div>
          <div class="detail-table"><JsonTable :value="node.data.patch" /></div>
        </template>
      </template>

      <template v-else-if="node.kind === 'compaction'">
        <div v-if="node.data.trigger" class="detail-field"><span class="detail-label">Trigger</span><span>{{ node.data.trigger }}</span></div>
        <div v-if="node.data.preTokens != null" class="detail-field">
          <span class="detail-label">Pre tokens</span><span>{{ node.data.preTokens.toLocaleString() }}</span>
        </div>
        <div v-if="node.data.postTokens != null" class="detail-field">
          <span class="detail-label">Post tokens</span><span>{{ node.data.postTokens.toLocaleString() }}</span>
        </div>
        <div v-if="node.data.durationMs != null" class="detail-field">
          <span class="detail-label">Duration</span><span>{{ (node.data.durationMs / 1000).toFixed(1) }}s</span>
        </div>
        <div v-if="node.data.compactResult" class="detail-field"><span class="detail-label">Result</span><span>{{ node.data.compactResult }}</span></div>
      </template>

      <template v-else-if="node.kind === 'system-init'">
        <div v-if="node.data.model" class="detail-field"><span class="detail-label">Model</span><span>{{ node.data.model }}</span></div>
        <div v-if="node.data.sessionId" class="detail-field"><span class="detail-label">Session</span><span>{{ node.data.sessionId }}</span></div>
        <div v-if="node.data.cwd" class="detail-field"><span class="detail-label">CWD</span><span>{{ node.data.cwd }}</span></div>
        <template v-if="node.data.tools">
          <div class="detail-section-title">Tools</div>
          <div class="detail-tools">{{ node.data.tools.join(', ') }}</div>
        </template>
      </template>

      <template v-else>
        <div class="detail-table"><JsonTable :value="node.data" /></div>
      </template>

    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownMessage from '../MarkdownMessage.vue'
import SummaryOverlay from './SummaryOverlay.vue'
import JsonTable from './JsonTable.vue'

const props = defineProps({
  node: { type: Object, default: null },
  graph: { type: Object, required: true },
  wsId: { type: String, required: true },
  topicId: { type: String, required: true },
})
defineEmits(['close', 'expand-toggle'])

const chatLink = computed(() => {
  if (!props.node?.messageId) return '#'
  return `/workspaces/${props.wsId}/topics/${props.topicId}#msg-${props.node.messageId}`
})

const rollupNode = computed(() => {
  if (!props.node || props.node.kind !== 'agent-message') return null
  return props.graph.nodes.find(n => n.kind === 'result-rollup' && n.parentId === props.node.id) ?? null
})

const messageSummaries = computed(() => {
  if (!props.node?.messageId) return []
  return props.graph.messageSummaries[props.node.messageId] ?? []
})
</script>

<style scoped>
.detail-panel {
  position: fixed;
  top: 60px;
  right: 0;
  width: 380px;
  height: calc(100vh - 60px);
  background: #fff;
  border-left: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  z-index: 100;
  box-shadow: -4px 0 16px rgba(0,0,0,0.08);
}
.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}
.detail-kind {
  font-size: 0.75em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}
.detail-close {
  background: none;
  border: none;
  cursor: pointer;
  color: #94a3b8;
  font-size: 0.85rem;
  padding: 2px 6px;
  border-radius: 4px;
}
.detail-close:hover { background: #f1f5f9; color: #1e293b; }
.detail-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  font-size: 0.82em;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.detail-field {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}
.detail-label {
  font-size: 0.85em;
  color: #94a3b8;
  min-width: 80px;
  flex-shrink: 0;
}
.detail-section-title {
  font-size: 0.75em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #94a3b8;
  margin-top: 8px;
  margin-bottom: 2px;
}
.detail-json {
  background: #1e293b;
  color: #e2e8f0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
}
.detail-result-body { background: #0f2027; color: #86efac; }
.detail-table {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 6px 10px;
  max-height: 300px;
  overflow-y: auto;
}
.detail-tools { color: #475569; font-size: 0.9em; word-break: break-word; }
.detail-mono {
  font-family: monospace;
  white-space: pre-wrap;
  background: #f8fafc;
  border-left: 3px solid #cbd5e1;
  padding: 8px 10px;
  border-radius: 0 4px 4px 0;
  font-size: 0.85em;
  max-height: 300px;
  overflow-y: auto;
}
.detail-thinking-body { background: #1a0a2e; color: #c4b5fd; border-left-color: #c0a85a; }
.detail-actions { display: flex; gap: 8px; margin-bottom: 4px; }
.detail-link { font-size: 0.82em; color: #2563eb; text-decoration: none; }
.detail-link:hover { text-decoration: underline; }
.detail-interrupted { display: flex; align-items: center; gap: 8px; }
.badge-interrupted { font-size: 0.78em; padding: 1px 6px; border-radius: 10px; background: #fef3c7; color: #92400e; font-weight: 600; }
.badge-error { font-size: 0.78em; padding: 1px 6px; border-radius: 10px; background: #fee2e2; color: #dc2626; font-weight: 600; }
.detail-meta { color: #64748b; font-size: 0.88em; }
.detail-stat { color: #64748b; font-size: 0.88em; }
.detail-rollup { display: flex; gap: 10px; flex-wrap: wrap; }
.detail-subtree-actions { display: flex; gap: 8px; margin-top: 8px; }
.detail-btn { font-size: 0.78em; padding: 3px 10px; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 4px; cursor: pointer; color: #475569; }
.detail-btn:hover { background: #e2e8f0; color: #1e293b; }
.detail-usage-table { display: flex; flex-direction: column; gap: 4px; }
</style>
