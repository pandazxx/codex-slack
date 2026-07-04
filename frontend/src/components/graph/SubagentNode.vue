<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-subagent">subagent</span>
      <span v-if="data.data.agentType" class="gn-label">{{ data.data.agentType }}</span>
      <span v-else class="gn-label">Agent</span>
      <button class="gn-chevron" @click.stop="$emit('expand-toggle', data)">
        {{ data.ui.collapsed ? '▶' : '▼' }}
      </button>
    </div>
    <div v-if="data.data.prompt" class="gn-preview">{{ (data.data.prompt || '').slice(0, 60) }}</div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
defineEmits(['select', 'expand-toggle'])
</script>

<style scoped>
@import './graph-node.css';
.gn-card { border-left: 3px solid #6c8cff; }
</style>
