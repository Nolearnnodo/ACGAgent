<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  createTextAnnotator,
  type TextAnnotation,
  type TextAnnotator,
} from '@recogito/text-annotator'
import '@recogito/text-annotator/text-annotator.css'

import type { EvidenceSpan } from '../../api/extractionAnnotations'
import type { AnnotationHighlight } from '../../utils/extractionAnnotation'

const props = withDefaults(defineProps<{
  text: string
  highlights: AnnotationHighlight[]
  activeEvidenceId?: string | null
  disabled?: boolean
}>(), {
  activeEvidenceId: null,
  disabled: false,
})

const emit = defineEmits<{
  selectionCreated: [evidence: EvidenceSpan]
  highlightActivated: [evidenceId: string]
}>()

const textElement = ref<HTMLElement | null>(null)
let annotator: TextAnnotator<TextAnnotation, TextAnnotation> | null = null

const colorByKind: Record<AnnotationHighlight['kind'], string> = {
  person: '#2f6fed',
  event: '#3f7280',
  history: '#78639a',
  relation: '#9a702f',
  excluded: '#7d7b73',
  unresolved: '#b56b2d',
}

function utf16ToCodePointOffset(text: string, offset: number) {
  return Array.from(text.slice(0, offset)).length
}

function toAnnotation(highlight: AnnotationHighlight): TextAnnotation {
  return {
    id: highlight.id,
    target: {
      annotation: highlight.id,
      selector: [{
        quote: highlight.evidence.quote,
        start: highlight.evidence.start_utf16,
        end: highlight.evidence.end_utf16,
      }],
    },
    bodies: [{
      id: `${highlight.id}-kind`,
      annotation: highlight.id,
      purpose: 'tagging',
      value: highlight.kind,
    }],
  }
}

function syncAnnotations() {
  if (!annotator) return
  annotator.setAnnotations(props.highlights.map(toAnnotation), true)
  if (props.activeEvidenceId) annotator.setSelected(props.activeEvidenceId)
}

function createAnnotator() {
  if (!textElement.value) return
  annotator?.destroy()
  annotator = createTextAnnotator<TextAnnotation, TextAnnotation>(textElement.value, {
    allowModifierSelect: true,
    annotatingEnabled: !props.disabled,
    renderer: 'SPANS',
    selectionMode: 'shortest',
    style: (annotation, state) => {
      const kind = annotation.bodies[0]?.value as AnnotationHighlight['kind'] | undefined
      const color = kind ? colorByKind[kind] : colorByKind.person
      return {
        fill: color,
        fillOpacity: state.selected ? 0.34 : 0.2,
        underlineColor: color,
        underlineThickness: state.selected ? 2 : 1,
        underlineOffset: 2,
      }
    },
  })
  annotator.on('createAnnotation', (annotation) => {
    const selector = annotation.target.selector[0]
    if (!selector || selector.end <= selector.start || !selector.quote.trim()) {
      annotator?.removeAnnotation(annotation.id)
      return
    }
    const evidence: EvidenceSpan = {
      id: typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? `evidence-${crypto.randomUUID()}`
        : `evidence-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      source: 'context',
      quote: selector.quote,
      start: utf16ToCodePointOffset(props.text, selector.start),
      end: utf16ToCodePointOffset(props.text, selector.end),
      start_utf16: selector.start,
      end_utf16: selector.end,
    }
    annotator?.removeAnnotation(annotation.id)
    emit('selectionCreated', evidence)
  })
  annotator.on('clickAnnotation', (annotation) => {
    emit('highlightActivated', annotation.id)
  })
  syncAnnotations()
}

function focusEvidence(evidenceId: string) {
  if (!annotator) return
  annotator.setSelected(evidenceId)
  annotator.scrollIntoView(evidenceId, textElement.value?.parentElement ?? undefined)
}

defineExpose({ focusEvidence })

watch(() => props.text, async () => {
  await nextTick()
  createAnnotator()
})
watch(() => props.highlights, syncAnnotations, { deep: true })
watch(() => props.activeEvidenceId, (id) => {
  if (!annotator) return
  annotator.setSelected(id ?? undefined)
})
watch(() => props.disabled, (disabled) => annotator?.setAnnotatingEnabled(!disabled))

onMounted(createAnnotator)
onBeforeUnmount(() => annotator?.destroy())
</script>

<template>
  <div
    ref="textElement"
    class="annotation-text"
    :class="{ 'annotation-text--disabled': disabled }"
  >{{ text }}</div>
</template>

<style scoped>
.annotation-text {
  min-height: 100%;
  padding: 34px clamp(24px, 4vw, 64px) 120px;
  color: #292722;
  font-family: 'Noto Serif SC', 'Songti SC', SimSun, serif;
  font-size: 19px;
  line-height: 2.15;
  letter-spacing: 0.045em;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  cursor: text;
  user-select: text;
}

.annotation-text--disabled {
  cursor: default;
}

.annotation-text :deep(.r6o-annotation) {
  border-radius: 2px;
  transition: background-color 140ms ease, box-shadow 140ms ease;
}

@media (max-width: 900px) {
  .annotation-text {
    padding: 24px 20px 96px;
    font-size: 17px;
  }
}
</style>
