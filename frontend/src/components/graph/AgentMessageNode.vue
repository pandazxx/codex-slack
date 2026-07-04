<template>
  <div
    class="gn-card"
    :class="{
      selected: data.selected,
      'gn-card-interrupted': data.data.interrupted,
      'gn-card-done': !data.data.interrupted,
    }"
    @click="$emit('select', data)"
  >
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-agent">{{ agentLabel }}</span>
      <button v-if="data.data.hasTranscript" class="gn-chevron" @click.stop="$emit('expand-toggle', data)">
        {{ data.ui.collapsed ? '▶' : '▼' }}
      </button>
      <span v-if="data.data.interrupted" class="gn-kind-badge gn-badge-warning">interrupted</span>
    </div>
    <div class="gn-preview">{{ preview }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select', 'expand-toggle'])

const agentLabel = computed(() => props.data.data.agentName ? `@${props.data.data.agentName}` : 'Agent')
const preview = computed(() => (props.data.data.text || '').slice(0, 80) || '(no text)')
</script>

<style scoped>
@import './graph-node.css';
</style>
