import { authorizedFetch, extractErrorMessage } from './api'

export type HandoffStatus =
  | 'proposed'
  | 'queued'
  | 'preparing'
  | 'running'
  | 'waiting_on_human'
  | 'pushing'
  | 'ci_running'
  | 'fixing'
  | 'done'
  | 'dismissed'
  | 'cancelled'
  | 'failed'
  | 'ci_failed'
  | 'taken_over'

export const ACTIVE_HANDOFF_STATUSES: HandoffStatus[] = [
  'queued',
  'preparing',
  'running',
  'waiting_on_human',
  'pushing',
  'ci_running',
  'fixing',
]

export const HANDOFF_STATUS_LABELS: Record<HandoffStatus, string> = {
  proposed: 'Draft',
  queued: 'Queued',
  preparing: 'Preparing',
  running: 'Claude is working',
  waiting_on_human: 'Waiting for an answer',
  pushing: 'Opening PR',
  ci_running: 'CI running',
  fixing: 'Fixing CI',
  done: 'Done',
  dismissed: 'Dismissed',
  cancelled: 'Stopped',
  failed: 'Failed',
  ci_failed: 'CI still failing',
  taken_over: 'Handed to a person',
}

export interface HandoffBrief {
  problem: string
  evidence: string[]
  suspected_files: string[]
  acceptance_check: string
}

export interface HandoffEvent {
  seq: number
  source: string
  kind: string
  message: string
  data: Record<string, unknown> | null
  created_at: string
}

export interface Handoff {
  id: string
  github_repository_id: string | null
  repo_full_name: string
  requested_by: string | null
  origin: 'slack' | 'notebook'
  notebook_id: string | null
  schedule_id: string | null
  title: string
  brief: HandoffBrief & Record<string, unknown>
  status: HandoffStatus
  base_branch: string
  branch: string | null
  pr_number: number | null
  pr_url: string | null
  untested: boolean
  ci_fix_round: number
  cost_usd: number
  error: string | null
  heartbeat_at: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export interface HandoffDetail extends Handoff {
  events: HandoffEvent[]
  has_transcript: boolean
  steps: string[]
  activity: string[]
  pending_revision: string | null
  pending_revision_title: string | null
  pending_revision_brief: (HandoffBrief & Record<string, unknown>) | null
  has_run: boolean
}

export interface HandoffPaths {
  allow: string[]
  deny: string[]
}

export interface HandoffRepoConfig {
  paths: HandoffPaths
  minutes: number
  default_channels: string[]
  mirror_channel: string | null
  allow_proposals: boolean
}

export interface HandoffRepoSettings {
  enabled: boolean
  config: HandoffRepoConfig
}

export interface HandoffSetupCheck {
  name: string
  ok: boolean
  detail: string
}

export interface HandoffRequirements {
  enabled: boolean
  git: boolean
  claude_cli: boolean
  claude_cli_error: string | null
  claude_auth: boolean
  os_isolation: boolean
  github_app: { configured: boolean; slug?: string; install_url?: string | null }
  max_concurrent: number
}

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authorizedFetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok || body?.success === false) {
    throw new Error(extractErrorMessage(body) || `Request failed (${response.status})`)
  }
  return body.data as T
}

export const HandoffsApi = {
  list: (limit: number) => request<Handoff[]>(`/handoffs?limit=${limit}`),
  get: (id: string) => request<HandoffDetail>(`/handoffs/${id}`),
  start: (id: string) => request<HandoffDetail>(`/handoffs/${id}/start`, { method: 'POST' }),
  dismiss: (id: string) => request<HandoffDetail>(`/handoffs/${id}/dismiss`, { method: 'POST' }),
  cancel: (id: string) => request<HandoffDetail>(`/handoffs/${id}/cancel`, { method: 'POST' }),
  takeOver: (id: string) => request<HandoffDetail>(`/handoffs/${id}/take-over`, { method: 'POST' }),
  restart: (id: string) => request<HandoffDetail>(`/handoffs/${id}/restart`, { method: 'POST' }),
  sendReviewFeedback: (id: string) =>
    request<{ outcome: string }>(`/handoffs/${id}/review-feedback`, { method: 'POST' }),
  rename: (id: string, title: string) =>
    request<HandoffDetail>(`/handoffs/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  editBrief: (id: string, brief: HandoffBrief, title?: string) =>
    request<HandoffDetail>(`/handoffs/${id}/brief`, { method: 'PATCH', body: JSON.stringify({ brief, title }) }),
  message: (id: string, text: string) =>
    request<{ outcome: string }>(`/handoffs/${id}/messages`, { method: 'POST', body: JSON.stringify({ text }) }),
  requirements: () => request<HandoffRequirements>('/handoffs/requirements'),
  repoSettings: (repoId: string) => request<HandoffRepoSettings>(`/github/repos/${repoId}/handoff-settings`),
  saveRepoSettings: (repoId: string, settings: HandoffRepoSettings) =>
    request<HandoffRepoSettings>(`/github/repos/${repoId}/handoff-settings`, {
      method: 'PUT',
      body: JSON.stringify(settings),
    }),
  testSetup: (repoId: string) =>
    request<{ ok: boolean; checks: HandoffSetupCheck[] }>(`/github/repos/${repoId}/handoff-test`, { method: 'POST' }),
  githubAppManifest: (body: { name?: string; organization?: string }) =>
    request<{ post_url: string; manifest: Record<string, unknown> }>('/github/app/manifest', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteGithubApp: () => request<null>('/github/app', { method: 'DELETE' }),
  async download(path: string, filename: string) {
    const response = await authorizedFetch(`${BASE}${path}`)
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(extractErrorMessage(body) || `Download failed (${response.status})`)
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  },
}

export const isActiveHandoff = (status: HandoffStatus) => ACTIVE_HANDOFF_STATUSES.includes(status)

export const TERMINAL_HANDOFF_STATUSES: HandoffStatus[] = ['done', 'dismissed', 'cancelled', 'failed', 'ci_failed', 'taken_over']

const TAKE_OVER_STATUSES: HandoffStatus[] = ['preparing', 'running', 'waiting_on_human', 'fixing', 'ci_running']
const FINISHED_WITH_PR: HandoffStatus[] = ['done', 'ci_failed', 'failed', 'cancelled']

export const parseHandoffTime = (value: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`)

export const canTakeOver = (handoff: Handoff) => !!handoff.branch && TAKE_OVER_STATUSES.includes(handoff.status)

export const canStop = (handoff: Handoff) => isActiveHandoff(handoff.status) && handoff.status !== 'pushing'

export const canRestart = (handoff: HandoffDetail) =>
  handoff.status === 'failed' ||
  handoff.status === 'cancelled' ||
  ((handoff.status === 'done' || handoff.status === 'ci_failed') && !!handoff.pending_revision)

export const canMessage = (handoff: Handoff) =>
  isActiveHandoff(handoff.status) || (FINISHED_WITH_PR.includes(handoff.status) && !!handoff.pr_url)

const TONES: { match: (status: HandoffStatus) => boolean; chip: string; accent: string }[] = [
  { match: (s) => s === 'done', chip: 'bg-green-900/40 text-green-300', accent: 'border-l-green-500' },
  { match: (s) => s === 'failed' || s === 'ci_failed', chip: 'bg-red-900/40 text-red-300', accent: 'border-l-red-500' },
  {
    match: (s) => s === 'cancelled' || s === 'dismissed' || s === 'taken_over',
    chip: 'bg-gray-800 text-gray-300',
    accent: 'border-l-gray-600',
  },
  { match: (s) => s === 'proposed', chip: 'bg-orange-900/40 text-orange-300', accent: 'border-l-brand-orange' },
]

export function handoffTone(status: HandoffStatus, revised = false): { chip: string; accent: string } {
  const tone = TONES.find((t) => t.match(status)) ?? { chip: 'bg-blue-900/40 text-blue-300', accent: 'border-l-blue-500' }
  return revised ? { chip: tone.chip, accent: 'border-l-amber-500' } : tone
}

