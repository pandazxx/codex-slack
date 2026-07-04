<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-warning">parse warning</span>
      <span class="gn-label">{{ (data.data.line || '').slice(0, 60) }}</span>
    </div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])
</script>

<style scoped>
@import './graph-node.css';
.gn-card { border-color: #f59e0b; }
</style>
