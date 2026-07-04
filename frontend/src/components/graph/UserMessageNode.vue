<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-user">you</span>
      <span class="gn-label">{{ preview }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const preview = computed(() => (props.data.data.text || '').slice(0, 80) || '(no text)')
</script>

<style scoped>
@import './graph-node.css';
.gn-card { border-color: #2563eb; }
</style>
