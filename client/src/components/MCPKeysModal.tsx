import { useState, useEffect } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'

import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs'
import { Loader2, Copy, Trash2, Key, Clock, CheckCircle2, Check } from 'lucide-react'
import { useMCPKeys } from '@/hooks/useMCPKeys'

import { ApiService } from '@/services/api'
import { showToast } from '../utils/toast'
import { useAppConfig } from '@/hooks/useAppConfig'
import { getBackendUrl } from '@/lib/tauri-api'

interface MCPKeysModalProps {
  open: boolean
  onClose: () => void
}

export function MCPKeysModal({ open, onClose }: MCPKeysModalProps) {
  const { isSelfHosted } = useAppConfig()
  const { data: allKeys = [], isLoading, refetch } = useMCPKeys()
  const keys = allKeys.filter(key => key.is_active) // Only show active keys
  const [isCreating, setIsCreating] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [createdKey, setCreatedKey] = useState<{ name: string; key: string } | null>(null)
  const [deletingKeyId, setDeletingKeyId] = useState<string | null>(null)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [keyToDelete, setKeyToDelete] = useState<{ id: string; name: string } | null>(null)
  const [showCreatedKeyDialog, setShowCreatedKeyDialog] = useState(false)
  const [isCopied, setIsCopied] = useState(false)
  const [lastCreatedApiKey, setLastCreatedApiKey] = useState<string | null>(null)
  const [mcpUrl, setMcpUrl] = useState('')
  const [copiedSnippet, setCopiedSnippet] = useState<string | null>(null)
  const [stdioConfig, setStdioConfig] = useState<{ command: string; args: string[]; env: Record<string, string> } | null>(null)

  useEffect(() => {
    if (!open) return
    if (isSelfHosted) {
      setMcpUrl(`${window.location.origin}/api/mcp/`)
    } else {
      getBackendUrl().then(url => setMcpUrl(`${url}/api/mcp/`))
      ApiService.getMCPStdioConfig().then(res => setStdioConfig(res.data)).catch(() => {})
    }
  }, [open, isSelfHosted])

  const displayApiKey = lastCreatedApiKey || 'YOUR_BYAAN_API_KEY'

  const copySnippet = (id: string, text: string) => {
    localStorage.setItem('byaan_mcp_setup_dismissed', 'true')
    navigator.clipboard.writeText(text).catch(() => {})
    setCopiedSnippet(id)
    showToast.success('Copied to clipboard')
    setTimeout(() => setCopiedSnippet(null), 2000)
  }

  const CodeBlock = ({ id, code }: { id: string; code: string }) => (
    <div className="relative group">
      <pre className="bg-[#2a2a2a] rounded-lg p-3 text-xs font-mono text-gray-300 overflow-x-auto pr-10 whitespace-pre-wrap break-all">
        {code}
      </pre>
      <button
        onClick={() => copySnippet(id, code)}
        className="absolute top-2 right-2 p-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-400 hover:text-white transition-colors"
      >
        {copiedSnippet === id ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  )

  const handleCreateKey = async () => {
    if (!newKeyName.trim()) {
      showToast.error('Please enter a key name')
      return
    }

    try {
      setIsCreating(true)
      const response = await ApiService.createMCPKey(newKeyName.trim())
      const apiKey = response.data.api_key
      setCreatedKey({
        name: newKeyName.trim(),
        key: apiKey
      })
      setLastCreatedApiKey(apiKey)
      setNewKeyName('')
      setShowCreatedKeyDialog(true)
      refetch()
    } catch (error: any) {
      showToast.error(error?.response?.data?.detail || 'Failed to create API key')
    } finally {
      setIsCreating(false)
    }
  }

  const handleDeleteClick = (keyId: string, keyName: string) => {
    setKeyToDelete({ id: keyId, name: keyName })
    setShowDeleteConfirm(true)
  }

  const handleConfirmDelete = async () => {
    if (!keyToDelete) return

    try {
      setDeletingKeyId(keyToDelete.id)
      await ApiService.revokeMCPKey(keyToDelete.id)
      setShowDeleteConfirm(false)
      setKeyToDelete(null)
      refetch()
      showToast.success('API key deleted successfully')
    } catch (error: any) {
      showToast.error(error?.response?.data?.detail || 'Failed to delete API key')
    } finally {
      setDeletingKeyId(null)
    }
  }

  const handleCancelDelete = () => {
    setShowDeleteConfirm(false)
    setKeyToDelete(null)
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setIsCopied(true)
    showToast.success('Copied to clipboard')
    setTimeout(() => setIsCopied(false), 2000)
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  if (isLoading) {
    return (
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-3xl">
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-brand-orange" />
          </div>
        </DialogContent>
      </Dialog>
    )
  }


  return (
    <>
      {/* Main Dialog */}
      <Dialog open={open} onOpenChange={(o) => !o && !showDeleteConfirm && !showCreatedKeyDialog && onClose()}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-3xl max-h-[90vh] overflow-y-auto custom-scrollbar">
          <DialogHeader>
            <DialogTitle className="text-white flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-brand-orange/20 flex items-center justify-center">
                <Key className="w-5 h-5 text-brand-orange" />
              </div>
              {isSelfHosted ? 'MCP API Keys' : 'MCP Setup'}
            </DialogTitle>
            <DialogDescription className="text-gray-400">
              {isSelfHosted
                ? 'Generate keys to connect Claude AI, Cursor, or other MCP clients to Byaan'
                : 'Connect Claude AI, Cursor, or other MCP clients to Byaan'}
            </DialogDescription>
          </DialogHeader>

        <div className="space-y-6 mt-4">
          {isSelfHosted && (
            <>
              {/* Create New Key Form */}
              <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg p-4">
                <h3 className="text-sm font-medium text-white mb-3">Generate New API Key</h3>
                <div className="space-y-3">
                  <div className="space-y-2">
                    <Label htmlFor="keyName" className="text-gray-300">
                      Key Name
                    </Label>
                    <Input
                      id="keyName"
                      type="text"
                      placeholder="e.g., 'Claude Code' or 'Cursor'"
                      value={newKeyName}
                      onChange={(e) => setNewKeyName(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && !isCreating && handleCreateKey()}
                      className="bg-[#2a2a2a] border-gray-700 text-white"
                    />
                    <p className="text-xs text-gray-500">
                      A descriptive name to identify where this key is used
                    </p>
                  </div>
                  <Button
                    onClick={handleCreateKey}
                    disabled={isCreating || !newKeyName.trim()}
                    className="bg-brand-orange hover:bg-brand-orange/90 w-full"
                  >
                    {isCreating ? (
                      <>
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        Generating...
                      </>
                    ) : (
                      <>
                        <Key className="w-4 h-4 mr-2" />
                        Generate API Key
                      </>
                    )}
                  </Button>
                </div>
              </div>

              {/* Active Keys List */}
              <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg">
                <h3 className="text-sm font-medium text-white px-4 py-3 border-b border-gray-800">
                  Active Keys
                </h3>
                {keys.length === 0 ? (
                  <div className="px-4 py-8 text-center text-gray-400 text-sm">
                    No API keys yet. Generate one to get started.
                  </div>
                ) : (
                  <div className="divide-y divide-gray-800 max-h-[144px] overflow-y-auto custom-scrollbar">
                    {keys.map((key) => (
                      <div key={key.id} className="px-4 py-4 hover:bg-gray-800/30 transition-colors">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-2">
                              <Key className="w-4 h-4 text-gray-400 flex-shrink-0" />
                              <span className="text-sm font-medium text-white">{key.name}</span>
                            </div>
                            <code className="text-xs text-gray-400 font-mono block mb-2">
                              {key.key_prefix}••••••••••••••••••••
                            </code>
                            <div className="flex items-center gap-4 text-xs text-gray-500">
                              <span className="flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                Created {formatDate(key.created_at)}
                              </span>
                              {key.last_used_at && (
                                <span>Last used {formatDate(key.last_used_at)}</span>
                              )}
                            </div>
                          </div>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDeleteClick(key.id, key.name)}
                            className="border-red-500/30 text-red-400 hover:bg-red-500/10"
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {/* Setup Instructions */}
          <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg p-4">
            <h3 className="text-sm font-medium text-white mb-3">Setup Instructions</h3>

            {isSelfHosted && (
              <>
                <div className="flex items-center gap-2 mb-4">
                  <span className="text-xs text-gray-400">MCP Endpoint:</span>
                  <code className="text-xs font-mono text-brand-orange bg-[#2a2a2a] px-2 py-1 rounded flex-1 min-w-0 truncate">
                    {mcpUrl || 'Loading...'}
                  </code>
                  {mcpUrl && (
                    <button
                      onClick={() => copySnippet('mcp-url', mcpUrl)}
                      className="p-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-400 hover:text-white transition-colors flex-shrink-0"
                    >
                      {copiedSnippet === 'mcp-url' ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  )}
                </div>

                {lastCreatedApiKey && (
                  <div className="bg-green-500/10 border border-green-500/20 rounded p-2 mb-4">
                    <p className="text-xs text-green-400">Your new API key has been auto-filled into the instructions below.</p>
                  </div>
                )}
              </>
            )}

            <Tabs defaultValue="claude-code">
              <TabsList className="bg-[#1a1a1a] border border-gray-800 w-full grid grid-cols-3">
                <TabsTrigger value="claude-code" className="text-xs data-[state=active]:bg-brand-orange/10 data-[state=active]:text-brand-orange">
                  Claude Code
                </TabsTrigger>
                <TabsTrigger value="cursor" className="text-xs data-[state=active]:bg-brand-orange/10 data-[state=active]:text-brand-orange">
                  Cursor
                </TabsTrigger>
                <TabsTrigger value="codex" className="text-xs data-[state=active]:bg-brand-orange/10 data-[state=active]:text-brand-orange">
                  Codex CLI
                </TabsTrigger>
              </TabsList>

              <TabsContent value="claude-code" className="space-y-3 mt-3">
                {isSelfHosted ? (
                  <>
                    <p className="text-xs text-gray-400">Run this command in your terminal:</p>
                    <CodeBlock
                      id="claude-code"
                      code={`claude mcp add-json byaan '{"type":"http","url":"${mcpUrl}","headers":{"Authorization":"Bearer ${displayApiKey}"}}' --scope user`}
                    />
                    <div className="space-y-1 text-xs text-gray-400">
                      <p>Then verify the connection:</p>
                      <CodeBlock id="claude-verify" code="claude mcp list" />
                      <p className="mt-2">Restart Claude Code to activate. Use Byaan naturally in conversation (e.g. "query my database for...").</p>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="text-xs text-gray-400">Run this command in your terminal:</p>
                    {stdioConfig ? (
                      <CodeBlock
                        id="claude-code"
                        code={`claude mcp add-json byaan '${JSON.stringify({
                          type: "stdio",
                          command: stdioConfig.command,
                          args: stdioConfig.args,
                          ...(Object.keys(stdioConfig.env).length > 0 ? { env: stdioConfig.env } : {})
                        })}' --scope user`}
                      />
                    ) : (
                      <p className="text-xs text-gray-500">Loading configuration...</p>
                    )}
                    <div className="space-y-1 text-xs text-gray-400">
                      <p>Then verify the connection:</p>
                      <CodeBlock id="claude-verify" code="claude mcp list" />
                      <p className="mt-2">Restart Claude Code to activate. Use Byaan naturally in conversation (e.g. "query my database for...").</p>
                    </div>
                  </>
                )}
              </TabsContent>

              <TabsContent value="cursor" className="space-y-3 mt-3">
                {isSelfHosted ? (
                  <>
                    <p className="text-xs text-gray-400">
                      Go to Cursor Settings &gt; Tools & MCP &gt; New MCP Server, or add to <code className="text-gray-300">~/.cursor/mcp.json</code> (global) or <code className="text-gray-300">.cursor/mcp.json</code> (project):
                    </p>
                    <CodeBlock
                      id="cursor"
                      code={JSON.stringify({
                        mcpServers: {
                          byaan: {
                            type: "http",
                            url: mcpUrl,
                            headers: {
                              Authorization: `Bearer ${displayApiKey}`
                            }
                          }
                        }
                      }, null, 2)}
                    />
                    <p className="text-xs text-gray-400">Restart Cursor after saving. Use <code className="text-gray-300">@ask_byaan</code> in Agent chat to query your data.</p>
                  </>
                ) : (
                  <>
                    <p className="text-xs text-gray-400">
                      Go to Cursor Settings &gt; Tools & MCP &gt; New MCP Server, or add to <code className="text-gray-300">~/.cursor/mcp.json</code> (global) or <code className="text-gray-300">.cursor/mcp.json</code> (project):
                    </p>
                    {stdioConfig ? (
                      <CodeBlock
                        id="cursor"
                        code={JSON.stringify({
                          mcpServers: {
                            byaan: {
                              command: stdioConfig.command,
                              args: stdioConfig.args,
                              ...(Object.keys(stdioConfig.env).length > 0 ? { env: stdioConfig.env } : {})
                            }
                          }
                        }, null, 2)}
                      />
                    ) : (
                      <p className="text-xs text-gray-500">Loading configuration...</p>
                    )}
                    <p className="text-xs text-gray-400">Restart Cursor after saving. Use <code className="text-gray-300">@ask_byaan</code> in Agent chat to query your data.</p>
                  </>
                )}
              </TabsContent>

              <TabsContent value="codex" className="space-y-3 mt-3">
                {isSelfHosted ? (
                  <>
                    <p className="text-xs text-gray-400">
                      Add to <code className="text-gray-300">~/.codex/config.toml</code>:
                    </p>
                    <CodeBlock
                      id="codex"
                      code={`[mcp_servers.byaan]\nurl = "${mcpUrl}"\nhttp_headers = { "Authorization" = "Bearer ${displayApiKey}" }`}
                    />
                    <p className="text-xs text-gray-400">Start Codex and run <code className="text-gray-300">/mcp</code> to verify byaan is enabled. Ask questions using Byaan in natural language.</p>
                  </>
                ) : (
                  <>
                    <p className="text-xs text-gray-400">
                      Add to <code className="text-gray-300">~/.codex/config.toml</code>:
                    </p>
                    {stdioConfig ? (
                      <CodeBlock
                        id="codex"
                        code={`[mcp_servers.byaan]\ncommand = "${stdioConfig.command}"\nargs = [${stdioConfig.args.map(a => `"${a}"`).join(', ')}]`}
                      />
                    ) : (
                      <p className="text-xs text-gray-500">Loading configuration...</p>
                    )}
                    <p className="text-xs text-gray-400">Start Codex and run <code className="text-gray-300">/mcp</code> to verify byaan is enabled. Ask questions using Byaan in natural language.</p>
                  </>
                )}
              </TabsContent>
            </Tabs>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <Button
            variant="outline"
            onClick={onClose}
            className="border-gray-700"
          >
            Close
          </Button>
        </div>
        </DialogContent>
      </Dialog>

      {/* Created Key Dialog */}
      <Dialog open={showCreatedKeyDialog} onOpenChange={(o) => {
        if (!o) {
          setShowCreatedKeyDialog(false)
          setCreatedKey(null)
        }
      }}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-white">API Key Created</DialogTitle>
            {createdKey && (
              <DialogDescription className="text-gray-400">
                Save "{createdKey.name}" securely. You won't be able to see it again.
              </DialogDescription>
            )}
          </DialogHeader>

          {createdKey && (
            <div className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label htmlFor="apiKey" className="text-gray-300">
                  API Key
                </Label>
                <Input
                  id="apiKey"
                  type="text"
                  value={createdKey.key}
                  readOnly
                  className="bg-[#2a2a2a] border-gray-700 text-green-400 font-mono text-xs"
                />
                <p className="text-xs text-gray-500">
                  Copy this key and store it in a secure location
                </p>
              </div>

              <div className="bg-amber-500/10 border border-amber-500/20 rounded p-3">
                <p className="text-xs text-amber-400">
                  ⚠️ This key will not be shown again after closing this dialog
                </p>
              </div>
            </div>
          )}

          <div className="flex justify-end gap-3 mt-4">
            <Button
              variant="outline"
              onClick={() => {
                setShowCreatedKeyDialog(false)
                setCreatedKey(null)
              }}
              className="border-gray-700"
            >
              Close
            </Button>
            {createdKey && (
              <Button
                onClick={() => copyToClipboard(createdKey.key)}
                className="bg-brand-orange hover:bg-brand-orange/90 transition-all"
              >
                {isCopied ? (
                  <>
                    <CheckCircle2 className="w-4 h-4 mr-2" />
                    Copied!
                  </>
                ) : (
                  <>
                    <Copy className="w-4 h-4 mr-2" />
                    Copy Key
                  </>
                )}
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={showDeleteConfirm} onOpenChange={(o) => {
        if (!o) {
          handleCancelDelete()
        }
      }}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-white">Delete API Key?</DialogTitle>
            {keyToDelete && (
              <DialogDescription className="text-gray-400">
                Are you sure you want to delete "{keyToDelete.name}"? This will immediately revoke access and cannot be undone.
              </DialogDescription>
            )}
          </DialogHeader>
          <div className="flex justify-end gap-3 mt-4">
            <Button
              variant="outline"
              onClick={handleCancelDelete}
              className="border-gray-700"
              disabled={deletingKeyId !== null}
            >
              Cancel
            </Button>
            <Button
              onClick={handleConfirmDelete}
              disabled={deletingKeyId !== null}
              className="bg-red-500 hover:bg-red-600"
            >
              {deletingKeyId !== null ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Deleting...
                </>
              ) : (
                'Delete Key'
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
