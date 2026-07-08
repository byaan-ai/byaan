import { useState, useEffect } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../ui/dialog'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select'
import { Loader2, Eye, EyeOff, Slack, Trash2, Settings, CheckCircle2, ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import { useLLMConnections } from '@/hooks/useLLMConnections'
import { useSlackConfig } from '@/hooks/useSlackConfig'
import { SlackSkillsSection } from './SlackSkillsSection'

interface SetupStep {
  id: number
  title: string
  content: React.ReactNode
}

function SetupStepsAccordion({ webhookUrl }: { webhookUrl: string }) {
  const [expandedStep, setExpandedStep] = useState(1)

  const steps: SetupStep[] = [
    {
      id: 1,
      title: 'Create Your Slack App',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>
              Go to{' '}
              <a
                href="https://api.slack.com/apps"
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand-orange hover:underline inline-flex items-center gap-1"
              >
                api.slack.com/apps
                <ExternalLink className="w-3 h-3" />
              </a>
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Click <strong className="text-gray-300">"Create New App"</strong> → <strong className="text-gray-300">"From scratch"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Enter app name (e.g., "Byaan") and select your workspace</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Click <strong className="text-gray-300">"Create App"</strong></span>
          </li>
        </ul>
      ),
    },
    {
      id: 2,
      title: 'Configure Bot Permissions',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>In left sidebar, click <strong className="text-gray-300">"OAuth & Permissions"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Scroll to <strong className="text-gray-300">"Scopes"</strong> → <strong className="text-gray-300">"Bot Token Scopes"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Add these scopes:</span>
          </li>
          <li className="ml-4 space-y-1">
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">app_mentions:read</code>
              <span className="text-gray-500 text-xs">— To receive @mentions</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">chat:write</code>
              <span className="text-gray-500 text-xs">— To send messages</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">files:write</code>
              <span className="text-gray-500 text-xs">— To upload screenshots and HTML files</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">channels:read</code>
              <span className="text-gray-500 text-xs">— To list channels (optional)</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">channels:history</code>
              <span className="text-gray-500 text-xs">— To follow up in public channel threads without re-mention</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">groups:history</code>
              <span className="text-gray-500 text-xs">— To follow up in private channel threads without re-mention</span>
            </div>
          </li>
        </ul>
      ),
    },
    {
      id: 3,
      title: 'Install App to Workspace',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Scroll up and click <strong className="text-gray-300">"Install to Workspace"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Review permissions and click <strong className="text-gray-300">"Allow"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>
              Copy the <strong className="text-gray-300">"Bot User OAuth Token"</strong>{' '}
              <span className="text-gray-500">(starts with <code className="text-xs bg-gray-700 px-1 rounded">xoxb-</code>)</span>
            </span>
          </li>
        </ul>
      ),
    },
    {
      id: 4,
      title: 'Set Up Event Subscriptions',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>In left sidebar, click <strong className="text-gray-300">"Event Subscriptions"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Toggle <strong className="text-gray-300">"Enable Events"</strong> to ON</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Paste this Request URL:</span>
          </li>
          <li className="ml-4">
            <code className="text-xs text-brand-orange bg-gray-800 px-2 py-1 rounded block break-all">
              {webhookUrl}
            </code>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Under <strong className="text-gray-300">"Subscribe to bot events"</strong>, add:</span>
          </li>
          <li className="ml-4 space-y-1">
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">app_mention</code>
              <span className="text-gray-500 text-xs">— For direct @Byaan mentions</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">message.channels</code>
              <span className="text-gray-500 text-xs">— For thread follow-ups in public channels</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-gray-700 px-1.5 py-0.5 rounded">message.groups</code>
              <span className="text-gray-500 text-xs">— For thread follow-ups in private channels</span>
            </div>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Click <strong className="text-gray-300">"Save Changes"</strong></span>
          </li>
        </ul>
      ),
    },
    {
      id: 5,
      title: 'Configure Interactivity & Shortcuts',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>In left sidebar, click <strong className="text-gray-300">"Interactivity & Shortcuts"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Toggle <strong className="text-gray-300">"Interactivity"</strong> to ON</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Paste this Request URL:</span>
          </li>
          <li className="ml-4">
            <code className="text-xs text-brand-orange bg-gray-800 px-2 py-1 rounded block break-all">
              {webhookUrl.replace('/events', '/interactivity')}
            </code>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Click <strong className="text-gray-300">"Save Changes"</strong></span>
          </li>
          <li className="flex items-start gap-2 mt-2 pt-2 border-t border-gray-800">
            <span className="text-gray-500 text-xs">ℹ️</span>
            <span className="text-xs">This enables the "Generate Dashboard" button functionality</span>
          </li>
        </ul>
      ),
    },
    {
      id: 6,
      title: 'Get Signing Secret',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>In left sidebar, click <strong className="text-gray-300">"Basic Information"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Scroll to <strong className="text-gray-300">"App Credentials"</strong></span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Click <strong className="text-gray-300">"Show"</strong> next to Signing Secret and copy it</span>
          </li>
        </ul>
      ),
    },
  ]

  return (
    <div className="bg-[#0d0d0d] rounded-lg border border-gray-800 overflow-hidden">
      <h3 className="text-sm font-medium text-white px-4 py-3 border-b border-gray-800">
        Setup Guide
      </h3>
      <div className="divide-y divide-gray-800">
        {steps.map((step) => (
          <div key={step.id}>
            <button
              type="button"
              onClick={() => setExpandedStep(expandedStep === step.id ? 0 : step.id)}
              className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-gray-800/50 transition-colors"
            >
              {expandedStep === step.id ? (
                <ChevronDown className="w-4 h-4 text-brand-orange flex-shrink-0" />
              ) : (
                <ChevronRight className="w-4 h-4 text-gray-500 flex-shrink-0" />
              )}
              <span className="text-brand-orange font-mono text-sm w-4">{step.id}.</span>
              <span className={`text-sm ${expandedStep === step.id ? 'text-white' : 'text-gray-400'}`}>
                {step.title}
              </span>
            </button>
            {expandedStep === step.id && (
              <div className="px-4 pb-4 pl-14">
                {step.content}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

interface SlackIntegrationModalProps {
  open: boolean
  onClose: () => void
}

export function SlackIntegrationModal({ open, onClose }: SlackIntegrationModalProps) {
  const {
    slackConfig,
    isConnected,
    loading,
    saving,
    connect,
    disconnect,
    updateSettings,
  } = useSlackConfig()

  const { data: llmConnections = [] } = useLLMConnections()

  const MASKED_CREDENTIAL = '••••••••••••••••'
  const [showSettingsForm, setShowSettingsForm] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)

  const [botToken, setBotToken] = useState('')
  const [signingSecret, setSigningSecret] = useState('')
  const [selectedLLMConnection, setSelectedLLMConnection] = useState<string>('')
  const [showBotToken, setShowBotToken] = useState(false)
  const [showSigningSecret, setShowSigningSecret] = useState(false)

  useEffect(() => {
    if (open && slackConfig) {
      setSelectedLLMConnection(slackConfig.default_llm_connection_id || '')
    }
  }, [open, slackConfig])

  useEffect(() => {
    if (!open) {
      resetForm()
      setShowSettingsForm(false)
      setShowDeleteConfirm(false)
    }
  }, [open])

  const isDirty = 
  (botToken !== MASKED_CREDENTIAL && botToken.trim() !== '') || 
  (signingSecret !== MASKED_CREDENTIAL && signingSecret.trim() !== '') || 
  (selectedLLMConnection !== (slackConfig?.default_llm_connection_id || ''));

  function resetForm() {
    setBotToken('')
    setSigningSecret('')
    setShowBotToken(false)
    setShowSigningSecret(false)
  }

  function openEditMode() {
    setBotToken(MASKED_CREDENTIAL)
    setSigningSecret(MASKED_CREDENTIAL)
    setSelectedLLMConnection(slackConfig?.default_llm_connection_id || '')
    setShowSettingsForm(true)
  }

  async function handleConnect() {
    if (!botToken.trim() || !signingSecret.trim()) return

    try {
      await connect({
        bot_token: botToken.trim(),
        signing_secret: signingSecret.trim(),
        default_llm_connection_id: selectedLLMConnection || null,
      })
      resetForm()
    } catch (error) {
      console.error('Failed to connect Slack:', error)
    }
  }

  async function handleUpdateSettings() {
    try {
      const updates: {
        bot_token?: string
        signing_secret?: string
        default_llm_connection_id?: string | null
      } = {
        default_llm_connection_id: selectedLLMConnection || null,
      }

      if (botToken !== MASKED_CREDENTIAL && botToken.trim()) {
        updates.bot_token = botToken.trim()
      }

      if (signingSecret !== MASKED_CREDENTIAL && signingSecret.trim()) {
        updates.signing_secret = signingSecret.trim()
      }

      await updateSettings(updates)
      resetForm()
      setShowSettingsForm(false)
    } catch (error) {
      console.error('Failed to update Slack settings:', error)
    }
  }

  async function handleDisconnect() {
    try {
      await disconnect()
      setShowDeleteConfirm(false)
    } catch (error) {
      console.error('Failed to disconnect Slack:', error)
    }
  }

  if (loading) {
    return (
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-2xl">
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-brand-orange" />
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  if (showDeleteConfirm) {
    return (
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-white">Disconnect Slack?</DialogTitle>
            <DialogDescription className="text-gray-400">
              This will remove the Slack integration. Your team won't be able to @mention Byaan in Slack anymore.
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-3 mt-4">
            <Button
              variant="outline"
              onClick={() => setShowDeleteConfirm(false)}
              className="border-gray-700"
            >
              Cancel
            </Button>
            <Button
              onClick={handleDisconnect}
              disabled={saving}
              className="bg-red-500 hover:bg-red-600"
            >
              {saving ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Disconnecting...
                </>
              ) : (
                'Disconnect'
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  if (isConnected && showSettingsForm) {
    return (
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-2xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-white flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-[#4A154B] flex items-center justify-center">
                <Slack className="w-5 h-5 text-white" />
              </div>
              Update Slack Integration
            </DialogTitle>
            <DialogDescription className="text-gray-400">
              Update credentials or model selection
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-6 mt-4 overflow-y-auto flex-1 custom-scrollbar">
            <SetupStepsAccordion webhookUrl={`${window.location.origin}/api/slack/events`} />

            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="botToken" className="text-gray-300">
                  Bot User OAuth Token
                </Label>
                <Input
                  id="botToken"
                  type="password"
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  placeholder="xoxb-..."
                  className="bg-[#2a2a2a] border-gray-700 text-white"
                />
                <p className="text-xs text-gray-500">
                  Leave as ••• to keep current token, or enter new token to update
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="signingSecret" className="text-gray-300">
                  Signing Secret
                </Label>
                <Input
                  id="signingSecret"
                  type="password"
                  value={signingSecret}
                  onChange={(e) => setSigningSecret(e.target.value)}
                  placeholder="Enter signing secret"
                  className="bg-[#2a2a2a] border-gray-700 text-white"
                />
                <p className="text-xs text-gray-500">
                  Leave as ••• to keep current secret, or enter new secret to update
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="llmConnection" className="text-gray-300">
                  Default AI Model
                </Label>
                <Select
                  value={selectedLLMConnection}
                  onValueChange={setSelectedLLMConnection}
                >
                  <SelectTrigger className="bg-[#2a2a2a] border-gray-700 text-white">
                    <SelectValue placeholder="Select a model" />
                  </SelectTrigger>
                  <SelectContent className="bg-[#2a2a2a] border-gray-700">
                    {llmConnections
                      .filter((conn) => conn.id && conn.id.trim() !== '')
                      .map((conn) => (
                      <SelectItem key={conn.id} value={conn.id}>
                        {conn.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-gray-500">
                  Model used for answering Slack questions
                </p>
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-gray-800">
            <Button
              variant="outline"
              onClick={() => {
                resetForm()
                setShowSettingsForm(false)
              }}
              className="border-gray-700"
            >
              Cancel
            </Button>
            <Button
              onClick={handleUpdateSettings}
              disabled={saving || !isDirty}
              className="bg-brand-orange hover:bg-brand-orange/90 disabled:opacity-50"
            >
              {saving ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Updating...
                </>
              ) : (
                'Update Slack'
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  if (isConnected) {
    return (
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-white flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-[#4A154B] flex items-center justify-center">
                <Slack className="w-5 h-5 text-white" />
              </div>
              Slack Integration
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-6 mt-2">
            <div className="bg-[#0d0d0d] border border-gray-800 rounded-lg p-4">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <CheckCircle2 className="w-5 h-5 text-green-400" />
                  <div>
                    <p className="text-sm font-medium text-white">
                      Connected to: {slackConfig?.slack_team_name || slackConfig?.slack_team_id}
                    </p>
                    {slackConfig?.default_llm_connection_id && (
                      <p className="text-xs text-gray-400 mt-0.5">
                        Default Model: {llmConnections.find(c => c.id === slackConfig.default_llm_connection_id)?.name || 'Unknown'}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={openEditMode}
                    className="border-gray-700 hover:bg-gray-800"
                  >
                    <Settings className="w-4 h-4 mr-2" />
                    Settings
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowDeleteConfirm(true)}
                    className="border-red-500/30 text-red-400 hover:bg-red-500/10"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            </div>

            <SlackSkillsSection isSlackConnected={true} />
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-[#1a1a1a] border-gray-800 max-w-2xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-white flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-[#4A154B] flex items-center justify-center">
              <Slack className="w-5 h-5 text-white" />
            </div>
            Slack Integration
          </DialogTitle>
          <DialogDescription className="text-gray-400">
            Connect Slack to let your team @mention Byaan in channels.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 mt-4 overflow-y-auto flex-1 custom-scrollbar">
          <SetupStepsAccordion webhookUrl={`${window.location.origin}/api/slack/events`} />

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="botToken" className="text-gray-300">
                Bot User OAuth Token
              </Label>
              <div className="relative">
                <Input
                  id="botToken"
                  type={showBotToken ? 'text' : 'password'}
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  placeholder="xoxb-..."
                  className="bg-[#2a2a2a] border-gray-700 text-white pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowBotToken(!showBotToken)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
                >
                  {showBotToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-gray-500">
                Found in OAuth & Permissions → Bot User OAuth Token
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="signingSecret" className="text-gray-300">
                Signing Secret
              </Label>
              <div className="relative">
                <Input
                  id="signingSecret"
                  type={showSigningSecret ? 'text' : 'password'}
                  value={signingSecret}
                  onChange={(e) => setSigningSecret(e.target.value)}
                  placeholder="Enter signing secret"
                  className="bg-[#2a2a2a] border-gray-700 text-white pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowSigningSecret(!showSigningSecret)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
                >
                  {showSigningSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              <p className="text-xs text-gray-500">
                Found in Basic Information → App Credentials → Signing Secret
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="llmConnection" className="text-gray-300">
                Default AI Model
              </Label>
              <Select
                value={selectedLLMConnection}
                onValueChange={setSelectedLLMConnection}
              >
                <SelectTrigger className="bg-[#2a2a2a] border-gray-700 text-white">
                  <SelectValue placeholder="Select a model" />
                </SelectTrigger>
                <SelectContent className="bg-[#2a2a2a] border-gray-700">
                  {llmConnections
                    .filter((conn) => conn.id && conn.id.trim() !== '')
                    .map((conn) => (
                    <SelectItem key={conn.id} value={conn.id}>
                      {conn.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-gray-500">
                Model used for answering Slack questions
              </p>
            </div>

          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-gray-800">
          <Button
            variant="outline"
            onClick={onClose}
            className="border-gray-700"
          >
            Cancel
          </Button>
          <Button
            onClick={handleConnect}
            disabled={saving || !botToken.trim() || !signingSecret.trim()}
            className="bg-brand-orange hover:bg-brand-orange/90"
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Connecting...
              </>
            ) : (
              'Connect Slack'
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
