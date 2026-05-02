<template>
  <div>
    <p class="breadcrumb"><RouterLink to="/">Workspaces</RouterLink> / {{ workspace?.name || id }}</p>
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const id = route.params.id

const workspace = ref(null)
const topics = ref([])
const loading = ref(true)
const creating = ref(false)
const createError = ref('')
const subject = ref('')

async function load() {
  loading.value = true
  try {
    const [wsRes, topicsRes] = await Promise.all([
      fetch(`/workspaces/${id}`),
      fetch(`/workspaces/${id}/topics`),
    ])
    workspace.value = await wsRes.json()
    topics.value = await topicsRes.json()
  } finally {
    loading.value = false
  }
}

async function createTopic() {
  creating.value = true
  createError.value = ''
  try {
    const r = await fetch(`/workspaces/${id}/topics`, {
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

onMounted(load)
</script>

<style scoped>
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 1rem; }
h2 { margin-bottom: 1rem; }
.create-form { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; }
.create-form input { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; flex: 1; min-width: 200px; }
.create-form button { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.create-form button:disabled { opacity: 0.6; cursor: default; }
.list { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }
.list li { background: #fff; padding: 0.75rem 1rem; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.muted { color: #64748b; }
.small { font-size: 0.85em; }
.error { color: #dc2626; font-size: 0.9em; }
</style>
