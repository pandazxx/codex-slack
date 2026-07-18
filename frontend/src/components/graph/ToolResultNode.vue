<template>
  <div
    class="gn-card"
    :class="{ selected: data.selected, 'gn-card-error': data.data.isError }"
    @click="$emit('select', data)"
  >
    <div class="gn-header">
      <span class="gn-kind-badge" :class="data.data.isError ? 'gn-badge-error' : 'gn-badge-result'">
        {{ data.data.isError ? 'error' : 'result' }}
      </span>
    </div>
    <div class="gn-preview">{{ preview }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ data: { type: Object, required: true } })
defineEmits(['select'])

const preview = computed(() => (props.data.data.contentText || '').slice(0, 160) || '(empty)')
</script>

<style scoped>
@import './graph-node.css';
</style>
