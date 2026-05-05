<template>
  <div class="chat-layout">
    <p class="breadcrumb">
      <RouterLink to="/">Workspaces</RouterLink> /
      <RouterLink :to="`/workspaces/${wsId}`">{{ wsId }}</RouterLink> /
      {{ topic?.subject || topicId }}
    </p>

    <div v-if="isArchived" class="archived-banner">This topic is archived — read only</div>
    <div class="status-bar" v-if="agentStatus && !isArchived">
      Agent: <em>{{ agentStatus }}</em>
    </div>

    <div class="messages" ref="msgBox">
      <p v-if="loading" class="muted center">Loading…</p>
      <p v-else-if="!messages.length" class="muted center">No messages yet. Send one below.</p>
      <div
        v-for="m in messages"
        :key="m.id"
        class="message"
        :class="m.sender === 'user' ? 'user' : 'agent'"
      >
        <span class="label">{{ m.sender === 'user' ? 'You' : (m.agent_name ? `@${m.agent_name}` : 'Agent') }}</span>
        <div class="bubble">
          <template v-if="m.sender === 'agent'">
            <template v-if="m.streaming && m.rows?.length">
              <div class="trace-row" v-for="(row, i) in m.rows" :key="i">
                <span v-if="row.kind === 'tool_use'">{{ toolUseLabel(row.event) }}</span>
                <span v-else-if="row.kind === 'task_progress'">↳ {{ row.event.description }}</span>
                <span v-else-if="row.kind === 'task_started'">🚀 {{ row.event.description }}</span>
                <span v-else-if="row.kind === 'retry_notice'">⟳ Session expired — retrying…</span>
                <details v-else-if="row.kind === 'folded'">
                  <summary>···</summary>
                  <pre>{{ JSON.stringify(row.event, null, 2) }}</pre>
                </details>
              </div>
            </template>
            <template v-else-if="!m.streaming && m.traceRows?.length">
              <details :open="m.traceOpen" @toggle="m.traceOpen = $event.target.open">
                <summary>▶ Show trace ({{ m.traceRows.length }} steps)</summary>
                <div class="trace-row" v-for="(row, i) in m.traceRows" :key="i">
                  <span v-if="row.kind === 'tool_use'">{{ toolUseLabel(row.event) }}</span>
                  <span v-else-if="row.kind === 'task_progress'">↳ {{ row.event.description }}</span>
                  <span v-else-if="row.kind === 'task_started'">🚀 {{ row.event.description }}</span>
                  <span v-else-if="row.kind === 'retry_notice'">⟳ Session expired — retrying…</span>
                  <details v-else-if="row.kind === 'folded'">
                    <summary>···</summary>
                    <pre>{{ JSON.stringify(row.event, null, 2) }}</pre>
                  </details>
                </div>
              </details>
            </template>
            <MarkdownMessage :text="m.text" />
            <span v-if="m.streaming" class="cursor">▍</span>
          </template>
          <template v-else>{{ m.text }}</template>
        </div>
        <div v-if="m.attachments && m.attachments.length" class="attachment-list">
          <template v-for="a in m.attachments" :key="a.id">
            <div v-if="a.mime_type && a.mime_type.startsWith('image/')" class="attachment-img-wrap">
              <img :src="`/api/attachments/${a.id}/download`" :alt="a.filename" class="attachment-img" />
            </div>
            <div v-else class="attachment-file">
              <a :href="`/api/attachments/${a.id}/download`" :download="a.filename">{{ a.filename }} ({{ formatSize(a.size_bytes) }})</a>
            </div>
          </template>
        </div>
        <details v-if="m.sender === 'user' && isDispatchPayload(m.transcript)" class="detail-panel">
          <summary class="detail-toggle">Details</summary>
          <div class="dispatch-detail">
            <div class="dispatch-meta">
              <span class="tr-badge tr-badge-tool">{{ parseDispatch(m.transcript).adapter }}</span>
              <span class="dispatch-info">@{{ parseDispatch(m.transcript).agent_name }}</span>
              <span class="dispatch-info">session: {{ parseDispatch(m.transcript).session_scope }} ({{ parseDispatch(m.transcript).is_new_session ? 'new' : 'resumed' }})</span>
            </div>
            <pre class="tr-raw dispatch-cmd">{{ buildDispatchCommand(m.transcript) }}</pre>
          </div>
        </details>
        <details v-if="m.sender === 'agent' && m.transcript" class="detail-panel">
          <summary class="detail-toggle">
            Details
            <button class="raw-btn" @click.prevent="toggleRaw(m.id)">{{ rawView[m.id] ? 'Parsed' : 'Raw' }}</button>
          </summary>
          <pre v-if="rawView[m.id]" class="tr-json tr-raw">{{ toJsonl(m.transcript) }}</pre>
          <div v-else class="transcript-view">
            <template v-for="(evt, i) in parseTranscript(m.transcript)" :key="i">
              <template v-if="evt.type === 'assistant' && evt.message">
                <div v-for="(blk, j) in (evt.message.content || [])" :key="j">
                  <div v-if="blk.type === 'thinking' && blk.thinking" class="tr-thinking">
                    <span class="tr-badge tr-badge-thinking">thinking</span>
                    <pre class="tr-json tr-thinking-body">{{ blk.thinking }}</pre>
                  </div>
                  <div v-else-if="blk.type === 'text' && blk.text" class="tr-text">{{ blk.text }}</div>
                  <div v-else-if="blk.type === 'tool_use'" class="tr-tool-call">
                    <span class="tr-badge tr-badge-tool">{{ blk.name }}</span>
                    <pre class="tr-json">{{ JSON.stringify(blk.input, null, 2) }}</pre>
                  </div>
                </div>
              </template>
              <template v-else-if="evt.type === 'user' && evt.message">
                <div v-for="(blk, j) in (evt.message.content || [])" :key="j">
                  <div v-if="blk.type === 'tool_result'" class="tr-tool-result">
                    <span class="tr-badge tr-badge-result">↳ result</span>
                    <pre class="tr-json tr-result-body">{{ extractToolResult(blk.content) }}</pre>
                  </div>
                </div>
              </template>
              <template v-else-if="evt.type === 'result'">
                <div class="tr-meta">
                  <span :class="evt.is_error ? 'tr-badge tr-badge-error' : 'tr-badge tr-badge-done'">{{ evt.is_error ? 'error' : 'done' }}</span>
                  <span v-if="(evt.total_cost_usd ?? evt.cost_usd) != null" class="tr-stat">${{ (evt.total_cost_usd ?? evt.cost_usd).toFixed(4) }}</span>
                  <span v-if="evt.duration_ms != null" class="tr-stat">{{ (evt.duration_ms / 1000).toFixed(1) }}s</span>
                  <span v-if="evt.num_turns != null" class="tr-stat">{{ evt.num_turns }} turn{{ evt.num_turns !== 1 ? 's' : '' }}</span>
                  <template v-if="evt.usage">
                    <span class="tr-stat tr-tokens">{{ (evt.usage.input_tokens ?? 0).toLocaleString() }}↑</span>
                    <span class="tr-stat tr-tokens">{{ (evt.usage.output_tokens ?? 0).toLocaleString() }}↓</span>
                    <span v-if="evt.usage.cache_read_input_tokens" class="tr-stat tr-cache">{{ evt.usage.cache_read_input_tokens.toLocaleString() }} cached</span>
                    <span v-if="evt.usage.cache_creation_input_tokens" class="tr-stat tr-cache">{{ evt.usage.cache_creation_input_tokens.toLocaleString() }} cache_write</span>
                  </template>
                </div>
              </template>
            </template>
          </div>
        </details>
        <span class="ts">{{ m.created_at }}</span>
      </div>
    </div>

    <template v-if="!isArchived">
      <div v-if="selectedFiles.length" class="file-chips">
        <span v-for="(f, i) in selectedFiles" :key="i" class="file-chip">
          {{ f.name }}
          <button class="chip-remove" @click="removeFile(i)">×</button>
        </span>
      </div>
      <form @submit.prevent="sendMessage" class="send-form">
        <input type="file" multiple ref="fileInput" class="file-input-hidden" @change="onFilesSelected" />
        <button type="button" class="attach-btn" @click="fileInput.click()" :disabled="sending" title="Attach files">📎</button>
        <textarea
          v-model="text"
          placeholder="Type a message…"
          rows="3"
          :disabled="sending"
          @keydown.enter.exact.prevent="sendMessage"
        />
        <button type="submit" :disabled="sending || !text.trim()">
          {{ sending ? 'Sending…' : 'Send' }}
        </button>
      </form>
      <p v-if="sendError" class="send-error">{{ sendError }}</p>
      <p class="hint muted">Enter to send · Shift+Enter for new line · Use <code>@name</code> to address a specific staff</p>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownMessage from '../components/MarkdownMessage.vue'

const route = useRoute()
const wsId = route.params.wsId
const topicId = route.params.topicId

const topic = ref(null)
const messages = ref([])
const loading = ref(true)
const sending = ref(false)
const text = ref('')
const agentStatus = ref('')
const msgBox = ref(null)
const rawView = ref({})
const fileInput = ref(null)
const selectedFiles = ref([])
const liveStreams = ref({})

const isArchived = computed(() => !!topic.value?.archived_at)
const sendError = ref('')

let ws = null

function scrollToBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

function classifyEvent(event) {
  const t = event.type, s = event.subtype
  if (t === 'assistant') {
    const c = event.message?.content || []
    if (c.some(b => b.type === 'text'))     return 'text'
    if (c.some(b => b.type === 'tool_use')) return 'tool_use'
    if (c.some(b => b.type === 'thinking')) return 'folded'
  }
  if (t === 'system' && s === 'task_progress') return 'task_progress'
  if (t === 'system' && s === 'task_started')  return 'task_started'
  if (t === 'system' && s === 'retry')         return 'retry_notice'
  if (t === 'user') {
    const c = event.message?.content || []
    if (c.some(b => b.type === 'tool_result')) return 'folded'
  }
  return 'hidden'
}

function toolUseLabel(event) {
  const block = event.message?.content?.find(b => b.type === 'tool_use')
  if (!block) return ''
  const { name, input } = block
  if (name === 'Bash')     return `⚙ Bash: ${(input.command   || '').slice(0, 80)}`
  if (name === 'Read')     return `📄 Read: ${input.file_path  || ''}`
  if (name === 'Write')    return `✏️ Write: ${input.file_path || ''}`
  if (name === 'Edit')     return `✏️ Edit: ${input.file_path  || ''}`
  if (name === 'Glob')     return `🔍 Glob: ${input.pattern    || ''}`
  if (name === 'Grep')     return `🔍 Grep: ${input.pattern    || ''}`
  if (name === 'Agent')    return `🤖 Agent: ${input.description || ''}`
  if (name === 'WebFetch') return `🌐 Fetch: ${input.url       || ''}`
  return `⚙ ${name}`
}

function transcriptToRows(transcriptJson) {
  if (!transcriptJson) return []
  try {
    return JSON.parse(transcriptJson)
      .map(event => ({ kind: classifyEvent(event), event }))
      .filter(r => r.kind !== 'hidden')
  } catch { return [] }
}

function handleChunk({ message_id, agent_name, event }) {
  let live = liveStreams.value[message_id]
  if (!live) {
    live = { rows: [], text: '' }
    liveStreams.value[message_id] = live
    messages.value.push({
      id: message_id, sender: 'agent',
      agent_name: agent_name || null,
      text: '', rows: live.rows,
      streaming: true, traceOpen: false,
      created_at: new Date().toISOString(),
    })
  }
  if (event.type === 'system' && event.subtype === 'retry') {
    live.rows.splice(0, live.rows.length, { kind: 'retry_notice', event })
    live.text = ''
    const msg = messages.value.find(m => m.id === message_id)
    if (msg) msg.text = ''
    scrollToBottom()
    return
  }
  const kind = classifyEvent(event)
  if (kind === 'text') {
    for (const blk of event.message?.content || [])
      if (blk.type === 'text') live.text += blk.text
    const msg = messages.value.find(m => m.id === message_id)
    if (msg) msg.text = live.text
  } else if (kind !== 'hidden') {
    live.rows.push({ kind, event })
  }
  scrollToBottom()
}

function finaliseMessage(data) {
  const idx = messages.value.findIndex(m => m.id === data.message_id)
  const finalMsg = {
    id: data.message_id, sender: data.sender || 'agent',
    agent_name: data.agent_name || null,
    text: data.last_response || data.text || '',
    transcript: data.transcript || null,
    traceRows: transcriptToRows(data.transcript),
    traceOpen: false, streaming: false,
    created_at: new Date().toISOString(),
  }
  if (idx >= 0) messages.value.splice(idx, 1, finalMsg)
  else messages.value.push(finalMsg)
  delete liveStreams.value[data.message_id]
  scrollToBottom()
}

async function load() {
  loading.value = true
  try {
    const [topicRes, msgsRes] = await Promise.all([
      fetch(`/api/workspaces/${wsId}/topics/${topicId}`),
      fetch(`/api/workspaces/${wsId}/topics/${topicId}/messages`),
    ])
    topic.value = await topicRes.json()
    const rawMsgs = await msgsRes.json()
    messages.value = rawMsgs.map(m => ({
      ...m,
      traceRows: transcriptToRows(m.transcript),
      traceOpen: false,
      streaming: false,
    }))
    scrollToBottom()
  } finally {
    loading.value = false
  }
}

function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/${topicId}`)

  ws.onmessage = (evt) => {
    const data = JSON.parse(evt.data)
    if (data.type === 'status') {
      agentStatus.value = data.state || ''
    } else if (data.type === 'chunk') {
      handleChunk(data)
    } else if (data.type === 'chunk_replay') {
      for (const event of data.events) {
        handleChunk({ message_id: data.message_id, agent_name: data.agent_name, event })
      }
    } else if (data.type === 'message') {
      finaliseMessage(data)
    }
  }

  ws.onclose = () => {
    setTimeout(connectWs, 3000)
  }
}

function onFilesSelected(evt) {
  const files = Array.from(evt.target.files || [])
  selectedFiles.value = [...selectedFiles.value, ...files]
  evt.target.value = ''
}

function removeFile(index) {
  selectedFiles.value = selectedFiles.value.filter((_, i) => i !== index)
}

function formatSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function sendMessage() {
  const msg = text.value.trim()
  if (!msg || sending.value) return
  sending.value = true
  sendError.value = ''
  const filesToSend = [...selectedFiles.value]
  try {
    const fd = new FormData()
    fd.append('text', msg)
    for (const f of filesToSend) {
      fd.append('files', f)
    }
    const r = await fetch(`/api/workspaces/${wsId}/topics/${topicId}/messages`, {
      method: 'POST',
      body: fd,
    })
    if (r.ok) {
      text.value = ''
      selectedFiles.value = []
      const data = await r.json()
      messages.value.push({
        id: data.message_id,
        sender: 'user',
        agent_name: null,
        text: msg,
        transcript: data.dispatch || null,
        attachments: data.attachments || [],
        created_at: new Date().toISOString(),
      })
      scrollToBottom()
    } else {
      const err = await r.json().catch(() => ({}))
      const detail = err.detail
      sendError.value = Array.isArray(detail)
        ? detail.map(d => d.msg).join(', ')
        : (typeof detail === 'string' ? detail : `Server error ${r.status}`)
    }
  } finally {
    sending.value = false
  }
}

function toggleRaw(id) { rawView.value[id] = !rawView.value[id] }

function isDispatchPayload(transcript) {
  if (!transcript) return false
  try { const p = JSON.parse(transcript); return p && typeof p === 'object' && 'adapter' in p } catch { return false }
}

function parseDispatch(transcript) {
  try { return JSON.parse(transcript) } catch { return {} }
}

function buildDispatchCommand(transcript) {
  try {
    const p = JSON.parse(transcript)
    if (!p.adapter) return ''
    if (p.adapter === 'codex') return `codex --full-auto -q ${JSON.stringify(p.text)}`
    const parts = ['claude', '--print', '--verbose', '--output-format', 'stream-json', '--dangerously-skip-permissions']
    if (p.session_id) parts.push(p.is_new_session ? `--session-id ${p.session_id}` : `--resume ${p.session_id}`)
    if (p.model) parts.push(`--model ${p.model}`)
    if (p.system_prompt) parts.push(`--append-system-prompt ${JSON.stringify(p.system_prompt)}`)
    if (p.subagent) parts.push(`--agent ${p.subagent}`)
    parts.push(JSON.stringify(p.text))
    return parts.join(' \\\n  ')
  } catch { return '(error parsing dispatch payload)' }
}

function toJsonl(raw) {
  try {
    return JSON.parse(raw).map(e => JSON.stringify(e, null, 2)).join('\n\n')
  } catch { return raw }
}

function parseTranscript(raw) {
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : [parsed]
  } catch {
    return []
  }
}

function extractToolResult(content) {
  if (!content) return ''
  if (typeof content === 'string') return content
  if (Array.isArray(content)) return content.map(c => c.text ?? JSON.stringify(c)).join('\n')
  return JSON.stringify(content, null, 2)
}

onMounted(async () => {
  await load()
  if (!isArchived.value) connectWs()
})

onUnmounted(() => {
  if (ws) { ws.onclose = null; ws.close() }
})
</script>

<style scoped>
.chat-layout { display: flex; flex-direction: column; height: calc(100vh - 110px); }
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 0.5rem; }
.archived-banner { font-size: 0.85em; background: #fef9c3; border: 1px solid #fde047; border-radius: 4px; padding: 0.25rem 0.75rem; margin-bottom: 0.5rem; color: #713f12; }
.status-bar { font-size: 0.85em; background: #fef9c3; border: 1px solid #fde047; border-radius: 4px; padding: 0.25rem 0.75rem; margin-bottom: 0.5rem; }
.messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 0.75rem; padding: 0.5rem 0; }
.message { display: flex; flex-direction: column; max-width: 72%; }
.message.agent { max-width: 88%; }
.message.user { align-self: flex-end; align-items: flex-end; }
.message.agent { align-self: flex-start; align-items: flex-start; }
.label { font-size: 0.75em; color: #64748b; margin-bottom: 2px; }
.bubble { padding: 0.6rem 0.9rem; border-radius: 12px; line-height: 1.45; }
.message.user .bubble { background: #2563eb; color: #fff; border-bottom-right-radius: 3px; white-space: pre-wrap; }
.message.agent .bubble { background: #fff; border: 1px solid #e2e8f0; border-bottom-left-radius: 3px; max-width: 100%; overflow: hidden; }
.ts { font-size: 0.7em; color: #94a3b8; margin-top: 2px; }
.detail-panel { margin-top: 4px; max-width: 100%; }
.detail-toggle { font-size: 0.72em; color: #94a3b8; cursor: pointer; user-select: none; display: flex; align-items: center; gap: 0.5rem; }
.detail-toggle:hover { color: #64748b; }
.raw-btn { font-size: 0.78em; padding: 1px 6px; background: #1e293b; color: #94a3b8; border: none; border-radius: 3px; cursor: pointer; }
.raw-btn:hover { background: #334155; color: #e2e8f0; }
.tr-raw { background: #0d1117; color: #e6edf3; padding: 0.6rem 0.75rem; border-radius: 6px; font-size: 0.72em; white-space: pre; overflow-x: auto; max-height: 400px; overflow-y: auto; margin-top: 6px; }
.transcript-view { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; font-size: 0.78em; max-height: 400px; overflow-y: auto; }
.tr-text { background: #f8fafc; border-left: 3px solid #cbd5e1; padding: 4px 8px; white-space: pre-wrap; color: #334155; border-radius: 0 4px 4px 0; }
.tr-tool-call, .tr-tool-result { display: flex; flex-direction: column; gap: 2px; }
.tr-json { background: #1e293b; color: #e2e8f0; padding: 5px 8px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; margin: 0; font-size: 0.9em; max-height: 200px; overflow-y: auto; }
.tr-result-body { background: #0f2027; color: #86efac; }
.tr-meta { display: flex; align-items: center; gap: 6px; padding: 3px 0; }
.tr-stat { color: #64748b; }
.tr-tokens { color: #6366f1; font-variant-numeric: tabular-nums; }
.tr-cache { color: #0891b2; }
.tr-badge { font-size: 0.78em; padding: 1px 6px; border-radius: 10px; font-weight: 600; }
.tr-badge-tool { background: #dbeafe; color: #1d4ed8; }
.tr-badge-result { background: #dcfce7; color: #15803d; }
.tr-badge-done { background: #dcfce7; color: #15803d; }
.tr-badge-error { background: #fee2e2; color: #dc2626; }
.tr-badge-thinking { background: #f3e8ff; color: #7c3aed; }
.tr-thinking { display: flex; flex-direction: column; gap: 2px; }
.tr-thinking-body { background: #1a0a2e; color: #c4b5fd; }
.send-form { display: flex; gap: 0.5rem; margin-top: 0.75rem; align-items: flex-end; }
.send-form textarea { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid #cbd5e1; border-radius: 6px; resize: none; font-family: inherit; font-size: 0.95rem; }
.send-form button { padding: 0.5rem 1.25rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; align-self: flex-end; }
.send-form button:disabled { opacity: 0.6; cursor: default; }
.attach-btn { background: #f1f5f9; color: #475569; padding: 0.5rem 0.6rem; font-size: 1.1rem; border: 1px solid #cbd5e1; }
.attach-btn:hover:not(:disabled) { background: #e2e8f0; }
.file-input-hidden { display: none; }
.file-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }
.file-chip { display: flex; align-items: center; gap: 4px; background: #e0f2fe; color: #0369a1; border-radius: 12px; padding: 2px 10px; font-size: 0.8em; }
.chip-remove { background: none; border: none; cursor: pointer; color: #0369a1; font-size: 0.9em; padding: 0; line-height: 1; }
.attachment-list { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.attachment-img-wrap { max-width: 280px; }
.attachment-img { max-width: 100%; border-radius: 6px; border: 1px solid #e2e8f0; }
.attachment-file { font-size: 0.82em; }
.attachment-file a { color: #2563eb; text-decoration: underline; }
.dispatch-detail { margin-top: 4px; }
.dispatch-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; font-size: 0.78em; }
.dispatch-info { color: #64748b; }
.dispatch-cmd { font-size: 0.72em; max-height: 200px; overflow-x: auto; white-space: pre; }
.send-error { color: #dc2626; font-size: 0.85em; margin-top: 0.25rem; }
.hint { font-size: 0.8em; text-align: right; margin-top: 0.25rem; }
.muted { color: #64748b; }
.center { text-align: center; }
.cursor {
  display: inline-block;
  animation: blink 1s steps(2) infinite;
}
@keyframes blink { to { visibility: hidden; } }
.trace-row { font-size: 0.85em; color: #888; padding: 1px 0; font-family: monospace; }
.trace-row details summary { cursor: pointer; }
</style>
