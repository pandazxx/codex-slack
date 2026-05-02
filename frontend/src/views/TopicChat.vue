<template>
  <div class="chat-layout">
    <p class="breadcrumb">
      <RouterLink to="/">Workspaces</RouterLink> /
      <RouterLink :to="`/workspaces/${wsId}`">{{ wsId }}</RouterLink> /
      {{ topic?.subject || topicId }}
    </p>

    <div class="status-bar" v-if="agentStatus">
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
        <span class="label">{{ m.sender === 'user' ? 'You' : (m.agent_name || 'Agent') }}</span>
        <div class="bubble">{{ m.text }}</div>
        <details v-if="m.sender === 'agent' && m.transcript" class="detail-panel">
          <summary class="detail-toggle">Details</summary>
          <div class="transcript-view">
            <template v-for="(evt, i) in parseTranscript(m.transcript)" :key="i">
              <template v-if="evt.type === 'assistant' && evt.message">
                <div v-for="(blk, j) in (evt.message.content || [])" :key="j">
                  <div v-if="blk.type === 'text' && blk.text" class="tr-text">{{ blk.text }}</div>
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
                  <span v-if="evt.cost_usd != null" class="tr-stat">${{ evt.cost_usd.toFixed(4) }}</span>
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

    <form @submit.prevent="sendMessage" class="send-form">
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
    <p class="hint muted">Enter to send · Shift+Enter for new line</p>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'

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

async function sendMessage() {
  const msg = text.value.trim()
  if (!msg || sending.value) return
  sending.value = true
  try {
    const r = await fetch(`/api/workspaces/${wsId}/topics/${topicId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: msg }),
    })
    if (r.ok) {
      text.value = ''
      const saved = {
        id: (await r.json()).message_id,
        sender: 'user',
        agent_name: null,
        text: msg,
        created_at: new Date().toISOString(),
      }
      messages.value.push(saved)
      scrollToBottom()
    }
  } finally {
    sending.value = false
  }
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

onMounted(() => {
  load()
  connectWs()
})

onUnmounted(() => {
  if (ws) { ws.onclose = null; ws.close() }
})
</script>

<style scoped>
.chat-layout { display: flex; flex-direction: column; height: calc(100vh - 110px); }
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 0.5rem; }
.status-bar { font-size: 0.85em; background: #fef9c3; border: 1px solid #fde047; border-radius: 4px; padding: 0.25rem 0.75rem; margin-bottom: 0.5rem; }
.messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 0.75rem; padding: 0.5rem 0; }
.message { display: flex; flex-direction: column; max-width: 72%; }
.message.user { align-self: flex-end; align-items: flex-end; }
.message.agent { align-self: flex-start; align-items: flex-start; }
.label { font-size: 0.75em; color: #64748b; margin-bottom: 2px; }
.bubble { padding: 0.6rem 0.9rem; border-radius: 12px; white-space: pre-wrap; line-height: 1.45; }
.message.user .bubble { background: #2563eb; color: #fff; border-bottom-right-radius: 3px; }
.message.agent .bubble { background: #fff; border: 1px solid #e2e8f0; border-bottom-left-radius: 3px; }
.ts { font-size: 0.7em; color: #94a3b8; margin-top: 2px; }
.detail-panel { margin-top: 4px; max-width: 100%; }
.detail-toggle { font-size: 0.72em; color: #94a3b8; cursor: pointer; user-select: none; }
.detail-toggle:hover { color: #64748b; }
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
.send-form { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.send-form textarea { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid #cbd5e1; border-radius: 6px; resize: none; font-family: inherit; font-size: 0.95rem; }
.send-form button { padding: 0.5rem 1.25rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; align-self: flex-end; }
.send-form button:disabled { opacity: 0.6; cursor: default; }
.hint { font-size: 0.8em; text-align: right; margin-top: 0.25rem; }
.muted { color: #64748b; }
.center { text-align: center; }
</style>
