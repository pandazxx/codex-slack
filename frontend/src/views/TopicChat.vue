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
.send-form { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.send-form textarea { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid #cbd5e1; border-radius: 6px; resize: none; font-family: inherit; font-size: 0.95rem; }
.send-form button { padding: 0.5rem 1.25rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; align-self: flex-end; }
.send-form button:disabled { opacity: 0.6; cursor: default; }
.hint { font-size: 0.8em; text-align: right; margin-top: 0.25rem; }
.muted { color: #64748b; }
.center { text-align: center; }
</style>
