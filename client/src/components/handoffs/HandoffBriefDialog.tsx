import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { HandoffsApi, type HandoffDetail } from '../../services/handoffs'
import { showToast } from '../../utils/toast'

interface HandoffBriefDialogProps {
  handoff: HandoffDetail
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: (detail: HandoffDetail) => void
}

const lines = (value: string) =>
  value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

const textareaClass =
  'w-full rounded-md border border-gray-700 bg-[#1a1a1a] px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-brand-orange'

export function HandoffBriefDialog({ handoff, open, onOpenChange, onSaved }: HandoffBriefDialogProps) {
  const [title, setTitle] = useState(handoff.title)
  const [problem, setProblem] = useState(handoff.brief.problem)
  const [evidence, setEvidence] = useState(handoff.brief.evidence.join('\n'))
  const [files, setFiles] = useState(handoff.brief.suspected_files.join('\n'))
  const [check, setCheck] = useState(handoff.brief.acceptance_check)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      const detail = await HandoffsApi.editBrief(
        handoff.id,
        { problem, evidence: lines(evidence), suspected_files: lines(files), acceptance_check: check },
        title,
      )
      onSaved(detail)
      onOpenChange(false)
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Could not save the brief')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#232323] border-gray-800 text-white max-w-xl">
        <DialogHeader>
          <DialogTitle>Edit handoff brief</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="handoff-title">Title</Label>
            <Input id="handoff-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="handoff-problem">Task</Label>
            <textarea id="handoff-problem" rows={4} className={textareaClass} value={problem} onChange={(e) => setProblem(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="handoff-evidence">Evidence (one per line, references only)</Label>
            <textarea id="handoff-evidence" rows={3} className={textareaClass} value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="handoff-files">Scope / files (one per line)</Label>
            <textarea id="handoff-files" rows={2} className={textareaClass} value={files} onChange={(e) => setFiles(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="handoff-check">Done when</Label>
            <textarea id="handoff-check" rows={2} className={textareaClass} value={check} onChange={(e) => setCheck(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button variant="brand-primary" onClick={save} disabled={saving || !problem.trim() || !title.trim()}>
            {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
