<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  type AIAnnotationGenerationSummary,
  type AIAnnotationReview,
  type AIAnnotationReviewAdditionData,
  type AIAnnotationReviewAdditionType,
  type AIAnnotationReviewItem,
  type AIAnnotationReviewOmission,
  listAIAnnotationGenerations,
  saveAIAnnotationReview,
  startAIAnnotationReview,
  submitAIAnnotationReview,
} from '../api/aiAnnotations'
import { getApiErrorMessage } from '../api/client'
import {
  EVENT_TYPES,
  type AnnotationDictionaries,
  type EvidenceSpan,
  type EventType,
  fetchAnnotationDictionaries,
} from '../api/extractionAnnotations'
import AnnotationTextCanvas from '../components/annotation/AnnotationTextCanvas.vue'
import AppLayout from '../layouts/AppLayout.vue'
import {
  type AnnotationHighlight,
  createHistoricalEvent,
  createLifeEvent,
  createPerson,
  createPersonRelation,
  evidenceGranularityIssue,
} from '../utils/extractionAnnotation'

type ReviewStepId = 'material' | 'persons' | 'relations' | 'checks'
const reviewSteps: Array<{ id: ReviewStepId; label: string }> = [
  { id: 'material', label: '材料' },
  { id: 'persons', label: '人物基本信息' },
  { id: 'relations', label: '人物关系' },
  { id: 'checks', label: '差缺补漏' },
]

const route = useRoute()
const jobId = computed(() => Number(route.params.jobId))
const review = ref<AIAnnotationReview | null>(null)
const currentGeneration = ref<AIAnnotationGenerationSummary | null>(null)
const siblingGenerations = ref<AIAnnotationGenerationSummary[]>([])
const loading = ref(true)
const saving = ref(false)
const submitting = ref(false)
const dirty = ref(false)
const feedback = ref('')
const activeItemId = ref<string | null>(null)
const activeOmissionId = ref<string | null>(null)
const activeEvidenceId = ref<string | null>(null)
const pendingEvidence = ref<EvidenceSpan | null>(null)
const activeGroupKey = ref('')
const activeStep = ref<ReviewStepId>('material')
const itemSearch = ref('')
const textCanvas = ref<InstanceType<typeof AnnotationTextCanvas> | null>(null)
const dictionaries = ref<AnnotationDictionaries>({
  eras: [],
  historical_events: [],
  relation_codes: [],
})
const dictionariesLoading = ref(false)
const dictionariesLoaded = ref(false)
const dictionaryFeedback = ref('')
const correctionEvidenceDrafts = ref<Record<string, string>>({})
const correctionEvidenceFeedback = ref<Record<string, string>>({})

interface ReviewSection {
  key: string
  title: string
  items: AIAnnotationReviewItem[]
  reviewed: number
  total: number
}

interface ReviewGroup {
  key: string
  title: string
  category: string
  sections: ReviewSection[]
  reviewed: number
  total: number
}

interface CorrectionOption {
  value: string
  label: string
}

interface EvidenceTargetOption {
  value: string
  targetType: string
  targetKey: string
  label: string
  kind: 'entity' | 'fact'
}

const additionTypeOptions: Array<{ value: AIAnnotationReviewAdditionType; label: string }> = [
  { value: 'person', label: '人物' },
  { value: 'life_event', label: '生平事件' },
  { value: 'historical_event', label: '历史事件' },
  { value: 'person_relation', label: '人物关系' },
  { value: 'evidence', label: '已有记录的证据' },
  { value: 'excluded_mention', label: '排除称谓' },
  { value: 'unresolved_item', label: '暂不能判断项' },
  { value: 'schema_conflict', label: '工具限制' },
  { value: 'note', label: '其他缺漏说明' },
]
const additionTypeToCategory: Record<AIAnnotationReviewAdditionType, string> = {
  note: '其他缺漏',
  person: '人物缺漏',
  life_event: '事件缺漏',
  historical_event: '历史事件缺漏',
  person_relation: '关系缺漏',
  evidence: '证据缺漏',
  excluded_mention: '其他缺漏',
  unresolved_item: '其他缺漏',
  schema_conflict: '结构增补',
}
const omissionCategoryOptions = [
  '人物缺漏', '等级缺漏', '属性缺漏', '事件缺漏', '时间缺漏', '地点缺漏',
  '官职缺漏', '历史事件缺漏', '关系缺漏', '证据缺漏', '其他缺漏', '结构增补',
]
const relationCategoryOptions: CorrectionOption[] = [
  '亲属', '教授与传承', '隶属与任用', '合作与援助', '交游与往来', '对抗与冲突', '其他',
].map((value) => ({ value, label: value }))
const coverageCategories: Record<string, string[]> = {
  'coverage:persons': ['人物缺漏', '等级缺漏', '属性缺漏'],
  'coverage:events': ['事件缺漏', '时间缺漏', '地点缺漏', '官职缺漏'],
  'coverage:history': ['历史事件缺漏'],
  'coverage:relations': ['关系缺漏'],
  'coverage:exceptions': ['其他缺漏', '结构增补'],
}
const factStateValueOptions: CorrectionOption[] = [
  { value: 'confirmed', label: '确定' },
  { value: 'uncertain', label: '暂不能判断' },
  { value: 'unsupported', label: '工具暂不支持' },
]
const fieldStateValueOptions: CorrectionOption[] = [
  { value: 'present', label: '原文明示' },
  { value: 'not_mentioned', label: '原文未提及' },
  { value: 'not_applicable', label: '本项不适用' },
  { value: 'uncertain', label: '暂不能判断' },
  { value: 'unsupported', label: '工具暂不支持' },
]

const sourceTypeOptions: CorrectionOption[] = [
  { value: '墓志铭或塔铭类', label: '墓志铭或塔铭类' },
  { value: '一般历史文献', label: '一般历史文献' },
  { value: '暂不能判断', label: '暂不能判断' },
]
const materialStatusOptions: CorrectionOption[] = [
  { value: '标题与正文完整匹配', label: '标题与正文完整匹配' },
  { value: '存在缺页、错配或转换问题', label: '存在缺页、错配或转换问题' },
  { value: '暂不能判断', label: '暂不能判断' },
]
const nullableBooleanOptions: CorrectionOption[] = ['是', '否', '未判断']
  .map((value) => ({ value, label: value }))
const booleanOptions: CorrectionOption[] = ['是', '否']
  .map((value) => ({ value, label: value }))
const levelOptions: CorrectionOption[] = ['1 级', '2 级', '3 级']
  .map((value) => ({ value, label: value }))
const eventTypeOptions: CorrectionOption[] = ['出生', '籍贯', '死亡', '埋葬', '任职']
  .map((value) => ({ value, label: value }))
const checkStateOptions: CorrectionOption[] = [
  '尚未检查', '已检查且有事实', '原文未提及', '暂不能判断', '工具暂不支持',
].map((value) => ({ value, label: value }))
const factStateOptions: CorrectionOption[] = ['确定', '暂不能判断', '工具暂不支持']
  .map((value) => ({ value, label: value }))
const fieldStateOptions: CorrectionOption[] = [
  '原文明示', '原文未提及', '本项不适用', '暂不能判断', '工具暂不支持',
].map((value) => ({ value, label: value }))

let saveTimer: number | null = null
let saveQueued = false
let activeSavePromise: Promise<void> | null = null

const isReadOnly = computed(() => review.value?.editable === false || submitting.value)

function reviewStepOfItem(item: AIAnnotationReviewItem): ReviewStepId {
  if (item.id.startsWith('passage:')) return 'material'
  if (item.id.startsWith('history:') || item.id.startsWith('relation:')) return 'relations'
  if (item.id.startsWith('coverage:') || item.id.startsWith('aux:')) return 'checks'
  return 'persons'
}

const coverageItems = computed(() => (review.value?.items ?? []).filter(
  (item) => item.id.startsWith('coverage:'),
))

function hasRecordedCoverageGap(item: AIAnnotationReviewItem) {
  const categories = coverageCategories[item.id] ?? []
  return (review.value?.omissions ?? []).some((omission) => (
    categories.includes(omission.category)
    && Boolean(omission.target_path.trim())
    && Boolean(omission.description.trim())
    && Boolean(omission.evidence.length || omission.evidence_note.trim())
  ))
}

const liveSummary = computed(() => {
  const items = (review.value?.items ?? []).filter((item) => !item.id.startsWith('coverage:'))
  const correct = items.filter((item) => item.verdict === 'correct').length
  const incorrect = items.filter((item) => item.verdict === 'incorrect').length
  const unreviewed = items.length - correct - incorrect
  const omissions = review.value?.omissions.length ?? 0
  const denominator = correct + incorrect
  return {
    total: items.length,
    reviewed: correct + incorrect,
    correct,
    incorrect,
    unreviewed,
    omissions,
    accuracy: denominator ? correct / denominator : 0,
  }
})

const progressPercent = computed(() => (
  review.value?.items.length
    ? Math.round(review.value.items.filter((item) => item.verdict !== 'unreviewed').length / review.value.items.length * 100)
    : 0
))

const stepStatuses = computed(() => reviewSteps.map((step) => {
  const items = (review.value?.items ?? []).filter((item) => reviewStepOfItem(item) === step.id)
  const reviewed = items.filter((item) => item.verdict !== 'unreviewed').length
  const missingCorrections = items.some((item) => (
    item.verdict === 'incorrect'
    && !item.id.startsWith('coverage:')
    && !item.remove_item
    && !item.correction.trim()
  ))
  const invalidRelationSummary = items.some((item) => (
    item.id.startsWith('relation:')
    && item.path.endsWith('.relation_summary')
    && item.verdict === 'incorrect'
    && item.correction.trim().length > 10
  ))
  const missingEventDetails = items.some((item) => (
    eventCheckRequiresEvidence(item) && !(item.correction_evidence ?? []).length
  ) || Boolean(eventCorrectionProblem(item)))
  const missingCoverageRecords = step.id === 'checks' && coverageItems.value.some((item) => (
    item.verdict === 'incorrect' && !hasRecordedCoverageGap(item)
  ))
  const conflictingCoverageRecords = step.id === 'checks' && coverageItems.value.some((item) => (
    item.verdict === 'correct' && hasRecordedCoverageGap(item)
  ))
  const incompleteOmissions = step.id === 'checks' && (review.value?.omissions ?? [])
    .some((omission) => Boolean(additionProblem(omission)))
  return {
    ...step,
    reviewed,
    total: items.length,
    complete: reviewed === items.length && !missingCorrections && !invalidRelationSummary && !missingEventDetails
      && !missingCoverageRecords && !conflictingCoverageRecords && !incompleteOmissions,
  }
}))
const activeStepStatus = computed(() => stepStatuses.value.find((step) => step.id === activeStep.value))

function selectStep(stepId: ReviewStepId) {
  activeStep.value = stepId
  itemSearch.value = ''
  const first = (review.value?.items ?? []).find((item) => (
    reviewStepOfItem(item) === stepId && !item.id.startsWith('coverage:')
  ))
  activeGroupKey.value = first?.group_key || ''
  activeItemId.value = first?.id ?? null
}

function groupItems(items: AIAnnotationReviewItem[]): ReviewGroup[] {
  const groups: ReviewGroup[] = []
  const groupMap = new Map<string, ReviewGroup>()
  for (const item of items) {
    const key = item.group_key || item.category || 'other'
    let group = groupMap.get(key)
    if (!group) {
      group = {
        key,
        title: item.group_title || item.category,
        category: item.category,
        sections: [],
        reviewed: 0,
        total: 0,
      }
      groupMap.set(key, group)
      groups.push(group)
    }
    const sectionKey = item.section_key || key
    let section = group.sections.find((entry) => entry.key === sectionKey)
    if (!section) {
      section = {
        key: sectionKey,
        title: item.section_title || item.group_title || item.category,
        items: [],
        reviewed: 0,
        total: 0,
      }
      group.sections.push(section)
    }
    section.items.push(item)
    section.total += 1
    group.total += 1
    if (item.verdict !== 'unreviewed') {
      section.reviewed += 1
      group.reviewed += 1
    }
  }
  return groups
}

const allGroups = computed(() => groupItems((review.value?.items ?? []).filter((item) => (
  reviewStepOfItem(item) === activeStep.value && !item.id.startsWith('coverage:')
))))

const filteredItems = computed(() => {
  const keyword = itemSearch.value.trim().toLowerCase()
  return (review.value?.items ?? []).filter((item) => (
    reviewStepOfItem(item) === activeStep.value
    && !item.id.startsWith('coverage:')
    && (
    !keyword || [
      item.group_title,
      item.section_title,
      item.field_label,
      item.ai_value,
      item.title,
      item.question,
      item.detail,
      item.path,
      item.correction,
      ...item.evidence.map((evidence) => evidence.quote),
    ].join(' ').toLowerCase().includes(keyword))
  ))
})

const visibleGroups = computed(() => {
  const groups = groupItems(filteredItems.value)
  if (itemSearch.value.trim()) return groups
  const selected = groups.find((group) => group.key === activeGroupKey.value)
  return selected ? [selected] : groups.slice(0, 1)
})

const activeItem = computed(() => (
  review.value?.items.find((item) => item.id === activeItemId.value) ?? null
))

const activeOmission = computed(() => (
  review.value?.omissions.find((item) => item.id === activeOmissionId.value) ?? null
))

const reviewPersonOptions = computed<CorrectionOption[]>(() => (
  (() => {
    const options = new Map<string, CorrectionOption>()
    for (const item of review.value?.items ?? []) {
      if (!item.id.startsWith('person:') || !item.id.endsWith(':name_surface') || item.remove_item) continue
      const personKey = item.id.slice('person:'.length, -':name_surface'.length)
      const correctedName = item.verdict === 'incorrect' ? item.correction.trim() : ''
      const personName = correctedName || item.ai_value || personKey
      options.set(personKey, { value: personKey, label: `${personName}（${personKey}）` })
    }
    for (const omission of review.value?.omissions ?? []) {
      if (omission.addition_type !== 'person') continue
      const personKey = String(omission.data.key || '')
      if (!personKey) continue
      const personName = String(omission.data.name_surface || '未命名人物')
      options.set(personKey, { value: personKey, label: `${personName}（新增）` })
    }
    return [...options.values()]
  })()
))

const reviewFactPersonOptions = computed(() => reviewPersonOptions.value.filter((option) => {
  const levelItem = (review.value?.items ?? []).find(
    (item) => item.id === `person:${option.value}:level`,
  )
  if (levelItem) {
    const level = levelItem.verdict === 'incorrect' && levelItem.correction.trim()
      ? Number(levelItem.correction.replace(/\D/g, ''))
      : Number(levelItem.ai_value.replace(/\D/g, ''))
    return level !== 3
  }
  const addition = review.value?.omissions.find(
    (item) => item.addition_type === 'person' && item.data.key === option.value,
  )
  return addition?.data.level !== 3
}))

const evidenceTargets = computed<EvidenceTargetOption[]>(() => {
  const targets = new Map<string, EvidenceTargetOption>()
  const add = (
    targetType: string,
    targetKey: string,
    label: string,
    kind: 'entity' | 'fact',
  ) => {
    if (!targetKey) return
    const value = `${targetType}:${targetKey}`
    if (!targets.has(value)) targets.set(value, { value, targetType, targetKey, label, kind })
  }
  const items = review.value?.items ?? []
  for (const item of items) {
    const parts = item.id.split(':')
    if (parts[0] === 'person' && parts.length >= 3) {
      const removed = items.find((candidate) => candidate.id === `person:${parts[1]}:name_surface`)?.remove_item
      if (!removed) add('person', parts[1], item.group_title, 'entity')
    } else if (parts[0] === 'event' && parts.length >= 4) {
      const removed = items.some((candidate) => (
        candidate.id.startsWith(`event:${parts[1]}:${parts[2]}:`)
        && !candidate.id.includes(':evidence')
        && candidate.remove_item
      ))
      if (!removed) add('life_event', parts[2], item.section_title, 'fact')
    } else if (parts[0] === 'history' && parts.length >= 4) {
      const removed = items.some((candidate) => (
        candidate.id.startsWith(`history:${parts[1]}:${parts[2]}:`)
        && !candidate.id.includes(':evidence')
        && candidate.remove_item
      ))
      if (!removed) add('historical_event', parts[2], item.section_title, 'fact')
    } else if (parts[0] === 'relation' && parts.length >= 3) {
      const removed = items.some((candidate) => (
        candidate.id.startsWith(`relation:${parts[1]}:`)
        && !candidate.id.includes(':evidence')
        && candidate.remove_item
      ))
      if (!removed) add('person_relation', parts[1], item.section_title, 'fact')
    } else if (parts[0] === 'aux' && parts.length >= 4) {
      const typeMap: Record<string, string> = {
        excluded_mentions: 'excluded_mention',
        unresolved_items: 'unresolved_item',
        schema_conflicts: 'schema_conflict',
      }
      const targetType = typeMap[parts[1]]
      if (targetType) {
        const removed = items.some((candidate) => (
          candidate.id.startsWith(`aux:${parts[1]}:${parts[2]}:`)
          && !candidate.id.includes(':evidence')
          && candidate.remove_item
        ))
        if (!removed) add(
          targetType,
          parts[2],
          item.section_title,
          targetType === 'excluded_mention' ? 'entity' : 'fact',
        )
      }
    }
  }
  for (const omission of review.value?.omissions ?? []) {
    const key = String(omission.data.key || '')
    if (!key) continue
    if (omission.addition_type === 'person') {
      add('person', key, `新增人物 · ${omission.data.name_surface || '未命名'}`, 'entity')
    } else if (omission.addition_type === 'life_event') {
      add('life_event', key, `新增${omission.data.event_type || ''}事件`, 'fact')
    } else if (omission.addition_type === 'historical_event') {
      add('historical_event', key, `新增历史事件 · ${omission.data.event_name || '未命名'}`, 'fact')
    } else if (omission.addition_type === 'person_relation') {
      add('person_relation', key, '新增人物关系', 'fact')
    } else if (['excluded_mention', 'unresolved_item', 'schema_conflict'].includes(omission.addition_type)) {
      add(
        omission.addition_type,
        key,
        `新增${additionTypeOptions.find((option) => option.value === omission.addition_type)?.label || '记录'}`,
        omission.addition_type === 'excluded_mention' ? 'entity' : 'fact',
      )
    }
  }
  return [...targets.values()]
})

const pendingEvidenceIssue = computed(() => {
  const mentionCorrection = Boolean(
    activeItem.value
    && (activeItem.value.path.includes('.mentions.') || activeItem.value.field_label.startsWith('指代')),
  )
  return evidenceGranularityIssue(pendingEvidence.value, mentionCorrection ? 'entity' : 'fact')
})
const pendingEntityIssue = computed(() => evidenceGranularityIssue(pendingEvidence.value, 'entity'))
const pendingFactIssue = computed(() => evidenceGranularityIssue(pendingEvidence.value, 'fact'))
const pendingActiveOmissionIssue = computed(() => (
  activeOmission.value
    ? evidenceGranularityIssue(pendingEvidence.value, additionEvidenceKind(activeOmission.value))
    : ''
))

const relatedStandardIds = computed(() => new Set(activeItem.value?.standard_ids ?? []))

const highlights = computed<AnnotationHighlight[]>(() => {
  const values = new Map<string, AnnotationHighlight>()
  const kindByCategory: Record<string, AnnotationHighlight['kind']> = {
    人物: 'person',
    证据: 'person',
    事件: 'event',
    历史事件: 'history',
    关系: 'relation',
    异常: 'unresolved',
  }
  for (const item of review.value?.items ?? []) {
    for (const evidence of item.evidence) {
      if (values.has(evidence.id)) continue
      values.set(evidence.id, {
        id: evidence.id,
        evidence,
        kind: kindByCategory[item.category] ?? 'event',
        label: item.title,
        objectKey: item.id,
      })
    }
    for (const evidence of item.correction_evidence ?? []) {
      if (values.has(evidence.id)) continue
      values.set(evidence.id, {
        id: evidence.id,
        evidence,
        kind: 'unresolved',
        label: `正确原文 · ${item.field_label || item.title}`,
        objectKey: item.id,
      })
    }
  }
  for (const omission of review.value?.omissions ?? []) {
    for (const evidence of omission.evidence) {
      if (values.has(evidence.id)) continue
      values.set(evidence.id, {
        id: evidence.id,
        evidence,
        kind: 'unresolved',
        label: `缺漏 · ${omission.description || omission.category}`,
        objectKey: omission.id,
      })
    }
  }
  return [...values.values()]
})

const submissionProblems = computed(() => {
  const problems: string[] = []
  if (liveSummary.value.unreviewed) problems.push(`还有 ${liveSummary.value.unreviewed} 项未判断`)
  const unreviewedCoverage = coverageItems.value.filter((item) => item.verdict === 'unreviewed').length
  if (unreviewedCoverage) problems.push(`还有 ${unreviewedCoverage} 项覆盖检查未完成`)
  const uncoveredGaps = coverageItems.value.filter((item) => (
    item.verdict === 'incorrect' && !hasRecordedCoverageGap(item)
  )).length
  if (uncoveredGaps) problems.push(`${uncoveredGaps} 类缺漏尚未逐条登记`)
  const conflictingCoverage = coverageItems.value.filter((item) => (
    item.verdict === 'correct' && hasRecordedCoverageGap(item)
  )).length
  if (conflictingCoverage) problems.push(`${conflictingCoverage} 项覆盖检查与缺漏记录不一致`)
  const missingCorrections = (review.value?.items ?? []).filter(
    (item) => item.verdict === 'incorrect' && !item.remove_item && !item.correction.trim(),
  ).length
  if (missingCorrections) problems.push(`${missingCorrections} 个错误项未填写正确值`)
  const invalidRelationSummaries = (review.value?.items ?? []).filter((item) => (
    item.id.startsWith('relation:')
    && item.path.endsWith('.relation_summary')
    && item.verdict === 'incorrect'
    && item.correction.trim().length > 10
  )).length
  if (invalidRelationSummaries) problems.push(`${invalidRelationSummaries} 条人物关系具体表现超过 10 字`)
  const missingEventEvidence = (review.value?.items ?? []).filter(
    (item) => eventCheckRequiresEvidence(item) && !(item.correction_evidence ?? []).length,
  ).length
  if (missingEventEvidence) problems.push(`${missingEventEvidence} 个新增事件事实未填写原文证据`)
  const incompleteEventCards = (review.value?.items ?? []).filter(
    (item) => Boolean(eventCorrectionProblem(item)),
  ).length
  if (incompleteEventCards) problems.push(`${incompleteEventCards} 个新增事件事实未填写完整卡片`)
  const incompleteOmissions = (review.value?.omissions ?? [])
    .filter((item) => Boolean(additionProblem(item))).length
  if (incompleteOmissions) problems.push(`${incompleteOmissions} 条缺漏记录未补完整`)
  return problems
})

function markCoverage(item: AIAnnotationReviewItem, hasGap: boolean) {
  if (isReadOnly.value) return
  if (!hasGap && hasRecordedCoverageGap(item)) {
    feedback.value = '右侧已有对应缺漏记录；请先删除或调整该记录，再标记无缺漏。'
    return
  }
  item.verdict = hasGap ? 'incorrect' : 'correct'
  item.correction = hasGap ? '缺漏已在右侧逐条登记' : ''
  item.reason = ''
  item.remove_item = false
  markDirty()
}

function addCoverageGap(item: AIAnnotationReviewItem) {
  if (!review.value || isReadOnly.value) return
  markCoverage(item, true)
  const omission: AIAnnotationReviewOmission = {
    id: uid('omission'),
    category: coverageCategories[item.id]?.[0] ?? '其他缺漏',
    target_path: '',
    description: '',
    correction: '',
    evidence: [],
    evidence_note: '',
    addition_type: 'note',
    parent_key: '',
    data: {},
  }
  review.value.omissions.push(omission)
  activeOmissionId.value = omission.id
  markDirty()
}

function uid(prefix: string) {
  return `${prefix}-${typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function timeLabel(value: string | null) {
  if (!value) return '尚未保存'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function snapshot(value: AIAnnotationReview) {
  return JSON.stringify({
    items: value.items.map((item) => [
      item.id,
      item.verdict,
      item.reason,
      item.correction,
      item.correction_evidence,
      item.correction_data,
      item.remove_item,
    ]),
    omissions: value.omissions,
  })
}

async function loadDictionaries() {
  if (dictionariesLoading.value || dictionariesLoaded.value) return
  dictionariesLoading.value = true
  dictionaryFeedback.value = ''
  try {
    dictionaries.value = await fetchAnnotationDictionaries()
    dictionariesLoaded.value = true
  } catch (error) {
    dictionaryFeedback.value = getApiErrorMessage(
      error,
      '标注字典加载失败，可暂时手工填写正确值。',
    )
  } finally {
    dictionariesLoading.value = false
  }
}

function providerLabel(provider: string | null | undefined) {
  const text = provider?.trim() ?? ''
  return text || '服务端默认'
}

function temperatureLabel(value: number | null | undefined) {
  return value === null || value === undefined ? '温度未设置' : `温度 ${value}`
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

let siblingRequestToken = 0

async function loadSiblingGenerations(taskId: number, generationJobId: number) {
  const token = ++siblingRequestToken
  currentGeneration.value = null
  siblingGenerations.value = []
  try {
    const list = await listAIAnnotationGenerations(taskId)
    if (token !== siblingRequestToken || review.value?.generation_job_id !== generationJobId) return
    const rows = list.groups
      .flatMap((group) => group.generations)
      .filter((row) => row.status === 'success' && !row.archived_at)
    currentGeneration.value = rows.find((row) => row.job_id === generationJobId) ?? null
    siblingGenerations.value = rows.filter((row) => row.job_id !== generationJobId)
  } catch {
    if (token !== siblingRequestToken) return
    currentGeneration.value = null
    siblingGenerations.value = []
  }
}

async function loadReview() {
  loading.value = true
  feedback.value = ''
  review.value = null
  currentGeneration.value = null
  siblingGenerations.value = []
  activeItemId.value = null
  activeOmissionId.value = null
  activeEvidenceId.value = null
  pendingEvidence.value = null
  correctionEvidenceDrafts.value = {}
  correctionEvidenceFeedback.value = {}
  void loadDictionaries()
  try {
    if (!Number.isInteger(jobId.value) || jobId.value <= 0) throw new Error('质检任务编号无效。')
    review.value = await startAIAnnotationReview(jobId.value)
    for (const item of review.value.items) ensureEventCheckCorrectionData(item)
    selectStep(stepStatuses.value.find((step) => !step.complete)?.id ?? 'checks')
    void loadSiblingGenerations(review.value.task_id, review.value.generation_job_id)
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '首轮质检内容加载失败。')
  } finally {
    loading.value = false
  }
}

function markDirty() {
  if (isReadOnly.value) return
  dirty.value = true
  scheduleSave()
}

function scheduleSave() {
  if (saveTimer !== null) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    saveTimer = null
    void saveDraft()
  }, 850)
}

async function saveDraft() {
  if (!review.value || isReadOnly.value || !dirty.value) return
  if (activeSavePromise) {
    saveQueued = true
    await activeSavePromise
    return
  }

  const operation = (async () => {
    saving.value = true
    const current = review.value
    if (!current) return
    const before = snapshot(current)
    dirty.value = false
    try {
      const next = await saveAIAnnotationReview(current.generation_job_id, current)
      if (review.value && snapshot(review.value) === before) {
        review.value = next
      } else {
        if (review.value) {
          review.value.id = next.id
          review.value.updated_at = next.updated_at
        }
        dirty.value = true
        saveQueued = true
      }
      feedback.value = '质检草稿已保存。'
    } catch (error) {
      dirty.value = true
      feedback.value = getApiErrorMessage(error, '质检草稿保存失败。')
    } finally {
      saving.value = false
      if (saveQueued) {
        saveQueued = false
        scheduleSave()
      }
    }
  })()
  activeSavePromise = operation
  try {
    await operation
  } finally {
    if (activeSavePromise === operation) activeSavePromise = null
  }
}

function setVerdict(item: AIAnnotationReviewItem, verdict: 'correct' | 'incorrect') {
  if (isReadOnly.value) return
  activeItemId.value = item.id
  item.verdict = verdict
  item.remove_item = false
  if (verdict === 'correct') {
    item.reason = ''
    item.correction = ''
    item.correction_evidence = []
    item.correction_data = {}
  } else if (!item.correction.trim()) {
    item.correction = binaryAlternativeFor(item) ?? ''
  }
  ensureEventCheckCorrectionData(item)
  markDirty()
}

function markSectionCorrect(section: ReviewSection) {
  if (isReadOnly.value) return
  for (const item of section.items) {
    if (item.verdict !== 'unreviewed') continue
    item.verdict = 'correct'
    item.remove_item = false
    item.reason = ''
    item.correction = ''
    item.correction_evidence = []
    item.correction_data = {}
  }
  markDirty()
}

const SILENT_DETAIL_VALUES = new Set([
  '', '（空）', '原文未提及', '本项不适用', 'not_mentioned', 'not_applicable',
])
const TIME_DETAIL_FIELDS = new Set(['raw', 'era', 'era_year', 'gregorian_year', 'month_text', 'day_text'])
const LOCATION_DETAIL_FIELDS = new Set(['raw', 'dao', 'fu', 'zhou', 'jun', 'xian', 'other'])
const quietExpanded = ref<Record<string, boolean>>({})

function fieldStateHidesDetails(state: unknown) {
  return state === 'not_mentioned' || state === 'not_applicable'
}

function isSilentDetailValue(value: string) {
  return SILENT_DETAIL_VALUES.has(value.trim())
}

function detailAspect(item: AIAnnotationReviewItem): 'time' | 'location' | null {
  const parts = item.path.split('.')
  const leaf = parts.at(-1) ?? ''
  const parent = parts.at(-2) ?? ''
  if (parent === 'time' && TIME_DETAIL_FIELDS.has(leaf)) return 'time'
  if (parent === 'location' && LOCATION_DETAIL_FIELDS.has(leaf)) return 'location'
  return null
}

function effectiveReviewValue(item: AIAnnotationReviewItem) {
  if (item.verdict === 'incorrect' && item.correction.trim()) return item.correction.trim()
  return item.ai_value.trim()
}

function detailCollapsedByState(section: ReviewSection, aspect: 'time' | 'location') {
  const suffix = aspect === 'time' ? '.time.state' : '.location.state'
  const state = section.items.find((item) => item.path.endsWith(suffix))
  if (!state) return false
  const value = effectiveReviewValue(state)
  return value === '原文未提及' || value === '本项不适用' || fieldStateHidesDetails(value)
}

function isCollapsibleDetail(section: ReviewSection, item: AIAnnotationReviewItem) {
  const aspect = detailAspect(item)
  if (!aspect) return false
  return detailCollapsedByState(section, aspect) || isSilentDetailValue(item.ai_value)
}

function collapsedDetailItems(section: ReviewSection, aspect: 'time' | 'location') {
  return section.items.filter((item) => detailAspect(item) === aspect && isCollapsibleDetail(section, item))
}

function quietBundle(section: ReviewSection, item: AIAnnotationReviewItem) {
  const aspect = detailAspect(item)
  if (!aspect) return null
  const items = collapsedDetailItems(section, aspect)
  if (items[0]?.id !== item.id) return null
  return { aspect, items }
}

function revealQuietItem(item: AIAnnotationReviewItem) {
  const aspect = detailAspect(item)
  if (!aspect) return
  const key = `${item.section_key}:${aspect}`
  if (quietExpanded.value[key]) return
  quietExpanded.value = { ...quietExpanded.value, [key]: true }
}

function quietGroupExpanded(section: ReviewSection, aspect: 'time' | 'location') {
  const items = collapsedDetailItems(section, aspect)
  if (itemSearch.value.trim()) return true
  if (items.some((item) => item.verdict === 'incorrect')) return true
  return quietExpanded.value[`${section.key}:${aspect}`] === true
}

function isDetailRowCollapsed(section: ReviewSection, item: AIAnnotationReviewItem) {
  const aspect = detailAspect(item)
  if (!aspect || !isCollapsibleDetail(section, item)) return false
  return !quietGroupExpanded(section, aspect)
}

function toggleQuietGroup(section: ReviewSection, item: AIAnnotationReviewItem) {
  const aspect = detailAspect(item)
  if (!aspect) return
  const key = `${section.key}:${aspect}`
  quietExpanded.value = { ...quietExpanded.value, [key]: !quietExpanded.value[key] }
}

function quietSummaryText(items: AIAnnotationReviewItem[]) {
  const silent = items.filter((item) => isSilentDetailValue(item.ai_value))
  const filled = items.filter((item) => !isSilentDetailValue(item.ai_value))
  const parts: string[] = []
  if (silent.length) {
    parts.push(`未提及 ${silent.map((item) => item.field_label || item.title).join('、')}`)
  }
  if (filled.length) {
    parts.push(filled.map((item) => `${item.field_label || item.title} ${item.ai_value}`).join('、'))
  }
  const text = parts.join(' · ')
  return text.length > 72 ? `${text.slice(0, 72)}…` : text
}

function markCollapsedDetailsCorrect(items: AIAnnotationReviewItem[]) {
  if (isReadOnly.value) return
  for (const item of items) {
    if (item.verdict !== 'unreviewed') continue
    item.verdict = 'correct'
    item.remove_item = false
    item.reason = ''
    item.correction = ''
    item.correction_evidence = []
    item.correction_data = {}
  }
  markDirty()
}

function groupWillBeRemoved(group: ReviewGroup) {
  return group.sections
    .flatMap((section) => section.items)
    .some((item) => item.id.endsWith(':name_surface') && item.remove_item)
}

function sectionWillBeRemoved(section: ReviewSection) {
  return section.items.some((item) => item.value_kind !== 'evidence' && item.remove_item)
}

function isPersonGroup(group: ReviewGroup) {
  return group.key.startsWith('person:')
}

function isRemovableSection(section: ReviewSection) {
  return section.items.some((item) => {
    if (item.value_kind === 'evidence') return false
    return /^(event|history|relation|aux):/.test(item.id)
  })
}

function isRemovableEvidenceItem(item: AIAnnotationReviewItem) {
  return item.value_kind === 'evidence'
    && /\.(?:mentions|evidence)\.\d+$/.test(item.path)
}

function togglePersonRemoval(group: ReviewGroup) {
  if (isReadOnly.value || !isPersonGroup(group)) return
  const removing = !groupWillBeRemoved(group)
  if (removing && !window.confirm(`确认删除“${group.title}”及其全部事件和关联关系吗？`)) return
  const nameItem = group.sections.flatMap((section) => section.items)
    .find((item) => item.id.endsWith(':name_surface'))
  const personName = nameItem
    ? (nameItem.verdict === 'incorrect' && nameItem.correction.trim()
        ? nameItem.correction.trim()
        : nameItem.ai_value)
    : ''
  for (const item of group.sections.flatMap((section) => section.items)) {
    item.remove_item = removing
    item.verdict = removing ? 'incorrect' : 'unreviewed'
    item.reason = removing ? 'AI 误建人物，移除整个人物及其附属记录。' : ''
    item.correction = ''
    item.correction_evidence = []
    item.correction_data = {}
  }
  const personKey = group.key.slice('person:'.length)
  const relationKeys = new Set(
    review.value?.ground_truth_document?.label.person_relations
      .filter((relation) => (
        relation.source_person_key === personKey || relation.target_person_key === personKey
      ))
      .map((relation) => relation.key) ?? [],
  )
  for (const item of review.value?.items ?? []) {
    if (!item.id.startsWith('relation:')) continue
    const relationKey = item.id.split(':')[1]
    const matchesByName = Boolean(
      personName
      && ['人物一', '人物二', '甲（source）', '乙（target）'].includes(item.field_label)
      && item.ai_value === personName,
    )
    if (removing && (relationKeys.has(relationKey) || matchesByName)) {
      const relationItems = review.value?.items.filter(
        (candidate) => candidate.id.startsWith(`relation:${relationKey}:`),
      ) ?? []
      for (const relationItem of relationItems) {
        relationItem.remove_item = true
        relationItem.verdict = 'incorrect'
        relationItem.reason = `随人物 ${personKey} 一并移除关联关系。`
        relationItem.correction = ''
        relationItem.correction_evidence = []
        relationItem.correction_data = {}
      }
    } else if (!removing && item.reason === `随人物 ${personKey} 一并移除关联关系。`) {
      item.remove_item = false
      item.verdict = 'unreviewed'
      item.reason = ''
    }
  }
  markDirty()
}

function toggleSectionRemoval(section: ReviewSection) {
  if (isReadOnly.value || !isRemovableSection(section)) return
  const removing = !sectionWillBeRemoved(section)
  if (removing && !window.confirm(`确认删除“${section.title}”整条记录吗？`)) {
    return
  }
  for (const item of section.items) {
    item.remove_item = removing
    item.verdict = removing ? 'incorrect' : 'unreviewed'
    item.reason = removing ? 'AI 误生成该记录，质检后移除。' : ''
    item.correction = ''
    item.correction_evidence = []
    item.correction_data = {}
  }
  markDirty()
}

function toggleEvidenceRemoval(item: AIAnnotationReviewItem) {
  if (isReadOnly.value || !isRemovableEvidenceItem(item)) return
  const removing = !item.remove_item
  if (removing && !window.confirm(`确认删除证据“${item.evidence[0]?.quote || item.ai_value}”吗？`)) return
  item.remove_item = removing
  item.verdict = removing ? 'incorrect' : 'unreviewed'
  item.reason = removing ? 'AI 误关联该证据，质检后移除。' : ''
  item.correction = ''
  item.correction_evidence = []
  item.correction_data = {}
  markDirty()
}

function selectGroup(groupKey: string) {
  activeGroupKey.value = groupKey
  itemSearch.value = ''
  const group = allGroups.value.find((entry) => entry.key === groupKey)
  const firstUnreviewed = group?.sections.flatMap((section) => section.items)
    .find((item) => item.verdict === 'unreviewed')
  activeItemId.value = firstUnreviewed?.id ?? group?.sections[0]?.items[0]?.id ?? activeItemId.value
}

async function focusItem(item: AIAnnotationReviewItem) {
  activeItemId.value = item.id
  revealQuietItem(item)
  if (item.group_key) activeGroupKey.value = item.group_key
  const evidence = item.evidence[0]
  if (!evidence) return
  await focusEvidence(evidence)
}

async function focusEvidence(evidence: EvidenceSpan) {
  activeEvidenceId.value = evidence.id
  await nextTick()
  textCanvas.value?.focusEvidence(evidence.id)
}

function activateHighlight(evidenceId: string) {
  activeEvidenceId.value = evidenceId
  const candidates = (review.value?.items ?? []).filter(
    (item) => item.evidence.some((evidence) => evidence.id === evidenceId),
  )
  const item = candidates.find((candidate) => candidate.path.includes('.mentions.'))
    ?? candidates[0]
  if (item) {
    const stepId = reviewStepOfItem(item)
    if (activeStep.value !== stepId) selectStep(stepId)
    activeItemId.value = item.id
    revealQuietItem(item)
    if (item.group_key) activeGroupKey.value = item.group_key
  }
  const omission = review.value?.omissions.find(
    (candidate) => candidate.evidence.some((evidence) => evidence.id === evidenceId),
  )
  if (omission) activeOmissionId.value = omission.id
}

function handleSelectionCreated(evidence: EvidenceSpan) {
  pendingEvidence.value = evidence
  activeEvidenceId.value = null
}

function correctionLabel(item: AIAnnotationReviewItem) {
  if (item.value_kind === 'evidence') return '正确原文'
  if (item.value_kind === 'coverage') return '应如何修正'
  return '正确值'
}

function isEventCheckItem(item: AIAnnotationReviewItem) {
  return item.path.includes('.event_checks.')
}

function eventCheckRequiresEvidence(item: AIAnnotationReviewItem) {
  return item.verdict === 'incorrect'
    && isEventCheckItem(item)
    && ['已检查且有事实', 'has_fact'].includes(item.correction.trim())
}

function eventCheckCreatesEvent(item: AIAnnotationReviewItem) {
  return eventCheckRequiresEvidence(item)
    && ['原文未提及', 'not_mentioned'].includes(item.ai_value.trim())
}

function eventTypeForCheck(item: AIAnnotationReviewItem): EventType | null {
  const eventType = item.path.split('.').at(-1) as EventType | undefined
  return eventType && EVENT_TYPES.includes(eventType) ? eventType : null
}

function ensureEventCheckCorrectionData(item: AIAnnotationReviewItem) {
  if (!eventCheckCreatesEvent(item)) return
  const eventType = eventTypeForCheck(item)
  if (!eventType) return
  const seed = createLifeEvent(eventType)
  const current = item.correction_data ?? {}
  const mergedTime = { ...seed.time, ...(current.time ?? {}) }
  item.correction_data = {
    key: String(current.key || seed.key),
    event_type: eventType,
    state: current.state ?? seed.state,
    time: eventType === '籍贯' ? seed.time : mergedTime,
    location: { ...seed.location, ...(current.location ?? {}) },
    official_title: eventType === '任职' ? String(current.official_title || '') : '',
    note: String(current.note || ''),
  }
}

function updateCorrection(item: AIAnnotationReviewItem) {
  activeItemId.value = item.id
  if (eventCheckCreatesEvent(item)) ensureEventCheckCorrectionData(item)
  else item.correction_data = {}
  markDirty()
}

function updateEventCorrection(item: AIAnnotationReviewItem) {
  ensureEventCheckCorrectionData(item)
  const eventType = eventTypeForCheck(item)
  if (eventType === '籍贯' && item.correction_data.time) {
    item.correction_data.time = createLifeEvent('籍贯').time
  }
  if (eventType !== '任职') item.correction_data.official_title = ''
  markDirty()
}

function setEventCorrectionNumber(
  item: AIAnnotationReviewItem,
  field: 'era_year' | 'gregorian_year',
  event: Event,
) {
  ensureEventCheckCorrectionData(item)
  if (!item.correction_data.time) return
  const value = (event.target as HTMLInputElement).value
  item.correction_data.time[field] = value === '' ? null : Number(value)
  updateEventCorrection(item)
}

function selectedEventCorrectionEra(item: AIAnnotationReviewItem) {
  const value = item.correction_data.time?.era?.trim()
  if (!value) return null
  return dictionaries.value.eras.find((entry) => (
    entry.era === value || entry.simplified === value || entry.traditional === value
  )) ?? null
}

function eventCorrectionProblem(item: AIAnnotationReviewItem) {
  if (!eventCheckCreatesEvent(item)) return ''
  const eventType = eventTypeForCheck(item)
  const data = item.correction_data
  if (!eventType || !data.key || data.event_type !== eventType || !data.time || !data.location) {
    return '新增事件卡片信息不完整'
  }
  if (eventType === '任职' && !String(data.official_title || '').trim()) {
    return '任职事件必须填写完整官职'
  }
  if (eventType === '籍贯' && data.time.state !== 'not_applicable') {
    return '籍贯事件的时间状态必须为不适用'
  }
  return ''
}

function correctionEvidenceActionLabel() {
  return activeItem.value && isEventCheckItem(activeItem.value)
    ? '绑定为该事件的事实证据'
    : '作为当前项的正确原文'
}

function correctionOptions(item: AIAnnotationReviewItem): CorrectionOption[] {
  const path = item.path
  const field = path.split('.').at(-1) ?? ''
  if (field === 'source_person_key' || field === 'target_person_key') {
    return reviewPersonOptions.value
  }
  if (path === 'passage.source_type') return sourceTypeOptions
  if (path === 'passage.material_status') return materialStatusOptions
  if (['is_female', 'is_damaged', 'is_clergy', 'has_courtesy_name'].includes(field)) {
    return nullableBooleanOptions
  }
  if (field === 'outside_dictionary') return booleanOptions
  if (field === 'relation_category') return relationCategoryOptions
  if (field === 'level') return levelOptions
  if (field === 'event_type') return eventTypeOptions
  if (path.includes('.event_checks.')) return checkStateOptions
  if (path.endsWith('.time.state') || path.endsWith('.location.state')) return fieldStateOptions
  if (field === 'state') return factStateOptions
  return []
}

function binaryAlternativeFor(item: AIAnnotationReviewItem): string | null {
  const options = correctionOptions(item)
  if (options.length !== 2) return null

  const aiValue = item.ai_value.trim()
  let matchingIndexes = options
    .map((option, index) => ({ option, index }))
    .filter(({ option }) => option.value === aiValue || option.label === aiValue)
    .map(({ index }) => index)

  const field = item.path.split('.').at(-1) ?? ''
  if (
    matchingIndexes.length !== 1
    && (field === 'source_person_key' || field === 'target_person_key')
  ) {
    const matchingPersonKeys = (review.value?.items ?? [])
      .filter((candidate) => (
        candidate.id.startsWith('person:')
        && candidate.id.endsWith(':name_surface')
        && candidate.ai_value.trim() === aiValue
      ))
      .map((candidate) => candidate.id.slice('person:'.length, -':name_surface'.length))
    if (matchingPersonKeys.length === 1) {
      matchingIndexes = options
        .map((option, index) => ({ option, index }))
        .filter(({ option }) => option.value === matchingPersonKeys[0])
        .map(({ index }) => index)
    }
  }

  if (matchingIndexes.length !== 1) return null
  return options[matchingIndexes[0] === 0 ? 1 : 0].value
}

function correctionDictionaryKind(item: AIAnnotationReviewItem): 'era' | 'historical-event' | null {
  if (item.path === 'passage.era' || item.path.endsWith('.time.era')) return 'era'
  if (item.path.includes('.historical_events.') && item.path.endsWith('.event_name')) {
    return 'historical-event'
  }
  return null
}

function correctionListId(item: AIAnnotationReviewItem) {
  const kind = correctionDictionaryKind(item)
  if (kind === 'era') return 'review-era-options'
  if (kind === 'historical-event') return 'review-history-options'
  return undefined
}

function selectedEra(item: AIAnnotationReviewItem) {
  const value = item.correction.trim()
  if (!value) return null
  return dictionaries.value.eras.find((entry) => (
    entry.era === value || entry.simplified === value || entry.traditional === value
  )) ?? null
}

function selectedHistoricalEvent(item: AIAnnotationReviewItem) {
  const value = item.correction.trim()
  if (!value) return null
  return dictionaries.value.historical_events.find((entry) => entry.event_name === value) ?? null
}

function evidenceRangeLabel(evidence: EvidenceSpan) {
  const length = evidence.end - evidence.start
  if (length <= 1) return `第 ${evidence.start} 字`
  return `第 ${evidence.start}–${evidence.end - 1} 字（${length} 字）`
}

function appendCorrectionEvidence(item: AIAnnotationReviewItem, evidence: EvidenceSpan) {
  const spans = item.correction_evidence ?? []
  if (spans.some((span) => span.start === evidence.start && span.end === evidence.end)) {
    correctionEvidenceFeedback.value[item.id] = '该处原文已经加入证据。'
    return false
  }
  if (spans.length >= 8) {
    correctionEvidenceFeedback.value[item.id] = '每个事实最多保留 8 处证据。'
    return false
  }
  spans.push(evidence)
  item.correction_evidence = spans
  correctionEvidenceFeedback.value[item.id] = ''
  return true
}

function addTypedCorrectionEvidence(item: AIAnnotationReviewItem) {
  if (!review.value || isReadOnly.value) return
  activeItemId.value = item.id
  const quote = (correctionEvidenceDrafts.value[item.id] ?? '').trim()
  if (!quote) {
    correctionEvidenceFeedback.value[item.id] = '请输入原文证据，或直接在左侧正文中划选。'
    return
  }
  const context = review.value.passage_context
  const startUtf16 = context.indexOf(quote)
  if (startUtf16 < 0) {
    correctionEvidenceFeedback.value[item.id] = '正文中没有找到完全一致的文字，请核对后重试。'
    return
  }
  if (context.indexOf(quote, startUtf16 + 1) >= 0) {
    correctionEvidenceFeedback.value[item.id] = '正文中有多处相同文字，请在左侧划选准确位置。'
    return
  }
  const evidence: EvidenceSpan = {
    id: uid('review-evidence'),
    source: 'context',
    quote,
    start: Array.from(context.slice(0, startUtf16)).length,
    end: Array.from(context.slice(0, startUtf16 + quote.length)).length,
    start_utf16: startUtf16,
    end_utf16: startUtf16 + quote.length,
  }
  const issue = evidenceGranularityIssue(evidence, 'fact')
  if (issue) {
    correctionEvidenceFeedback.value[item.id] = issue
    return
  }
  if (appendCorrectionEvidence(item, evidence)) {
    correctionEvidenceDrafts.value[item.id] = ''
    markDirty()
  }
}

function applyPendingToCorrection() {
  if (!activeItem.value || !pendingEvidence.value || isReadOnly.value || pendingEvidenceIssue.value) return
  const item = activeItem.value
  const evidence = pendingEvidence.value
  item.verdict = 'incorrect'
  if (!isEventCheckItem(item)) item.correction = evidence.quote
  if (appendCorrectionEvidence(item, evidence)) {
    pendingEvidence.value = null
    markDirty()
  }
}

function removeCorrectionEvidence(item: AIAnnotationReviewItem, evidenceId: string) {
  if (isReadOnly.value) return
  item.correction_evidence = (item.correction_evidence ?? []).filter((span) => span.id !== evidenceId)
  if (!item.correction_evidence.length && item.correction) {
    /* keep typed correction */
  } else if (item.correction_evidence.length && !isEventCheckItem(item)) {
    item.correction = item.correction_evidence.map((span) => span.quote).join(' / ')
  }
  markDirty()
}

function defaultAdditionData(type: AIAnnotationReviewAdditionType): AIAnnotationReviewAdditionData {
  if (type === 'person') {
    const person = createPerson()
    return {
      key: person.key,
      name_surface: person.name_surface,
      completed_name: person.completed_name,
      completion_reason: person.completion_reason,
      courtesy_name: person.courtesy_name,
      hao: person.hao,
      titles: person.titles,
      level: person.level,
      level_reason: person.level_reason,
      event_checks: person.event_checks,
    }
  }
  if (type === 'life_event') {
    const event = createLifeEvent('出生')
    return {
      key: event.key,
      event_type: event.event_type,
      state: event.state,
      time: event.time,
      location: event.location,
      official_title: event.official_title,
      note: event.note,
    }
  }
  if (type === 'historical_event') {
    const event = createHistoricalEvent()
    return {
      key: event.key,
      event_name: event.event_name,
      outside_dictionary: event.outside_dictionary,
      relation_summary: event.relation_summary,
      state: event.state,
      note: event.note,
    }
  }
  if (type === 'person_relation') {
    const source = reviewPersonOptions.value[0]?.value ?? ''
    const target = reviewPersonOptions.value[1]?.value ?? ''
    const relation = createPersonRelation(source, target)
    return {
      key: relation.key,
      source_person_key: relation.source_person_key,
      target_person_key: relation.target_person_key,
      relation_category: relation.relation_category,
      relation_summary: relation.relation_summary,
      state: relation.state,
    }
  }
  if (type === 'evidence') return { target_type: '', target_key: '' }
  if (type === 'excluded_mention') return { key: uid('excluded'), reason: '' }
  if (type === 'unresolved_item') return { key: uid('unresolved'), category: '人物或事实', note: '' }
  if (type === 'schema_conflict') return { key: uid('conflict'), code: '', note: '' }
  return {}
}

function refreshAdditionMetadata(omission: AIAnnotationReviewOmission) {
  const data = omission.data
  if (!omission.category) omission.category = additionTypeToCategory[omission.addition_type]
  if (omission.addition_type === 'person') {
    omission.target_path = 'persons'
    omission.description = `漏建人物${data.name_surface ? `“${data.name_surface}”` : ''}`
    omission.correction = String(data.name_surface || '')
  } else if (omission.addition_type === 'life_event') {
    omission.target_path = `person:${omission.parent_key}:life_events`
    omission.description = `漏建${data.event_type || ''}事件`
    omission.correction = [data.event_type, data.official_title, data.time?.raw, data.location?.raw]
      .filter(Boolean).join(' · ')
  } else if (omission.addition_type === 'historical_event') {
    omission.target_path = `person:${omission.parent_key}:historical_events`
    omission.description = '漏建历史事件关联'
    omission.correction = String(data.event_name || '')
  } else if (omission.addition_type === 'person_relation') {
    omission.target_path = 'person_relations'
    omission.description = '漏建人物关系'
    omission.correction = `${data.source_person_key || '？'}与${data.target_person_key || '？'} · ${data.relation_category || '待选类别'} · ${data.relation_summary || '待填具体表现'}`
  } else if (omission.addition_type === 'evidence') {
    omission.target_path = `${data.target_type || ''}:${data.target_key || ''}:evidence`
    omission.description = '漏标原文证据'
    omission.correction = omission.evidence.map((item) => item.quote).join(' / ')
  } else if (omission.addition_type === 'excluded_mention') {
    omission.target_path = 'excluded_mentions'
    omission.description = '漏记应排除称谓'
    omission.correction = String(data.reason || '')
  } else if (omission.addition_type === 'unresolved_item') {
    omission.target_path = 'unresolved_items'
    omission.description = '漏记暂不能判断项'
    omission.correction = String(data.note || '')
  } else if (omission.addition_type === 'schema_conflict') {
    omission.target_path = 'schema_conflicts'
    omission.description = '漏记工具限制'
    omission.correction = [data.code, data.note].filter(Boolean).join(' · ')
  }
}

function addStructuredAddition(
  type: AIAnnotationReviewAdditionType = 'person',
  evidence?: EvidenceSpan,
  target?: EvidenceTargetOption,
) {
  if (!review.value || isReadOnly.value) return
  const data = defaultAdditionData(type)
  if (type === 'person' && evidence) data.name_surface = evidence.quote
  if (type === 'evidence' && target) {
    data.target_type = target.targetType
    data.target_key = target.targetKey
  }
  const omission: AIAnnotationReviewOmission = {
    id: uid('omission'),
    category: additionTypeToCategory[type],
    target_path: '',
    description: '',
    correction: '',
    evidence: evidence ? [evidence] : [],
    evidence_note: '',
    addition_type: type,
    parent_key: ['life_event', 'historical_event'].includes(type)
      ? (reviewFactPersonOptions.value[0]?.value ?? '')
      : '',
    data,
  }
  refreshAdditionMetadata(omission)
  review.value.omissions.push(omission)
  activeOmissionId.value = omission.id
  pendingEvidence.value = null
  markDirty()
}

function quickAdd(event: Event) {
  const select = event.target as HTMLSelectElement
  const type = select.value as AIAnnotationReviewAdditionType
  if (type) addStructuredAddition(type)
  select.value = ''
}

function changeAdditionType(omission: AIAnnotationReviewOmission, event: Event) {
  const type = (event.target as HTMLSelectElement).value as AIAnnotationReviewAdditionType
  omission.addition_type = type
  omission.category = additionTypeToCategory[type]
  omission.parent_key = ['life_event', 'historical_event'].includes(type)
    ? (reviewFactPersonOptions.value[0]?.value ?? '')
    : ''
  omission.data = defaultAdditionData(type)
  refreshAdditionMetadata(omission)
  markDirty()
}

function evidenceTargetValue(omission: AIAnnotationReviewOmission) {
  return `${omission.data.target_type || ''}:${omission.data.target_key || ''}`
}

function setEvidenceTarget(omission: AIAnnotationReviewOmission, event: Event) {
  const selected = evidenceTargets.value.find(
    (item) => item.value === (event.target as HTMLSelectElement).value,
  )
  omission.data.target_type = selected?.targetType ?? ''
  omission.data.target_key = selected?.targetKey ?? ''
  refreshAdditionMetadata(omission)
  markDirty()
}

function targetForReviewItem(item: AIAnnotationReviewItem | null) {
  if (!item) return null
  const parts = item.id.split(':')
  let value = ''
  if (parts[0] === 'person' && parts[1] && parts[2] !== 'event-check') value = `person:${parts[1]}`
  else if (parts[0] === 'event' && parts[2]) value = `life_event:${parts[2]}`
  else if (parts[0] === 'history' && parts[2]) value = `historical_event:${parts[2]}`
  else if (parts[0] === 'relation' && parts[1]) value = `person_relation:${parts[1]}`
  else if (parts[0] === 'aux' && parts[2]) {
    const typeMap: Record<string, string> = {
      excluded_mentions: 'excluded_mention',
      unresolved_items: 'unresolved_item',
      schema_conflicts: 'schema_conflict',
    }
    if (typeMap[parts[1]]) value = `${typeMap[parts[1]]}:${parts[2]}`
  }
  return evidenceTargets.value.find((target) => target.value === value) ?? null
}

const activeEvidenceTarget = computed(() => targetForReviewItem(activeItem.value))

function addPendingAsPerson() {
  if (!pendingEvidence.value || pendingEntityIssue.value) return
  addStructuredAddition('person', pendingEvidence.value)
}

function addPendingToActiveRecord() {
  const target = activeEvidenceTarget.value
  const evidence = pendingEvidence.value
  if (!target || !evidence) return
  const issue = target.kind === 'entity' ? pendingEntityIssue.value : pendingFactIssue.value
  if (issue) return
  addStructuredAddition('evidence', evidence, target)
}

function additionEvidenceKind(omission: AIAnnotationReviewOmission): 'entity' | 'fact' {
  if (omission.addition_type === 'person' || omission.addition_type === 'excluded_mention') {
    return 'entity'
  }
  if (omission.addition_type === 'evidence') {
    const target = evidenceTargets.value.find((item) => item.value === evidenceTargetValue(omission))
    return target?.kind ?? 'fact'
  }
  return 'fact'
}

function appendPendingEvidence() {
  if (!activeOmission.value || !pendingEvidence.value) return
  const issue = evidenceGranularityIssue(
    pendingEvidence.value,
    additionEvidenceKind(activeOmission.value),
  )
  if (issue) return
  if (!activeOmission.value.evidence.some((item) => item.id === pendingEvidence.value?.id)) {
    activeOmission.value.evidence.push(pendingEvidence.value)
  }
  refreshAdditionMetadata(activeOmission.value)
  pendingEvidence.value = null
  markDirty()
}

function updateAddition(omission: AIAnnotationReviewOmission) {
  refreshAdditionMetadata(omission)
  markDirty()
}

function updateAdditionTitles(omission: AIAnnotationReviewOmission, event: Event) {
  omission.data.titles = (event.target as HTMLInputElement).value
    .split(/[、,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
  updateAddition(omission)
}

function changeLifeEventType(omission: AIAnnotationReviewOmission, event: Event) {
  const eventType = (event.target as HTMLSelectElement).value as EventType
  omission.data.event_type = eventType
  if (omission.data.time) {
    omission.data.time.state = eventType === '籍贯' ? 'not_applicable' : 'not_mentioned'
    if (eventType === '籍贯') {
      omission.data.time.raw = ''
      omission.data.time.era = ''
      omission.data.time.era_year = null
      omission.data.time.gregorian_year = null
      omission.data.time.month_text = ''
      omission.data.time.day_text = ''
    }
  }
  if (eventType !== '任职') omission.data.official_title = ''
  updateAddition(omission)
}

function additionProblem(omission: AIAnnotationReviewOmission) {
  const data = omission.data
  if (!omissionCategoryOptions.includes(omission.category)) return '请选择缺漏类型'
  if (omission.addition_type === 'note') {
    if (!omission.target_path.trim() || !omission.description.trim() || !omission.correction.trim()) {
      return '缺漏说明未补完整'
    }
  } else if (omission.addition_type === 'person') {
    if (!String(data.name_surface || '').trim() || !String(data.level_reason || '').trim()) {
      return '新增人物缺少姓名或分级理由'
    }
  } else if (omission.addition_type === 'life_event') {
    if (!omission.parent_key || !data.event_type) return '新增事件未选择人物或类型'
    if (data.event_type === '任职' && !String(data.official_title || '').trim()) return '任职事件缺少官职'
  } else if (omission.addition_type === 'historical_event') {
    if (!omission.parent_key || !String(data.event_name || '').trim() || !String(data.relation_summary || '').trim()) {
      return '新增历史事件信息不完整'
    }
  } else if (omission.addition_type === 'person_relation') {
    if (
      !data.source_person_key
      || !data.target_person_key
      || data.source_person_key === data.target_person_key
    ) return '新增关系必须选择两个不同人物'
    if (!relationCategoryOptions.some((option) => option.value === data.relation_category)) return '新增关系必须选择七类之一'
    if (!String(data.relation_summary || '').trim() || String(data.relation_summary).length > 10) {
      return '新增关系的具体表现须填写且不超过 10 字'
    }
  } else if (omission.addition_type === 'evidence') {
    if (!data.target_type || !data.target_key) return '新增证据未选择所属记录'
  } else if (omission.addition_type === 'excluded_mention') {
    if (!String(data.reason || '').trim()) return '排除称谓缺少原因'
  } else if (omission.addition_type === 'unresolved_item') {
    if (!String(data.category || '').trim() || !String(data.note || '').trim()) return '暂不能判断项信息不完整'
  } else if (omission.addition_type === 'schema_conflict') {
    if (!String(data.code || '').trim() || !String(data.note || '').trim()) return '工具限制信息不完整'
  }
  if (!omission.evidence.length && !omission.evidence_note.trim()) return '缺漏或结构增补缺少原文证据或位置说明'
  return ''
}

function removeOmission(omissionId: string) {
  if (!review.value || isReadOnly.value) return
  const index = review.value.omissions.findIndex((item) => item.id === omissionId)
  if (index >= 0) review.value.omissions.splice(index, 1)
  if (activeOmissionId.value === omissionId) {
    activeOmissionId.value = review.value.omissions[0]?.id ?? null
  }
  markDirty()
}

function removeOmissionEvidence(omissionId: string, evidenceId: string) {
  const omission = review.value?.omissions.find((item) => item.id === omissionId)
  if (!omission || isReadOnly.value) return
  omission.evidence = omission.evidence.filter((item) => item.id !== evidenceId)
  refreshAdditionMetadata(omission)
  markDirty()
}

async function submitReview() {
  if (!review.value || submissionProblems.value.length || submitting.value) return
  if (!window.confirm('确认提交当前质检结果吗？盲标正式提交前仍可继续修改并再次提交。')) return
  submitting.value = true
  feedback.value = ''
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
  }
  try {
    // 提交必须排在已发出的草稿保存之后；否则较晚返回的草稿响应会把
    // 页面上的 submitted 状态覆盖回 draft，看起来像质检没有提交。
    if (activeSavePromise) await activeSavePromise
    if (saveTimer !== null) {
      window.clearTimeout(saveTimer)
      saveTimer = null
    }
    saveQueued = false
    const current = review.value
    if (!current) return
    review.value = await submitAIAnnotationReview(current.generation_job_id, current)
    dirty.value = false
    feedback.value = `首轮质检已提交，正确率 ${percent(review.value.summary.accuracy)}。`
  } catch (error) {
    feedback.value = getApiErrorMessage(error, '首轮质检提交失败。')
  } finally {
    submitting.value = false
  }
}

watch(
  () => route.params.jobId,
  async (_jobId, previous) => {
    if (route.name !== 'extraction-ai-annotation-review') return
    let previousSaveFailed = false
    if (previous !== undefined && dirty.value && !isReadOnly.value) {
      await saveDraft()
      previousSaveFailed = dirty.value
    }
    await loadReview()
    if (previousSaveFailed) feedback.value = '上一份质检草稿保存失败，请回到该份后重试。'
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (saveTimer !== null) window.clearTimeout(saveTimer)
  if (dirty.value && !isReadOnly.value) void saveDraft()
})
</script>

<template>
  <AppLayout variant="workspace">
    <div class="review-workbench">
      <header class="review-topbar">
        <div class="review-title">
          <RouterLink to="/annotations/extraction/ai" aria-label="返回 AI 标注工作台">←</RouterLink>
          <div>
            <span>AI FIRST-PASS REVIEW · 试用</span>
            <h2>{{ review?.passage_title ?? '首轮 AI 标注质检' }}</h2>
          </div>
        </div>
        <div v-if="review" class="review-progress">
          <div>
            <span>总检查进度</span>
            <strong>{{ review.items.filter((item) => item.verdict !== 'unreviewed').length }} / {{ review.items.length }}</strong>
          </div>
          <i><b :style="{ width: `${progressPercent}%` }" /></i>
          <small>{{ progressPercent }}%</small>
        </div>
        <div v-if="review" class="accuracy-block">
          <span>首轮正确率</span>
          <strong>{{ percent(liveSummary.accuracy) }}</strong>
          <small>正确 ÷（正确 + 错误）</small>
        </div>
        <div class="top-actions">
          <span v-if="review" class="state" :data-state="review.status">
            {{ !review.editable
              ? '盲标已提交 · 已锁定'
              : saving
                ? '保存中…'
                : dirty
                  ? '待保存'
                  : review.status === 'submitted'
                    ? '质检已提交 · 可修改'
                    : review.submitted_at
                      ? '修改已保存 · 待重新提交'
                      : '草稿已保存' }}
          </span>
          <RouterLink to="/annotations/extraction/ai">返回工作台</RouterLink>
        </div>
      </header>

      <nav
        v-if="currentGeneration || siblingGenerations.length"
        class="generation-links"
        aria-label="同篇其它生成"
      >
        <span v-if="currentGeneration">
          当前 {{ providerLabel(currentGeneration.provider) }} · {{ currentGeneration.model || '默认模型' }}
          · {{ formatDateTime(currentGeneration.created_at) }} · {{ temperatureLabel(currentGeneration.temperature) }}
        </span>
        <RouterLink
          v-for="row in siblingGenerations"
          :key="row.job_id"
          :to="`/annotations/extraction/ai/review/${row.job_id}`"
        >
          {{ providerLabel(row.provider) }} · {{ row.model || '默认模型' }}
          · {{ formatDateTime(row.created_at) }} · {{ temperatureLabel(row.temperature) }}
        </RouterLink>
      </nav>

      <nav v-if="review" class="review-steps" aria-label="标注质检步骤">
        <button
          v-for="(step, index) in stepStatuses"
          :key="step.id"
          type="button"
          :class="{ active: activeStep === step.id, complete: step.complete, incomplete: !step.complete }"
          :aria-current="activeStep === step.id ? 'step' : undefined"
          @click="selectStep(step.id)"
        >
          <b>{{ String(index + 1).padStart(2, '0') }}</b>
          <span>{{ step.label }}</span>
          <small>{{ step.complete ? '已完成' : `未完成 ${step.reviewed}/${step.total}` }}</small>
        </button>
      </nav>

      <div v-if="loading" class="page-state">
        <b>正在组装检查清单</b>
        <p>检查对象固定为不可变的首轮 AI 生成结果。</p>
      </div>
      <div v-else-if="!review" class="page-state page-state--error">
        <b>无法进入质检</b>
        <p>{{ feedback || '首轮 AI 结果不存在或尚未完成。' }}</p>
        <RouterLink to="/annotations/extraction/ai">返回 AI 标注工作台</RouterLink>
      </div>

      <div v-else class="review-body">
        <section class="source-column">
          <header class="column-heading">
            <div>
              <span>01 / SOURCE EVIDENCE</span>
              <h3>原文与实体证据</h3>
            </div>
            <small>
              任务 #{{ review.task_id }} · 首轮 #{{ review.generation_job_id }}
              <template v-if="currentGeneration">
                · {{ providerLabel(currentGeneration.provider) }} · {{ currentGeneration.model || '默认模型' }}
              </template>
            </small>
          </header>
          <div class="source-legend">
            <span><i data-kind="person" />人物</span>
            <span><i data-kind="event" />事件</span>
            <span><i data-kind="history" />历史事件</span>
            <span><i data-kind="relation" />关系</span>
            <span><i data-kind="unresolved" />缺漏</span>
            <span><i data-kind="located" />当前定位</span>
          </div>
          <div class="source-scroll">
            <AnnotationTextCanvas
              ref="textCanvas"
              :text="review.passage_context"
              :highlights="highlights"
              :active-evidence-id="activeEvidenceId"
              :disabled="isReadOnly"
              @selection-created="handleSelectionCreated"
              @highlight-activated="activateHighlight"
            />
          </div>
          <Transition name="dock">
            <div v-if="pendingEvidence" class="evidence-dock">
              <button type="button" aria-label="取消选区" @click="pendingEvidence = null">×</button>
              <div>
                <span>已划选 {{ pendingEvidence.end - pendingEvidence.start }} 字 · {{ evidenceRangeLabel(pendingEvidence) }}</span>
                <strong>“{{ pendingEvidence.quote }}”</strong>
                <small v-if="pendingEvidenceIssue">{{ pendingEvidenceIssue }}</small>
              </div>
              <div>
                <button
                  type="button"
                  :disabled="!activeItem || Boolean(pendingEvidenceIssue)"
                  @click="applyPendingToCorrection"
                >{{ correctionEvidenceActionLabel() }}</button>
                <button type="button" :disabled="Boolean(pendingEntityIssue)" @click="addPendingAsPerson">新增人物</button>
                <button
                  type="button"
                  :disabled="!activeEvidenceTarget || Boolean(activeEvidenceTarget?.kind === 'entity' ? pendingEntityIssue : pendingFactIssue)"
                  @click="addPendingToActiveRecord"
                >追加到当前记录</button>
                <button
                  type="button"
                  :disabled="!activeOmission || Boolean(pendingActiveOmissionIssue)"
                  @click="appendPendingEvidence"
                >加入当前补录</button>
                <button type="button" :disabled="Boolean(pendingFactIssue)" @click="addStructuredAddition('life_event', pendingEvidence)">新增事件</button>
              </div>
            </div>
          </Transition>
        </section>

        <section class="check-column">
          <header class="column-heading check-heading">
            <div>
              <span>02 / FIELD DECISIONS</span>
              <h3>{{ reviewSteps.find((step) => step.id === activeStep)?.label }} · 逐字段判断</h3>
            </div>
            <b>{{ (activeStepStatus?.total ?? 0) - (activeStepStatus?.reviewed ?? 0) }} 未判断</b>
          </header>
          <div class="check-tools">
            <label>
              <span>搜索字段</span>
              <input v-model="itemSearch" type="search" placeholder="人物、字段、正确值或原文证据" />
            </label>
            <nav aria-label="检查对象">
              <button
                v-for="group in allGroups"
                :key="group.key"
                type="button"
                :class="{ active: !itemSearch && activeGroupKey === group.key }"
                @click="selectGroup(group.key)"
              >
                {{ group.title }}
                <small>{{ group.reviewed }}/{{ group.total }}</small>
              </button>
            </nav>
            <p class="offset-guide">
              <b>与抽取标注页同一粒度</b>
              每一行对应一个需填写字段。字段可纠正，人物及各类记录可整条删除，单条证据也可删除；划选左侧原文后可新增人物、事件或给当前记录追加证据。证据位置从 0 起算、左闭右开。
            </p>
          </div>
          <div class="check-scroll">
            <p v-if="!visibleGroups.length" class="empty">没有匹配的检查项。</p>
            <section
              v-for="group in visibleGroups"
              :key="group.key"
              class="field-group"
            >
              <header class="field-group__heading">
                <div>
                  <span>{{ group.category }}</span>
                  <h4>{{ group.title }}</h4>
                </div>
                <div class="field-group__actions">
                  <small>{{ group.reviewed }} / {{ group.total }}</small>
                  <button
                    v-if="isPersonGroup(group)"
                    type="button"
                    :class="{ restore: groupWillBeRemoved(group) }"
                    :disabled="isReadOnly"
                    @click="togglePersonRemoval(group)"
                  >{{ groupWillBeRemoved(group) ? '恢复人物' : '删除人物' }}</button>
                </div>
              </header>
              <article
                v-for="section in group.sections"
                :key="section.key"
                class="field-section"
              >
                  <header>
                    <strong>{{ section.title }}</strong>
                    <div>
                      <small>{{ section.reviewed }}/{{ section.total }}</small>
                      <button
                        v-if="isRemovableSection(section)"
                        type="button"
                        :disabled="isReadOnly"
                        @click.stop="toggleSectionRemoval(section)"
                      >{{ sectionWillBeRemoved(section) ? '恢复记录' : '删除记录' }}</button>
                      <button
                        type="button"
                        :disabled="isReadOnly || section.reviewed === section.total"
                      @click="markSectionCorrect(section)"
                    >本节未判均正确</button>
                  </div>
                </header>
                <table class="field-table">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>AI 填写</th>
                      <th>判定</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template v-for="item in section.items" :key="item.id">
                      <tr v-if="quietBundle(section, item)" class="quiet-row" @click="toggleQuietGroup(section, item)">
                        <th colspan="3">
                          <div class="quiet-row__bar">
                            <button type="button">
                              {{ detailAspect(item) && quietGroupExpanded(section, detailAspect(item) || 'time') ? '收起' : '展开' }}
                            </button>
                            <span :title="(quietBundle(section, item)?.items || []).map((entry) => `${entry.field_label || entry.title} ${entry.ai_value || '（空）'}`).join('、')">
                              {{ quietSummaryText(quietBundle(section, item)?.items || []) }}
                            </span>
                            <small>
                              {{ (quietBundle(section, item)?.items || []).filter((entry) => entry.verdict !== 'unreviewed').length }}/{{ quietBundle(section, item)?.items.length || 0 }}
                            </small>
                            <button
                              v-if="(quietBundle(section, item)?.items || []).some((entry) => entry.verdict === 'unreviewed')"
                              type="button"
                              :disabled="isReadOnly"
                              @click.stop="markCollapsedDetailsCorrect(quietBundle(section, item)?.items || [])"
                            >未判均正确</button>
                          </div>
                        </th>
                      </tr>
                      <tr
                        v-show="!isDetailRowCollapsed(section, item)"
                        :class="{ active: activeItemId === item.id }"
                        :data-verdict="item.verdict"
                        @click="activeItemId = item.id"
                      >
                        <th scope="row">
                          <span :title="item.question">{{ item.field_label || item.title }}</span>
                        </th>
                        <td>
                          <div class="ai-cell">
                            <strong :title="item.path">{{ item.ai_value || '（空）' }}</strong>
                            <small v-if="item.remove_item" class="ai-cell__removed">整条记录将移除</small>
                            <button
                              v-if="item.evidence.length"
                              type="button"
                              :class="{ active: item.evidence.some((evidence) => evidence.id === activeEvidenceId) }"
                              @click.stop="focusItem(item)"
                            >定位</button>
                            <button
                              v-if="isRemovableEvidenceItem(item) && !sectionWillBeRemoved(section)"
                              type="button"
                              class="ai-cell__delete"
                              :disabled="isReadOnly"
                              @click.stop="toggleEvidenceRemoval(item)"
                            >{{ item.remove_item ? '恢复' : '删除' }}</button>
                          </div>
                        </td>
                        <td>
                          <div class="verdict-buttons" role="group" :aria-label="`${item.field_label || item.title}判定`">
                            <button
                              type="button"
                              :class="{ selected: item.verdict === 'correct' }"
                              :disabled="isReadOnly"
                              @click.stop="setVerdict(item, 'correct')"
                            ><i>✓</i> 正确</button>
                            <button
                              type="button"
                              :class="{ selected: item.verdict === 'incorrect' }"
                              :disabled="isReadOnly"
                              @click.stop="setVerdict(item, 'incorrect')"
                            ><i>×</i> 错误</button>
                          </div>
                        </td>
                      </tr>
                      <tr v-if="item.verdict === 'incorrect' && !item.remove_item" class="correction-row">
                        <td colspan="3">
                          <div class="reason-field" @click.stop="activeItemId = item.id">
                            <span>{{ correctionLabel(item) }} <b>必填</b></span>
                            <div
                              v-if="(item.correction_evidence ?? []).length && !eventCheckCreatesEvent(item)"
                              class="correction-evidence"
                            >
                              <button
                                v-for="evidence in item.correction_evidence"
                                :key="evidence.id"
                                type="button"
                                @click.stop="focusEvidence(evidence)"
                              >
                                “{{ evidence.quote }}” · {{ evidenceRangeLabel(evidence) }}
                                <i v-if="!isReadOnly" @click.stop="removeCorrectionEvidence(item, evidence.id)">×</i>
                              </button>
                            </div>
                            <select
                              v-if="correctionOptions(item).length"
                              v-model="item.correction"
                              :disabled="isReadOnly"
                              @change="updateCorrection(item)"
                            >
                              <option value="" disabled>请选择正确值</option>
                              <option
                                v-for="option in correctionOptions(item)"
                                :key="option.value"
                                :value="option.value"
                              >{{ option.label }}</option>
                            </select>
                            <input
                              v-else
                              v-model="item.correction"
                              :type="correctionDictionaryKind(item) ? 'search' : 'text'"
                              :list="correctionListId(item)"
                              :disabled="isReadOnly"
                              :maxlength="item.id.startsWith('relation:') && item.path.endsWith('.relation_summary') ? 10 : 4000"
                              :placeholder="correctionDictionaryKind(item) === 'era'
                                ? '输入年号或朝代搜索'
                                : correctionDictionaryKind(item) === 'historical-event'
                                  ? '输入事件名或年代搜索'
                                  : item.value_kind === 'evidence'
                                    ? '在左侧划选正确原文；无法划选时再手填称呼'
                                    : (item.correction_hint || `填写正确的${item.field_label || '内容'}`)"
                              @input="markDirty"
                            />
                            <section
                              v-if="eventCheckCreatesEvent(item)"
                              class="event-correction-card"
                            >
                              <header>
                                <div>
                                  <span>新增完整事件</span>
                                  <strong>{{ eventTypeForCheck(item) }}</strong>
                                </div>
                                <small>该卡片将直接写入人物生平事件</small>
                              </header>

                              <div class="event-correction-grid">
                                <label>
                                  <span>事件类型</span>
                                  <input :value="eventTypeForCheck(item) || ''" type="text" disabled />
                                </label>
                                <label>
                                  <span>事实状态</span>
                                  <select
                                    v-model="item.correction_data.state"
                                    :disabled="isReadOnly"
                                    @change="updateEventCorrection(item)"
                                  >
                                    <option v-for="option in factStateValueOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                                  </select>
                                </label>
                              </div>

                              <template v-if="item.correction_data.time && item.correction_data.location">
                                <div class="event-correction-card__section-heading">
                                  <strong>时间</strong>
                                  <small v-if="eventTypeForCheck(item) === '籍贯'">籍贯事件固定为不适用</small>
                                </div>
                                <label>
                                  <span>时间状态</span>
                                  <select
                                    v-model="item.correction_data.time.state"
                                    :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'"
                                    @change="updateEventCorrection(item)"
                                  >
                                    <option v-for="option in fieldStateValueOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                                  </select>
                                </label>
                                <template v-if="!fieldStateHidesDetails(item.correction_data.time.state)">
                                <label>
                                  <span>原文时间</span>
                                  <input
                                    v-model.trim="item.correction_data.time.raw"
                                    :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'"
                                    maxlength="255"
                                    placeholder="如：开元二十年、明年"
                                    @input="updateEventCorrection(item)"
                                  />
                                </label>
                                <div class="event-correction-grid event-correction-grid--three">
                                  <label>
                                    <span>年号</span>
                                    <input
                                      v-model.trim="item.correction_data.time.era"
                                      list="review-era-options"
                                      :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'"
                                      maxlength="64"
                                      placeholder="输入年号或朝代搜索"
                                      @input="updateEventCorrection(item)"
                                    />
                                  </label>
                                  <label>
                                    <span>年号内年</span>
                                    <input
                                      :value="item.correction_data.time.era_year ?? ''"
                                      type="number"
                                      min="1"
                                      max="999"
                                      :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'"
                                      @input="setEventCorrectionNumber(item, 'era_year', $event)"
                                    />
                                  </label>
                                  <label>
                                    <span>公元年</span>
                                    <input
                                      :value="item.correction_data.time.gregorian_year ?? ''"
                                      type="number"
                                      :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'"
                                      @input="setEventCorrectionNumber(item, 'gregorian_year', $event)"
                                    />
                                  </label>
                                </div>
                                <small v-if="selectedEventCorrectionEra(item)" class="event-correction-card__dictionary-hint">
                                  已选 {{ selectedEventCorrectionEra(item)?.dynasty }} ·
                                  {{ selectedEventCorrectionEra(item)?.start_year }}—{{ selectedEventCorrectionEra(item)?.end_year }}
                                </small>
                                <div class="event-correction-grid">
                                  <label><span>月原文</span><input v-model.trim="item.correction_data.time.month_text" :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'" maxlength="32" @input="updateEventCorrection(item)" /></label>
                                  <label><span>日原文</span><input v-model.trim="item.correction_data.time.day_text" :disabled="isReadOnly || eventTypeForCheck(item) === '籍贯'" maxlength="32" @input="updateEventCorrection(item)" /></label>
                                </div>
                                </template>

                                <div class="event-correction-card__section-heading"><strong>地点</strong></div>
                                <label>
                                  <span>地点状态</span>
                                  <select v-model="item.correction_data.location.state" :disabled="isReadOnly" @change="updateEventCorrection(item)">
                                    <option v-for="option in fieldStateValueOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                                  </select>
                                </label>
                                <template v-if="!fieldStateHidesDetails(item.correction_data.location.state)">
                                <label><span>原文地点</span><input v-model.trim="item.correction_data.location.raw" :disabled="isReadOnly" maxlength="255" @input="updateEventCorrection(item)" /></label>
                                <div class="event-correction-grid event-correction-grid--three">
                                  <label><span>道</span><input v-model.trim="item.correction_data.location.dao" :disabled="isReadOnly" maxlength="64" @input="updateEventCorrection(item)" /></label>
                                  <label><span>府</span><input v-model.trim="item.correction_data.location.fu" :disabled="isReadOnly" maxlength="64" @input="updateEventCorrection(item)" /></label>
                                  <label><span>州</span><input v-model.trim="item.correction_data.location.zhou" :disabled="isReadOnly" maxlength="64" @input="updateEventCorrection(item)" /></label>
                                  <label><span>郡</span><input v-model.trim="item.correction_data.location.jun" :disabled="isReadOnly" maxlength="64" @input="updateEventCorrection(item)" /></label>
                                  <label><span>县</span><input v-model.trim="item.correction_data.location.xian" :disabled="isReadOnly" maxlength="64" @input="updateEventCorrection(item)" /></label>
                                  <label><span>其他</span><input v-model.trim="item.correction_data.location.other" :disabled="isReadOnly" maxlength="128" @input="updateEventCorrection(item)" /></label>
                                </div>
                                </template>
                              </template>

                              <label v-if="eventTypeForCheck(item) === '任职'">
                                <span>完整官职 <b>必填</b></span>
                                <input
                                  v-model.trim="item.correction_data.official_title"
                                  :disabled="isReadOnly"
                                  maxlength="255"
                                  placeholder="保留行、守、试、检校、兼、赠等限定词"
                                  @input="updateEventCorrection(item)"
                                />
                              </label>
                              <label>
                                <span>事件备注</span>
                                <textarea v-model.trim="item.correction_data.note" :disabled="isReadOnly" rows="2" maxlength="1000" @input="updateEventCorrection(item)" />
                              </label>

                              <div
                                class="event-fact-evidence"
                                :class="{ missing: !(item.correction_evidence ?? []).length }"
                              >
                                <div class="event-fact-evidence__heading">
                                  <span>事件证据 <b>必填</b></span>
                                  <small>{{ (item.correction_evidence ?? []).length }} 处</small>
                                </div>
                                <div v-if="(item.correction_evidence ?? []).length" class="correction-evidence">
                                  <button
                                    v-for="evidence in item.correction_evidence"
                                    :key="evidence.id"
                                    type="button"
                                    @click.stop="focusEvidence(evidence)"
                                  >
                                    “{{ evidence.quote }}” · {{ evidenceRangeLabel(evidence) }}
                                    <i v-if="!isReadOnly" @click.stop="removeCorrectionEvidence(item, evidence.id)">×</i>
                                  </button>
                                </div>
                                <div class="event-fact-evidence__entry">
                                  <input
                                    v-model="correctionEvidenceDrafts[item.id]"
                                    type="text"
                                    maxlength="64"
                                    :disabled="isReadOnly"
                                    placeholder="粘贴一处原文；重复文字请直接在左侧划选"
                                    @keydown.enter.prevent="addTypedCorrectionEvidence(item)"
                                  />
                                  <button type="button" :disabled="isReadOnly" @click="addTypedCorrectionEvidence(item)">加入证据</button>
                                </div>
                                <small v-if="correctionEvidenceFeedback[item.id]" class="event-fact-evidence__error">{{ correctionEvidenceFeedback[item.id] }}</small>
                                <small v-else>也可在左侧划选后点击“绑定为该事件的事实证据”。</small>
                              </div>
                              <small v-if="eventCorrectionProblem(item)" class="event-correction-card__error">{{ eventCorrectionProblem(item) }}</small>
                            </section>
                            <div
                              v-if="eventCheckRequiresEvidence(item) && !eventCheckCreatesEvent(item)"
                              class="event-fact-evidence"
                              :class="{ missing: !(item.correction_evidence ?? []).length }"
                            >
                              <div class="event-fact-evidence__heading">
                                <span>事实证据 <b>必填</b></span>
                                <small>{{ (item.correction_evidence ?? []).length }} 处</small>
                              </div>
                              <div class="event-fact-evidence__entry">
                                <input
                                  v-model="correctionEvidenceDrafts[item.id]"
                                  type="text"
                                  maxlength="64"
                                  :disabled="isReadOnly"
                                  placeholder="粘贴一处原文；重复文字请直接在左侧划选"
                                  @keydown.enter.prevent="addTypedCorrectionEvidence(item)"
                                />
                                <button
                                  type="button"
                                  :disabled="isReadOnly"
                                  @click="addTypedCorrectionEvidence(item)"
                                >加入证据</button>
                              </div>
                              <small v-if="correctionEvidenceFeedback[item.id]" class="event-fact-evidence__error">
                                {{ correctionEvidenceFeedback[item.id] }}
                              </small>
                              <small v-else>
                                可粘贴唯一出现的原文，或在左侧拖动划选后点击“绑定为该事件的事实证据”。
                              </small>
                            </div>
                            <small v-if="correctionDictionaryKind(item) === 'era'" class="dictionary-correction-hint">
                              <template v-if="dictionariesLoading">正在加载年号字典…</template>
                              <template v-else-if="selectedEra(item)">
                                已选 {{ selectedEra(item)?.dynasty }} · {{ selectedEra(item)?.start_year }}—{{ selectedEra(item)?.end_year }}
                              </template>
                              <template v-else>{{ dictionaryFeedback || `可搜索 ${dictionaries.eras.length} 条年号记录，正确值将保存标准年号名。` }}</template>
                            </small>
                            <small v-else-if="correctionDictionaryKind(item) === 'historical-event'" class="dictionary-correction-hint">
                              <template v-if="dictionariesLoading">正在加载历史事件字典…</template>
                              <template v-else-if="selectedHistoricalEvent(item)">
                                已选 {{ selectedHistoricalEvent(item)?.year_label }}
                                <span v-if="selectedHistoricalEvent(item)?.event_details">· {{ selectedHistoricalEvent(item)?.event_details }}</span>
                              </template>
                              <template v-else>{{ dictionaryFeedback || `可搜索 ${dictionaries.historical_events.length} 条事件记录；字典外事件请同时将“名称表外事件”改为“是”。` }}</template>
                            </small>
                            <small v-else-if="!eventCheckCreatesEvent(item)">
                              {{ item.value_kind === 'evidence'
                                ? '请在左侧原文划选。系统会自动记录原文和起止位置，不必手填索引。'
                                : correctionOptions(item).length
                                  ? '该字段使用与抽取标注页一致的受控取值。'
                                  : (item.correction_hint || '按标注页同一写法填写正确内容。') }}
                            </small>
                          </div>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </article>
            </section>
          </div>
        </section>

        <aside class="review-rail">
          <div class="review-rail__scroll">
            <section class="score-section">
              <span>03 / REVIEW SUMMARY</span>
              <div class="score-ring" :style="{ '--score': `${liveSummary.accuracy * 360}deg` }">
                <strong>{{ percent(liveSummary.accuracy) }}</strong>
                <small>首轮正确率</small>
              </div>
              <dl>
                <div><dt>正确</dt><dd>{{ liveSummary.correct }}</dd></div>
                <div><dt>错误</dt><dd>{{ liveSummary.incorrect }}</dd></div>
                <div><dt>未判断</dt><dd>{{ liveSummary.unreviewed }}</dd></div>
              </dl>
              <p>正确率只统计已标注项目；覆盖检查和缺漏单独登记、单独统计。</p>
            </section>

            <section v-if="activeStep === 'checks'" class="coverage-section">
              <header>
                <span>覆盖检查</span>
                <strong>{{ coverageItems.filter((item) => item.verdict !== 'unreviewed').length }} / {{ coverageItems.length }}</strong>
              </header>
              <p>按人物、事件、关系和异常逐项回看原文。发现缺漏时，在下方为每条缺漏登记类型、对象、原文证据和应补位置。</p>
              <article v-for="item in coverageItems" :key="item.id" class="coverage-item">
                <strong>{{ item.field_label }}</strong>
                <small>{{ item.question }}</small>
                <div>
                  <button type="button" :class="{ selected: item.verdict === 'correct' }" :disabled="isReadOnly" @click="markCoverage(item, false)">无缺漏</button>
                  <button type="button" :class="{ selected: item.verdict === 'incorrect' }" :disabled="isReadOnly" @click="addCoverageGap(item)">有缺漏，登记</button>
                </div>
                <small v-if="item.verdict === 'incorrect' && !hasRecordedCoverageGap(item)" class="coverage-item__warning">请在下方完成对应缺漏记录。</small>
              </article>
            </section>

            <section class="omission-section">
              <header>
                <div>
                  <span>缺漏与结构增补</span>
                  <strong>{{ review.omissions.length }}</strong>
                </div>
                <select :disabled="isReadOnly" aria-label="选择新增记录类型" @change="quickAdd">
                  <option value="">＋ 新增…</option>
                  <option v-for="option in additionTypeOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </header>
              <p v-if="!review.omissions.length" class="empty">通读正文发现缺漏时，逐条登记；也可划选原文后直接补建结构化记录。</p>
              <article
                v-for="(omission, index) in review.omissions"
                :key="omission.id"
                class="omission-card"
                :class="{ active: activeOmissionId === omission.id }"
                @click="activeOmissionId = omission.id"
              >
                <header>
                  <b>{{ omission.category === '结构增补' ? '结构增补' : '缺漏' }} {{ index + 1 }}</b>
                  <button type="button" :disabled="isReadOnly" @click.stop="removeOmission(omission.id)">删除</button>
                </header>
                <label>
                  <span>缺漏类型 <b>必填</b></span>
                  <select v-model="omission.category" :disabled="isReadOnly" @change="markDirty">
                    <option v-for="category in omissionCategoryOptions" :key="category" :value="category">{{ category }}</option>
                  </select>
                </label>
                <label>
                  <span>新增类型</span>
                  <select :value="omission.addition_type" :disabled="isReadOnly" @change="changeAdditionType(omission, $event)">
                    <option v-for="option in additionTypeOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <template v-if="omission.addition_type === 'person'">
                  <label>
                    <span>原文姓名 <b>必填</b></span>
                    <input v-model.trim="omission.data.name_surface" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" />
                  </label>
                  <div class="addition-grid">
                    <label>
                      <span>人物等级</span>
                      <select v-model.number="omission.data.level" :disabled="isReadOnly" @change="updateAddition(omission)">
                        <option :value="1">1 级</option><option :value="2">2 级</option><option :value="3">3 级</option>
                      </select>
                    </label>
                    <label>
                      <span>篇内补全姓名</span>
                      <input v-model.trim="omission.data.completed_name" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" />
                    </label>
                  </div>
                  <label>
                    <span>分级理由 <b>必填</b></span>
                    <textarea v-model.trim="omission.data.level_reason" :disabled="isReadOnly" rows="2" maxlength="500" @input="updateAddition(omission)" />
                  </label>
                  <div class="addition-grid">
                    <label><span>字</span><input v-model.trim="omission.data.courtesy_name" :disabled="isReadOnly" maxlength="128" @input="updateAddition(omission)" /></label>
                    <label><span>号</span><input v-model.trim="omission.data.hao" :disabled="isReadOnly" maxlength="128" @input="updateAddition(omission)" /></label>
                  </div>
                  <label>
                    <span>称号（顿号分隔）</span>
                    <input :value="omission.data.titles?.join('、') || ''" :disabled="isReadOnly" @input="updateAdditionTitles(omission, $event)" />
                  </label>
                </template>

                <template v-else-if="omission.addition_type === 'life_event'">
                  <label>
                    <span>所属人物 <b>必填</b></span>
                    <select v-model="omission.parent_key" :disabled="isReadOnly" @change="updateAddition(omission)">
                      <option value="" disabled>请选择人物</option>
                      <option v-for="person in reviewFactPersonOptions" :key="person.value" :value="person.value">{{ person.label }}</option>
                    </select>
                  </label>
                  <div class="addition-grid">
                    <label>
                      <span>事件类型</span>
                      <select :value="omission.data.event_type" :disabled="isReadOnly" @change="changeLifeEventType(omission, $event)">
                        <option v-for="eventType in EVENT_TYPES" :key="eventType">{{ eventType }}</option>
                      </select>
                    </label>
                    <label>
                      <span>事实状态</span>
                      <select v-model="omission.data.state" :disabled="isReadOnly" @change="updateAddition(omission)">
                        <option v-for="option in factStateValueOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                      </select>
                    </label>
                  </div>
                  <label v-if="omission.data.event_type === '任职'">
                    <span>官职 <b>必填</b></span>
                    <input v-model.trim="omission.data.official_title" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" />
                  </label>
                  <template v-if="omission.data.time && omission.data.location">
                    <div class="addition-subheading">时间</div>
                    <label>
                      <span>时间状态</span>
                      <select v-model="omission.data.time.state" :disabled="isReadOnly" @change="updateAddition(omission)">
                        <option v-for="option in fieldStateValueOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                      </select>
                    </label>
                    <template v-if="!fieldStateHidesDetails(omission.data.time.state)">
                    <label><span>原文时间</span><input v-model.trim="omission.data.time.raw" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" /></label>
                    <div class="addition-grid addition-grid--three">
                      <label><span>年号</span><input v-model.trim="omission.data.time.era" list="review-era-options" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                      <label><span>年次</span><input v-model.number="omission.data.time.era_year" type="number" min="1" max="999" :disabled="isReadOnly" @input="updateAddition(omission)" /></label>
                      <label><span>公元年</span><input v-model.number="omission.data.time.gregorian_year" type="number" :disabled="isReadOnly" @input="updateAddition(omission)" /></label>
                    </div>
                    <div class="addition-grid">
                      <label><span>月</span><input v-model.trim="omission.data.time.month_text" :disabled="isReadOnly" maxlength="32" @input="updateAddition(omission)" /></label>
                      <label><span>日</span><input v-model.trim="omission.data.time.day_text" :disabled="isReadOnly" maxlength="32" @input="updateAddition(omission)" /></label>
                    </div>
                    </template>
                    <div class="addition-subheading">地点</div>
                    <label>
                      <span>地点状态</span>
                      <select v-model="omission.data.location.state" :disabled="isReadOnly" @change="updateAddition(omission)">
                        <option v-for="option in fieldStateValueOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                      </select>
                    </label>
                    <template v-if="!fieldStateHidesDetails(omission.data.location.state)">
                    <label><span>原文地点</span><input v-model.trim="omission.data.location.raw" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" /></label>
                    <div class="addition-grid addition-grid--three">
                      <label><span>道</span><input v-model.trim="omission.data.location.dao" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                      <label><span>府</span><input v-model.trim="omission.data.location.fu" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                      <label><span>州</span><input v-model.trim="omission.data.location.zhou" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                      <label><span>郡</span><input v-model.trim="omission.data.location.jun" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                      <label><span>县</span><input v-model.trim="omission.data.location.xian" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                      <label><span>其他</span><input v-model.trim="omission.data.location.other" :disabled="isReadOnly" maxlength="128" @input="updateAddition(omission)" /></label>
                    </div>
                    </template>
                  </template>
                  <label><span>备注</span><textarea v-model.trim="omission.data.note" :disabled="isReadOnly" rows="2" maxlength="1000" @input="updateAddition(omission)" /></label>
                </template>

                <template v-else-if="omission.addition_type === 'historical_event'">
                  <label>
                    <span>所属人物 <b>必填</b></span>
                    <select v-model="omission.parent_key" :disabled="isReadOnly" @change="updateAddition(omission)">
                      <option value="" disabled>请选择人物</option>
                      <option v-for="person in reviewFactPersonOptions" :key="person.value" :value="person.value">{{ person.label }}</option>
                    </select>
                  </label>
                  <label><span>历史事件名称 <b>必填</b></span><input v-model.trim="omission.data.event_name" list="review-history-options" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" /></label>
                  <label><span>人物与事件关系（≤15 字） <b>必填</b></span><input v-model.trim="omission.data.relation_summary" :disabled="isReadOnly" maxlength="15" @input="updateAddition(omission)" /></label>
                  <label class="check-label"><input v-model="omission.data.outside_dictionary" type="checkbox" :disabled="isReadOnly" @change="updateAddition(omission)" /><span>名称不在历史事件字典中</span></label>
                  <label><span>备注</span><textarea v-model.trim="omission.data.note" :disabled="isReadOnly" rows="2" maxlength="1000" @input="updateAddition(omission)" /></label>
                </template>

                <template v-else-if="omission.addition_type === 'person_relation'">
                  <div class="addition-grid">
                    <label><span>人物一 <b>必填</b></span><select v-model="omission.data.source_person_key" :disabled="isReadOnly" @change="updateAddition(omission)"><option value="" disabled>请选择</option><option v-for="person in reviewPersonOptions" :key="person.value" :value="person.value">{{ person.label }}</option></select></label>
                    <label><span>人物二 <b>必填</b></span><select v-model="omission.data.target_person_key" :disabled="isReadOnly" @change="updateAddition(omission)"><option value="" disabled>请选择</option><option v-for="person in reviewPersonOptions" :key="person.value" :value="person.value">{{ person.label }}</option></select></label>
                  </div>
                  <label><span>关系类别 <b>必填</b></span><select v-model="omission.data.relation_category" :disabled="isReadOnly" @change="updateAddition(omission)"><option value="" disabled>请选择七类之一</option><option v-for="option in relationCategoryOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
                  <label><span>具体表现（不超过 10 字） <b>必填</b></span><input v-model.trim="omission.data.relation_summary" :disabled="isReadOnly" maxlength="10" placeholder="例如 父子、共同修史" @input="updateAddition(omission)" /></label>
                </template>

                <template v-else-if="omission.addition_type === 'evidence'">
                  <label>
                    <span>证据所属记录 <b>必填</b></span>
                    <select :value="evidenceTargetValue(omission)" :disabled="isReadOnly" @change="setEvidenceTarget(omission, $event)">
                      <option value=":" disabled>请选择人物、事件、关系或异常项</option>
                      <option v-for="target in evidenceTargets" :key="target.value" :value="target.value">{{ target.label }}</option>
                    </select>
                  </label>
                </template>

                <template v-else-if="omission.addition_type === 'excluded_mention'">
                  <label><span>排除原因 <b>必填</b></span><input v-model.trim="omission.data.reason" :disabled="isReadOnly" maxlength="255" @input="updateAddition(omission)" /></label>
                </template>
                <template v-else-if="omission.addition_type === 'unresolved_item'">
                  <label><span>分类 <b>必填</b></span><input v-model.trim="omission.data.category" :disabled="isReadOnly" maxlength="64" @input="updateAddition(omission)" /></label>
                  <label><span>说明 <b>必填</b></span><textarea v-model.trim="omission.data.note" :disabled="isReadOnly" rows="2" maxlength="1000" @input="updateAddition(omission)" /></label>
                </template>
                <template v-else-if="omission.addition_type === 'schema_conflict'">
                  <label><span>限制代码 <b>必填</b></span><input v-model.trim="omission.data.code" :disabled="isReadOnly" maxlength="64" placeholder="例如 multiple_protagonists" @input="updateAddition(omission)" /></label>
                  <label><span>说明 <b>必填</b></span><textarea v-model.trim="omission.data.note" :disabled="isReadOnly" rows="2" maxlength="1000" @input="updateAddition(omission)" /></label>
                </template>

                <template v-else>
                  <label><span>应补入步骤和位置 <b>必填</b></span><input v-model.trim="omission.target_path" :disabled="isReadOnly" maxlength="255" placeholder="例如 人物基本信息 / 李某 / 任职" @input="markDirty" /></label>
                  <label><span>缺漏对象和具体内容 <b>必填</b></span><textarea v-model.trim="omission.description" :disabled="isReadOnly" rows="2" maxlength="2000" @input="markDirty" /></label>
                  <label><span>应补录内容 <b>必填</b></span><textarea v-model.trim="omission.correction" :disabled="isReadOnly" rows="2" maxlength="4000" @input="markDirty" /></label>
                </template>

                <small v-if="additionProblem(omission)" class="addition-problem">{{ additionProblem(omission) }}</small>
                <div class="omission-evidence">
                  <span>原文证据</span>
                  <button
                    v-for="evidence in omission.evidence"
                    :key="evidence.id"
                    type="button"
                    @click.stop="activeEvidenceId = evidence.id; textCanvas?.focusEvidence(evidence.id)"
                  >
                    “{{ evidence.quote }}”
                    <i v-if="!isReadOnly" @click.stop="removeOmissionEvidence(omission.id, evidence.id)">×</i>
                  </button>
                  <small v-if="!omission.evidence.length">请在左侧原文划选；确实无法划选时填写下方说明。</small>
                </div>
                <label>
                  <span>无可划选证据时说明</span>
                  <textarea v-model.trim="omission.evidence_note" :disabled="isReadOnly" rows="2" maxlength="1000" placeholder="例如：事实仅见标题或原文残缺。" @input="markDirty" />
                </label>
              </article>
            </section>

            <section class="standard-section">
              <header>
                <div>
                  <span>完整检查规范</span>
                  <strong>{{ review.standards.length }} 条</strong>
                </div>
                <small>{{ review.standard_version }}</small>
              </header>
              <ol>
                <li
                  v-for="standard in review.standards"
                  :key="standard.id"
                  :class="{ related: relatedStandardIds.has(standard.id) }"
                >
                  <b>{{ standard.id }}</b>
                  <div>
                    <strong>{{ standard.title }}</strong>
                    <p>{{ standard.instruction }}</p>
                  </div>
                </li>
              </ol>
            </section>
          </div>

          <footer class="submit-bar">
            <p v-if="!review.editable">
              {{ review.blind_submitted_at ? `盲标已于 ${timeLabel(review.blind_submitted_at)} 提交` : '盲标已提交' }}，质检记录已锁定。
            </p>
            <p v-else-if="submissionProblems.length">{{ submissionProblems.join('；') }}。</p>
            <p v-else-if="review.status === 'submitted'">质检已于 {{ timeLabel(review.submitted_at) }} 提交；盲标提交前仍可修改。</p>
            <p v-else-if="review.submitted_at">上次质检提交于 {{ timeLabel(review.submitted_at) }}；当前修改已保存，请重新提交。</p>
            <p v-else>全部检查完成，可以提交首轮质检。</p>
            <button
              type="button"
              :disabled="isReadOnly || Boolean(submissionProblems.length) || submitting"
              @click="submitReview"
            >{{ submitting
              ? '提交中…'
              : isReadOnly
                ? '盲标已提交'
                : review.status === 'submitted'
                  ? '再次提交质检'
                  : review.submitted_at
                    ? '重新提交质检'
                    : '提交首轮质检' }}</button>
            <small>{{ feedback || `最近保存：${timeLabel(review.updated_at)}` }}</small>
          </footer>
        </aside>
      </div>
      <datalist id="review-era-options">
        <option
          v-for="item in dictionaries.eras"
          :key="`${item.dynasty}-${item.era}`"
          :value="item.era"
        >{{ item.dynasty }} · {{ item.start_year }}—{{ item.end_year }}</option>
      </datalist>
      <datalist id="review-history-options">
        <option
          v-for="item in dictionaries.historical_events"
          :key="item.id"
          :value="item.event_name"
        >{{ item.year_label }} · {{ item.event_details }}</option>
      </datalist>
    </div>
  </AppLayout>
</template>

<style scoped>
.review-workbench {
  --blue: #2f6fed;
  --blue-dark: #244f9e;
  --ink: #293b60;
  --muted: #7183a8;
  --line: #dde6f3;
  --paper: #fff;
  height: 100vh;
  overflow: hidden;
  background: #f5f8fd;
  color: #44536f;
}

.generation-links {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  padding: 8px 22px;
  border-bottom: 1px solid #e1e8f3;
  background: #f8fbff;
  color: var(--muted);
  font-size: 10px;
}
.generation-links a { color: var(--blue); text-decoration: none; }
.generation-links a:hover { text-decoration: underline; }
.review-workbench:has(.generation-links) .review-body { height: calc(100vh - 176px); }

.review-topbar {
  height: 78px;
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(230px, .7fr) 170px auto;
  align-items: center;
  gap: 24px;
  box-sizing: border-box;
  padding: 11px 22px;
  border-bottom: 1px solid #e1e8f3;
  background: #fff;
  box-shadow: 0 8px 24px rgb(70 97 145 / 7%);
}

.review-title { display: flex; align-items: center; gap: 12px; min-width: 0; }
.review-title > a { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid var(--line); border-radius: 9px; color: var(--blue); font-size: 18px; text-decoration: none; }
.review-title div { min-width: 0; }
.review-title span, .column-heading span, .score-section > span { color: var(--blue); font: 700 9px/1.2 ui-monospace, monospace; letter-spacing: .08em; }
.review-title h2 { overflow: hidden; margin: 4px 0 0; color: var(--ink); font-size: 18px; text-overflow: ellipsis; white-space: nowrap; }

.review-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; padding: 7px 18px; border-bottom: 1px solid #e1e8f3; background: #fff; }
.review-steps button { display: flex; align-items: center; gap: 9px; min-width: 0; border: 1px solid #edc0c3; border-radius: 8px; padding: 8px 10px; background: #fff7f7; color: #a93d45; text-align: left; cursor: pointer; }
.review-steps button.complete { border-color: #a8d5bd; background: #effaf3; color: #247552; }
.review-steps button.active { box-shadow: 0 0 0 2px currentColor inset; }
.review-steps button:disabled { opacity: .6; cursor: not-allowed; }
.review-steps b { font: 700 11px ui-monospace, monospace; }
.review-steps span { min-width: 0; overflow: hidden; font-size: 11px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.review-steps small { margin-left: auto; font-size: 9px; white-space: nowrap; }

.review-progress { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; }
.review-progress div { display: grid; }
.review-progress span, .accuracy-block span { color: var(--muted); font-size: 9px; }
.review-progress strong { color: var(--ink); font-size: 12px; }
.review-progress i { height: 5px; overflow: hidden; border-radius: 999px; background: #edf2fa; }
.review-progress i b { display: block; height: 100%; border-radius: inherit; background: var(--blue); transition: width 180ms ease; }
.review-progress small { color: var(--blue); font-size: 10px; }
.accuracy-block { display: grid; border-left: 1px solid var(--line); padding-left: 18px; }
.accuracy-block strong { color: var(--blue); font-size: 22px; line-height: 1; }
.accuracy-block small { margin-top: 3px; color: var(--muted); font-size: 8px; white-space: nowrap; }
.top-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
.top-actions > a { border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; color: var(--blue); font-size: 10px; text-decoration: none; }
.state { color: #8b6a29; font-size: 9px; white-space: nowrap; }
.state[data-state='submitted'] { color: #258660; }

.review-body { height: calc(100vh - 134px); display: grid; grid-template-columns: minmax(340px, .9fr) minmax(470px, 1.18fr) 320px; min-width: 0; }
.source-column, .check-column, .review-rail { min-width: 0; min-height: 0; position: relative; border-right: 1px solid var(--line); background: var(--paper); }
.check-column { display: grid; grid-template-rows: auto auto 1fr; background: #f9fbfe; }
.review-rail { display: grid; grid-template-rows: 1fr auto; border-right: 0; }
.column-heading { min-height: 67px; display: flex; align-items: center; justify-content: space-between; gap: 14px; box-sizing: border-box; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.column-heading h3 { margin: 4px 0 0; color: var(--ink); font-size: 15px; }
.column-heading small { color: var(--muted); font: 9px ui-monospace, monospace; }
.check-heading b { color: #a2701f; font-size: 10px; }

.source-legend { height: 34px; display: flex; align-items: center; gap: 12px; padding: 0 18px; border-bottom: 1px solid #eef2f8; color: var(--muted); font-size: 9px; }
.source-legend span { display: flex; align-items: center; gap: 4px; }
.source-legend i { width: 7px; height: 7px; border-radius: 2px; background: #2f6fed; }
.source-legend i[data-kind='event'] { background: #3f7280; }
.source-legend i[data-kind='history'] { background: #78639a; }
.source-legend i[data-kind='relation'] { background: #9a702f; }
.source-legend i[data-kind='unresolved'] { background: #b56b2d; }
.source-legend i[data-kind='located'] { background: #ffcd2e; box-shadow: 0 0 0 1px #b45309; }
.source-scroll { height: calc(100% - 102px); overflow: auto; }

.evidence-dock { position: absolute; right: 16px; bottom: 16px; left: 16px; z-index: 3; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 12px 40px 12px 14px; border: 1px solid #bcd0f6; border-radius: 12px; background: #23375f; color: #fff; box-shadow: 0 14px 36px rgb(26 45 79 / 25%); }
.evidence-dock > button:first-child { position: absolute; top: 5px; right: 8px; border: 0; background: none; color: #b9c7e3; cursor: pointer; }
.evidence-dock > div { min-width: 0; display: grid; gap: 3px; }
.evidence-dock span { color: #aebedd; font-size: 9px; }
.evidence-dock strong { overflow: hidden; font: 500 13px 'Noto Serif SC', serif; text-overflow: ellipsis; white-space: nowrap; }
.evidence-dock small { color: #ffb8aa; font-size: 9px; }
.evidence-dock > div:last-child { display: flex; flex-flow: row wrap; align-items: center; justify-content: flex-end; gap: 6px; }
.evidence-dock > div:last-child button { border: 1px solid #7196df; border-radius: 7px; padding: 7px 8px; background: #2f6fed; color: #fff; font-size: 9px; cursor: pointer; }
.evidence-dock > div:last-child button + button { background: transparent; }
.evidence-dock button:disabled { opacity: .4; cursor: not-allowed; }

.check-tools { display: grid; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); background: #fff; }
.check-tools label { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 9px; }
.check-tools label span { color: var(--muted); font-size: 9px; }
.check-tools input { min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px; outline: none; color: var(--ink); font-size: 10px; }
.check-tools input:focus { border-color: #9db9ef; box-shadow: 0 0 0 3px #eef4ff; }
.check-tools nav { display: flex; gap: 4px; overflow-x: auto; }
.check-tools nav button { flex: 0 0 auto; display: grid; gap: 1px; border: 0; border-radius: 7px; padding: 5px 8px; background: #f1f5fb; color: var(--muted); font-size: 9px; cursor: pointer; }
.check-tools nav button small { color: #8b9bb8; font-size: 8px; }
.check-tools nav button.active { background: #e4edff; color: var(--blue); font-weight: 700; }
.check-tools nav button.active small { color: var(--blue); }
.offset-guide { margin: 0; padding: 8px 9px; border-left: 2px solid #9bb8ee; background: #f6f9ff; color: #60708c; font-size: 9px; line-height: 1.55; }
.offset-guide b { margin-right: 5px; color: #365f9f; }
.check-scroll, .review-rail__scroll { min-height: 0; overflow: auto; }
.check-scroll { padding: 12px; }
.field-group { margin-bottom: 14px; }
.field-group__heading { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.field-group__heading span { color: var(--blue); font-size: 8px; font-weight: 700; }
.field-group__heading h4 { margin: 2px 0 0; color: var(--ink); font-size: 14px; }
.field-group__heading small { color: var(--muted); font: 9px ui-monospace, monospace; }
.field-group__actions { display: flex; align-items: center; gap: 7px; }
.field-group__actions button { border: 1px solid #e1a2a6; border-radius: 6px; padding: 4px 7px; background: #fff7f7; color: #b54d55; font-size: 8px; cursor: pointer; }
.field-group__actions button.restore { border-color: #a8c8b8; background: #f1faf5; color: #267653; }
.field-group__actions button:disabled { opacity: .45; cursor: default; }
.field-section { margin-bottom: 10px; overflow: hidden; border: 1px solid var(--line); border-radius: 10px; background: #fff; }
.field-section > header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; border-bottom: 1px solid #eef2f8; background: #f8fbff; }
.field-section > header strong { color: var(--ink); font-size: 11px; }
.field-section > header div { display: flex; align-items: center; gap: 8px; }
.field-section > header small { color: var(--muted); font: 9px ui-monospace, monospace; }
.field-section > header button { border: 1px solid #cddbf2; border-radius: 6px; padding: 3px 7px; background: #f5f8ff; color: var(--blue); font-size: 8px; cursor: pointer; }
.field-section > header button:disabled { opacity: .45; cursor: default; }
.field-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.field-table th, .field-table td { padding: 7px 9px; border-bottom: 1px solid #eef2f8; text-align: left; vertical-align: middle; }
.field-table thead th { color: #8090ab; font-size: 8px; font-weight: 700; background: #fcfdff; }
.field-table thead th:nth-child(1), .field-table tbody th { width: 22%; }
.field-table thead th:nth-child(3), .field-table tbody td:last-child { width: 148px; }
.field-table tbody th { color: #365f9f; font-size: 10px; font-weight: 600; }
.field-table tbody tr { cursor: pointer; }
.field-table tbody tr:hover td, .field-table tbody tr:hover th, .field-table tbody tr.active td, .field-table tbody tr.active th { background: #f4f8ff; }
.quiet-row th { padding: 5px 9px; background: #f6f8fc; }
.quiet-row__bar { display: flex; align-items: center; gap: 8px; min-width: 0; }
.quiet-row__bar > span { min-width: 0; overflow: hidden; color: #7183a8; font-size: 10px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.quiet-row__bar small { flex: 0 0 auto; color: #8b9bb8; font: 9px ui-monospace, monospace; }
.quiet-row__bar button { flex: 0 0 auto; border: 0; background: none; color: var(--blue); font-size: 9px; cursor: pointer; }
.quiet-row__bar button:disabled { opacity: .45; cursor: default; }
.field-table tbody tr[data-verdict='correct'] th { box-shadow: inset 3px 0 0 #3a9b70; }
.field-table tbody tr[data-verdict='incorrect'] th { box-shadow: inset 3px 0 0 #c85a61; }
.ai-cell { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.ai-cell strong { min-width: 0; overflow-wrap: anywhere; color: #263e66; font: 600 11px/1.45 'Noto Serif SC', serif; }
.ai-cell__removed { flex: 0 0 auto; color: #b54d55; font-size: 8px; }
.ai-cell button { flex: 0 0 auto; border: 0; background: none; color: var(--blue); font-size: 8px; cursor: pointer; }
.ai-cell button.active { font-weight: 700; }
.ai-cell button.ai-cell__delete { color: #b54d55; }
.correction-row td { padding: 0 9px 9px 9px; background: #fff8f8; }
.correction-row .reason-field { margin-top: 0; }
.correction-row .reason-field small { color: #8a7753; font-size: 8px; line-height: 1.5; }
.correction-evidence { display: flex; flex-wrap: wrap; gap: 4px; }
.correction-evidence > button { display: flex; align-items: center; gap: 6px; max-width: 100%; border: 1px solid #d8e3f5; border-radius: 6px; padding: 4px 6px; background: #f4f8ff; color: #315f9f; font-size: 9px; cursor: pointer; }
.correction-evidence i { color: #b54d55; font-style: normal; }
.event-fact-evidence { display: grid; gap: 6px; padding: 8px; border: 1px solid #c9d9f2; border-radius: 8px; background: #f7faff; }
.event-fact-evidence.missing { border-color: #e1a2a6; background: #fff7f7; }
.event-fact-evidence__heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.event-fact-evidence__heading small { color: #6078a3 !important; }
.event-fact-evidence__entry { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px; }
.event-fact-evidence__entry button { border: 1px solid #9bb8ee; border-radius: 7px; padding: 0 10px; background: #e8f1ff; color: #2f6fed; font-size: 9px; cursor: pointer; }
.event-fact-evidence__entry button:disabled { opacity: .45; cursor: default; }
.event-fact-evidence__error { color: #b54d55 !important; }
.event-correction-card { display: grid; gap: 8px; margin-top: 2px; padding: 11px; border: 1px solid #b9ccec; border-left: 3px solid var(--blue); border-radius: 9px; background: #f8fbff; }
.event-correction-card > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 7px; border-bottom: 1px solid #dfe8f6; }
.event-correction-card > header div { display: flex; align-items: baseline; gap: 7px; }
.event-correction-card > header span { color: #687a9e; font-size: 8px; font-weight: 700; letter-spacing: .06em; }
.event-correction-card > header strong { color: #285fae; font-size: 12px; }
.event-correction-card > header small { color: #7183a3 !important; text-align: right; }
.event-correction-card label { display: grid; gap: 4px; }
.event-correction-card label > span { color: #687a9e; font-size: 9px; }
.event-correction-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.event-correction-grid--three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.event-correction-card__section-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 3px; padding: 6px 0 3px; border-bottom: 1px solid #dfe8f6; }
.event-correction-card__section-heading strong { color: #315f9f; font-size: 9px; }
.event-correction-card__section-heading small { color: #7485a2 !important; }
.event-correction-card__dictionary-hint { color: #53709e !important; }
.event-correction-card__error { padding: 6px 7px; border-radius: 6px; background: #fff0f1; color: #b54d55 !important; }
.event-correction-card input:disabled, .event-correction-card select:disabled { background: #eef2f7; color: #7a879d; }
.omission-evidence > button { max-width: 100%; overflow: hidden; border: 1px solid #d8e3f5; border-radius: 6px; padding: 4px 6px; background: #f4f8ff; color: #526c9c; font-family: 'Noto Serif SC', serif; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.verdict-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
.verdict-buttons button { border: 1px solid var(--line); border-radius: 7px; padding: 5px 4px; background: #fff; color: var(--muted); font-size: 9px; cursor: pointer; }
.verdict-buttons button:first-child.selected { border-color: #82c6a8; background: #eef9f4; color: #267653; }
.verdict-buttons button:last-child.selected { border-color: #e1a2a6; background: #fff3f3; color: #af414a; }
.verdict-buttons button:disabled { cursor: default; }
.verdict-buttons i { font-style: normal; font-weight: 800; }
.reason-field, .omission-card label { display: grid; gap: 4px; margin-top: 8px; }
.reason-field span, .omission-card label span, .omission-evidence > span { color: #687a9e; font-size: 9px; }
.reason-field b, .omission-card label b { color: #b74c55; }
.reason-field input, .reason-field select, .reason-field textarea, .omission-card textarea, .omission-card input, .omission-card select { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 7px; padding: 7px 8px; outline: none; background: #fff; color: var(--ink); font: inherit; font-size: 10px; line-height: 1.5; resize: vertical; }
.reason-field input:focus, .reason-field select:focus, .reason-field textarea:focus, .omission-card textarea:focus, .omission-card input:focus, .omission-card select:focus { border-color: #9db9ef; box-shadow: 0 0 0 3px #eef4ff; }
.dictionary-correction-hint { display: block; overflow: hidden; color: #53709e !important; text-overflow: ellipsis; white-space: nowrap; }

.review-rail__scroll { padding: 14px; }
.score-section, .coverage-section, .omission-section, .standard-section { padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.coverage-section { margin-top: 15px; }
.coverage-section > header { display: flex; align-items: baseline; justify-content: space-between; }
.coverage-section > header span { color: var(--ink); font-size: 11px; font-weight: 700; }
.coverage-section > header strong { color: var(--blue); font-size: 10px; }
.coverage-section > p { color: var(--muted); font-size: 9px; line-height: 1.55; }
.coverage-item { display: grid; gap: 6px; margin-top: 8px; padding: 9px; border: 1px solid var(--line); border-radius: 8px; background: #fbfcff; }
.coverage-item strong { color: var(--ink); font-size: 10px; }
.coverage-item small { color: var(--muted); font-size: 9px; line-height: 1.5; }
.coverage-item > div { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
.coverage-item button { border: 1px solid var(--line); border-radius: 6px; padding: 5px 3px; background: #fff; color: var(--muted); font-size: 9px; cursor: pointer; }
.coverage-item button:first-child.selected { border-color: #82c6a8; background: #eef9f4; color: #267653; }
.coverage-item button:last-child.selected { border-color: #e1a2a6; background: #fff3f3; color: #af414a; }
.coverage-item button:disabled { cursor: default; }
.coverage-item .coverage-item__warning { color: #af414a; }
.score-ring { --score: 0deg; width: 116px; aspect-ratio: 1; display: grid; place-content: center; margin: 12px auto; border-radius: 50%; background: radial-gradient(circle at center, #fff 61%, transparent 62%), conic-gradient(var(--blue) var(--score), #e9eff8 0); text-align: center; }
.score-ring strong { color: var(--ink); font-size: 21px; }
.score-ring small { color: var(--muted); font-size: 8px; }
.score-section dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin: 0; }
.score-section dl div { padding: 7px 3px; border-radius: 7px; background: #f4f7fb; text-align: center; }
.score-section dt { color: var(--muted); font-size: 8px; }
.score-section dd { margin: 3px 0 0; color: var(--ink); font-size: 13px; font-weight: 700; }
.score-section p { margin: 9px 0 0; color: var(--muted); font-size: 9px; line-height: 1.55; }
.omission-section, .standard-section { padding-top: 15px; }
.omission-section > header, .standard-section > header, .omission-card > header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.omission-section > header div, .standard-section > header div { display: flex; align-items: baseline; gap: 7px; }
.omission-section > header span, .standard-section > header span { color: var(--ink); font-size: 11px; font-weight: 700; }
.omission-section > header strong, .standard-section > header strong { color: var(--blue); font-size: 10px; }
.omission-section > header button, .omission-section > header select { border: 1px solid #cddbf2; border-radius: 7px; padding: 5px 7px; background: #f5f8ff; color: var(--blue); font-size: 9px; cursor: pointer; }
.empty { margin: 10px 0; color: var(--muted); font-size: 10px; line-height: 1.6; }
.omission-card { margin-top: 10px; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: #fbfcff; }
.omission-card.active { border-color: #9db9ef; background: #f7faff; }
.omission-card > header b { color: var(--ink); font-size: 10px; }
.omission-card > header button { border: 0; background: none; color: #b54d55; font-size: 8px; cursor: pointer; }
.addition-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.addition-grid--three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.addition-subheading { margin-top: 10px; padding-bottom: 3px; border-bottom: 1px solid #e8eef7; color: #315f9f; font-size: 9px; font-weight: 700; }
.omission-card label.check-label { display: flex; align-items: center; gap: 6px; }
.omission-card label.check-label input { width: auto; }
.addition-problem { display: block; margin-top: 8px; padding: 6px 7px; border-radius: 6px; background: #fff2f2; color: #b54d55; font-size: 8px; line-height: 1.45; }
.omission-evidence { display: grid; gap: 4px; margin-top: 8px; }
.omission-evidence > button { display: flex; align-items: center; justify-content: space-between; gap: 5px; }
.omission-evidence i { color: #b54d55; font-style: normal; }
.omission-evidence small { color: #9a7a3d; font-size: 8px; line-height: 1.5; }
.standard-section > header small { color: var(--muted); font: 8px ui-monospace, monospace; }
.standard-section ol { display: grid; gap: 7px; margin: 10px 0 0; padding: 0; list-style: none; }
.standard-section li { display: grid; grid-template-columns: 29px 1fr; gap: 7px; padding: 7px 5px; border-left: 2px solid transparent; }
.standard-section li.related { border-left-color: var(--blue); background: #f3f7ff; }
.standard-section li > b { color: #8ca0c2; font: 9px ui-monospace, monospace; }
.standard-section li strong { color: var(--ink); font-size: 10px; }
.standard-section li p { margin: 3px 0 0; color: var(--muted); font-size: 9px; line-height: 1.55; }

.submit-bar { padding: 12px 14px; border-top: 1px solid var(--line); background: #fff; box-shadow: 0 -8px 24px rgb(70 97 145 / 6%); }
.submit-bar p { margin: 0 0 8px; color: #8d6731; font-size: 9px; line-height: 1.5; }
.submit-bar button { width: 100%; border: 0; border-radius: 9px; padding: 9px; background: var(--blue); color: #fff; font-size: 10px; font-weight: 700; cursor: pointer; }
.submit-bar button:disabled { background: #bdc9df; cursor: not-allowed; }
.submit-bar small { display: block; overflow: hidden; margin-top: 6px; color: var(--muted); font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }

.page-state { height: calc(100vh - 78px); display: grid; place-content: center; text-align: center; }
.page-state b { color: var(--ink); font-size: 18px; }
.page-state p { color: var(--muted); }
.page-state a { color: var(--blue); }
.page-state--error b { color: #b54d55; }
.dock-enter-active, .dock-leave-active { transition: opacity 150ms ease, transform 150ms ease; }
.dock-enter-from, .dock-leave-to { opacity: 0; transform: translateY(8px); }

@media (max-width: 1450px) {
  .review-body { grid-template-columns: minmax(320px, 1fr) minmax(420px, 1.1fr) 280px; }
  .review-topbar { grid-template-columns: minmax(260px, 1fr) minmax(190px, .6fr) 150px auto; gap: 15px; }
}

@media (max-width: 1280px) {
  .review-workbench { height: auto; min-height: 100vh; overflow: visible; }
  .review-topbar { height: auto; grid-template-columns: 1fr auto; }
  .review-progress { order: 3; }
  .accuracy-block { order: 4; }
  .review-body { height: auto; grid-template-columns: 1fr; }
  .source-column { height: 70vh; }
  .check-column { height: 80vh; }
  .review-rail { min-height: 80vh; }
}

@media (max-width: 720px) {
  .review-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .review-steps button { padding: 7px 8px; }
  .review-steps small { font-size: 8px; }
  .event-correction-grid, .event-correction-grid--three { grid-template-columns: 1fr; }
}
</style>
