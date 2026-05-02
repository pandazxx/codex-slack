<template>
  <div>
    <h2>Workspaces</h2>

    <form @submit.prevent="createWorkspace" class="create-form">
      <input v-model="form.name" placeholder="Name" required />
      <input v-model="form.repo_url" placeholder="Repository URL" required />
      <input v-model="form.repo_ref" placeholder="Branch (default: master)" />
      <button type="submit" :disabled="creating">
        {{ creating ? 'Creating…' : 'New Workspace' }}
      </button>
      <span v-if="createError" class="error">{{ createError }}</span>
    </form>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="!workspaces.length" class="muted">No workspaces yet.</p>
    <ul v-else class="list">
      <li v-for="ws in workspaces" :key="ws.id">
        <div class="list-row">
          <RouterLink :to="`/workspaces/${ws.id}`">{{ ws.name }}</RouterLink>
          <span class="muted small"> — {{ ws.repo_url }}</span>
          <button class="remove-btn" @click.prevent="deleteWorkspace(ws.id, ws.name)" title="Delete workspace">✕</button>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const workspaces = ref([])
const loading = ref(true)
const creating = ref(false)
const createError = ref('')
const form = ref({ name: '', repo_url: '', repo_ref: '' })

async function load() {
  loading.value = true
  try {
    const r = await fetch('/api/workspaces')
    workspaces.value = await r.json()
  } finally {
    loading.value = false
  }
}

async function createWorkspace() {
  creating.value = true
  createError.value = ''
  try {
    const payload = { name: form.value.name, repo_url: form.value.repo_url }
    if (form.value.repo_ref.trim()) payload.repo_ref = form.value.repo_ref.trim()
    const r = await fetch('/api/workspaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!r.ok) {
      const err = await r.json()
      createError.value = err.detail || 'Error creating workspace'
      return
    }
    form.value = { name: '', repo_url: '', repo_ref: '' }
    await load()
  } finally {
    creating.value = false
  }
}

async function deleteWorkspace(id, name) {
  if (!confirm(`Delete workspace "${name}"? This cannot be undone.`)) return
  await fetch(`/api/workspaces/${id}`, { method: 'DELETE' })
  await load()
}

onMounted(load)
</script>

<style scoped>
h2 { margin-bottom: 1rem; }
.create-form { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; }
.create-form input { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; flex: 1; min-width: 160px; }
.create-form button { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.create-form button:disabled { opacity: 0.6; cursor: default; }
.list { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }
.list li { background: #fff; padding: 0.75rem 1rem; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.list-row { display: flex; align-items: center; gap: 0.5rem; }
.list-row a { font-weight: 500; }
.list-row .muted { flex: 1; }
.remove-btn { background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 0.9em; padding: 0.15rem 0.4rem; border-radius: 3px; }
.remove-btn:hover { background: #fee2e2; color: #dc2626; }
.muted { color: #64748b; }
.small { font-size: 0.85em; }
.error { color: #dc2626; font-size: 0.9em; }
</style>
