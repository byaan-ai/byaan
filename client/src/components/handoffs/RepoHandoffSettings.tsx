import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, XCircle } from 'lucide-react'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Switch } from '../ui/switch'
import { handoffKeys } from '../../hooks/useHandoffs'
import { ApiService } from '../../services/api'
import { HandoffsApi, type HandoffRepoSettings, type HandoffSetupCheck } from '../../services/handoffs'
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
  const [draft, setDraft] = useState<HandoffRepoSettings | null>(null)
  const [allow, setAllow] = useState('')
  const [deny, setDeny] = useState('')
  const [minutes, setMinutes] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [checks, setChecks] = useState<HandoffSetupCheck[] | null>(null)

  const { data: settings, isLoading } = useQuery({
    queryKey: handoffKeys.repoSettings(repo.id),
    queryFn: () => HandoffsApi.repoSettings(repo.id),
    enabled: open,
  })
  const { data: channels } = useQuery({
    queryKey: ['slack', 'channels'],
    queryFn: () => ApiService.getSlackChannels(),
    enabled: open,
    retry: false,
  })

  useEffect(() => {
    if (!settings) return
    setDraft(settings)
    setAllow(settings.config.paths.allow.join('\n'))
    setDeny(settings.config.paths.deny.join('\n'))
    setMinutes(String(settings.config.minutes))
  }, [settings])

  const update = (patch: Partial<HandoffRepoSettings['config']>) =>
    setDraft((current) => (current ? { ...current, config: { ...current.config, ...patch } } : current))

  const pending = draft && {
    ...draft,
    config: { ...draft.config, minutes: Number(minutes), paths: { allow: toLines(allow), deny: toLines(deny) } },
  }
  const dirty = !!settings && !!pending && stableJson(pending) !== stableJson(settings)

  const save = async () => {
    if (!pending) return
    const limit = pending.config.minutes
    if (!Number.isInteger(limit) || limit < MIN_MINUTES || limit > MAX_MINUTES) {
      showToast.error(`Time limit must be a whole number between ${MIN_MINUTES} and ${MAX_MINUTES} minutes`)
      return
    }
    setSaving(true)
    try {
      const saved = await HandoffsApi.saveRepoSettings(repo.id, pending)
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

  const toggleChannel = (id: string) => {
    if (!draft) return
    const current = draft.config.default_channels
    update({ default_channels: current.includes(id) ? current.filter((c) => c !== id) : [...current, id] })
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
          {isLoading || !draft ? (
            <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
          ) : (
            <>
              <label className="flex items-center justify-between gap-3">
                <span className="text-gray-300">Allow handoffs on this repository</span>
                <Switch checked={draft.enabled} disabled={!canEdit} onCheckedChange={(enabled) => setDraft({ ...draft, enabled })} />
              </label>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="space-y-1">
                  <span className="text-gray-400 text-xs">May edit (one glob per line, empty = whole repo)</span>
                  <textarea rows={3} disabled={!canEdit} value={allow} onChange={(e) => setAllow(e.target.value)}
                    className="w-full rounded-md border border-gray-700 bg-[#111] px-2 py-1 font-mono text-xs text-white" />
                </label>
                <label className="space-y-1">
                  <span className="text-gray-400 text-xs">Never edit (.github/** is always blocked)</span>
                  <textarea rows={3} disabled={!canEdit} value={deny} onChange={(e) => setDeny(e.target.value)}
                    className="w-full rounded-md border border-gray-700 bg-[#111] px-2 py-1 font-mono text-xs text-white" />
                </label>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="space-y-1">
                  <span className="text-gray-400 text-xs">Time limit per run (minutes)</span>
                  <Input type="number" min={MIN_MINUTES} max={MAX_MINUTES} disabled={!canEdit} value={minutes}
                    onChange={(e) => setMinutes(e.target.value)} />
                </label>
                <label className="space-y-1">
                  <span className="text-gray-400 text-xs">Mirror start and result to channel</span>
                  <select disabled={!canEdit} value={draft.config.mirror_channel ?? ''}
                    onChange={(e) => update({ mirror_channel: e.target.value || null })}
                    className="w-full rounded-md border border-gray-700 bg-[#111] px-2 py-1.5 text-white">
                    <option value="">None</option>
                    {(channels ?? []).map((c) => (
                      <option key={c.id} value={c.id}>#{c.name}</option>
                    ))}
                  </select>
                </label>
              </div>

              {(channels ?? []).length > 0 && (
                <div className="space-y-1">
                  <span className="text-gray-400 text-xs">Default for Slack channels (used to pick this repo)</span>
                  <div className="flex flex-wrap gap-2">
                    {(channels ?? []).map((c) => (
                      <button key={c.id} disabled={!canEdit} onClick={() => toggleChannel(c.id)}
                        className={`text-xs px-2 py-1 rounded-full border ${draft.config.default_channels.includes(c.id) ? 'border-brand-orange text-brand-orange' : 'border-gray-700 text-gray-400'}`}>
                        #{c.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <label className="flex items-center justify-between gap-3">
                <span>
                  <span className="block text-gray-300">Let Monitors propose fixes</span>
                  <span className="block text-xs text-gray-500">
                    When a scheduled Monitor finds a code problem in this repo, Byaan drafts a handoff card. The draft must
                    cite the file and line plus a failing check (test, CI or error). Nothing starts until someone approves it.
                  </span>
                </span>
                <Switch checked={draft.config.allow_proposals} disabled={!canEdit} onCheckedChange={(v) => update({ allow_proposals: v })} />
              </label>

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

              <div className="flex gap-2">
                <Button size="sm" variant="brand-primary" onClick={save} disabled={!canEdit || saving || !dirty}>
                  {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Save
                </Button>
                <Button size="sm" variant="outline" className="border-gray-700 text-white" onClick={testSetup} disabled={!canEdit || testing}>
                  {testing && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Test setup
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
