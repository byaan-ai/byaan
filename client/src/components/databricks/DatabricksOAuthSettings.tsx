import { useEffect, useState } from 'react'
import { Button } from '../ui/button'
import { Card } from '../ui/card'
import { Input } from '../ui/input'
import { Switch } from '../ui/switch'
import { Loader2, Trash2, Settings2, ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import { ApiService } from '../../services/api'

interface DatabricksOAuthSettingsProps {
  onConfigChanged?: () => void
}

function SetupStepsAccordion({ callbackUrl }: { callbackUrl: string }) {
  const [expandedStep, setExpandedStep] = useState(0)

  const steps = [
    {
      id: 1,
      title: 'Register Byaan as a custom OAuth app integration',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>
              Open{' '}
              <a
                href="https://accounts.cloud.databricks.com/settings/app-integrations"
                target="_blank"
                rel="noopener noreferrer"
                className="text-brand-orange hover:underline inline-flex items-center gap-1"
              >
                Databricks App integrations
                <ExternalLink className="w-3 h-3" />
              </a>
              {' '}as an account admin and click <strong className="text-gray-300">Add connection</strong>. One-time setup — all your users sign in through this app.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>
              Name it <strong className="text-gray-300">"Byaan"</strong>. Set the redirect URL to:
            </span>
          </li>
          <li className="ml-4">
            <code className="text-xs text-brand-orange bg-gray-800 px-2 py-1 rounded block break-all">
              {callbackUrl}
            </code>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>
              Scopes: <code className="text-gray-300">sql</code>, <code className="text-gray-300">offline_access</code>, <code className="text-gray-300">all-apis</code>.
            </span>
          </li>
        </ul>
      ),
    },
    {
      id: 2,
      title: 'Copy the Client ID and Client Secret',
      content: (
        <ul className="space-y-2 text-sm text-gray-400">
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Copy the generated <strong className="text-gray-300">Client ID</strong> and <strong className="text-gray-300">Client Secret</strong>.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="text-gray-500 mt-0.5">•</span>
            <span>Paste both below — they are stored encrypted at rest.</span>
          </li>
        </ul>
      ),
    },
  ]

  return (
    <div className="bg-[#0d0d0d] rounded-lg border border-gray-800 overflow-hidden mb-4">
      <h3 className="text-sm font-medium text-white px-4 py-3 border-b border-gray-800">Setup Guide</h3>
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
            {expandedStep === step.id && <div className="px-4 pb-4 pl-14">{step.content}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

export function DatabricksOAuthSettings({ onConfigChanged }: DatabricksOAuthSettingsProps) {
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [secretConfigured, setSecretConfigured] = useState(false)
  const [redirectUri, setRedirectUri] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    ApiService.getDatabricksOAuthSettings()
      .then((data) => {
        setClientId(data.client_id)
        setSecretConfigured(data.client_secret_configured)
        setRedirectUri(data.redirect_uri)
        if (data.client_secret_configured) setExpanded(true)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    if (!clientId.trim() || !clientSecret.trim()) return
    setSaving(true)
    try {
      await ApiService.saveDatabricksOAuthSettings(clientId.trim(), clientSecret.trim())
      setSecretConfigured(true)
      setClientSecret('')
      onConfigChanged?.()
    } catch (err) {
      console.error('Failed to save Databricks OAuth config:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleRemove = async () => {
    setRemoving(true)
    try {
      await ApiService.deleteDatabricksOAuthSettings()
      setClientId('')
      setClientSecret('')
      setSecretConfigured(false)
      setExpanded(false)
      onConfigChanged?.()
    } catch (err) {
      console.error('Failed to remove Databricks OAuth config:', err)
    } finally {
      setRemoving(false)
    }
  }

  if (loading) {
    return (
      <Card className="bg-[#1a1a1a] border-gray-800 p-6 mb-8">
        <div className="flex items-center justify-center py-4">
          <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
        </div>
      </Card>
    )
  }

  return (
    <Card className="bg-[#1a1a1a] border-gray-800 p-6 mb-8">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings2 className="w-5 h-5 text-gray-400" />
          <div>
            <h3 className="text-white font-medium">Enable Databricks OAuth</h3>
            <p className="text-xs text-gray-500">
              Required so users can sign in to Databricks instead of pasting personal access tokens.
            </p>
          </div>
        </div>
        <Switch checked={expanded} onCheckedChange={setExpanded} />
      </div>

      {expanded && (
        <div className="mt-5 pt-5 border-t border-gray-800">
          <SetupStepsAccordion callbackUrl={redirectUri} />

          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-400 mb-1 block">Client ID</label>
              <Input
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="bg-[#111] border-gray-700 text-white text-sm"
              />
            </div>

            <div>
              <label className="text-xs text-gray-400 mb-1 block">
                Client Secret {secretConfigured && <span className="text-green-400 ml-1">(configured)</span>}
              </label>
              <Input
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder={secretConfigured ? '••••••••••••••••' : 'Enter client secret'}
                className="bg-[#111] border-gray-700 text-white text-sm"
              />
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              {secretConfigured && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleRemove}
                  disabled={removing}
                  className="border-red-800 text-red-400 hover:bg-red-900/20"
                >
                  {removing ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Trash2 className="w-4 h-4 mr-1" />}
                  Remove
                </Button>
              )}
              <Button
                size="sm"
                onClick={handleSave}
                disabled={saving || !clientId.trim() || !clientSecret.trim()}
                className="bg-brand-orange hover:bg-brand-orange/90"
              >
                {saving && <Loader2 className="w-4 h-4 animate-spin mr-1" />}
                Save
              </Button>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
