import apiClient from './client'

export interface IdentityReviewItem {
  source_person_id: number
  source_name: string | null
  target_person_id: number
  target_name: string | null
  confidence: number
  reason: string
  evidence: Record<string, unknown> | null
  source_passages: string[]
  target_passages: string[]
  hops: number | null
  llm_decision: string | null
  llm_used_full_text: boolean
  annotated: boolean
}

export interface IdentityAIReport {
  id: number
  source_person_id: number
  target_person_id: number
  source_name: string | null
  target_name: string | null
  report_markdown: string
  updated_at: string | null
}

export interface IdentityReviewListResponse {
  pending_count: number
  items: IdentityReviewItem[]
}

export interface GraphElements {
  nodes: Array<{ id: string; label: string; type: string; properties: Record<string, unknown> }>
  edges: Array<{ source: string; target: string; label: string; properties: Record<string, unknown> }>
}

export interface PassageText {
  doc_id: number
  title: string
  context: string
}

export interface IdentityDecisionLog {
  hop: number
  decision: string
  confidence: number
  reason: string
  positive_evidence: string[]
  negative_evidence: string[]
  missing_evidence: string[]
  used_full_text: boolean
  created_at: string | null
}

export interface IdentityEvidenceResponse {
  source_evidence: Record<string, unknown>
  target_evidence: Record<string, unknown>
  graph_elements: GraphElements
  review_relation: Record<string, unknown> | null
  decision_logs: IdentityDecisionLog[]
  source_passage_texts: PassageText[]
  target_passage_texts: PassageText[]
  ai_report: IdentityAIReport | null
}

export interface AdjudicateResponse {
  status: string
  action: string
  detail: Record<string, unknown>
}

export interface AnnotateResponse {
  status: string
  annotation_id: number
}

export interface GenerateIdentityReportsResponse {
  pair_count: number
  created_count: number
  updated_count: number
  skipped_count: number
  failed_count: number
  failures: Array<Record<string, unknown>>
}

export async function listPendingReviews(mode: 'pending' | 'annotation' = 'pending') {
  const { data } = await apiClient.get<IdentityReviewListResponse>(
    '/review/identity-pending',
    { params: { mode } },
  )
  return data
}

export async function getReviewEvidence(sourceId: number, targetId: number) {
  const { data } = await apiClient.get<IdentityEvidenceResponse>(
    `/review/identity/${sourceId}/${targetId}/evidence`,
  )
  return data
}

export async function adjudicateIdentity(sourceId: number, targetId: number, decision: string) {
  const { data } = await apiClient.post<AdjudicateResponse>(
    `/review/identity/${sourceId}/${targetId}/adjudicate`,
    { decision },
  )
  return data
}

export async function annotateIdentity(
  sourceId: number,
  targetId: number,
  humanConfidence: number,
  note: string = '',
) {
  const { data } = await apiClient.post<AnnotateResponse>(
    `/review/identity/${sourceId}/${targetId}/annotate`,
    { human_confidence: humanConfidence, note },
  )
  return data
}

export async function generateIdentityReports(force = false) {
  const { data } = await apiClient.post<GenerateIdentityReportsResponse>(
    '/review/identity/reports/generate',
    { force },
  )
  return data
}
