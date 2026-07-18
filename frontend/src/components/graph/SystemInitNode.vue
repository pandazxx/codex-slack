<template>
  <div class="gn-card system-init-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-neutral">init</span>
      <span class="gn-label">{{ data.data.model || 'system init' }}</span>
    </div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])
</script>

<style scoped>
@import './graph-node.css';
.system-init-card { opacity: 0.65; }
</style>
