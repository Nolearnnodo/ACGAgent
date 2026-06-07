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
}

export interface IdentityReviewListResponse {
  pending_count: number
  items: IdentityReviewItem[]
}

export interface GraphElements {
  nodes: Array<{ id: string; label: string; type: string; properties: Record<string, unknown> }>
  edges: Array<{ source: string; target: string; label: string; properties: Record<string, unknown> }>
}

export interface IdentityEvidenceResponse {
  source_evidence: Record<string, unknown>
  target_evidence: Record<string, unknown>
  graph_elements: GraphElements
  review_relation: Record<string, unknown> | null
}

export interface AdjudicateResponse {
  status: string
  action: string
  detail: Record<string, unknown>
}

export async function listPendingReviews() {
  const { data } = await apiClient.get<IdentityReviewListResponse>('/review/identity-pending')
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
