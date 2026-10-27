import { useEffect, useMemo, useState } from 'react'
import { Loader2, Trash2 } from 'lucide-react'
import { Card } from '../ui/card'
import { Button } from '../ui/button'
import { useDeleteSchedule, useUpdateSchedule, type ScheduleRead, type ScheduleUpdate } from '../../hooks/useSchedules'
import { ApiService } from '../../services/api'
import { useSlackConfig } from '../../hooks/useSlackConfig'
import { useAppConfig } from '../../hooks/useAppConfig'

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

interface MonitorCardProps {
  monitor: ScheduleRead
  onChanged?: () => void
}

interface ParsedCron {
  hour: number
  minute: number
  days: Set<number>
}

function parseCron(expr: string | null | undefined): ParsedCron | null {
  if (!expr) return null
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return null
  const [m, h, dom, mo, dow] = parts
  if (dom !== '*' || mo !== '*') return null
  const minute = Number(m)
  const hour = Number(h)
  if (!Number.isInteger(minute) || !Number.isInteger(hour)) return null
  if (minute < 0 || minute > 59 || hour < 0 || hour > 23) return null
  const days = new Set<number>()
  if (dow === '*') {
    for (let i = 0; i < 7; i++) days.add(i)
    return { hour, minute, days }
  }
  for (const token of dow.split(',')) {
    const t = token.trim()
    if (!t) return null
    if (t.includes('-')) {
      const [lo, hi] = t.split('-').map(Number)
      if (!Number.isInteger(lo) || !Number.isInteger(hi)) return null
      if (lo < 0 || hi > 6 || lo > hi) return null
      for (let i = lo; i <= hi; i++) days.add(i)
    } else {
      const d = Number(t)
      if (!Number.isInteger(d) || d < 0 || d > 6) return null
      days.add(d)
    }
  }
  return days.size ? { hour, minute, days } : null
}

function buildCron(hour: number, minute: number, days: Set<number>): string {
  const sortedDays = [...days].sort((a, b) => a - b)
  const dowField = sortedDays.length === 7 ? '*' : sortedDays.join(',')
  return `${minute} ${hour} * * ${dowField}`
}

function statusTone(status: ScheduleRead['status']) {
  switch (status) {
    case 'active':
      return 'bg-green-900/40 text-green-300'
    case 'paused':
      return 'bg-amber-900/40 text-amber-300'
    case 'auto_paused':
      return 'bg-red-900/40 text-red-300'
    default:
      return 'bg-gray-800 text-gray-300'
  }
}

function formatNext(dateStr?: string | null) {
  if (!dateStr) return 'n/a'
  try {
    const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(dateStr) ? dateStr : `${dateStr}Z`
    const d = new Date(normalized)
    if (Number.isNaN(d.getTime())) return dateStr
    return d.toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return dateStr
  }
}

export function MonitorCard({ monitor, onChanged }: MonitorCardProps) {
  const update = useUpdateSchedule()
  const del = useDeleteSchedule()
  const [showEdit, setShowEdit] = useState(false)
  const [draftInstruction, setDraftInstruction] = useState(monitor.instruction || '')

  const parsed = useMemo(() => parseCron(monitor.cron_expression), [monitor.cron_expression])

  const browserTz = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    } catch {
      return 'UTC'
    }
  }, [])

  const { isSelfHosted } = useAppConfig()
  const { isConnected: isSlackConnected } = useSlackConfig(isSelfHosted)
  const [slackChannels, setSlackChannels] = useState<Array<{ id: string; name: string }>>([])

  useEffect(() => {
    if (!isSlackConnected) return
    let cancelled = false
    ApiService.getSlackChannels()
      .then((channels) => {
        if (!cancelled) setSlackChannels(channels)
      })
      .catch((err) => console.error('Failed to load Slack channels:', err))
    return () => {
      cancelled = true
    }
  }, [isSlackConnected])

  const delivery: 'thread' | 'channel' | 'broadcast' = !monitor.slack_thread_ts
    ? 'channel'
    : monitor.broadcast_to_channel
    ? 'broadcast'
    : 'thread'

  const pushPatch = (patch: ScheduleUpdate) => {
    update.mutate(
      { scheduleId: monitor.id, data: patch },
      { onSuccess: () => onChanged?.() }
    )
  }

  const onTimeChange = (value: string) => {
    if (!parsed) return
    const [hh, mm] = value.split(':')
    const hour = Number(hh)
    const minute = Number(mm)
    if (!Number.isInteger(hour) || !Number.isInteger(minute)) return
    pushPatch({ cron_expression: buildCron(hour, minute, parsed.days) })
  }

  const toggleDay = (d: number) => {
    if (!parsed) return
    const next = new Set(parsed.days)
    if (next.has(d)) next.delete(d)
    else next.add(d)
    if (next.size === 0) return
    pushPatch({ cron_expression: buildCron(parsed.hour, parsed.minute, next) })
  }

  const onDeliveryChange = (value: 'thread' | 'channel' | 'broadcast') => {
    if (value === 'thread') {
      pushPatch({ broadcast_to_channel: false })
    } else if (value === 'channel') {
      pushPatch({ slack_thread_ts: null, broadcast_to_channel: false })
    } else {
      pushPatch({ broadcast_to_channel: true })
    }
  }

  const onStatusChange = (value: ScheduleRead['status']) => {
    if (value === monitor.status) return
    pushPatch({ status: value, is_enabled: value === 'active' })
  }

  const onSaveInstruction = () => {
    pushPatch({ instruction: draftInstruction })
    setShowEdit(false)
  }

  const timeValue = parsed
    ? `${String(parsed.hour).padStart(2, '0')}:${String(parsed.minute).padStart(2, '0')}`
    : ''

  const timezoneLabel = monitor.timezone || browserTz
  const timezoneHint = monitor.timezone && monitor.timezone !== browserTz ? ` (your browser: ${browserTz})` : ''

  return (
    <Card className="p-4 bg-[#2a2a2a] border-gray-800 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-white font-medium truncate">{monitor.name}</h4>
            <span
              className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${statusTone(monitor.status)}`}
            >
              {(monitor.status || 'active').replace('_', ' ')}
            </span>
            {monitor.origin && monitor.origin !== 'ui' && (
              <span
                className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
                  monitor.origin === 'slack' ? 'bg-purple-900/40 text-purple-300' : 'bg-blue-900/40 text-blue-300'
                }`}
                title={`Created from ${monitor.origin}`}
              >
                {monitor.origin === 'notebook_chat' ? 'chat' : monitor.origin}
              </span>
            )}
            {monitor.is_running && (
              <span className="text-xs text-brand-orange inline-flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" /> Running
              </span>
            )}
          </div>
          <div className="text-xs text-gray-500 mt-1">Next: {formatNext(monitor.next_run_at)}</div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-red-400 hover:text-red-300"
          onClick={() => del.mutate(monitor.id, { onSuccess: () => onChanged?.() })}
          disabled={del.isPending}
          aria-label="Delete monitor"
        >
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Status</label>
          <select
            className="w-full bg-[#1f1f1f] border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
            value={monitor.status || 'active'}
            onChange={(e) => onStatusChange(e.target.value as ScheduleRead['status'])}
            disabled={update.isPending}
          >
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            {monitor.status === 'auto_paused' && (
              <option value="auto_paused" disabled>
                Auto-paused (resume to re-enable)
              </option>
            )}
          </select>
        </div>

        <div>
          <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Time</label>
          {parsed ? (
            <input
              type="time"
              value={timeValue}
              onChange={(e) => onTimeChange(e.target.value)}
              className="w-full bg-[#1f1f1f] border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
              disabled={update.isPending}
            />
          ) : (
            <code className="text-sm text-gray-400">{monitor.cron_expression}</code>
          )}
        </div>

        <div className="md:col-span-2">
          <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Days</label>
          {parsed ? (
            <div className="flex gap-1.5 flex-wrap">
              {DAY_LABELS.map((label, idx) => {
                const on = parsed.days.has(idx)
                return (
                  <button
                    type="button"
                    key={label}
                    onClick={() => toggleDay(idx)}
                    disabled={update.isPending}
                    className={`text-xs px-2 py-1 rounded border ${
                      on
                        ? 'bg-brand-orange/20 text-brand-orange border-brand-orange/40'
                        : 'bg-[#1f1f1f] text-gray-400 border-gray-700 hover:text-white'
                    }`}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          ) : (
            <span className="text-sm text-gray-400">Edit the schedule in chat to use day pickers.</span>
          )}
        </div>

        <div>
          <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Timezone</label>
          <div className="text-sm text-white">
            {timezoneLabel}
            {timezoneHint && <span className="text-gray-500">{timezoneHint}</span>}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">Ask Byaan in chat to change the timezone.</div>
        </div>

        <div>
          <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Delivery</label>
          <select
            className="w-full bg-[#1f1f1f] border border-gray-700 rounded px-2 py-1.5 text-sm text-white disabled:opacity-60"
            value={monitor.slack_channel_id || '_notebook'}
            onChange={(e) => {
              const v = e.target.value
              if (v === '_notebook') {
                pushPatch({
                  slack_channel_id: null,
                  slack_thread_ts: null,
                  broadcast_to_channel: false,
                })
              } else {
                pushPatch({ slack_channel_id: v })
              }
            }}
            disabled={update.isPending || (!isSlackConnected && !monitor.slack_channel_id)}
          >
            <option value="_notebook">Notebook only</option>
            {slackChannels
              .filter((c) => c.id?.trim())
              .map((c) => (
                <option key={c.id} value={c.id}>
                  #{c.name}
                </option>
              ))}
            {monitor.slack_channel_id &&
              !slackChannels.some((c) => c.id === monitor.slack_channel_id) && (
                <option value={monitor.slack_channel_id}>
                  current channel
                </option>
              )}
          </select>
          {!isSlackConnected && !monitor.slack_channel_id && (
            <div className="text-[11px] text-gray-500 mt-1">Connect Slack to post monitor results to a channel.</div>
          )}
        </div>

        {monitor.slack_channel_id && (
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Post as</label>
            <select
              className="w-full bg-[#1f1f1f] border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
              value={delivery}
              onChange={(e) => onDeliveryChange(e.target.value as 'thread' | 'channel' | 'broadcast')}
              disabled={update.isPending}
            >
              <option value="thread">Reply in Slack thread</option>
              <option value="channel">Post to Slack channel</option>
              <option value="broadcast">Thread + broadcast to channel</option>
            </select>
          </div>
        )}
      </div>

      <div>
        <div className="flex items-center justify-between">
          <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">What it checks</label>
          <button
            type="button"
            className="text-[11px] text-brand-orange hover:text-brand-orange/80"
            onClick={() => setShowEdit((v) => !v)}
          >
            {showEdit ? 'Cancel' : 'Edit'}
          </button>
        </div>
        {showEdit ? (
          <div className="space-y-2">
            <textarea
              value={draftInstruction}
              onChange={(e) => setDraftInstruction(e.target.value)}
              className="w-full bg-[#1f1f1f] border border-gray-700 rounded px-2 py-1.5 text-sm text-white min-h-[90px]"
              placeholder="What should Byaan check each run?"
            />
            <div className="flex gap-2">
              <Button type="button" size="sm" onClick={onSaveInstruction} disabled={update.isPending}>
                Save
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setShowEdit(false)
                  setDraftInstruction(monitor.instruction || '')
                }}
              >
                Discard
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-300 whitespace-pre-wrap">
            {monitor.instruction || <span className="text-gray-500">No instruction set.</span>}
          </p>
        )}
      </div>
    </Card>
  )
}
