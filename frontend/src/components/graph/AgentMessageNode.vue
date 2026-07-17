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
      <button v-if="items.length" class="gn-chevron" @click.stop="data.onListToggle && data.onListToggle()">
        {{ data.listExpanded ? '▼' : '▶' }} {{ items.length }}
      </button>
      <span v-if="data.data.interrupted" class="gn-kind-badge gn-badge-warning">interrupted</span>
    </div>
    <div class="gn-preview">{{ preview }}</div>

    <div
      v-if="data.listExpanded && items.length"
      class="gn-childlist nowheel nodrag"
      :style="{ height: data.listHeight + 'px' }"
      @wheel.stop
      @mousedown.stop
    >
      <div
        v-for="it in items"
        :key="it.id"
        class="gn-childitem"
        :title="it.text"
        @click.stop="data.onChildSelect && data.onChildSelect(it.id)"
      >
        <span class="gn-childicon">{{ it.icon }}</span>
        <span class="gn-childtext">{{ it.text }}</span>
      </div>
    </div>
    <div
      v-if="data.listExpanded && items.length"
      class="gn-resize-handle nodrag"
      title="Drag to resize"
      @mousedown.stop.prevent="startResize"
    ></div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const agentLabel = computed(() => props.data.data.agentName ? `@${props.data.data.agentName}` : 'Agent')
const preview = computed(() => (props.data.data.text || '').slice(0, 160) || '(no text)')
const items = computed(() => props.data.childItems || [])

function startResize(e) {
  const startY = e.clientY
  const startH = props.data.listHeight ?? 200
  function onMove(ev) {
    const h = Math.max(60, startH + ev.clientY - startY)
    props.data.onListResize && props.data.onListResize(h)
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
</script>

<style scoped>
@import './graph-node.css';

.gn-childlist {
  margin-top: 6px;
  overflow-y: auto;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  padding: 2px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  cursor: default;
}
.gn-childitem {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-radius: 4px;
  font-size: 0.9em;
  color: #475569;
  cursor: pointer;
}
.gn-childitem:hover { background: #e2e8f0; }
.gn-childicon { flex-shrink: 0; }
.gn-childtext { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.gn-resize-handle {
  height: 10px;
  cursor: ns-resize;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  user-select: none;
}
.gn-resize-handle::after { content: '⋯'; letter-spacing: 3px; font-size: 0.7em; }
.gn-resize-handle:hover { color: #475569; }
</style>
