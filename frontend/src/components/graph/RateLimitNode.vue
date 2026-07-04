<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-warning">rate limit</span>
      <span v-if="data.data.tier" class="gn-label">tier {{ data.data.tier }}</span>
      <span v-if="data.data.resetsAt" class="gn-meta">resets {{ formatReset }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const formatReset = computed(() => {
  if (!props.data.data.resetsAt) return ''
  try { return new Date(props.data.data.resetsAt).toLocaleTimeString() } catch { return props.data.data.resetsAt }
})
</script>

<style scoped>
@import './graph-node.css';
.gn-meta { font-size: 0.82em; color: #64748b; }
</style>
