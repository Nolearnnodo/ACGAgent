import apiClient from './client'

export interface PassageSummary {
  doc_id: number
  title: string
  source_type: string
  workflow_status: string
  updated_at: string
}

export interface PassageDetail extends PassageSummary {
  context: string
  file_name: string | null
  created_by: number
  created_at: string
}

export interface PassageStepRun {
  id: number
  step_no: number
  skill_code: string
  status: string
  input_json: string
  output_json: string
  error_message: string
  created_at: string
}

export interface PassageExecutionRun {
  id: number
  trigger_type: string
  status: string
  started_at: string
  finished_at: string | null
  steps: PassageStepRun[]
}

export async function uploadPassages(files: File[]) {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }

  const { data } = await apiClient.post<PassageDetail[]>('/passages/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    timeout: 60000,
  })
  return data
}

export async function createManualPassage(payload: { title: string; context: string }) {
  const { data } = await apiClient.post<PassageDetail>('/passages/manual', payload)
  return data
}

export async function listPassages() {
  const { data } = await apiClient.get<PassageSummary[]>('/passages')
  return data
}

export async function fetchPassage(docId: number) {
  const { data } = await apiClient.get<PassageDetail>(`/passages/${docId}`)
  return data
}

export async function fetchPassageRuns(docId: number) {
  const { data } = await apiClient.get<PassageExecutionRun[]>(`/passages/${docId}/runs`)
  return data
}
