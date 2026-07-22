<script setup lang="ts">
import axios from 'axios'
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'
import { onBeforeRouteLeave, RouterLink } from 'vue-router'

import {
  EVENT_TYPES,
  type AnnotationDictionaries,
  type CheckState,
  type EventType,
  type EvidenceSpan,
  type ExtractionAnnotationLabel,
  type ExtractionTaskDetail,
  type ExtractionTaskSummary,
  type FieldState,
  type PersonAnnotation,
  type PersonRelationAnnotation,
  claimExtractionTask,
  createExtractionTask,
  fetchAnnotationDictionaries,
  fetchExtractionTask,
  listExtractionTasks,
  saveExtractionDraft,
  submitExtractionAnnotation,
} from '../api/extractionAnnotations'
import { getApiErrorMessage } from '../api/client'
import { listPassages, type PassageSummary } from '../api/passages'
import AnnotationTextCanvas from '../components/annotation/AnnotationTextCanvas.vue'
import AppLayout from '../layouts/AppLayout.vue'
import { useAuthStore } from '../stores/auth'
import {
  collectHighlights,
  createHistoricalEvent,
  createLifeEvent,
  createPerson,
  createPersonRelation,
  parseRelationCodes,
  relationReverseSuggestions,
  runClientChecks,
  uid,
} from '../utils/extractionAnnotation'

type StepId = 'document' | 'persons' | 'events' | 'relations' | 'checks'
type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'
type TaskFilter = 'mine' | 'available' | 'all'
type PassageTaskState = 'uncreated' | 'established' | 'completed'

const steps: { id: StepId; index: string; label: string }[] = [
  { id: 'document', index: '01', label: '文献' },
  { id: 'persons', index: '02', label: '人物' },
  { id: 'events', index: '03', label: '事件' },
  { id: 'relations', index: '04', label: '关系' },
  { id: 'checks', index: '05', label: '检查' },
]

const checkStateOptions: { value: CheckState; label: string }[] = [
  { value: 'unreviewed', label: '尚未检查' },
  { value: 'has_fact', label: '已检查且有事实' },
  { value: 'not_mentioned', label: '原文未提及' },
  { value: 'uncertain', label: '暂不能判断' },
  { value: 'unsupported', label: '工具暂不支持' },
]

const fieldStateOptions: { value: FieldState; label: string }[] = [
  { value: 'present', label: '原文明示' },
  { value: 'not_mentioned', label: '原文未提及' },
  { value: 'not_applicable', label: '本项不适用' },
  { value: 'uncertain', label: '暂不能判断' },
  { value: 'unsupported', label: '工具暂不支持' },
]

const authStore = useAuthStore()
const tasks = ref<ExtractionTaskSummary[]>([])
const passageOptions = ref<PassageSummary[]>([])
const selectedPassageId = ref<number | null>(null)
const taskFilter = ref<TaskFilter>('mine')
const loadingTasks = ref(false)
const creatingTask = ref(false)
const openingTaskId = ref<number | null>(null)
const activeDetail = ref<ExtractionTaskDetail | null>(null)
const label = ref<ExtractionAnnotationLabel | null>(null)
const activeStep = ref<StepId>('document')
const activePersonKey = ref<string | null>(null)
const activeEventKey = ref<string | null>(null)
const activeHistoricalEventKey = ref<string | null>(null)
const activeRelationKey = ref<string | null>(null)
const activeEvidenceId = ref<string | null>(null)
const pendingEvidence = ref<EvidenceSpan | null>(null)
const saveState = ref<SaveState>('idle')
const saveMessage = ref('')
const dirty = ref(false)
const serverIssues = ref<string[]>([])
const submitting = ref(false)
const textCanvas = ref<InstanceType<typeof AnnotationTextCanvas> | null>(null)
const dictionaries = ref<AnnotationDictionaries>({
  eras: [],
  historical_events: [],
  relation_codes: [],
})

let hydrating = false
let saveTimer: number | null = null
let activeSavePromise: Promise<void> | null = null

const isReadOnly = computed(() => activeDetail.value?.submission.state === 'submitted')
const highlights = computed(() => collectHighlights(label.value))
const clientChecks = computed(() => runClientChecks(label.value))
const blockingCheckCount = computed(
  () => clientChecks.value.filter((item) => item.level === 'error').length,
)
const currentPerson = computed(() =>
  label.value?.persons.find((person) => person.key === activePersonKey.value) ?? null,
)
const currentEvent = computed(() =>
  currentPerson.value?.life_events.find((event) => event.key === activeEventKey.value) ?? null,
)
const currentHistoricalEvent = computed(() =>
  currentPerson.value?.historical_events.find(
    (event) => event.key === activeHistoricalEventKey.value,
  ) ?? null,
)
const currentRelation = computed(() =>
  label.value?.person_relations.find((relation) => relation.key === activeRelationKey.value) ?? null,
)
const reverseSuggestions = computed(() =>
  currentRelation.value ? relationReverseSuggestions(currentRelation.value.codes) : [],
)
const relationPersons = computed(() => label.value?.persons.filter((person) => person.level !== 3) ?? [])
const filteredTasks = computed(() => {
  if (taskFilter.value === 'mine') return tasks.value.filter((task) => task.submission_id)
  if (taskFilter.value === 'available') {
    return tasks.value.filter((task) => !task.submission_id && task.available_slots > 0)
  }
  return tasks.value
})
const latestTaskByPassage = computed(() => {
  const result = new Map<number, ExtractionTaskSummary>()
  for (const task of tasks.value) {
    const current = result.get(task.passage_id)
    if (!current || Date.parse(task.updated_at) > Date.parse(current.updated_at)) {
      result.set(task.passage_id, task)
    }
  }
  return result
})
const selectedExistingTask = computed(() => (
  selectedPassageId.value === null
    ? null
    : latestTaskByPassage.value.get(selectedPassageId.value) ?? null
))
const selectedPassageTaskState = computed<PassageTaskState>(() => (
  selectedExistingTask.value?.status === 'completed'
    ? 'completed'
    : selectedExistingTask.value
      ? 'established'
      : 'uncreated'
))
const createTaskButtonLabel = computed(() => {
  if (creatingTask.value) return '创建中…'
  if (selectedPassageTaskState.value === 'completed') return '该古籍任务已完成'
  if (selectedPassageTaskState.value === 'established') return '该古籍已建立任务'
  return '建立双人盲标任务'
})
const progressPercent = computed(() => {
  if (!label.value) return 0
  let completed = 0
  if (label.value.passage.source_type !== 'uncertain') completed += 1
  if (label.value.persons.length && label.value.persons.every((person) => person.name_surface && person.level_reason)) completed += 1
  const reviewedPeople = label.value.persons.filter((person) => person.level !== 3)
  if (reviewedPeople.length && reviewedPeople.every((person) => EVENT_TYPES.every(
    (eventType) => person.event_checks[eventType] !== 'unreviewed',
  ))) completed += 1
  if (label.value.person_relations.every((relation) => relation.evidence.length)) completed += 1
  if (!blockingCheckCount.value) completed += 1
  return completed * 20
})
const pendingTargetLabel = computed(() => {
  if (activeStep.value === 'events' && currentEvent.value) {
    return `${currentPerson.value?.name_surface || '当前人物'}的${currentEvent.value.event_type}事件`
  }
  if (activeStep.value === 'events' && currentHistoricalEvent.value) {
    return `${currentPerson.value?.name_surface || '当前人物'}的历史事件关联`
  }
  if (activeStep.value === 'relations' && currentRelation.value) return '当前人物关系'
  return ''
})

function statusLabel(status: string | null) {
  const labels: Record<string, string> = {
    open: '待领取',
    in_progress: '标注中',
    ready_for_adjudication: '待裁定',
    completed: '已完成',
    draft: '草稿',
    submitted: '已提交',
    returned: '已退回',
  }
  return status ? labels[status] ?? status : '可领取'
}

function passageTaskState(passageId: number): PassageTaskState {
  const task = latestTaskByPassage.value.get(passageId)
  if (!task) return 'uncreated'
  return task.status === 'completed' ? 'completed' : 'established'
}

function passageTaskPrefix(passageId: number) {
  const state = passageTaskState(passageId)
  if (state === 'completed') return '【已完成】'
  if (state === 'established') return '【已建立】'
  return '【未建立】'
}

function saveStateLabel() {
  const labels: Record<SaveState, string> = {
    idle: '尚未修改',
    dirty: '有未保存修改',
    saving: '保存中…',
    saved: '已保存',
    error: '保存失败',
  }
  return labels[saveState.value]
}

function cloneLabel(value: ExtractionAnnotationLabel) {
  return structuredClone(value)
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    tasks.value = await listExtractionTasks()
  } catch (error) {
    saveMessage.value = getApiErrorMessage(error, '任务队列加载失败。')
  } finally {
    loadingTasks.value = false
  }
}

async function loadAdminPassages() {
  if (!authStore.isAdmin) return
  try {
    passageOptions.value = await listPassages()
    if (!selectedPassageId.value && passageOptions.value.length) {
      selectedPassageId.value = passageOptions.value[0].doc_id
    }
  } catch (error) {
    saveMessage.value = getApiErrorMessage(error, '古籍列表加载失败。')
  }
}

async function loadDictionaries() {
  try {
    dictionaries.value = await fetchAnnotationDictionaries()
  } catch (error) {
    saveMessage.value = getApiErrorMessage(error, '标注字典加载失败。')
  }
}

async function handleCreateTask() {
  if (!selectedPassageId.value) return
  if (selectedExistingTask.value) {
    const stateText = selectedExistingTask.value.status === 'completed' ? '已完成' : '已建立'
    taskFilter.value = 'all'
    saveMessage.value = `任务 #${selectedExistingTask.value.id} ${stateText}，不会覆盖或重复建立。`
    return
  }
  creatingTask.value = true
  try {
    await createExtractionTask({ passage_id: selectedPassageId.value })
    taskFilter.value = 'all'
    await loadTasks()
    saveMessage.value = '标注任务已创建，可由两位标注员独立领取。'
  } catch (error) {
    saveMessage.value = getApiErrorMessage(error, '创建任务失败。')
  } finally {
    creatingTask.value = false
  }
}

function canLeaveCurrentTask() {
  return !dirty.value || window.confirm('当前任务还有未保存修改，确定离开吗？')
}

async function openTask(task: ExtractionTaskSummary) {
  if (openingTaskId.value || (activeDetail.value?.id !== task.id && !canLeaveCurrentTask())) return
  openingTaskId.value = task.id
  serverIssues.value = []
  saveMessage.value = ''
  try {
    const detail = task.submission_id
      ? await fetchExtractionTask(task.id)
      : await claimExtractionTask(task.id)
    await hydrateTask(detail)
    await loadTasks()
  } catch (error) {
    saveMessage.value = getApiErrorMessage(error, '打开任务失败。')
  } finally {
    openingTaskId.value = null
  }
}

async function hydrateTask(detail: ExtractionTaskDetail) {
  hydrating = true
  clearSaveTimer()
  activeDetail.value = detail
  label.value = cloneLabel(detail.submission.label)
  activeStep.value = 'document'
  activePersonKey.value = label.value.persons[0]?.key ?? null
  activeEventKey.value = null
  activeHistoricalEventKey.value = null
  activeRelationKey.value = null
  activeEvidenceId.value = null
  pendingEvidence.value = null
  dirty.value = false
  saveState.value = detail.submission.state === 'submitted' ? 'saved' : 'idle'
  await nextTick()
  hydrating = false
}

function clearSaveTimer() {
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer)
    saveTimer = null
  }
}

function scheduleAutosave() {
  clearSaveTimer()
  if (isReadOnly.value || !activeDetail.value) return
  saveTimer = window.setTimeout(() => void saveDraft(false), 1200)
}

async function saveDraft(showFeedback = true) {
  if (!activeDetail.value || !label.value || isReadOnly.value) return
  if (activeSavePromise) return activeSavePromise
  if (!dirty.value) {
    if (showFeedback) saveMessage.value = '当前草稿没有新的修改。'
    return
  }
  clearSaveTimer()
  const taskId = activeDetail.value.id
  const revision = activeDetail.value.submission.revision
  const snapshot = cloneLabel(label.value)
  const snapshotJson = JSON.stringify(snapshot)
  saveState.value = 'saving'
  serverIssues.value = []

  activeSavePromise = (async () => {
    try {
      const detail = await saveExtractionDraft(taskId, revision, snapshot)
      if (!activeDetail.value || activeDetail.value.id !== taskId) return
      activeDetail.value.submission.revision = detail.submission.revision
      activeDetail.value.status = detail.status
      const unchanged = label.value && JSON.stringify(label.value) === snapshotJson
      dirty.value = !unchanged
      saveState.value = unchanged ? 'saved' : 'dirty'
      if (showFeedback) saveMessage.value = `草稿已保存 · revision ${detail.submission.revision}`
      if (!unchanged) scheduleAutosave()
    } catch (error) {
      saveState.value = 'error'
      saveMessage.value = getApiErrorMessage(error, '草稿保存失败。')
      serverIssues.value = extractServerIssues(error)
    } finally {
      activeSavePromise = null
    }
  })()
  return activeSavePromise
}

function extractServerIssues(error: unknown) {
  if (!axios.isAxiosError(error) || !Array.isArray(error.response?.data?.detail)) return []
  return error.response.data.detail.map((item: unknown) => {
    if (!item || typeof item !== 'object') return String(item)
    const issue = item as { path?: string; message?: string; msg?: string }
    return `${issue.path ? `${issue.path}：` : ''}${issue.message ?? issue.msg ?? '数据不符合要求。'}`
  })
}

async function handleSubmit() {
  if (!activeDetail.value || !label.value || isReadOnly.value || submitting.value) return
  serverIssues.value = []
  if (blockingCheckCount.value) {
    activeStep.value = 'checks'
    saveMessage.value = `还有 ${blockingCheckCount.value} 个阻断问题，请先处理。`
    return
  }
  if (!window.confirm('提交后本轮盲标将锁定，确定提交吗？')) return
  if (activeSavePromise) await activeSavePromise
  if (dirty.value) await saveDraft(false)
  if (saveState.value === 'error' || !activeDetail.value || !label.value) return

  submitting.value = true
  try {
    const detail = await submitExtractionAnnotation(
      activeDetail.value.id,
      activeDetail.value.submission.revision,
      cloneLabel(label.value),
    )
    await hydrateTask(detail)
    saveMessage.value = '本轮盲标已提交并锁定。'
    await loadTasks()
  } catch (error) {
    serverIssues.value = extractServerIssues(error)
    saveMessage.value = getApiErrorMessage(error, '提交失败。')
    activeStep.value = 'checks'
  } finally {
    submitting.value = false
  }
}

function handleSelectionCreated(evidence: EvidenceSpan) {
  pendingEvidence.value = evidence
  activeEvidenceId.value = null
}

function createPersonFromSelection() {
  if (!label.value || !pendingEvidence.value) return
  const person = createPerson(pendingEvidence.value)
  label.value.persons.push(person)
  activePersonKey.value = person.key
  activeStep.value = 'persons'
  activeEvidenceId.value = pendingEvidence.value.id
  pendingEvidence.value = null
}

function addBlankPerson() {
  if (!label.value) return
  const person = createPerson()
  label.value.persons.push(person)
  activePersonKey.value = person.key
  activeStep.value = 'persons'
}

function addMentionToCurrentPerson() {
  if (!currentPerson.value || !pendingEvidence.value) return
  currentPerson.value.mentions.push(pendingEvidence.value)
  activeEvidenceId.value = pendingEvidence.value.id
  pendingEvidence.value = null
}

function addEvidenceToCurrentFact() {
  if (!pendingEvidence.value) return
  if (activeStep.value === 'events' && currentEvent.value) {
    currentEvent.value.evidence.push(pendingEvidence.value)
  } else if (activeStep.value === 'events' && currentHistoricalEvent.value) {
    currentHistoricalEvent.value.evidence.push(pendingEvidence.value)
  } else if (activeStep.value === 'relations' && currentRelation.value) {
    currentRelation.value.evidence.push(pendingEvidence.value)
  } else {
    return
  }
  activeEvidenceId.value = pendingEvidence.value.id
  pendingEvidence.value = null
}

function excludePendingSelection() {
  if (!label.value || !pendingEvidence.value) return
  label.value.excluded_mentions.push({
    key: uid('excluded'),
    reason: '匿名泛称（待核对）',
    evidence: [pendingEvidence.value],
  })
  activeEvidenceId.value = pendingEvidence.value.id
  pendingEvidence.value = null
}

function markPendingUnresolved() {
  if (!label.value || !pendingEvidence.value) return
  label.value.unresolved_items.push({
    key: uid('unresolved'),
    category: '人物或事实',
    note: '',
    evidence: [pendingEvidence.value],
  })
  activeEvidenceId.value = pendingEvidence.value.id
  pendingEvidence.value = null
  activeStep.value = 'checks'
}

function activateHighlight(evidenceId: string) {
  const highlight = highlights.value.find((item) => item.id === evidenceId)
  if (!highlight) return
  activeEvidenceId.value = evidenceId
  if (highlight.kind === 'person') {
    activeStep.value = 'persons'
    activePersonKey.value = highlight.personKey ?? null
  } else if (highlight.kind === 'event' || highlight.kind === 'history') {
    activeStep.value = 'events'
    activePersonKey.value = highlight.personKey ?? null
    activeEventKey.value = highlight.kind === 'event' ? highlight.objectKey ?? null : null
    activeHistoricalEventKey.value = highlight.kind === 'history' ? highlight.objectKey ?? null : null
  } else if (highlight.kind === 'relation') {
    activeStep.value = 'relations'
    activeRelationKey.value = highlight.objectKey ?? null
  } else {
    activeStep.value = 'checks'
  }
}

function focusEvidence(evidenceId?: string) {
  if (!evidenceId) return
  activateHighlight(evidenceId)
  textCanvas.value?.focusEvidence(evidenceId)
}

function removeEvidence(items: EvidenceSpan[], evidenceId: string) {
  const index = items.findIndex((item) => item.id === evidenceId)
  if (index >= 0) items.splice(index, 1)
  if (activeEvidenceId.value === evidenceId) activeEvidenceId.value = null
}

function selectPerson(person: PersonAnnotation) {
  activePersonKey.value = person.key
  activeEventKey.value = null
  activeHistoricalEventKey.value = null
}

function removeCurrentPerson() {
  if (!label.value || !currentPerson.value) return
  if (!window.confirm(`确定删除人物“${currentPerson.value.name_surface || '未命名'}”及其全部事件吗？`)) return
  const personKey = currentPerson.value.key
  label.value.persons = label.value.persons.filter((person) => person.key !== personKey)
  label.value.person_relations = label.value.person_relations.filter(
    (relation) => relation.source_person_key !== personKey && relation.target_person_key !== personKey,
  )
  activePersonKey.value = label.value.persons[0]?.key ?? null
}

function setTitles(event: Event) {
  if (!currentPerson.value) return
  currentPerson.value.titles = (event.target as HTMLTextAreaElement).value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function addLifeEvent(eventType: EventType) {
  if (!currentPerson.value || currentPerson.value.level === 3) return
  const event = createLifeEvent(eventType)
  currentPerson.value.life_events.push(event)
  currentPerson.value.event_checks[eventType] = 'has_fact'
  activeEventKey.value = event.key
  activeHistoricalEventKey.value = null
}

function handleEventTypeChange(event: Event) {
  if (!currentPerson.value || !currentEvent.value) return
  const oldType = currentEvent.value.event_type
  const nextType = (event.target as HTMLSelectElement).value as EventType
  currentEvent.value.event_type = nextType
  if (nextType === '籍贯') {
    currentEvent.value.time = createLifeEvent('籍贯').time
  }
  if (nextType !== '任职') currentEvent.value.official_title = ''
  currentPerson.value.event_checks[nextType] = 'has_fact'
  if (!currentPerson.value.life_events.some((item) => item.event_type === oldType)) {
    currentPerson.value.event_checks[oldType] = 'unreviewed'
  }
}

function removeCurrentEvent() {
  if (!currentPerson.value || !currentEvent.value) return
  const eventType = currentEvent.value.event_type
  currentPerson.value.life_events = currentPerson.value.life_events.filter(
    (event) => event.key !== currentEvent.value?.key,
  )
  if (!currentPerson.value.life_events.some((event) => event.event_type === eventType)) {
    currentPerson.value.event_checks[eventType] = 'unreviewed'
  }
  activeEventKey.value = currentPerson.value.life_events[0]?.key ?? null
}

function addHistoricalEventLink() {
  if (!currentPerson.value || currentPerson.value.level === 3) return
  const event = createHistoricalEvent()
  currentPerson.value.historical_events.push(event)
  activeHistoricalEventKey.value = event.key
  activeEventKey.value = null
}

function removeCurrentHistoricalEvent() {
  if (!currentPerson.value || !currentHistoricalEvent.value) return
  currentPerson.value.historical_events = currentPerson.value.historical_events.filter(
    (event) => event.key !== currentHistoricalEvent.value?.key,
  )
  activeHistoricalEventKey.value = currentPerson.value.historical_events[0]?.key ?? null
}

function setNullableTimeNumber(field: 'era_year' | 'gregorian_year', event: Event) {
  if (!currentEvent.value) return
  const value = (event.target as HTMLInputElement).value
  currentEvent.value.time[field] = value === '' ? null : Number(value)
}

function addRelation() {
  if (!label.value || relationPersons.value.length < 2) return
  const relation = createPersonRelation(relationPersons.value[0].key, relationPersons.value[1].key)
  label.value.person_relations.push(relation)
  activeRelationKey.value = relation.key
}

function selectRelation(relation: PersonRelationAnnotation) {
  activeRelationKey.value = relation.key
}

function setRelationCodes(direction: 'forward' | 'reverse', event: Event) {
  if (!currentRelation.value) return
  const codes = parseRelationCodes((event.target as HTMLInputElement).value)
  if (direction === 'forward') {
    currentRelation.value.codes = codes
    const suggestions = relationReverseSuggestions(codes)
    if (suggestions.length === 1) {
      currentRelation.value.reverse_codes = [...suggestions[0]]
    } else if (!suggestions.some(
      (candidate) => candidate.join('') === currentRelation.value?.reverse_codes.join(''),
    )) {
      currentRelation.value.reverse_codes = []
    }
  } else currentRelation.value.reverse_codes = codes
}

function useReverseSuggestion(codes: PersonRelationAnnotation['reverse_codes']) {
  if (currentRelation.value) currentRelation.value.reverse_codes = [...codes]
}

function reconcileHistoricalEventDictionary() {
  if (!currentHistoricalEvent.value) return
  const exact = dictionaries.value.historical_events.some(
    (item) => item.event_name === currentHistoricalEvent.value?.event_name,
  )
  if (exact) currentHistoricalEvent.value.outside_dictionary = false
}

function removeCurrentRelation() {
  if (!label.value || !currentRelation.value) return
  label.value.person_relations = label.value.person_relations.filter(
    (relation) => relation.key !== currentRelation.value?.key,
  )
  activeRelationKey.value = label.value.person_relations[0]?.key ?? null
}

function addSchemaConflict() {
  label.value?.schema_conflicts.push({
    key: uid('conflict'),
    code: 'multiple_protagonists',
    note: '',
    evidence: [],
  })
}

function removeByKey<T extends { key: string }>(items: T[], key: string) {
  const index = items.findIndex((item) => item.key === key)
  if (index >= 0) items.splice(index, 1)
}

function handleKeyboard(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault()
    void saveDraft(true)
  }
  if (activeStep.value === 'persons' && currentPerson.value && !isReadOnly.value) {
    if (event.altKey && ['1', '2', '3'].includes(event.key)) {
      currentPerson.value.level = Number(event.key) as 1 | 2 | 3
    }
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

watch(label, () => {
  if (hydrating || isReadOnly.value || !activeDetail.value) return
  dirty.value = true
  saveState.value = 'dirty'
  scheduleAutosave()
}, { deep: true })

onBeforeRouteLeave(() => canLeaveCurrentTask())

onMounted(async () => {
  window.addEventListener('keydown', handleKeyboard)
  window.addEventListener('beforeunload', handleBeforeUnload)
  await Promise.all([loadTasks(), loadAdminPassages(), loadDictionaries()])
})

onBeforeUnmount(() => {
  clearSaveTimer()
  window.removeEventListener('keydown', handleKeyboard)
  window.removeEventListener('beforeunload', handleBeforeUnload)
})
</script>

<template>
  <AppLayout variant="workspace">
    <div class="workbench">
      <header class="workbench__topbar">
        <div class="workbench__identity">
          <span class="workbench__eyebrow">A · LABEL</span>
          <div>
            <h2>知识抽取标注</h2>
            <p v-if="activeDetail">任务 #{{ activeDetail.id }} · {{ activeDetail.passage.title }}</p>
            <p v-else>选择或领取一篇古籍开始独立盲标</p>
          </div>
        </div>
        <div class="workbench__progress" aria-label="标注完成度">
          <span>{{ progressPercent }}%</span>
          <i><b :style="{ width: `${progressPercent}%` }" /></i>
        </div>
        <nav v-if="authStore.isAdmin" class="workbench__modes" aria-label="标注模式">
          <span>独立盲标</span>
          <RouterLink to="/annotations/extraction/adjudication">复核裁定</RouterLink>
        </nav>
        <div class="workbench__actions">
          <span class="save-indicator" :data-state="saveState">
            <i />{{ saveStateLabel() }}
          </span>
          <button
            class="button button--quiet"
            type="button"
            :disabled="!activeDetail || isReadOnly || saveState === 'saving'"
            @click="saveDraft(true)"
          >保存草稿</button>
          <button
            class="button button--primary"
            type="button"
            :disabled="!activeDetail || isReadOnly || submitting"
            @click="handleSubmit"
          >{{ submitting ? '提交中…' : isReadOnly ? '已锁定' : '提交标注' }}</button>
        </div>
      </header>

      <div class="workbench__body">
        <aside class="task-rail">
          <div class="task-rail__heading">
            <div>
              <span>任务队列</span>
              <strong>{{ filteredTasks.length }}</strong>
            </div>
            <button type="button" title="刷新任务" @click="loadTasks">↻</button>
          </div>

          <div class="task-filter" role="tablist">
            <button
              v-for="item in ([
                { value: 'mine', label: '我的' },
                { value: 'available', label: '可领取' },
                { value: 'all', label: '全部' },
              ] as const)"
              :key="item.value"
              type="button"
              :class="{ active: taskFilter === item.value }"
              @click="taskFilter = item.value"
            >{{ item.label }}</button>
          </div>

          <div v-if="authStore.isAdmin" class="task-create">
            <label for="passage-task-select">从古籍创建任务</label>
            <select
              id="passage-task-select"
              v-model="selectedPassageId"
              :class="`task-state--${selectedPassageTaskState}`"
            >
              <option :value="null" disabled>选择古籍</option>
              <option
                v-for="passage in passageOptions"
                :key="passage.doc_id"
                :value="passage.doc_id"
                :class="`task-state--${passageTaskState(passage.doc_id)}`"
              >
                {{ passageTaskPrefix(passage.doc_id) }} #{{ passage.doc_id }} {{ passage.title }}
              </option>
            </select>
            <div class="task-create__legend" aria-label="古籍任务状态图例">
              <span data-state="uncreated"><i />未建立</span>
              <span data-state="established"><i />已建立</span>
              <span data-state="completed"><i />已完成</span>
            </div>
            <p
              v-if="selectedExistingTask"
              class="task-create__notice"
              :data-state="selectedPassageTaskState"
            >
              任务 #{{ selectedExistingTask.id }} {{ selectedPassageTaskState === 'completed' ? '已完成' : '已建立' }}，不会覆盖。
            </p>
            <button
              type="button"
              :disabled="!selectedPassageId || loadingTasks || creatingTask || !!selectedExistingTask"
              @click="handleCreateTask"
            >
              {{ createTaskButtonLabel }}
            </button>
          </div>

          <div class="task-list">
            <p v-if="loadingTasks" class="task-list__empty">正在读取任务…</p>
            <p v-else-if="!filteredTasks.length" class="task-list__empty">
              {{ taskFilter === 'mine' ? '还没有领取任务' : '当前没有符合条件的任务' }}
            </p>
            <button
              v-for="task in filteredTasks"
              :key="task.id"
              class="task-item"
              :class="{ active: activeDetail?.id === task.id }"
              type="button"
              :disabled="openingTaskId === task.id || (!task.submission_id && task.available_slots === 0)"
              @click="openTask(task)"
            >
              <span class="task-item__index">#{{ String(task.id).padStart(3, '0') }}</span>
              <strong>{{ task.passage_title }}</strong>
              <small>
                <span>{{ statusLabel(task.submission_state || task.status) }}</span>
                <span>{{ task.claimed_count }}/{{ task.required_annotation_count }} 人</span>
              </small>
            </button>
          </div>
        </aside>

        <main class="text-workspace">
          <template v-if="activeDetail && label">
            <div class="text-workspace__meta">
              <div>
                <span>DOC {{ activeDetail.passage.doc_id }}</span>
                <h3>{{ activeDetail.passage.title }}</h3>
              </div>
              <div class="annotation-legend" aria-label="高亮图例">
                <span><i data-kind="person" />人物</span>
                <span><i data-kind="event" />事件</span>
                <span><i data-kind="relation" />关系</span>
                <span><i data-kind="unresolved" />待定</span>
              </div>
            </div>
            <div class="text-workspace__scroll">
              <AnnotationTextCanvas
                ref="textCanvas"
                :text="activeDetail.passage.context"
                :highlights="highlights"
                :active-evidence-id="activeEvidenceId"
                :disabled="isReadOnly"
                @selection-created="handleSelectionCreated"
                @highlight-activated="activateHighlight"
              />
            </div>
            <Transition name="selection-dock">
              <div v-if="pendingEvidence" class="selection-dock">
                <button class="selection-dock__close" type="button" @click="pendingEvidence = null">×</button>
                <div>
                  <span>已选择 {{ pendingEvidence.end - pendingEvidence.start }} 字</span>
                  <strong>“{{ pendingEvidence.quote }}”</strong>
                </div>
                <div class="selection-dock__actions">
                  <button type="button" @click="createPersonFromSelection">新建人物</button>
                  <button
                    type="button"
                    :disabled="!currentPerson"
                    @click="addMentionToCurrentPerson"
                  >添加为人物指代</button>
                  <button
                    v-if="pendingTargetLabel"
                    type="button"
                    @click="addEvidenceToCurrentFact"
                  >作为{{ pendingTargetLabel }}证据</button>
                  <button type="button" @click="excludePendingSelection">排除称谓</button>
                  <button type="button" @click="markPendingUnresolved">暂不能判断</button>
                </div>
              </div>
            </Transition>
          </template>
          <div v-else class="workspace-empty">
            <span>ACG</span>
            <h3>从左侧领取一篇古籍</h3>
            <p>任务打开后，划选正文即可建立人物、事实证据或待定项。</p>
          </div>
        </main>

        <aside class="inspector">
          <nav class="step-nav" aria-label="标注步骤">
            <button
              v-for="step in steps"
              :key="step.id"
              type="button"
              :class="{ active: activeStep === step.id }"
              :disabled="!label"
              @click="activeStep = step.id"
            >
              <span>{{ step.index }}</span>{{ step.label }}
            </button>
          </nav>

          <div v-if="label && activeDetail" class="inspector__scroll">
            <fieldset :disabled="isReadOnly">
              <section v-if="activeStep === 'document'" class="inspector-section">
                <header>
                  <span>01 / DOCUMENT</span>
                  <h3>文献与探针属性</h3>
                  <p>只依据当前标题和完整正文，不使用外部常识补全。</p>
                </header>

                <label class="field">
                  <span>文献类型</span>
                  <select v-model="label.passage.source_type">
                    <option value="uncertain">暂不能判断</option>
                    <option value="epitaph">墓志铭或塔铭类</option>
                    <option value="history">一般历史文献</option>
                  </select>
                </label>
                <label class="field">
                  <span>材料状态</span>
                  <select v-model="label.passage.material_status">
                    <option value="complete">标题与正文完整匹配</option>
                    <option value="issue">存在缺页、错配或转换问题</option>
                    <option value="uncertain">暂不能判断</option>
                  </select>
                </label>

                <div class="probe-grid">
                  <label v-for="probe in ([
                    { key: 'is_female', label: '主人公明示为女性' },
                    { key: 'is_damaged', label: '正文含残缺符号' },
                    { key: 'is_clergy', label: '主人公明示为出家人' },
                    { key: 'has_courtesy_name', label: '明确出现字或号' },
                  ] as const)" :key="probe.key">
                    <span>{{ probe.label }}</span>
                    <select v-model="label.passage[probe.key]">
                      <option :value="null">未判断</option>
                      <option :value="true">是</option>
                      <option :value="false">否</option>
                    </select>
                  </label>
                </div>

                <label class="field">
                  <span>文献级年号（试标项）</span>
                  <input v-model.trim="label.passage.era" list="annotation-era-options" maxlength="64" placeholder="输入或从年号字典选择" />
                  <small class="dictionary-hint">当前载入 {{ dictionaries.eras.length }} 条年号记录；提交时按字典复核。</small>
                </label>
                <label class="field">
                  <span>年号选择依据</span>
                  <textarea v-model.trim="label.passage.era_basis" rows="3" placeholder="如：原文明示下葬年号" />
                </label>
                <label class="field">
                  <span>材料备注</span>
                  <textarea v-model.trim="label.passage.note" rows="4" placeholder="记录错配、残损或文字疑误" />
                </label>
              </section>

              <section v-else-if="activeStep === 'persons'" class="inspector-section">
                <header>
                  <span>02 / PERSONS</span>
                  <h3>人物识别与分级</h3>
                  <p>划选正文可快速新建人物；Alt + 1/2/3 可切换当前人物等级。</p>
                </header>

                <div class="object-tabs">
                  <button
                    v-for="person in label.persons"
                    :key="person.key"
                    type="button"
                    :class="{ active: person.key === activePersonKey }"
                    @click="selectPerson(person)"
                  >
                    <strong>{{ person.name_surface || '未命名' }}</strong>
                    <span>L{{ person.level }} · {{ person.mentions.length }} 处</span>
                  </button>
                  <button class="object-tabs__add" type="button" @click="addBlankPerson">＋ 人物</button>
                </div>

                <template v-if="currentPerson">
                  <div class="level-switch" aria-label="人物等级">
                    <button
                      v-for="levelNo in ([1, 2, 3] as const)"
                      :key="levelNo"
                      type="button"
                      :class="{ active: currentPerson.level === levelNo }"
                      @click="currentPerson.level = levelNo"
                    >L{{ levelNo }}</button>
                  </div>
                  <label class="field">
                    <span>原文姓名</span>
                    <input v-model.trim="currentPerson.name_surface" maxlength="255" />
                  </label>
                  <label class="field">
                    <span>分级理由</span>
                    <textarea v-model.trim="currentPerson.level_reason" rows="3" placeholder="说明篇章作用和与主人公的关系" />
                  </label>
                  <div class="field-row">
                    <label class="field">
                      <span>篇内补全姓名</span>
                      <input v-model.trim="currentPerson.completed_name" maxlength="255" />
                    </label>
                    <label class="field">
                      <span>字</span>
                      <input v-model.trim="currentPerson.courtesy_name" maxlength="128" />
                    </label>
                  </div>
                  <label v-if="currentPerson.completed_name" class="field">
                    <span>补全理由</span>
                    <input v-model.trim="currentPerson.completion_reason" maxlength="500" />
                  </label>
                  <div class="field-row">
                    <label class="field">
                      <span>号</span>
                      <input v-model.trim="currentPerson.hao" maxlength="128" />
                    </label>
                    <label class="field">
                      <span>称号（每行一个）</span>
                      <textarea :value="currentPerson.titles.join('\n')" rows="2" @input="setTitles" />
                    </label>
                  </div>

                  <div class="evidence-block">
                    <div class="evidence-block__title">
                      <span>人物指代 / mentions</span>
                      <small>从正文划选后添加</small>
                    </div>
                    <button
                      v-for="evidence in currentPerson.mentions"
                      :key="evidence.id"
                      class="evidence-chip"
                      type="button"
                      @click="focusEvidence(evidence.id)"
                    >
                      <span>“{{ evidence.quote }}”</span>
                      <i @click.stop="removeEvidence(currentPerson.mentions, evidence.id)">×</i>
                    </button>
                    <p v-if="!currentPerson.mentions.length">尚未绑定原文位置，可在原文中拖动选择文字并绑定。</p>
                  </div>

                  <button class="danger-link" type="button" @click="removeCurrentPerson">删除当前人物</button>
                </template>
                <p v-else class="inspector-empty">先从正文划选姓名，或点击“＋ 人物”。</p>
              </section>

              <section v-else-if="activeStep === 'events'" class="inspector-section">
                <header>
                  <span>03 / EVENTS</span>
                  <h3>生平与历史事件</h3>
                  <p>一级、二级人物逐类检查；三级人物不进入主评测事件。</p>
                </header>

                <label class="field">
                  <span>当前人物</span>
                  <select v-model="activePersonKey">
                    <option v-for="person in label.persons" :key="person.key" :value="person.key">
                      L{{ person.level }} · {{ person.name_surface || '未命名' }}
                    </option>
                  </select>
                </label>

                <template v-if="currentPerson && currentPerson.level !== 3">
                  <div class="event-checks">
                    <label v-for="eventType in EVENT_TYPES" :key="eventType">
                      <span>{{ eventType }}</span>
                      <select v-model="currentPerson.event_checks[eventType]">
                        <option v-for="option in checkStateOptions" :key="option.value" :value="option.value">
                          {{ option.label }}
                        </option>
                      </select>
                      <button type="button" @click="addLifeEvent(eventType)">＋</button>
                    </label>
                  </div>

                  <div class="event-list">
                    <button
                      v-for="event in currentPerson.life_events"
                      :key="event.key"
                      type="button"
                      :class="{ active: event.key === activeEventKey }"
                      @click="activeEventKey = event.key; activeHistoricalEventKey = null"
                    >
                      <span>{{ event.event_type }}</span>
                      <strong>{{ event.official_title || event.location.raw || event.time.raw || '待填写' }}</strong>
                    </button>
                    <button
                      v-for="event in currentPerson.historical_events"
                      :key="event.key"
                      type="button"
                      :class="{ active: event.key === activeHistoricalEventKey }"
                      @click="activeHistoricalEventKey = event.key; activeEventKey = null"
                    >
                      <span>历史事件</span>
                      <strong>{{ event.event_name || '待填写' }}</strong>
                    </button>
                    <button class="event-list__add" type="button" @click="addHistoricalEventLink">
                      ＋ 历史事件关联
                    </button>
                  </div>

                  <div v-if="currentEvent" class="object-editor">
                    <div class="object-editor__heading">
                      <strong>生平事件</strong>
                      <button type="button" @click="removeCurrentEvent">删除</button>
                    </div>
                    <div class="field-row">
                      <label class="field">
                        <span>事件类型</span>
                        <select :value="currentEvent.event_type" @change="handleEventTypeChange">
                          <option v-for="eventType in EVENT_TYPES" :key="eventType" :value="eventType">{{ eventType }}</option>
                        </select>
                      </label>
                      <label class="field">
                        <span>事实状态</span>
                        <select v-model="currentEvent.state">
                          <option value="confirmed">确定</option>
                          <option value="uncertain">暂不能判断</option>
                          <option value="unsupported">工具暂不支持</option>
                        </select>
                      </label>
                    </div>

                    <div class="subsection">
                      <h4>时间</h4>
                      <label class="field">
                        <span>时间状态</span>
                        <select v-model="currentEvent.time.state" :disabled="currentEvent.event_type === '籍贯'">
                          <option v-for="option in fieldStateOptions" :key="option.value" :value="option.value">
                            {{ option.label }}
                          </option>
                        </select>
                      </label>
                      <label class="field">
                        <span>原文时间</span>
                        <input v-model.trim="currentEvent.time.raw" :disabled="currentEvent.event_type === '籍贯'" placeholder="如：开元二十年、明年" />
                      </label>
                      <div class="field-row field-row--three">
                        <label class="field"><span>年号</span><input v-model.trim="currentEvent.time.era" list="annotation-era-options" :disabled="currentEvent.event_type === '籍贯'" /></label>
                        <label class="field"><span>年号内年</span><input type="number" :value="currentEvent.time.era_year ?? ''" :disabled="currentEvent.event_type === '籍贯'" @input="setNullableTimeNumber('era_year', $event)" /></label>
                        <label class="field"><span>公元年</span><input type="number" :value="currentEvent.time.gregorian_year ?? ''" :disabled="currentEvent.event_type === '籍贯'" @input="setNullableTimeNumber('gregorian_year', $event)" /></label>
                      </div>
                      <div class="field-row">
                        <label class="field"><span>月原文</span><input v-model.trim="currentEvent.time.month_text" :disabled="currentEvent.event_type === '籍贯'" /></label>
                        <label class="field"><span>日原文</span><input v-model.trim="currentEvent.time.day_text" :disabled="currentEvent.event_type === '籍贯'" /></label>
                      </div>
                    </div>

                    <div class="subsection">
                      <h4>地点</h4>
                      <label class="field">
                        <span>地点状态</span>
                        <select v-model="currentEvent.location.state">
                          <option v-for="option in fieldStateOptions" :key="option.value" :value="option.value">
                            {{ option.label }}
                          </option>
                        </select>
                      </label>
                      <label class="field"><span>原文地点</span><input v-model.trim="currentEvent.location.raw" /></label>
                      <div class="location-grid">
                        <label v-for="field in ([
                          { key: 'dao', label: '道' }, { key: 'fu', label: '府' },
                          { key: 'zhou', label: '州' }, { key: 'jun', label: '郡' },
                          { key: 'xian', label: '县' }, { key: 'other', label: '其他' },
                        ] as const)" :key="field.key">
                          <span>{{ field.label }}</span>
                          <input v-model.trim="currentEvent.location[field.key]" />
                        </label>
                      </div>
                    </div>

                    <label v-if="currentEvent.event_type === '任职'" class="field">
                      <span>完整官职</span>
                      <input v-model.trim="currentEvent.official_title" placeholder="保留行、守、试、检校、兼、赠等限定词" />
                    </label>
                    <label class="field"><span>事件备注</span><textarea v-model.trim="currentEvent.note" rows="3" /></label>
                    <div class="evidence-block">
                      <div class="evidence-block__title"><span>事件证据</span><small>划选正文后添加到当前事实</small></div>
                      <button v-for="evidence in currentEvent.evidence" :key="evidence.id" class="evidence-chip" type="button" @click="focusEvidence(evidence.id)">
                        <span>“{{ evidence.quote }}”</span><i @click.stop="removeEvidence(currentEvent.evidence, evidence.id)">×</i>
                      </button>
                      <p v-if="!currentEvent.evidence.length">尚未绑定证据</p>
                    </div>
                  </div>

                  <div v-else-if="currentHistoricalEvent" class="object-editor">
                    <div class="object-editor__heading">
                      <strong>人物—历史事件</strong>
                      <button type="button" @click="removeCurrentHistoricalEvent">删除</button>
                    </div>
                    <label class="field">
                      <span>标准事件名称</span>
                      <input v-model.trim="currentHistoricalEvent.event_name" list="annotation-history-options" @change="reconcileHistoricalEventDictionary" />
                      <small class="dictionary-hint">优先从历史事件字典选择；表外事实需勾选下一项。</small>
                    </label>
                    <label class="check-line"><input v-model="currentHistoricalEvent.outside_dictionary" type="checkbox" />名称表外事件</label>
                    <label class="field"><span>关系说明（≤15 字）</span><input v-model.trim="currentHistoricalEvent.relation_summary" maxlength="15" placeholder="如：因乱扈从" /></label>
                    <label class="field"><span>备注</span><textarea v-model.trim="currentHistoricalEvent.note" rows="3" /></label>
                    <div class="evidence-block">
                      <div class="evidence-block__title"><span>关联证据</span><small>需要同时支持人物、事件和联系</small></div>
                      <button v-for="evidence in currentHistoricalEvent.evidence" :key="evidence.id" class="evidence-chip" type="button" @click="focusEvidence(evidence.id)">
                        <span>“{{ evidence.quote }}”</span><i @click.stop="removeEvidence(currentHistoricalEvent.evidence, evidence.id)">×</i>
                      </button>
                      <p v-if="!currentHistoricalEvent.evidence.length">尚未绑定证据</p>
                    </div>
                  </div>
                  <p v-else class="inspector-empty">点击五类事件后的“＋”，或建立历史事件关联。</p>
                </template>
                <p v-else-if="currentPerson" class="inspector-empty">三级人物只保留最小信息，不展开主评测事件。</p>
                <p v-else class="inspector-empty">请先建立并选择人物。</p>
              </section>

              <section v-else-if="activeStep === 'relations'" class="inspector-section">
                <header>
                  <span>04 / RELATIONS</span>
                  <h3>人物关系</h3>
                  <p>从甲看乙：乙是甲的谁。先确认两端，再填写正向与反向关系链。</p>
                </header>

                <div class="object-tabs">
                  <button
                    v-for="relation in label.person_relations"
                    :key="relation.key"
                    type="button"
                    :class="{ active: relation.key === activeRelationKey }"
                    @click="selectRelation(relation)"
                  >
                    <strong>{{ relation.codes.join('') || '未定义' }}</strong>
                    <span>{{ relation.evidence.length }} 处证据</span>
                  </button>
                  <button
                    class="object-tabs__add"
                    type="button"
                    :disabled="relationPersons.length < 2"
                    @click="addRelation"
                  >＋ 关系</button>
                </div>

                <template v-if="currentRelation">
                  <div class="relation-direction">
                    <label class="field">
                      <span>甲（source）</span>
                      <select v-model="currentRelation.source_person_key">
                        <option v-for="person in relationPersons" :key="person.key" :value="person.key">{{ person.name_surface || '未命名' }}</option>
                      </select>
                    </label>
                    <span>看</span>
                    <label class="field">
                      <span>乙（target）</span>
                      <select v-model="currentRelation.target_person_key">
                        <option v-for="person in relationPersons" :key="person.key" :value="person.key">{{ person.name_surface || '未命名' }}</option>
                      </select>
                    </label>
                  </div>
                  <div class="field-row">
                    <label class="field">
                      <span>正向代码链</span>
                      <input :value="currentRelation.codes.join('')" maxlength="3" placeholder="如 F、FS、O" @input="setRelationCodes('forward', $event)" />
                    </label>
                    <label class="field">
                      <span>反向代码链</span>
                      <input :value="currentRelation.reverse_codes.join('')" maxlength="3" placeholder="如 S、DF、O" @input="setRelationCodes('reverse', $event)" />
                    </label>
                  </div>
                  <p class="relation-code-help">F 父 · M 母 · S 子 · D 女 · H 夫 · W 妻 · Z 妾 · C/B 兄弟姐妹 · O 其他</p>
                  <div v-if="reverseSuggestions.length" class="reverse-suggestions">
                    <span>{{ reverseSuggestions.length === 1 ? '已按路径自动反转' : '请选择合法反向链（不猜子女性别）' }}</span>
                    <button
                      v-for="candidate in reverseSuggestions"
                      :key="candidate.join('')"
                      type="button"
                      :class="{ active: candidate.join('') === currentRelation.reverse_codes.join('') }"
                      @click="useReverseSuggestion(candidate)"
                    >{{ candidate.join('') }}</button>
                  </div>
                  <div v-if="currentRelation.codes.includes('O') || currentRelation.reverse_codes.includes('O')" class="field-row">
                    <label class="field"><span>正向说明</span><input v-model.trim="currentRelation.note" maxlength="15" /></label>
                    <label class="field"><span>反向说明</span><input v-model.trim="currentRelation.reverse_note" maxlength="15" /></label>
                  </div>
                  <label class="field">
                    <span>关系状态</span>
                    <select v-model="currentRelation.state">
                      <option value="confirmed">确定</option>
                      <option value="uncertain">暂不能判断</option>
                      <option value="unsupported">工具暂不支持</option>
                    </select>
                  </label>
                  <div class="evidence-block">
                    <div class="evidence-block__title"><span>关系证据</span><small>需要支持两端人物和关系本身</small></div>
                    <button v-for="evidence in currentRelation.evidence" :key="evidence.id" class="evidence-chip" type="button" @click="focusEvidence(evidence.id)">
                      <span>“{{ evidence.quote }}”</span><i @click.stop="removeEvidence(currentRelation.evidence, evidence.id)">×</i>
                    </button>
                    <p v-if="!currentRelation.evidence.length">尚未绑定证据</p>
                  </div>
                  <button class="danger-link" type="button" @click="removeCurrentRelation">删除当前关系</button>
                </template>
                <p v-else class="inspector-empty">
                  {{ relationPersons.length < 2 ? '至少需要两位一级或二级人物才能建立关系。' : '点击“＋ 关系”开始。' }}
                </p>
              </section>

              <section v-else class="inspector-section">
                <header>
                  <span>05 / VALIDATION</span>
                  <h3>完整性检查</h3>
                  <p>前端先提示，提交时后端会再次校验 Schema、正文哈希和全部证据位置。</p>
                </header>

                <div class="check-summary">
                  <strong>{{ blockingCheckCount }}</strong>
                  <span>个阻断问题</span>
                  <small>{{ label.unresolved_items.length }} 个待定项 · {{ label.schema_conflicts.length }} 个 Schema 冲突</small>
                </div>
                <ul class="check-list">
                  <li v-for="(check, index) in clientChecks" :key="`${check.message}-${index}`" :data-level="check.level">
                    <i />{{ check.message }}
                  </li>
                  <li v-for="(issue, index) in serverIssues" :key="`${issue}-${index}`" data-level="error">
                    <i />{{ issue }}
                  </li>
                </ul>

                <div class="review-list">
                  <div class="review-list__heading">
                    <strong>排除称谓</strong><span>{{ label.excluded_mentions.length }}</span>
                  </div>
                  <article v-for="item in label.excluded_mentions" :key="item.key">
                    <button type="button" @click="focusEvidence(item.evidence[0]?.id)">“{{ item.evidence[0]?.quote }}”</button>
                    <input v-model.trim="item.reason" maxlength="255" placeholder="排除原因" />
                    <i @click="removeByKey(label.excluded_mentions, item.key)">×</i>
                  </article>
                </div>

                <div class="review-list">
                  <div class="review-list__heading">
                    <strong>暂不能判断</strong><span>{{ label.unresolved_items.length }}</span>
                  </div>
                  <article v-for="item in label.unresolved_items" :key="item.key">
                    <button type="button" @click="focusEvidence(item.evidence[0]?.id)">“{{ item.evidence[0]?.quote || '文献级问题' }}”</button>
                    <input v-model.trim="item.category" maxlength="64" placeholder="分类" />
                    <textarea v-model.trim="item.note" rows="2" placeholder="说明冲突、残缺或多种解释" />
                    <i @click="removeByKey(label.unresolved_items, item.key)">×</i>
                  </article>
                </div>

                <div class="review-list">
                  <div class="review-list__heading">
                    <strong>Schema 冲突</strong>
                    <button type="button" @click="addSchemaConflict">＋ 添加</button>
                  </div>
                  <article v-for="item in label.schema_conflicts" :key="item.key">
                    <select v-model="item.code">
                      <option value="multiple_protagonists">多人并列主人公</option>
                      <option value="material_unusable">材料不可用</option>
                      <option value="unsupported_fact">工具暂不支持的事实</option>
                      <option value="multiple_relations">同一人物对多重关系</option>
                    </select>
                    <textarea v-model.trim="item.note" rows="2" placeholder="说明原文事实和工具限制" />
                    <i @click="removeByKey(label.schema_conflicts, item.key)">×</i>
                  </article>
                </div>

                <div class="submit-panel">
                  <p>提交后当前盲标槽位将锁定；另一位标注员和 AI 输出仍不会在本页面显示。</p>
                  <button type="button" :disabled="blockingCheckCount > 0 || submitting" @click="handleSubmit">
                    {{ submitting ? '正在提交…' : `提交第 ${activeDetail.submission.slot_no} 号盲标` }}
                  </button>
                </div>
              </section>
            </fieldset>
          </div>
          <div v-else class="inspector__empty">
            <span>五步标注</span>
            <p>领取任务后，这里会显示结构化编辑器。</p>
          </div>

          <footer class="inspector__footer">
            <span v-if="saveMessage">{{ saveMessage }}</span>
            <span v-else>Ctrl + S 保存 · Alt + 1/2/3 切换人物等级</span>
          </footer>
        </aside>
      </div>
      <datalist id="annotation-era-options">
        <option
          v-for="item in dictionaries.eras"
          :key="`${item.dynasty}-${item.era}`"
          :value="item.era"
        >{{ item.dynasty }} · {{ item.start_year }}—{{ item.end_year }}</option>
      </datalist>
      <datalist id="annotation-history-options">
        <option
          v-for="item in dictionaries.historical_events"
          :key="item.id"
          :value="item.event_name"
        >{{ item.year_label }}</option>
      </datalist>
    </div>
  </AppLayout>
</template>

<style scoped>
.workbench {
  --ink: #2d2a25;
  --muted: #797268;
  --paper: #fbf8ef;
  --paper-deep: #f2ecdf;
  --line: #ded6c8;
  --vermilion: #a34432;
  --vermilion-dark: #7d3025;
  --teal: #416f77;
  height: 100vh;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  background: var(--paper);
}

.workbench button,
.workbench input,
.workbench select,
.workbench textarea {
  font: inherit;
}

.workbench__topbar {
  height: 74px;
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 160px auto auto;
  align-items: center;
  gap: 24px;
  padding: 0 22px;
  border-bottom: 1px solid var(--line);
  background: rgba(251, 248, 239, 0.96);
}

.workbench__modes { display: flex; border-bottom: 1px solid #c9bfae; }
.workbench__modes a,
.workbench__modes span { padding: 6px 9px; color: var(--muted); font-size: 10px; text-decoration: none; white-space: nowrap; }
.workbench__modes span { border-bottom: 2px solid var(--vermilion); color: var(--vermilion); font-weight: 650; }

.workbench__identity {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.workbench__eyebrow {
  writing-mode: vertical-rl;
  font-size: 8px;
  line-height: 1;
  letter-spacing: 0.14em;
  color: var(--vermilion);
}

.workbench__identity h2,
.workbench__identity p {
  margin: 0;
}

.workbench__identity h2 {
  font-family: 'Noto Serif SC', 'Songti SC', serif;
  font-size: 19px;
  letter-spacing: 0.08em;
}

.workbench__identity p {
  margin-top: 3px;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.workbench__progress {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--muted);
}

.workbench__progress i {
  width: 120px;
  height: 2px;
  display: block;
  background: #d8d0c2;
}

.workbench__progress b {
  height: 100%;
  display: block;
  background: var(--vermilion);
  transition: width 240ms ease;
}

.workbench__actions {
  display: flex;
  align-items: center;
  gap: 9px;
}

.save-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 5px;
  font-size: 12px;
  color: var(--muted);
}

.save-indicator i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #aaa296;
}

.save-indicator[data-state='dirty'] i,
.save-indicator[data-state='saving'] i { background: #b97938; }
.save-indicator[data-state='saved'] i { background: #4f765d; }
.save-indicator[data-state='error'] i { background: #aa3d31; }

.button {
  border: 1px solid var(--line);
  padding: 8px 12px;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
  transition: background-color 140ms ease, color 140ms ease, border-color 140ms ease;
}

.button:hover:not(:disabled) { border-color: #b8ad9c; background: #f4eee2; }
.button--primary { border-color: var(--vermilion); background: var(--vermilion); color: #fff9f0; }
.button--primary:hover:not(:disabled) { background: var(--vermilion-dark); border-color: var(--vermilion-dark); }
.button:disabled { opacity: 0.45; cursor: not-allowed; }

.workbench__body {
  height: calc(100vh - 74px);
  display: grid;
  grid-template-columns: 238px minmax(420px, 1fr) 420px;
  min-width: 0;
}

.task-rail,
.inspector {
  min-width: 0;
  background: #f4efe4;
}

.task-rail {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--line);
}

.task-rail__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 17px 16px 12px;
}

.task-rail__heading div { display: flex; align-items: baseline; gap: 8px; }
.task-rail__heading span { font-size: 13px; font-weight: 650; }
.task-rail__heading strong { color: var(--vermilion); font-size: 12px; }
.task-rail__heading button { border: 0; background: transparent; color: var(--muted); cursor: pointer; font-size: 17px; }

.task-filter {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  padding: 0 14px 12px;
  border-bottom: 1px solid var(--line);
}

.task-filter button {
  border: 0;
  border-bottom: 1px solid transparent;
  padding: 7px 3px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  cursor: pointer;
}

.task-filter button.active { color: var(--vermilion); border-color: var(--vermilion); font-weight: 650; }

.task-create {
  display: grid;
  gap: 7px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}

.task-create label { color: var(--muted); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; }
.task-create select { min-width: 0; border: 1px solid var(--line); padding: 7px; background: var(--paper); color: var(--ink); font-size: 11px; }
.task-create button { border: 0; padding: 7px; background: #dfd6c7; color: #514b43; font-size: 11px; cursor: pointer; }

.task-list { flex: 1; overflow-y: auto; }
.task-list__empty { margin: 28px 18px; color: var(--muted); font-size: 12px; line-height: 1.7; }

.task-item {
  width: 100%;
  display: grid;
  grid-template-columns: 38px 1fr;
  gap: 5px 8px;
  padding: 13px 14px;
  border: 0;
  border-bottom: 1px solid rgba(207, 198, 183, 0.68);
  text-align: left;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
  transition: background-color 140ms ease;
}

.task-item:hover:not(:disabled) { background: #eee6d8; }
.task-item.active { background: var(--paper); box-shadow: inset 3px 0 0 var(--vermilion); }
.task-item:disabled { opacity: 0.55; cursor: not-allowed; }
.task-item__index { grid-row: span 2; color: #9c9284; font: 10px/1.5 ui-monospace, monospace; }
.task-item strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 12px/1.5 'Noto Serif SC', serif; }
.task-item small { display: flex; justify-content: space-between; color: var(--muted); font-size: 10px; }

.text-workspace {
  position: relative;
  min-width: 0;
  overflow: hidden;
  background-color: var(--paper);
  background-image: linear-gradient(rgba(115, 100, 78, 0.025) 1px, transparent 1px);
  background-size: 100% 32px;
}

.text-workspace__meta {
  height: 66px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 24px;
  border-bottom: 1px solid rgba(222, 214, 200, 0.78);
  background: rgba(251, 248, 239, 0.92);
}

.text-workspace__meta span { color: var(--muted); font: 9px ui-monospace, monospace; letter-spacing: 0.12em; }
.text-workspace__meta h3 { margin: 3px 0 0; font: 600 16px 'Noto Serif SC', serif; }
.text-workspace__scroll { height: calc(100% - 66px); overflow-y: auto; }

.annotation-legend { display: flex; gap: 11px; }
.annotation-legend span { display: flex; align-items: center; gap: 5px; letter-spacing: 0; font-family: inherit; }
.annotation-legend i { width: 13px; height: 4px; background: var(--vermilion); }
.annotation-legend i[data-kind='event'] { background: #3f7280; }
.annotation-legend i[data-kind='relation'] { background: #9a702f; }
.annotation-legend i[data-kind='unresolved'] { background: #b56b2d; }

.workspace-empty {
  height: 100%;
  display: grid;
  place-content: center;
  justify-items: center;
  padding: 30px;
  text-align: center;
}

.workspace-empty > span {
  width: 74px;
  height: 74px;
  display: grid;
  place-items: center;
  border: 1px solid #c9bdab;
  border-radius: 50%;
  color: var(--vermilion);
  font: 600 18px 'Noto Serif SC', serif;
}

.workspace-empty h3 { margin: 22px 0 7px; font: 600 20px 'Noto Serif SC', serif; }
.workspace-empty p { margin: 0; color: var(--muted); font-size: 13px; }

.selection-dock {
  position: absolute;
  z-index: 8;
  left: 24px;
  right: 24px;
  bottom: 18px;
  display: grid;
  grid-template-columns: minmax(130px, 0.6fr) 1.4fr;
  gap: 12px 20px;
  padding: 14px 16px;
  border: 1px solid #cdbdab;
  background: rgba(45, 42, 37, 0.96);
  color: #fffaf1;
  box-shadow: 0 14px 38px rgba(44, 35, 27, 0.24);
}

.selection-dock__close { position: absolute; top: 6px; right: 8px; border: 0; background: transparent; color: #d8cfc2; cursor: pointer; }
.selection-dock > div:first-of-type { min-width: 0; display: grid; gap: 2px; }
.selection-dock span { color: #bdb4a8; font-size: 9px; text-transform: uppercase; }
.selection-dock strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 500 13px 'Noto Serif SC', serif; }
.selection-dock__actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.selection-dock__actions button { border: 1px solid #6b655d; padding: 6px 8px; background: transparent; color: #f7efe5; font-size: 10px; cursor: pointer; }
.selection-dock__actions button:first-child { border-color: #cf6a56; background: var(--vermilion); }
.selection-dock__actions button:disabled { opacity: 0.35; cursor: not-allowed; }
.selection-dock-enter-active, .selection-dock-leave-active { transition: opacity 160ms ease, transform 160ms ease; }
.selection-dock-enter-from, .selection-dock-leave-to { opacity: 0; transform: translateY(10px); }

.inspector {
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--line);
}

.step-nav {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  border-bottom: 1px solid var(--line);
  background: #ede6da;
}

.step-nav button {
  display: grid;
  justify-items: center;
  gap: 3px;
  border: 0;
  border-right: 1px solid var(--line);
  padding: 9px 2px 8px;
  background: transparent;
  color: var(--muted);
  font-size: 10px;
  cursor: pointer;
}

.step-nav button:last-child { border-right: 0; }
.step-nav button span { font: 8px ui-monospace, monospace; }
.step-nav button.active { color: var(--vermilion); background: var(--paper); box-shadow: inset 0 -2px 0 var(--vermilion); }
.step-nav button:disabled { cursor: default; opacity: 0.45; }

.inspector__scroll { flex: 1; min-height: 0; overflow-y: auto; }
.inspector fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.inspector-section { display: grid; gap: 15px; padding: 22px 20px 70px; }
.inspector-section > header { padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.inspector-section > header span { color: var(--vermilion); font: 9px ui-monospace, monospace; letter-spacing: 0.13em; }
.inspector-section > header h3 { margin: 5px 0 6px; font: 600 18px 'Noto Serif SC', serif; }
.inspector-section > header p { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; }

.field { min-width: 0; display: grid; gap: 6px; }
.field > span,
.probe-grid label > span,
.location-grid label > span { color: #655f56; font-size: 10px; font-weight: 650; }
.field input,
.field select,
.field textarea,
.probe-grid select,
.location-grid input,
.review-list input,
.review-list select,
.review-list textarea {
  width: 100%;
  min-width: 0;
  border: 1px solid #d5ccbd;
  border-radius: 0;
  padding: 8px 9px;
  outline: none;
  background: rgba(255, 252, 246, 0.72);
  color: var(--ink);
  font-size: 12px;
  transition: border-color 140ms ease, background-color 140ms ease;
}

.field input:focus,
.field select:focus,
.field textarea:focus,
.review-list input:focus,
.review-list textarea:focus { border-color: var(--vermilion); background: #fffdf8; }
.field textarea,
.review-list textarea { resize: vertical; line-height: 1.55; }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.field-row--three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.probe-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.probe-grid label { display: grid; gap: 5px; }

.object-tabs {
  display: flex;
  gap: 6px;
  padding-bottom: 3px;
  overflow-x: auto;
}

.object-tabs button {
  flex: 0 0 auto;
  display: grid;
  gap: 1px;
  border: 1px solid var(--line);
  padding: 7px 9px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.object-tabs button strong { font-size: 11px; }
.object-tabs button span { color: var(--muted); font-size: 9px; }
.object-tabs button.active { border-color: var(--vermilion); background: #fffaf1; }
.object-tabs .object-tabs__add { place-items: center; border-style: dashed; color: var(--vermilion); }
.object-tabs button:disabled { opacity: 0.45; cursor: not-allowed; }

.level-switch { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); }
.level-switch button { border: 0; padding: 7px; background: #f7f2e8; color: var(--muted); cursor: pointer; }
.level-switch button.active { background: var(--vermilion); color: white; }

.evidence-block { display: grid; gap: 7px; padding: 11px; border-left: 2px solid #c6b9a6; background: rgba(237, 230, 218, 0.68); }
.evidence-block__title { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
.evidence-block__title span { font-size: 10px; font-weight: 650; }
.evidence-block__title small { color: var(--muted); font-size: 9px; }
.evidence-block > p { margin: 2px 0; color: var(--muted); font-size: 10px; }
.evidence-chip { display: flex; align-items: center; justify-content: space-between; gap: 8px; border: 0; padding: 5px 7px; background: #fffaf1; color: var(--ink); text-align: left; font-size: 10px; cursor: pointer; }
.evidence-chip span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evidence-chip i { color: var(--muted); font-style: normal; font-size: 14px; }

.danger-link { justify-self: start; border: 0; border-bottom: 1px solid #b96659; padding: 3px 0; background: transparent; color: #9c4033; font-size: 10px; cursor: pointer; }
.inspector-empty { margin: 12px 0; padding: 20px 12px; border: 1px dashed #cfc5b6; color: var(--muted); text-align: center; font-size: 11px; line-height: 1.7; }

.event-checks { display: grid; gap: 5px; }
.event-checks label { display: grid; grid-template-columns: 42px 1fr 28px; align-items: center; gap: 6px; }
.event-checks label span { font-size: 10px; font-weight: 650; }
.event-checks select { min-width: 0; border: 1px solid var(--line); padding: 6px; background: var(--paper); color: var(--ink); font-size: 10px; }
.event-checks button { height: 28px; border: 1px solid var(--line); background: #e9e1d4; color: var(--vermilion); cursor: pointer; }

.event-list { display: flex; gap: 5px; overflow-x: auto; padding: 2px 0; }
.event-list button { flex: 0 0 auto; display: grid; gap: 2px; max-width: 135px; border: 1px solid var(--line); padding: 7px 8px; background: transparent; text-align: left; cursor: pointer; }
.event-list button span { color: var(--vermilion); font-size: 8px; }
.event-list button strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; }
.event-list button.active { border-color: var(--teal); background: #edf4f2; }
.event-list .event-list__add { place-content: center; border-style: dashed; color: var(--teal); }

.object-editor { display: grid; gap: 13px; padding-top: 15px; border-top: 1px solid var(--line); }
.object-editor__heading { display: flex; align-items: center; justify-content: space-between; }
.object-editor__heading strong { font: 600 13px 'Noto Serif SC', serif; }
.object-editor__heading button { border: 0; background: transparent; color: #9c4033; font-size: 10px; cursor: pointer; }
.subsection { display: grid; gap: 10px; padding: 11px; border: 1px solid var(--line); }
.subsection h4 { margin: 0; color: var(--teal); font-size: 11px; }
.location-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.location-grid label { display: grid; gap: 4px; }
.check-line { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 11px; }

.relation-direction { display: grid; grid-template-columns: 1fr auto 1fr; align-items: end; gap: 8px; }
.relation-direction > span { padding-bottom: 9px; color: var(--vermilion); font: 600 11px 'Noto Serif SC', serif; }
.relation-code-help { margin: -6px 0 0; color: var(--muted); font-size: 9px; line-height: 1.6; }
.dictionary-hint { color: var(--muted); font-size: 9px; line-height: 1.5; }
.reverse-suggestions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: -4px; }
.reverse-suggestions > span { width: 100%; color: var(--muted); font-size: 9px; }
.reverse-suggestions button { min-width: 34px; border: 1px solid #cfc5b6; padding: 4px 7px; background: transparent; color: var(--ink); cursor: pointer; font: 600 10px ui-monospace, monospace; }
.reverse-suggestions button.active { border-color: var(--vermilion); background: #f1dfd7; color: var(--vermilion-dark); }

.check-summary { display: grid; grid-template-columns: auto 1fr; align-items: baseline; column-gap: 8px; padding: 14px 0; border-block: 1px solid var(--line); }
.check-summary strong { color: var(--vermilion); font: 500 30px ui-monospace, monospace; }
.check-summary span { font: 600 13px 'Noto Serif SC', serif; }
.check-summary small { grid-column: 1 / -1; color: var(--muted); font-size: 10px; }
.check-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.check-list li { display: grid; grid-template-columns: 7px 1fr; align-items: start; gap: 7px; font-size: 10px; line-height: 1.55; }
.check-list li i { width: 6px; height: 6px; margin-top: 5px; border-radius: 50%; background: #b7793e; }
.check-list li[data-level='error'] i { background: #ab4134; }
.check-list li[data-level='ok'] i { background: #4f765d; }

.review-list { display: grid; gap: 8px; padding-top: 10px; border-top: 1px solid var(--line); }
.review-list__heading { display: flex; justify-content: space-between; align-items: center; }
.review-list__heading strong { font: 600 12px 'Noto Serif SC', serif; }
.review-list__heading span { color: var(--muted); font-size: 10px; }
.review-list__heading button { border: 0; background: transparent; color: var(--vermilion); font-size: 10px; cursor: pointer; }
.review-list article { position: relative; display: grid; gap: 5px; padding: 9px 25px 9px 9px; background: rgba(237, 230, 218, 0.62); }
.review-list article > button { justify-self: start; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; border: 0; padding: 0; background: transparent; color: var(--teal); text-align: left; font-size: 10px; cursor: pointer; }
.review-list article > i { position: absolute; top: 7px; right: 8px; color: var(--muted); font-style: normal; cursor: pointer; }

.submit-panel { display: grid; gap: 10px; padding: 14px; background: #2f2c27; color: #f5ede2; }
.submit-panel p { margin: 0; color: #c9c0b5; font-size: 10px; line-height: 1.6; }
.submit-panel button { border: 1px solid var(--vermilion); padding: 9px; background: var(--vermilion); color: white; cursor: pointer; }
.submit-panel button:disabled { border-color: #625b53; background: #514b44; color: #948b81; cursor: not-allowed; }

.inspector__empty { flex: 1; display: grid; place-content: center; gap: 7px; padding: 30px; text-align: center; color: var(--muted); }
.inspector__empty span { color: var(--vermilion); font: 600 16px 'Noto Serif SC', serif; }
.inspector__empty p { margin: 0; font-size: 11px; }
.inspector__footer { min-height: 34px; display: flex; align-items: center; padding: 7px 14px; border-top: 1px solid var(--line); background: #e9e1d4; color: var(--muted); font-size: 9px; }

@media (max-width: 1320px) {
  .workbench__body { grid-template-columns: 205px minmax(380px, 1fr) 370px; }
  .workbench__topbar { grid-template-columns: minmax(220px, 1fr) 140px auto; gap: 14px; }
  .workbench__modes { display: none; }
  .workbench__progress i { width: 80px; }
  .save-indicator { display: none; }
}

@media (max-width: 1050px) {
  .workbench { height: auto; min-height: 100vh; overflow: visible; }
  .workbench__topbar { position: sticky; top: 0; z-index: 20; grid-template-columns: 1fr auto; }
  .workbench__progress { display: none; }
  .workbench__body { height: auto; grid-template-columns: 190px minmax(360px, 1fr); }
  .task-rail { min-height: calc(100vh - 74px); }
  .text-workspace { min-height: calc(100vh - 74px); }
  .inspector { grid-column: 1 / -1; min-height: 720px; border-top: 1px solid var(--line); border-left: 0; }
  .inspector__scroll { overflow: visible; }
}

@media (max-width: 760px) {
  .workbench__topbar { height: auto; min-height: 74px; padding-block: 10px; }
  .workbench__identity p { max-width: 180px; }
  .workbench__actions .button--quiet { display: none; }
  .workbench__body { grid-template-columns: 1fr; }
  .task-rail { min-height: 0; max-height: 280px; border-right: 0; border-bottom: 1px solid var(--line); }
  .text-workspace { min-height: 70vh; }
  .selection-dock { left: 10px; right: 10px; grid-template-columns: 1fr; }
  .field-row,
  .field-row--three,
  .probe-grid { grid-template-columns: 1fr; }
}

/* 与现有古籍上传、手工输入和审核页共用蓝白视觉系统。 */
.workbench {
  --ink: #31456f;
  --muted: #7183a8;
  --paper: #ffffff;
  --paper-deep: #fafcff;
  --line: #e4ebf7;
  --vermilion: #2f6fed;
  --vermilion-dark: #245bc7;
  --teal: #5079cf;
  background: #f8fbff;
  color: #44536f;
}

.workbench__topbar {
  height: 82px;
  padding: 0 24px;
  border-bottom: 1px solid #e8eef8;
  background: #ffffff;
  box-shadow: 0 8px 24px rgba(115, 137, 177, 0.08);
}

.workbench__eyebrow {
  writing-mode: horizontal-tb;
  padding: 4px 8px;
  border-radius: 8px;
  background: #e6efff;
  color: #2f6fed;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  white-space: nowrap;
}

.workbench__identity h2,
.text-workspace__meta h3,
.workspace-empty h3,
.inspector-section > header h3,
.object-editor__heading strong,
.review-list__heading strong,
.check-summary span {
  font-family: 'Segoe UI', 'PingFang SC', sans-serif;
  letter-spacing: 0;
}

.workbench__identity h2 { color: #31456f; font-size: 20px; }
.workbench__identity p { color: #7183a8; font-size: 12px; }
.workbench__progress i { height: 5px; border-radius: 999px; background: #e3eaf6; overflow: hidden; }
.workbench__progress b { border-radius: inherit; background: #2f6fed; }
.workbench__modes { border: 1px solid #dce6f5; border-radius: 10px; overflow: hidden; }
.workbench__modes a,
.workbench__modes span { padding: 7px 11px; }
.workbench__modes span { border: 0; background: #2f6fed; color: #ffffff; }

.button { border-color: #dce6f5; border-radius: 10px; background: #ffffff; color: #506287; }
.button:hover:not(:disabled) { border-color: #8fb3ff; background: #f0f6ff; color: #2f6fed; }
.button--primary { border-color: #2f6fed; background: #2f6fed; color: #ffffff; }
.button--primary:hover:not(:disabled) { border-color: #245bc7; background: #245bc7; }

.workbench__body {
  height: calc(100vh - 82px);
  gap: 16px;
  padding: 16px;
  background: #f8fbff;
}

.task-rail,
.text-workspace,
.inspector {
  border: 0;
  border-radius: 18px;
  background: #ffffff;
  box-shadow: 0 14px 36px rgba(115, 137, 177, 0.12);
  overflow: hidden;
}

.task-rail__heading { padding: 18px 16px 12px; }
.task-rail__heading span { color: #31456f; }
.task-filter { gap: 4px; padding: 0 12px 12px; border-bottom-color: #eef2f8; }
.task-filter button { border: 0; border-radius: 9px; padding: 7px 4px; }
.task-filter button.active { border: 0; background: #e8f1ff; color: #2f6fed; }
.task-create { margin: 0 10px 10px; padding: 12px; border: 1px solid #e4ebf7; border-radius: 12px; background: #fafcff; }
.task-create label { color: #7183a8; letter-spacing: 0; text-transform: none; }
.task-create select { border-color: #dce6f5; border-radius: 9px; padding: 8px; background: #ffffff; color: #44536f; }
.task-create select.task-state--established { border-color: #8fb3ff; background: #f5f9ff; color: #2f6fed; }
.task-create select.task-state--completed { border-color: #91d4b8; background: #f2fbf6; color: #23865f; }
.task-create option.task-state--uncreated { color: #44536f; }
.task-create option.task-state--established { color: #2f6fed; }
.task-create option.task-state--completed { color: #23865f; font-weight: 600; }
.task-create__legend { display: flex; align-items: center; gap: 10px; color: #7183a8; font-size: 9px; }
.task-create__legend span { display: inline-flex; align-items: center; gap: 4px; }
.task-create__legend i { width: 6px; height: 6px; border-radius: 50%; background: #aab5c8; }
.task-create__legend span[data-state='established'] i { background: #2f6fed; }
.task-create__legend span[data-state='completed'] i { background: #32a474; }
.task-create__notice { margin: 0; font-size: 10px; line-height: 1.5; }
.task-create__notice[data-state='established'] { color: #2f6fed; }
.task-create__notice[data-state='completed'] { color: #23865f; }
.task-create button { border-radius: 9px; padding: 8px; background: #2f6fed; color: #ffffff; font-weight: 600; }
.task-create button:disabled { background: #e7edf7; color: #8b99b2; cursor: not-allowed; }
.task-list { padding: 0 8px 10px; }
.task-item { width: calc(100% - 4px); margin: 4px 2px; border: 1px solid #e4ebf7; border-radius: 12px; background: #fafcff; }
.task-item:hover:not(:disabled) { border-color: #8fb3ff; background: #f0f6ff; }
.task-item.active { border-color: #2f6fed; background: #eef5ff; box-shadow: none; }
.task-item strong { color: #31456f; font-family: 'Segoe UI', 'PingFang SC', sans-serif; }
.task-item__index { color: #8a97b3; }

.text-workspace { background: #ffffff; background-image: none; }
.text-workspace__meta { border-bottom-color: #e9eef8; background: #fafcff; }
.text-workspace__meta span { color: #7085b0; }
.text-workspace__meta h3 { color: #31456f; }
.workspace-empty > span { border-color: #b8cdf6; background: #f5f8ff; color: #2f6fed; }
.workspace-empty h3 { color: #31456f; }

.selection-dock {
  border: 1px solid #8fb3ff;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.98);
  color: #31456f;
  box-shadow: 0 18px 42px rgba(74, 103, 160, 0.2);
}
.selection-dock__close { color: #7183a8; }
.selection-dock span { color: #7183a8; }
.selection-dock strong { color: #31456f; }
.selection-dock__actions button { border-color: #dce6f5; border-radius: 8px; background: #f7faff; color: #506287; }
.selection-dock__actions button:first-child { border-color: #2f6fed; background: #2f6fed; color: #ffffff; }

.step-nav { border-bottom-color: #e4ebf7; background: #f5f8ff; }
.step-nav button { border-right-color: #e4ebf7; color: #7183a8; }
.step-nav button.active { background: #eef5ff; color: #2f6fed; box-shadow: inset 0 -3px 0 #2f6fed; }
.inspector-section > header { border-bottom-color: #e9eef8; }
.inspector-section > header span { color: #2f6fed; }
.inspector-section > header h3 { color: #31456f; }

.field > span,
.probe-grid label > span,
.location-grid label > span { color: #506287; }
.field input,
.field select,
.field textarea,
.probe-grid select,
.location-grid input,
.review-list input,
.review-list select,
.review-list textarea {
  border-color: #dce6f5;
  border-radius: 10px;
  background: #ffffff;
  color: #44536f;
}
.field input:focus,
.field select:focus,
.field textarea:focus,
.review-list input:focus,
.review-list textarea:focus { border-color: #2f6fed; background: #ffffff; box-shadow: 0 0 0 3px rgba(47, 111, 237, 0.1); }

.object-tabs button,
.event-list button { border-color: #e4ebf7; border-radius: 10px; background: #fafcff; }
.object-tabs button.active,
.event-list button.active { border-color: #2f6fed; background: #eef5ff; }
.object-tabs .object-tabs__add,
.event-list .event-list__add { color: #2f6fed; }
.level-switch { gap: 6px; border: 0; background: transparent; }
.level-switch button { border: 1px solid #e4ebf7; border-radius: 9px; background: #fafcff; }
.level-switch button.active { border-color: #2f6fed; background: #2f6fed; }
.evidence-block { border: 1px solid #dfe8f7; border-left: 3px solid #8fb3ff; border-radius: 12px; background: #f7faff; }
.evidence-chip { border-radius: 8px; background: #ffffff; color: #44536f; }
.event-checks select { border-color: #dce6f5; border-radius: 9px; background: #ffffff; color: #44536f; }
.event-checks button { border-color: #dce6f5; border-radius: 8px; background: #e8f1ff; color: #2f6fed; }
.subsection { border-color: #e4ebf7; border-radius: 12px; background: #fafcff; }
.subsection h4 { color: #2f6fed; }
.reverse-suggestions button { border-color: #dce6f5; border-radius: 8px; background: #fafcff; }
.reverse-suggestions button.active { border-color: #2f6fed; background: #e8f1ff; color: #2f6fed; }
.review-list article { border: 1px solid #e9eef8; border-radius: 10px; background: #fafcff; }
.submit-panel { border: 1px solid #d6e4ff; border-radius: 12px; background: #eef5ff; color: #31456f; }
.submit-panel p { color: #586a8e; }
.submit-panel button { border: 0; border-radius: 10px; background: #2f6fed; }
.submit-panel button:disabled { background: #b6c4e3; color: #ffffff; }
.inspector__footer { border-top-color: #e4ebf7; background: #f5f8ff; color: #7183a8; }

@media (max-width: 1050px) {
  .workbench__body { height: auto; }
  .task-rail,
  .text-workspace,
  .inspector { border-radius: 14px; }
}
</style>
