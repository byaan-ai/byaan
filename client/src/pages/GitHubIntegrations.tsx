import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Input } from '../components/ui/input'
import {
  Github,
  Search,
  Loader2,
  Trash2,
  PlayCircle,
  RefreshCw,
  Lock,
  Globe,
  StopCircle,
  FolderOpen,
  Plus,
  AlertCircle,
  Users,
} from 'lucide-react'
import { GitHubService, type GitHubRepo, type ConnectedRepo } from '../services/github'
import { LocalRepoService } from '../services/localRepos'
import { isTauriApp } from '../lib/tauri-api'
import { useStore } from '../stores/useStore'
import { useScopes } from '@/hooks/useScopes'
import { Switch } from '@/components/ui/switch'
import { GitHubOAuthDialog } from '../components/github/GitHubOAuthDialog'
import { GitHubOAuthSettings } from '../components/github/GitHubOAuthSettings'
import { ModelSelector } from '../components/ModelSelector'
import { ApiService, type LLMConnection } from '../services/api'
import type { LLMProvider } from '../types/llm'

export default function GitHubIntegrations() {
  const {
    githubConnected,
    githubUsername,
    githubAuthMethod,
    setGitHubConnected,
    connectedRepos,
    setConnectedRepos,
    addConnectedRepo,
    updateRepoStatus,
    updateRepoScope,
    removeConnectedRepo,
    preferredProvider,
    preferredModel,
    fetchPreferredModel,
    setPreferredModel,
    clearPreferredModel,
    openSidebar,
    user,
  } = useStore()
  const { features } = useScopes()
  const teamSharingEnabled = features.team_sharing_enabled
  const currentUserId = user?.id
  const [togglingShareRepos, setTogglingShareRepos] = useState<Set<string>>(new Set())

  const [showOAuthDialog, setShowOAuthDialog] = useState(false)
  const [oauthAvailable, setOauthAvailable] = useState(true)
  const [canConfigureOAuth, setCanConfigureOAuth] = useState(false)
  const [repos, setRepos] = useState<GitHubRepo[]>([])
  const [repoSearch, setRepoSearch] = useState('')
  const [loadingRepos, setLoadingRepos] = useState(false)
  const [connectingRepo, setConnectingRepo] = useState<string | null>(null)
  const [analyzingRepos, setAnalyzingRepos] = useState<Set<string>>(new Set())
  const [availableConnections, setAvailableConnections] = useState<LLMConnection[]>([])
  const [availableModels, setAvailableModels] = useState<Record<string, string[]>>({})
  const [selectedProvider, setSelectedProvider] = useState<LLMProvider | undefined>()
  const [selectedModel, setSelectedModel] = useState<string | undefined>()
  const [selectedConnectionId, setSelectedConnectionId] = useState<string | undefined>()
  const [disconnecting, setDisconnecting] = useState(false)
  const [localRepos, setLocalRepos] = useState<ConnectedRepo[]>([])
  const [connectingLocal, setConnectingLocal] = useState(false)
  const [analyzingLocalRepos, setAnalyzingLocalRepos] = useState<Set<string>>(new Set())
  const [repoProgress, setRepoProgress] = useState<Record<string, string>>({})
  const pollingIntervals = useRef<Record<string, ReturnType<typeof setInterval>>>({})
  const isTauri = isTauriApp()

  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const loadLLMData = useCallback(async () => {
    try {
      const [connectionsResponse, modelsResponse] = await Promise.all([
        ApiService.listLLMConnections(),
        ApiService.getAvailableModels(),
      ])
      setAvailableConnections(connectionsResponse.items)
      const models = 'models_by_provider' in modelsResponse
        ? modelsResponse.models_by_provider
        : { [modelsResponse.provider]: modelsResponse.models }
      setAvailableModels(models)
    } catch {
      console.error('Failed to load LLM data')
    }
  }, [])

  const refreshAuthConfig = useCallback(() => {
    GitHubService.getAuthConfig()
      .then(config => {
        setOauthAvailable(config.oauth_available)
        setCanConfigureOAuth(config.can_configure_oauth)
      })
      .catch(() => {
        setOauthAvailable(false)
        setCanConfigureOAuth(false)
      })
  }, [])

  useEffect(() => {
    loadLLMData()
    fetchPreferredModel()
    refreshAuthConfig()
  }, [loadLLMData, fetchPreferredModel, refreshAuthConfig])

  useEffect(() => {
    if (availableConnections.length === 0 || selectedProvider) return

    if (preferredProvider && preferredModel) {
      const connection = availableConnections.find(c => c.type === preferredProvider)
      const models = availableModels[preferredProvider] || []
      if (connection && models.includes(preferredModel)) {
        setSelectedProvider(preferredProvider as LLMProvider)
        setSelectedModel(preferredModel)
        setSelectedConnectionId(connection.id)
        return
      }
    }

    const firstConnection = availableConnections[0]
    if (firstConnection) {
      const models = availableModels[firstConnection.type] || []
      if (models.length > 0) {
        setSelectedProvider(firstConnection.type as LLMProvider)
        setSelectedModel(models[0])
        setSelectedConnectionId(firstConnection.id)
      }
    }
  }, [availableConnections, availableModels, preferredProvider, preferredModel])

  const loadStatus = useCallback(async () => {
    try {
      const status = await GitHubService.getStatus()
      setGitHubConnected(status.connected, status.username, status.auth_method)
    } catch {
      setGitHubConnected(false)
    }
  }, [setGitHubConnected])

  const loadConnectedRepos = useCallback(async () => {
    try {
      const data = await GitHubService.getConnectedRepos()
      setConnectedRepos(data)
    } catch {
      console.error('Failed to load connected repos')
    }
  }, [setConnectedRepos])

  const loadLocalRepos = useCallback(async () => {
    if (!isTauri) return
    try {
      const data = await LocalRepoService.getConnectedRepos()
      setLocalRepos(data)
    } catch {
      console.error('Failed to load local repos')
    }
  }, [isTauri])

  useEffect(() => {
    loadStatus()
    loadConnectedRepos()
    loadLocalRepos()
  }, [loadStatus, loadConnectedRepos, loadLocalRepos])

  useEffect(() => {
    if (searchParams.get('github_connected') === 'true') {
      loadStatus()
      loadConnectedRepos()
      setSearchParams({}, { replace: true })
    }
    if (searchParams.get('github_error')) {
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams, loadStatus, loadConnectedRepos])

  useEffect(() => {
    return () => {
      Object.values(pollingIntervals.current).forEach(clearInterval)
    }
  }, [])

  const searchRepos = useCallback(async () => {
    if (!githubConnected) return
    setLoadingRepos(true)
    try {
      const data = await GitHubService.listRepos(1, repoSearch || undefined)
      setRepos(data)
    } catch {
      console.error('Failed to load repos')
    } finally {
      setLoadingRepos(false)
    }
  }, [githubConnected, repoSearch])

  useEffect(() => {
    if (githubConnected) {
      const timer = setTimeout(searchRepos, 300)
      return () => clearTimeout(timer)
    }
  }, [githubConnected, repoSearch, searchRepos])

  const handleConnect = async (repo: GitHubRepo) => {
    setConnectingRepo(repo.full_name)
    try {
      const connected = await GitHubService.connectRepo(repo.full_name, repo.default_branch)
      addConnectedRepo(connected)
      if (selectedConnectionId) {
        handleAnalyze(connected.id)
      }
    } catch (err) {
      console.error('Failed to connect repo:', err)
    } finally {
      setConnectingRepo(null)
    }
  }

  const handleDisconnect = async (repoId: string) => {
    try {
      await GitHubService.deleteRepo(repoId)
      removeConnectedRepo(repoId)
    } catch {
      console.error('Failed to disconnect repo')
    }
  }

  const handleToggleShare = async (repo: ConnectedRepo, share: boolean) => {
    if (togglingShareRepos.has(repo.id)) return
    setTogglingShareRepos((prev) => new Set(prev).add(repo.id))
    try {
      const updated = share
        ? await GitHubService.shareRepoWithTeam(repo.id)
        : await GitHubService.unshareRepoFromTeam(repo.id)
      updateRepoScope(repo.id, updated.scope)
    } catch (err) {
      console.error('Failed to toggle repo sharing:', err)
    } finally {
      setTogglingShareRepos((prev) => {
        const next = new Set(prev)
        next.delete(repo.id)
        return next
      })
    }
  }

  const startPolling = (repoId: string) => {
    if (pollingIntervals.current[repoId]) return
    pollingIntervals.current[repoId] = setInterval(async () => {
      try {
        const status = await GitHubService.getRepoStatus(repoId)
        updateRepoStatus(repoId, status.status, status.error)
        if (status.progress_message) {
          setRepoProgress((prev) => ({ ...prev, [repoId]: status.progress_message! }))
        }
        if (status.status !== 'analyzing') {
          clearInterval(pollingIntervals.current[repoId])
          delete pollingIntervals.current[repoId]
          setAnalyzingRepos((prev) => {
            const next = new Set(prev)
            next.delete(repoId)
            return next
          })
          setRepoProgress((prev) => {
            const next = { ...prev }
            delete next[repoId]
            return next
          })
          loadConnectedRepos()
        }
      } catch {
        clearInterval(pollingIntervals.current[repoId])
        delete pollingIntervals.current[repoId]
      }
    }, 3000)
  }

  const handleModelSelectionChange = (data: { provider: LLMProvider; model: string; connectionId: string } | undefined) => {
    if (data) {
      setSelectedProvider(data.provider)
      setSelectedModel(data.model)
      setSelectedConnectionId(data.connectionId)
      setPreferredModel(data.provider, data.model)
    } else {
      setSelectedProvider(undefined)
      setSelectedModel(undefined)
      setSelectedConnectionId(undefined)
    }
  }

  const handleAnalyze = async (repoId: string) => {
    if (!selectedConnectionId) return
    try {
      await GitHubService.analyzeRepo(repoId, selectedConnectionId)
      updateRepoStatus(repoId, 'analyzing')
      setAnalyzingRepos((prev) => new Set(prev).add(repoId))
      startPolling(repoId)
    } catch {
      console.error('Failed to start analysis')
    }
  }

  const handleCancelAnalysis = async (repoId: string) => {
    try {
      await GitHubService.cancelAnalysis(repoId)
      updateRepoStatus(repoId, 'cancelled')
      setAnalyzingRepos((prev) => {
        const next = new Set(prev)
        next.delete(repoId)
        return next
      })
      if (pollingIntervals.current[repoId]) {
        clearInterval(pollingIntervals.current[repoId])
        delete pollingIntervals.current[repoId]
      }
    } catch {
      console.error('Failed to cancel analysis')
    }
  }

  const handleAddLocalRepo = async () => {
    try {
      const { open } = await import('@tauri-apps/plugin-dialog')
      const selected = await open({ directory: true, multiple: false, title: 'Select Repository Folder' })
      if (!selected) return

      setConnectingLocal(true)
      const repo = await LocalRepoService.connectRepo(selected as string)
      setLocalRepos((prev) => [...prev, repo])
      if (selectedConnectionId) {
        handleAnalyzeLocal(repo.id)
      }
    } catch (err) {
      console.error('Failed to connect local repo:', err)
    } finally {
      setConnectingLocal(false)
    }
  }

  const handleDeleteLocalRepo = async (repoId: string) => {
    try {
      await LocalRepoService.deleteRepo(repoId)
      setLocalRepos((prev) => prev.filter((r) => r.id !== repoId))
    } catch {
      console.error('Failed to delete local repo')
    }
  }

  const startLocalPolling = (repoId: string) => {
    if (pollingIntervals.current[repoId]) return
    pollingIntervals.current[repoId] = setInterval(async () => {
      try {
        const status = await LocalRepoService.getRepoStatus(repoId)
        setLocalRepos((prev) =>
          prev.map((r) => (r.id === repoId ? { ...r, analysis_status: status.status, analysis_error: status.error } : r))
        )
        if (status.progress_message) {
          setRepoProgress((prev) => ({ ...prev, [repoId]: status.progress_message! }))
        }
        if (status.status !== 'analyzing') {
          clearInterval(pollingIntervals.current[repoId])
          delete pollingIntervals.current[repoId]
          setAnalyzingLocalRepos((prev) => {
            const next = new Set(prev)
            next.delete(repoId)
            return next
          })
          setRepoProgress((prev) => {
            const next = { ...prev }
            delete next[repoId]
            return next
          })
          loadLocalRepos()
        }
      } catch {
        clearInterval(pollingIntervals.current[repoId])
        delete pollingIntervals.current[repoId]
      }
    }, 3000)
  }

  const handleAnalyzeLocal = async (repoId: string) => {
    if (!selectedConnectionId) return
    try {
      await LocalRepoService.analyzeRepo(repoId, selectedConnectionId)
      setLocalRepos((prev) => prev.map((r) => (r.id === repoId ? { ...r, analysis_status: 'analyzing' } : r)))
      setAnalyzingLocalRepos((prev) => new Set(prev).add(repoId))
      startLocalPolling(repoId)
    } catch {
      console.error('Failed to start local analysis')
    }
  }

  const handleCancelLocalAnalysis = async (repoId: string) => {
    try {
      await LocalRepoService.cancelAnalysis(repoId)
      setLocalRepos((prev) => prev.map((r) => (r.id === repoId ? { ...r, analysis_status: 'cancelled' } : r)))
      setAnalyzingLocalRepos((prev) => {
        const next = new Set(prev)
        next.delete(repoId)
        return next
      })
      if (pollingIntervals.current[repoId]) {
        clearInterval(pollingIntervals.current[repoId])
        delete pollingIntervals.current[repoId]
      }
    } catch {
      console.error('Failed to cancel local analysis')
    }
  }

  const handleDisconnectGitHub = async () => {
    setDisconnecting(true)
    try {
      await GitHubService.disconnect()
      setGitHubConnected(false)
      setRepos([])
    } catch {
      console.error('Failed to disconnect GitHub')
    } finally {
      setDisconnecting(false)
    }
  }

  const connectedNames = new Set(connectedRepos.map((r) => r.repo_full_name))

  const statusBadge = (status: string, repoId?: string) => {
    const badge = (() => {
      switch (status) {
        case 'completed':
          return <Badge className="bg-green-900/30 text-green-400 border-green-800">Analyzed</Badge>
        case 'analyzing':
          return (
            <Badge className="bg-blue-900/30 text-blue-400 border-blue-800">
              <Loader2 className="w-3 h-3 animate-spin mr-1" /> Analyzing
            </Badge>
          )
        case 'cancelled':
          return <Badge className="bg-yellow-900/30 text-yellow-400 border-yellow-800">Cancelled</Badge>
        case 'failed':
          return <Badge className="bg-red-900/30 text-red-400 border-red-800">Failed</Badge>
        default:
          return <Badge className="bg-gray-900/30 text-gray-400 border-gray-800">Pending</Badge>
      }
    })()

    const progressMsg = repoId ? repoProgress[repoId] : undefined
    if (status === 'analyzing' && progressMsg) {
      return (
        <div className="flex flex-col items-end gap-1">
          {badge}
          <span className="text-xs text-blue-400/70">{progressMsg}</span>
        </div>
      )
    }
    return badge
  }

  return (
    <div className="bg-[#0d0d0d] w-full min-h-screen flex flex-col">
      <div className="w-full px-8 pt-[50px] pb-8">
        <div className="max-w-[850px] mx-auto">
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-3">
              <Github className="w-7 h-7 text-white" />
              <h1 className="text-2xl font-bold text-white tracking-tight">GitHub</h1>
            </div>
          </div>

          {canConfigureOAuth && (
            <GitHubOAuthSettings onConfigChanged={refreshAuthConfig} />
          )}

          {/* Connection Card */}
          <Card className="bg-[#1a1a1a] border-gray-800 p-6 mb-8">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className={`w-3 h-3 rounded-full ${githubConnected ? 'bg-green-400' : 'bg-gray-600'}`} />
                <div>
                  <h3 className="text-white font-medium flex items-center gap-2">
                    {githubConnected ? `Connected as ${githubUsername}` : 'Not connected'}
                    {githubConnected && githubAuthMethod === 'pat_fine_grained' && (
                      <span className="text-[10px] uppercase tracking-wide px-2 py-0.5 rounded bg-brand-orange/15 text-brand-orange border border-brand-orange/30">
                        Fine-grained PAT
                      </span>
                    )}
                    {githubConnected && githubAuthMethod === 'pat_classic' && (
                      <span className="text-[10px] uppercase tracking-wide px-2 py-0.5 rounded bg-gray-500/15 text-gray-300 border border-gray-500/30">
                        Classic PAT
                      </span>
                    )}
                  </h3>
                  <p className="text-sm text-gray-500">
                    {githubConnected
                      ? 'Your GitHub account is linked. You can connect and analyze public and private repositories.'
                      : 'Connect your GitHub account to get started. Both public and private repositories are supported.'}
                  </p>
                </div>
              </div>
              {githubConnected ? (
                <Button
                  variant="outline"
                  onClick={handleDisconnectGitHub}
                  disabled={disconnecting}
                  className="border-red-800 text-red-400 hover:bg-red-900/20"
                >
                  {disconnecting && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
                  Disconnect
                </Button>
              ) : (
                <Button onClick={() => setShowOAuthDialog(true)} className="bg-brand-orange hover:bg-brand-orange/90">
                  Connect GitHub
                </Button>
              )}
            </div>
          </Card>

          {/* LLM Selector */}
          {githubConnected && (
            <div className="mb-6 flex items-center gap-3">
              <label className="text-sm text-gray-400">LLM for analysis:</label>
              <ModelSelector
                selectedProvider={selectedProvider}
                selectedModel={selectedModel}
                selectedConnectionId={selectedConnectionId}
                connections={availableConnections}
                availableModels={availableModels}
                onSelectionChange={handleModelSelectionChange}
                onConnectionCreated={loadLLMData}
                preferredProvider={preferredProvider}
                preferredModel={preferredModel}
                onSetPreferred={(provider, model) => setPreferredModel(provider, model)}
                onClearPreferred={clearPreferredModel}
                compact
                className="w-52 h-8 px-2 py-1 text-xs"
              />
            </div>
          )}

          {availableConnections.length === 0 && (
            <div className="mb-4 flex items-center gap-2 text-sm text-yellow-400/80 bg-yellow-900/10 border border-yellow-900/30 rounded-lg px-3 py-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>
                Add an LLM connection in{' '}
                <button onClick={() => navigate('/connections')} className="underline hover:text-yellow-300">
                  LLM Connections
                </button>{' '}
                to analyze repositories.
              </span>
            </div>
          )}

          {/* Repository Picker */}
          {githubConnected && (
            <div className="mb-8">
              <h2 className="text-lg font-semibold text-white mb-4">Add Repository</h2>
              <div className="relative mb-4">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <Input
                  value={repoSearch}
                  onChange={(e) => setRepoSearch(e.target.value)}
                  placeholder="Search your repositories..."
                  className="pl-10 bg-[#1a1a1a] border-gray-700 text-white"
                />
              </div>

              {loadingRepos ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                </div>
              ) : (
                <div className="space-y-2 max-h-[300px] overflow-y-auto">
                  {repos.map((repo) => (
                    <div
                      key={repo.full_name}
                      className="flex items-center justify-between px-4 py-3 bg-[#1a1a1a] rounded-lg border border-gray-800 hover:border-gray-700 transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        {repo.private ? <Lock className="w-4 h-4 text-gray-500 flex-shrink-0" /> : <Globe className="w-4 h-4 text-gray-500 flex-shrink-0" />}
                        <div className="min-w-0">
                          <p className="text-sm text-white truncate">{repo.full_name}</p>
                          {repo.description && <p className="text-xs text-gray-500 truncate">{repo.description}</p>}
                        </div>
                        {repo.language && (
                          <Badge variant="outline" className="text-xs border-gray-700 text-gray-400 flex-shrink-0">
                            {repo.language}
                          </Badge>
                        )}
                      </div>
                      {connectedNames.has(repo.full_name) ? (
                        <Badge className="bg-green-900/30 text-green-400 border-green-800 ml-3">Connected</Badge>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleConnect(repo)}
                          disabled={connectingRepo === repo.full_name || !selectedConnectionId}
                          title={!selectedConnectionId ? 'Select an LLM connection to analyze repositories' : undefined}
                          className="border-gray-700 text-white hover:bg-[#333333] ml-3"
                        >
                          {connectingRepo === repo.full_name ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            'Connect'
                          )}
                        </Button>
                      )}
                    </div>
                  ))}
                  {repos.length === 0 && !loadingRepos && (
                    <p className="text-center text-sm text-gray-500 py-4">No repositories found</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Local Repositories (Tauri only) */}
          {isTauri && (
            <div className="mb-8">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <FolderOpen className="w-5 h-5 text-gray-400" />
                  <h2 className="text-lg font-semibold text-white">Local Repositories</h2>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleAddLocalRepo}
                  disabled={connectingLocal || !selectedConnectionId}
                  title={!selectedConnectionId ? 'Select an LLM connection to analyze repositories' : undefined}
                  className="border-gray-700 text-white hover:bg-[#333333]"
                >
                  {connectingLocal ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Plus className="w-4 h-4 mr-2" />}
                  Add Local Repository
                </Button>
              </div>

              {/* LLM Selector for local repos (shown if no GitHub connection) */}
              {!githubConnected && localRepos.length > 0 && (
                <div className="mb-4 flex items-center gap-3">
                  <label className="text-sm text-gray-400">LLM for analysis:</label>
                  <ModelSelector
                    selectedProvider={selectedProvider}
                    selectedModel={selectedModel}
                    selectedConnectionId={selectedConnectionId}
                    connections={availableConnections}
                    availableModels={availableModels}
                    onSelectionChange={handleModelSelectionChange}
                    onConnectionCreated={loadLLMData}
                    preferredProvider={preferredProvider}
                    preferredModel={preferredModel}
                    onSetPreferred={(provider, model) => setPreferredModel(provider, model)}
                    onClearPreferred={clearPreferredModel}
                    compact
                    className="w-52 h-8 px-2 py-1 text-xs"
                  />
                </div>
              )}

              {localRepos.length > 0 ? (
                <div className="space-y-3">
                  {localRepos.map((repo) => (
                    <Card key={repo.id} className="bg-[#1a1a1a] border-gray-800 overflow-hidden">
                      <div className="px-5 py-4">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <FolderOpen className="w-5 h-5 text-gray-400" />
                            <div>
                              <p className="text-white font-medium">{repo.repo_full_name.replace('local/', '')}</p>
                              <p className="text-xs text-gray-500 truncate max-w-[400px]">{repo.local_path}</p>
                            </div>
                          </div>

                          <div className="flex items-center gap-3">
                            {statusBadge(repo.analysis_status, repo.id)}

                            {repo.analysis_status === 'analyzing' ? (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handleCancelLocalAnalysis(repo.id)}
                                className="border-yellow-800 text-yellow-400 hover:bg-yellow-900/20"
                                title="Cancel analysis"
                              >
                                <StopCircle className="w-4 h-4" />
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handleAnalyzeLocal(repo.id)}
                                disabled={!selectedConnectionId || analyzingLocalRepos.has(repo.id)}
                                className="border-gray-700 text-white hover:bg-[#333333]"
                                title={!selectedConnectionId ? 'Select an LLM connection to analyze repositories' : repo.analysis_status === 'completed' ? 'Re-analyze' : 'Analyze'}
                              >
                                {repo.analysis_status === 'completed' ? (
                                  <RefreshCw className="w-4 h-4" />
                                ) : (
                                  <PlayCircle className="w-4 h-4" />
                                )}
                              </Button>
                            )}

                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleDeleteLocalRepo(repo.id)}
                              className="border-red-800 text-red-400 hover:bg-red-900/20"
                            >
                              <Trash2 className="w-4 h-4" />
                            </Button>
                          </div>
                        </div>

                        {repo.analysis_status === 'failed' && repo.analysis_error && (
                          <p className="mt-2 text-xs text-red-400">{repo.analysis_error}</p>
                        )}

                        {repo.analysis_status === 'completed' && repo.skills?.length > 0 && (
                          <div className="mt-2 flex items-center gap-2 flex-wrap">
                            {repo.skills.map((skill) => (
                              <Badge
                                key={skill.id}
                                className="bg-purple-900/30 text-purple-400 border-purple-800 text-xs cursor-pointer hover:bg-purple-900/50"
                                onClick={() => openSidebar('skills', skill.id)}
                              >
                                {skill.name}
                              </Badge>
                            ))}
                          </div>
                        )}

                        {repo.language_breakdown && (
                          <div className="mt-3 flex gap-2 flex-wrap">
                            {Object.entries(JSON.parse(repo.language_breakdown) as Record<string, number>)
                              .sort(([, a], [, b]) => b - a)
                              .slice(0, 5)
                              .map(([lang]) => (
                                <Badge key={lang} variant="outline" className="text-xs border-gray-700 text-gray-400">
                                  {lang}
                                </Badge>
                              ))}
                          </div>
                        )}
                      </div>
                    </Card>
                  ))}
                </div>
              ) : (
                <Card className="bg-[#1a1a1a] border-gray-800 p-6">
                  <p className="text-sm text-gray-500 text-center">
                    No local repositories connected. Click &quot;Add Local Repository&quot; to select a folder.
                  </p>
                </Card>
              )}
            </div>
          )}

          {/* Connected Repos */}
          {connectedRepos.filter(r => r.source !== 'local').length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-4">Connected Repositories</h2>
              {!githubConnected && (
                <p className="text-sm text-yellow-400/70 mb-3">
                  Reconnect GitHub to re-analyze or use these repositories in chat.
                </p>
              )}
              <div className="space-y-3">
                {connectedRepos.filter(r => r.source !== 'local').map((repo) => (
                  <Card key={repo.id} className={`bg-[#1a1a1a] border-gray-800 overflow-hidden ${!githubConnected ? 'opacity-60' : ''}`}>
                    <div className="px-5 py-4">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <Github className="w-5 h-5 text-gray-400" />
                          <div>
                            <p className="text-white font-medium">{repo.repo_full_name}</p>
                            <p className="text-xs text-gray-500">
                              {repo.default_branch}
                              {repo.last_analyzed_sha && (
                                <span className="ml-2 font-mono">{repo.last_analyzed_sha.slice(0, 7)}</span>
                              )}
                            </p>
                          </div>
                        </div>

                        <div className={`flex items-center gap-3 ${!githubConnected ? 'opacity-50 pointer-events-none' : ''}`}>
                          {statusBadge(repo.analysis_status, repo.id)}

                          {repo.analysis_status === 'analyzing' ? (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleCancelAnalysis(repo.id)}
                              className="border-yellow-800 text-yellow-400 hover:bg-yellow-900/20"
                              title="Cancel analysis"
                            >
                              <StopCircle className="w-4 h-4" />
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleAnalyze(repo.id)}
                              disabled={!selectedConnectionId || analyzingRepos.has(repo.id) || !githubConnected}
                              className="border-gray-700 text-white hover:bg-[#333333]"
                              title={!selectedConnectionId ? 'Select an LLM connection to analyze repositories' : repo.analysis_status === 'completed' ? 'Re-analyze' : 'Analyze'}
                            >
                              {repo.analysis_status === 'completed' ? (
                                <RefreshCw className="w-4 h-4" />
                              ) : (
                                <PlayCircle className="w-4 h-4" />
                              )}
                            </Button>
                          )}

                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleDisconnect(repo.id)}
                            className="border-red-800 text-red-400 hover:bg-red-900/20"
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </div>

                      {repo.analysis_status === 'failed' && repo.analysis_error && (
                        <p className="mt-2 text-xs text-red-400">{repo.analysis_error}</p>
                      )}

                      {(repo.analysis_status === 'completed' && repo.skills?.length > 0) ||
                      (teamSharingEnabled && (repo.user_id === currentUserId || repo.scope === 'org')) ? (
                        <div className="mt-2 flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2 flex-wrap">
                            {repo.analysis_status === 'completed' &&
                              repo.skills?.map((skill) => (
                                <Badge
                                  key={skill.id}
                                  className="bg-purple-900/30 text-purple-400 border-purple-800 text-xs cursor-pointer hover:bg-purple-900/50"
                                  onClick={() => openSidebar('skills', skill.id)}
                                >
                                  {skill.name}
                                </Badge>
                              ))}
                          </div>
                          {teamSharingEnabled && repo.user_id === currentUserId && (
                            <div
                              className="flex items-center gap-2 shrink-0"
                              title="Share with team lets Slack and other workspace users access this repo"
                            >
                              <Users
                                className={`w-4 h-4 ${repo.scope === 'org' ? 'text-green-400' : 'text-gray-500'}`}
                              />
                              <span className="text-sm text-gray-300">Share with team</span>
                              <Switch
                                checked={repo.scope === 'org'}
                                onCheckedChange={(checked) => handleToggleShare(repo, checked)}
                                disabled={togglingShareRepos.has(repo.id)}
                              />
                              {togglingShareRepos.has(repo.id) && (
                                <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                              )}
                            </div>
                          )}
                          {teamSharingEnabled && repo.scope === 'org' && repo.user_id !== currentUserId && (
                            <Badge className="bg-green-900/30 text-green-400 border-green-800 text-xs shrink-0">
                              <Users className="w-3 h-3 mr-1 inline" />
                              Shared by team
                            </Badge>
                          )}
                        </div>
                      ) : null}

                      {repo.language_breakdown && (
                        <div className="mt-3 flex gap-2 flex-wrap">
                          {Object.entries(JSON.parse(repo.language_breakdown) as Record<string, number>)
                            .sort(([, a], [, b]) => b - a)
                            .slice(0, 5)
                            .map(([lang]) => (
                              <Badge key={lang} variant="outline" className="text-xs border-gray-700 text-gray-400">
                                {lang}
                              </Badge>
                            ))}
                        </div>
                      )}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <GitHubOAuthDialog
        open={showOAuthDialog}
        onOpenChange={setShowOAuthDialog}
        onSuccess={() => {
          loadStatus()
          loadConnectedRepos()
        }}
        oauthAvailable={oauthAvailable}
      />
    </div>
  )
}
