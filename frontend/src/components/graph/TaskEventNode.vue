<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-task">{{ subtypeLabel }}</span>
      <span v-if="data.data.taskType" class="gn-kind-badge gn-badge-neutral">{{ data.data.taskType }}</span>
      <span class="gn-label">{{ data.data.description || data.data.subtype }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const subtypeLabel = computed(() => {
  const s = props.data.data.subtype || ''
  if (s === 'task_started')      return 'started'
  if (s === 'task_progress')     return 'progress'
  if (s === 'task_updated')      return 'updated'
  if (s === 'task_notification') return 'notify'
  return s
})
</script>

<style scoped>
@import './graph-node.css';
</style>
