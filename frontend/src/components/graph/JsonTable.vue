<template>
  <span v-if="!isNested" class="jt-scalar">{{ scalarText }}</span>
  <table v-else class="jt-table">
    <tbody>
      <tr v-for="[k, v] in entries" :key="k">
        <td class="jt-key">{{ k }}</td>
        <td class="jt-cell"><JsonTable :value="v" /></td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ value: { default: null } })

const isNested = computed(() =>
  props.value !== null &&
  typeof props.value === 'object' &&
  Object.keys(props.value).length > 0)

const entries = computed(() =>
  Array.isArray(props.value)
    ? props.value.map((v, i) => [i, v])
    : Object.entries(props.value ?? {}))

const scalarText = computed(() => {
  const v = props.value
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'object') return Array.isArray(v) ? '[]' : '{}'
  return String(v)
})
</script>

<style scoped>
.jt-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 0.95em;
}
.jt-table .jt-table {
  border-left: 2px solid #e2e8f0;
}
.jt-key {
  color: #94a3b8;
  font-family: monospace;
  font-size: 0.9em;
  vertical-align: top;
  padding: 3px 8px 3px 0;
  white-space: nowrap;
}
.jt-cell {
  padding: 3px 0;
  vertical-align: top;
  width: 100%;
}
.jt-table tr + tr > td {
  border-top: 1px solid #f1f5f9;
}
.jt-scalar {
  font-family: monospace;
  font-size: 0.92em;
  color: #1e293b;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
