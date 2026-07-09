import { useState, useMemo, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Check, X, Pencil, ExternalLink, MessageCircleQuestion, Loader2, Sparkles } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { useSkillSuggestions, useApproveSuggestion, useRejectSuggestion } from '../hooks/useSkillSuggestions'
import type { SkillSuggestion, SuggestionType, SuggestionStatus, EvidenceItem } from '../services/skillSuggestions'

type TypeBadgeStyle = { label: string; className: string }

function getTypeBadge(type: SuggestionType): TypeBadgeStyle {
  switch (type) {
    case 'edit':
      return { label: 'Mistake', className: 'bg-red-500/10 text-red-400 border border-red-500/20' }
    case 'promotion':
      return { label: 'Promotion', className: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' }
    case 'clarification':
      return { label: 'Question', className: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' }
    case 'new_skill':
      return { label: 'New skill', className: 'bg-blue-500/10 text-blue-400 border border-blue-500/20' }
    case 'casebook':
      return { label: 'Casebook', className: 'bg-purple-500/10 text-purple-400 border border-purple-500/20' }
    default:
      return { label: type, className: 'bg-gray-500/10 text-gray-400 border border-gray-500/20' }
  }
}

function getStatusBadge(status: SuggestionStatus): TypeBadgeStyle {
  switch (status) {
    case 'pending':
      return { label: 'Pending', className: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' }
    case 'approved':
      return { label: 'Approved', className: 'bg-green-500/10 text-green-400 border border-green-500/20' }
    case 'applied':
      return { label: 'Applied', className: 'bg-green-500/10 text-green-400 border border-green-500/20' }
    case 'rejected':
      return { label: 'Rejected', className: 'bg-red-500/10 text-red-400 border border-red-500/20' }
    case 'superseded':
      return { label: 'Superseded', className: 'bg-gray-500/10 text-gray-400 border border-gray-500/20' }
    default:
      return { label: status, className: 'bg-gray-500/10 text-gray-400 border border-gray-500/20' }
  }
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  if (isNaN(date.getTime())) return ''
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function sourceOriginLabel(origin: string | undefined): string {
  if (!origin) return ''
  if (origin.toLowerCase() === 'slack') return 'Slack'
  if (origin.toLowerCase() === 'app') return 'App'
  return origin
}

function isEvidenceArray(evidence: SkillSuggestion['evidence']): evidence is EvidenceItem[] {
  return Array.isArray(evidence)
}

function DiffView({ section, before, after }: { section: string; before: string; after: string }) {
  const beforeLines = (before || '').split('\n')
  const afterLines = (after || '').split('\n')

  return (
    <div className="space-y-2">
      {section && <p className="text-xs font-medium text-gray-400">Section: <span className="font-mono text-gray-300">{section}</span></p>}
      <div className="rounded-lg overflow-hidden border border-gray-800 font-mono text-xs">
        {before && (
          <div className="bg-red-500/5">
            {beforeLines.map((line, i) => (
              <div key={`b-${i}`} className="flex text-red-300/90">
                <span className="select-none w-6 text-center text-red-500/60 flex-shrink-0">-</span>
                <span className="whitespace-pre-wrap break-words py-0.5 pr-3">{line}</span>
              </div>
            ))}
          </div>
        )}
        {after && (
          <div className="bg-green-500/5 border-t border-gray-800">
            {afterLines.map((line, i) => (
              <div key={`a-${i}`} className="flex text-green-300/90">
                <span className="select-none w-6 text-center text-green-500/60 flex-shrink-0">+</span>
                <span className="whitespace-pre-wrap break-words py-0.5 pr-3">{line}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ConversationLink({ suggestion }: { suggestion: SkillSuggestion }) {
  const threadUrl = suggestion.source?.thread_url
  const notebookId = suggestion.source?.notebook_id
  if (threadUrl) {
    return (
      <a
        href={threadUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-sm text-brand-orange hover:underline"
      >
        <ExternalLink className="w-3.5 h-3.5" /> View conversation
      </a>
    )
  }
  if (notebookId) {
    return (
      <Link
        to={`/notebook/${notebookId}`}
        className="inline-flex items-center gap-1.5 text-sm text-brand-orange hover:underline"
      >
        <ExternalLink className="w-3.5 h-3.5" /> View conversation
      </Link>
    )
  }
  return null
}

export default function SkillReviewPage() {
  const [showResolved, setShowResolved] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [approveConfirmOpen, setApproveConfirmOpen] = useState(false)
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [rejectModalOpen, setRejectModalOpen] = useState(false)
  const [editInstructions, setEditInstructions] = useState('')
  const [rejectReason, setRejectReason] = useState('')

  const { data: pending = [], isLoading: loadingPending } = useSkillSuggestions('pending')
  const { data: rejected = [] } = useSkillSuggestions('rejected')
  const { data: applied = [] } = useSkillSuggestions('applied')

  const approveMutation = useApproveSuggestion()
  const rejectMutation = useRejectSuggestion()

  const resolved = useMemo(() => {
    return [...applied, ...rejected].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    )
  }, [applied, rejected])

  const visibleList = useMemo(() => {
    return showResolved ? [...pending, ...resolved] : pending
  }, [showResolved, pending, resolved])

  const allById = useMemo(() => {
    const map = new Map<string, SkillSuggestion>()
    for (const s of [...pending, ...resolved]) map.set(s.id, s)
    return map
  }, [pending, resolved])

  useEffect(() => {
    if (!selectedId && pending.length > 0) {
      setSelectedId(pending[0].id)
    }
  }, [pending, selectedId])

  const selected = selectedId ? allById.get(selectedId) ?? null : null

  const handleApproveConfirm = () => {
    if (!selected) return
    approveMutation.mutate(
      { id: selected.id },
      { onSuccess: () => setApproveConfirmOpen(false) }
    )
  }

  const handleEditApprove = () => {
    if (!selected) return
    approveMutation.mutate(
      { id: selected.id, finalInstructions: editInstructions },
      {
        onSuccess: () => {
          setEditModalOpen(false)
          setEditInstructions('')
        },
      }
    )
  }

  const handleReject = () => {
    if (!selected || !rejectReason.trim()) return
    rejectMutation.mutate(
      { id: selected.id, reason: rejectReason.trim() },
      {
        onSuccess: () => {
          setRejectModalOpen(false)
          setRejectReason('')
        },
      }
    )
  }

  const openEditModal = () => {
    setEditInstructions(selected?.proposed_instructions || '')
    setEditModalOpen(true)
  }

  return (
    <div className="bg-[#0d0d0d] w-full h-full flex flex-col">
      {/* Header */}
      <div className="px-8 pt-8 pb-4 border-b border-gray-800">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-brand-orange/10 flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-brand-orange" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white tracking-tight">Skill Review</h1>
              <p className="text-xs text-gray-400">Review and apply learnings suggested by the AI</p>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showResolved}
              onChange={(e) => setShowResolved(e.target.checked)}
              className="accent-brand-orange"
            />
            Show resolved
          </label>
        </div>
      </div>

      {/* Two-pane */}
      <div className="flex-1 flex min-h-0">
        {/* Left list */}
        <div className="w-[360px] flex-shrink-0 border-r border-gray-800 overflow-y-auto custom-scrollbar">
          {loadingPending ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-5 h-5 text-brand-orange animate-spin" />
            </div>
          ) : visibleList.length === 0 ? (
            <div className="text-center py-16 px-6 text-gray-500 text-sm">
              No suggestions to review
            </div>
          ) : (
            <div className="p-3 space-y-2">
              {visibleList.map((s) => {
                const typeBadge = getTypeBadge(s.suggestion_type)
                const isResolved = s.status !== 'pending'
                const isSelected = s.id === selectedId
                return (
                  <button
                    key={s.id}
                    onClick={() => setSelectedId(s.id)}
                    className={`w-full text-left rounded-lg border p-3 transition-colors ${
                      isSelected
                        ? 'bg-[#1a1a1a] border-brand-orange/50'
                        : 'bg-[#1a1a1a] border-gray-800 hover:border-gray-700'
                    } ${isResolved ? 'opacity-50' : ''}`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-1.5">
                      <span className="text-sm font-medium text-white line-clamp-2">{s.title}</span>
                      <span className={`flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${typeBadge.className}`}>
                        {typeBadge.label}
                      </span>
                    </div>
                    {s.skill_name && (
                      <p className="text-xs text-gray-400 truncate">{s.skill_name}</p>
                    )}
                    <div className="flex items-center gap-2 mt-1.5 text-[11px] text-gray-500">
                      {s.source?.origin && <span>{sourceOriginLabel(s.source.origin)}</span>}
                      {(s.source?.date || s.created_at) && (
                        <span>{formatDate(s.source?.date || s.created_at)}</span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Right detail */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          {!selected ? (
            <div className="flex items-center justify-center h-full text-gray-500 text-sm">
              Select a suggestion to review
            </div>
          ) : (
            <SuggestionDetail
              suggestion={selected}
              isBusy={approveMutation.isPending || rejectMutation.isPending}
              onApprove={() => setApproveConfirmOpen(true)}
              onEditApprove={openEditModal}
              onReject={() => {
                setRejectReason('')
                setRejectModalOpen(true)
              }}
            />
          )}
        </div>
      </div>

      {/* Approve confirm */}
      <Dialog open={approveConfirmOpen} onOpenChange={(open) => { if (!approveMutation.isPending) setApproveConfirmOpen(open) }}>
        <DialogContent className="max-w-md bg-[#2a2a2a] border-[#444444]">
          <DialogHeader>
            <DialogTitle className="text-white">Approve suggestion?</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-[#aaaaaa]">
              This will apply the proposed change to <span className="font-semibold text-white">{selected?.skill_name || 'the skill'}</span> and create a new version.
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setApproveConfirmOpen(false)}
                disabled={approveMutation.isPending}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                variant="brand-primary"
                onClick={handleApproveConfirm}
                disabled={approveMutation.isPending}
              >
                {approveMutation.isPending ? 'Applying...' : 'Approve'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit & approve modal */}
      <Dialog open={editModalOpen} onOpenChange={(open) => { if (!approveMutation.isPending) setEditModalOpen(open) }}>
        <DialogContent className="max-w-2xl bg-[#2a2a2a] border-[#444444]">
          <DialogHeader>
            <DialogTitle className="text-white">Edit & approve</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-gray-400">Adjust the instructions before applying them to the skill.</p>
            <textarea
              value={editInstructions}
              onChange={(e) => setEditInstructions(e.target.value)}
              rows={14}
              className="w-full rounded-md bg-[#1a1a1a] border border-[#555555] text-white text-sm font-mono p-3 focus:border-brand-orange focus:ring-1 focus:ring-brand-orange/50 focus:outline-none custom-scrollbar"
              placeholder="Final instructions..."
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setEditModalOpen(false)}
                disabled={approveMutation.isPending}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                variant="brand-primary"
                onClick={handleEditApprove}
                disabled={approveMutation.isPending || !editInstructions.trim()}
              >
                {approveMutation.isPending ? 'Applying...' : 'Apply changes'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Reject modal */}
      <Dialog open={rejectModalOpen} onOpenChange={(open) => { if (!rejectMutation.isPending) setRejectModalOpen(open) }}>
        <DialogContent className="max-w-md bg-[#2a2a2a] border-[#444444]">
          <DialogHeader>
            <DialogTitle className="text-white">Reject suggestion</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-gray-400">Add a reason for rejecting this suggestion. This is required.</p>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              rows={4}
              className="w-full rounded-md bg-[#1a1a1a] border border-[#555555] text-white text-sm p-3 focus:border-brand-orange focus:ring-1 focus:ring-brand-orange/50 focus:outline-none"
              placeholder="Reason for rejection..."
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setRejectModalOpen(false)}
                disabled={rejectMutation.isPending}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                onClick={handleReject}
                disabled={rejectMutation.isPending || !rejectReason.trim()}
                className="bg-red-800 hover:bg-red-900 text-white"
              >
                {rejectMutation.isPending ? 'Rejecting...' : 'Reject'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SuggestionDetail({
  suggestion,
  isBusy,
  onApprove,
  onEditApprove,
  onReject,
}: {
  suggestion: SkillSuggestion
  isBusy: boolean
  onApprove: () => void
  onEditApprove: () => void
  onReject: () => void
}) {
  const statusBadge = getStatusBadge(suggestion.status)
  const typeBadge = getTypeBadge(suggestion.suggestion_type)
  const isPending = suggestion.status === 'pending'
  const isClarification = suggestion.suggestion_type === 'clarification'

  return (
    <div className="max-w-3xl mx-auto px-8 py-6 space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-lg font-semibold text-white">{suggestion.title}</h2>
          <span className={`flex-shrink-0 px-2 py-0.5 rounded text-xs font-medium ${statusBadge.className}`}>
            {statusBadge.label}
          </span>
        </div>
        <div className="flex items-center gap-2 mt-2 flex-wrap text-xs text-gray-400">
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${typeBadge.className}`}>{typeBadge.label}</span>
          {suggestion.skill_name && <span className="text-gray-300">{suggestion.skill_name}</span>}
          <span className="text-gray-600">•</span>
          <span className="capitalize">{suggestion.confidence} confidence</span>
          {suggestion.source?.origin && (
            <>
              <span className="text-gray-600">•</span>
              <span>{sourceOriginLabel(suggestion.source.origin)}</span>
            </>
          )}
          <span className="text-gray-600">•</span>
          <span>{formatDate(suggestion.source?.date || suggestion.created_at)}</span>
        </div>
      </div>

      {/* Clarification: prominent question */}
      {isClarification && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
          <div className="flex items-start gap-2">
            <MessageCircleQuestion className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm text-amber-100 font-medium">{suggestion.title}</p>
              <p className="text-xs text-amber-300/70 mt-2">
                Answer this in the originating thread — this is a clarifying question, not a change to apply here.
              </p>
              <div className="mt-2">
                <ConversationLink suggestion={suggestion} />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Rationale */}
      {suggestion.rationale && (
        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Rationale</h3>
          <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">{suggestion.rationale}</p>
        </section>
      )}

      {/* Evidence */}
      {suggestion.evidence != null && (
        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Evidence</h3>
          {isEvidenceArray(suggestion.evidence) ? (
            <div className="space-y-2">
              {suggestion.evidence.map((item, i) => (
                <div key={i} className="rounded-lg border border-gray-800 bg-[#1a1a1a] p-3 space-y-1">
                  <p className="text-sm text-white">{item.claim}</p>
                  {item.check && <p className="text-xs text-gray-400"><span className="text-gray-500">Check:</span> {item.check}</p>}
                  {item.result && <p className="text-xs text-gray-400"><span className="text-gray-500">Result:</span> {item.result}</p>}
                </div>
              ))}
            </div>
          ) : (
            <pre className="rounded-lg border border-gray-800 bg-[#1a1a1a] p-3 text-xs text-gray-300 font-mono overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(suggestion.evidence, null, 2)}
            </pre>
          )}
        </section>
      )}

      {/* Diff */}
      {!isClarification && suggestion.patch && (
        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Proposed change</h3>
          <DiffView section={suggestion.patch.section} before={suggestion.patch.before} after={suggestion.patch.after} />
        </section>
      )}

      {/* Actions */}
      {isPending && (
        <div className="pt-2 border-t border-gray-800">
          {isClarification ? (
            <div className="flex items-center gap-3 pt-4">
              <Button
                onClick={onReject}
                disabled={isBusy}
                variant="outline"
                className="border-gray-700 text-gray-300 hover:bg-gray-800"
              >
                <X className="w-4 h-4 mr-1.5" /> Dismiss
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-3 pt-4 flex-wrap">
              <Button
                onClick={onApprove}
                disabled={isBusy}
                variant="brand-primary"
              >
                <Check className="w-4 h-4 mr-1.5" /> Approve
              </Button>
              <Button
                onClick={onEditApprove}
                disabled={isBusy}
                variant="outline"
                className="border-gray-700 text-gray-300 hover:bg-gray-800"
              >
                <Pencil className="w-4 h-4 mr-1.5" /> Edit & approve
              </Button>
              <Button
                onClick={onReject}
                disabled={isBusy}
                variant="ghost"
                className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
              >
                <X className="w-4 h-4 mr-1.5" /> Reject…
              </Button>
              <div className="ml-auto">
                <ConversationLink suggestion={suggestion} />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Resolved review note */}
      {!isPending && suggestion.review_note && (
        <div className="pt-2 border-t border-gray-800">
          <p className="text-xs text-gray-500 pt-4">
            <span className="text-gray-400">Review note:</span> {suggestion.review_note}
          </p>
        </div>
      )}
    </div>
  )
}
