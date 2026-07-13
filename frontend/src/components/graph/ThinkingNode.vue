<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-thinking">thinking</span>
    </div>
    <div class="gn-preview">{{ preview }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const preview = computed(() => (props.data.data.text || '').slice(0, 160) || '…')
</script>

<style scoped>
@import './graph-node.css';
.gn-card { border-left: 3px solid #c0a85a; }
</style>
