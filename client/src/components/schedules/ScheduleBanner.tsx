import { Calendar, Pencil, Pause, Play, Trash2, MessageSquare } from 'lucide-react'
import { type ScheduleRead } from '../../hooks/useSchedules'

interface ScheduleBannerProps {
  schedule: ScheduleRead
  onEdit: () => void
  onToggle: (enabled: boolean) => void
  onDelete: () => void
  slackChannelName?: string
}

function formatCronSchedule(cron: string): string {
  const parts = cron.split(' ')
  if (parts.length !== 5) return cron

  const [minute, hour, dayOfMonth, , dayOfWeek] = parts

  const hourNum = parseInt(hour)
  const minuteNum = parseInt(minute)

  if (isNaN(hourNum) || isNaN(minuteNum)) {
    return cron
  }

  const ampm = hourNum >= 12 ? 'PM' : 'AM'
  const hour12 = hourNum === 0 ? 12 : hourNum > 12 ? hourNum - 12 : hourNum
  const timeStr = `${hour12}:${minuteNum.toString().padStart(2, '0')} ${ampm}`

  if (dayOfMonth === '*' && dayOfWeek === '*') {
    return `Daily at ${timeStr}`
  }

  if (dayOfMonth === '*' && dayOfWeek !== '*') {
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    const dayName = days[parseInt(dayOfWeek)] || dayOfWeek
    return `${dayName} at ${timeStr}`
  }

  return `${cron} (${timeStr})`
}

export function ScheduleBanner({ schedule, onEdit, onToggle, onDelete, slackChannelName }: ScheduleBannerProps) {
  const scheduleText = formatCronSchedule(schedule.cron_expression)
  const truncatedInstruction = schedule.instruction && schedule.instruction.length > 60
    ? schedule.instruction.slice(0, 60) + '...'
    : schedule.instruction

  const effectiveStatus = schedule.status || (schedule.is_enabled ? 'active' : 'paused')
  const statusLabel: Record<string, string> = {
    active: 'SCHEDULED',
    paused: 'PAUSED',
    draft: 'DRAFT',
    auto_paused: 'AUTO-PAUSED',
  }
  const label = statusLabel[effectiveStatus] || (schedule.is_enabled ? 'SCHEDULED' : 'PAUSED')
  const isActive = effectiveStatus === 'active'

  return (
    <div className={`flex items-center gap-3 px-4 py-2.5 border-b ${
      isActive
        ? 'bg-brand-orange/5 border-brand-orange/20'
        : 'bg-[#1f1f1f] border-[#333]'
    }`}>
      <div className={`flex items-center justify-center w-8 h-8 rounded-lg ${
        isActive ? 'bg-brand-orange/10' : 'bg-[#2a2a2a]'
      }`}>
        <Calendar className={`w-4 h-4 ${isActive ? 'text-brand-orange' : 'text-gray-500'}`} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-xs">
          <span className={`font-medium ${isActive ? 'text-brand-orange' : 'text-gray-400'}`}>
            {label}
          </span>
          <span className="text-gray-500">·</span>
          <span className="text-gray-300">{scheduleText}</span>
          {slackChannelName && (
            <>
              <span className="text-gray-500">→</span>
              <span className="flex items-center gap-1 text-gray-400">
                <MessageSquare className="w-3 h-3" />
                #{slackChannelName}
              </span>
            </>
          )}
        </div>
        {truncatedInstruction && (
          <p className="text-xs text-gray-500 truncate mt-0.5">
            "{truncatedInstruction}"
          </p>
        )}
      </div>

      <div className="flex items-center gap-1">
        <button
          onClick={onEdit}
          className="p-1.5 hover:bg-[#2a2a2a] rounded-lg transition-colors text-gray-400 hover:text-white"
          title="Edit schedule"
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onToggle(!isActive)}
          className={`p-1.5 rounded-lg transition-colors ${
            isActive
              ? 'hover:bg-yellow-500/10 text-yellow-500 hover:text-yellow-400'
              : 'hover:bg-green-500/10 text-green-500 hover:text-green-400'
          }`}
          title={isActive ? 'Pause schedule' : 'Resume schedule'}
        >
          {isActive ? (
            <Pause className="w-3.5 h-3.5" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
        </button>
        <button
          onClick={onDelete}
          className="p-1.5 hover:bg-red-500/10 rounded-lg transition-colors text-gray-400 hover:text-red-400"
          title="Delete schedule"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}
