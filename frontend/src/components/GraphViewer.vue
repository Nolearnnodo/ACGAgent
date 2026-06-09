<script setup lang="ts">
import { NVL } from '@neo4j-nvl/base'
import type { Node, Relationship, NvlOptions } from '@neo4j-nvl/base'
import { DragNodeInteraction, PanInteraction, ZoomInteraction, HoverInteraction } from '@neo4j-nvl/interaction-handlers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

export interface GraphNode {
  id: string
  label: string
  type: string
  properties: Record<string, unknown>
}

export interface GraphEdge {
  source: string
  target: string
  label: string
  properties: Record<string, unknown>
}

export interface GraphElements {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

interface Props {
  elements: GraphElements
  height?: string
  interactive?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  height: '400px',
  interactive: true,
})

const nvlContainer = ref<HTMLDivElement | null>(null)
const isFullscreen = ref(false)
let nvl: NVL | null = null
let interactions: { destroy?: () => void }[] = []

const tooltipContent = ref('')
const tooltipVisible = ref(false)
const tooltipX = ref(0)
const tooltipY = ref(0)

const colorMap: Record<string, string> = {
  Person_Nodes: '#4A90D9',
  Passage_Info: '#E8A838',
  Life_Events: '#50C878',
  Time: '#9B59B6',
  Location: '#2ECC71',
  Official_title: '#E74C3C',
  Historical_Events: '#F39C12',
}

let nodeDataMap: Map<string, GraphNode> = new Map()
let edgeDataMap: Map<string, GraphEdge> = new Map()

const RELATION_CODE_LABELS: Record<string, string> = {
  F: '父',
  M: '母',
  S: '子',
  D: '女',
  W: '妻',
  H: '夫',
  B: '兄弟',
  Z: '妾',
  O: '臣属/部下',
  T: '师生',
}

function isReviewFocusNode(node: GraphNode) {
  return node.properties.review_focus === true
}

function buildNvlNodes(elements: GraphElements): Node[] {
  const edgeIndex = new Map<string, string[]>()
  for (const e of elements.edges) {
    if (!edgeIndex.has(e.source)) edgeIndex.set(e.source, [])
    edgeIndex.get(e.source)!.push(e.target)
    if (!edgeIndex.has(e.target)) edgeIndex.set(e.target, [])
    edgeIndex.get(e.target)!.push(e.source)
  }

  const placed = new Map<string, { x: number; y: number }>()
  const result: Node[] = []

  const mainPerson = elements.nodes.find(
    (n) => n.type === 'Person_Nodes' && !n.id.startsWith('Person_Nodes_name_'),
  )

  if (mainPerson) {
    placed.set(mainPerson.id, { x: 0, y: 0 })
  }

  const layerOrder = [
    'Life_Events', 'Historical_Events',
    'Person_Nodes',
    'Time', 'Location', 'Official_title',
    'Passage_Info',
  ]

  let layerRadius = 180
  const scale = Math.max(1, Math.sqrt(elements.nodes.length / 25))

  for (const type of layerOrder) {
    const layerNodes = elements.nodes.filter((n) => n.type === type && !placed.has(n.id))
    if (!layerNodes.length) continue

    const r = layerRadius * scale
    const angleStep = (2 * Math.PI) / Math.max(layerNodes.length, 1)

    for (let i = 0; i < layerNodes.length; i++) {
      const n = layerNodes[i]
      const neighbors = edgeIndex.get(n.id) || []
      const placedNeighbors = neighbors.map((id) => placed.get(id)).filter(Boolean) as { x: number; y: number }[]

      let cx = 0, cy = 0
      if (placedNeighbors.length) {
        cx = placedNeighbors.reduce((s, p) => s + p.x, 0) / placedNeighbors.length
        cy = placedNeighbors.reduce((s, p) => s + p.y, 0) / placedNeighbors.length
      }

      const baseAngle = Math.atan2(cy, cx || 0.001)
      const spread = placedNeighbors.length ? 0.6 : angleStep
      const angle = baseAngle + (i - layerNodes.length / 2) * spread + (Math.random() - 0.5) * 0.2
      const jitter = (Math.random() - 0.5) * 20 * scale

      const x = cx + Math.cos(angle) * (r + jitter)
      const y = cy + Math.sin(angle) * (r + jitter)
      placed.set(n.id, { x, y })
    }

    layerRadius += 100 + layerNodes.length * 8
  }

  for (const n of elements.nodes) {
    if (placed.has(n.id) && n !== mainPerson) continue
  }

  for (const n of elements.nodes) {
    const pos = placed.get(n.id) || { x: (Math.random() - 0.5) * 400, y: (Math.random() - 0.5) * 400 }
    const isFocus = isReviewFocusNode(n)
    const labelEl = document.createElement('div')
    labelEl.textContent = n.label
    labelEl.style.cssText = `
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      font-family: "Noto Serif SC", "Source Han Serif SC", serif;
      max-width: 52px;
      font-size: ${isFocus ? '15px' : '13px'};
      font-weight: ${isFocus ? '700' : '400'};
      color: #fff;
      text-align: center;
      line-height: 1;
      pointer-events: none;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-shadow: 0 1px 2px rgba(0,0,0,0.4);
    `
    result.push({
      id: n.id,
      html: labelEl,
      color: isFocus ? '#DC2626' : colorMap[n.type] || '#95A5A6',
      size: 30,
      x: pos.x,
      y: pos.y,
    })
  }

  return result
}

function buildNvlRelationships(elements: GraphElements): Relationship[] {
  const nodeIds = new Set(elements.nodes.map((n) => n.id))
  return elements.edges
    .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    .map((e) => ({
      id: `${e.source}__${e.target}__${e.label}`,
      from: e.source,
      to: e.target,
      caption: e.label,
      color: '#8a97b3',
      width: 2,
    }))
}

function initNvl() {
  if (!nvlContainer.value) return

  nodeDataMap = new Map(props.elements.nodes.map((n) => [n.id, n]))
  edgeDataMap = new Map(
    props.elements.edges.map((e) => [`${e.source}__${e.target}__${e.label}`, e]),
  )

  const nodes = buildNvlNodes(props.elements)
  const relationships = buildNvlRelationships(props.elements)
  const focusNodeIds = props.elements.nodes.filter(isReviewFocusNode).map((n) => n.id)

  const options: NvlOptions = {
    disableTelemetry: true,
    layout: 'd3Force' as never,
    initialZoom: 1,
    minZoom: 0.05,
    maxZoom: 8,
    renderer: 'canvas',
  }

  nvl = new NVL(nvlContainer.value, nodes, relationships, options, {
    onLayoutDone: () => {
      nvl?.fit(focusNodeIds.length >= 2 ? focusNodeIds : [], { animated: true } as never)
    },
  })

  if (props.interactive) {
    const drag = new DragNodeInteraction(nvl)
    const pan = new PanInteraction(nvl)
    const zoom = new ZoomInteraction(nvl)
    const hover = new HoverInteraction(nvl)

    hover.updateCallback('onHover', (...args: unknown[]) => {
      const [element, _hitElements, event] = args as [Node | Relationship | null, unknown, MouseEvent]
      if (!element) {
        tooltipVisible.value = false
        return
      }

      // 节点 hover
      if (nodeDataMap.has(element.id)) {
        const graphNode = nodeDataMap.get(element.id)!
        const entries = [
          `<div><strong>类型:</strong> ${graphNode.type}</div>`,
          `<div><strong>名称:</strong> ${graphNode.label}</div>`,
          ...Object.entries(graphNode.properties)
            .filter(([k]) => !['id', 'color', 'shape'].includes(k))
            .slice(0, 8)
            .map(([k, v]) => `<div><strong>${k}:</strong> ${v}</div>`),
        ].join('')
        tooltipContent.value = entries
        tooltipX.value = event.clientX + 14
        tooltipY.value = event.clientY - 14
        tooltipVisible.value = true
        return
      }

      // 边 hover
      if (edgeDataMap.has(element.id)) {
        const graphEdge = edgeDataMap.get(element.id)!
        const srcNode = nodeDataMap.get(graphEdge.source)
        const tgtNode = nodeDataMap.get(graphEdge.target)
        const codes = (graphEdge.properties.codes as string[]) || []
        const codeLabels = codes.map((c) => {
          const label = RELATION_CODE_LABELS[c]
          return label ? `${c}(${label})` : c
        })
        const entries = [
          `<div><strong>${srcNode?.label || '?'}</strong> → <strong>${tgtNode?.label || '?'}</strong></div>`,
          `<div><strong>关系:</strong> ${codeLabels.join(', ') || graphEdge.label}</div>`,
        ]
        const note = graphEdge.properties.note as string | undefined
        if (note) {
          entries.push(`<div><strong>备注:</strong> ${note}</div>`)
        }
        const otherProps = Object.entries(graphEdge.properties)
          .filter(([k]) => !['codes', 'note'].includes(k))
        for (const [k, v] of otherProps.slice(0, 4)) {
          if (v != null) entries.push(`<div><strong>${k}:</strong> ${v}</div>`)
        }
        tooltipContent.value = entries.join('')
        tooltipX.value = event.clientX + 14
        tooltipY.value = event.clientY - 14
        tooltipVisible.value = true
        return
      }

      tooltipVisible.value = false
    })

    interactions = [drag, pan, zoom, hover] as { destroy?: () => void }[]
  }
}

function destroyNvl() {
  tooltipVisible.value = false
  for (const handler of interactions) {
    handler.destroy?.()
  }
  interactions = []
  if (nvl) {
    nvl.destroy()
    nvl = null
  }
}

watch(() => props.elements, () => {
  destroyNvl()
  setTimeout(() => {
    initNvl()
  }, 0)
}, { deep: true })

onMounted(() => {
  initNvl()
})

onBeforeUnmount(() => {
  destroyNvl()
})

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
  setTimeout(() => {
    nvl?.fit([], { animated: true } as never)
  }, 100)
}
</script>

<template>
  <div class="graph-viewer">
    <div class="graph-viewer__toolbar">
      <button class="graph-viewer__fullscreen-btn" type="button" @click="toggleFullscreen">
        {{ isFullscreen ? '退出全屏' : '全屏' }}
      </button>
    </div>
    <div
      ref="nvlContainer"
      class="graph-viewer__container"
      :style="{ height: isFullscreen ? '80vh' : props.height }"
    />
    <div
      v-if="tooltipVisible"
      class="graph-viewer__tooltip"
      :style="{ left: tooltipX + 'px', top: tooltipY + 'px' }"
      v-html="tooltipContent"
    />
  </div>
</template>

<style scoped>
.graph-viewer {
  position: relative;
  width: 100%;
  border: 1px solid #e4ebf7;
  border-radius: 12px;
  background: #fafcff;
  overflow: hidden;
}

.graph-viewer__toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 6px 10px;
  border-bottom: 1px solid #e4ebf7;
  background: #f5f8ff;
}

.graph-viewer__fullscreen-btn {
  border: 1px solid #dce6f5;
  background: #fff;
  color: #506287;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.graph-viewer__fullscreen-btn:hover {
  background: #eef3ff;
  color: #2f6fed;
}

.graph-viewer__container {
  width: 100%;
  transition: height 0.3s ease;
}

.graph-viewer__tooltip {
  position: fixed;
  z-index: 9999;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  max-width: 280px;
  pointer-events: none;
  line-height: 1.6;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}

.graph-viewer__tooltip :deep(strong) {
  color: #93c5fd;
}
</style>
