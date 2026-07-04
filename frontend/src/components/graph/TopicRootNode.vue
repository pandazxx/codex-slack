<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-neutral">topic</span>
      <span class="gn-label">{{ data.data.subject || 'Topic' }}</span>
    </div>
    <div v-if="data.data.workspaceName" class="gn-preview">{{ data.data.workspaceName }}</div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])
</script>

<style scoped>
@import './graph-node.css';
</style>
