<template>
  <section class="env-section">
    <div class="header-row">
      <h2>Env Vars</h2>
    </div>
    <p class="muted hint">Environment variables injected into this workspace's agent container. Workspace rows override global config. Changes take effect after an agent restart.</p>
    <p class="warn hint">Values are stored as plain text. Treat this database file as a sensitive asset.</p>

    <div v-if="dirtySinceLastStart" class="restart-banner">
      <span v-if="containerRunning">
        Configuration changed — restart required to apply.
      </span>
      <span v-else>
        Saved. Will apply when the agent next starts.
      </span>
    </div>

    <div class="config-add-form">
      <input v-model="newKey" placeholder="KEY" class="config-key-input" :disabled="saving" @keydown.enter="addVar" />
      <input v-model="newValue" placeholder="value" class="config-val-input" :disabled="saving" @keydown.enter="addVar" />
      <button class="btn-add" @click="addVar" :disabled="!newKey.trim() || saving">
        {{ saving ? 'Saving…' : 'Add / Update' }}
      </button>
    </div>
    <span v-if="saveError" class="error">{{ saveError }}</span>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="!rows.length" class="muted">No env vars set for this workspace.</p>
    <table v-else class="config-table">
      <thead>
        <tr><th>Key</th><th>Value</th><th>Source</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key" :class="{ 'row-inherited': row.source === 'global' }">
          <td><code>{{ row.key }}</code></td>
          <td class="config-val">
            <span v-if="isSecret(row.key) && !revealed.has(row.key)">
              ••••••••
              <button class="reveal-btn" @click="reveal(row.key)" title="Show value">reveal</button>
            </span>
            <span v-else>
              {{ row.value }}
              <button v-if="isSecret(row.key)" class="reveal-btn" @click="hide(row.key)" title="Hide value">hide</button>
            </span>
          </td>
          <td>
            <span v-if="row.source === 'global'" class="badge-global">global</span>
            <span v-else-if="row.source === 'shadows'" class="badge-shadows">workspace (shadows global)</span>
            <span v-else class="badge-workspace">workspace</span>
          </td>
          <td class="actions">
            <button
              v-if="row.source !== 'global'"
              class="action-btn remove-btn"
              @click="deleteVar(row.key)"
              :disabled="saving"
              title="Delete"
            >✕</button>
            <span v-else class="muted small">manage in <RouterLink to="/settings">Settings</RouterLink></span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { RouterLink } from 'vue-router'

const props = defineProps({
  workspaceId: { type: [String, Number], required: true },
  containerRunning: { type: Boolean, default: false },
})

const SECRET_KEY_SUBSTRINGS = ['KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'PASSPHRASE']

const mergedConfig = ref({})
const globalConfig = ref({})
const loading = ref(false)
const saving = ref(false)
const saveError = ref('')
const newKey = ref('')
const newValue = ref('')
const revealed = ref(new Set())

const dirtyStorageKey = computed(() => `workspace_${props.workspaceId}_dirty`)

const dirtySinceLastStart = ref(
  localStorage.getItem(`workspace_${props.workspaceId}_dirty`) === 'true'
)

watch(() => props.workspaceId, () => {
  dirtySinceLastStart.value = localStorage.getItem(dirtyStorageKey.value) === 'true'
})

function isSecret(key) {
  const upper = key.toUpperCase()
  return SECRET_KEY_SUBSTRINGS.some(s => upper.includes(s))
}

function reveal(key) {
  revealed.value = new Set([...revealed.value, key])
}

function hide(key) {
  const next = new Set(revealed.value)
  next.delete(key)
  revealed.value = next
}

function markDirty() {
  localStorage.setItem(dirtyStorageKey.value, 'true')
  dirtySinceLastStart.value = true
}

function clearDirty() {
  localStorage.removeItem(dirtyStorageKey.value)
  dirtySinceLastStart.value = false
}

const rows = computed(() => {
  const merged = mergedConfig.value
  const global = globalConfig.value
  const result = []

  for (const [key, value] of Object.entries(merged)) {
    const inGlobal = Object.prototype.hasOwnProperty.call(global, key)
    const sameValueAsGlobal = inGlobal && global[key] === value
    let source
    if (!inGlobal) {
      source = 'workspace'
    } else if (sameValueAsGlobal) {
      source = 'global'
    } else {
      source = 'shadows'
    }
    result.push({ key, value, source })
  }

  result.sort((a, b) => {
    const order = { workspace: 0, shadows: 1, global: 2 }
    if (order[a.source] !== order[b.source]) return order[a.source] - order[b.source]
    return a.key.localeCompare(b.key)
  })

  return result
})

async function loadConfig() {
  loading.value = true
  try {
    const [mergedRes, globalRes] = await Promise.all([
      fetch(`/api/workspaces/${props.workspaceId}/config`),
      fetch('/api/config'),
    ])
    mergedConfig.value = mergedRes.ok ? await mergedRes.json() : {}
    globalConfig.value = globalRes.ok ? await globalRes.json() : {}
  } finally {
    loading.value = false
  }
}

async function patchConfig(patch) {
  const r = await fetch(`/api/workspaces/${props.workspaceId}/config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${r.status}`)
  }
  return r.json()
}

async function addVar() {
  const key = newKey.value.trim()
  if (!key) return
  saving.value = true
  saveError.value = ''
  try {
    await patchConfig({ set: { [key]: newValue.value }, delete: [] })
    newKey.value = ''
    newValue.value = ''
    await loadConfig()
    markDirty()
  } catch (err) {
    saveError.value = err.message
  } finally {
    saving.value = false
  }
}

async function deleteVar(key) {
  if (!confirm(`Delete env var "${key}" from this workspace?`)) return
  saving.value = true
  saveError.value = ''
  try {
    await patchConfig({ set: {}, delete: [key] })
    await loadConfig()
    markDirty()
  } catch (err) {
    saveError.value = err.message
  } finally {
    saving.value = false
  }
}

function onRestartSuccess() {
  clearDirty()
}

defineExpose({ onRestartSuccess, loadConfig })

onMounted(loadConfig)
</script>

<style scoped>
.env-section { border-top: 1px solid #e2e8f0; padding-top: 1.5rem; margin-bottom: 2rem; }
.header-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
h2 { margin: 0; }
.hint { font-size: 0.85em; margin-bottom: 0.75rem; }
.muted { color: #64748b; }
.small { font-size: 0.82em; }
.warn { color: #92400e; background: #fef9c3; padding: 0.4rem 0.75rem; border-radius: 4px; }
.error { color: #dc2626; font-size: 0.9em; }

.restart-banner {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 6px;
  padding: 0.6rem 1rem;
  margin-bottom: 1rem;
  font-size: 0.9em;
  color: #92400e;
}

.config-add-form { display: flex; gap: 0.5rem; margin-bottom: 0.75rem; align-items: center; flex-wrap: wrap; }
.config-key-input { width: 200px; padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-family: monospace; font-size: 0.9em; }
.config-val-input { flex: 1; min-width: 120px; padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9em; }

.config-table { width: 100%; border-collapse: collapse; font-size: 0.9em; margin-top: 0.5rem; }
.config-table th { text-align: left; padding: 0.4rem 0.75rem; border-bottom: 2px solid #e2e8f0; color: #64748b; font-weight: 600; }
.config-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #f1f5f9; }
.row-inherited td { color: #94a3b8; }
.config-val { font-family: monospace; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.badge-global { background: #ede9fe; color: #6d28d9; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.badge-workspace { background: #dcfce7; color: #15803d; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.badge-shadows { background: #fef3c7; color: #b45309; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }

.actions { white-space: nowrap; }
.action-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.85em; padding: 0.15rem 0.4rem; border-radius: 3px; text-decoration: underline; }
.action-btn:hover { background: #eff6ff; }
.remove-btn { color: #94a3b8; text-decoration: none; }
.remove-btn:hover:not(:disabled) { background: #fee2e2; color: #dc2626; }
.remove-btn:disabled { opacity: 0.6; cursor: default; }

.reveal-btn { background: none; border: none; color: #64748b; cursor: pointer; font-size: 0.78em; padding: 0 0.3rem; text-decoration: underline; vertical-align: middle; }
.reveal-btn:hover { color: #334155; }

.btn-add { padding: 0.35rem 0.85rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
.btn-add:disabled { opacity: 0.6; cursor: default; }
</style>
