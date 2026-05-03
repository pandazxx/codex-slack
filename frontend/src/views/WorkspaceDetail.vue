<template>
  <div>
    <p class="breadcrumb"><RouterLink to="/">Workspaces</RouterLink> / {{ workspace?.name || id }}</p>

    <div v-if="isArchived" class="archived-banner">This workspace is archived — read only</div>

    <div v-if="!isArchived" class="agent-status-bar">
      <span class="agent-status-label">Agent container:</span>
      <span v-if="agentStatus" :class="['status-badge', statusClass]">{{ statusLabel }}</span>
      <span v-else class="status-badge status-unknown">checking…</span>
      <span v-if="agentStatus?.error" class="status-error">{{ agentStatus.error }}</span>
    </div>

    <section>
      <div class="header-row">
        <h2>Topics</h2>
        <RouterLink v-if="!isArchived" :to="`/workspaces/${id}/archived-topics`" class="archived-link">View Archived</RouterLink>
      </div>
      <form v-if="!isArchived" @submit.prevent="createTopic" class="create-form">
        <input v-model="subject" placeholder="Topic subject" required />
        <button type="submit" :disabled="creating">
          {{ creating ? 'Creating…' : 'New Topic' }}
        </button>
        <span v-if="createError" class="error">{{ createError }}</span>
      </form>

      <p v-if="loading" class="muted">Loading…</p>
      <p v-else-if="!topics.length" class="muted">No topics yet.</p>
      <ul v-else class="list">
        <li v-for="t in topics" :key="t.id">
          <span class="topic-row">
            <RouterLink :to="`/workspaces/${id}/topics/${t.id}`">{{ t.subject }}</RouterLink>
            <span class="muted small"> — branch: {{ t.branch_name }}</span>
          </span>
          <button v-if="!isArchived" class="remove-btn" @click="deleteTopic(t.id, t.subject)" title="Archive topic">Archive</button>
        </li>
      </ul>
    </section>

    <section class="agents-section">
      <h2>Agents</h2>
      <form v-if="!isArchived" @submit.prevent="addAgent" class="create-form">
        <input v-model="agentForm.agent_name" placeholder="Name (e.g. engineer)" required />
        <select v-model="agentForm.adapter">
          <option value="claude-code">claude-code</option>
          <option value="codex">codex</option>
        </select>
        <input v-model="agentForm.subagent" placeholder="Subagent (optional)" />
        <button type="submit" :disabled="addingAgent">
          {{ addingAgent ? 'Adding…' : 'Add Agent' }}
        </button>
        <span v-if="agentError" class="error">{{ agentError }}</span>
      </form>

      <p v-if="!agents.length" class="muted">No agents configured.</p>
      <table v-else class="agent-table">
        <thead>
          <tr><th>Name</th><th>Adapter</th><th>Subagent</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="a in agents" :key="a.id">
            <td><code>@{{ a.agent_name }}</code></td>
            <td>{{ a.adapter }}</td>
            <td>{{ a.subagent || '—' }}</td>
            <td>
              <button v-if="!isArchived" class="remove-btn" @click="removeAgent(a.id)" title="Remove">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint muted">Use <code>@agent-name</code> in a topic to address a specific agent.</p>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const id = route.params.id

const workspace = ref(null)
const topics = ref([])
const agents = ref([])
const loading = ref(true)
const creating = ref(false)
const createError = ref('')
const subject = ref('')
const addingAgent = ref(false)
const agentError = ref('')
const agentForm = ref({ agent_name: '', adapter: 'claude-code', subagent: '' })
const agentStatus = ref(null)
let statusTimer = null

const isArchived = computed(() => !!workspace.value?.archived_at)

const statusClass = computed(() => {
  const s = agentStatus.value?.status
  if (s === 'running') return 'status-running'
  if (s === 'restarting') return 'status-restarting'
  if (s === 'exited') return agentStatus.value?.exit_code === 0 ? 'status-stopped' : 'status-crashed'
  if (s === 'not_found') return 'status-unknown'
  return 'status-unknown'
})

const statusLabel = computed(() => {
  const s = agentStatus.value
  if (!s) return ''
  if (s.status === 'running') return 'Running'
  if (s.status === 'restarting') return `Restarting (restarts: ${s.restart_count ?? '?'})`
  if (s.status === 'exited') {
    const code = s.exit_code ?? '?'
    return code === 0 ? 'Stopped (exit 0)' : `Crashed (exit ${code}, restarts: ${s.restart_count ?? '?'})`
  }
  if (s.status === 'not_found') return 'Not found'
  if (s.status === 'dry_run') return 'Dry run'
  return s.status
})

async function fetchAgentStatus() {
  try {
    const r = await fetch(`/api/workspaces/${id}/agent-status`)
    if (r.ok) agentStatus.value = await r.json()
  } catch { /* ignore */ }
}

async function load() {
  loading.value = true
  try {
    const wsRes = await fetch(`/api/workspaces/${id}`)
    workspace.value = await wsRes.json()
    const topicsParam = workspace.value.archived_at ? '?archived=true' : ''
    const [topicsRes, agentsRes] = await Promise.all([
      fetch(`/api/workspaces/${id}/topics${topicsParam}`),
      fetch(`/api/workspaces/${id}/agents`),
    ])
    topics.value = await topicsRes.json()
    agents.value = await agentsRes.json()
  } finally {
    loading.value = false
  }
}

async function createTopic() {
  creating.value = true
  createError.value = ''
  try {
    const r = await fetch(`/api/workspaces/${id}/topics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject: subject.value }),
    })
    if (!r.ok) {
      const err = await r.json()
      createError.value = err.detail || 'Error creating topic'
      return
    }
    subject.value = ''
    await load()
  } finally {
    creating.value = false
  }
}

async function addAgent() {
  addingAgent.value = true
  agentError.value = ''
  try {
    const payload = {
      agent_name: agentForm.value.agent_name,
      adapter: agentForm.value.adapter,
      subagent: agentForm.value.subagent || null,
    }
    const r = await fetch(`/api/workspaces/${id}/agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!r.ok) {
      const err = await r.json()
      agentError.value = err.detail || 'Error adding agent'
      return
    }
    agentForm.value = { agent_name: '', adapter: 'claude-code', subagent: '' }
    await load()
  } finally {
    addingAgent.value = false
  }
}

async function deleteTopic(topicId, subject) {
  if (!confirm(`Archive topic "${subject}"?`)) return
  await fetch(`/api/workspaces/${id}/topics/${topicId}`, { method: 'DELETE' })
  await load()
}

async function removeAgent(agentId) {
  await fetch(`/api/workspaces/${id}/agents/${agentId}`, { method: 'DELETE' })
  await load()
}

onMounted(async () => {
  await load()
  if (!isArchived.value) {
    await fetchAgentStatus()
    statusTimer = setInterval(fetchAgentStatus, 10000)
  }
})

onUnmounted(() => {
  if (statusTimer) clearInterval(statusTimer)
})
</script>

<style scoped>
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 1rem; }
.archived-banner { background: #fef9c3; border: 1px solid #fde047; border-radius: 6px; padding: 0.5rem 1rem; margin-bottom: 1rem; font-size: 0.9em; color: #713f12; }
.agent-status-bar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; font-size: 0.85em; }
.agent-status-label { color: #64748b; }
.status-badge { padding: 2px 10px; border-radius: 10px; font-weight: 600; font-size: 0.85em; }
.status-running { background: #dcfce7; color: #15803d; }
.status-restarting { background: #fef9c3; color: #92400e; }
.status-crashed { background: #fee2e2; color: #dc2626; }
.status-stopped { background: #f1f5f9; color: #475569; }
.status-unknown { background: #f1f5f9; color: #94a3b8; }
.status-error { color: #dc2626; font-size: 0.9em; }
h2 { margin: 0; }
.header-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.archived-link { font-size: 0.85em; color: #64748b; text-decoration: none; }
.archived-link:hover { text-decoration: underline; color: #2563eb; }
section { margin-bottom: 2rem; }
.agents-section { border-top: 1px solid #e2e8f0; padding-top: 1.5rem; }
.create-form { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; }
.create-form input, .create-form select { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; flex: 1; min-width: 120px; }
.create-form button { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.create-form button:disabled { opacity: 0.6; cursor: default; }
.list { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }
.list li { background: #fff; padding: 0.75rem 1rem; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); display: flex; align-items: center; justify-content: space-between; }
.topic-row { display: flex; align-items: center; gap: 0.5rem; flex: 1; }
.agent-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
.agent-table th { text-align: left; padding: 0.4rem 0.75rem; border-bottom: 2px solid #e2e8f0; color: #64748b; font-weight: 600; }
.agent-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #f1f5f9; }
.remove-btn { background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 0.9em; padding: 0.15rem 0.4rem; border-radius: 3px; }
.remove-btn:hover { background: #fee2e2; color: #dc2626; }
.muted { color: #64748b; }
.small { font-size: 0.85em; }
.error { color: #dc2626; font-size: 0.9em; }
.hint { font-size: 0.82em; margin-top: 0.75rem; }
</style>
