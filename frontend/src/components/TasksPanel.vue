<template>
  <section v-if="tasks.length" class="tasks-panel">
    <button class="tasks-panel-header" @click="collapsed = !collapsed" :aria-expanded="!collapsed">
      <span class="tasks-panel-title">Tasks <span class="tasks-count-badge">{{ tasks.length }}</span></span>
      <span class="tasks-panel-toggle" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
    </button>
    <ul v-if="!collapsed" class="tasks-list">
      <li
        v-for="t in tasks"
        :key="t.id"
        class="task-row"
        :class="{ 'task-row-active': activeTaskId === t.id }"
        @click="toggleTask(t.id)"
        :title="t.goal"
      >
        <span :class="stateBadgeClass(t.state)" class="task-state-badge">{{ stateLabel(t.state) }}</span>
        <span class="task-assignee">@{{ t.assignee_name }}</span>
        <span class="task-goal">{{ shortGoal(t.goal) }}</span>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  wsId:        { type: String, required: true },
  topicId:     { type: String, required: true },
  activeTaskId: { type: String, default: null },
})

const emit = defineEmits(['select-task'])

const tasks = ref([])
const collapsed = ref(false)

async function load() {
  const r = await fetch(`/api/workspaces/${props.wsId}/topics/${props.topicId}/orchestrate/tasks`)
  if (r.ok) tasks.value = await r.json()
}

function refresh() { load() }

function toggleTask(taskId) {
  emit('select-task', props.activeTaskId === taskId ? null : taskId)
}

function shortGoal(goal) {
  if (!goal) return ''
  return goal.length > 60 ? goal.slice(0, 60) + '…' : goal
}

function stateLabel(state) {
  switch (state) {
    case 'submitted':       return 'queued'
    case 'working':         return 'working'
    case 'input-required':  return 'waiting'
    case 'completed':       return 'done'
    case 'failed':          return 'failed'
    case 'escalated':       return 'escalated'
    default:                return state
  }
}

function stateBadgeClass(state) {
  switch (state) {
    case 'submitted':       return 'task-badge task-badge-queued'
    case 'working':         return 'task-badge task-badge-working'
    case 'input-required':  return 'task-badge task-badge-waiting'
    case 'completed':       return 'task-badge task-badge-done'
    case 'failed':          return 'task-badge task-badge-failed'
    case 'escalated':       return 'task-badge task-badge-waiting'
    default:                return 'task-badge task-badge-queued'
  }
}

watch(() => [props.wsId, props.topicId], load, { immediate: true })

defineExpose({ refresh })
</script>

<style scoped>
.tasks-panel {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 0.5rem;
  background: #f8fafc;
  overflow: hidden;
}

.tasks-panel-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0.75rem;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.8em;
  font-weight: 600;
  color: #475569;
  text-align: left;
}
.tasks-panel-header:hover { background: #f1f5f9; }

.tasks-panel-title { display: flex; align-items: center; gap: 0.4rem; }
.tasks-panel-toggle { color: #94a3b8; font-size: 0.85em; }

.tasks-count-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.25rem;
  height: 1.25rem;
  padding: 0 0.3rem;
  background: #e2e8f0;
  color: #475569;
  border-radius: 9999px;
  font-size: 0.78em;
  font-weight: 700;
}

.tasks-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid #e2e8f0;
}

.task-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.35rem 0.75rem;
  cursor: pointer;
  font-size: 0.78em;
  border-bottom: 1px solid #f1f5f9;
  transition: background 0.1s;
}
.task-row:last-child { border-bottom: none; }
.task-row:hover { background: #e9f0fb; }
.task-row-active { background: #dbeafe; }
.task-row-active:hover { background: #bfdbfe; }

.task-assignee { color: #7c3aed; font-weight: 600; white-space: nowrap; }
.task-goal { color: #334155; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }

.task-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 9999px;
  font-size: 0.78em;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}
.task-badge-queued  { background: #f1f5f9; color: #64748b; }
.task-badge-working { background: #dbeafe; color: #1d4ed8; }
.task-badge-waiting { background: #fef3c7; color: #92400e; }
.task-badge-done    { background: #dcfce7; color: #15803d; }
.task-badge-failed  { background: #fee2e2; color: #dc2626; }
</style>
