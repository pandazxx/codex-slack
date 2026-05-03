<template>
  <div>
    <p class="breadcrumb">
      <RouterLink to="/">Workspaces</RouterLink> /
      <RouterLink :to="`/workspaces/${id}`">{{ workspaceName }}</RouterLink> /
      Archived Topics
    </p>
    <h2>Archived Topics</h2>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="!topics.length" class="muted">No archived topics.</p>
    <ul v-else class="list">
      <li v-for="t in topics" :key="t.id">
        <div class="list-row">
          <RouterLink :to="`/workspaces/${id}/topics/${t.id}`" class="topic-name">{{ t.subject }}</RouterLink>
          <span class="muted small"> — branch: {{ t.branch_name }}</span>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const id = route.params.id

const workspaceName = ref('')
const topics = ref([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const [wsRes, topicsRes] = await Promise.all([
      fetch(`/api/workspaces/${id}`),
      fetch(`/api/workspaces/${id}/topics?archived=true`),
    ])
    if (wsRes.ok) {
      const ws = await wsRes.json()
      workspaceName.value = ws.name
    }
    topics.value = await topicsRes.json()
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
.topic-name { font-weight: 500; text-decoration: none; color: #334155; }
.topic-name:hover { text-decoration: underline; color: #2563eb; }
.muted { color: #64748b; }
.small { font-size: 0.85em; }
</style>
