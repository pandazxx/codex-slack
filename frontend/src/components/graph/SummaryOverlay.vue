<template>
  <div v-if="summaries.length" class="summary-overlay">
    <div v-for="s in summaries" :key="s.id" class="summary-item">
      <div class="summary-header">
        <span class="summary-kind">{{ s.kind }}</span>
        <span class="summary-meta">{{ s.producedBy }} · {{ formatDate(s.producedAt) }}</span>
      </div>
      <div v-if="s.title" class="summary-title">{{ s.title }}</div>
      <MarkdownMessage :text="s.body" />
    </div>
  </div>
</template>

<script setup>
import MarkdownMessage from '../MarkdownMessage.vue'

defineProps({
  summaries: { type: Array, default: () => [] },
})

function formatDate(iso) {
  try { return new Date(iso).toLocaleString() } catch { return iso }
}
</script>

<style scoped>
.summary-overlay { margin-top: 12px; border-top: 1px solid #e2e8f0; padding-top: 8px; }
.summary-item { margin-bottom: 12px; }
.summary-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.summary-kind { font-size: 0.72em; padding: 1px 6px; border-radius: 8px; background: #eff6ff; color: #1d4ed8; font-weight: 600; }
.summary-meta { font-size: 0.72em; color: #94a3b8; }
.summary-title { font-weight: 600; font-size: 0.88em; margin-bottom: 4px; color: #1e293b; }
</style>
