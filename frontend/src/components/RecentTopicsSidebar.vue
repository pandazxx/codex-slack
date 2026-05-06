<template>
  <aside class="sidebar">
    <div class="sidebar-title">Recent Topics</div>
    <RouterLink
      v-for="topic in topics"
      :key="topic.topic_id"
      :to="`/workspaces/${topic.workspace_id}/topics/${topic.topic_id}`"
      active-class="active"
      @click="markSeen(topic.topic_id)"
    >
      <span class="topic-name">{{ topic.workspace_name }}/{{ topic.subject }}</span>
      <span v-if="isNew(topic)" class="new-badge">new</span>
    </RouterLink>
    <div v-if="topics.length === 0" class="empty">No topics yet</div>
  </aside>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'

let ws = null
let wsTimer = null

const route = useRoute()
const topics = ref([])

async function load() {
  try {
    const res = await fetch('/api/topics/recent?limit=20', { cache: 'no-store' })
    if (res.ok) topics.value = await res.json()
  } catch {}
}

function nowTs() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
}

function getLastSeen(topicId) {
  return localStorage.getItem(`topic-last-seen:${topicId}`)
}

function markSeen(topicId) {
  localStorage.setItem(`topic-last-seen:${topicId}`, nowTs())
}

function isNew(topic) {
  const lastSeen = getLastSeen(topic.topic_id)
  if (!lastSeen) return false
  return topic.last_activity_at > lastSeen
}

function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/events`)
  ws.onmessage = () => load()
  ws.onclose = () => { wsTimer = setTimeout(connectWs, 3000) }
}

// Refresh sidebar on every navigation; mark topic as seen when leaving it
watch(
  () => route.params.topicId,
  (topicId, prevTopicId) => {
    if (prevTopicId && prevTopicId !== topicId) markSeen(prevTopicId)
    load()
  },
)

let interval
onMounted(() => {
  load()
  connectWs()
  interval = setInterval(load, 60_000)
})
onUnmounted(() => {
  clearInterval(interval)
  clearTimeout(wsTimer)
  if (ws) { ws.onclose = null; ws.close() }
})
</script>

<style scoped>
.sidebar {
  width: 220px;
  min-width: 220px;
  background: #1e293b;
  color: #cbd5e1;
  padding: 1rem 0;
  overflow-y: auto;
  border-right: 1px solid #334155;
}

.sidebar-title {
  padding: 0 1rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: #64748b;
}

.sidebar a {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.35rem 1rem;
  font-size: 0.82rem;
  gap: 0.4rem;
  line-height: 1.3;
  text-decoration: none;
  color: #cbd5e1;
}

.sidebar a:hover {
  background: #334155;
}

.sidebar a.active {
  background: #1d4ed8;
  color: #f8fafc;
}

.topic-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.new-badge {
  flex-shrink: 0;
  background: #ef4444;
  color: #fff;
  font-size: 0.6rem;
  padding: 0.1em 0.45em;
  border-radius: 9999px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.empty {
  padding: 0.5rem 1rem;
  font-size: 0.8rem;
  color: #475569;
  font-style: italic;
}
</style>
