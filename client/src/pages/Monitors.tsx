import { useMemo, useState } from 'react'
import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import {
  Loader2,
  Play,
  Pause,
  Pencil,
  Trash2,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  CircleOff,
} from 'lucide-react'
import {
  useSchedules,
  useUpdateSchedule,
  useDeleteSchedule,
  useTestSchedule,
  useScheduleRuns,
  useMonitorRunningState,
  useRunningStage,
  clearMonitorRunning,
  type ScheduleRead,
  type ScheduleRunRead,
} from '../hooks/useSchedules'
import { MonitorCard } from '../components/monitors/MonitorCard'
import { ScheduleCard } from '../components/schedules/ScheduleCard'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '../components/ui/dialog'
import { useNavigate } from 'react-router-dom'
import { showToast } from '../utils/toast'

type FilterKey = 'all' | 'active' | 'paused' | 'attention' | 'schedules'

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function isMonitorRow(s: ScheduleRead): boolean {
  // Primary signal is origin — monitors are chat/Slack-created; UI schedules
  // (clock composer, API) stay in the Schedules tab regardless of their
  // notify_policy or instruction value.
  if (s.origin === 'slack' || s.origin === 'notebook_chat') return true
  // Anything with a draft / auto_paused state only exists through the monitor
  // flow, so surface it on the monitor side too.
  if (s.status === 'draft' || s.status === 'auto_paused') return true
  return false
}

function humanizeSchedule(expr?: string | null): string {
  if (!expr) return '—'
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [m, h, dom, mo, dow] = parts
  if (dom !== '*' || mo !== '*') return expr
  const minute = Number(m)
  const hour = Number(h)
  if (!Number.isInteger(minute) || !Number.isInteger(hour)) return expr
  const time = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  if (dow === '*') return `Daily ${time}`
  const dayNums = new Set<number>()
  for (const token of dow.split(',')) {
    const t = token.trim()
    if (t.includes('-')) {
      const [lo, hi] = t.split('-').map(Number)
      if (Number.isInteger(lo) && Number.isInteger(hi)) {
        for (let i = lo; i <= hi; i++) dayNums.add(i)
      }
    } else {
      const d = Number(t)
      if (Number.isInteger(d)) dayNums.add(d)
    }
  }
  if (dayNums.size === 7) return `Daily ${time}`
  const weekdays = new Set([1, 2, 3, 4, 5])
  const sameAsWeekdays = dayNums.size === 5 && [...dayNums].every(d => weekdays.has(d))
  if (sameAsWeekdays) return `Weekdays ${time}`
  const weekend = new Set([0, 6])
  const sameAsWeekend = dayNums.size === 2 && [...dayNums].every(d => weekend.has(d))
  if (sameAsWeekend) return `Weekends ${time}`
  return `${[...dayNums].sort().map(d => DAY_LABELS[d]).join(', ')} ${time}`
}

function formatRelativeDate(dateStr?: string | null): string {
  if (!dateStr) return '—'
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

function statusChip(s: ScheduleRead): { label: string; tone: string } {
  const status = s.status || (s.is_enabled ? 'active' : 'paused')
  if (status === 'active') return { label: 'Active', tone: 'bg-green-900/40 text-green-300' }
  if (status === 'draft') return { label: 'Draft', tone: 'bg-gray-800 text-gray-300' }
  if (status === 'auto_paused') return { label: 'Auto-paused', tone: 'bg-red-900/40 text-red-300' }
  if (s.paused_until) {
    const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(s.paused_until) ? s.paused_until : `${s.paused_until}Z`
    const d = new Date(normalized)
    if (!Number.isNaN(d.getTime())) {
      const label = `Paused until ${d.toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
      })}`
      return { label, tone: 'bg-amber-900/40 text-amber-300' }
    }
  }
  return { label: 'Paused', tone: 'bg-amber-900/40 text-amber-300' }
}

function originPill(s: ScheduleRead): { label: string; tone: string; detail: string } {
  const origin = s.origin || 'ui'
  const notebookName = (s as ScheduleRead & { notebook_name?: string }).notebook_name
  if (origin === 'slack') {
    return {
      label: 'Slack',
      tone: 'bg-purple-900/40 text-purple-300 border-purple-700/50',
      detail: s.slack_thread_ts ? 'thread reply' : 'channel post',
    }
  }
  if (origin === 'notebook_chat') {
    return {
      label: 'Notebook',
      tone: 'bg-blue-900/40 text-blue-300 border-blue-700/50',
      detail: notebookName ? `${notebookName} · created in chat` : 'created in chat',
    }
  }
  return {
    label: 'App',
    tone: 'bg-gray-800 text-gray-300 border-gray-700',
    detail: notebookName ? notebookName : 'created in the app',
  }
}

function runDotTone(status: string | null | undefined, outcome?: string | null): string {
  if (status === 'failed' || status === 'timeout') return 'bg-red-500'
  if (outcome === 'alert') return 'bg-amber-500'
  if (status === 'success' || status === 'partial') return 'bg-green-500'
  return 'bg-gray-700'
}

function RunDots({ runs }: { runs: ScheduleRunRead[] | undefined }) {
  const slots = useMemo(() => {
    const latest = (runs || []).slice(0, 14)
    const items = latest.map(r => runDotTone(r.status, r.outcome))
    while (items.length < 7) items.push('bg-gray-700/50')
    return items.slice(0, 14)
  }, [runs])
  return (
    <div className="flex gap-[3px] items-center">
      {slots.map((cls, i) => (
        <span key={i} className={`inline-block w-[7px] h-[7px] rounded-sm ${cls}`} />
      ))}
    </div>
  )
}

function MonitorRow({
  schedule,
  selected,
  onSelect,
  onToggle,
  runs,
}: {
  schedule: ScheduleRead
  selected: boolean
  onSelect: () => void
  onToggle: () => void
  runs: ScheduleRunRead[] | undefined
}) {
  const chip = statusChip(schedule)
  const nextRun = formatRelativeDate(schedule.next_run_at)
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left p-3 rounded-xl border transition-colors mb-2 ${
        selected
          ? 'border-brand-orange shadow-[inset_0_0_0_1px_rgba(249,115,22,0.5)] bg-[#1f1f1f]'
          : 'border-gray-800 bg-[#1f1f1f] hover:bg-[#242424]'
      }`}
    >
      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 items-start">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-medium text-white truncate">{schedule.name}</div>
            <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${chip.tone}`}>
              {chip.label}
            </span>
          </div>
          {(() => {
            const pill = originPill(schedule)
            return (
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <span
                  className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${pill.tone}`}
                >
                  {pill.label}
                </span>
                <span className="text-xs text-gray-500 truncate">{pill.detail}</span>
              </div>
            )
          })()}
        </div>
        <div className="flex flex-col items-end gap-2">
          <button
            type="button"
            role="switch"
            aria-checked={schedule.status === 'active'}
            onClick={e => {
              e.stopPropagation()
              onToggle()
            }}
            className={`relative inline-flex w-9 h-5 rounded-full transition-colors ${
              schedule.status === 'active' ? 'bg-brand-orange' : 'bg-gray-700'
            }`}
          >
            <span
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                schedule.status === 'active' ? 'translate-x-4' : 'translate-x-0.5'
              }`}
            />
          </button>
          <div className="text-[11px] text-right text-gray-400 leading-tight">
            {humanizeSchedule(schedule.cron_expression)}
            <br />
            <span className="text-gray-500">next {nextRun}</span>
          </div>
        </div>
      </div>
      <div className="mt-2">
        <RunDots runs={runs} />
      </div>
    </button>
  )
}

function RunRow({ run }: { run: ScheduleRunRead }) {
  const tone = runDotTone(run.status, run.outcome)
  const label = (() => {
    if (run.status === 'failed' || run.status === 'timeout') return 'Failed'
    if (run.outcome === 'alert') return `Alert${run.findings ? ` · ${run.findings.length} finding${run.findings.length === 1 ? '' : 's'}` : ''}`
    if (run.outcome === 'no_change') return 'No change'
    if (run.outcome === 'ok') return 'OK'
    if (run.trigger === 'catchup') return 'OK · catch-up run'
    return (run.status || 'Run').toString()
  })()
  const duration = run.duration_ms ? `${Math.round(run.duration_ms / 100) / 10}s` : null
  const started = run.started_at
    ? (() => {
        const raw = run.started_at
        const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw : `${raw}Z`
        const d = new Date(normalized)
        if (Number.isNaN(d.getTime())) return raw
        return d.toLocaleString(undefined, {
          weekday: 'short',
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        })
      })()
    : ''
  const body = run.headline || run.error_message || ''
  return (
    <div className="grid grid-cols-[14px_minmax(0,1fr)] gap-2.5 py-2.5 border-b border-gray-800 last:border-b-0">
      <span className={`w-2.5 h-2.5 rounded-full mt-1.5 ${tone}`} />
      <div>
        <div className="flex justify-between gap-2 text-sm text-white">
          <span>{label}</span>
          <span className="font-mono text-[11px] text-gray-500 whitespace-nowrap">
            {started}
            {duration && ` · ${duration}`}
          </span>
        </div>
        {body && <div className="text-xs text-gray-400 mt-0.5">{body}</div>}
      </div>
    </div>
  )
}

export default function MonitorsPage() {
  const navigate = useNavigate()
  const { data: schedules = [], isLoading } = useSchedules()
  const update = useUpdateSchedule()
  const del = useDeleteSchedule()
  const test = useTestSchedule()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterKey>('all')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ScheduleRead | null>(null)

  const monitors = schedules.filter(isMonitorRow)
  const legacySchedules = schedules.filter(s => !isMonitorRow(s))

  const counts = useMemo(
    () => ({
      all: monitors.length,
      active: monitors.filter(s => (s.status || (s.is_enabled ? 'active' : 'paused')) === 'active').length,
      paused: monitors.filter(s => {
        const st = s.status || (s.is_enabled ? 'active' : 'paused')
        return st === 'paused'
      }).length,
      attention: monitors.filter(s => {
        const st = s.status || ''
        return st === 'auto_paused' || (s.consecutive_failures || 0) >= 1
      }).length,
      schedules: legacySchedules.length,
    }),
    [monitors, legacySchedules]
  )

  const visibleRows: ScheduleRead[] = useMemo(() => {
    if (filter === 'schedules') return legacySchedules
    if (filter === 'all') return monitors
    if (filter === 'active') {
      return monitors.filter(s => (s.status || (s.is_enabled ? 'active' : 'paused')) === 'active')
    }
    if (filter === 'paused') {
      return monitors.filter(s => (s.status || '') === 'paused')
    }
    return monitors.filter(s => {
      const st = s.status || ''
      return st === 'auto_paused' || (s.consecutive_failures || 0) >= 1
    })
  }, [filter, monitors, legacySchedules])

  const selected = selectedId ? schedules.find(s => s.id === selectedId) : visibleRows[0]
  const effectiveSelected = selected || visibleRows[0]
  const effectiveSelectedId = effectiveSelected?.id

  const { data: runs = [] } = useScheduleRuns(effectiveSelectedId, 25)

  const toggleStatus = (s: ScheduleRead) => {
    const next = (s.status || (s.is_enabled ? 'active' : 'paused')) === 'active' ? 'paused' : 'active'
    update.mutate({ scheduleId: s.id, data: { status: next, is_enabled: next === 'active' } })
  }

  const handleRunNow = () => {
    if (!effectiveSelectedId) return
    test.mutate(effectiveSelectedId)
  }

  const handleDelete = (id: string) => {
    const target = schedules.find(s => s.id === id)
    if (!target) return
    setDeleteTarget(target)
  }

  const confirmDelete = () => {
    if (!deleteTarget) return
    const id = deleteTarget.id
    del.mutate(id, {
      onSuccess: () => {
        if (selectedId === id) setSelectedId(null)
        setDeleteTarget(null)
        showToast.success('Monitor deleted')
      },
      onError: () => {
        setDeleteTarget(null)
      },
    })
  }

  const filterPills: { key: FilterKey; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: counts.all },
    { key: 'active', label: 'Active', count: counts.active },
    { key: 'paused', label: 'Paused', count: counts.paused },
    { key: 'attention', label: 'Needs attention', count: counts.attention },
    { key: 'schedules', label: 'Schedules', count: counts.schedules },
  ]

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full p-12">
        <Loader2 className="w-6 h-6 animate-spin text-brand-orange" />
      </div>
    )
  }

  return (
    <div className="h-full flex overflow-hidden">
      <div className="w-[570px] min-w-[480px] max-w-[720px] border-r border-gray-800 flex flex-col">
        <div className="p-5 pb-3">
          <h2 className="text-xl font-semibold text-white">Monitors</h2>
          <p className="text-sm text-gray-400 mt-1">Checks Byaan runs for you on a schedule.</p>
          <div className="flex gap-1.5 mt-4 flex-wrap">
            {filterPills.map(p => (
              <button
                key={p.key}
                type="button"
                onClick={() => {
                  setFilter(p.key)
                  setSelectedId(null)
                }}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                  filter === p.key
                    ? 'bg-white text-[#1a1a1a] border-white font-semibold'
                    : 'bg-transparent border-gray-700 text-gray-300 hover:text-white'
                }`}
              >
                {p.label} {p.count > 0 && <span className="opacity-75">{p.count}</span>}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-5 pb-5 custom-scrollbar">
          {visibleRows.length === 0 ? (
            <Card className="p-6 text-center bg-[#1f1f1f] border-gray-800 mt-3">
              <CircleOff className="w-6 h-6 text-gray-500 mx-auto mb-2" />
              <h3 className="text-sm font-medium text-white mb-1">Nothing here</h3>
              <p className="text-xs text-gray-400">
                {filter === 'schedules'
                  ? 'No legacy query schedules.'
                  : 'Create a monitor from a notebook or Slack thread.'}
              </p>
            </Card>
          ) : filter === 'schedules' ? (
            legacySchedules.map(s => (
              <ScheduleCard
                key={s.id}
                schedule={s}
                onEdit={() => navigate(`/notebook/${s.notebook_id}/preview`)}
                onDelete={() => handleDelete(s.id)}
                onToggle={(_, enabled) =>
                  update.mutate({ scheduleId: s.id, data: { is_enabled: enabled } })
                }
                onNavigateToNotebook={id => navigate(`/notebook/${id}/preview`)}
                showNotebookName
              />
            ))
          ) : (
            visibleRows.map(s => (
              <MonitorRow
                key={s.id}
                schedule={s}
                selected={effectiveSelectedId === s.id}
                onSelect={() => setSelectedId(s.id)}
                onToggle={() => toggleStatus(s)}
                runs={effectiveSelectedId === s.id ? runs : undefined}
              />
            ))
          )}
        </div>
      </div>

      <div className="flex-1 min-w-0 overflow-y-auto custom-scrollbar p-6">
        {!effectiveSelected ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-500">
            Pick a monitor on the left to see its details.
          </div>
        ) : filter === 'schedules' ? (
          <ScheduleDetail
            schedule={effectiveSelected}
            runs={runs}
            onRunNow={handleRunNow}
            onTogglePause={() => toggleStatus(effectiveSelected)}
            onDelete={() => handleDelete(effectiveSelected.id)}
            onOpenNotebook={() => navigate(`/notebook/${effectiveSelected.notebook_id}/preview`)}
            runNowPending={test.isPending}
          />
        ) : editingId === effectiveSelected.id ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-white">Edit monitor</h3>
              <Button type="button" variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                Done
              </Button>
            </div>
            <MonitorCard monitor={effectiveSelected} onChanged={() => undefined} />
          </div>
        ) : (
          <MonitorDetail
            schedule={effectiveSelected}
            runs={runs}
            onRunNow={handleRunNow}
            onTogglePause={() => toggleStatus(effectiveSelected)}
            onEdit={() => setEditingId(effectiveSelected.id)}
            onDelete={() => handleDelete(effectiveSelected.id)}
            onOpenNotebook={() =>
              navigate(`/notebook/${effectiveSelected.notebook_id}/preview`)
            }
            runNowPending={test.isPending}
          />
        )}
      </div>

      <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent className="bg-[#1a1a1a] border-gray-800 text-white">
          <DialogHeader>
            <DialogTitle>Delete monitor?</DialogTitle>
          </DialogHeader>
          <div className="text-sm text-gray-300 space-y-2">
            <p>
              This will permanently delete <b>{deleteTarget?.name}</b>, including its run history and any Slack / notebook delivery hooks.
            </p>
            <p className="text-gray-500 text-xs">This cannot be undone.</p>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setDeleteTarget(null)} disabled={del.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="default"
              className="bg-red-600 hover:bg-red-700 text-white"
              onClick={confirmDelete}
              disabled={del.isPending}
            >
              {del.isPending ? 'Deleting…' : 'Delete monitor'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function MonitorDetail({
  schedule,
  runs,
  onRunNow,
  onTogglePause,
  onEdit,
  onDelete,
  onOpenNotebook,
  runNowPending,
}: {
  schedule: ScheduleRead
  runs: ScheduleRunRead[]
  onRunNow: () => void
  onTogglePause: () => void
  onEdit: () => void
  onDelete: () => void
  onOpenNotebook: () => void
  runNowPending: boolean
}) {
  const chip = statusChip(schedule)
  const effectiveStatus = schedule.status || (schedule.is_enabled ? 'active' : 'paused')
  const isDraft = effectiveStatus === 'draft'
  const isActive = effectiveStatus === 'active'
  const running = useMonitorRunningState(schedule.id)
  const showRunning = running || runNowPending
  const stage = useRunningStage(showRunning ? schedule.id : null)
  const notifyLabel = (() => {
    const p = schedule.notify_policy || 'always'
    if (p === 'always') return 'posts every run'
    if (p === 'on_alert') return 'tells you on alert'
    if (p === 'on_change') return 'tells you on change'
    return p
  })()
  const subtitleParts: string[] = []
  subtitleParts.push(`${humanizeSchedule(schedule.cron_expression)} ${schedule.timezone || 'UTC'}`)
  subtitleParts.push(notifyLabel)

  const latest = runs[0]
  const outcomeIcon = (() => {
    if (!latest) return null
    if (latest.status === 'failed' || latest.status === 'timeout')
      return <XCircle className="w-4 h-4 text-red-400" />
    if (latest.outcome === 'alert') return <AlertTriangle className="w-4 h-4 text-amber-400" />
    return <CheckCircle2 className="w-4 h-4 text-green-400" />
  })()

  return (
    <div className="max-w-3xl space-y-5">
      <div>
        <div className="flex items-center gap-3 flex-wrap">
          <h3 className="text-xl font-semibold text-white">{schedule.name}</h3>
          <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${chip.tone}`}>
            {chip.label}
          </span>
          {(() => {
            const pill = originPill(schedule)
            return (
              <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${pill.tone}`}>
                {pill.label}
              </span>
            )
          })()}
        </div>
        <p className="text-sm text-gray-400 mt-1">{subtitleParts.join(' · ')}</p>
        <p className="text-xs text-gray-500 mt-0.5">{originPill(schedule).detail}</p>
      </div>

      <div className="flex gap-2 flex-wrap">
        {isDraft && (
          <Button type="button" size="sm" onClick={onTogglePause}>
            Turn on
          </Button>
        )}
        <Button type="button" size="sm" variant={isDraft ? 'ghost' : 'default'} onClick={onRunNow} disabled={showRunning}>
          {showRunning ? (
            <>
              <Loader2 className="w-4 h-4 mr-1 animate-spin" /> Running test…
            </>
          ) : (
            <>
              <Play className="w-4 h-4 mr-1" /> Run a test now
            </>
          )}
        </Button>
        {!isDraft && (
          <Button type="button" size="sm" variant="ghost" onClick={onTogglePause}>
            <Pause className="w-4 h-4 mr-1" /> {isActive ? 'Pause' : 'Resume'}
          </Button>
        )}
        <Button type="button" size="sm" variant="ghost" onClick={onEdit}>
          <Pencil className="w-4 h-4 mr-1" /> Edit
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onOpenNotebook}>
          Open notebook
        </Button>
        <Button type="button" size="sm" variant="ghost" className="text-red-400" onClick={onDelete}>
          <Trash2 className="w-4 h-4 mr-1" /> Delete
        </Button>
      </div>

      {showRunning && (
        <Card className="p-3 bg-amber-900/20 border-amber-700/40 text-sm text-amber-100 flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin shrink-0" />
          <span className="flex-1">
            Running a test for <b>{schedule.name}</b>
            {stage ? <> — {stage}</> : null}
            . Result will post in the notebook shortly.
          </span>
          <button
            type="button"
            onClick={() => clearMonitorRunning(schedule.id)}
            className="text-amber-200/70 hover:text-amber-100 text-xs px-2 py-1"
            title="Dismiss"
          >
            Dismiss
          </button>
        </Card>
      )}

      <section>
        <h4 className="text-[10px] uppercase tracking-widest text-gray-500 mb-2">Brief</h4>
        <Card className="p-4 bg-[#1f1f1f] border-gray-800 text-sm text-gray-200 whitespace-pre-wrap">
          {schedule.instruction || <span className="text-gray-500">No instruction set.</span>}
        </Card>
      </section>

      <section>
        <h4 className="text-[10px] uppercase tracking-widest text-gray-500 mb-2 flex items-center gap-2">
          Run history
          {outcomeIcon}
        </h4>
        <Card className="p-4 bg-[#1f1f1f] border-gray-800">
          {runs.length === 0 ? (
            <div className="text-sm text-gray-500">No runs yet.</div>
          ) : (
            <div className="divide-y divide-gray-800">
              {runs.map(r => (
                <RunRow key={r.id} run={r} />
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  )
}

function ScheduleDetail({
  schedule,
  runs,
  onRunNow,
  onTogglePause,
  onDelete,
  onOpenNotebook,
  runNowPending,
}: {
  schedule: ScheduleRead
  runs: ScheduleRunRead[]
  onRunNow: () => void
  onTogglePause: () => void
  onDelete: () => void
  onOpenNotebook: () => void
  runNowPending: boolean
}) {
  const chip = statusChip(schedule)
  const isActive = (schedule.status || (schedule.is_enabled ? 'active' : 'paused')) === 'active'
  const subtitle = `${humanizeSchedule(schedule.cron_expression)} ${schedule.timezone || 'UTC'} · schedule`
  const latest = runs[0]
  const outcomeIcon = (() => {
    if (!latest) return null
    if (latest.status === 'failed' || latest.status === 'timeout')
      return <XCircle className="w-4 h-4 text-red-400" />
    return <CheckCircle2 className="w-4 h-4 text-green-400" />
  })()

  return (
    <div className="max-w-3xl space-y-5">
      <div>
        <div className="flex items-center gap-3 flex-wrap">
          <h3 className="text-xl font-semibold text-white">{schedule.name}</h3>
          <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${chip.tone}`}>
            {chip.label}
          </span>
          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border bg-gray-800 text-gray-300 border-gray-700">
            App
          </span>
        </div>
        <p className="text-sm text-gray-400 mt-1">{subtitle}</p>
        {schedule.next_run_at && (
          <p className="text-xs text-gray-500 mt-0.5">Next run · {formatRelativeDate(schedule.next_run_at)}</p>
        )}
      </div>

      <div className="flex gap-2 flex-wrap">
        <Button type="button" size="sm" onClick={onRunNow} disabled={runNowPending}>
          <Play className="w-4 h-4 mr-1" /> Run a test now
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onTogglePause}>
          <Pause className="w-4 h-4 mr-1" /> {isActive ? 'Pause' : 'Resume'}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onOpenNotebook}>
          Open notebook to edit
        </Button>
        <Button type="button" size="sm" variant="ghost" className="text-red-400" onClick={onDelete}>
          <Trash2 className="w-4 h-4 mr-1" /> Delete
        </Button>
      </div>

      <section>
        <h4 className="text-[10px] uppercase tracking-widest text-gray-500 mb-2">What it does</h4>
        <Card className="p-4 bg-[#1f1f1f] border-gray-800 text-sm text-gray-200 whitespace-pre-wrap">
          {schedule.instruction ||
            "This schedule runs the notebook's saved queries on a cron. Edit it from the notebook."}
        </Card>
      </section>

      <section>
        <h4 className="text-[10px] uppercase tracking-widest text-gray-500 mb-2 flex items-center gap-2">
          Run history
          {outcomeIcon}
        </h4>
        <Card className="p-4 bg-[#1f1f1f] border-gray-800">
          {runs.length === 0 ? (
            <div className="text-sm text-gray-500">No runs yet.</div>
          ) : (
            <div className="divide-y divide-gray-800">
              {runs.map(r => (
                <RunRow key={r.id} run={r} />
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  )
}
