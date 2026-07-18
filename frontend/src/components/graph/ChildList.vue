<template>
  <div
    ref="listEl"
    class="gn-childlist nowheel nodrag"
    :style="{ height: height + 'px' }"
    @wheel.stop
    @mousedown.stop
  >
    <div ref="listInner" class="gn-childlist-inner">
      <div
        v-for="it in items"
        :key="it.id"
        class="gn-childitem"
        :class="{ 'gn-childitem-open': it.expanded }"
        :title="it.text"
        @click.stop="$emit('select', it.id)"
      >
        <span class="gn-childicon">{{ it.icon }}</span>
        <span class="gn-childtext">{{ it.text }}</span>
        <button
          v-if="it.hasChildren"
          class="gn-childexpand"
          :title="it.expanded ? 'Collapse box' : 'Expand into box'"
          @click.stop="$emit('toggle', it.id)"
        >{{ it.expanded ? '▼' : '▶' }}</button>
        <Handle
          v-if="it.hasChildren"
          type="source"
          :id="it.id"
          :position="Position.Right"
          class="gn-item-handle"
        />
      </div>
    </div>
  </div>
  <div
    class="gn-resize-handle nodrag"
    title="Drag to resize"
    @mousedown.stop.prevent="startResize"
  ></div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted } from 'vue'
import { Handle, Position } from '@vue-flow/core'

const MIN_LIST_HEIGHT = 66
const LIST_PADDING = 8
const ITEM_GAP = 4

const props = defineProps({
  items: { type: Array, default: () => [] },
  height: { type: Number, default: 200 },
  width: { type: Number, default: 296 },
})
const emit = defineEmits(['select', 'toggle', 'resize'])

const listEl = ref(null)
const listInner = ref(null)

// Height at which every item is fully shown without scrolling. Items are
// flex-shrunk to fit the current box, so each item's full content height is
// read from its scrollHeight (which ignores the overflow clip) rather than
// its rendered height.
function naturalHeight() {
  const inner = listInner.value
  if (!inner || !inner.children.length) return Infinity
  let total = LIST_PADDING + ITEM_GAP * (inner.children.length - 1)
  for (const el of inner.children) total += el.scrollHeight
  return total
}

// Never reserve more vertical space than the content needs: if the current
// height overshoots the content, shrink it so no empty gap is left behind.
function clampToContent() {
  const natural = naturalHeight()
  if (!Number.isFinite(natural)) return
  if ((props.height ?? 0) > natural) emit('resize', props.width ?? 296, natural)
}

watch(() => props.items.length, () => nextTick(clampToContent))
onMounted(() => nextTick(clampToContent))

function startResize(e) {
  const startX = e.clientX
  const startY = e.clientY
  const startW = props.width ?? 296
  const startH = props.height ?? 200
  const maxH = naturalHeight()
  function onMove(ev) {
    const w = Math.max(220, startW + ev.clientX - startX)
    let h = Math.max(MIN_LIST_HEIGHT, startH + ev.clientY - startY)
    h = Math.min(h, maxH)
    emit('resize', w, h)
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
  height: 100%;
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
  flex: 1 1 auto;
  min-height: 44px;
  overflow: hidden;
  position: relative;
}
/* Invisible edge anchor: gives the pop-out linkage line its origin at the
 * right edge of this specific item row rather than at the whole box. */
.gn-item-handle {
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 6px;
  height: 6px;
  min-width: 0;
  min-height: 0;
  border: none !important;
  background: transparent !important;
  opacity: 0;
  pointer-events: none;
}
.gn-childitem:hover { background: #eef2f7; border-color: #94a3b8; }
.gn-childitem-open { border-color: #6c8cff; background: #f5f7ff; }
.gn-childicon { flex-shrink: 0; line-height: 1.4; }
.gn-childtext {
  line-height: 1.4;
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  flex: 1;
}
.gn-childexpand {
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.85em;
  color: #6c8cff;
  padding: 0 2px;
  line-height: 1.4;
}
.gn-childexpand:hover { color: #4338ca; }
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
