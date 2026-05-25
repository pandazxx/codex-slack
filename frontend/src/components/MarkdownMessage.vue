<template>
  <div class="md-wrap">
    <div class="md-body" ref="container" v-html="rendered"></div>
  </div>
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
import { ref, computed, watch, nextTick, onMounted } from 'vue'

const props = defineProps({
  text: { type: String, required: true },
  fullText: { type: String, default: null },
})

const container = ref(null)

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

// Skip mermaid rendering on the collapsed bubble (fullText present = preview mode).
// The caller renders the full text in the expand dialog via a separate MarkdownMessage.
onMounted(() => { if (!props.fullText) nextTick(() => renderMermaid(container.value)) })
watch(rendered, () => { if (!props.fullText) nextTick(() => renderMermaid(container.value)) })
</script>

<style scoped>
.md-wrap { position: relative; }
.md-body { line-height: 1.6; word-break: break-word; }
.md-body :deep(p) { margin: 0 0 0.6em; }
.md-body :deep(p:last-child) { margin-bottom: 0; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3) { margin: 0.75em 0 0.3em; font-weight: 600; }
.md-body :deep(ul), .md-body :deep(ol) { margin: 0.4em 0; padding-left: 1.4em; }
.md-body :deep(li) { margin: 0.15em 0; }
.md-body :deep(blockquote) { border-left: 3px solid #cbd5e1; margin: 0.5em 0; padding: 0.2em 0.75em; color: #64748b; }
.md-body :deep(pre) { background: #1e293b; border-radius: 6px; padding: 0.75rem 1rem; overflow-x: auto; margin: 0.5em 0; }
.md-body :deep(code) { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.88em; }
.md-body :deep(pre code) { background: none; padding: 0; border-radius: 0; color: #e2e8f0; }
.md-body :deep(:not(pre) > code) { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 3px; padding: 1px 5px; }
.md-body :deep(table) { border-collapse: collapse; width: 100%; margin: 0.5em 0; font-size: 0.9em; }
.md-body :deep(th) { background: #f8fafc; border: 1px solid #e2e8f0; padding: 0.4rem 0.75rem; text-align: left; font-weight: 600; }
.md-body :deep(td) { border: 1px solid #e2e8f0; padding: 0.35rem 0.75rem; }
.md-body :deep(tr:nth-child(even)) { background: #f8fafc; }
.md-body :deep(a) { color: #2563eb; text-decoration: underline; }
.md-body :deep(img) { max-width: 100%; border-radius: 6px; }
.md-body :deep(hr) { border: none; border-top: 1px solid #e2e8f0; margin: 0.75em 0; }
.md-body :deep(.mermaid-wrap) { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 0.5rem; text-align: center; overflow-x: auto; margin: 0.5em 0; }
.md-body :deep(.mermaid-wrap svg) { max-width: 100%; height: auto; }
.md-body :deep(.mermaid-error) { color: #dc2626; font-size: 0.85em; padding: 0.75rem; }
/* highlight.js tokens */
.md-body :deep(.hljs-keyword), .md-body :deep(.hljs-selector-tag) { color: #c792ea; }
.md-body :deep(.hljs-string) { color: #c3e88d; }
.md-body :deep(.hljs-comment) { color: #546e7a; font-style: italic; }
.md-body :deep(.hljs-number), .md-body :deep(.hljs-literal) { color: #f78c6c; }
.md-body :deep(.hljs-title), .md-body :deep(.hljs-function) { color: #82aaff; }
.md-body :deep(.hljs-built_in) { color: #80cbc4; }
.md-body :deep(.hljs-attr), .md-body :deep(.hljs-attribute) { color: #ffcb6b; }
.md-body :deep(.hljs-type) { color: #decb6b; }
.md-body :deep(.hljs-symbol) { color: #89ddff; }
</style>
