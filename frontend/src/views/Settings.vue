<template>
  <div>
    <h1>Settings</h1>

    <!-- ── Global Staff ───────────────────────────────────────────────── -->
    <section>
      <div class="section-header">
        <h2>Global Staff</h2>
        <button v-if="!showStaffForm" class="btn-primary" @click="startCreateStaff">+ Add Staff</button>
      </div>
      <p class="muted hint">Global staff are available in every workspace. Use <code>@name</code> in a topic to address them.</p>

      <div v-if="showStaffForm" class="staff-form card">
        <h3>{{ editingStaff ? `Edit @${editingStaff}` : 'New Global Staff' }}</h3>
        <div class="form-grid">
          <label>Name <span class="req">*</span></label>
          <input v-model="staffForm.name" placeholder="e.g. reviewer" :disabled="!!editingStaff" required />

          <label>Adapter <span class="req">*</span></label>
          <select v-model="staffForm.adapter">
            <option value="claude-code">claude-code</option>
            <option value="codex">codex</option>
          </select>

          <label>Model</label>
          <input v-model="staffForm.model" placeholder="e.g. claude-opus-4-7 (leave blank for default)" />

          <label>System Prompt</label>
          <textarea v-model="staffForm.system_prompt" placeholder="--append-system-prompt value" rows="3" />

          <label>Sub-agent</label>
          <input v-model="staffForm.agent" placeholder="--agent flag value (optional)" />

          <label>Session Scope</label>
          <select v-model="staffForm.session_scope">
            <option value="topic">topic — fresh session per topic</option>
            <option value="workspace">workspace — shared across all topics</option>
            <option value="global">global — single shared session</option>
          </select>

          <label>Default</label>
          <label class="checkbox-label">
            <input type="checkbox" v-model="staffForm.is_default" />
            Route messages here when no @mention is used
          </label>
        </div>
        <div class="form-actions">
          <button class="btn-primary" @click="saveStaff" :disabled="savingStaff">{{ savingStaff ? 'Saving…' : 'Save' }}</button>
          <button class="btn-secondary" @click="cancelStaffForm">Cancel</button>
          <span v-if="staffError" class="error">{{ staffError }}</span>
        </div>
      </div>

      <p v-if="!staffs.length && !showStaffForm" class="muted">No global staff configured.</p>
      <table v-else-if="staffs.length" class="staff-table">
        <thead>
          <tr><th>@Name</th><th>Adapter</th><th>Model</th><th>Session</th><th>Default</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in staffs" :key="s.name">
            <td><code>@{{ s.name }}</code></td>
            <td>{{ s.adapter }}</td>
            <td>{{ s.model || '—' }}</td>
            <td>{{ s.session_scope }}</td>
            <td>{{ s.is_default ? '✓' : '' }}</td>
            <td class="actions">
              <button class="action-btn" @click="startEditStaff(s)">Edit</button>
              <button class="action-btn remove-btn" @click="deleteStaff(s.name)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- ── System Variables ──────────────────────────────────────────── -->
    <section>
      <div class="section-header">
        <h2>System Variables</h2>
      </div>
      <p class="muted hint">Credentials injected into every agent container. Set these to enable the corresponding features.</p>
      <p class="warn hint">⚠ Values are stored as plain text. Treat this database file as a sensitive asset.</p>

      <table class="config-table">
        <thead>
          <tr><th>Key</th><th>Value</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="sv in systemVars" :key="sv.name">
            <td><code>{{ sv.name }}</code></td>
            <td class="config-val">
              <template v-if="editingSysVar === sv.name">
                <input v-model="sysVarEditValue" class="config-inline-input" @keydown.enter="saveSysVar(sv.name)" @keydown.escape="editingSysVar = null" />
              </template>
              <template v-else-if="config[sv.name] !== undefined">
                <span v-if="sv.sensitive && !revealedKeys[sv.name]">••••••••</span>
                <span v-else>{{ config[sv.name] }}</span>
                <button v-if="sv.sensitive" class="reveal-btn" @click="toggleReveal(sv.name)">{{ revealedKeys[sv.name] ? 'hide' : 'show' }}</button>
              </template>
              <span v-else class="unset-label">— unset —</span>
            </td>
            <td class="sysvar-actions">
              <template v-if="editingSysVar === sv.name">
                <button class="action-btn" @click="saveSysVar(sv.name)">Save</button>
                <button class="action-btn" @click="editingSysVar = null">Cancel</button>
              </template>
              <template v-else>
                <button class="action-btn" @click="startEditSysVar(sv.name)">{{ config[sv.name] !== undefined ? 'Update' : 'Set' }}</button>
                <button v-if="config[sv.name] !== undefined" class="action-btn remove-btn" @click="deleteConfigKey(sv.name)">Unset</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- ── User-defined Config ────────────────────────────────────────── -->
    <section>
      <div class="section-header">
        <h2>User-defined Config</h2>
      </div>
      <p class="muted hint">Arbitrary environment variables applied to every agent container. Workspace config overrides these. Changes take effect on next container start.</p>

      <div class="config-add-form">
        <input v-model="newConfigKey" placeholder="KEY" class="config-key-input" @input="checkSysVarWarning" />
        <input v-model="newConfigValue" placeholder="value" class="config-val-input" />
        <button class="btn-primary" @click="addConfigKey" :disabled="!newConfigKey.trim()">Add / Update</button>
      </div>
      <p v-if="sysVarWarning" class="sysvar-warning">⚠ {{ sysVarWarning }}</p>

      <p v-if="!userConfigKeys.length" class="muted">No user-defined config keys set.</p>
      <table v-else class="config-table">
        <thead>
          <tr><th>Key</th><th>Value</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="key in userConfigKeys" :key="key">
            <td><code>{{ key }}</code></td>
            <td class="config-val">{{ isSensitive(key) ? '••••••••' : config[key] }}</td>
            <td><button class="action-btn remove-btn" @click="deleteConfigKey(key)">✕</button></td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const staffs = ref([])
const showStaffForm = ref(false)
const editingStaff = ref(null)
const savingStaff = ref(false)
const staffError = ref('')
const staffForm = ref({ name: '', adapter: 'claude-code', model: '', system_prompt: '', agent: '', session_scope: 'topic', is_default: false })

const config = ref({})
const systemVars = ref([])
const newConfigKey = ref('')
const newConfigValue = ref('')
const sysVarWarning = ref('')
const editingSysVar = ref(null)
const sysVarEditValue = ref('')
const revealedKeys = ref({})

const _SENSITIVE = ['API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'OAUTH']
function isSensitive(key) {
  return _SENSITIVE.some(s => key.toUpperCase().includes(s))
}

const systemVarNames = computed(() => new Set(systemVars.value.map(sv => sv.name)))
const userConfigKeys = computed(() => Object.keys(config.value).filter(k => !systemVarNames.value.has(k)))

function checkSysVarWarning() {
  const key = newConfigKey.value.trim().toUpperCase()
  sysVarWarning.value = systemVarNames.value.has(key)
    ? `"${key}" is a system variable — use the System Variables section above.`
    : ''
}

function toggleReveal(name) {
  revealedKeys.value[name] = !revealedKeys.value[name]
}

function startEditSysVar(name) {
  editingSysVar.value = name
  sysVarEditValue.value = config.value[name] ?? ''
}

async function saveSysVar(name) {
  const value = sysVarEditValue.value
  await fetch('/api/config', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ set: { [name]: value }, delete: [] }),
  })
  editingSysVar.value = null
  sysVarEditValue.value = ''
  await loadConfig()
}

async function loadStaffs() {
  const r = await fetch('/api/staffs')
  staffs.value = await r.json()
}

async function loadConfig() {
  const r = await fetch('/api/config')
  config.value = await r.json()
}

async function loadSystemVars() {
  const r = await fetch('/api/config/system-variables')
  if (r.ok) systemVars.value = await r.json()
}

function startCreateStaff() {
  editingStaff.value = null
  staffForm.value = { name: '', adapter: 'claude-code', model: '', system_prompt: '', agent: '', session_scope: 'topic', is_default: false }
  showStaffForm.value = true
  staffError.value = ''
}

function startEditStaff(s) {
  editingStaff.value = s.name
  staffForm.value = {
    name: s.name,
    adapter: s.adapter,
    model: s.model || '',
    system_prompt: s.system_prompt || '',
    agent: s.agent || '',
    session_scope: s.session_scope,
    is_default: s.is_default,
  }
  showStaffForm.value = true
  staffError.value = ''
}

function cancelStaffForm() {
  showStaffForm.value = false
  editingStaff.value = null
  staffError.value = ''
}

async function saveStaff() {
  savingStaff.value = true
  staffError.value = ''
  try {
    const body = {
      name: staffForm.value.name.trim().toLowerCase(),
      adapter: staffForm.value.adapter,
      model: staffForm.value.model.trim() || null,
      system_prompt: staffForm.value.system_prompt.trim() || null,
      agent: staffForm.value.agent.trim() || null,
      session_scope: staffForm.value.session_scope,
      is_default: staffForm.value.is_default,
    }
    const url = editingStaff.value ? `/api/staffs/${editingStaff.value}` : '/api/staffs'
    const method = editingStaff.value ? 'PUT' : 'POST'
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!r.ok) {
      const err = await r.json()
      staffError.value = err.detail || 'Error saving staff'
      return
    }
    showStaffForm.value = false
    editingStaff.value = null
    await loadStaffs()
  } finally {
    savingStaff.value = false
  }
}

async function deleteStaff(name) {
  if (!confirm(`Delete global staff @${name}?`)) return
  await fetch(`/api/staffs/${name}`, { method: 'DELETE' })
  await loadStaffs()
}

async function addConfigKey() {
  const key = newConfigKey.value.trim()
  const value = newConfigValue.value
  if (!key) return
  await fetch('/api/config', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ set: { [key]: value }, delete: [] }),
  })
  newConfigKey.value = ''
  newConfigValue.value = ''
  await loadConfig()
}

async function deleteConfigKey(key) {
  if (!confirm(`Delete config key "${key}"?`)) return
  await fetch('/api/config', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ set: {}, delete: [key] }),
  })
  await loadConfig()
}

onMounted(async () => {
  await Promise.all([loadStaffs(), loadConfig(), loadSystemVars()])
})
</script>

<style scoped>
h1 { margin-bottom: 2rem; }
section { margin-bottom: 3rem; border-top: 1px solid #e2e8f0; padding-top: 1.5rem; }
.section-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.75rem; }
.section-header h2 { margin: 0; }
.hint { font-size: 0.85em; margin-bottom: 0.75rem; }
.warn { color: #92400e; background: #fef9c3; padding: 0.4rem 0.75rem; border-radius: 4px; }
.muted { color: #64748b; }
.req { color: #dc2626; }
.error { color: #dc2626; font-size: 0.9em; }

.card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
.card h3 { margin: 0 0 1rem; font-size: 1rem; color: #334155; }

.form-grid { display: grid; grid-template-columns: 160px 1fr; gap: 0.5rem 1rem; align-items: start; margin-bottom: 1rem; }
.form-grid label { font-size: 0.9em; color: #475569; padding-top: 0.4rem; }
.form-grid input, .form-grid select, .form-grid textarea { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9em; font-family: inherit; width: 100%; box-sizing: border-box; }
.form-grid textarea { resize: vertical; }
.checkbox-label { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9em; padding-top: 0.2rem; }
.form-actions { display: flex; gap: 0.75rem; align-items: center; }

.staff-table, .config-table { width: 100%; border-collapse: collapse; font-size: 0.9em; margin-top: 0.5rem; }
.staff-table th, .config-table th { text-align: left; padding: 0.4rem 0.75rem; border-bottom: 2px solid #e2e8f0; color: #64748b; font-weight: 600; }
.staff-table td, .config-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #f1f5f9; }
.config-val { font-family: monospace; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.actions { white-space: nowrap; }

.config-add-form { display: flex; gap: 0.5rem; margin-bottom: 1rem; align-items: center; }
.config-key-input { width: 200px; padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-family: monospace; font-size: 0.9em; }
.config-val-input { flex: 1; padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9em; }
.config-inline-input { width: 100%; padding: 0.25rem 0.5rem; border: 1px solid #93c5fd; border-radius: 4px; font-size: 0.9em; font-family: monospace; }
.unset-label { color: #94a3b8; font-style: italic; font-size: 0.9em; }
.reveal-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.8em; margin-left: 0.5rem; text-decoration: underline; padding: 0; }
.sysvar-actions { white-space: nowrap; }
.sysvar-warning { color: #92400e; background: #fef9c3; padding: 0.3rem 0.75rem; border-radius: 4px; font-size: 0.85em; margin-bottom: 0.5rem; }

.btn-primary { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.btn-primary:disabled { opacity: 0.6; cursor: default; }
.btn-secondary { padding: 0.4rem 1rem; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.action-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.85em; padding: 0.15rem 0.4rem; border-radius: 3px; text-decoration: underline; }
.action-btn:hover { background: #eff6ff; }
.remove-btn { color: #94a3b8; text-decoration: none; }
.remove-btn:hover { background: #fee2e2; color: #dc2626; }
</style>
