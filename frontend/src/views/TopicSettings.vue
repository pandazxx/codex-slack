<template>
  <div>
    <p class="breadcrumb">
      <RouterLink to="/">Workspaces</RouterLink> /
      <RouterLink :to="`/workspaces/${wsId}`">Workspace</RouterLink> /
      <RouterLink :to="`/workspaces/${wsId}/topics/${topicId}`">{{ topicSubject || topicId }}</RouterLink> /
      Settings
    </p>

    <h1 class="page-title">Topic Settings — {{ topicSubject }}</h1>

    <!-- ── Event Actions card ────────────────────────────────────────────── -->
    <section>
      <div class="section-header">
        <h2>Event Actions</h2>
        <button v-if="!showForm && !loading" class="btn-add" @click="startCreate">+ Add action</button>
      </div>
      <p class="muted hint">Trigger configured staff when something happens in this topic.</p>

      <p v-if="loading" class="muted">Loading…</p>

      <!-- Create / Edit form -->
      <div v-if="showForm" class="card">
        <h3>{{ editingId ? 'Edit action' : 'New action' }}</h3>
        <div class="form-grid">
          <label>Event type <span class="req">*</span></label>
          <select v-model="form.event_type" :disabled="!!editingId">
            <option value="topic_message_sent">topic_message_sent — user sends a message</option>
            <option value="topic_message_received">topic_message_received — agent replies</option>
            <option value="topic_scheduler">topic_scheduler — cron schedule</option>
            <option value="topic_archived">topic_archived — topic is archived</option>
            <option value="topic_archiving">topic_archiving — before topic is archived (veto)</option>
          </select>

          <label>Staff <span class="req">*</span></label>
          <select v-model="form.staff_name">
            <option value="" disabled>— select staff —</option>
            <template v-for="group in staffGroups" :key="group.label">
              <optgroup :label="group.label">
                <option v-for="s in group.items" :key="s.name" :value="s.name">{{ s.name }}</option>
              </optgroup>
            </template>
          </select>

          <label>Prompt template <span class="req">*</span></label>
          <div>
            <textarea v-model="form.prompt_template" rows="4" placeholder="e.g. Summarise {topic_name}" />
            <p class="form-hint">
              Available variables for <strong>{{ form.event_type }}</strong>:
              {{ variableHint }}.<br>
              Escape literal braces as <code>&#123;&#123;</code> / <code>&#125;&#125;</code>.
            </p>
          </div>

          <template v-if="form.event_type === 'topic_message_sent'">
            <label>Timing <span class="req">*</span></label>
            <select v-model="form.timing">
              <option value="before">before — fires before the user message is dispatched</option>
              <option value="after">after — fires after the user message is dispatched</option>
            </select>
          </template>

          <template v-if="form.event_type === 'topic_scheduler'">
            <label>Cron expression <span class="req">*</span></label>
            <div>
              <input v-model="form.cron_expr" placeholder="e.g. 0 9 * * *" class="cron-input" />
              <span class="tz-hint muted small"> interpreted in {{ configuredTimezone }}</span>
            </div>
          </template>

          <label>Enabled</label>
          <label class="checkbox-label">
            <input type="checkbox" v-model="form.enabled" />
            Fire this action when the event occurs
          </label>

          <label>Structured output</label>
          <div>
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.structured_output" />
              Expect JSON response from staff
            </label>
            <p v-if="form.structured_output" class="form-hint">
              Staff must reply with one of:<br>
              <code>{"message": "text to post in topic"}</code><br>
              <code>{"break": true, "message": "reason"}</code><br>
              <code>{"silent": true, "log": "optional log"}</code>
            </p>
          </div>
        </div>
        <div class="form-actions">
          <button class="btn-primary" @click="saveAction" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
          <button class="btn-secondary" @click="cancelForm">Cancel</button>
          <span v-if="formError" class="error">{{ formError }}</span>
        </div>
      </div>

      <!-- Action list -->
      <p v-if="!loading && !actions.length && !showForm" class="muted">No event actions configured.</p>
      <div v-else-if="!loading && actions.length" class="action-list">
        <div v-for="a in actions" :key="a.id" class="action-card">
          <div class="action-header">
            <span class="action-toggle">
              <input type="checkbox" :checked="a.enabled" @change="toggleEnabled(a)" :title="a.enabled ? 'Disable' : 'Enable'" />
            </span>
            <span class="action-type-badge" :class="typeBadgeClass(a.event_type)">{{ a.event_type }}</span>
            <span v-if="a.timing" class="timing-badge">{{ a.timing }}</span>
            <span v-if="a.structured_output" class="structured-badge" title="Expects JSON response">JSON</span>
            <span class="action-staff">@{{ a.staff_name }}</span>
            <span class="action-preview muted">{{ a.prompt_template.slice(0, 60) }}{{ a.prompt_template.length > 60 ? '…' : '' }}</span>
            <div class="action-btns">
              <button class="action-btn" @click="startEdit(a)">Edit</button>
              <button class="action-btn remove-btn" @click="deleteAction(a.id)">✕</button>
            </div>
          </div>

          <div v-if="a.cron_expr" class="action-cron muted small">
            <code>{{ a.cron_expr }}</code> ({{ configuredTimezone }})
          </div>

          <div class="action-run-info">
            <span class="muted small">Last run: {{ relativeTime(a.last_run_at) }}</span>
            <span v-if="a.last_run_status" :class="['run-status-badge', runStatusClass(a.last_run_status)]">{{ a.last_run_status }}</span>
            <span v-if="a.last_run_output" class="run-output-toggle">
              <button class="link-btn" @click="toggleOutput(a.id)">{{ expandedOutputs[a.id] ? 'hide' : 'details' }}</button>
            </span>
          </div>
          <div v-if="a.last_run_output && expandedOutputs[a.id]" class="run-output">
            {{ a.last_run_output }}
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const wsId = route.params.wsId
const topicId = route.params.topicId

const topicSubject = ref('')
const actions = ref([])
const loading = ref(true)
const showForm = ref(false)
const editingId = ref(null)
const saving = ref(false)
const formError = ref('')
const configuredTimezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone)
const expandedOutputs = ref({})
const staffGroups = ref([])

const emptyForm = () => ({
  event_type: 'topic_message_sent',
  staff_name: '',
  prompt_template: '',
  timing: 'after',
  cron_expr: '',
  enabled: true,
  structured_output: false,
})
const form = ref(emptyForm())

const variableHint = computed(() => {
  const m = {
    topic_message_sent: '{msgbody}, {message_json}, {topic_name}, {topic_json}',
    topic_message_received: '{msgbody}, {response_json}, {topic_name}, {topic_json}',
    topic_scheduler: '{topic_name}, {workspace_name}, {topic_json}',
    topic_archived: '{topic_name}, {topic_json}',
    topic_archiving: '{topic_name}, {topic_json}',
  }
  return m[form.value.event_type] || ''
})

async function load() {
  loading.value = true
  try {
    const [topicRes, actionsRes, tzRes, globalStaffRes, wsStaffRes, topicStaffRes] = await Promise.all([
      fetch(`/api/workspaces/${wsId}/topics/${topicId}`),
      fetch(`/api/workspaces/${wsId}/topics/${topicId}/event-actions`),
      fetch('/api/config/system-settings'),
      fetch('/api/staffs'),
      fetch(`/api/workspaces/${wsId}/staffs`),
      fetch(`/api/workspaces/${wsId}/topics/${topicId}/staffs`),
    ])
    if (topicRes.ok) {
      const t = await topicRes.json()
      topicSubject.value = t.subject || topicId
    }
    if (actionsRes.ok) actions.value = await actionsRes.json()
    if (tzRes.ok) {
      const tz = await tzRes.json()
      configuredTimezone.value = tz.timezone_configured
        ? tz.timezone
        : Intl.DateTimeFormat().resolvedOptions().timeZone
    }
    const groups = []
    if (topicStaffRes.ok) {
      const items = (await topicStaffRes.json()).filter(s => !s.inherited_from)
      if (items.length) groups.push({ label: 'Topic', items })
    }
    if (wsStaffRes.ok) {
      const items = (await wsStaffRes.json()).filter(s => !s.inherited_from)
      if (items.length) groups.push({ label: 'Workspace', items })
    }
    if (globalStaffRes.ok) {
      const items = (await globalStaffRes.json()).filter(s => !s.inherited_from)
      if (items.length) groups.push({ label: 'Global', items })
    }
    staffGroups.value = groups
  } finally {
    loading.value = false
  }
}

function startCreate() {
  editingId.value = null
  form.value = emptyForm()
  showForm.value = true
  formError.value = ''
}

function startEdit(a) {
  editingId.value = a.id
  form.value = {
    event_type: a.event_type,
    staff_name: a.staff_name,
    prompt_template: a.prompt_template,
    timing: a.timing ?? null,
    cron_expr: a.cron_expr ?? '',
    enabled: a.enabled,
    structured_output: a.structured_output,
  }
  showForm.value = true
  formError.value = ''
}

function cancelForm() {
  showForm.value = false
  editingId.value = null
  formError.value = ''
}

async function saveAction() {
  saving.value = true
  formError.value = ''
  try {
    const isEdit = !!editingId.value
    // event_type is immutable; only include it on POST.
    // EventActionPatch uses extra='forbid', so sending event_type on PATCH would 422.
    const body = {
      staff_name: form.value.staff_name.trim(),
      prompt_template: form.value.prompt_template,
      enabled: form.value.enabled,
      structured_output: form.value.structured_output,
    }
    if (!isEdit) {
      body.event_type = form.value.event_type
    }
    if (form.value.event_type === 'topic_message_sent') {
      body.timing = form.value.timing
    }
    if (form.value.event_type === 'topic_scheduler') {
      body.cron_expr = form.value.cron_expr.trim()
    }
    if (form.value.event_type === 'topic_message_received') {
      body.timing = 'after'
    }

    const url = isEdit
      ? `/api/workspaces/${wsId}/topics/${topicId}/event-actions/${editingId.value}`
      : `/api/workspaces/${wsId}/topics/${topicId}/event-actions`
    const method = isEdit ? 'PATCH' : 'POST'
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const err = await r.json()
      const detail = err.detail
      formError.value = Array.isArray(detail)
        ? detail.map(d => d.msg).join(', ')
        : (typeof detail === 'string' ? detail : `Error ${r.status}`)
      return
    }
    showForm.value = false
    editingId.value = null
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(a) {
  await fetch(
    `/api/workspaces/${wsId}/topics/${topicId}/event-actions/${a.id}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !a.enabled }),
    },
  )
  await load()
}

async function deleteAction(id) {
  if (!confirm('Delete this event action?')) return
  await fetch(`/api/workspaces/${wsId}/topics/${topicId}/event-actions/${id}`, { method: 'DELETE' })
  await load()
}

function toggleOutput(id) {
  expandedOutputs.value[id] = !expandedOutputs.value[id]
}

function relativeTime(iso) {
  if (!iso) return 'never'
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return `${Math.round(diff)}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} hr ago`
  return `${Math.round(diff / 86400)} days ago`
}

function typeBadgeClass(eventType) {
  return {
    'badge-sent': eventType === 'topic_message_sent',
    'badge-received': eventType === 'topic_message_received',
    'badge-scheduler': eventType === 'topic_scheduler',
    'badge-archived': eventType === 'topic_archived',
    'badge-archiving': eventType === 'topic_archiving',
  }
}

function runStatusClass(status) {
  return status === 'ok' ? 'status-ok' : 'status-error'
}

onMounted(load)
</script>

<style scoped>
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 0.5rem; }
.page-title { margin-bottom: 1.5rem; }
.section-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.75rem; }
.section-header h2 { margin: 0; }
.hint { font-size: 0.85em; margin-bottom: 0.75rem; }
.muted { color: #64748b; }
.small { font-size: 0.82em; }
.req { color: #dc2626; }
.error { color: #dc2626; font-size: 0.9em; }

.card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 1.25rem; margin-bottom: 1.25rem; }
.card h3 { margin: 0 0 1rem; font-size: 1rem; color: #334155; }

.form-grid { display: grid; grid-template-columns: 160px 1fr; gap: 0.5rem 1rem; align-items: start; margin-bottom: 1rem; }
.form-grid label { font-size: 0.9em; color: #475569; padding-top: 0.4rem; }
.form-grid input, .form-grid select, .form-grid textarea { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9em; font-family: inherit; width: 100%; box-sizing: border-box; }
.form-grid textarea { resize: vertical; }
.checkbox-label { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9em; padding-top: 0.2rem; }
.form-actions { display: flex; gap: 0.75rem; align-items: center; }
.form-hint { font-size: 0.82em; color: #64748b; margin-top: 0.25rem; }

.cron-input { width: 220px; }
.tz-hint { display: inline-block; margin-left: 0.5rem; }

.btn-add { padding: 0.35rem 0.85rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
.btn-primary { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.btn-primary:disabled { opacity: 0.6; cursor: default; }
.btn-secondary { padding: 0.4rem 1rem; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 4px; cursor: pointer; font-size: 0.9em; }

.action-list { display: flex; flex-direction: column; gap: 0.75rem; }
.action-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 6px; padding: 0.75rem 1rem; }
.action-header { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.action-toggle input { cursor: pointer; }
.action-type-badge { border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.badge-sent { background: #dbeafe; color: #1d4ed8; }
.badge-received { background: #dcfce7; color: #15803d; }
.badge-scheduler { background: #f3e8ff; color: #7c3aed; }
.badge-archived { background: #fef9c3; color: #713f12; }
.timing-badge { background: #f1f5f9; color: #475569; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; }
.structured-badge { background: #ecfdf5; color: #065f46; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.action-staff { font-weight: 600; font-size: 0.9em; }
.action-preview { font-size: 0.82em; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.action-btns { margin-left: auto; display: flex; gap: 0.25rem; white-space: nowrap; }
.action-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.85em; padding: 0.15rem 0.4rem; border-radius: 3px; text-decoration: underline; }
.action-btn:hover { background: #eff6ff; }
.remove-btn { color: #94a3b8; text-decoration: none; }
.remove-btn:hover { background: #fee2e2; color: #dc2626; }

.action-cron { margin-top: 0.25rem; }
.action-run-info { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.4rem; font-size: 0.82em; }
.run-status-badge { border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.status-ok { background: #dcfce7; color: #15803d; }
.status-error { background: #fee2e2; color: #dc2626; }
.run-output-toggle { margin-left: 0.25rem; }
.link-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.82em; text-decoration: underline; padding: 0; }
.run-output { margin-top: 0.35rem; background: #1e293b; color: #e2e8f0; padding: 0.5rem 0.75rem; border-radius: 4px; font-size: 0.78em; font-family: monospace; white-space: pre-wrap; word-break: break-all; }
</style>
