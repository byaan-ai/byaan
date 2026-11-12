import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog'
import { Button } from '../ui/button'
import { Label } from '../ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select'
import { handoffKeys } from '../../hooks/useHandoffs'
import { HandoffsApi } from '../../services/handoffs'
import { showToast } from '../../utils/toast'

interface HandoffTeamSettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const UNTIL_FIXED = 'until-fixed'
const ROUND_OPTIONS = [
  { value: '1', label: '1 attempt' },
  { value: '3', label: '3 attempts' },
  { value: '5', label: '5 attempts' },
  { value: UNTIL_FIXED, label: 'Until fixed' },
]

const textareaClass =
  'w-full rounded-md border border-gray-700 bg-[#1a1a1a] px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-brand-orange custom-scrollbar'

export function HandoffTeamSettingsDialog({ open, onOpenChange }: HandoffTeamSettingsDialogProps) {
  const queryClient = useQueryClient()
  const { data: settings, isLoading } = useQuery({
    queryKey: handoffKeys.teamSettings,
    queryFn: HandoffsApi.teamSettings,
    enabled: open,
  })
  const [instructions, setInstructions] = useState('')
  const [rounds, setRounds] = useState('3')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!settings || !open) return
    setInstructions(settings.instructions)
    setRounds(settings.ci_fix_rounds === null ? UNTIL_FIXED : String(settings.ci_fix_rounds))
  }, [settings, open])

  const canEdit = !!settings?.can_edit
  const ciFixRounds = rounds === UNTIL_FIXED ? null : Number(rounds)
  const dirty =
    !!settings && (instructions.trim() !== settings.instructions || ciFixRounds !== settings.ci_fix_rounds)

  const save = async () => {
    setSaving(true)
    try {
      const saved = await HandoffsApi.saveTeamSettings({ instructions, ci_fix_rounds: ciFixRounds })
      queryClient.setQueryData(handoffKeys.teamSettings, { ...settings, ...saved })
      showToast.success('Handoff settings saved')
      onOpenChange(false)
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Could not save handoff settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#232323] border-gray-800 text-white max-w-2xl max-h-[90vh] overflow-y-auto custom-scrollbar">
        <DialogHeader>
          <DialogTitle>Handoff settings</DialogTitle>
          <DialogDescription className="text-gray-400">
            Applies to every handoff in this workspace.{!canEdit && settings && ' Only an admin can change these.'}
          </DialogDescription>
        </DialogHeader>
        {isLoading || !settings ? (
          <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
        ) : (
          <div className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="handoff-built-in-rules">Built-in rules (always sent to Claude Code)</Label>
              <textarea
                id="handoff-built-in-rules"
                readOnly
                rows={8}
                value={settings.built_in_rules}
                className={`${textareaClass} text-gray-400 cursor-default`}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="handoff-team-instructions">Your instructions</Label>
              <textarea
                id="handoff-team-instructions"
                rows={5}
                maxLength={4000}
                disabled={!canEdit}
                placeholder="Added after the built-in rules on every run, e.g. run ruff before submitting, start the PR summary with the ticket number."
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                className={`${textareaClass} disabled:opacity-60`}
              />
              <p className="text-xs text-gray-500">If they conflict with the built-in rules, the built-in rules win.</p>
            </div>
            <div className="space-y-1">
              <Label>CI fix attempts</Label>
              <Select value={rounds} onValueChange={setRounds} disabled={!canEdit}>
                <SelectTrigger className="bg-[#1a1a1a] border-gray-700 text-white text-sm h-9 w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#2a2a2a] border-gray-600">
                  {ROUND_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value} className="text-sm">
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-gray-500">
                {rounds === UNTIL_FIXED
                  ? 'Keeps fixing until all checks pass. Stops if CI fails the same way 3 times in a row.'
                  : 'How many times Claude Code tries to fix failing CI before a person takes over. Also stops if CI fails the same way 3 times in a row.'}
              </p>
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            {canEdit ? 'Cancel' : 'Close'}
          </Button>
          {canEdit && (
            <Button variant="brand-primary" onClick={save} disabled={saving || !dirty}>
              {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              Save
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
