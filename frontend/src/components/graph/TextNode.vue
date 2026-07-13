<template>
  <div class="gn-card" :class="{ selected: data.selected }" @click="$emit('select', data)">
    <div class="gn-header">
      <span class="gn-kind-badge gn-badge-neutral">text</span>
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
</style>
