import {
  EVENT_TYPES,
  OMISSION_CATEGORIES,
  RELATION_CATEGORIES,
  RELATION_CODES,
  type CheckState,
  type EventType,
  type EvidenceSpan,
  type ExtractionAnnotationLabel,
  type HistoricalEventAnnotation,
  type LifeEventAnnotation,
  type PersonAnnotation,
  type PersonRelationAnnotation,
  type RelationCode,
} from '../api/extractionAnnotations'

export type HighlightKind = 'person' | 'event' | 'history' | 'relation' | 'excluded' | 'unresolved'

export interface AnnotationHighlight {
  id: string
  evidence: EvidenceSpan
  kind: HighlightKind
  label: string
  personKey?: string
  objectKey?: string
}

export interface ClientCheck {
  level: 'error' | 'warning' | 'ok'
  step: 'material' | 'persons' | 'relations' | 'checks'
  message: string
}

export type EvidenceGranularityKind = 'entity' | 'fact'

export function evidenceGranularityIssue(
  evidence: EvidenceSpan | null,
  kind: EvidenceGranularityKind,
) {
  if (!evidence) return ''
  const quote = evidence.quote
  if (quote !== quote.trim()) return '选区首尾含空白，请重新划选。'
  if (kind === 'entity') {
    if (quote.length > 24 || /[，。！？；：、,.!?;:\n\r]/.test(quote)) {
      return '实体范围过大：只选择姓名、称谓或稳定指代本身，不要选择完整句子。'
    }
    return ''
  }
  if (
    quote.length > 64
    || /[。！？；\n\r]/.test(quote)
    || (quote.length > 32 && /[，：]/.test(quote))
  ) {
    return '事实范围过大：请选择最小充分短语，跨句事实拆成多条短证据。'
  }
  return ''
}

export function uid(prefix: string) {
  const suffix = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

export function createEmptyLabel(specVersion = '0.3.0'): ExtractionAnnotationLabel {
  return {
    schema_version: specVersion,
    step_reviews: { material: false, persons: false, relations: false, checks: false },
    passage: {
      source_type: 'uncertain',
      material_status: 'complete',
      is_female: null,
      is_damaged: null,
      is_clergy: null,
      has_courtesy_name: null,
      era: '',
      era_basis: '',
      note: '',
    },
    persons: [],
    person_relations: [],
    excluded_mentions: [],
    unresolved_items: [],
    schema_conflicts: [],
    omissions: [],
  }
}

export function createPerson(evidence?: EvidenceSpan): PersonAnnotation {
  return {
    key: uid('person'),
    name_surface: evidence?.quote ?? '',
    completed_name: '',
    completion_reason: '',
    courtesy_name: '',
    hao: '',
    titles: [],
    level: 3,
    level_reason: '',
    mentions: evidence ? [evidence] : [],
    event_checks: Object.fromEntries(
      EVENT_TYPES.map((eventType) => [eventType, 'unreviewed']),
    ) as Record<EventType, CheckState>,
    life_events: [],
    historical_events: [],
  }
}

export function createLifeEvent(eventType: EventType): LifeEventAnnotation {
  return {
    key: uid('event'),
    event_type: eventType,
    state: 'confirmed',
    time: {
      state: eventType === '籍贯' ? 'not_applicable' : 'not_mentioned',
      raw: '',
      era: '',
      era_year: null,
      gregorian_year: null,
      month_text: '',
      day_text: '',
    },
    location: {
      state: 'not_mentioned',
      raw: '',
      dao: '',
      fu: '',
      zhou: '',
      jun: '',
      xian: '',
      other: '',
    },
    official_title: '',
    evidence: [],
    note: '',
  }
}

export function createHistoricalEvent(): HistoricalEventAnnotation {
  return {
    key: uid('history'),
    event_name: '',
    outside_dictionary: false,
    relation_summary: '',
    state: 'confirmed',
    evidence: [],
    note: '',
  }
}

export function createPersonRelation(
  sourcePersonKey: string,
  targetPersonKey: string,
): PersonRelationAnnotation {
  return {
    key: uid('relation'),
    source_person_key: sourcePersonKey,
    target_person_key: targetPersonKey,
    relation_category: '',
    relation_summary: '',
    codes: [],
    reverse_codes: [],
    note: '',
    reverse_note: '',
    state: 'confirmed',
    evidence: [],
  }
}

export function parseRelationCodes(value: string): RelationCode[] {
  return value
    .toUpperCase()
    .split('')
    .filter((item): item is RelationCode => RELATION_CODES.includes(item as RelationCode))
    .slice(0, 3)
}

const inverseRelationCodes: Record<RelationCode, RelationCode[]> = {
  F: ['S', 'D'],
  M: ['S', 'D'],
  S: ['F', 'M'],
  D: ['F', 'M'],
  H: ['W', 'Z'],
  W: ['H'],
  Z: ['H'],
  C: ['C'],
  B: ['B'],
  O: ['O'],
}

export function relationReverseSuggestions(codes: RelationCode[]): RelationCode[][] {
  if (!codes.length || codes.length > 3) return []
  return [...codes].reverse().reduce<RelationCode[][]>(
    (chains, code) => chains.flatMap((chain) => inverseRelationCodes[code].map((next) => [...chain, next])),
    [[]],
  )
}

export function areInverseRelationChains(codes: RelationCode[], reverseCodes: RelationCode[]) {
  return relationReverseSuggestions(codes).some(
    (candidate) => candidate.join('') === reverseCodes.join(''),
  )
}

export function collectHighlights(label: ExtractionAnnotationLabel | null): AnnotationHighlight[] {
  if (!label) return []
  const highlights: AnnotationHighlight[] = []
  for (const person of label.persons) {
    for (const evidence of person.mentions) {
      highlights.push({
        id: evidence.id,
        evidence,
        kind: 'person',
        label: person.name_surface || '未命名人物',
        personKey: person.key,
      })
    }
    for (const event of person.life_events) {
      for (const evidence of event.evidence) {
        highlights.push({
          id: evidence.id,
          evidence,
          kind: 'event',
          label: `${person.name_surface || '未命名'} · ${event.event_type}`,
          personKey: person.key,
          objectKey: event.key,
        })
      }
    }
    for (const event of person.historical_events) {
      for (const evidence of event.evidence) {
        highlights.push({
          id: evidence.id,
          evidence,
          kind: 'history',
          label: `${person.name_surface || '未命名'} · ${event.event_name || '历史事件'}`,
          personKey: person.key,
          objectKey: event.key,
        })
      }
    }
  }
  for (const relation of label.person_relations) {
    for (const evidence of relation.evidence) {
      highlights.push({
        id: evidence.id,
        evidence,
        kind: 'relation',
        label: `人物关系 · ${relation.relation_category || '待分类'}${relation.relation_summary ? ` · ${relation.relation_summary}` : ''}`,
        objectKey: relation.key,
      })
    }
  }
  for (const excluded of label.excluded_mentions) {
    for (const evidence of excluded.evidence) {
      highlights.push({
        id: evidence.id,
        evidence,
        kind: 'excluded',
        label: `排除 · ${excluded.reason || '待说明'}`,
        objectKey: excluded.key,
      })
    }
  }
  for (const unresolved of label.unresolved_items) {
    for (const evidence of unresolved.evidence) {
      highlights.push({
        id: evidence.id,
        evidence,
        kind: 'unresolved',
        label: `待定 · ${unresolved.category || '未分类'}`,
        objectKey: unresolved.key,
      })
    }
  }
  for (const omission of label.omissions ?? []) {
    for (const evidence of omission.evidence) {
      highlights.push({
        id: evidence.id,
        evidence,
        kind: 'unresolved',
        label: `缺漏 · ${omission.category || '待分类'}`,
        objectKey: omission.key,
      })
    }
  }
  return highlights
}

export function runClientChecks(label: ExtractionAnnotationLabel | null): ClientCheck[] {
  if (!label) return []
  const checks: ClientCheck[] = []
  type Step = ClientCheck['step']
  const issue = (step: Step, message: string) => checks.push({ level: 'error' as const, step, message })
  const checkEvidence = (evidence: EvidenceSpan, kind: EvidenceGranularityKind, owner: string, step: Step) => {
    const message = evidenceGranularityIssue(evidence, kind)
    if (message) issue(step, `${owner}：${message}`)
  }
  const passage = label.passage
  if (passage.source_type === 'uncertain' && !passage.note.trim()) {
    issue('material', '文献类型暂不能判断时须在材料备注中说明。')
  }
  if (passage.material_status !== 'complete' && !passage.note.trim()) {
    issue('material', '材料有问题或暂不能判断时须在材料备注中说明。')
  }
  if ([passage.is_female, passage.is_damaged, passage.is_clergy, passage.has_courtesy_name].some((value) => value === null)) {
    issue('material', '四项主人公概况均须逐项判断。')
  }
  if (!passage.era_basis.trim()) issue('material', '请填写文献级年号的选择依据，或说明为何暂不能判断。')
  for (const person of label.persons) {
    for (const evidence of person.mentions) {
      checkEvidence(evidence, 'entity', `${person.name_surface || person.key} 的人物证据`, 'persons')
    }
    for (const event of person.life_events) {
      for (const evidence of event.evidence) {
        checkEvidence(evidence, 'fact', `${person.name_surface || person.key} 的${event.event_type}证据`, 'persons')
      }
    }
    for (const event of person.historical_events) {
      for (const evidence of event.evidence) {
        checkEvidence(evidence, 'fact', `${event.event_name || '历史事件'}证据`, 'relations')
      }
    }
  }
  for (const relation of label.person_relations) {
    for (const evidence of relation.evidence) checkEvidence(evidence, 'fact', '人物关系证据', 'relations')
  }
  for (const excluded of label.excluded_mentions) {
    for (const evidence of excluded.evidence) checkEvidence(evidence, 'entity', '排除称谓证据', 'persons')
  }
  for (const unresolved of label.unresolved_items) {
    for (const evidence of unresolved.evidence) checkEvidence(evidence, 'fact', '待判断项证据', 'checks')
  }
  for (const conflict of label.schema_conflicts) {
    for (const evidence of conflict.evidence) checkEvidence(evidence, 'fact', 'Schema 冲突证据', 'checks')
  }
  for (const omission of label.omissions ?? []) {
    for (const evidence of omission.evidence) checkEvidence(evidence, 'fact', '缺漏证据', 'checks')
  }
  if (!label.persons.length) {
    issue('persons', '至少需要建立一位人物。')
    return checks
  }
  const levelOneCount = label.persons.filter((person) => person.level === 1).length
  const hasMultiConflict = label.schema_conflicts.some((item) =>
    ['multiple_protagonists', 'material_unusable'].includes(item.code),
  )
  if (levelOneCount !== 1 && !hasMultiConflict) {
    issue('persons', '普通文献必须有且仅有一位一级人物。')
  }
  for (const person of label.persons) {
    const name = person.name_surface || '未命名人物'
    if (!person.name_surface.trim()) issue('persons', '有人物尚未填写原文姓名。')
    if (!person.mentions.length) issue('persons', `${name} 没有原文指代证据。`)
    if (!person.level_reason.trim()) issue('persons', `${name} 尚未填写分级理由。`)
    if (person.level === 3 && (person.life_events.length || person.historical_events.length)) {
      issue('persons', `${name} 是三级人物，不能进入主评测事件。`)
    }
    if (person.level !== 3) {
      for (const eventType of EVENT_TYPES) {
        const state = person.event_checks[eventType]
        const eventCount = person.life_events.filter((event) => event.event_type === eventType).length
        if (state === 'unreviewed') {
          issue('persons', `${name} 尚未检查${eventType}事件。`)
        } else if (state === 'has_fact' && !eventCount) {
          issue('persons', `${name} 的${eventType}标为有事实，但没有事件记录。`)
        } else if (eventCount && state !== 'has_fact') {
          issue('persons', `${name} 已有${eventType}事件，检查状态需要改为“有事实”。`)
        }
      }
    }
    for (const event of person.life_events) {
      if (!event.evidence.length) issue('persons', `${name} 的${event.event_type}事件缺少证据。`)
      if (event.event_type === '任职' && !event.official_title.trim()) {
        issue('persons', `${name} 的任职事件缺少官职。`)
      }
      if (event.state !== 'confirmed' && !event.note.trim()) issue('persons', `${name} 的${event.event_type}事件须说明待定或工具限制。`)
    }
    for (const event of person.historical_events) {
      if (!event.event_name.trim() || !event.relation_summary.trim() || !event.evidence.length) {
        issue('relations', `${name} 有未填写完整的历史事件关联。`)
      }
      if (event.state !== 'confirmed' && !event.note.trim()) issue('relations', `${name} 的历史事件关联须说明待定或工具限制。`)
    }
  }
  for (const relation of label.person_relations) {
    if (
      !label.persons.some((person) => person.key === relation.source_person_key)
      || !label.persons.some((person) => person.key === relation.target_person_key)
      || relation.source_person_key === relation.target_person_key
      || !RELATION_CATEGORIES.includes(relation.relation_category as typeof RELATION_CATEGORIES[number])
      || !relation.relation_summary?.trim()
      || relation.relation_summary.length > 10
      || !relation.evidence.length
    ) {
      issue('relations', '存在未填写完整的人物关系：须有两端人物、七类关系、10 字内具体表现和证据。')
    }
    if (relation.state !== 'confirmed' && !relation.note.trim()) issue('relations', '待定或工具暂不支持的人物关系须说明原因。')
  }
  for (const item of label.unresolved_items) {
    if (!item.note.trim()) issue('checks', '有“暂不能判断”项尚未填写说明。')
  }
  for (const item of label.schema_conflicts) {
    if (!item.note.trim()) issue('checks', '有工具限制或 Schema 冲突尚未填写说明。')
  }
  for (const item of label.excluded_mentions) {
    if (!item.reason.trim() || !item.evidence.length) issue('persons', '排除称谓须写明原因并绑定原文证据。')
  }
  for (const omission of label.omissions ?? []) {
    if (!OMISSION_CATEGORIES.includes(omission.category as typeof OMISSION_CATEGORIES[number])) {
      issue('checks', '缺漏须选择第 10.3 节中的类型。')
    }
    if (!omission.description.trim() || !omission.target_path.trim()) {
      issue('checks', '缺漏须写明具体对象、内容和应补入的位置。')
    }
    if (!omission.evidence.length && !omission.evidence_note.trim()) {
      issue('checks', '缺漏须绑定原文证据，或写明原文位置。')
    }
    if (omission.resolution === 'open') issue('checks', '还有未处理的缺漏。')
    if (omission.resolution !== 'open' && !omission.resolution_note.trim()) {
      issue('checks', '已处理或工具暂不支持的缺漏须写明处理结果。')
    }
  }
  if (!checks.length) {
    checks.push({ level: 'ok', step: 'checks', message: '当前内容已通过前端完整性检查，可逐步确认后提交。' })
  }
  return checks
}
