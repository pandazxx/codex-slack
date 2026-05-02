<template>
  <div>
    <h2>Workspaces</h2>

    <form @submit.prevent="createWorkspace" class="create-form">
      <input v-model="form.name" placeholder="Name" required />
      <input v-model="form.repo_url" placeholder="Repository URL" required />
      <button type="submit" :disabled="creating">
        {{ creating ? 'Creating…' : 'New Workspace' }}
      </button>
      <span v-if="createError" class="error">{{ createError }}</span>
    </form>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="!workspaces.length" class="muted">No workspaces yet.</p>
    <ul v-else class="list">
      <li v-for="ws in workspaces" :key="ws.id">
        <RouterLink :to="`/workspaces/${ws.id}`">{{ ws.name }}</RouterLink>
        <span class="muted small"> — {{ ws.repo_url }}</span>
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
const form = ref({ name: '', repo_url: '' })

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
    const r = await fetch('/api/workspaces', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
    })
    if (!r.ok) {
      const err = await r.json()
      createError.value = err.detail || 'Error creating workspace'
      return
    }
    form.value = { name: '', repo_url: '' }
    await load()
  } finally {
    creating.value = false
  }
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
.muted { color: #64748b; }
.small { font-size: 0.85em; }
.error { color: #dc2626; font-size: 0.9em; }
</style>
