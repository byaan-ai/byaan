import { getBackendUrl } from '../lib/tauri-api'
import { getAccessToken } from './tokenStore'
import { isHostedMode as getRuntimeIsHosted, getApiBaseUrl as getRuntimeApiBaseUrl } from '../lib/runtime-config'

async function getApiUrl(): Promise<string> {
  const runtimeBase = getRuntimeApiBaseUrl()
  if (runtimeBase && runtimeBase !== '/api') {
    return `${runtimeBase}/github`
  }
  if (getRuntimeIsHosted()) {
    return `/api/github`
  }
  const { isTauriApp } = await import('../lib/tauri-api')
  if (isTauriApp()) {
    const base = await getBackendUrl()
    return `${base}/api/github`
  }
  return `/api/github`
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
  const base = await getApiUrl()
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

export interface GitHubRepo {
  full_name: string
  private: boolean
  language: string | null
  description: string | null
  default_branch: string
}

export interface RepoSkill {
  id: string
  name: string
  github_analysis_type: string | null
}

export interface ConnectedRepo {
  id: string
  tenant_id: string
  user_id: string
  source: string
  repo_full_name: string
  default_branch: string
  local_path: string | null
  last_analyzed_sha: string | null
  analysis_status: string
  analysis_error: string | null
  language_breakdown: string | null
  is_active: boolean
  scope: 'user' | 'org'
  created_at: string
  updated_at: string
  skills: RepoSkill[]
}

export const GitHubService = {
  async getAuthConfig() {
    const res = await apiFetch('/auth/config')
    return res.data as { oauth_available: boolean; can_configure_oauth: boolean }
  },

  async connectWithPAT(token: string) {
    const res = await apiFetch('/auth/pat', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
    return res.data as { connected: boolean; username: string }
  },

  async startOAuth() {
    const res = await apiFetch('/oauth/start', { method: 'POST' })
    return res.data as { auth_url: string; state: string }
  },

  async callbackOAuth(code: string, state: string) {
    const res = await apiFetch('/oauth/callback', {
      method: 'POST',
      body: JSON.stringify({ code, state }),
    })
    return res.data as { connected: boolean; username: string }
  },

  async startDeviceFlow() {
    const res = await apiFetch('/oauth/device/start', { method: 'POST' })
    return res.data as { device_code: string; user_code: string; verification_uri: string; expires_in: number; interval: number }
  },

  async pollDeviceToken(deviceCode: string) {
    const res = await apiFetch('/oauth/device/poll', {
      method: 'POST',
      body: JSON.stringify({ device_code: deviceCode }),
    })
    return res.data as { status: string; connected: boolean; username: string | null }
  },

  async getStatus() {
    const res = await apiFetch('/oauth/status')
    return res.data as {
      connected: boolean
      username: string | null
      scopes: string[] | null
      auth_method: 'oauth' | 'pat_classic' | 'pat_fine_grained' | null
    }
  },

  async disconnect() {
    return apiFetch('/oauth/disconnect', { method: 'POST' })
  },

  async listRepos(page = 1, search?: string) {
    const params = new URLSearchParams({ page: String(page) })
    if (search) params.set('search', search)
    const res = await apiFetch(`/repos?${params}`)
    return res.data as GitHubRepo[]
  },

  async connectRepo(repoFullName: string, defaultBranch: string) {
    const res = await apiFetch('/repos/connect', {
      method: 'POST',
      body: JSON.stringify({ repo_full_name: repoFullName, default_branch: defaultBranch }),
    })
    return res.data as ConnectedRepo
  },

  async getConnectedRepos() {
    const res = await apiFetch('/repos/connected')
    return res.data as ConnectedRepo[]
  },

  async getRepo(id: string) {
    const res = await apiFetch(`/repos/${id}`)
    return res.data as ConnectedRepo
  },

  async deleteRepo(id: string) {
    return apiFetch(`/repos/${id}`, { method: 'DELETE' })
  },

  async shareRepoWithTeam(id: string) {
    const res = await apiFetch(`/repos/${id}/share`, { method: 'POST' })
    return res.data as ConnectedRepo
  },

  async unshareRepoFromTeam(id: string) {
    const res = await apiFetch(`/repos/${id}/unshare`, { method: 'POST' })
    return res.data as ConnectedRepo
  },

  async analyzeRepo(id: string, llmConnectionId: string) {
    const res = await apiFetch(`/repos/${id}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ llm_connection_id: llmConnectionId }),
    })
    return res.data as { status: string }
  },

  async cancelAnalysis(id: string) {
    return apiFetch(`/repos/${id}/analyze/cancel`, { method: 'POST' })
  },

  async getRepoStatus(id: string) {
    const res = await apiFetch(`/repos/${id}/status`)
    return res.data as { status: string; progress_message: string | null; files_analyzed: number | null; total_files: number | null; error: string | null }
  },

  async getOAuthSettings() {
    const res = await apiFetch('/admin/oauth-config')
    return res.data as { client_id: string; client_secret_configured: boolean }
  },

  async saveOAuthSettings(clientId: string, clientSecret: string) {
    return apiFetch('/admin/oauth-config', {
      method: 'PUT',
      body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
    })
  },

  async deleteOAuthSettings() {
    return apiFetch('/admin/oauth-config', { method: 'DELETE' })
  },
}
