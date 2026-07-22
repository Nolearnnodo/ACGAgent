import {
  EVENT_TYPES,
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
  message: string
}

export function uid(prefix: string) {
  const suffix = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

export function createEmptyLabel(specVersion = '0.2.0'): ExtractionAnnotationLabel {
  return {
    schema_version: specVersion,
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
    codes: ['O'],
    reverse_codes: ['O'],
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
        label: `人物关系 · ${relation.codes.join('') || '未定义'}`,
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
  return highlights
}

export function runClientChecks(label: ExtractionAnnotationLabel | null): ClientCheck[] {
  if (!label) return []
  const checks: ClientCheck[] = []
  if (!label.persons.length) {
    checks.push({ level: 'error', message: '至少需要建立一位人物。' })
    return checks
  }
  const levelOneCount = label.persons.filter((person) => person.level === 1).length
  const hasMultiConflict = label.schema_conflicts.some((item) =>
    ['multiple_protagonists', 'material_unusable'].includes(item.code),
  )
  if (levelOneCount !== 1 && !hasMultiConflict) {
    checks.push({ level: 'error', message: '普通文献必须有且仅有一位一级人物。' })
  }
  for (const person of label.persons) {
    const name = person.name_surface || '未命名人物'
    if (!person.name_surface.trim()) checks.push({ level: 'error', message: '有人物尚未填写原文姓名。' })
    if (!person.mentions.length) checks.push({ level: 'error', message: `${name} 没有原文 mention。` })
    if (!person.level_reason.trim()) checks.push({ level: 'error', message: `${name} 尚未填写分级理由。` })
    if (person.level === 3 && (person.life_events.length || person.historical_events.length)) {
      checks.push({ level: 'error', message: `${name} 是三级人物，不能进入主评测事件。` })
    }
    if (person.level !== 3) {
      for (const eventType of EVENT_TYPES) {
        const state = person.event_checks[eventType]
        const eventCount = person.life_events.filter((event) => event.event_type === eventType).length
        if (state === 'unreviewed') {
          checks.push({ level: 'error', message: `${name} 尚未检查${eventType}事件。` })
        } else if (state === 'has_fact' && !eventCount) {
          checks.push({ level: 'error', message: `${name} 的${eventType}标为有事实，但没有事件记录。` })
        } else if (eventCount && state !== 'has_fact') {
          checks.push({ level: 'error', message: `${name} 已有${eventType}事件，检查状态需要改为“有事实”。` })
        }
      }
    }
    for (const event of person.life_events) {
      if (!event.evidence.length) checks.push({ level: 'error', message: `${name} 的${event.event_type}事件缺少证据。` })
      if (event.event_type === '任职' && !event.official_title.trim()) {
        checks.push({ level: 'error', message: `${name} 的任职事件缺少官职。` })
      }
    }
    for (const event of person.historical_events) {
      if (!event.event_name.trim() || !event.relation_summary.trim() || !event.evidence.length) {
        checks.push({ level: 'error', message: `${name} 有未填写完整的历史事件关联。` })
      }
    }
  }
  for (const relation of label.person_relations) {
    if (
      relation.source_person_key === relation.target_person_key
      || !relation.codes.length
      || !relation.reverse_codes.length
      || !relation.evidence.length
    ) {
      checks.push({ level: 'error', message: '存在未填写完整的人物关系。' })
    }
    if (
      relation.codes.length
      && relation.reverse_codes.length
      && !areInverseRelationChains(relation.codes, relation.reverse_codes)
    ) {
      checks.push({ level: 'error', message: '存在正向与反向代码链不互逆的人物关系。' })
    }
  }
  for (const item of label.unresolved_items) {
    if (!item.note.trim()) checks.push({ level: 'warning', message: '有“暂不能判断”项尚未填写说明。' })
  }
  if (!checks.length) {
    checks.push({ level: 'ok', message: '当前标签已通过前端完整性检查，可提交后端复核。' })
  }
  return checks
}
