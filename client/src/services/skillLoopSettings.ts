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

export interface SkillLoopSettings {
  enabled: boolean
  digest_enabled: boolean
  digest_hour: number
  slack_reviewers_channel_id: string | null
  slack_workspace_connected: boolean
  loop_globally_enabled: boolean
}

export interface SkillLoopSettingsUpdate {
  enabled?: boolean
  digest_enabled?: boolean
  digest_hour?: number
  slack_reviewers_channel_id?: string | null
}

export interface SkillLoopSlackChannels {
  connected: boolean
  channels: Array<{ id: string; name: string }>
}

export interface SkillLoopRunNowResult {
  queued?: number
  note?: string
  message?: string
}

export const SkillLoopSettingsService = {
  async get(): Promise<SkillLoopSettings> {
    const res = await apiFetch('/skill-loop/settings')
    return res.data as SkillLoopSettings
  },

  async getSlackChannels(): Promise<SkillLoopSlackChannels> {
    const res = await apiFetch('/skill-loop/slack-channels')
    return res.data as SkillLoopSlackChannels
  },

  async runNow(): Promise<SkillLoopRunNowResult> {
    const res = await apiFetch('/skill-loop/run-now', { method: 'POST' })
    return { ...(res.data as SkillLoopRunNowResult), message: res.message }
  },

  async update(update: SkillLoopSettingsUpdate): Promise<SkillLoopSettings> {
    const res = await apiFetch('/skill-loop/settings', {
      method: 'PUT',
      body: JSON.stringify(update),
    })
    return res.data as SkillLoopSettings
  },
}
