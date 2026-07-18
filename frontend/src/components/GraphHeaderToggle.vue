<template>
  <div class="view-toggle">
    <RouterLink
      :to="`/workspaces/${wsId}/topics/${topicId}`"
      class="toggle-btn"
      :class="{ active: activeView === 'chat' }"
    >Chat</RouterLink>
    <RouterLink
      :to="`/workspaces/${wsId}/topics/${topicId}/graph`"
      class="toggle-btn"
      :class="{ active: activeView === 'graph' }"
    >Graph</RouterLink>
  </div>
</template>

<script setup>
defineProps({
  wsId: { type: String, required: true },
  topicId: { type: String, required: true },
  activeView: { type: String, default: 'chat' },
})
</script>

<style scoped>
.view-toggle {
  display: inline-flex;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
  font-size: 0.82em;
}
.toggle-btn {
  padding: 3px 12px;
  text-decoration: none;
  color: #64748b;
  background: #fff;
  transition: background 0.1s, color 0.1s;
}
.toggle-btn:hover { background: #f1f5f9; color: #1e293b; }
.toggle-btn.active { background: #1e293b; color: #fff; }
</style>
