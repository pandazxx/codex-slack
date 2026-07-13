<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-tool">tool</span>
      <template v-if="data.data.mcpServer">
        <span class="gn-kind-badge gn-badge-neutral">{{ data.data.mcpServer }}</span>
        <span class="gn-label">{{ data.data.mcpTool }}</span>
      </template>
      <span v-else class="gn-label">{{ data.data.toolName }}</span>
      <button v-if="data.hasChildren" class="gn-chevron" @click.stop="data.onExpandToggle && data.onExpandToggle()">
        {{ data.ui.collapsed ? '▶' : '▼' }}
      </button>
    </div>
    <div class="gn-preview">{{ data.data.label }}</div>
    <div
      v-if="data.data.result"
      class="gn-result-line"
      :class="{ 'gn-result-error': data.data.result.isError }"
    >
      <span class="gn-kind-badge" :class="data.data.result.isError ? 'gn-badge-error' : 'gn-badge-result'">
        {{ data.data.result.isError ? 'error' : 'result' }}
      </span>
      <span class="gn-result-text">{{ resultPreview }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const resultPreview = computed(() =>
  (props.data.data.result?.contentText || '').slice(0, 160) || '(empty)')
</script>

<style scoped>
@import './graph-node.css';
</style>
