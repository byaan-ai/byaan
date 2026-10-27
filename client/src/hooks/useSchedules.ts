import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiService, type ScheduleRead, type ScheduleCreate, type ScheduleUpdate, type ScheduleTestResult, type ScheduleRunRead } from '../services/api'
import { showToast } from '../utils/toast'

export function useSchedules(notebookId?: string) {
  return useQuery({
    queryKey: notebookId ? ['schedules', 'notebook', notebookId] : ['schedules'],
    queryFn: async () => {
      if (notebookId) {
        return await ApiService.getNotebookSchedules(notebookId)
      }
      return await ApiService.listSchedules()
    },
  })
}

export function useCreateSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ notebookId, data }: { notebookId: string; data: ScheduleCreate }) =>
      ApiService.createSchedule(notebookId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      showToast.success('Schedule created successfully')
    },
    onError: (error) => {
      console.error('Error creating schedule:', error)
      showToast.error(error instanceof Error ? error.message : 'Failed to create schedule')
    },
  })
}

export function useUpdateSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ scheduleId, data }: { scheduleId: string; data: ScheduleUpdate }) =>
      ApiService.updateSchedule(scheduleId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      showToast.success('Schedule updated successfully')
    },
    onError: (error) => {
      console.error('Error updating schedule:', error)
      showToast.error(error instanceof Error ? error.message : 'Failed to update schedule')
    },
  })
}

export function useDeleteSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (scheduleId: string) => ApiService.deleteSchedule(scheduleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      showToast.success('Schedule deleted successfully')
    },
    onError: (error) => {
      console.error('Error deleting schedule:', error)
      showToast.error(error instanceof Error ? error.message : 'Failed to delete schedule')
    },
  })
}

// Persisted running-monitor markers survive page refresh. Each entry records
// when the user clicked "Run a test now" so we can detect completion by
// comparing against the latest run row from the backend.
const RUNNING_STORAGE_KEY = 'byaan:running-monitors'
const RUNNING_MAX_AGE_MS = 10 * 60_000

type RunningMarker = { startedAt: number }
type RunningMap = Record<string, RunningMarker>

const RUNNING_LISTENERS = new Set<() => void>()

function readRunningMap(): RunningMap {
  try {
    const raw = localStorage.getItem(RUNNING_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as RunningMap
    const now = Date.now()
    const out: RunningMap = {}
    for (const [id, marker] of Object.entries(parsed)) {
      if (marker && typeof marker.startedAt === 'number' && now - marker.startedAt < RUNNING_MAX_AGE_MS) {
        out[id] = marker
      }
    }
    return out
  } catch {
    return {}
  }
}

function writeRunningMap(map: RunningMap) {
  try {
    localStorage.setItem(RUNNING_STORAGE_KEY, JSON.stringify(map))
  } catch {
    // Best-effort — if localStorage is unavailable the banner just won't persist.
  }
  RUNNING_LISTENERS.forEach(fn => fn())
}

function markRunning(monitorId: string) {
  const map = readRunningMap()
  map[monitorId] = { startedAt: Date.now() }
  writeRunningMap(map)
}

export function clearMonitorRunning(monitorId: string) {
  clearRunning(monitorId)
}

function clearRunning(monitorId: string) {
  const map = readRunningMap()
  if (map[monitorId]) {
    delete map[monitorId]
    writeRunningMap(map)
  }
}

export function getRunningMonitorIds(): string[] {
  return Object.keys(readRunningMap())
}

export function getRunningMonitorStartedAt(monitorId: string): number | null {
  const map = readRunningMap()
  return map[monitorId]?.startedAt ?? null
}

export function runningStageLabel(elapsedMs: number): string {
  if (elapsedMs < 3_000) return 'Preparing context…'
  if (elapsedMs < 10_000) return 'Thinking…'
  if (elapsedMs < 30_000) return 'Running queries…'
  if (elapsedMs < 60_000) return 'Summarizing results…'
  return 'Wrapping up…'
}

export function useRunningStage(monitorId: string | null | undefined): string | null {
  const [stage, setStage] = useState<string | null>(null)
  useEffect(() => {
    if (!monitorId) {
      setStage(null)
      return
    }
    const update = () => {
      const started = getRunningMonitorStartedAt(monitorId)
      if (started === null) {
        setStage(null)
      } else {
        setStage(runningStageLabel(Date.now() - started))
      }
    }
    update()
    const interval = setInterval(update, 1_500)
    return () => clearInterval(interval)
  }, [monitorId])
  return stage
}

export function useMonitorRunningState(monitorId: string | null | undefined) {
  const [, forceRender] = useState(0)
  useEffect(() => {
    const fn = () => forceRender(v => v + 1)
    RUNNING_LISTENERS.add(fn)
    const onStorage = (e: StorageEvent) => {
      if (e.key === RUNNING_STORAGE_KEY) fn()
    }
    window.addEventListener('storage', onStorage)
    return () => {
      RUNNING_LISTENERS.delete(fn)
      window.removeEventListener('storage', onStorage)
    }
  }, [])
  if (!monitorId) return false
  return !!readRunningMap()[monitorId]
}

export function useRunningMonitorIds() {
  const [ids, setIds] = useState<string[]>(() => getRunningMonitorIds())
  useEffect(() => {
    const fn = () => setIds(getRunningMonitorIds())
    RUNNING_LISTENERS.add(fn)
    const onStorage = (e: StorageEvent) => {
      if (e.key === RUNNING_STORAGE_KEY) fn()
    }
    window.addEventListener('storage', onStorage)
    return () => {
      RUNNING_LISTENERS.delete(fn)
      window.removeEventListener('storage', onStorage)
    }
  }, [])
  return ids
}

// Polls the runs endpoint for every currently-running monitor and clears the
// marker as soon as a run row with a `completed_at` newer than the marker
// start-time appears. Mount once at app root.
export function useMonitorRunWatcher() {
  const queryClient = useQueryClient()
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      const map = readRunningMap()
      const ids = Object.keys(map)
      if (ids.length === 0) return
      // While any monitor is marked running, keep nudging the notebook message
      // cache so the pre-stream header card and in-flight assistant messages
      // show up without waiting for completion.
      queryClient.invalidateQueries({ queryKey: ['messages'] })
      try {
        window.dispatchEvent(new CustomEvent('byaan:monitor-messages-refresh'))
      } catch {
        // Best-effort — react-query consumers refetch via invalidate above anyway.
      }
      await Promise.all(
        ids.map(async (id) => {
          const marker = map[id]
          try {
            const runs = await ApiService.getScheduleRuns(id, 3)
            const parseServerDate = (s: string): number => {
              // Backend serializes naive UTC datetimes without a tz suffix.
              // Date.parse would otherwise interpret them as local time.
              const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`
              return Date.parse(normalized)
            }
            const match = runs.find((r) => {
              if (!r.completed_at) return false
              const completed = parseServerDate(r.completed_at)
              return !Number.isNaN(completed) && completed >= marker.startedAt - 5_000
            })
            if (match) {
              clearRunning(id)
              queryClient.invalidateQueries({ queryKey: ['schedules'] })
              queryClient.invalidateQueries({ queryKey: ['messages'] })
              queryClient.invalidateQueries({ queryKey: ['threads'] })
              queryClient.invalidateQueries({ queryKey: ['schedule-runs', id] })
              try {
                window.dispatchEvent(new CustomEvent('byaan:monitor-test-complete', { detail: { monitorId: id } }))
              } catch {
                // Dispatch is best-effort — notebook consumers will still refetch via
                // react-query cache invalidation above.
              }
            } else {
              // Fallback: if any run row was started at/after our marker and is no
              // longer in a running state, consider the test done. Guards against
              // backends that forget to set completed_at.
              const terminal = runs.find((r) => {
                const started = parseServerDate(r.started_at)
                const notRunning = r.status && r.status !== 'running' && r.status !== 'pending'
                return !Number.isNaN(started) && started >= marker.startedAt - 5_000 && notRunning
              })
              if (terminal) {
                clearRunning(id)
                queryClient.invalidateQueries({ queryKey: ['schedules'] })
                queryClient.invalidateQueries({ queryKey: ['messages'] })
                queryClient.invalidateQueries({ queryKey: ['threads'] })
                queryClient.invalidateQueries({ queryKey: ['schedule-runs', id] })
              } else if (Date.now() - marker.startedAt > RUNNING_MAX_AGE_MS) {
                clearRunning(id)
              }
            }
          } catch {
            // Transient fetch failure — try again on the next tick.
          }
        })
      )
    }
    tick()
    const interval = setInterval(() => {
      if (!cancelled) tick()
    }, 5_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [queryClient])
}

export function useTestSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (scheduleId: string) => ApiService.testSchedule(scheduleId),
    onMutate: (scheduleId: string) => {
      showToast.info('Running a test for your monitor… results will appear in the notebook shortly.', {
        autoClose: 8000,
      })
      markRunning(scheduleId)
      // Kick several short-interval refetches so the header message the backend
      // writes before streaming shows up in the notebook almost immediately,
      // instead of waiting for the run to complete.
      const earlyRefresh = () => {
        queryClient.invalidateQueries({ queryKey: ['messages'] })
        queryClient.invalidateQueries({ queryKey: ['threads'] })
        try {
          window.dispatchEvent(new CustomEvent('byaan:monitor-messages-refresh', { detail: { monitorId: scheduleId } }))
        } catch {
          // Best-effort — notebook listener will catch the next tick if this fires fail.
        }
      }
      setTimeout(earlyRefresh, 1_500)
      setTimeout(earlyRefresh, 4_000)
      setTimeout(earlyRefresh, 8_000)
    },
    onSuccess: (result: ScheduleTestResult, scheduleId) => {
      if (result.success) {
        showToast.success(result.summary || 'Test run queued')
        const refresh = () => {
          queryClient.invalidateQueries({ queryKey: ['schedules'] })
          queryClient.invalidateQueries({ queryKey: ['messages'] })
          queryClient.invalidateQueries({ queryKey: ['threads'] })
          queryClient.invalidateQueries({ queryKey: ['schedule-runs', scheduleId] })
        }
        setTimeout(refresh, 15_000)
        setTimeout(refresh, 45_000)
      } else {
        clearRunning(scheduleId)
        showToast.error(result.error || 'Test run failed')
      }
    },
    onError: (error, scheduleId) => {
      clearRunning(scheduleId)
      console.error('Error testing schedule:', error)
      showToast.error(error instanceof Error ? error.message : 'Failed to test schedule')
    },
  })
}

export function usePreviewSchedule() {
  return useMutation({
    mutationFn: (notebookId: string) => ApiService.previewSchedule(notebookId),
    onSuccess: (result: ScheduleTestResult) => {
      if (result.success) {
        showToast.success('Preview completed successfully')
      } else {
        showToast.error(result.error || 'Preview failed')
      }
    },
    onError: (error) => {
      console.error('Error previewing schedule:', error)
      showToast.error(error instanceof Error ? error.message : 'Failed to preview schedule')
    },
  })
}

export function useScheduleRuns(scheduleId: string | null | undefined, limit = 20) {
  return useQuery({
    queryKey: ['schedule-runs', scheduleId, limit],
    queryFn: () => (scheduleId ? ApiService.getScheduleRuns(scheduleId, limit) : Promise.resolve([])),
    enabled: !!scheduleId,
  })
}

export type { ScheduleRead, ScheduleCreate, ScheduleUpdate, ScheduleTestResult, ScheduleRunRead }
