<template>
  <div v-if="selectedFiles.length" class="file-chips">
    <span v-for="(f, i) in selectedFiles" :key="i" class="file-chip">
      {{ f.name }}
      <button class="chip-remove" @click="removeFile(i)">×</button>
    </span>
  </div>
  <form @submit.prevent="handleSend" class="send-form">
    <div v-if="availableStaffs.length" class="send-staff-row">
      <label class="send-staff-label">Staff:</label>
      <select class="send-staff-select" :value="topic?.current_staff_name || ''" @change="emit('set-staff', $event.target.value)">
        <option value="">— use default —</option>
        <option v-for="s in availableStaffs" :key="s.name" :value="s.name">@{{ s.name }}</option>
      </select>
    </div>
    <div class="send-input-row">
      <input type="file" multiple ref="fileInput" class="file-input-hidden" @change="onFilesSelected" />
      <button type="button" class="attach-btn" @click="fileInput.click()" :disabled="sending" title="Attach files">📎</button>
      <textarea
        v-model="text"
        placeholder="Type a message…"
        rows="3"
        :disabled="sending"
        @keydown.enter.shift.exact.prevent="handleSend"
        @paste="onPaste"
      />
      <div class="send-side">
        <button type="button" class="expand-btn" @click="openComposeOverlay" title="Open full-screen editor">⊞</button>
        <button type="submit" :disabled="sending || !canSend">
          {{ sending ? 'Sending…' : 'Send' }}
        </button>
      </div>
    </div>
  </form>
  <p v-if="sendError" class="send-error">{{ sendError }}</p>
  <p class="hint muted">Shift+Enter to send · Enter for new line · Use <code>@name</code> to address a specific staff</p>
  <Teleport to="body">
    <div v-if="composeOverlay" class="compose-overlay" @click.self="closeComposeOverlay">
      <div class="compose-modal">
        <div class="compose-modal-header">
          <span class="compose-modal-title">Compose message</span>
          <button class="compose-modal-close" @click="closeComposeOverlay" title="Close (Esc)">✕</button>
        </div>
        <textarea
          class="compose-modal-textarea"
          v-model="text"
          placeholder="Type a message…"
          :disabled="sending"
          @keydown.shift.enter.exact.prevent="handleSendFromOverlay"
          @paste="onPaste"
          ref="overlayTextarea"
        />
        <div class="compose-modal-footer">
          <span class="compose-modal-hint">Shift+Enter to send · Esc to close</span>
          <div class="compose-modal-actions">
            <button type="button" class="compose-modal-cancel" @click="closeComposeOverlay">Cancel</button>
            <button type="button" class="compose-modal-send" @click="handleSendFromOverlay" :disabled="sending || !canSend">
              {{ sending ? 'Sending…' : 'Send' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'

const props = defineProps({
  sending: Boolean,
  sendError: String,
  availableStaffs: Array,
  topic: Object,
  topicId: String,
})

const emit = defineEmits(['send', 'set-staff'])

const text = ref('')
const selectedFiles = ref([])
const composeOverlay = ref(false)
const overlayTextarea = ref(null)
const fileInput = ref(null)
const overlayHasPendingSend = ref(false)

const canSend = computed(() => !!text.value.trim())

function draftKey(tid) { return `draft:${tid}` }
function saveDraft(tid, val) {
  if (val) localStorage.setItem(draftKey(tid), val)
  else localStorage.removeItem(draftKey(tid))
}
function loadDraft(tid) { return localStorage.getItem(draftKey(tid)) || '' }

onMounted(() => {
  text.value = loadDraft(props.topicId)
  document.addEventListener('keydown', _handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', _handleKeydown)
  clearTimeout(draftTimer)
})

watch(() => props.topicId, (newId) => {
  text.value = loadDraft(newId)
})

let draftTimer = null
watch(text, (val) => {
  clearTimeout(draftTimer)
  draftTimer = setTimeout(() => saveDraft(props.topicId, val), 500)
})

// Close compose overlay after a send triggered from it, once sending completes without error
watch(() => props.sending, (isSending) => {
  if (!isSending && overlayHasPendingSend.value) {
    overlayHasPendingSend.value = false
    nextTick(() => {
      if (!props.sendError) closeComposeOverlay()
    })
  }
})

function handleSend() {
  const msg = text.value.trim()
  if (!msg || props.sending) return
  emit('send', { text: msg, files: [...selectedFiles.value] })
}

function handleSendFromOverlay() {
  if (!text.value.trim() || props.sending) return
  overlayHasPendingSend.value = true
  handleSend()
}

function reset() {
  text.value = ''
  selectedFiles.value = []
  saveDraft(props.topicId, '')
}

defineExpose({ reset })

function openComposeOverlay() {
  composeOverlay.value = true
  nextTick(() => overlayTextarea.value?.focus())
}

function closeComposeOverlay() {
  composeOverlay.value = false
}

function _handleKeydown(e) {
  if (e.key === 'Escape' && composeOverlay.value) closeComposeOverlay()
}

function onFilesSelected(evt) {
  const files = Array.from(evt.target.files || [])
  selectedFiles.value = [...selectedFiles.value, ...files]
  evt.target.value = ''
}

function removeFile(index) {
  selectedFiles.value = selectedFiles.value.filter((_, i) => i !== index)
}

function mimeToExt(mime) {
  switch (mime) {
    case 'image/png':     return 'png'
    case 'image/jpeg':    return 'jpg'
    case 'image/gif':     return 'gif'
    case 'image/webp':    return 'webp'
    case 'image/bmp':     return 'bmp'
    case 'image/svg+xml': return 'svg'
    default: {
      const m = /^image\/([a-z0-9+-]+)$/i.exec(mime)
      if (!m) return 'png'
      const ext = m[1].split('+')[0].replace(/[^a-z0-9]/gi, '')
      return ext || 'png'
    }
  }
}

function renamePastedImage(file) {
  const ext = mimeToExt(file.type) || 'png'
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  const name = `pasted-image-${ts}.${ext}`
  return new File([file], name, { type: file.type, lastModified: file.lastModified })
}

function onPaste(evt) {
  if (props.sending) return
  const items = evt.clipboardData?.items
  if (!items || !items.length) return

  const pastedImages = []
  for (const item of items) {
    if (item.kind !== 'file') continue
    if (!item.type || !item.type.startsWith('image/')) continue
    const file = item.getAsFile()
    if (!file) continue
    pastedImages.push(renamePastedImage(file))
  }

  if (pastedImages.length === 0) return
  evt.preventDefault()
  selectedFiles.value = [...selectedFiles.value, ...pastedImages]
}
</script>

<style scoped>
.send-form { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.75rem; }
.send-staff-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.35rem 0.6rem; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; }
.send-staff-label { font-size: 0.8em; font-weight: 600; color: #475569; white-space: nowrap; }
.send-staff-select { flex: 1; font-size: 0.88em; padding: 0.25rem 0.5rem; border: 1px solid #cbd5e1; border-radius: 4px; background: #fff; color: #1e293b; cursor: pointer; }
.send-input-row { display: flex; gap: 0.5rem; align-items: flex-end; }
.send-input-row textarea { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid #cbd5e1; border-radius: 6px; resize: none; font-family: inherit; font-size: 0.95rem; }
.send-side { display: flex; flex-direction: column; gap: 0.35rem; align-items: stretch; }
.send-side button { padding: 0.5rem 1.25rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
.send-side button:disabled { opacity: 0.6; cursor: default; }
.expand-btn { background: #f1f5f9 !important; color: #475569 !important; border: 1px solid #cbd5e1 !important; font-size: 1rem; padding: 0.3rem 0.5rem !important; }
.expand-btn:hover { background: #e2e8f0 !important; }
.compose-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 200; }
.compose-modal { background: #fff; border-radius: 10px; width: 90vw; height: 85vh; display: flex; flex-direction: column; padding: 1rem; gap: 0.75rem; box-shadow: 0 20px 60px rgba(0,0,0,0.35); }
.compose-modal-header { display: flex; align-items: center; justify-content: space-between; }
.compose-modal-title { font-size: 0.95em; font-weight: 600; color: #1e293b; }
.compose-modal-close { background: none; border: none; font-size: 1.1rem; color: #94a3b8; cursor: pointer; line-height: 1; padding: 2px 6px; border-radius: 4px; }
.compose-modal-close:hover { background: #f1f5f9; color: #475569; }
.compose-modal-textarea { flex: 1; resize: none; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0.75rem; font-family: inherit; font-size: 1rem; line-height: 1.5; outline: none; }
.compose-modal-textarea:focus { border-color: #2563eb; box-shadow: 0 0 0 2px #2563eb26; }
.compose-modal-footer { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; }
.compose-modal-hint { font-size: 0.78em; color: #94a3b8; }
.compose-modal-actions { display: flex; gap: 0.5rem; }
.compose-modal-cancel { padding: 0.45rem 1rem; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 6px; cursor: pointer; font-size: 0.9em; }
.compose-modal-cancel:hover { background: #e2e8f0; }
.compose-modal-send { padding: 0.45rem 1.25rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em; font-weight: 600; }
.compose-modal-send:disabled { opacity: 0.6; cursor: default; }
.compose-modal-send:not(:disabled):hover { background: #1d4ed8; }
.attach-btn { background: #f1f5f9; color: #475569; padding: 0.5rem 0.6rem; font-size: 1.1rem; border: 1px solid #cbd5e1; }
.attach-btn:hover:not(:disabled) { background: #e2e8f0; }
.file-input-hidden { display: none; }
.file-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }
.file-chip { display: flex; align-items: center; gap: 4px; background: #e0f2fe; color: #0369a1; border-radius: 12px; padding: 2px 10px; font-size: 0.8em; }
.chip-remove { background: none; border: none; cursor: pointer; color: #0369a1; font-size: 0.9em; padding: 0; line-height: 1; }
.send-error { color: #dc2626; font-size: 0.85em; margin-top: 0.25rem; }
.hint { font-size: 0.8em; text-align: right; margin-top: 0.25rem; }
.muted { color: #64748b; }

@media (max-width: 768px) {
  .send-form { gap: 0.35rem; }
  .send-form textarea { font-size: 16px; padding: 0.5rem 0.6rem; }
  .send-form button { padding: 0.5rem 0.85rem; }
}
</style>
