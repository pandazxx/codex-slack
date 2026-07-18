<template>
  <div
    class="gn-card"
    :class="{ selected: data.selected, 'gn-card-error': data.data.isError, 'gn-card-done': !data.data.isError }"
    @click="$emit('select', data)"
  >
    <div class="gn-header">
      <span class="gn-kind-badge" :class="data.data.isError ? 'gn-badge-error' : 'gn-badge-result'">
        {{ data.data.isError ? 'error' : 'done' }}
      </span>
      <span v-if="data.data.cost != null" class="gn-label">${{ data.data.cost.toFixed(4) }}</span>
      <span v-if="data.data.durationMs != null" class="gn-meta">{{ (data.data.durationMs / 1000).toFixed(1) }}s</span>
    </div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])
</script>

<style scoped>
@import './graph-node.css';
.gn-meta { font-size: 0.82em; color: #64748b; }
</style>
