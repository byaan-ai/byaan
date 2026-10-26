import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiService, type ScheduleRead, type ScheduleCreate, type ScheduleUpdate, type ScheduleTestResult } from '../services/api'
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

export function useTestSchedule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (scheduleId: string) => ApiService.testSchedule(scheduleId),
    onSuccess: (result: ScheduleTestResult) => {
      if (result.success) {
        showToast.success(result.summary || 'Test run queued')
        // Monitor runs land in the notebook as a new assistant message once
        // the runner picks them up (usually within 60s). Nudge the message
        // and schedule caches a few times so the UI refreshes without the
        // user manually reloading the notebook.
        const refresh = () => {
          queryClient.invalidateQueries({ queryKey: ['schedules'] })
          queryClient.invalidateQueries({ queryKey: ['messages'] })
          queryClient.invalidateQueries({ queryKey: ['threads'] })
          try {
            window.dispatchEvent(new CustomEvent('byaan:monitor-test-complete'))
          } catch {
            // Dispatching the event is a best-effort hint for the open notebook
            // to re-fetch; failing it just means the user may need to refresh.
          }
        }
        setTimeout(refresh, 15_000)
        setTimeout(refresh, 45_000)
        setTimeout(refresh, 90_000)
      } else {
        showToast.error(result.error || 'Test run failed')
      }
    },
    onError: (error) => {
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

export type { ScheduleRead, ScheduleCreate, ScheduleUpdate, ScheduleTestResult }
