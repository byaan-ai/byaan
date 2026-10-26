import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo } from "react"
import { ApiService, type ScheduleRead } from "../../services/api"
import { useTestSchedule, useUpdateSchedule } from "../../hooks/useSchedules"
import { Button } from "../ui/button"
import { Card } from "../ui/card"
import { Loader2, Play, Power } from "lucide-react"
import { MonitorCard } from "./MonitorCard"

interface ChatMonitorCardProps {
  monitorId: string
  deleted?: boolean
}

export function ChatMonitorCard({ monitorId, deleted }: ChatMonitorCardProps) {
  const queryClient = useQueryClient()
  const testSchedule = useTestSchedule()
  const update = useUpdateSchedule()
  const { data: schedules, isLoading } = useQuery({
    queryKey: ["schedules"],
    queryFn: () => ApiService.listSchedules(),
  })

  const monitor = useMemo<ScheduleRead | undefined>(
    () => schedules?.find((s) => s.id === monitorId),
    [schedules, monitorId]
  )

  if (deleted) {
    return (
      <Card className="my-2 p-3 bg-[#232323] border-gray-800 text-sm text-gray-400">
        Monitor removed.
      </Card>
    )
  }

  if (isLoading) {
    return (
      <Card className="my-2 p-3 bg-[#232323] border-gray-800 text-sm text-gray-400 flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading monitor…
      </Card>
    )
  }

  if (!monitor) {
    return (
      <Card className="my-2 p-3 bg-[#232323] border-gray-800 text-sm text-gray-400">
        Monitor no longer available.
      </Card>
    )
  }

  const onChanged = () => queryClient.invalidateQueries({ queryKey: ["schedules"] })

  return (
    <div className="my-3 space-y-2">
      <MonitorCard monitor={monitor} onChanged={onChanged} />
      <div className="flex gap-2">
        {monitor.status === 'draft' && (
          <Button
            type="button"
            size="sm"
            onClick={() =>
              update.mutate(
                { scheduleId: monitor.id, data: { status: 'active', is_enabled: true } },
                { onSuccess: () => onChanged?.() }
              )
            }
            disabled={update.isPending}
          >
            <Power className="w-4 h-4 mr-1" />
            Turn on
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant={monitor.status === 'draft' ? 'ghost' : 'default'}
          onClick={() => testSchedule.mutate(monitor.id)}
          disabled={testSchedule.isPending}
        >
          <Play className="w-4 h-4 mr-1" />
          Run a test now
        </Button>
      </div>
    </div>
  )
}
