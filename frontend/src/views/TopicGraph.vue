<template>
  <div class="graph-layout">
    <p class="breadcrumb">
      <RouterLink to="/">Workspaces</RouterLink> /
      <RouterLink :to="`/workspaces/${wsId}`">{{ workspace?.name || wsId }}</RouterLink> /
      {{ topic?.subject || topicId }}
      <GraphHeaderToggle :wsId="wsId" :topicId="topicId" activeView="graph" class="breadcrumb-toggle" />
    </p>

    <div class="graph-toolbar">
      <button class="toolbar-btn" @click="collapseAll">Collapse all</button>
      <button class="toolbar-btn" @click="expandAll">Expand all</button>
      <button class="toolbar-btn" @click="fitView">Fit view</button>
      <label class="toolbar-btn minimap-toggle">
        <input type="checkbox" v-model="showMinimap" @change="saveMinimapPref" />
        Minimap
      </label>
      <div v-if="diagnosticChip" class="diag-chip" :title="diagnosticDetail">
        &#9888; {{ diagnosticChip }}
      </div>
    </div>

    <div v-if="loading" class="graph-status">Loading…</div>
    <div v-else-if="loadError" class="graph-status graph-error">{{ loadError }}</div>

    <div v-else class="graph-canvas" @keydown="onKeydown" tabindex="0">
      <VueFlow
        :nodes="vfNodes"
        :edges="vfEdges"
        :node-types="nodeTypes"
        :default-edge-options="defaultEdgeOptions"
        fit-view-on-init
        class="graph-flow"
        @node-click="onNodeClick"
        @pane-click="clearSelection"
        @init="onFlowInit"
      >
        <Background />
        <Controls />
        <MiniMap v-if="showMinimap" :node-color="minimapColor" />
      </VueFlow>
    </div>

    <NodeDetailPanel
      v-if="selectedNode"
      :node="selectedNode"
      :graph="graph"
      :wsId="wsId"
      :topicId="topicId"
      @close="clearSelection"
      @expand-toggle="toggleCollapse"
    />
  </div>
</template>

<script setup>
import { ref, shallowRef, triggerRef, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

import { buildTopicGraph } from '../lib/transcriptGraph.js'
import { layoutGraph, estimateNodeHeight } from '../lib/graphLayout.js'
import GraphHeaderToggle from '../components/GraphHeaderToggle.vue'
import NodeDetailPanel from '../components/graph/NodeDetailPanel.vue'
import TopicRootNode from '../components/graph/TopicRootNode.vue'
import UserMessageNode from '../components/graph/UserMessageNode.vue'
import AgentMessageNode from '../components/graph/AgentMessageNode.vue'
import ThinkingNode from '../components/graph/ThinkingNode.vue'
import TextNode from '../components/graph/TextNode.vue'
import ToolUseNode from '../components/graph/ToolUseNode.vue'
import ToolResultNode from '../components/graph/ToolResultNode.vue'
import SubagentNode from '../components/graph/SubagentNode.vue'
import ResultRollupNode from '../components/graph/ResultRollupNode.vue'
import TaskEventNode from '../components/graph/TaskEventNode.vue'
import CompactionNode from '../components/graph/CompactionNode.vue'
import SystemInitNode from '../components/graph/SystemInitNode.vue'
import ParseWarningNode from '../components/graph/ParseWarningNode.vue'

const route = useRoute()
const router = useRouter()
const wsId = route.params.wsId
const topicId = route.params.topicId

const topic = ref(null)
const workspace = ref(null)
const graph = shallowRef(null)
const loading = ref(true)
const loadError = ref('')
const selectedNodeId = ref(route.query.node ?? null)
const showMinimap = ref(localStorage.getItem('topicGraph.showMinimap') !== 'false')

// Embedded child-list state (graph renders first-level message boxes with their
// children as an in-box scrollable list rather than as separate spatial nodes).
const SPINE_KINDS = new Set(['user-message', 'agent-message'])
const LIST_DEFAULT_HEIGHT = 200
const LIST_CHROME = 28
const BOX_DEFAULT_WIDTH = 296
const expandedLists = ref(new Set())
const listHeights = ref({})
const boxWidths = ref({})

function getListHeight(id) { return listHeights.value[id] ?? LIST_DEFAULT_HEIGHT }
function getBoxWidth(id) { return boxWidths.value[id] ?? BOX_DEFAULT_WIDTH }

function setListHeight(id, h) {
  listHeights.value = { ...listHeights.value, [id]: h }
  recomputeLayout()
}

// Corner drag resizes the box in both axes: height feeds the layout (boxes
// below shift down); width is purely visual (no depth columns to the right).
function resizeBox(id, w, h) {
  boxWidths.value = { ...boxWidths.value, [id]: w }
  listHeights.value = { ...listHeights.value, [id]: h }
  recomputeLayout()
}

function toggleList(id) {
  const s = new Set(expandedLists.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  expandedLists.value = s
  recomputeLayout()
}

const CHILD_ICONS = {
  'thinking':      '💭',
  'text':          '📝',
  'tool-use':      '⚙',
  'tool-result':   '↳',
  'subagent':      '🤖',
  'result-rollup': '✓',
  'task-event':    '🚀',
  'compaction':    '⚖',
  'system-init':   'ⓘ',
  'parse-warning': '⚠',
}

function childText(child) {
  const d = child.data || {}
  switch (child.kind) {
    case 'thinking':      return (d.text || '').slice(0, 100) || 'Thinking'
    case 'text':          return (d.text || '').slice(0, 100) || '(text)'
    case 'tool-use':      return d.label || 'tool'
    case 'tool-result':   return (d.contentText || '').slice(0, 100) || 'result'
    case 'subagent':      return d.agentType ? `${d.agentType}: ${(d.prompt || '').slice(0, 80)}` : 'subagent'
    case 'result-rollup': return 'result'
    case 'task-event':    return (d.description || '').slice(0, 100) || 'task'
    case 'compaction':    return 'compaction'
    case 'system-init':   return 'init'
    case 'parse-warning': return 'parse error'
    default:              return child.kind
  }
}

function childItemsFor(nodeId) {
  if (!graph.value) return []
  return graph.value.nodes
    .filter(n => n.parentId === nodeId)
    .sort((a, b) => a.sequence - b.sequence)
    .map(c => ({ id: c.id, icon: CHILD_ICONS[c.kind] || '•', text: childText(c) }))
}

// Height override fed to layoutGraph: an expanded box reserves vertical space
// for its child list so boxes below it shift down instead of overlapping.
function nodeHeightOverride(node) {
  let h = estimateNodeHeight(node)
  if (node.kind === 'agent-message' && expandedLists.value.has(node.id)) {
    h += getListHeight(node.id) + LIST_CHROME
  }
  return h
}

let flowInstance = null

const nodeTypes = {
  'topic':          TopicRootNode,
  'user-message':   UserMessageNode,
  'agent-message':  AgentMessageNode,
  'thinking':       ThinkingNode,
  'text':           TextNode,
  'tool-use':       ToolUseNode,
  'tool-result':    ToolResultNode,
  'subagent':       SubagentNode,
  'result-rollup':  ResultRollupNode,
  'task-event':     TaskEventNode,
  'compaction':     CompactionNode,
  'system-init':    SystemInitNode,
  'parse-warning':  ParseWarningNode,
}

const defaultEdgeOptions = {
  style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
  animated: false,
}

function edgeStyle(kind) {
  if (kind === 'invokes') return { stroke: '#6c8cff', strokeWidth: 2 }
  if (kind === 'summarizes') return { stroke: '#a855f7', strokeDasharray: '4 3', strokeWidth: 1.5 }
  return { stroke: '#cbd5e1', strokeWidth: 1.5 }
}

const selectedNode = computed(() => {
  if (!graph.value || !selectedNodeId.value) return null
  return graph.value.nodes.find(n => n.id === selectedNodeId.value) ?? null
})

const vfNodes = computed(() => {
  if (!graph.value) return []
  const parentIds = new Set()
  for (const n of graph.value.nodes) {
    if (n.parentId != null) parentIds.add(n.parentId)
  }
  return graph.value.nodes
    .filter(n => !n.ui.hidden)
    .map(n => {
      const base = {
        id: n.id,
        type: n.kind,
        position: { x: n.ui.x ?? 0, y: n.ui.y ?? 0 },
        data: {
          ...n,
          selected: n.id === selectedNodeId.value,
          hasChildren: parentIds.has(n.id),
          // Vue Flow does not forward emits from custom node components,
          // so collapse toggling is passed down as a callback (#248).
          onExpandToggle: () => toggleCollapse(n.id),
        },
      }
      if (n.kind === 'agent-message') {
        Object.assign(base.data, {
          childItems: childItemsFor(n.id),
          listExpanded: expandedLists.value.has(n.id),
          listHeight: getListHeight(n.id),
          boxWidth: getBoxWidth(n.id),
          onListToggle: () => toggleList(n.id),
          onBoxResize: (w, h) => resizeBox(n.id, w, h),
          onChildSelect: (cid) => selectNode(cid),
        })
      }
      return base
    })
})

const vfEdges = computed(() => {
  if (!graph.value) return []
  const visibleIds = new Set(vfNodes.value.map(n => n.id))
  return graph.value.edges
    .filter(e => visibleIds.has(e.source) && visibleIds.has(e.target))
    .map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      style: edgeStyle(e.kind),
    }))
})

const diagnosticChip = computed(() => {
  if (!graph.value?.diagnostics?.length) return null
  const warns = graph.value.diagnostics.filter(d => d.level === 'warn').length
  const errs  = graph.value.diagnostics.filter(d => d.level === 'error').length
  const parts = []
  if (errs)  parts.push(`${errs} error${errs > 1 ? 's' : ''}`)
  if (warns) parts.push(`${warns} warning${warns > 1 ? 's' : ''}`)
  return parts.join(', ')
})

const diagnosticDetail = computed(() => {
  if (!graph.value?.diagnostics?.length) return ''
  return graph.value.diagnostics.map(d => `[${d.code}] ${d.message}`).join('\n')
})

function minimapColor(node) {
  const kind = node.type
  if (kind === 'agent-message')  return '#22c55e'
  if (kind === 'user-message')   return '#2563eb'
  if (kind === 'tool-use')       return '#94a3b8'
  if (kind === 'subagent')       return '#6c8cff'
  if (kind === 'thinking')       return '#c0a85a'
  return '#e2e8f0'
}

function saveMinimapPref() {
  localStorage.setItem('topicGraph.showMinimap', String(showMinimap.value))
}

function recomputeLayout() {
  if (!graph.value) return
  layoutGraph(graph.value, { nodeHeight: nodeHeightOverride })
  triggerRef(graph)
}

// In embedded-list mode the chevron / detail-panel expand button toggles the
// in-box child list rather than un-hiding spatial child nodes.
function toggleCollapse(nodeOrData) {
  if (!graph.value) return
  const id = nodeOrData?.id ?? nodeOrData
  const node = graph.value.nodes.find(n => n.id === id)
  if (!node) return
  if (node.kind === 'agent-message') toggleList(id)
}

function collapseAll() {
  expandedLists.value = new Set()
  recomputeLayout()
}

function expandAll() {
  if (!graph.value) return
  const s = new Set()
  for (const n of graph.value.nodes) {
    if (n.kind === 'agent-message') s.add(n.id)
  }
  expandedLists.value = s
  recomputeLayout()
}

function fitView() {
  flowInstance?.fitView()
}

function onFlowInit(instance) {
  flowInstance = instance
  nextTick(() => instance.fitView())
}

function onNodeClick(evt) {
  const nodeId = evt.node?.id ?? evt.id
  if (!nodeId) return
  selectNode(nodeId)
}

function selectNode(id) {
  selectedNodeId.value = id
  router.replace({ query: { ...route.query, node: id } })
}

function clearSelection() {
  selectedNodeId.value = null
  const q = { ...route.query }
  delete q.node
  router.replace({ query: q })
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    clearSelection()
    return
  }
  if (!selectedNode.value || !graph.value) return
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    const visibleNodes = graph.value.nodes
      .filter(n => !n.ui.hidden)
      .sort((a, b) => a.sequence - b.sequence)
    const idx = visibleNodes.findIndex(n => n.id === selectedNode.value.id)
    if (idx < 0) return
    const next = e.key === 'ArrowRight'
      ? visibleNodes[idx + 1]
      : visibleNodes[idx - 1]
    if (next) selectNode(next.id)
    e.preventDefault()
  }
  if (e.key === ' ') {
    if (selectedNode.value.kind === 'agent-message') toggleList(selectedNode.value.id)
    e.preventDefault()
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [topicRes, msgsRes, wsRes] = await Promise.all([
      fetch(`/api/workspaces/${wsId}/topics/${topicId}`),
      fetch(`/api/workspaces/${wsId}/topics/${topicId}/messages`),
      fetch(`/api/workspaces/${wsId}`),
    ])
    if (!topicRes.ok) throw new Error(`Topic load failed: ${topicRes.status}`)
    if (!msgsRes.ok)  throw new Error(`Messages load failed: ${msgsRes.status}`)

    topic.value = await topicRes.json()
    const messages = await msgsRes.json()
    if (wsRes.ok) workspace.value = await wsRes.json()

    const parsedMaxEvents = Number(route.query.maxEvents)
    const maxEvents = Number.isFinite(parsedMaxEvents) && parsedMaxEvents > 0
      ? parsedMaxEvents
      : undefined
    const built = buildTopicGraph({
      workspaceId: wsId,
      topicId,
      topicSubject: topic.value?.subject ?? '',
      workspaceName: workspace.value?.name ?? '',
      messages,
      generatedAt: new Date().toISOString(),
      ...(maxEvents !== undefined ? { maxEvents } : {}),
    })
    // Spine messages own their children as an in-box list, so collapse them in
    // the spatial layout: their descendants become hidden and take no vertical
    // space. The embedded list (expandedLists) is a separate, per-box control.
    for (const n of built.nodes) {
      if (SPINE_KINDS.has(n.kind)) n.ui.collapsed = true
    }
    layoutGraph(built, { nodeHeight: nodeHeightOverride })
    graph.value = built
    triggerRef(graph)

    if (selectedNodeId.value) {
      const exists = built.nodes.some(n => n.id === selectedNodeId.value)
      if (!exists) selectedNodeId.value = null
    }
  } catch (err) {
    loadError.value = err.message || 'Failed to load graph'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  document.addEventListener('keydown', onKeydownGlobal)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydownGlobal)
})

function onKeydownGlobal(e) {
  if (document.activeElement && document.activeElement.tagName === 'INPUT') return
  if (e.key === 'Escape') clearSelection()
}

watch(() => route.query.node, (id) => {
  if (id !== selectedNodeId.value) selectedNodeId.value = id ?? null
})
</script>

<style scoped>
.graph-layout {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 80px);
}
.breadcrumb {
  font-size: 0.9em;
  color: #64748b;
  margin-bottom: 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.breadcrumb-toggle { margin-left: auto; }
.graph-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.toolbar-btn {
  font-size: 0.78em;
  padding: 3px 10px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  cursor: pointer;
  color: #475569;
  line-height: 1.4;
}
.toolbar-btn:hover { background: #e2e8f0; color: #1e293b; }
.minimap-toggle { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.minimap-toggle input { cursor: pointer; }
.diag-chip {
  font-size: 0.75em;
  padding: 2px 10px;
  background: #fef3c7;
  color: #92400e;
  border-radius: 10px;
  border: 1px solid #fde68a;
  cursor: help;
  white-space: nowrap;
}
.graph-status {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 0.9em;
}
.graph-error { color: #dc2626; }
.graph-canvas {
  flex: 1;
  min-height: 0;
  outline: none;
}
.graph-flow {
  width: 100%;
  height: 100%;
}
</style>
