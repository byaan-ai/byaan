import { getBackendUrl } from '../lib/tauri-api'
import { getAccessToken } from './tokenStore'
import { isHostedMode as getRuntimeIsHosted, getApiBaseUrl as getRuntimeApiBaseUrl } from '../lib/runtime-config'

async function getApiRoot(): Promise<string> {
  const runtimeBase = getRuntimeApiBaseUrl()
  if (runtimeBase && runtimeBase !== '/api') {
    return runtimeBase
  }
  if (getRuntimeIsHosted()) {
    return '/api'
  }
  const { isTauriApp } = await import('../lib/tauri-api')
  if (isTauriApp()) {
    const base = await getBackendUrl()
    return `${base}/api`
  }
  return '/api'
}

function getHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAccessToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const tenantId = localStorage.getItem('byaan_active_tenant')
  if (tenantId) {
    headers['X-Tenant-ID'] = tenantId
  }
  return headers
}

async function apiFetch(path: string, init?: RequestInit) {
  const base = await getApiRoot()
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { ...getHeaders(), ...(init?.headers || {}) },
    credentials: 'include',
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(error.detail || error.message || 'Request failed')
  }
  const data = await response.json()
  if (data && data.success === false) {
    throw new Error(data.message || 'Request failed')
  }
  return data
}

export type SuggestionType = 'edit' | 'new_skill' | 'casebook' | 'clarification' | 'promotion'
export type SuggestionStatus = 'pending' | 'approved' | 'rejected' | 'applied' | 'superseded'
export type SuggestionConfidence = 'low' | 'medium' | 'high'

export interface EvidenceItem {
  claim: string
  check: string
  result: string
}

export interface SuggestionPatch {
  section: string
  before: string
  after: string
}

export interface SuggestionSource {
  origin: string
  channel?: string
  channel_id?: string
  thread_ts?: string
  thread_url?: string
  slack_channel_id?: string | null
  slack_thread_ts?: string | null
  notebook_id?: string
  participants?: string[]
  date?: string
  repo_id?: string
  repo_full_name?: string
  base_sha?: string
  head_sha?: string
  compare_url?: string
  files?: string[]
}

export interface SkillSuggestion {
  id: string
  tenant_id: string
  skill_id: string | null
  skill_name: string | null
  suggestion_type: SuggestionType
  title: string
  rationale: string
  evidence: EvidenceItem[] | Record<string, unknown> | null
  patch: SuggestionPatch | null
  proposed_instructions: string | null
  confidence: SuggestionConfidence
  status: SuggestionStatus
  source: SuggestionSource | null
  slack_channel_id: string | null
  slack_message_ts: string | null
  reviewed_by: string | null
  reviewed_via: string | null
  reviewer_slack_user_id: string | null
  reviewer_display_name: string | null
  review_note: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export interface ApproveResult {
  new_version: number | null
}

export interface SkillVersion {
  id: string
  version: number
  changed_by: string | null
  suggestion_id: string | null
  created_at: string
}

export const SkillSuggestionsService = {
  async list(status?: SuggestionStatus): Promise<SkillSuggestion[]> {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    const res = await apiFetch(`/skill-suggestions${query}`)
    return res.data as SkillSuggestion[]
  },

  async getPendingCount(): Promise<number> {
    const res = await apiFetch('/skill-suggestions/pending-count')
    return (res.data?.count as number) ?? 0
  },

  async get(id: string): Promise<SkillSuggestion> {
    const res = await apiFetch(`/skill-suggestions/${id}`)
    return res.data as SkillSuggestion
  },

  async approve(id: string, finalInstructions?: string): Promise<ApproveResult> {
    const res = await apiFetch(`/skill-suggestions/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify(finalInstructions !== undefined ? { final_instructions: finalInstructions } : {}),
    })
    const newVersion = res.data?.new_version
    return { new_version: typeof newVersion === 'number' ? newVersion : null }
  },

  async reject(id: string, reason: string): Promise<SkillSuggestion> {
    const res = await apiFetch(`/skill-suggestions/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    })
    return res.data as SkillSuggestion
  },

  async getSkillVersions(skillId: string): Promise<SkillVersion[]> {
    const res = await apiFetch(`/custom-skills/${skillId}/versions`)
    return res.data as SkillVersion[]
  },
}
