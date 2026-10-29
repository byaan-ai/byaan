import { useState } from 'react'
import { Card } from '../ui/card'
import { Button } from '../ui/button'
import { Switch } from '../ui/switch'
import { Trash2, Loader2, Edit, Clock, ExternalLink } from 'lucide-react'
import type { ScheduleRead } from '../../hooks/useSchedules'

interface ScheduleCardProps {
  schedule: ScheduleRead
  onEdit: (schedule: ScheduleRead) => void
  onDelete: (scheduleId: string) => void
  onToggle: (scheduleId: string, enabled: boolean) => void
  onNavigateToNotebook?: (notebookId: string) => void
  showNotebookName?: boolean
}

function humanizeCron(cron: string): string {
  const parts = cron.split(' ')
  if (parts.length !== 5) return cron

  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts

  if (dayOfMonth === '*' && month === '*') {
    if (dayOfWeek === '*') {
      return `Daily at ${formatTime(hour, minute)}`
    }
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    const dayNum = parseInt(dayOfWeek)
    if (!isNaN(dayNum) && dayNum >= 0 && dayNum <= 6) {
      return `${days[dayNum]} at ${formatTime(hour, minute)}`
    }
  }

  return cron
}

function formatTime(hour: string, minute: string): string {
  const h = parseInt(hour)
  const m = parseInt(minute)
  if (isNaN(h) || isNaN(m)) return `${hour}:${minute}`

  const period = h >= 12 ? 'PM' : 'AM'
  const displayHour = h === 0 ? 12 : h > 12 ? h - 12 : h
  return `${displayHour}:${m.toString().padStart(2, '0')} ${period}`
}

function formatNextRun(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Not scheduled'
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(dateStr) ? dateStr : `${dateStr}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return dateStr
  const now = new Date()
  const diffMs = date.getTime() - now.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffDays === 0) {
    return `Today at ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  } else if (diffDays === 1) {
    return `Tomorrow at ${date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  } else if (diffDays < 7) {
    return date.toLocaleDateString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })
  }
  return date.toLocaleDateString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function ScheduleCard({
  schedule,
  onEdit,
  onDelete,
  onToggle,
  onNavigateToNotebook,
  showNotebookName = false,
}: ScheduleCardProps) {
  const [isDeleting, setIsDeleting] = useState(false)
  const [isToggling, setIsToggling] = useState(false)

  const handleDelete = async () => {
    setIsDeleting(true)
    try {
      await onDelete(schedule.id)
    } finally {
      setIsDeleting(false)
    }
  }

  const handleToggle = async (enabled: boolean) => {
    setIsToggling(true)
    try {
      await onToggle(schedule.id, enabled)
    } finally {
      setIsToggling(false)
    }
  }

  return (
    <Card className="p-4 bg-[#2a2a2a] border-gray-800">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-white font-medium truncate">{schedule.name}</h4>
            {schedule.status && schedule.status !== 'active' && (
              <span
                className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
                  schedule.status === 'draft'
                    ? 'bg-gray-800 text-gray-300'
                    : schedule.status === 'paused'
                    ? 'bg-amber-900/40 text-amber-300'
                    : 'bg-red-900/40 text-red-300'
                }`}
                title={`Monitor status: ${schedule.status}`}
              >
                {schedule.status.replace('_', ' ')}
              </span>
            )}
            {schedule.origin && schedule.origin !== 'ui' && (
              <span
                className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
                  schedule.origin === 'slack'
                    ? 'bg-purple-900/40 text-purple-300'
                    : 'bg-blue-900/40 text-blue-300'
                }`}
                title={`Created from ${schedule.origin}`}
              >
                {schedule.origin === 'notebook_chat' ? 'chat' : schedule.origin}
              </span>
            )}
            {schedule.is_running && (
              <span className="text-xs text-brand-orange flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />
                Running
              </span>
            )}
          </div>

          {showNotebookName && schedule.notebook_name && (
            <button
              onClick={() => onNavigateToNotebook?.(schedule.notebook_id)}
              className="flex items-center gap-1 text-sm text-gray-400 hover:text-brand-orange transition-colors mt-0.5"
            >
              <span className="truncate">{schedule.notebook_name}</span>
              <ExternalLink className="w-3 h-3 flex-shrink-0" />
            </button>
          )}

          <div className="flex items-center gap-2 mt-2">
            <Clock className="w-3.5 h-3.5 text-gray-500" />
            <p className="text-sm text-gray-400">{humanizeCron(schedule.cron_expression)}</p>
          </div>

          <p className="text-xs text-gray-500 mt-1">
            {schedule.is_enabled
              ? `Next: ${formatNextRun(schedule.next_run_at)}`
              : 'Paused'}
          </p>

          {schedule.timezone !== 'UTC' && (
            <p className="text-xs text-gray-500">{schedule.timezone}</p>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <Switch
            checked={schedule.is_enabled}
            onCheckedChange={handleToggle}
            disabled={isToggling}
          />

          <Button
            size="sm"
            variant="ghost"
            onClick={() => onEdit(schedule)}
            className="text-gray-400 hover:text-white hover:bg-gray-800 h-8 w-8 p-0"
            title="Edit schedule"
          >
            <Edit className="w-4 h-4" />
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={handleDelete}
            disabled={isDeleting}
            className="text-gray-400 hover:text-red-400 hover:bg-gray-800 h-8 w-8 p-0"
            title="Delete schedule"
          >
            {isDeleting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4" />
            )}
          </Button>
        </div>
      </div>
    </Card>
  )
}
