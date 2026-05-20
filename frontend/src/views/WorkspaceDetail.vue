<template>
  <div>
    <p class="breadcrumb"><RouterLink to="/">Workspaces</RouterLink> / {{ workspace?.name || id }}</p>

    <div v-if="isArchived" class="archived-banner">This workspace is archived — read only</div>

    <div v-if="!isArchived" class="agent-status-bar">
      <span class="agent-status-label">Agent container:</span>
      <span v-if="agentStatus" :class="['status-badge', statusClass]">{{ statusLabel }}</span>
      <span v-else class="status-badge status-unknown">checking…</span>
      <span v-if="agentStatus?.version" class="agent-version">{{ agentStatus.version }}</span>
      <span v-if="agentStatus?.error" class="status-error">{{ agentStatus.error }}</span>
      <button class="btn-refresh-auth" @click="refreshAuth" :disabled="refreshing || restarting">
        {{ refreshing ? 'Refreshing…' : 'Refresh Auth' }}
      </button>
      <button class="btn-restart-agent" @click="restartAgent" :disabled="restarting || refreshing">
        {{ restarting ? 'Restarting…' : 'Restart Agent' }}
      </button>
      <span v-if="workspace?.last_refreshed_at" class="muted small">refreshed {{ formatInTimezone(workspace.last_refreshed_at, configuredTimezone) }}</span>
    </div>

    <section>
      <div class="header-row">
        <h2>Topics</h2>
        <RouterLink v-if="!isArchived" :to="`/workspaces/${id}/archived-topics`" class="archived-link">View Archived</RouterLink>
      </div>
      <form v-if="!isArchived" @submit.prevent="createTopic" class="create-form">
        <input v-model="subject" placeholder="Topic subject" required />

        <div class="branch-picker" ref="branchPickerEl">
          <div class="branch-input-wrap">
            <input
              v-model="branchFilter"
              @input="onBranchInput"
              @focus="onBranchFocus"
              @blur="scheduleBranchClose"
              placeholder="Branch (required)"
              autocomplete="off"
              class="branch-input"
              :title="selectedBranch ? `Base branch: ${selectedBranch}` : 'Select a base branch'"
            />
            <button v-if="selectedBranch" type="button" class="branch-clear" @mousedown.prevent="clearBranch" title="Clear branch">×</button>
            <span class="branch-caret" @mousedown.prevent="toggleBranchDropdown">▾</span>
          </div>
          <ul v-if="branchDropdownOpen" class="branch-dropdown">
            <li v-if="branchesLoading" class="branch-hint">Loading…</li>
            <li v-else-if="!filteredBranches.length" class="branch-hint">No branches found</li>
            <template v-else>
              <li
                v-for="b in filteredBranches"
                :key="b"
                @mousedown.prevent="selectBranch(b)"
                :class="{ 'branch-selected': b === selectedBranch }"
              >{{ b }}</li>
            </template>
          </ul>
        </div>

        <button type="submit" :disabled="creating">
          {{ creating ? 'Creating…' : 'New Topic' }}
        </button>
        <span v-if="createError" class="error">{{ createError }}</span>
      </form>

      <p v-if="loading" class="muted">Loading…</p>
      <p v-else-if="!topics.length" class="muted">No topics yet.</p>
      <ul v-else class="list">
        <li v-for="t in topics" :key="t.id">
          <span class="topic-row">
            <RouterLink :to="`/workspaces/${id}/topics/${t.id}`">{{ t.subject }}</RouterLink>
            <span class="muted small"> — branch: {{ t.branch_name }}</span>
            <span v-if="t.repo_ref" class="repo-ref-badge">
              {{ t.repo_ref }}<template v-if="t.base_sha"> · {{ t.base_sha.substring(0, 7) }}</template>
            </span>
          </span>
          <RouterLink v-if="!isArchived && !t.archived_at" :to="`/workspaces/${id}/topics/${t.id}/settings`" class="topic-settings-btn" title="Topic settings">&#9881;</RouterLink>
          <button v-if="!isArchived" class="remove-btn"
            :class="{ 'veto-rejected-btn': !!vetoResults[t.id] }"
            @click="handleArchiveClick(t)"
            :disabled="!!archivingTopics[t.id]"
            :title="vetoResults[t.id] ? 'Click to see veto details' : 'Archive topic'">
            {{ archivingTopics[t.id] ? 'Archiving…' : vetoResults[t.id] ? 'Archive rejected' : 'Archive' }}
          </button>
        </li>
      </ul>
    </section>

    <!-- ── Env Vars ──────────────────────────────────────────────────── -->
    <WorkspaceEnvVarsPanel
      v-if="!isArchived"
      ref="envPanel"
      :workspace-id="id"
      :container-running="agentStatus?.status === 'running'"
    />

    <!-- ── Staff section ─────────────────────────────────────────────── -->
    <section class="staffs-section">
      <div class="header-row">
        <h2>Staff</h2>
        <button v-if="!isArchived && !showStaffForm" class="btn-add" @click="startCreateStaff">+ Add Staff</button>
      </div>
      <p class="muted hint">Use <code>@name</code> in a message to address a specific staff. Staff shown with a <span class="badge-global">global</span> badge are inherited from global settings.</p>

      <div v-if="showStaffForm && !isArchived" class="staff-form card">
        <h3>{{ editingStaff ? `Edit @${editingStaff}` : 'New Workspace Staff' }}</h3>
        <div class="form-grid">
          <label>Name <span class="req">*</span></label>
          <input v-model="staffForm.name" placeholder="e.g. engineer" :disabled="!!editingStaff" required />

          <label>Adapter <span class="req">*</span></label>
          <select v-model="staffForm.adapter">
            <option value="claude-code">claude-code</option>
            <option value="codex">codex</option>
          </select>

          <label>Model</label>
          <input v-model="staffForm.model" placeholder="e.g. claude-opus-4-7 (blank = adapter default)" />

          <label>System Prompt</label>
          <textarea v-model="staffForm.system_prompt" placeholder="--append-system-prompt value (optional)" rows="3" />

          <label>Sub-agent</label>
          <input v-model="staffForm.agent" placeholder="--agent flag value (optional)" />

          <label>Session Scope</label>
          <select v-model="staffForm.session_scope">
            <option value="topic">topic — fresh session per topic</option>
            <option value="workspace">workspace — shared across all topics</option>
            <option value="global">global — single shared session</option>
            <option value="none">none — stateless, no session tracking</option>
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

      <p v-if="!staffs.length" class="muted">No staff configured.</p>
      <div v-else class="table-wrap">
        <table class="staff-table">
          <thead>
            <tr><th>@Name</th><th>Adapter</th><th>Model</th><th>Session</th><th>Default</th><th>Source</th><th v-if="!isArchived"></th></tr>
          </thead>
          <tbody>
            <tr v-for="s in staffs" :key="s.name" :class="{ inherited: s.inherited_from }">
              <td><code>@{{ s.name }}</code></td>
              <td>{{ s.adapter }}</td>
              <td>{{ s.model || '—' }}</td>
              <td>{{ s.session_scope }}</td>
              <td>
                <span v-if="s.is_default" class="badge-default">✓ default</span>
                <button v-else-if="!isArchived && !s.inherited_from" class="action-btn set-default-btn" @click="setDefaultStaff(s.name)">Set default</button>
              </td>
              <td>
                <span v-if="s.inherited_from" class="badge-global">{{ s.inherited_from }}</span>
                <span v-else class="badge-local">workspace</span>
              </td>
              <td v-if="!isArchived" class="actions">
                <template v-if="!s.inherited_from">
                  <button class="action-btn" @click="startEditStaff(s)">Edit</button>
                  <button class="action-btn remove-btn" @click="deleteStaff(s.name)">✕</button>
                </template>
                <span v-else class="muted small">manage in <RouterLink to="/settings">Settings</RouterLink></span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ── Workspace Event Actions ──────────────────────────────────── -->
    <section v-if="!isArchived" class="event-actions-section">
      <div class="header-row">
        <h2>Workspace Event Actions</h2>
        <button v-if="!showActionForm" class="btn-add" @click="startCreateAction">+ Add action</button>
      </div>
      <p class="muted hint">Trigger configured staff on events in <em>any</em> topic of this workspace. Applied in addition to per-topic event actions.</p>

      <!-- Create / Edit form -->
      <div v-if="showActionForm" class="card">
        <h3>{{ editingActionId ? 'Edit action' : 'New workspace action' }}</h3>
        <div class="form-grid">
          <label>Event type <span class="req">*</span></label>
          <select v-model="actionForm.event_type" :disabled="!!editingActionId">
            <option value="topic_message_sent">topic_message_sent — user sends a message</option>
            <option value="topic_message_received">topic_message_received — agent replies</option>
            <option value="topic_archived">topic_archived — topic is archived</option>
            <option value="topic_archiving">topic_archiving — before topic is archived (veto)</option>
          </select>

          <label>Staff <span class="req">*</span></label>
          <select v-model="actionForm.staff_name">
            <option value="" disabled>— select staff —</option>
            <template v-for="group in actionStaffGroups" :key="group.label">
              <optgroup :label="group.label">
                <option v-for="s in group.items" :key="s.name" :value="s.name">{{ s.name }}</option>
              </optgroup>
            </template>
          </select>

          <label>Prompt template <span class="req">*</span></label>
          <div>
            <textarea v-model="actionForm.prompt_template" rows="4" placeholder="e.g. Summarise {topic_name}" />
            <p class="form-hint">
              Available variables for <strong>{{ actionForm.event_type }}</strong>:
              {{ actionVariableHint }}.<br>
              Escape literal braces as <code>&#123;&#123;</code> / <code>&#125;&#125;</code>.
            </p>
          </div>

          <template v-if="actionForm.event_type === 'topic_message_sent'">
            <label>Timing <span class="req">*</span></label>
            <select v-model="actionForm.timing">
              <option value="before">before — fires before the user message is dispatched</option>
              <option value="after">after — fires after the user message is dispatched</option>
            </select>
          </template>

          <label>Enabled</label>
          <label class="checkbox-label">
            <input type="checkbox" v-model="actionForm.enabled" />
            Fire this action when the event occurs
          </label>

          <label>Silent</label>
          <div>
            <label class="checkbox-label">
              <input type="checkbox" v-model="actionForm.silent" />
              Hide messages from this action in the topic chat by default
            </label>
            <p class="form-hint">When checked, messages are stored but hidden unless "Show silent messages" is enabled in the chat.</p>
          </div>

          <label>Structured output</label>
          <div>
            <label class="checkbox-label">
              <input type="checkbox" v-model="actionForm.structured_output" />
              Expect JSON response from staff
            </label>
            <p v-if="actionForm.structured_output" class="form-hint">
              Staff must reply with one of:<br>
              <code>{"message": "text to post in topic"}</code><br>
              <code>{"break": true, "message": "reason"}</code><br>
              <code>{"silent": true, "log": "optional log"}</code>
            </p>
          </div>
        </div>
        <div class="form-actions">
          <button class="btn-primary" @click="saveAction" :disabled="savingAction">{{ savingAction ? 'Saving…' : 'Save' }}</button>
          <button class="btn-secondary" @click="cancelActionForm">Cancel</button>
          <span v-if="actionError" class="error">{{ actionError }}</span>
        </div>
      </div>

      <!-- Action list -->
      <p v-if="!actions.length && !showActionForm" class="muted">No workspace event actions configured.</p>
      <div v-else-if="actions.length" class="action-list">
        <div v-for="a in actions" :key="a.id" class="action-card">
          <div class="action-header">
            <span class="action-toggle">
              <input type="checkbox" :checked="a.enabled" @change="toggleAction(a)" :title="a.enabled ? 'Disable' : 'Enable'" />
            </span>
            <span class="action-type-badge" :class="actionTypeBadgeClass(a.event_type)">{{ a.event_type }}</span>
            <span v-if="a.timing" class="timing-badge">{{ a.timing }}</span>
            <span v-if="a.structured_output" class="structured-badge" title="Expects JSON response">JSON</span>
            <span class="action-staff">@{{ a.staff_name }}</span>
            <span class="action-preview muted">{{ a.prompt_template.slice(0, 60) }}{{ a.prompt_template.length > 60 ? '…' : '' }}</span>
            <div class="action-btns">
              <button class="action-btn" @click="startEditAction(a)">Edit</button>
              <button class="action-btn remove-btn" @click="deleteAction(a.id)">✕</button>
            </div>
          </div>
          <div class="action-run-info">
            <span class="muted small">Last run: {{ actionRelativeTime(a.last_run_at) }}</span>
            <span v-if="a.last_run_status" :class="['run-status-badge', actionRunStatusClass(a.last_run_status)]">{{ a.last_run_status }}</span>
            <span v-if="a.last_run_output" class="run-output-toggle">
              <button class="link-btn" @click="toggleActionOutput(a.id)">{{ expandedActionOutputs[a.id] ? 'hide' : 'details' }}</button>
            </span>
          </div>
          <div v-if="a.last_run_output && expandedActionOutputs[a.id]" class="run-output">
            {{ a.last_run_output }}
          </div>
        </div>
      </div>
    </section>

    <!-- ── Notes ────────────────────────────────────────────────────── -->
    <NotesPanel v-if="!isArchived" :workspace-id="id" />

    <!-- ── Veto dialog ──────────────────────────────────────────────── -->
    <div v-if="vetoDialog" class="veto-overlay">
      <div class="veto-dialog card">
        <h3 v-if="vetoDialog.status === 'vetoed'">Archive blocked</h3>
        <h3 v-else>Veto staff timed out</h3>
        <p class="veto-subject">Topic: <strong>{{ vetoDialog.subject }}</strong></p>
        <p v-if="vetoDialog.reason" class="veto-reason">{{ vetoDialog.reason }}</p>
        <div class="form-actions">
          <button class="btn-primary" @click="overrideTopic">Override and archive anyway</button>
          <button class="btn-secondary" @click="checkAgain">Check again</button>
          <button class="btn-secondary" @click="vetoDialog = null">Cancel</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import WorkspaceEnvVarsPanel from '../components/WorkspaceEnvVarsPanel.vue'
import NotesPanel from '../components/NotesPanel.vue'
import { formatInTimezone } from '../utils/datetime.js'

const route = useRoute()
const id = route.params.id

const workspace = ref(null)
const topics = ref([])
const staffs = ref([])
const loading = ref(true)
const configuredTimezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone)
const creating = ref(false)
const createError = ref('')
const subject = ref('')
const agentStatus = ref(null)
let statusTimer = null

const branches = ref([])
const branchesLoading = ref(false)
const branchesFetched = ref(false)
const branchFilter = ref('')
const selectedBranch = ref('')
const branchDropdownOpen = ref(false)
let branchCloseTimer = null

const filteredBranches = computed(() => {
  const q = branchFilter.value.toLowerCase()
  if (!q || q === selectedBranch.value.toLowerCase()) return branches.value
  return branches.value.filter(b => b.toLowerCase().includes(q))
})

const refreshing = ref(false)
const restarting = ref(false)
const envPanel = ref(null)

const showStaffForm = ref(false)
const editingStaff = ref(null)
const savingStaff = ref(false)
const staffError = ref('')
const staffForm = ref({ name: '', adapter: 'claude-code', model: '', system_prompt: '', agent: '', session_scope: 'topic', is_default: false })

// Workspace event actions state
const actions = ref([])
const showActionForm = ref(false)
const editingActionId = ref(null)
const savingAction = ref(false)
const actionError = ref('')
const expandedActionOutputs = ref({})
const actionStaffGroups = ref([])

const emptyActionForm = () => ({
  event_type: 'topic_message_sent',
  staff_name: '',
  prompt_template: '',
  timing: 'after',
  enabled: true,
  silent: true,
  structured_output: false,
})
const actionForm = ref(emptyActionForm())

const archivingTopics = ref({})  // topicId → true while DELETE is in flight
const vetoResults = ref({})     // topicId → { topicId, subject, status, reason } — mirrors DB
const vetoDialog = ref(null)    // null | entry from vetoResults (shown when user clicks the button)

const isArchived = computed(() => !!workspace.value?.archived_at)

const actionVariableHint = computed(() => {
  const m = {
    topic_message_sent: '{msgbody}, {message_json}, {topic_name}, {topic_json}',
    topic_message_received: '{msgbody}, {response_json}, {topic_name}, {topic_json}',
    topic_archived: '{topic_name}, {topic_json}',
    topic_archiving: '{topic_name}, {topic_json}',
  }
  return m[actionForm.value.event_type] || ''
})

const statusClass = computed(() => {
  const s = agentStatus.value?.status
  if (s === 'running') return 'status-running'
  if (s === 'restarting') return 'status-restarting'
  if (s === 'exited') return (agentStatus.value?.exit_code === 0 || agentStatus.value?.exit_code === 143) ? 'status-stopped' : 'status-crashed'
  if (s === 'not_found') return 'status-unknown'
  return 'status-unknown'
})

const statusLabel = computed(() => {
  const s = agentStatus.value
  if (!s) return ''
  if (s.status === 'running') return 'Running'
  if (s.status === 'restarting') return `Restarting (restarts: ${s.restart_count ?? '?'})`
  if (s.status === 'exited') {
    const code = s.exit_code ?? '?'
    if (code === 0 || code === 143) return 'Stopped'
    return `Crashed (exit ${code}, restarts: ${s.restart_count ?? '?'})`
  }
  if (s.status === 'not_found') return 'Not found'
  if (s.status === 'dry_run') return 'Dry run'
  return s.status
})

async function refreshAuth() {
  refreshing.value = true
  try {
    await fetch(`/api/workspaces/${id}/refresh-auth`, { method: 'POST' })
    await load()
  } finally {
    refreshing.value = false
  }
}

async function restartAgent() {
  if (!confirm('Restart the agent container? It will pick up the latest config/credentials.')) return
  restarting.value = true
  try {
    const r = await fetch(`/api/workspaces/${id}/restart-agent`, { method: 'POST' })
    if (r.ok) {
      envPanel.value?.onRestartSuccess()
    }
    await fetchAgentStatus()
  } finally {
    restarting.value = false
  }
}

async function fetchAgentStatus() {
  try {
    const r = await fetch(`/api/workspaces/${id}/agent-status`)
    if (r.ok) agentStatus.value = await r.json()
  } catch { /* ignore */ }
}

async function fetchBranches() {
  if (branchesFetched.value) return
  branchesLoading.value = true
  try {
    const r = await fetch(`/api/workspaces/${id}/branches`)
    if (r.ok) {
      branches.value = await r.json()
      branchesFetched.value = true
      if (!selectedBranch.value) {
        const fallback = branches.value.find(b => b === 'master')
          || branches.value.find(b => b === 'main')
        if (fallback) {
          selectedBranch.value = fallback
          branchFilter.value = fallback
        }
      }
    }
  } catch { /* ignore */ } finally {
    branchesLoading.value = false
  }
}

function onBranchFocus() {
  fetchBranches()
  branchDropdownOpen.value = true
}

function onBranchInput() {
  if (branchFilter.value !== selectedBranch.value) selectedBranch.value = ''
  branchDropdownOpen.value = true
}

function selectBranch(b) {
  selectedBranch.value = b
  branchFilter.value = b
  branchDropdownOpen.value = false
}

function clearBranch() {
  selectedBranch.value = ''
  branchFilter.value = ''
}

function toggleBranchDropdown() {
  if (!branchDropdownOpen.value) fetchBranches()
  branchDropdownOpen.value = !branchDropdownOpen.value
}

function scheduleBranchClose() {
  branchCloseTimer = setTimeout(() => { branchDropdownOpen.value = false }, 150)
}

async function load() {
  loading.value = true
  try {
    const [wsRes, tzRes] = await Promise.all([
      fetch(`/api/workspaces/${id}`),
      fetch('/api/config/system-settings'),
    ])
    workspace.value = await wsRes.json()
    if (tzRes.ok) {
      const tz = await tzRes.json()
      configuredTimezone.value = tz.timezone_configured
        ? tz.timezone
        : Intl.DateTimeFormat().resolvedOptions().timeZone
    }
    const topicsParam = workspace.value.archived_at ? '?archived=true' : ''
    const [topicsRes, staffsRes, actionsRes, globalStaffRes] = await Promise.all([
      fetch(`/api/workspaces/${id}/topics${topicsParam}`),
      fetch(`/api/workspaces/${id}/staffs`),
      fetch(`/api/workspaces/${id}/event-actions`),
      fetch('/api/staffs'),
    ])
    topics.value = await topicsRes.json()
    staffs.value = await staffsRes.json()
    if (actionsRes.ok) actions.value = await actionsRes.json()
    // Build staff groups for event action form
    const groups = []
    if (staffsRes.ok) {
      const items = staffs.value.filter(s => !s.inherited_from)
      if (items.length) groups.push({ label: 'Workspace', items })
    }
    if (globalStaffRes.ok) {
      const items = (await globalStaffRes.json()).filter(s => !s.inherited_from)
      if (items.length) groups.push({ label: 'Global', items })
    }
    actionStaffGroups.value = groups
    // Rebuild vetoResults from DB-backed topic fields
    const vr = {}
    for (const t of topics.value) {
      if (t.veto_status) vr[t.id] = { topicId: t.id, subject: t.subject, status: t.veto_status, reason: t.veto_reason || '' }
    }
    vetoResults.value = vr
  } finally {
    loading.value = false
  }
}

async function createTopic() {
  if (!selectedBranch.value) {
    createError.value = 'Please select a branch'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    const r = await fetch(`/api/workspaces/${id}/topics`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject: subject.value,
        repo_ref: selectedBranch.value || null,
      }),
    })
    if (!r.ok) {
      const err = await r.json()
      createError.value = err.detail || 'Error creating topic'
      return
    }
    subject.value = ''
    clearBranch()
    await load()
  } finally {
    creating.value = false
  }
}

function handleArchiveClick(t) {
  if (archivingTopics.value[t.id]) return
  if (vetoResults.value[t.id]) {
    vetoDialog.value = vetoResults.value[t.id]
  } else {
    deleteTopic(t.id, t.subject)
  }
}

function showVetoDialog(topicId) {
  vetoDialog.value = vetoResults.value[topicId] || null
}

async function _archiveTopic(topicId, subject) {
  archivingTopics.value = { ...archivingTopics.value, [topicId]: true }
  try {
    const res = await fetch(`/api/workspaces/${id}/topics/${topicId}`, { method: 'DELETE' })
    if (res.status === 204) {
      const next = { ...vetoResults.value }
      delete next[topicId]
      vetoResults.value = next
      await load()
    } else if (res.status === 423) {
      const body = await res.json().catch(() => ({}))
      vetoResults.value = { ...vetoResults.value, [topicId]: { topicId, subject, status: 'vetoed', reason: body?.detail?.reason || body?.detail || 'Archive blocked by veto staff.' } }
      vetoDialog.value = vetoResults.value[topicId]
    } else if (res.status === 504) {
      const body = await res.json().catch(() => ({}))
      vetoResults.value = { ...vetoResults.value, [topicId]: { topicId, subject, status: 'timeout', reason: body?.detail?.reason || 'Veto staff did not respond in time.' } }
      vetoDialog.value = vetoResults.value[topicId]
    }
  } finally {
    const next = { ...archivingTopics.value }
    delete next[topicId]
    archivingTopics.value = next
  }
}

async function deleteTopic(topicId, subject) {
  if (!confirm(`Archive topic "${subject}"?`)) return
  await _archiveTopic(topicId, subject)
}

async function checkAgain() {
  if (!vetoDialog.value) return
  const { topicId, subject } = vetoDialog.value
  vetoDialog.value = null
  const next = { ...vetoResults.value }
  delete next[topicId]
  vetoResults.value = next
  await _archiveTopic(topicId, subject)
}

async function overrideTopic() {
  if (!vetoDialog.value) return
  const { topicId } = vetoDialog.value
  vetoDialog.value = null
  archivingTopics.value = { ...archivingTopics.value, [topicId]: true }
  try {
    await fetch(`/api/workspaces/${id}/topics/${topicId}?override=true`, { method: 'DELETE' })
    const next = { ...vetoResults.value }
    delete next[topicId]
    vetoResults.value = next
    await load()
  } finally {
    const next = { ...archivingTopics.value }
    delete next[topicId]
    archivingTopics.value = next
  }
}

function startCreateAction() {
  editingActionId.value = null
  actionForm.value = emptyActionForm()
  showActionForm.value = true
  actionError.value = ''
}

function startEditAction(a) {
  editingActionId.value = a.id
  actionForm.value = {
    event_type: a.event_type,
    staff_name: a.staff_name,
    prompt_template: a.prompt_template,
    timing: a.timing ?? null,
    enabled: a.enabled,
    silent: a.silent,
    structured_output: a.structured_output,
  }
  showActionForm.value = true
  actionError.value = ''
}

function cancelActionForm() {
  showActionForm.value = false
  editingActionId.value = null
  actionError.value = ''
}

async function saveAction() {
  savingAction.value = true
  actionError.value = ''
  try {
    const isEdit = !!editingActionId.value
    const body = {
      staff_name: actionForm.value.staff_name.trim(),
      prompt_template: actionForm.value.prompt_template,
      enabled: actionForm.value.enabled,
      silent: actionForm.value.silent,
      structured_output: actionForm.value.structured_output,
    }
    if (!isEdit) {
      body.event_type = actionForm.value.event_type
    }
    if (actionForm.value.event_type === 'topic_message_sent') {
      body.timing = actionForm.value.timing
    }
    if (actionForm.value.event_type === 'topic_message_received') {
      body.timing = 'after'
    }

    const url = isEdit
      ? `/api/workspaces/${id}/event-actions/${editingActionId.value}`
      : `/api/workspaces/${id}/event-actions`
    const method = isEdit ? 'PATCH' : 'POST'
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) {
      const err = await r.json()
      const detail = err.detail
      actionError.value = Array.isArray(detail)
        ? detail.map(d => d.msg).join(', ')
        : (typeof detail === 'string' ? detail : `Error ${r.status}`)
      return
    }
    showActionForm.value = false
    editingActionId.value = null
    await load()
  } finally {
    savingAction.value = false
  }
}

async function toggleAction(a) {
  await fetch(`/api/workspaces/${id}/event-actions/${a.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: !a.enabled }),
  })
  await load()
}

async function deleteAction(actionId) {
  if (!confirm('Delete this workspace event action?')) return
  await fetch(`/api/workspaces/${id}/event-actions/${actionId}`, { method: 'DELETE' })
  await load()
}

function toggleActionOutput(actionId) {
  expandedActionOutputs.value[actionId] = !expandedActionOutputs.value[actionId]
}

function actionRelativeTime(iso) {
  if (!iso) return 'never'
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return `${Math.round(diff)}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} hr ago`
  return `${Math.round(diff / 86400)} days ago`
}

function actionTypeBadgeClass(eventType) {
  return {
    'badge-sent': eventType === 'topic_message_sent',
    'badge-received': eventType === 'topic_message_received',
    'badge-archived': eventType === 'topic_archived',
    'badge-archiving': eventType === 'topic_archiving',
  }
}

function actionRunStatusClass(status) {
  return status === 'ok' ? 'status-ok' : 'status-error'
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
    const name = editingStaff.value
    const url = name ? `/api/workspaces/${id}/staffs/${name}` : `/api/workspaces/${id}/staffs`
    const method = name ? 'PUT' : 'POST'
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!r.ok) {
      const err = await r.json()
      staffError.value = err.detail || 'Error saving staff'
      return
    }
    showStaffForm.value = false
    editingStaff.value = null
    await load()
  } finally {
    savingStaff.value = false
  }
}

async function deleteStaff(name) {
  if (!confirm(`Delete staff @${name} from this workspace?`)) return
  await fetch(`/api/workspaces/${id}/staffs/${name}`, { method: 'DELETE' })
  await load()
}

async function setDefaultStaff(name) {
  await fetch(`/api/workspaces/${id}/staffs/${name}/set-default`, { method: 'POST' })
  await load()
}

onMounted(async () => {
  await load()
  if (!isArchived.value) {
    await fetchAgentStatus()
    statusTimer = setInterval(fetchAgentStatus, 10000)
    fetchBranches()
  }
})

onUnmounted(() => {
  if (statusTimer) clearInterval(statusTimer)
  if (branchCloseTimer) clearTimeout(branchCloseTimer)
})
</script>

<style scoped>
.breadcrumb { font-size: 0.9em; color: #64748b; margin-bottom: 1rem; }
.archived-banner { background: #fef9c3; border: 1px solid #fde047; border-radius: 6px; padding: 0.5rem 1rem; margin-bottom: 1rem; font-size: 0.9em; color: #713f12; }
.agent-status-bar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; font-size: 0.85em; flex-wrap: wrap; }
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.agent-status-label { color: #64748b; }
.status-badge { padding: 2px 10px; border-radius: 10px; font-weight: 600; font-size: 0.85em; }
.status-running { background: #dcfce7; color: #15803d; }
.status-restarting { background: #fef9c3; color: #92400e; }
.status-crashed { background: #fee2e2; color: #dc2626; }
.status-stopped { background: #f1f5f9; color: #475569; }
.status-unknown { background: #f1f5f9; color: #94a3b8; }
.agent-version { color: #94a3b8; font-size: 0.85em; }
.status-error { color: #dc2626; font-size: 0.9em; }
.btn-refresh-auth { margin-left: 0.75rem; padding: 2px 10px; font-size: 0.8em; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 4px; cursor: pointer; }
.btn-refresh-auth:hover:not(:disabled) { background: #e2e8f0; }
.btn-refresh-auth:disabled { opacity: 0.6; cursor: default; }
.btn-restart-agent { padding: 2px 10px; font-size: 0.8em; background: #fff7ed; color: #c2410c; border: 1px solid #fed7aa; border-radius: 4px; cursor: pointer; }
.btn-restart-agent:hover:not(:disabled) { background: #ffedd5; }
.btn-restart-agent:disabled { opacity: 0.6; cursor: default; }
h2 { margin: 0; }
.header-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
.archived-link { font-size: 0.85em; color: #64748b; text-decoration: none; }
.archived-link:hover { text-decoration: underline; color: #2563eb; }
section { margin-bottom: 2rem; }
.staffs-section { border-top: 1px solid #e2e8f0; padding-top: 1.5rem; }
.create-form { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; }
.create-form input { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; flex: 1; min-width: 120px; }
.create-form button { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; }
.create-form button:disabled { opacity: 0.6; cursor: default; }
.list { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }
.list li { background: #fff; padding: 0.75rem 1rem; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.08); display: flex; align-items: center; justify-content: space-between; }
.topic-row { display: flex; align-items: center; gap: 0.5rem; flex: 1; }

.card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 1.25rem; margin-bottom: 1.25rem; }
.card h3 { margin: 0 0 1rem; font-size: 1rem; color: #334155; }
.form-grid { display: grid; grid-template-columns: 150px 1fr; gap: 0.5rem 1rem; align-items: start; margin-bottom: 1rem; }
.form-grid label { font-size: 0.9em; color: #475569; padding-top: 0.4rem; }
.form-grid input, .form-grid select, .form-grid textarea { padding: 0.4rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9em; font-family: inherit; width: 100%; box-sizing: border-box; }
.form-grid textarea { resize: vertical; }
.checkbox-label { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9em; padding-top: 0.2rem; }
.form-actions { display: flex; gap: 0.75rem; align-items: center; }
.req { color: #dc2626; }
.error { color: #dc2626; font-size: 0.9em; }

.staff-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
.staff-table th { text-align: left; padding: 0.4rem 0.75rem; border-bottom: 2px solid #e2e8f0; color: #64748b; font-weight: 600; }
.staff-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #f1f5f9; }
.staff-table tr.inherited td { color: #94a3b8; }
.badge-global { background: #ede9fe; color: #6d28d9; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.badge-local { background: #dcfce7; color: #15803d; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.actions { white-space: nowrap; }
.action-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.85em; padding: 0.15rem 0.4rem; border-radius: 3px; text-decoration: underline; }
.action-btn:hover { background: #eff6ff; }
.remove-btn { color: #94a3b8; text-decoration: none; }
.remove-btn:hover { background: #fee2e2; color: #dc2626; }

.btn-add { padding: 0.35rem 0.85rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
.btn-primary { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.btn-primary:disabled { opacity: 0.6; cursor: default; }
.btn-secondary { padding: 0.4rem 1rem; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.hint { font-size: 0.85em; margin-bottom: 0.75rem; }
.muted { color: #64748b; }
.small { font-size: 0.82em; }
.remove-btn { background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 0.9em; padding: 0.15rem 0.4rem; border-radius: 3px; }
.remove-btn:hover { background: #fee2e2; color: #dc2626; }
.set-default-btn { color: #64748b; text-decoration: none; }
.set-default-btn:hover { background: #f0fdf4; color: #15803d; }
.badge-default { background: #dcfce7; color: #15803d; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.topic-settings-btn { color: #94a3b8; text-decoration: none; font-size: 1em; padding: 0.15rem 0.4rem; border-radius: 3px; }
.topic-settings-btn:hover { background: #f1f5f9; color: #475569; }

.branch-picker { position: relative; min-width: 180px; flex: 1; max-width: 260px; }
.branch-input-wrap { display: flex; align-items: center; border: 1px solid #cbd5e1; border-radius: 4px; background: #fff; overflow: visible; }
.branch-input { flex: 1; padding: 0.4rem 0.5rem; border: none; outline: none; font-size: 0.9em; background: transparent; min-width: 0; }
.branch-clear { background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 1em; padding: 0 0.3rem; line-height: 1; }
.branch-clear:hover { color: #dc2626; }
.branch-caret { padding: 0 0.4rem; color: #64748b; cursor: pointer; font-size: 0.75em; user-select: none; }
.branch-caret:hover { color: #1e293b; }
.branch-dropdown { position: absolute; top: calc(100% + 2px); left: 0; right: 0; background: #fff; border: 1px solid #cbd5e1; border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,.1); max-height: 220px; overflow-y: auto; z-index: 100; list-style: none; padding: 0.25rem 0; margin: 0; }
.branch-dropdown li { padding: 0.35rem 0.75rem; cursor: pointer; font-size: 0.88em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.branch-dropdown li:hover { background: #f1f5f9; }
.branch-dropdown li.branch-selected { background: #eff6ff; color: #2563eb; font-weight: 500; }
.branch-hint { color: #94a3b8; cursor: default !important; font-size: 0.85em; }
.branch-hint:hover { background: none !important; }
.repo-ref-badge { background: #dbeafe; color: #1d4ed8; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 500; margin-left: 0.25rem; }

@media (max-width: 768px) {
  h2 { font-size: 1.15rem; }
  .breadcrumb { font-size: 0.85em; word-break: break-word; }
  .header-row { flex-wrap: wrap; gap: 0.5rem; }
  .agent-status-bar { gap: 0.4rem 0.5rem; }
  .btn-refresh-auth { margin-left: 0; }
  .create-form input { flex: 1 1 100%; min-width: 0; }
  .branch-picker { flex: 1 1 100%; max-width: none; }
  .create-form button { width: 100%; }
  .list li { flex-direction: column; align-items: stretch; gap: 0.5rem; }
  .topic-row { flex-wrap: wrap; }
  .card { padding: 1rem; }
  .form-grid { grid-template-columns: 1fr; gap: 0.25rem 0; }
  .form-grid label { padding-top: 0.25rem; font-weight: 500; }
  .form-grid label + input,
  .form-grid label + select,
  .form-grid label + textarea,
  .form-grid label + .checkbox-label { margin-bottom: 0.5rem; }
  .form-actions { flex-wrap: wrap; }
}
.event-actions-section { border-top: 1px solid #e2e8f0; padding-top: 1.5rem; }
.action-list { display: flex; flex-direction: column; gap: 0.75rem; }
.action-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 6px; padding: 0.75rem 1rem; }
.action-header { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.action-toggle input { cursor: pointer; }
.action-type-badge { border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.badge-sent { background: #dbeafe; color: #1d4ed8; }
.badge-received { background: #dcfce7; color: #15803d; }
.badge-archived { background: #fef9c3; color: #713f12; }
.badge-archiving { background: #fee2e2; color: #991b1b; }
.timing-badge { background: #f1f5f9; color: #475569; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; }
.structured-badge { background: #ecfdf5; color: #065f46; border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.action-staff { font-weight: 600; font-size: 0.9em; }
.action-preview { font-size: 0.82em; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.action-btns { margin-left: auto; display: flex; gap: 0.25rem; white-space: nowrap; }
.action-run-info { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.4rem; font-size: 0.82em; }
.run-status-badge { border-radius: 10px; padding: 1px 8px; font-size: 0.78em; font-weight: 600; }
.status-ok { background: #dcfce7; color: #15803d; }
.status-error { background: #fee2e2; color: #dc2626; }
.run-output-toggle { margin-left: 0.25rem; }
.link-btn { background: none; border: none; color: #2563eb; cursor: pointer; font-size: 0.82em; text-decoration: underline; padding: 0; }
.run-output { margin-top: 0.35rem; background: #1e293b; color: #e2e8f0; padding: 0.5rem 0.75rem; border-radius: 4px; font-size: 0.78em; font-family: monospace; white-space: pre-wrap; word-break: break-all; }

.veto-rejected-btn { background: #fef2f2 !important; color: #dc2626 !important; border: 1px solid #fecaca !important; }
.veto-rejected-btn:hover { background: #fee2e2 !important; }
.veto-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.veto-dialog { background: #fff; border-radius: 8px; padding: 1.5rem; max-width: 480px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.18); }
.veto-dialog h3 { margin: 0 0 0.75rem; color: #dc2626; }
.veto-subject { margin: 0 0 0.5rem; font-size: 0.95em; }
.veto-reason { background: #fef2f2; border: 1px solid #fecaca; border-radius: 4px; padding: 0.5rem 0.75rem; font-size: 0.9em; color: #991b1b; margin: 0.5rem 0 1rem; white-space: pre-wrap; }
</style>
