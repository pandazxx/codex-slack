<template>
  <div class="md-wrap">
    <div class="md-body" ref="container" v-html="rendered"></div>
    <button class="expand-btn" @click="expanded = true" title="Expand message">⛶</button>
  </div>
  <Teleport to="body">
    <div v-if="expanded" class="md-overlay" @click.self="expanded = false">
      <div class="md-dialog">
        <button class="md-close" @click="expanded = false" title="Close (Esc)">✕</button>
        <div class="md-dialog-body" ref="dialogContainer" v-html="rendered"></div>
      </div>
    </div>
  </Teleport>
</template>

<script>
// Module-level singletons — shared across all MarkdownMessage instances so
// mermaid is initialized exactly once and IDs never collide between bubbles.
import { marked } from 'marked'
import hljs from 'highlight.js/lib/common'

let _mermaidMod = null
let _mermaidSeq = 0

async function _getMermaid() {
  if (!_mermaidMod) {
    const m = await import('mermaid')
    _mermaidMod = m.default
    _mermaidMod.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' })
  }
  return _mermaidMod
}

function _esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

marked.use({
  breaks: true,
  renderer: {
    code({ text, lang }) {
      if (lang === 'mermaid') {
        return `<pre class="mermaid-block">${_esc(text)}</pre>`
      }
      const language = lang && hljs.getLanguage(lang) ? lang : null
      const highlighted = language
        ? hljs.highlight(text, { language }).value
        : _esc(text)
      const cls = language ? ` class="hljs language-${language}"` : ' class="hljs"'
      return `<pre><code${cls}>${highlighted}</code></pre>`
    },
    link({ href, title, text }) {
      const t = title ? ` title="${_esc(title)}"` : ''
      return `<a href="${href}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`
    },
  },
})
</script>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  text: { type: String, required: true },
})

const container = ref(null)
const dialogContainer = ref(null)
const expanded = ref(false)

const rendered = computed(() => marked.parse(props.text || ''))

async function renderMermaid(el) {
  const blocks = Array.from(
    el?.querySelectorAll('pre.mermaid-block:not([data-processed])') ?? []
  )
  if (!blocks.length) return
  const m = await _getMermaid()
  for (const block of blocks) {
    block.dataset.processed = 'pending'
    const source = block.textContent ?? ''
    const wrapper = document.createElement('div')
    wrapper.className = 'mermaid-wrap'
    block.replaceWith(wrapper)
    try {
      const { svg } = await m.render(`mermaid-${++_mermaidSeq}`, source)
      wrapper.innerHTML = svg
    } catch (e) {
      wrapper.textContent = `[diagram error: ${e?.message ?? e}]`
      wrapper.className = 'mermaid-wrap mermaid-error'
    }
  }
}

function onEsc(e) {
  if (e.key === 'Escape') expanded.value = false
}

watch(expanded, (val) => {
  if (val) {
    nextTick(() => renderMermaid(dialogContainer.value))
    document.addEventListener('keydown', onEsc)
  } else {
    document.removeEventListener('keydown', onEsc)
  }
})

onMounted(() => nextTick(() => renderMermaid(container.value)))
watch(rendered, () => nextTick(() => renderMermaid(container.value)))
onUnmounted(() => document.removeEventListener('keydown', onEsc))
</script>

<style scoped>
.md-wrap { position: relative; }
.expand-btn {
  position: absolute;
  top: 0; right: 0;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 0.85rem;
  line-height: 1.5;
  cursor: pointer;
  color: #64748b;
  opacity: 0;
  transition: opacity 0.15s;
}
.md-wrap:hover .expand-btn { opacity: 1; }
.expand-btn:hover { background: #f1f5f9; color: #1e293b; }

/* ── fullscreen overlay ── */
.md-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  z-index: 9999;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 5vh 5vw;
  overflow-y: auto;
}
.md-dialog {
  position: relative;
  background: #fff;
  border-radius: 10px;
  width: 100%;
  max-width: 880px;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.35);
}
.md-close {
  position: absolute;
  top: 10px; right: 14px;
  z-index: 1;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 0.85rem;
  cursor: pointer;
  color: #475569;
}
.md-close:hover { background: #e2e8f0; color: #1e293b; }
.md-dialog-body {
  padding: 2rem 2.5rem;
  overflow-y: auto;
  flex: 1;
  line-height: 1.6;
  word-break: break-word;
}

/* ── shared markdown styles (inline bubble + fullscreen dialog) ── */
.md-body { line-height: 1.6; word-break: break-word; }
.md-body :deep(p),
.md-dialog-body :deep(p) { margin: 0 0 0.6em; }
.md-body :deep(p:last-child),
.md-dialog-body :deep(p:last-child) { margin-bottom: 0; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3),
.md-dialog-body :deep(h1), .md-dialog-body :deep(h2), .md-dialog-body :deep(h3) { margin: 0.75em 0 0.3em; font-weight: 600; }
.md-body :deep(ul), .md-body :deep(ol),
.md-dialog-body :deep(ul), .md-dialog-body :deep(ol) { margin: 0.4em 0; padding-left: 1.4em; }
.md-body :deep(li),
.md-dialog-body :deep(li) { margin: 0.15em 0; }
.md-body :deep(blockquote),
.md-dialog-body :deep(blockquote) { border-left: 3px solid #cbd5e1; margin: 0.5em 0; padding: 0.2em 0.75em; color: #64748b; }
.md-body :deep(pre),
.md-dialog-body :deep(pre) { background: #1e293b; border-radius: 6px; padding: 0.75rem 1rem; overflow-x: auto; margin: 0.5em 0; }
.md-body :deep(code),
.md-dialog-body :deep(code) { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.88em; }
.md-body :deep(pre code),
.md-dialog-body :deep(pre code) { background: none; padding: 0; border-radius: 0; color: #e2e8f0; }
.md-body :deep(:not(pre) > code),
.md-dialog-body :deep(:not(pre) > code) { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 3px; padding: 1px 5px; }
.md-body :deep(table),
.md-dialog-body :deep(table) { border-collapse: collapse; width: 100%; margin: 0.5em 0; font-size: 0.9em; }
.md-body :deep(th),
.md-dialog-body :deep(th) { background: #f8fafc; border: 1px solid #e2e8f0; padding: 0.4rem 0.75rem; text-align: left; font-weight: 600; }
.md-body :deep(td),
.md-dialog-body :deep(td) { border: 1px solid #e2e8f0; padding: 0.35rem 0.75rem; }
.md-body :deep(tr:nth-child(even)),
.md-dialog-body :deep(tr:nth-child(even)) { background: #f8fafc; }
.md-body :deep(a),
.md-dialog-body :deep(a) { color: #2563eb; text-decoration: underline; }
.md-body :deep(img),
.md-dialog-body :deep(img) { max-width: 100%; border-radius: 6px; }
.md-body :deep(hr),
.md-dialog-body :deep(hr) { border: none; border-top: 1px solid #e2e8f0; margin: 0.75em 0; }
.md-body :deep(.mermaid-wrap),
.md-dialog-body :deep(.mermaid-wrap) { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 0.5rem; text-align: center; overflow-x: auto; margin: 0.5em 0; }
.md-body :deep(.mermaid-wrap svg),
.md-dialog-body :deep(.mermaid-wrap svg) { max-width: 100%; height: auto; }
.md-body :deep(.mermaid-error),
.md-dialog-body :deep(.mermaid-error) { color: #dc2626; font-size: 0.85em; padding: 0.75rem; }
/* highlight.js tokens */
.md-body :deep(.hljs-keyword), .md-body :deep(.hljs-selector-tag),
.md-dialog-body :deep(.hljs-keyword), .md-dialog-body :deep(.hljs-selector-tag) { color: #c792ea; }
.md-body :deep(.hljs-string),
.md-dialog-body :deep(.hljs-string) { color: #c3e88d; }
.md-body :deep(.hljs-comment),
.md-dialog-body :deep(.hljs-comment) { color: #546e7a; font-style: italic; }
.md-body :deep(.hljs-number), .md-body :deep(.hljs-literal),
.md-dialog-body :deep(.hljs-number), .md-dialog-body :deep(.hljs-literal) { color: #f78c6c; }
.md-body :deep(.hljs-title), .md-body :deep(.hljs-function),
.md-dialog-body :deep(.hljs-title), .md-dialog-body :deep(.hljs-function) { color: #82aaff; }
.md-body :deep(.hljs-built_in),
.md-dialog-body :deep(.hljs-built_in) { color: #80cbc4; }
.md-body :deep(.hljs-attr), .md-body :deep(.hljs-attribute),
.md-dialog-body :deep(.hljs-attr), .md-dialog-body :deep(.hljs-attribute) { color: #ffcb6b; }
.md-body :deep(.hljs-type),
.md-dialog-body :deep(.hljs-type) { color: #decb6b; }
.md-body :deep(.hljs-symbol),
.md-dialog-body :deep(.hljs-symbol) { color: #89ddff; }
</style>
