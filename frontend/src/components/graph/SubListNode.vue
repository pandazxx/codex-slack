<template>
  <div
    class="gn-card gn-sublist"
    :class="{ selected: data.selected }"
    :style="cardStyle"
    @click="$emit('select', data)"
  >
    <div class="gn-header">
      <span class="gn-kind-badge" :class="data.badgeClass">{{ data.icon }} {{ data.kindLabel }}</span>
      <span class="gn-label">{{ data.title }}</span>
      <span v-if="items.length" class="gn-count">{{ items.length }}</span>
    </div>

    <ChildList
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

const items = computed(() => props.data.childItems || [])

const cardStyle = computed(() => {
  const w = props.data.boxWidth || 296
  return { width: w + 'px', maxWidth: 'none' }
})
</script>

<style scoped>
@import './graph-node.css';

.gn-sublist { border-style: dashed; border-color: #c7d2fe; }
.gn-count {
  flex-shrink: 0;
  font-size: 0.75em;
  color: #94a3b8;
}
</style>
