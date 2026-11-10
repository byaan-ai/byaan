import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, XCircle } from 'lucide-react'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Switch } from '../ui/switch'
import { handoffKeys } from '../../hooks/useHandoffs'
import { HandoffsApi, type HandoffSetupCheck } from '../../services/handoffs'
import type { ConnectedRepo } from '../../services/github'
import { showToast } from '../../utils/toast'

interface RepoHandoffSettingsProps {
  repo: ConnectedRepo
  canEdit: boolean
  onChanged: () => void
}

const toLines = (value: string) =>
  value
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean)

const stableJson = (value: unknown): string =>
  JSON.stringify(value, (_key, item) =>
    item && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b)))
      : item,
  )

const MIN_MINUTES = 5
const MAX_MINUTES = 240

export function RepoHandoffSettings({ repo, canEdit, onChanged }: RepoHandoffSettingsProps) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [allow, setAllow] = useState('')
  const [deny, setDeny] = useState('')
  const [minutes, setMinutes] = useState('')
  const [saving, setSaving] = useState(false)
  const [toggling, setToggling] = useState(false)
  const [testing, setTesting] = useState(false)
  const [checks, setChecks] = useState<HandoffSetupCheck[] | null>(null)

  const { data: settings, isLoading } = useQuery({
    queryKey: handoffKeys.repoSettings(repo.id),
    queryFn: () => HandoffsApi.repoSettings(repo.id),
    enabled: open,
  })

  const savedConfig = settings?.config
  useEffect(() => {
    if (!savedConfig) return
    setAllow(savedConfig.paths.allow.join('\n'))
    setDeny(savedConfig.paths.deny.join('\n'))
    setMinutes(String(savedConfig.minutes))
  }, [savedConfig])

  const pendingConfig = savedConfig && {
    ...savedConfig,
    minutes: Number(minutes),
    paths: { allow: toLines(allow), deny: toLines(deny) },
  }
  const dirty = !!pendingConfig && stableJson(pendingConfig) !== stableJson(savedConfig)

  const toggle = async (enabled: boolean) => {
    if (!settings) return
    setToggling(true)
    try {
      const saved = await HandoffsApi.saveRepoSettings(repo.id, { enabled })
      queryClient.setQueryData(handoffKeys.repoSettings(repo.id), { ...settings, enabled: saved.enabled })
      showToast.success(saved.enabled ? 'Handoffs turned on' : 'Handoffs turned off')
      onChanged()
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Could not update handoffs')
    } finally {
      setToggling(false)
    }
  }

  const save = async () => {
    if (!settings || !pendingConfig) return
    const limit = pendingConfig.minutes
    if (!Number.isInteger(limit) || limit < MIN_MINUTES || limit > MAX_MINUTES) {
      showToast.error(`Time limit must be a whole number between ${MIN_MINUTES} and ${MAX_MINUTES} minutes`)
      return
    }
    setSaving(true)
    try {
      const saved = await HandoffsApi.saveRepoSettings(repo.id, { enabled: settings.enabled, config: pendingConfig })
      queryClient.setQueryData(handoffKeys.repoSettings(repo.id), saved)
      showToast.success('Handoff settings saved')
      onChanged()
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Could not save settings')
    } finally {
      setSaving(false)
    }
  }

  const testSetup = async () => {
    setTesting(true)
    setChecks(null)
    try {
      const result = await HandoffsApi.testSetup(repo.id)
      setChecks(result.checks)
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Test setup failed')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="mt-3 border-t border-gray-800 pt-3">
      <button className="flex items-center gap-2 text-sm text-gray-300 hover:text-white" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        Handoffs to Claude Code
        <span className={`text-xs px-2 py-0.5 rounded-full ${repo.handoff_enabled ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-400'}`}>
          {repo.handoff_enabled ? 'On' : 'Off'}
        </span>
      </button>

      {open && (
        <div className="mt-3 space-y-4 text-sm">
          {isLoading || !settings ? (
            <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
          ) : (
            <>
              <label className="flex items-center justify-between gap-3">
                <span className="text-gray-300">Allow handoffs on this repository</span>
                <Switch checked={settings.enabled} disabled={!canEdit || toggling} onCheckedChange={toggle} />
              </label>

              <fieldset
                disabled={!canEdit || !settings.enabled}
                className={`min-w-0 space-y-4 transition-opacity ${settings.enabled ? '' : 'pointer-events-none select-none opacity-40'}`}
              >
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <label className="space-y-1">
                    <span className="text-gray-400 text-xs">May edit (one glob per line, empty = whole repo)</span>
                    <textarea rows={3} value={allow} onChange={(e) => setAllow(e.target.value)}
                      className="w-full rounded-md border border-gray-700 bg-[#111] px-2 py-1 font-mono text-xs text-white" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-gray-400 text-xs">Never edit (.github/** is always blocked)</span>
                    <textarea rows={3} value={deny} onChange={(e) => setDeny(e.target.value)}
                      className="w-full rounded-md border border-gray-700 bg-[#111] px-2 py-1 font-mono text-xs text-white" />
                  </label>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <label className="space-y-1">
                    <span className="text-gray-400 text-xs">Time limit per run (minutes)</span>
                    <Input type="number" min={MIN_MINUTES} max={MAX_MINUTES} value={minutes}
                      onChange={(e) => setMinutes(e.target.value)} />
                  </label>
                </div>

                {checks && (
                  <ul className="space-y-1 text-xs">
                    {checks.map((check) => (
                      <li key={check.name} className="flex items-center gap-2">
                        {check.ok ? <CheckCircle2 className="w-3 h-3 text-green-400" /> : <XCircle className="w-3 h-3 text-red-400" />}
                        <span className="text-gray-300">{check.name}</span>
                        {check.detail && <span className="text-gray-500">{check.detail}</span>}
                      </li>
                    ))}
                  </ul>
                )}

                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="brand-primary" onClick={save} disabled={saving || !dirty}>
                    {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Save
                  </Button>
                  <Button size="sm" variant="outline" className="border-gray-700 text-white" onClick={testSetup} disabled={testing}>
                    {testing && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Test setup
                  </Button>
                </div>
              </fieldset>
            </>
          )}
        </div>
      )}
    </div>
  )
}
