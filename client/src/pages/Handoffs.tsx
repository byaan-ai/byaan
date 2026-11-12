import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronRight,
  CircleOff,
  Download,
  ExternalLink,
  GitPullRequest,
  Hand,
  Loader2,
  Pencil,
  Play,
  RotateCcw,
  Send,
  Settings,
  Square,
} from 'lucide-react'
import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { HandoffBriefDialog } from '../components/handoffs/HandoffBriefDialog'
import { HandoffTeamSettingsDialog } from '../components/handoffs/HandoffTeamSettingsDialog'
import { handoffKeys, refreshHandoff, useHandoff, useHandoffAction, useHandoffMessage } from '../hooks/useHandoffs'
import {
  ACTIVE_HANDOFF_STATUSES,
  HANDOFF_STATUS_LABELS,
  HandoffsApi,
  canMessage,
  canRestart,
  canStop,
  canTakeOver,
  handoffTone,
  isActiveHandoff,
  parseHandoffTime,
  type Handoff,
  type HandoffEvent,
  type HandoffStatus,
} from '../services/handoffs'
import { showToast } from '../utils/toast'

type FilterKey = 'all' | 'active' | 'proposed' | 'done' | 'attention' | 'stopped'

const ATTENTION: HandoffStatus[] = ['failed', 'ci_failed']
const STOPPED: HandoffStatus[] = ['cancelled', 'dismissed', 'taken_over']

const FILTERS: { key: FilterKey; label: string; match: (s: HandoffStatus) => boolean }[] = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'active', label: 'In flight', match: (s) => ACTIVE_HANDOFF_STATUSES.includes(s) },
  { key: 'proposed', label: 'Drafts', match: (s) => s === 'proposed' },
  { key: 'done', label: 'Done', match: (s) => s === 'done' },
  { key: 'attention', label: 'Needs attention', match: (s) => ATTENTION.includes(s) },
  { key: 'stopped', label: 'Stopped', match: (s) => STOPPED.includes(s) },
]

const SOURCE_LABELS: Record<string, string> = {
  executor: 'Byaan',
  agent: 'Claude',
  github: 'GitHub',
  human: 'Person',
  system: 'Byaan',
}

function formatWhen(value?: string | null): string {
  if (!value) return '—'
  const d = parseHandoffTime(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function originPill(h: Handoff): { label: string; tone: string } {
  if (h.schedule_id) return { label: 'Monitor', tone: 'bg-amber-900/40 text-amber-300 border-amber-700/50' }
  if (h.origin === 'slack') return { label: 'Slack', tone: 'bg-purple-900/40 text-purple-300 border-purple-700/50' }
  return { label: 'Notebook', tone: 'bg-blue-900/40 text-blue-300 border-blue-700/50' }
}

function eventTone(event: HandoffEvent): string {
  const status = event.data?.status as HandoffStatus | undefined
  const state = event.data?.state as string | undefined
  if (status === 'done' || state === 'success') return 'bg-green-500'
  if ((status && (ATTENTION.includes(status) || status === 'fixing')) || state === 'failure') return 'bg-red-500'
  if (event.source === 'human') return 'bg-amber-500'
  if (event.kind === 'result') return 'bg-blue-500'
  return 'bg-gray-600'
}

function Chips({ handoff }: { handoff: Handoff }) {
  const pill = originPill(handoff)
  return (
    <>
      <span
        className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${handoffTone(handoff.status).chip}`}
      >
        {isActiveHandoff(handoff.status) && <Loader2 className="w-3 h-3 animate-spin" />}
        {HANDOFF_STATUS_LABELS[handoff.status]}
      </span>
      <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${pill.tone}`}>{pill.label}</span>
    </>
  )
}

function HandoffRow({ handoff, selected, onSelect }: { handoff: Handoff; selected: boolean; onSelect: () => void }) {
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
          <div className="text-sm font-medium text-white truncate">{handoff.title}</div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <Chips handoff={handoff} />
            <span className="text-xs text-gray-500 truncate">{handoff.repo_full_name || 'no repository'}</span>
          </div>
        </div>
        <div className="text-[11px] text-right text-gray-400 leading-tight">
          {handoff.pr_number ? `PR #${handoff.pr_number}` : '—'}
          <br />
          <span className="text-gray-500">{formatWhen(handoff.updated_at)}</span>
        </div>
      </div>
    </button>
  )
}

const COLLAPSED_BRIEF_PX = 140

function CollapsibleBrief({ children }: { children: React.ReactNode }) {
  const contentRef = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [height, setHeight] = useState(0)

  useLayoutEffect(() => {
    const content = contentRef.current
    if (!content) return
    const measure = () => setHeight(content.scrollHeight)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(content)
    return () => observer.disconnect()
  }, [])

  const overflows = height > COLLAPSED_BRIEF_PX + 8
  const collapsed = overflows && !expanded
  return (
    <div>
      <div
        className="relative overflow-hidden transition-[max-height] duration-300 ease-out"
        style={{ maxHeight: collapsed ? COLLAPSED_BRIEF_PX : height + 16 }}
      >
        <div ref={contentRef}>{children}</div>
        {collapsed && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-[#1f1f1f] to-transparent" />
        )}
      </div>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          className="mt-2 flex items-center gap-1 text-xs text-gray-400 hover:text-white transition-colors"
        >
          <ChevronRight className={`w-3.5 h-3.5 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`} />
          {expanded ? 'Show less' : 'Show full brief'}
        </button>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="text-[10px] uppercase tracking-widest text-gray-500 mb-2">{title}</h4>
      <Card className="p-4 bg-[#1f1f1f] border-gray-800 text-sm text-gray-200">{children}</Card>
    </section>
  )
}

function HandoffDetailPanel({ handoffId }: { handoffId: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: handoff } = useHandoff(handoffId)
  const action = useHandoffAction()
  const message = useHandoffMessage()
  const [editing, setEditing] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState('')
  const [savingTitle, setSavingTitle] = useState(false)
  const [changes, setChanges] = useState('')

  if (!handoff) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-5 h-5 animate-spin text-brand-orange" />
      </div>
    )
  }

  const status = handoff.status
  const revised = !!handoff.pending_revision
  const brief = (revised && handoff.pending_revision_brief) || handoff.brief
  const shownTitle = (revised && handoff.pending_revision_title) || handoff.title
  const busy = action.isPending || message.isPending
  const question =
    status === 'waiting_on_human' ? [...handoff.events].reverse().find((e) => e.kind === 'question') : undefined
  const options = (question?.data?.options as string[] | undefined) ?? []
  const events = handoff.events.filter((e) => e.kind !== 'tool' && e.kind !== 'notify')

  const run = (kind: 'start' | 'dismiss' | 'cancel' | 'takeOver' | 'restart') =>
    action.mutate(
      { id: handoff.id, action: kind },
      { onError: (e) => showToast.error(e instanceof Error ? e.message : 'Action failed') },
    )

  const rename = async () => {
    if (savingTitle) return
    setSavingTitle(true)
    try {
      refreshHandoff(queryClient, await HandoffsApi.rename(handoff.id, title.trim()))
      setRenaming(false)
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Could not rename')
    } finally {
      setSavingTitle(false)
    }
  }

  const send = (text: string) => {
    if (!text.trim() || message.isPending) return
    message.mutate(
      { id: handoff.id, text: text.trim() },
      {
        onSuccess: () => {
          setChanges('')
          showToast.success('Sent to Claude')
        },
        onError: (e) => showToast.error(e instanceof Error ? e.message : 'Could not send'),
      },
    )
  }

  const subtitle = [
    handoff.repo_full_name ? `${handoff.repo_full_name} from ${handoff.base_branch}` : 'no repository',
    handoff.branch,
    handoff.untested && handoff.pr_url ? 'UNTESTED' : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="max-w-3xl space-y-5">
      <div>
        {renaming ? (
          <div className="flex items-center gap-2">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus />
            <Button size="sm" onClick={rename} disabled={!title.trim() || savingTitle}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRenaming(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-3 flex-wrap">
            <h3 className="text-xl font-semibold text-white">{shownTitle}</h3>
            <Chips handoff={handoff} />
          </div>
        )}
        <p className="text-sm text-gray-400 mt-1 break-all">{subtitle}</p>
        <p className="text-xs text-gray-500 mt-0.5">
          Created {formatWhen(handoff.created_at)}
          {handoff.finished_at ? ` · finished ${formatWhen(handoff.finished_at)}` : ''}
        </p>
      </div>

      <div className="flex gap-2 flex-wrap">
        {status === 'proposed' && (
          <>
            <Button size="sm" onClick={() => run('start')} disabled={busy}>
              <Play className="w-4 h-4 mr-1" /> Start handoff
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil className="w-4 h-4 mr-1" /> View &amp; edit brief
            </Button>
            <Button size="sm" variant="ghost" onClick={() => run('dismiss')} disabled={busy}>
              Not now
            </Button>
          </>
        )}
        {canRestart(handoff) && (
          <Button size="sm" onClick={() => run('restart')} disabled={busy}>
            <RotateCcw className="w-4 h-4 mr-1" /> Re-start
          </Button>
        )}
        {handoff.pr_url && (
          <Button size="sm" variant="ghost" asChild>
            <a href={handoff.pr_url} target="_blank" rel="noreferrer">
              <GitPullRequest className="w-4 h-4 mr-1" /> Open PR #{handoff.pr_number}
            </a>
          </Button>
        )}
        {canTakeOver(handoff) && (
          <Button size="sm" variant="ghost" onClick={() => run('takeOver')} disabled={busy}>
            <Hand className="w-4 h-4 mr-1" /> Take over
          </Button>
        )}
        {canStop(handoff) && (
          <Button size="sm" variant="ghost" className="text-red-400" onClick={() => run('cancel')} disabled={busy}>
            <Square className="w-4 h-4 mr-1" /> Stop
          </Button>
        )}
        {handoff.notebook_id && (
          <Button size="sm" variant="ghost" onClick={() => navigate(`/notebook/${handoff.notebook_id}/preview`)}>
            <ExternalLink className="w-4 h-4 mr-1" /> Open notebook
          </Button>
        )}
        {!renaming && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setTitle(handoff.title)
              setRenaming(true)
            }}
          >
            <Pencil className="w-4 h-4 mr-1" /> Rename
          </Button>
        )}
        {handoff.has_transcript && (
          <Button
            size="sm"
            variant="ghost"
            title="Download Claude Code's full session log (.jsonl): every file it read and edited, the commands it ran and its reasoning. Kept for 72 hours after the handoff ends."
            onClick={() =>
              HandoffsApi.download(`/handoffs/${handoff.id}/transcript`, `handoff-${handoff.id}.jsonl`).catch((e) =>
                showToast.error(e instanceof Error ? e.message : 'Download failed'),
              )
            }
          >
            <Download className="w-4 h-4 mr-1" /> Claude session log
          </Button>
        )}
      </div>

      {handoff.error && !isActiveHandoff(status) && (
        <Card className="p-3 bg-red-900/20 border-red-800/40 text-sm text-red-200">{handoff.error}</Card>
      )}

      <Section title={revised ? 'Brief · revision ready, Re-start applies it' : 'Brief'}>
        <CollapsibleBrief>
          <div className="space-y-3">
            <p className="whitespace-pre-wrap">{brief.problem}</p>
            {brief.suspected_files.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-xs text-gray-500">Scope</span>
                {brief.suspected_files.map((file) => (
                  <code key={file} className="text-xs bg-gray-800 text-gray-200 px-1.5 py-0.5 rounded">
                    {file}
                  </code>
                ))}
              </div>
            )}
            {brief.acceptance_check && (
              <div>
                <div className="text-xs text-gray-500">Done when</div>
                <p className="whitespace-pre-wrap">{brief.acceptance_check}</p>
              </div>
            )}
          </div>
        </CollapsibleBrief>
      </Section>

      {handoff.steps.length > 0 && (
        <Section title="What Claude did">
          <ul className="list-disc pl-5 space-y-1">
            {handoff.steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ul>
        </Section>
      )}

      {question && (
        <Section title="Claude has a question">
          <div className="space-y-3">
            <p className="whitespace-pre-wrap">{question.message}</p>
            {options.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {options.map((option) => (
                  <Button key={option} size="sm" variant="ghost" disabled={busy} onClick={() => send(option)}>
                    {option}
                  </Button>
                ))}
              </div>
            )}
          </div>
        </Section>
      )}

      {canMessage(handoff) && (
        <Section title={question ? 'Answer Claude' : 'Ask for changes'}>
          <div className="flex gap-2">
            <Input
              value={changes}
              onChange={(e) => setChanges(e.target.value)}
              placeholder={question ? 'Your answer…' : 'What should Claude change?'}
              onKeyDown={(e) => e.key === 'Enter' && send(changes)}
            />
            <Button size="sm" onClick={() => send(changes)} disabled={!changes.trim() || busy}>
              <Send className="w-4 h-4 mr-1" /> Send
            </Button>
          </div>
        </Section>
      )}

      <Section title="Activity">
        {events.length === 0 ? (
          <div className="text-gray-500">No activity yet.</div>
        ) : (
          <div className="divide-y divide-gray-800">
            {[...events].reverse().map((event) => (
              <div key={event.seq} className="grid grid-cols-[14px_minmax(0,1fr)] gap-2.5 py-2.5">
                <span className={`w-2.5 h-2.5 rounded-full mt-1.5 ${eventTone(event)}`} />
                <div>
                  <div className="flex justify-between gap-2">
                    <span className="text-white whitespace-pre-wrap">
                      {event.message ||
                        (event.kind === 'status' && event.data?.status
                          ? HANDOFF_STATUS_LABELS[event.data.status as HandoffStatus]
                          : event.kind)}
                    </span>
                    <span className="font-mono text-[11px] text-gray-500 whitespace-nowrap">
                      {SOURCE_LABELS[event.source] ?? event.source} · {formatWhen(event.created_at)}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {editing && status === 'proposed' && (
        <HandoffBriefDialog
          handoff={handoff}
          open={editing}
          onOpenChange={setEditing}
          onSaved={(detail) => refreshHandoff(queryClient, detail)}
        />
      )}
    </div>
  )
}

export default function HandoffsPage() {
  const [filter, setFilter] = useState<FilterKey>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const { data: handoffs = [], isLoading } = useQuery({
    queryKey: handoffKeys.page,
    queryFn: () => HandoffsApi.list(200),
    refetchInterval: 10000,
    staleTime: 0,
  })

  const counts = useMemo(
    () => Object.fromEntries(FILTERS.map((f) => [f.key, handoffs.filter((h) => f.match(h.status)).length])),
    [handoffs],
  )
  const match = FILTERS.find((f) => f.key === filter)!.match
  const visible = handoffs.filter((h) => match(h.status))
  const selected = (selectedId && handoffs.find((h) => h.id === selectedId)) || visible[0]

  const exportCsv = () =>
    HandoffsApi.download('/handoffs/export', 'byaan-handoffs.csv').catch((e) =>
      showToast.error(e instanceof Error ? e.message : 'Export failed'),
    )

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full p-12">
        <Loader2 className="w-6 h-6 animate-spin text-brand-orange" />
      </div>
    )
  }

  return (
    <div className="h-full flex overflow-hidden">
      <HandoffTeamSettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <div className="w-[570px] min-w-[480px] max-w-[720px] border-r border-gray-800 flex flex-col">
        <div className="p-5 pb-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-white">Handoffs</h2>
              <p className="text-sm text-gray-400 mt-1">Code fixes handed to Claude Code from Slack, notebooks and Monitors.</p>
            </div>
            <div className="flex shrink-0 gap-1">
              <Button size="sm" variant="ghost" onClick={() => setSettingsOpen(true)}>
                <Settings className="w-4 h-4 mr-1" /> Settings
              </Button>
              <Button size="sm" variant="ghost" onClick={exportCsv}>
                <Download className="w-4 h-4 mr-1" /> Export CSV
              </Button>
            </div>
          </div>
          <div className="flex gap-1.5 mt-4 flex-wrap">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => {
                  setFilter(f.key)
                  setSelectedId(null)
                }}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                  filter === f.key
                    ? 'bg-white text-[#1a1a1a] border-white font-semibold'
                    : 'bg-transparent border-gray-700 text-gray-300 hover:text-white'
                }`}
              >
                {f.label} {counts[f.key] > 0 && <span className="opacity-75">{counts[f.key]}</span>}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-5 pb-5 custom-scrollbar">
          {visible.length === 0 ? (
            <Card className="p-6 text-center bg-[#1f1f1f] border-gray-800 mt-3">
              <CircleOff className="w-6 h-6 text-gray-500 mx-auto mb-2" />
              <h3 className="text-sm font-medium text-white mb-1">Nothing here</h3>
              <p className="text-xs text-gray-400">Ask Byaan in Slack or a notebook to hand a code fix to Claude Code.</p>
            </Card>
          ) : (
            visible.map((h) => (
              <HandoffRow key={h.id} handoff={h} selected={selected?.id === h.id} onSelect={() => setSelectedId(h.id)} />
            ))
          )}
        </div>
      </div>

      <div className="flex-1 min-w-0 overflow-y-auto custom-scrollbar p-6">
        {selected ? (
          <HandoffDetailPanel key={selected.id} handoffId={selected.id} />
        ) : (
          <div className="h-full flex items-center justify-center text-sm text-gray-500">
            Pick a handoff on the left to see its details.
          </div>
        )}
      </div>
    </div>
  )
}
