<template>
  <div>
    <p class="breadcrumb"><RouterLink to="/">Workspaces</RouterLink> / {{ workspace?.name || id }}</p>

    <section>
      <h2>Topics</h2>
      <form @submit.prevent="createTopic" class="create-form">
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
          <RouterLink :to="`/workspaces/${id}/topics/${t.id}`">{{ t.subject }}</RouterLink>
          <span class="muted small"> — branch: {{ t.branch_name }}</span>
        </li>
      </ul>
    </section>

    <section class="agents-section">
      <h2>Agents</h2>
      <form @submit.prevent="addAgent" class="create-form">
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
              <button class="remove-btn" @click="removeAgent(a.id)" title="Remove">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint muted">Use <code>@agent-name</code> in a topic to address a specific agent.</p>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
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

async function load() {
  loading.value = true
  try {
    const [wsRes, topicsRes, agentsRes] = await Promise.all([
      fetch(`/api/workspaces/${id}`),
      fetch(`/api/workspaces/${id}/topics`),
      fetch(`/api/workspaces/${id}/agents`),
    ])
    workspace.value = await wsRes.json()
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

async function removeAgent(agentId) {
  await fetch(`/api/workspaces/${id}/agents/${agentId}`, { method: 'DELETE' })
  await load()
}

onMounted(load)
</script>

<style scoped>
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 1rem; }
h2 { margin-bottom: 1rem; }
section { margin-bottom: 2rem; }
.agents-section { border-top: 1px solid #e2e8f0; padding-top: 1.5rem; }
.create-form { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; }
.create-form input, .create-form select { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; flex: 1; min-width: 120px; }
.create-form button { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.create-form button:disabled { opacity: 0.6; cursor: default; }
.list { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }
.list li { background: #fff; padding: 0.75rem 1rem; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
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
