import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleStop, GitPullRequest, Loader2, UserRound, XCircle } from 'lucide-react'
import { Button } from '../ui/button'
import { useHandoff, useHandoffAction, useHandoffMessagesRefresh } from '../../hooks/useHandoffs'
import {
  HANDOFF_STATUS_LABELS,
  isActiveHandoff,
  parseHandoffTime,
  type HandoffEvent,
  type HandoffStatus,
} from '../../services/handoffs'
import { showToast } from '../../utils/toast'

const RUN_START_STATUSES = ['queued', 'fixing']

const PHASE_LABELS: Partial<Record<HandoffStatus, string>> = {
  queued: 'Waiting for a free slot',
  preparing: 'Cloning the repository',
  running: 'Claude Code is working',
  waiting_on_human: 'Waiting for your answer',
  pushing: 'Checking changes and pushing',
  ci_running: 'PR open · waiting for CI',
  fixing: 'Claude Code is fixing CI failures',
}

function formatDuration(ms: number) {
  const seconds = Math.max(0, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${seconds % 60}s`
}

const TOOL_VERBS: Record<string, string> = {
  Read: 'Reading',
  Edit: 'Editing',
  MultiEdit: 'Editing',
  Write: 'Writing',
  Glob: 'Finding files',
  Grep: 'Searching for',
  LS: 'Listing',
  Bash: 'Running',
}

function describeTool(message: string) {
  const [tool, ...rest] = message.split(' ')
  const target = rest.join(' ').split('\n')[0].trim()
  if (tool.startsWith('mcp__byaan_handoff__')) return tool === 'mcp__byaan_handoff__submit_result' ? 'Submitting the result' : 'Reporting progress'
  const verb = TOOL_VERBS[tool] ?? tool
  const short = target.length > 80 ? `${target.slice(0, 80)}…` : target
  return short ? `${verb} ${short}` : verb
}

function statusOf(event: HandoffEvent): HandoffStatus | undefined {
  return event.kind === 'status' ? (event.data?.status as HandoffStatus | undefined) : undefined
}

function OutcomeIcon({ status }: { status: HandoffStatus }) {
  if (status === 'done' || status === 'ci_running') return <CheckCircle2 className="w-4 h-4 text-green-400" />
  if (status === 'cancelled' || status === 'dismissed') return <CircleStop className="w-4 h-4 text-gray-400" />
  if (status === 'taken_over') return <UserRound className="w-4 h-4 text-blue-300" />
  return <XCircle className="w-4 h-4 text-red-400" />
}

export function ChatHandoffRunCard({ handoffId, startSeq }: { handoffId: string; startSeq: number }) {
  const { data: handoff, error } = useHandoff(handoffId)
  const action = useHandoffAction()
  const [now, setNow] = useState(() => Date.now())
  useHandoffMessagesRefresh(handoff)

  const run = useMemo(() => {
    if (!handoff) return null
    const events = handoff.events
    const start = events.find((e) => e.seq === startSeq)
    const next = events.find((e) => e.seq > startSeq && RUN_START_STATUSES.includes(statusOf(e) ?? ''))
    const inRun = events.filter((e) => e.seq >= startSeq && (!next || e.seq < next.seq))
    const statuses = inRun.map(statusOf).filter(Boolean) as HandoffStatus[]
    const isLatest = !next
    const status = isLatest ? handoff.status : statuses[statuses.length - 1] ?? handoff.status
    const lastTool = [...inRun].reverse().find((e) => e.kind === 'tool')
    const lastProgress = [...inRun].reverse().find((e) => e.kind === 'progress')
    const end = isLatest && isActiveHandoff(handoff.status) ? null : inRun[inRun.length - 1]
    const openedPr = inRun.some((e) => statusOf(e) === 'ci_running' || (statusOf(e) === 'done' && e.data?.pr_opened))
    return {
      trigger: start?.message || 'Run started',
      startedAt: start ? parseHandoffTime(start.created_at).getTime() : null,
      endedAt: end ? parseHandoffTime(end.created_at).getTime() : null,
      status,
      active: isLatest && isActiveHandoff(handoff.status),
      tools: inRun.filter((e) => e.kind === 'tool').length,
      now: lastTool ? describeTool(lastTool.message) : undefined,
      progress: lastProgress?.message,
      openedPr,
      ciFailedAfter: !!next && statusOf(next) === 'fixing',
    }
  }, [handoff, startSeq])

  useEffect(() => {
    if (!run?.active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [run?.active])

  if (error) {
    return <div className="my-3 text-xs text-gray-500">This run is no longer available.</div>
  }
  if (!handoff || !run) {
    return (
      <div className="my-3 flex items-center gap-2 text-xs text-gray-500">
        <Loader2 className="w-3 h-3 animate-spin" /> Loading run…
      </div>
    )
  }

  const elapsed = run.startedAt ? formatDuration((run.endedAt ?? now) - run.startedAt) : null
  const stoppable = run.active && handoff.status !== 'pushing'

  return (
    <div className="my-4">
      <div className="flex items-center gap-3 text-[11px] uppercase tracking-wide text-gray-500">
        <div className="h-px flex-1 bg-gray-800" />
        <span className="normal-case">
          {run.trigger}
          {run.startedAt && ` · ${new Date(run.startedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}
        </span>
        <div className="h-px flex-1 bg-gray-800" />
      </div>

      <div
        className={`mt-2 rounded-lg border px-3 py-2 text-sm ${
          run.active ? 'border-brand-orange/40 bg-brand-orange/5' : 'border-gray-800 bg-[#1e1e1e]'
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {run.active ? (
              <Loader2 className="w-4 h-4 animate-spin text-brand-orange shrink-0" />
            ) : run.ciFailedAfter ? (
              <AlertTriangle className="w-4 h-4 text-yellow-400 shrink-0" />
            ) : (
              <OutcomeIcon status={run.status} />
            )}
            <span className="text-gray-200 truncate">
              {run.active
                ? PHASE_LABELS[run.status] ?? HANDOFF_STATUS_LABELS[run.status]
                : run.ciFailedAfter
                  ? 'Pushed · CI failed'
                  : HANDOFF_STATUS_LABELS[run.status]}
            </span>
            <span className="text-xs text-gray-500 shrink-0 truncate">{handoff.title}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0 text-xs text-gray-500">
            {elapsed && <span>{elapsed}</span>}
            {stoppable && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-red-400"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate(
                    { id: handoff.id, action: 'cancel' },
                    { onError: (e) => showToast.error(e instanceof Error ? e.message : 'Could not stop') },
                  )
                }
              >
                Stop
              </Button>
            )}
          </div>
        </div>

        {run.active && (run.now || run.progress) && (
          <div className="mt-1 space-y-0.5 text-xs text-gray-400">
            {run.progress && <div>Latest: {run.progress}</div>}
            {run.now && (handoff.status === 'running' || handoff.status === 'fixing') && (
              <div className="truncate" title={run.now}>
                Now: {run.now}
              </div>
            )}
          </div>
        )}

        {!run.active && (
          <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-gray-500">
            {run.tools > 0 && <span>{run.tools} steps</span>}
            {run.openedPr && handoff.pr_url && (
              <a className="inline-flex items-center gap-1 text-brand-orange" href={handoff.pr_url} target="_blank" rel="noreferrer">
                <GitPullRequest className="w-3 h-3" /> PR #{handoff.pr_number}
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
