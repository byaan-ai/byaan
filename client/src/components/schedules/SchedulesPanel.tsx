import { useNavigate } from 'react-router-dom'
import { Button } from '../ui/button'
import { Card } from '../ui/card'
import { X, Clock, Loader2 } from 'lucide-react'
import { useSchedules, useDeleteSchedule, useUpdateSchedule, type ScheduleRead } from '../../hooks/useSchedules'
import { ScheduleCard } from './ScheduleCard'
import { MonitorCard } from '../monitors/MonitorCard'

function isMonitor(schedule: ScheduleRead): boolean {
  if (schedule.origin && schedule.origin !== 'ui') return true
  if (schedule.status && schedule.status !== 'active') return true
  if (schedule.notify_policy && schedule.notify_policy !== 'always') return true
  if (schedule.slack_thread_ts) return true
  return false
}

interface SchedulesPanelProps {
  open: boolean
  onClose: () => void
}

export function SchedulesPanel({ open, onClose }: SchedulesPanelProps) {
  const navigate = useNavigate()
  const { data: schedules = [], isLoading } = useSchedules()
  const deleteSchedule = useDeleteSchedule()
  const updateSchedule = useUpdateSchedule()

  const handleEdit = (schedule: ScheduleRead) => {
    onClose()
    navigate(`/notebook/${schedule.notebook_id}/preview`)
  }

  const handleDelete = async (scheduleId: string) => {
    await deleteSchedule.mutateAsync(scheduleId)
  }

  const handleToggle = async (scheduleId: string, enabled: boolean) => {
    await updateSchedule.mutateAsync({
      scheduleId,
      data: { is_enabled: enabled },
    })
  }

  const handleNavigateToNotebook = (notebookId: string) => {
    onClose()
    navigate(`/notebook/${notebookId}/preview`)
  }

  const activeCount = schedules.filter((s) => s.is_enabled).length

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-40 transition-opacity"
          onClick={onClose}
        />
      )}

      <div
        className={`fixed right-0 top-0 h-full w-96 bg-[#1a1a1a] border-l border-gray-800 z-50 transform transition-transform duration-300 ease-in-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          <div className="flex items-center justify-between p-4 border-b border-gray-800">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold text-white">Schedules</h2>
              {activeCount > 0 && (
                <span className="text-xs bg-brand-orange/20 text-brand-orange px-2 py-0.5 rounded-full">
                  {activeCount} active
                </span>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="h-8 w-8 p-0 text-gray-400 hover:text-white hover:bg-gray-800"
            >
              <X className="w-5 h-5" />
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-6 h-6 animate-spin text-brand-orange" />
              </div>
            ) : schedules.length === 0 ? (
              <Card className="p-6 text-center bg-[#2a2a2a] border-gray-800">
                <div className="w-12 h-12 bg-brand-orange/10 rounded-full flex items-center justify-center mx-auto mb-3">
                  <Clock className="w-6 h-6 text-brand-orange" />
                </div>
                <h3 className="text-sm font-medium text-white mb-1">No Schedules</h3>
                <p className="text-xs text-gray-400">
                  Open a notebook and click the clock icon to create a schedule.
                </p>
              </Card>
            ) : (
              schedules.map((schedule) =>
                isMonitor(schedule) ? (
                  <MonitorCard key={schedule.id} monitor={schedule} />
                ) : (
                  <ScheduleCard
                    key={schedule.id}
                    schedule={schedule}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                    onToggle={handleToggle}
                    onNavigateToNotebook={handleNavigateToNotebook}
                    showNotebookName
                  />
                )
              )
            )}
          </div>
        </div>
      </div>
    </>
  )
}
