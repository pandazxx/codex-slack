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

    <div
      v-if="data.listExpanded && items.length"
      ref="listEl"
      class="gn-childlist nowheel nodrag"
      :style="{ height: data.listHeight + 'px' }"
      @wheel.stop
      @mousedown.stop
    >
      <div ref="listInner" class="gn-childlist-inner">
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
import { computed, ref, watch, nextTick, onMounted } from 'vue'

const MIN_LIST_HEIGHT = 66
const LIST_PADDING = 8

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const listEl = ref(null)
const listInner = ref(null)

const agentLabel = computed(() => props.data.data.agentName ? `@${props.data.data.agentName}` : 'Agent')
const preview = computed(() => (props.data.data.text || '').slice(0, 160) || '(no text)')
const items = computed(() => props.data.childItems || [])

const cardStyle = computed(() => {
  if (props.data.listExpanded && props.data.boxWidth) {
    return { width: props.data.boxWidth + 'px', maxWidth: 'none' }
  }
  return {}
})

// Full height the list needs to show every item without scrolling, measured
// from the unconstrained inner wrapper so it's independent of the scroll box.
function naturalHeight() {
  if (!listInner.value) return Infinity
  return listInner.value.offsetHeight + LIST_PADDING
}

// Never reserve more vertical space than the content needs: if the stored
// height overshoots the content, shrink it so no empty gap is left behind.
function clampToContent() {
  const natural = naturalHeight()
  if (!Number.isFinite(natural)) return
  if ((props.data.listHeight ?? 0) > natural && props.data.onBoxResize) {
    props.data.onBoxResize(props.data.boxWidth ?? 296, natural)
  }
}

watch(() => props.data.listExpanded, (v) => { if (v) nextTick(clampToContent) })
watch(() => items.value.length, () => { if (props.data.listExpanded) nextTick(clampToContent) })
onMounted(() => { if (props.data.listExpanded) nextTick(clampToContent) })

function startResize(e) {
  const startX = e.clientX
  const startY = e.clientY
  const startW = props.data.boxWidth ?? 296
  const startH = props.data.listHeight ?? 200
  const maxH = naturalHeight()
  function onMove(ev) {
    const w = Math.max(220, startW + ev.clientX - startX)
    let h = Math.max(MIN_LIST_HEIGHT, startH + ev.clientY - startY)
    h = Math.min(h, maxH)
    props.data.onBoxResize && props.data.onBoxResize(w, h)
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
  background: #f1f5f9;
  padding: 4px;
  cursor: default;
}
.gn-childlist-inner {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.gn-childitem {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 5px 7px;
  border: 1px solid #cbd5e1;
  border-radius: 5px;
  background: #fff;
  font-size: 0.9em;
  color: #475569;
  cursor: pointer;
  min-height: 44px;
}
.gn-childitem:hover { background: #eef2f7; border-color: #94a3b8; }
.gn-childicon { flex-shrink: 0; line-height: 1.4; }
.gn-childtext {
  line-height: 1.4;
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 10;
  line-clamp: 10;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.gn-resize-handle {
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 14px;
  height: 14px;
  cursor: nwse-resize;
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
  color: #94a3b8;
  user-select: none;
  line-height: 1;
}
.gn-resize-handle::after { content: '◢'; font-size: 0.75em; }
.gn-resize-handle:hover { color: #475569; }
</style>
