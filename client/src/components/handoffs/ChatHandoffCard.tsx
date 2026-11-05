import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, ExternalLink, GitPullRequest, Loader2, Send } from 'lucide-react'
import { Card } from '../ui/card'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { HandoffBriefDialog } from './HandoffBriefDialog'
import {
  handoffKeys,
  refreshHandoff,
  useHandoff,
  useHandoffAction,
  useHandoffMessage,
  useHandoffMessagesRefresh,
} from '../../hooks/useHandoffs'
import {
  HANDOFF_STATUS_LABELS,
  HandoffsApi,
  canMessage,
  canRestart,
  canStop,
  canTakeOver,
  handoffTone,
  isActiveHandoff,
  type HandoffEvent,
  type HandoffStatus,
} from '../../services/handoffs'
import { showToast } from '../../utils/toast'

const latest = (events: HandoffEvent[], kind: string) => [...events].reverse().find((e) => e.kind === kind)

const errorText = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback)

function taskSummary(problem: string, fallback: string, limit = 200): string {
  const text = problem.split(/\s+/).join(' ').trim()
  const sentence = text.split(/(?<=[.!?])\s+(?=[A-Z])/)[0] ?? ''
  if (!sentence) return fallback
  if (sentence.length <= limit) return sentence
  return `${sentence.slice(0, limit).replace(/\s+\S*$/, '').replace(/[,;:]$/, '')}…`
}

function withInlineCode(text: string) {
  return text.split('`').map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part))
}

function StatusBadge({ status }: { status: HandoffStatus }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 text-xs px-2 py-0.5 rounded-full ${handoffTone(status).chip}`}
    >
      {isActiveHandoff(status) && <Loader2 className="w-3 h-3 animate-spin" />}
      {HANDOFF_STATUS_LABELS[status]}
    </span>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[52px_minmax(0,1fr)] gap-2 items-baseline">
      <span className="text-xs text-gray-500">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

function BriefSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-1">{title}</div>
      {children}
    </div>
  )
}

export function ChatHandoffCard({ handoffId }: { handoffId: string }) {
  const { data: handoff, isLoading, error } = useHandoff(handoffId)
  const action = useHandoffAction()
  const sendMessage = useHandoffMessage()
  const queryClient = useQueryClient()
  const sendReview = useMutation({
    mutationFn: () => HandoffsApi.sendReviewFeedback(handoffId),
    onSuccess: () => {
      showToast.success('Sent the review comments to Claude')
      queryClient.invalidateQueries({ queryKey: handoffKeys.detail(handoffId) })
    },
    onError: (e) => showToast.error(errorText(e, 'Could not send')),
  })
  const [editing, setEditing] = useState(false)
  const [reply, setReply] = useState('')
  const [showBrief, setShowBrief] = useState(false)

  useHandoffMessagesRefresh(handoff)

  if (isLoading) {
    return (
      <Card className="my-2 p-3 bg-[#232323] border-gray-800 text-sm text-gray-400 flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading handoff…
      </Card>
    )
  }
  if (error || !handoff) {
    return <Card className="my-2 p-3 bg-[#232323] border-gray-800 text-sm text-gray-400">Handoff no longer available.</Card>
  }

  const run = (kind: 'start' | 'dismiss' | 'cancel' | 'takeOver' | 'restart') =>
    action.mutate({ id: handoff.id, action: kind }, { onError: (e) => showToast.error(errorText(e, 'Action failed')) })

  const send = (text: string, onSuccess?: () => void) => {
    if (!text.trim() || sendMessage.isPending) return
    sendMessage.mutate(
      { id: handoff.id, text: text.trim() },
      { onSuccess, onError: (e) => showToast.error(errorText(e, 'Could not send')) },
    )
  }

  const progress = latest(handoff.events, 'progress')
  const question = handoff.status === 'waiting_on_human' ? latest(handoff.events, 'question') : undefined
  const options = (question?.data?.options as string[] | undefined) ?? []
  const review = latest(handoff.events, 'review')
  const busy = action.isPending
  const revised = !!handoff.pending_revision
  const shownTitle = (revised && handoff.pending_revision_title) || handoff.title
  const shownBrief = (revised && handoff.pending_revision_brief) || handoff.brief
  const shownProblem = revised && !handoff.pending_revision_brief ? (handoff.pending_revision ?? '') : shownBrief.problem
  const label = revised
    ? `Revision · Re-start applies it${handoff.pr_number ? ` to PR #${handoff.pr_number}` : ''}`
    : handoff.status === 'proposed'
      ? 'Handoff draft'
      : 'Handoff to Claude Code'
  const files = shownBrief.suspected_files ?? []
  const evidence = shownBrief.evidence ?? []
  const hasDetails =
    !!shownProblem || evidence.length > 0 || !!shownBrief.acceptance_check || handoff.steps.length > 0 || handoff.activity.length > 0

  return (
    <Card
      className={`my-3 overflow-hidden border border-gray-800 border-l-4 ${handoffTone(handoff.status, revised).accent} bg-[#232323] text-sm text-gray-200`}
    >
      <div className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-widest text-gray-500">{label}</div>
            <div className="mt-0.5 font-medium text-white truncate" title={shownTitle}>
              {shownTitle}
            </div>
          </div>
          <StatusBadge status={handoff.status} />
        </div>

        <div className="space-y-1.5">
          <Row label="Repo">
            <span className="text-gray-300">
              <code className="text-xs">{handoff.repo_full_name}</code> <span className="text-gray-500">from</span>{' '}
              <code className="text-xs">{handoff.base_branch}</code>
            </span>
          </Row>
          <Row label="Task">
            <span className="text-gray-300">{taskSummary(shownProblem, shownTitle)}</span>
          </Row>
          {files.length > 0 && (
            <Row label="Scope">
              <div className="flex flex-wrap gap-1">
                {files.map((f) => (
                  <code key={f} className="text-[11px] bg-black/30 text-gray-300 px-1.5 py-0.5 rounded">
                    {f}
                  </code>
                ))}
              </div>
            </Row>
          )}
        </div>

        {hasDetails && (
          <div>
            <button
              type="button"
              onClick={() => setShowBrief(!showBrief)}
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-white transition-colors"
              aria-expanded={showBrief}
            >
              <ChevronRight className={`w-3.5 h-3.5 transition-transform duration-200 ${showBrief ? 'rotate-90' : ''}`} />
              {showBrief ? 'Hide brief' : 'Show brief'}
            </button>
            <div
              className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out ${
                showBrief ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
              }`}
            >
              <div className="overflow-hidden">
                <div className="mt-2 space-y-3 rounded-lg bg-black/20 border border-gray-800 p-3 text-xs">
                  {shownProblem && (
                    <BriefSection title="Task">
                      <p className="text-gray-300 whitespace-pre-wrap leading-relaxed">{shownProblem}</p>
                    </BriefSection>
                  )}
                  {evidence.length > 0 && (
                    <BriefSection title="Evidence">
                      <ul className="list-disc pl-4 space-y-0.5 text-gray-300">
                        {evidence.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </BriefSection>
                  )}
                  {shownBrief.acceptance_check && (
                    <BriefSection title="Done when">
                      <p className="text-gray-300 whitespace-pre-wrap leading-relaxed">{shownBrief.acceptance_check}</p>
                    </BriefSection>
                  )}
                  {handoff.steps.length > 0 && (
                    <BriefSection title="What Claude did">
                      <ol className="list-decimal pl-4 space-y-0.5 text-gray-300">
                        {handoff.steps.map((step, i) => (
                          <li key={i}>{step}</li>
                        ))}
                      </ol>
                    </BriefSection>
                  )}
                  {handoff.activity.length > 0 && (
                    <BriefSection title="Recorded by Byaan">
                      <div className="space-y-0.5 text-gray-500">
                        {handoff.activity.map((line) => (
                          <div key={line}>{withInlineCode(line)}</div>
                        ))}
                      </div>
                    </BriefSection>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {(handoff.branch || handoff.pr_url || (progress && isActiveHandoff(handoff.status))) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 border-t border-gray-800 pt-2.5">
            {handoff.branch && (
              <span className="truncate max-w-full">
                branch <code className="text-gray-400">{handoff.branch}</code>
              </span>
            )}
            {handoff.pr_url && (
              <a className="text-brand-orange inline-flex items-center gap-1" href={handoff.pr_url} target="_blank" rel="noreferrer">
                <GitPullRequest className="w-3 h-3" /> PR #{handoff.pr_number}
              </a>
            )}
            {handoff.untested && handoff.pr_url && <span className="text-yellow-400">UNTESTED</span>}
            {handoff.ci_fix_round > 0 && (handoff.status === 'fixing' || handoff.status === 'ci_running') && (
              <span>CI fix round {handoff.ci_fix_round}/3</span>
            )}
            {progress && isActiveHandoff(handoff.status) && <span className="text-gray-400">· {progress.message}</span>}
          </div>
        )}

        {handoff.error && !isActiveHandoff(handoff.status) && (
          <div className="text-xs text-red-300 bg-red-500/10 border border-red-500/20 rounded-md px-2.5 py-1.5">{handoff.error}</div>
        )}

        {question && (
          <div className="rounded-md border border-brand-orange/30 bg-brand-orange/5 p-2 space-y-2">
            <div className="text-gray-200">Claude asks: {question.message}</div>
            {options.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {options.map((option) => (
                  <Button
                    key={option}
                    size="sm"
                    variant="brand-ghost"
                    disabled={sendMessage.isPending}
                    onClick={() => send(option)}
                  >
                    {option}
                  </Button>
                ))}
              </div>
            )}
          </div>
        )}

        {canMessage(handoff) && (
          <div className="flex gap-2">
            <Input
              value={reply}
              placeholder={question ? 'Answer Claude…' : 'Ask for changes…'}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && send(reply, () => setReply(''))}
            />
            <Button
              size="sm"
              variant="brand-ghost"
              onClick={() => send(reply, () => setReply(''))}
              disabled={sendMessage.isPending || !reply.trim()}
            >
              <Send className="w-4 h-4" />
            </Button>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {handoff.status === 'proposed' && (
            <>
              <Button size="sm" variant="brand-primary" disabled={busy} onClick={() => run('start')}>
                {handoff.has_run ? 'Re-start' : 'Start handoff'}
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(true)}>
                View &amp; edit brief
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => run('dismiss')}>
                Not now
              </Button>
            </>
          )}
          {handoff.pr_url && (
            <Button size="sm" variant="ghost" asChild>
              <a href={handoff.pr_url} target="_blank" rel="noreferrer">
                Open PR <ExternalLink className="w-3 h-3 ml-1" />
              </a>
            </Button>
          )}
          {review && (handoff.status === 'done' || handoff.status === 'ci_failed') && (
            <Button size="sm" variant="ghost" disabled={sendReview.isPending} onClick={() => sendReview.mutate()}>
              Send review to Claude
            </Button>
          )}
          {canTakeOver(handoff) && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => run('takeOver')}>
              Take over
            </Button>
          )}
          {canStop(handoff) && (
            <Button size="sm" variant="ghost" className="text-red-400" disabled={busy} onClick={() => run('cancel')}>
              Stop
            </Button>
          )}
          {canRestart(handoff) && (
            <Button size="sm" variant={revised ? 'brand-primary' : 'ghost'} disabled={busy} onClick={() => run('restart')}>
              Re-start
            </Button>
          )}
        </div>

        {handoff.status === 'taken_over' && handoff.branch && handoff.pr_number && (
          <code className="block text-xs bg-black/40 rounded p-2">
            git fetch origin {handoff.branch} && git checkout {handoff.branch}
          </code>
        )}
      </div>

      {editing && (
        <HandoffBriefDialog
          handoff={handoff}
          open={editing}
          onOpenChange={setEditing}
          onSaved={(detail) => refreshHandoff(queryClient, detail)}
        />
      )}
    </Card>
  )
}
