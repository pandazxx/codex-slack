<template>
  <div>
    <p class="breadcrumb"><RouterLink to="/">Workspaces</RouterLink> / Archived</p>
    <h2>Archived Workspaces</h2>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="!workspaces.length" class="muted">No archived workspaces.</p>
    <ul v-else class="list">
      <li v-for="ws in workspaces" :key="ws.id">
        <div class="list-row">
          <span class="ws-name">{{ ws.name }}</span>
          <span class="muted small"> — {{ ws.repo_url }}</span>
          <RouterLink :to="`/workspaces/${ws.id}/archived-topics`" class="muted small link">Archived Topics</RouterLink>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const workspaces = ref([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const r = await fetch('/api/workspaces?archived=true')
    workspaces.value = await r.json()
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 1rem; }
h2 { margin-bottom: 1rem; }
.list { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }
.list li { background: #fff; padding: 0.75rem 1rem; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.list-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.ws-name { font-weight: 500; color: #334155; }
.link { margin-left: auto; text-decoration: none; color: #2563eb; }
.link:hover { text-decoration: underline; }
.muted { color: #64748b; }
.small { font-size: 0.85em; }
</style>
