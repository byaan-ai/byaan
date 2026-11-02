import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Check, Copy, ExternalLink, KeyRound, Loader2, Pencil, Plus, RotateCw, Trash2, X, Zap } from 'lucide-react'

import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Switch } from './ui/switch'
import { useAppConfig } from '../hooks/useAppConfig'
import {
  useCreateExternalMCPServer,
  useDeleteExternalMCPServer,
  useExternalMCPServers,
  useTestExternalMCPServer,
  useUpdateExternalMCPServer,
} from '../hooks/useExternalMCPServers'
import { ApiService } from '../services/api'
import type { ExternalMCPServer, ExternalMCPServerPayload, ExternalMCPTransport } from '../services/api'
import { showToast } from '../utils/toast'

type AuthType = 'static_bearer' | 'oauth'

type FormState = {
  id: string | null
  name: string
  transport: ExternalMCPTransport
  authType: AuthType
  url: string
  headers: { key: string; value: string }[]
  command: string
  args: string
  env: { key: string; value: string }[]
  enabled: boolean
  headersTouched: boolean
  envTouched: boolean
}

const emptyForm: FormState = {
  id: null,
  name: '',
  transport: 'streamable_http',
  authType: 'oauth',
  url: '',
  headers: [],
  command: '',
  args: '',
  env: [],
  enabled: true,
  headersTouched: false,
  envTouched: false,
}

const MASK = '***'

const toPayload = (f: FormState, _existing: ExternalMCPServer | null): Partial<ExternalMCPServerPayload> => {
  const isEditing = f.id !== null

  if (f.transport === 'stdio') {
    const base: Partial<ExternalMCPServerPayload> = { enabled: f.enabled }
    if (!isEditing) {
      base.name = f.name
      base.transport = 'stdio'
      base.config = {
        command: f.command,
        args: f.args.split('\n').map(s => s.trim()).filter(Boolean),
        env: Object.fromEntries(
          f.env.filter(e => e.key && e.value !== MASK).map(e => [e.key, e.value]),
        ),
      }
    } else if (f.envTouched) {
      base.config = {
        command: f.command,
        args: f.args.split('\n').map(s => s.trim()).filter(Boolean),
        env: Object.fromEntries(
          f.env.filter(e => e.key && e.value !== MASK).map(e => [e.key, e.value]),
        ),
      }
    }
    return base
  }

  const base: Partial<ExternalMCPServerPayload> = { enabled: f.enabled }
  if (!isEditing) {
    base.name = f.name
    base.transport = f.transport
    base.config = {
      url: f.url,
      headers: Object.fromEntries(
        f.headers.filter(h => h.key && h.value !== MASK).map(h => [h.key, h.value]),
      ),
    }
  } else if (f.headersTouched) {
    base.config = {
      url: f.url,
      headers: Object.fromEntries(
        f.headers.filter(h => h.key && h.value !== MASK).map(h => [h.key, h.value]),
      ),
    }
  }
  return base
}

const fromServer = (s: ExternalMCPServer): FormState => {
  const cfg = s.config as Record<string, unknown>
  const authType: AuthType = s.auth_type === 'oauth' ? 'oauth' : 'static_bearer'
  if (s.transport === 'stdio') {
    return {
      id: s.id,
      name: s.name,
      transport: 'stdio',
      authType: 'static_bearer',
      enabled: s.enabled,
      url: '',
      headers: [],
      command: String(cfg.command ?? ''),
      args: Array.isArray(cfg.args) ? (cfg.args as string[]).join('\n') : '',
      env: Object.entries((cfg.env as Record<string, string>) ?? {}).map(([key, value]) => ({ key, value })),
      headersTouched: false,
      envTouched: false,
    }
  }
  return {
    id: s.id,
    name: s.name,
    transport: s.transport,
    authType,
    enabled: s.enabled,
    url: String(cfg.url ?? ''),
    headers: Object.entries((cfg.headers as Record<string, string>) ?? {}).map(([key, value]) => ({ key, value })),
    command: '',
    args: '',
    env: [],
    headersTouched: false,
    envTouched: false,
  }
}

export function ExternalMCPsTab() {
  const { isSelfHosted } = useAppConfig()
  const queryClient = useQueryClient()
  const { data: servers = [], isLoading } = useExternalMCPServers()
  const createMutation = useCreateExternalMCPServer()
  const updateMutation = useUpdateExternalMCPServer()
  const deleteMutation = useDeleteExternalMCPServer()
  const testMutation = useTestExternalMCPServer()

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [existingServer, setExistingServer] = useState<ExternalMCPServer | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ExternalMCPServer | null>(null)
  const [testResult, setTestResult] = useState<{ id: string; success: boolean; tools: string[]; error: string | null } | null>(null)
  const [isTestingBeforeSave, setIsTestingBeforeSave] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [oauthFlow, setOauthFlow] = useState<{ authorization_url: string; state: string; status: 'waiting' | 'success' | 'error'; message?: string; origin: 'add_form' | 'reconnect' } | null>(null)
  const [copiedUrl, setCopiedUrl] = useState(false)
  const [reconnectingId, setReconnectingId] = useState<string | null>(null)

  const isEditing = useMemo(() => form.id !== null, [form.id])
  const isSaving = createMutation.isPending || updateMutation.isPending

  const openCreate = () => {
    setForm(emptyForm)
    setExistingServer(null)
    setShowForm(true)
  }

  const openEdit = (server: ExternalMCPServer) => {
    setForm(fromServer(server))
    setExistingServer(server)
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setExistingServer(null)
    setForm(emptyForm)
  }

  const buildTestConfig = (): { transport: ExternalMCPTransport; config: Record<string, unknown> } | null => {
    if (form.transport === 'stdio') {
      if (isEditing && !form.envTouched && existingServer) {
        return null
      }
      return {
        transport: 'stdio',
        config: {
          command: form.command,
          args: form.args.split('\n').map(s => s.trim()).filter(Boolean),
          env: Object.fromEntries(form.env.filter(e => e.key && e.value !== MASK).map(e => [e.key, e.value])),
        },
      }
    }
    if (isEditing && !form.headersTouched && existingServer) {
      return null
    }
    return {
      transport: form.transport,
      config: {
        url: form.url,
        headers: Object.fromEntries(form.headers.filter(h => h.key && h.value !== MASK).map(h => [h.key, h.value])),
      },
    }
  }

  const openOauthFlowDialog = (authorization_url: string, state: string, origin: 'add_form' | 'reconnect') => {
    setShowForm(false)
    setOauthFlow({ authorization_url, state, status: 'waiting', origin })
    setCopiedUrl(false)
    try {
      window.open(authorization_url, '_blank', 'noopener,noreferrer')
    } catch {
      // popup blocked; URL still shown in dialog for manual copy
    }
  }

  const handleOAuthConnect = async () => {
    setSaveError(null)
    if (!form.name || !form.url) {
      setSaveError('Name and URL are required for OAuth connect')
      return
    }
    setIsTestingBeforeSave(true)
    try {
      const result = await ApiService.startExternalMCPOAuth({
        name: form.name,
        mcp_url: form.url,
        transport: form.transport === 'stdio' ? 'streamable_http' : (form.transport as 'sse' | 'streamable_http'),
        enabled: form.enabled,
        tool_filter: null,
      })
      openOauthFlowDialog(result.authorization_url, result.state, 'add_form')
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'OAuth connect failed')
    } finally {
      setIsTestingBeforeSave(false)
    }
  }

  const handleOAuthReconnect = async (server: ExternalMCPServer) => {
    setReconnectingId(server.id)
    try {
      const result = await ApiService.reconnectExternalMCPOAuth(server.id)
      openOauthFlowDialog(result.authorization_url, result.state, 'reconnect')
    } catch (err: unknown) {
      showToast.error(err instanceof Error ? err.message : 'Reconnect failed')
    } finally {
      setReconnectingId(null)
    }
  }

  useEffect(() => {
    if (!oauthFlow || oauthFlow.status !== 'waiting') return

    let cancelled = false
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; status?: string; name?: string | null; msg?: string | null } | null
      if (!data || data.type !== 'byaan-mcp-oauth') return
      if (data.status === 'success') {
        setOauthFlow(s => s ? { ...s, status: 'success', message: data.name ?? undefined } : s)
        showToast.success(data.name ? `External MCP "${data.name}" connected via OAuth` : 'External MCP connected via OAuth')
      } else {
        setOauthFlow(s => s ? { ...s, status: 'error', message: data.msg ?? 'OAuth failed' } : s)
      }
    }
    window.addEventListener('message', onMessage)

    const poll = async () => {
      while (!cancelled) {
        await new Promise(r => setTimeout(r, 2000))
        if (cancelled) return
        try {
          const result = await ApiService.pollExternalMCPOAuthStatus(oauthFlow.state)
          if (result.status === 'success') {
            setOauthFlow(s => s ? { ...s, status: 'success', message: result.name ?? undefined } : s)
            showToast.success(result.name ? `External MCP "${result.name}" connected via OAuth` : 'External MCP connected via OAuth')
            return
          }
          if (result.status === 'error') {
            setOauthFlow(s => s ? { ...s, status: 'error', message: result.error ?? 'OAuth failed' } : s)
            return
          }
        } catch {
          // transient polling error; retry next tick
        }
      }
    }
    poll()

    return () => {
      cancelled = true
      window.removeEventListener('message', onMessage)
    }
  }, [oauthFlow?.state, oauthFlow?.status])

  const copyAuthUrl = async () => {
    if (!oauthFlow) return
    try {
      await navigator.clipboard.writeText(oauthFlow.authorization_url)
      setCopiedUrl(true)
      setTimeout(() => setCopiedUrl(false), 2000)
    } catch {
      showToast.error('Failed to copy URL')
    }
  }

  const closeOauthFlow = (success: boolean) => {
    const wasReconnect = oauthFlow?.origin === 'reconnect'
    setOauthFlow(null)
    setCopiedUrl(false)
    if (success) {
      queryClient.invalidateQueries({ queryKey: ['external-mcp-servers'] })
      setExistingServer(null)
      setForm(emptyForm)
      return
    }
    if (wasReconnect) {
      setExistingServer(null)
      setForm(emptyForm)
    } else {
      setShowForm(true)
    }
  }

  const handleSave = async () => {
    setSaveError(null)
    if (form.authType === 'oauth' && !isEditing) {
      await handleOAuthConnect()
      return
    }
    const testPayload = buildTestConfig()
    if (testPayload) {
      setIsTestingBeforeSave(true)
      try {
        const result = await ApiService.testExternalMCPConfig(testPayload)
        if (!result.success) {
          setSaveError(result.error || 'Connection test failed')
          setIsTestingBeforeSave(false)
          return
        }
      } catch (err: unknown) {
        setSaveError(err instanceof Error ? err.message : 'Connection test failed')
        setIsTestingBeforeSave(false)
        return
      }
      setIsTestingBeforeSave(false)
    }

    const payload = toPayload(form, existingServer)
    try {
      if (isEditing && form.id) {
        await updateMutation.mutateAsync({ id: form.id, payload })
      } else {
        await createMutation.mutateAsync(payload as ExternalMCPServerPayload)
      }
      closeForm()
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const handleToggleEnabled = async (server: ExternalMCPServer) => {
    await updateMutation.mutateAsync({ id: server.id, payload: { enabled: !server.enabled } })
  }

  const handleTest = async (server: ExternalMCPServer) => {
    const result = await testMutation.mutateAsync(server.id)
    setTestResult({ id: server.id, ...result })
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    await deleteMutation.mutateAsync(deleteTarget.id)
    setDeleteTarget(null)
  }

  const updateHeaders = (next: FormState['headers']) =>
    setForm({ ...form, headers: next, headersTouched: true })
  const updateEnv = (next: FormState['env']) =>
    setForm({ ...form, env: next, envTouched: true })

  if (showForm) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-white">
            {isEditing ? `Edit ${form.name}` : 'Add External MCP Server'}
          </h3>
          <Button variant="outline" size="sm" onClick={closeForm} className="border-gray-700">
            Cancel
          </Button>
        </div>

        <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg p-4 space-y-4">
          {form.transport !== 'stdio' && (
            <div>
              <Label className="text-gray-300">Authentication Method</Label>
              <div className="grid grid-cols-2 gap-3 mt-2">
                <button
                  type="button"
                  disabled={isEditing}
                  onClick={() => !isEditing && setForm({ ...form, authType: 'oauth' })}
                  className={`text-left rounded-lg p-4 border transition-all ${
                    form.authType === 'oauth'
                      ? 'border-brand-orange bg-brand-orange/10'
                      : 'border-gray-700 bg-[#0d0d0d] hover:border-gray-600'
                  } ${isEditing ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Zap className={`w-4 h-4 ${form.authType === 'oauth' ? 'text-brand-orange' : 'text-gray-400'}`} />
                    <div className="text-sm font-semibold text-white">OAuth 2.1</div>
                  </div>
                  <div className="text-xs text-gray-400">Sign in with provider (e.g. PostHog, Notion)</div>
                </button>
                <button
                  type="button"
                  disabled={isEditing}
                  onClick={() => !isEditing && setForm({ ...form, authType: 'static_bearer' })}
                  className={`text-left rounded-lg p-4 border transition-all ${
                    form.authType === 'static_bearer'
                      ? 'border-brand-orange bg-brand-orange/10'
                      : 'border-gray-700 bg-[#0d0d0d] hover:border-gray-600'
                  } ${isEditing ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <KeyRound className={`w-4 h-4 ${form.authType === 'static_bearer' ? 'text-brand-orange' : 'text-gray-400'}`} />
                    <div className="text-sm font-semibold text-white">Static API Key</div>
                  </div>
                  <div className="text-xs text-gray-400">Bearer token (e.g. Retell, GitHub)</div>
                </button>
              </div>
            </div>
          )}

          <div>
            <Label className="text-gray-300">Name</Label>
            <Input
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value.toLowerCase() })}
              placeholder="e.g. my-mcp-server"
              disabled={isEditing}
              className="bg-[#2a2a2a] border-gray-700 text-white"
            />
            <p className="text-xs text-gray-500 mt-1">
              Lowercase, 3–64 chars. Used to namespace the server's tools so they don't collide with other servers.
            </p>
          </div>

          <div>
            <Label className="text-gray-300">Transport</Label>
            <Select
              value={form.transport}
              onValueChange={(v: ExternalMCPTransport) => setForm({ ...form, transport: v })}
              disabled={isEditing}
            >
              <SelectTrigger className="bg-[#2a2a2a] border-gray-700 text-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="streamable_http">Streamable HTTP (recommended)</SelectItem>
                <SelectItem value="sse">Server-Sent Events (SSE)</SelectItem>
                {!isSelfHosted && <SelectItem value="stdio">stdio (local only)</SelectItem>}
              </SelectContent>
            </Select>
          </div>

          {form.transport !== 'stdio' ? (
            <>
              <div>
                <Label className="text-gray-300">URL</Label>
                <Input
                  value={form.url}
                  onChange={e => setForm({ ...form, url: e.target.value })}
                  placeholder="https://example.com/mcp"
                  className="bg-[#2a2a2a] border-gray-700 text-white"
                />
              </div>
              {form.authType === 'oauth' && !isEditing && (
                <div className="rounded-md border border-brand-orange/30 bg-brand-orange/10 p-3 text-xs text-gray-300">
                  Click <span className="text-brand-orange font-semibold">Connect with OAuth</span> below. Byaan will redirect you to the provider's sign-in page. After you approve, you'll land back here with the server connected.
                </div>
              )}
              {form.authType === 'static_bearer' && (
              <div>
                <Label className="text-gray-300">Headers</Label>
                {isEditing && !form.headersTouched && (
                  <p className="text-xs text-yellow-400 mb-2">
                    Secrets masked. Clear and re-enter any header to update it; otherwise existing values are preserved.
                  </p>
                )}
                <div className="space-y-2">
                  {form.headers.map((h, idx) => (
                    <div key={idx} className="flex gap-2">
                      <Input
                        placeholder="Authorization"
                        value={h.key}
                        onChange={e => {
                          const next = [...form.headers]
                          next[idx] = { ...next[idx], key: e.target.value }
                          updateHeaders(next)
                        }}
                        className="bg-[#2a2a2a] border-gray-700 text-white"
                      />
                      <Input
                        placeholder="Bearer YOUR_API_KEY"
                        value={h.value}
                        onChange={e => {
                          const next = [...form.headers]
                          next[idx] = { ...next[idx], value: e.target.value }
                          updateHeaders(next)
                        }}
                        className="bg-[#2a2a2a] border-gray-700 text-white"
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => updateHeaders(form.headers.filter((_, i) => i !== idx))}
                        className="border-gray-700"
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const hasAuthAlready = form.headers.some(h => h.key.toLowerCase() === 'authorization')
                      const prefill = hasAuthAlready
                        ? { key: '', value: '' }
                        : { key: 'Authorization', value: 'Bearer YOUR_API_KEY' }
                      updateHeaders([...form.headers, prefill])
                    }}
                    className="border-gray-700"
                  >
                    <Plus className="w-4 h-4 mr-1" /> Add header
                  </Button>
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  e.g. <code>Authorization</code>: <code>Bearer YOUR_API_KEY</code>
                </p>
              </div>
              )}
            </>
          ) : (
            <>
              <div>
                <Label className="text-gray-300">Command</Label>
                <Input
                  value={form.command}
                  onChange={e => setForm({ ...form, command: e.target.value })}
                  placeholder="npx"
                  className="bg-[#2a2a2a] border-gray-700 text-white"
                />
              </div>
              <div>
                <Label className="text-gray-300">Arguments (one per line)</Label>
                <textarea
                  rows={4}
                  value={form.args}
                  onChange={e => setForm({ ...form, args: e.target.value })}
                  placeholder={'-y\n@modelcontextprotocol/server-github'}
                  className="w-full rounded-md border border-gray-700 bg-[#2a2a2a] px-3 py-2 text-sm font-mono text-white"
                />
              </div>
              <div>
                <Label className="text-gray-300">Environment Variables</Label>
                {isEditing && !form.envTouched && (
                  <p className="text-xs text-yellow-400 mb-2">
                    Secrets masked. Clear and re-enter any variable to update; otherwise existing values are preserved.
                  </p>
                )}
                <div className="space-y-2">
                  {form.env.map((e, idx) => (
                    <div key={idx} className="flex gap-2">
                      <Input
                        placeholder="KEY"
                        value={e.key}
                        onChange={ev => {
                          const next = [...form.env]
                          next[idx] = { ...next[idx], key: ev.target.value }
                          updateEnv(next)
                        }}
                        className="bg-[#2a2a2a] border-gray-700 text-white"
                      />
                      <Input
                        placeholder="value"
                        value={e.value}
                        onChange={ev => {
                          const next = [...form.env]
                          next[idx] = { ...next[idx], value: ev.target.value }
                          updateEnv(next)
                        }}
                        className="bg-[#2a2a2a] border-gray-700 text-white"
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => updateEnv(form.env.filter((_, i) => i !== idx))}
                        className="border-gray-700"
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => updateEnv([...form.env, { key: '', value: '' }])}
                    className="border-gray-700"
                  >
                    <Plus className="w-4 h-4 mr-1" /> Add variable
                  </Button>
                </div>
              </div>
            </>
          )}

          <div className="flex items-center gap-2 pt-2">
            <Switch checked={form.enabled} onCheckedChange={v => setForm({ ...form, enabled: v })} />
            <Label className="text-gray-300">Enabled</Label>
          </div>
        </div>

        {saveError && (
          <div className="bg-red-500/10 border border-red-500/30 rounded px-3 py-2 text-xs text-red-400">
            ✗ {saveError}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={closeForm} className="border-gray-700">Cancel</Button>
          <Button
            onClick={handleSave}
            disabled={!form.name || isSaving || isTestingBeforeSave}
            className="bg-brand-orange hover:bg-brand-orange/90"
          >
            {isTestingBeforeSave ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                {form.authType === 'oauth' && !isEditing ? 'Redirecting…' : 'Testing connection…'}
              </>
            ) : isSaving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                Saving…
              </>
            ) : form.authType === 'oauth' && !isEditing ? (
              <>
                <Check className="w-4 h-4 mr-2" />
                Connect with OAuth
              </>
            ) : (
              <>
                <Check className="w-4 h-4 mr-2" />
                Test & Save
              </>
            )}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-white">External MCP Servers</h3>
          <p className="text-xs text-gray-500 mt-1">
            Connect Byaan's agent to any MCP-compliant server to extend its tools.
          </p>
        </div>
        <Button onClick={openCreate} className="bg-brand-orange hover:bg-brand-orange/90" size="sm">
          <Plus className="w-4 h-4 mr-2" />
          Add Server
        </Button>
      </div>

      <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg p-4 text-xs text-gray-400 space-y-2">
        <div className="text-white font-medium">How it works</div>
        <ol className="space-y-1 list-decimal list-inside">
          <li>Pick an MCP server you want Byaan to use and get credentials (API key for remote servers, env vars for local).</li>
          <li>Click <span className="text-brand-orange">Add Server</span> and choose a transport:
            <ul className="list-disc list-inside ml-4 mt-1 space-y-0.5">
              <li><span className="text-gray-300">Streamable HTTP</span> — modern remote transport. Paste URL + <code className="text-brand-orange">Authorization: Bearer YOUR_API_KEY</code>.</li>
              <li><span className="text-gray-300">SSE</span> — Server-Sent Events. Same URL + Authorization header pattern.</li>
              {!isSelfHosted && (
                <li><span className="text-gray-300">stdio</span> — local subprocess. Provide a command (e.g. <code className="text-brand-orange">npx</code>), args, and env vars with any required secrets.</li>
              )}
            </ul>
          </li>
          <li>Click <span className="text-brand-orange">Test &amp; Save</span> — Byaan verifies the connection before saving.</li>
          <li>Chat normally — Byaan calls the external tools when relevant.</li>
        </ol>
        <p className="text-gray-500">
          Each server's tools are namespaced under its name to prevent collisions. Secrets are encrypted at rest and masked in the UI.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
        </div>
      ) : servers.length === 0 ? (
        <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg px-4 py-8 text-center text-gray-400 text-sm">
          No external MCP servers configured yet.
        </div>
      ) : (
        <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg divide-y divide-gray-800 max-h-[300px] overflow-y-auto custom-scrollbar">
          {(servers as ExternalMCPServer[]).map((server: ExternalMCPServer) => {
            const activeResult = testResult && testResult.id === server.id ? testResult : null
            return (
            <div key={server.id} className="px-4 py-4 hover:bg-gray-800/30 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="text-sm font-medium text-white">{server.name}</span>
                    <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
                      {server.transport}
                    </span>
                    {server.auth_type === 'oauth' && (
                      <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-brand-orange/20 text-brand-orange">
                        OAuth
                      </span>
                    )}
                    {!server.enabled && (
                      <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-gray-700 text-gray-400">
                        disabled
                      </span>
                    )}
                    {server.oauth_status?.needs_reconnect && (
                      <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">
                        reconnect
                      </span>
                    )}
                  </div>
                  <code className="text-xs text-gray-500 font-mono block truncate">
                    {(() => {
                      const cfg = server.config as { command?: string; args?: string[]; url?: string }
                      return server.transport === 'stdio'
                        ? `${cfg.command ?? ''} ${(cfg.args ?? []).join(' ')}`
                        : cfg.url ?? ''
                    })()}
                  </code>
                  {server.decrypt_error && (
                    <div className="mt-2 text-xs text-red-400">
                      ⚠ {server.decrypt_error} Click Edit to re-enter.
                    </div>
                  )}
                  {activeResult && (
                    <div className={`mt-2 text-xs ${activeResult.success ? 'text-green-400' : 'text-red-400'}`}>
                      {activeResult.success
                        ? `✓ Connected · ${activeResult.tools.length} tool(s): ${activeResult.tools.slice(0, 5).join(', ')}${activeResult.tools.length > 5 ? '…' : ''}`
                        : `✗ ${activeResult.error}`}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <Switch
                    checked={server.enabled}
                    onCheckedChange={() => handleToggleEnabled(server)}
                  />
                  {server.auth_type === 'oauth' && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleOAuthReconnect(server)}
                      disabled={reconnectingId === server.id}
                      className="border-gray-700"
                      title="Reconnect OAuth"
                    >
                      {reconnectingId === server.id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <RotateCw className="w-4 h-4" />
                      )}
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleTest(server)}
                    disabled={testMutation.isPending}
                    className="border-gray-700"
                    title="Run connection test"
                  >
                    {testMutation.isPending && testMutation.variables === server.id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Zap className="w-4 h-4" />
                    )}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => openEdit(server)}
                    className="border-gray-700"
                    title="Edit server"
                  >
                    <Pencil className="w-4 h-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setDeleteTarget(server)}
                    className="border-red-500/30 text-red-400 hover:bg-red-500/10"
                    title="Delete server"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            </div>
          )})}
        </div>
      )}

      {oauthFlow && createPortal(
        <div
          className="fixed inset-0 bg-black/80 z-[9999] flex items-center justify-center p-4"
          style={{ pointerEvents: 'auto' }}
          onClick={() => oauthFlow.status !== 'waiting' && closeOauthFlow(oauthFlow.status === 'success')}
        >
          <div
            className="bg-[#1a1a1a] border border-gray-800 rounded-lg max-w-xl w-full p-6"
            style={{ pointerEvents: 'auto' }}
            onClick={e => e.stopPropagation()}
          >
            {oauthFlow.status === 'waiting' && (
              <>
                <div className="flex items-center gap-3 mb-4">
                  <Loader2 className="w-5 h-5 animate-spin text-brand-orange" />
                  <h3 className="text-base font-semibold text-white">Waiting for authorization…</h3>
                </div>
                <p className="text-sm text-gray-400 mb-4">
                  A browser tab opened for you to sign in. Approve access, and this dialog will close automatically.
                </p>
                <div className="bg-[#0d0d0d] border border-gray-800 rounded p-3 mb-3">
                  <div className="text-xs text-gray-500 mb-2">If the tab didn't open, copy this URL and open it manually:</div>
                  <div className="flex gap-2 items-start">
                    <code className="flex-1 text-xs text-gray-300 font-mono break-all max-h-32 overflow-y-auto block">
                      {oauthFlow.authorization_url}
                    </code>
                    <Button variant="outline" size="sm" onClick={copyAuthUrl} className="border-gray-700 flex-shrink-0" title="Copy URL">
                      {copiedUrl ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => window.open(oauthFlow.authorization_url, '_blank', 'noopener,noreferrer')} className="border-gray-700 flex-shrink-0" title="Open in new tab">
                      <ExternalLink className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
                <p className="text-xs text-gray-500 mb-4">
                  Return to this tab after authenticating. We'll detect the completion automatically.
                </p>
                <div className="flex justify-end">
                  <Button variant="outline" onClick={() => closeOauthFlow(false)} className="border-gray-700">
                    Cancel
                  </Button>
                </div>
              </>
            )}
            {oauthFlow.status === 'success' && (
              <>
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-8 h-8 rounded-full bg-green-500/20 border border-green-500 text-green-400 flex items-center justify-center">
                    <Check className="w-4 h-4" />
                  </div>
                  <h3 className="text-base font-semibold text-white">Connected successfully</h3>
                </div>
                <p className="text-sm text-gray-400 mb-4">
                  {oauthFlow.message ? `"${oauthFlow.message}" is now connected and ready to use in chat.` : 'External MCP server connected and ready to use.'}
                </p>
                <div className="flex justify-end">
                  <Button onClick={() => closeOauthFlow(true)} className="bg-brand-orange hover:bg-brand-orange/90">
                    Done
                  </Button>
                </div>
              </>
            )}
            {oauthFlow.status === 'error' && (
              <>
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-8 h-8 rounded-full bg-red-500/20 border border-red-500 text-red-400 flex items-center justify-center">
                    <X className="w-4 h-4" />
                  </div>
                  <h3 className="text-base font-semibold text-white">Authorization failed</h3>
                </div>
                <p className="text-sm text-red-400 mb-4">{oauthFlow.message || 'OAuth connection failed.'}</p>
                <div className="flex justify-end">
                  <Button variant="outline" onClick={() => closeOauthFlow(false)} className="border-gray-700">
                    Close
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>,
        document.body
      )}

      {deleteTarget && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setDeleteTarget(null)}>
          <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg max-w-sm w-full p-5" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-white mb-2">Delete External MCP Server</h3>
            <p className="text-sm text-gray-400 mb-4">
              Delete <span className="font-semibold text-white">{deleteTarget.name}</span>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDeleteTarget(null)} className="border-gray-700">
                Cancel
              </Button>
              <Button
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className="bg-red-500 hover:bg-red-600"
              >
                {deleteMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                Delete
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
