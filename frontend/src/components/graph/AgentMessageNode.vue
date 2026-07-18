<template>
  <div
    class="gn-card"
    :class="{
      selected: data.selected,
      'gn-card-interrupted': data.data.interrupted,
      'gn-card-done': !data.data.interrupted,
    }"
    :style="cardStyle"
    @click="$emit('select', data)"
  >
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-agent">{{ agentLabel }}</span>
      <button v-if="items.length" class="gn-chevron" @click.stop="data.onListToggle && data.onListToggle()">
        {{ data.listExpanded ? '▼' : '▶' }} {{ items.length }}
      </button>
      <span v-if="data.data.interrupted" class="gn-kind-badge gn-badge-warning">interrupted</span>
    </div>
    <div class="gn-preview">{{ preview }}</div>

    <ChildList
      v-if="data.listExpanded && items.length"
      :items="items"
      :height="data.listHeight"
      :width="data.boxWidth"
      @select="(id) => data.onChildSelect && data.onChildSelect(id)"
      @toggle="(id) => data.onItemToggle && data.onItemToggle(id)"
      @resize="(w, h) => data.onBoxResize && data.onBoxResize(w, h)"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ChildList from './ChildList.vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const agentLabel = computed(() => props.data.data.agentName ? `@${props.data.data.agentName}` : 'Agent')
const preview = computed(() => (props.data.data.text || '').slice(0, 160) || '(no text)')
const items = computed(() => props.data.childItems || [])

const cardStyle = computed(() => {
  if (props.data.listExpanded && props.data.boxWidth) {
    return { width: props.data.boxWidth + 'px', maxWidth: 'none' }
  }
  return {}
})
</script>

<style scoped>
@import './graph-node.css';
</style>
