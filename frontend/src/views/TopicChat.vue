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
          <MarkdownMessage v-if="m.sender !== 'user'" :text="m.text" />
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

const isArchived = computed(() => !!topic.value?.archived_at)

let ws = null

function scrollToBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

async function load() {
  loading.value = true
  try {
    const [topicRes, msgsRes] = await Promise.all([
      fetch(`/api/workspaces/${wsId}/topics/${topicId}`),
      fetch(`/api/workspaces/${wsId}/topics/${topicId}/messages`),
    ])
    topic.value = await topicRes.json()
    messages.value = await msgsRes.json()
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
    } else if (data.type === 'message') {
      messages.value.push({
        id: data.message_id || Date.now().toString(),
        sender: data.sender || 'agent',
        agent_name: data.agent_name || null,
        text: data.last_response || data.text || '',
        transcript: data.transcript || null,
        created_at: new Date().toISOString(),
      })
      scrollToBottom()
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
      const saved = {
        id: data.message_id,
        sender: 'user',
        agent_name: null,
        text: msg,
        attachments: data.attachments || [],
        created_at: new Date().toISOString(),
      }
      messages.value.push(saved)
      scrollToBottom()
    }
  } finally {
    sending.value = false
  }
}

function toggleRaw(id) { rawView.value[id] = !rawView.value[id] }

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
.hint { font-size: 0.8em; text-align: right; margin-top: 0.25rem; }
.muted { color: #64748b; }
.center { text-align: center; }
</style>
