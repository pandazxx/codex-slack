<template>
  <section class="notes-section">
    <div class="section-header">
      <h2>Notes</h2>
      <button v-if="!showForm" class="btn-add" @click="startCreate">+ Add note</button>
    </div>
    <p class="muted hint">
      Inject into prompts:
      <code>{ws:note:notes:&lt;tag&gt;}</code> (key: value pairs) or
      <code>{ws:note:keys:&lt;tag&gt;}</code> (keys only).
    </p>

    <p v-if="loading" class="muted">Loading…</p>

    <!-- Create / Edit form -->
    <div v-if="showForm" class="card">
      <h3>{{ editingKey ? 'Edit note' : 'New note' }}</h3>
      <div class="form-grid">
        <label>Key <span class="req">*</span></label>
        <input
          v-model="form.key"
          placeholder="slug-style-key"
          :disabled="!!editingKey"
          required
        />

        <label>Value <span class="req">*</span></label>
        <textarea v-model="form.value" rows="3" placeholder="Note content" required />

        <label>Tags</label>
        <input
          v-model="tagsInput"
          placeholder="memory, context, goals  (comma-separated)"
        />
      </div>
      <div class="form-actions">
        <button class="btn-primary" :disabled="saving" @click="save">
          {{ saving ? 'Saving…' : (editingKey ? 'Update' : 'Create') }}
        </button>
        <button class="btn-secondary" @click="cancelForm">Cancel</button>
      </div>
      <p v-if="formError" class="error">{{ formError }}</p>
    </div>

    <!-- Notes list -->
    <div v-if="!loading && notes.length" class="notes-table-wrap">
      <table class="notes-table">
        <thead>
          <tr>
            <th>Key</th>
            <th>Value</th>
            <th>Tags</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="n in notes" :key="n.key">
            <td class="note-key">{{ n.key }}</td>
            <td class="note-value">{{ truncate(n.value) }}</td>
            <td class="note-tags">
              <span v-for="t in n.tags" :key="t" class="tag-badge">{{ t }}</span>
              <span v-if="!n.tags.length" class="muted small">—</span>
            </td>
            <td class="actions">
              <button class="action-btn" @click="startEdit(n)">Edit</button>
              <button class="action-btn remove-btn" @click="deleteNote(n.key)">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!loading && !showForm" class="muted">No notes yet.</p>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const props = defineProps({
  workspaceId: { type: String, required: true },
  topicId:     { type: String, default: null },
})

const baseUrl = props.topicId
  ? `/api/workspaces/${props.workspaceId}/topics/${props.topicId}/notes`
  : `/api/workspaces/${props.workspaceId}/notes`

const notes     = ref([])
const loading   = ref(true)
const showForm  = ref(false)
const editingKey = ref(null)
const saving    = ref(false)
const formError = ref('')
const tagsInput = ref('')

const emptyForm = () => ({ key: '', value: '' })
const form = ref(emptyForm())

function truncate(str, len = 80) {
  return str.length > len ? str.slice(0, len) + '…' : str
}

async function load() {
  loading.value = true
  try {
    const r = await fetch(baseUrl)
    if (r.ok) notes.value = await r.json()
  } finally {
    loading.value = false
  }
}

function startCreate() {
  form.value = emptyForm()
  tagsInput.value = ''
  editingKey.value = null
  formError.value = ''
  showForm.value = true
}

function startEdit(note) {
  form.value = { key: note.key, value: note.value }
  tagsInput.value = note.tags.join(', ')
  editingKey.value = note.key
  formError.value = ''
  showForm.value = true
}

function cancelForm() {
  showForm.value = false
  editingKey.value = null
  formError.value = ''
}

async function save() {
  formError.value = ''
  saving.value = true
  try {
    const tags = tagsInput.value
      .split(',')
      .map(t => t.trim())
      .filter(Boolean)

    const isEdit = !!editingKey.value
    const url = isEdit ? `${baseUrl}/${editingKey.value}` : baseUrl
    const method = isEdit ? 'PATCH' : 'POST'
    const body = isEdit
      ? { value: form.value.value, tags }
      : { key: form.value.key, value: form.value.value, tags }

    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      const detail = err.detail
      formError.value = Array.isArray(detail)
        ? detail.map(e => e.msg || JSON.stringify(e)).join('; ')
        : (detail || `Error ${r.status}`)
      return
    }
    showForm.value = false
    editingKey.value = null
    await load()
  } finally {
    saving.value = false
  }
}

async function deleteNote(key) {
  if (!confirm(`Delete note "${key}"?`)) return
  await fetch(`${baseUrl}/${key}`, { method: 'DELETE' })
  await load()
}

onMounted(load)
</script>

<style scoped>
.notes-section { margin-top: 2rem; }

.section-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.5rem;
}
.section-header h2 { margin: 0; }

.hint { margin-top: 0; margin-bottom: 1rem; font-size: 0.85em; }

.card {
  border: 1px solid var(--border, #ddd);
  border-radius: 6px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
  background: var(--card-bg, #fff);
}
.card h3 { margin: 0 0 0.75rem; font-size: 1rem; }

.form-grid {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 0.5rem 0.75rem;
  align-items: start;
  margin-bottom: 0.75rem;
}
.form-grid label { padding-top: 0.3rem; font-size: 0.9em; }
.form-grid input,
.form-grid textarea {
  width: 100%;
  box-sizing: border-box;
  font-size: 0.9em;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border, #ccc);
  border-radius: 4px;
  font-family: inherit;
}
.form-grid textarea { resize: vertical; }
.form-grid input:disabled { opacity: 0.6; background: var(--disabled-bg, #f5f5f5); }

.req { color: var(--danger, #c00); }

.form-actions { display: flex; gap: 0.5rem; }

.notes-table-wrap { overflow-x: auto; }
.notes-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9em;
}
.notes-table th,
.notes-table td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border, #eee);
}
.notes-table th { font-weight: 600; color: var(--muted, #666); }
.note-key  { font-family: monospace; white-space: nowrap; }
.note-value { color: var(--text, #333); max-width: 360px; word-break: break-word; }
.note-tags  { white-space: nowrap; }

.tag-badge {
  display: inline-block;
  padding: 0.1rem 0.45rem;
  border-radius: 9999px;
  background: var(--tag-bg, #e8f0fe);
  color: var(--tag-text, #1a56db);
  font-size: 0.78em;
  margin-right: 0.25rem;
}

.actions { white-space: nowrap; }
.action-btn {
  font-size: 0.8em;
  padding: 0.2rem 0.5rem;
  margin-right: 0.25rem;
  cursor: pointer;
  border: 1px solid var(--border, #ccc);
  border-radius: 4px;
  background: transparent;
}
.remove-btn { color: var(--danger, #c00); border-color: var(--danger, #c00); }

.error { color: var(--danger, #c00); font-size: 0.85em; margin-top: 0.5rem; }
.muted { color: var(--muted, #777); }
.small { font-size: 0.85em; }
</style>
