<template>
  <div class="gn-card compaction-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-compact">compaction</span>
      <span v-if="tokenReduction" class="gn-label">{{ tokenReduction }}</span>
      <span v-if="data.data.trigger" class="gn-meta">{{ data.data.trigger }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const tokenReduction = computed(() => {
  const { preTokens, postTokens } = props.data.data
  if (preTokens == null || postTokens == null) return null
  const pct = preTokens > 0 ? Math.round((1 - postTokens / preTokens) * 100) : 0
  return `${preTokens.toLocaleString()} → ${postTokens.toLocaleString()} (-${pct}%)`
})
</script>

<style scoped>
@import './graph-node.css';
.compaction-card { border-style: dashed; border-color: #a855f7; }
.gn-meta { font-size: 0.82em; color: #64748b; }
</style>
