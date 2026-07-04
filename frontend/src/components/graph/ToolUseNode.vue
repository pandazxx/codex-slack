<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-tool">tool</span>
      <template v-if="data.data.mcpServer">
        <span class="gn-kind-badge gn-badge-neutral">{{ data.data.mcpServer }}</span>
        <span class="gn-label">{{ data.data.mcpTool }}</span>
      </template>
      <span v-else class="gn-label">{{ data.data.toolName }}</span>
      <button class="gn-chevron" @click.stop="$emit('expand-toggle', data)">
        {{ data.ui.collapsed ? '▶' : '▼' }}
      </button>
    </div>
    <div class="gn-preview">{{ data.data.label }}</div>
  </div>
</template>

<script setup>
defineProps({ data: { type: Object, required: true } })
defineEmits(['select', 'expand-toggle'])
</script>

<style scoped>
@import './graph-node.css';
</style>
