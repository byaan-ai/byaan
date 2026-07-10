import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SkillLoopSettingsService, type SkillLoopSettingsUpdate } from '../services/skillLoopSettings'
import { useStore } from '../stores/useStore'
import { showToast } from '../utils/toast'

const SETTINGS_QUERY_KEY = ['skill-loop-settings']

export const useSkillLoopSettings = () => {
  const isAuthenticated = useStore(state => state.isAuthenticated)
  const tenants = useStore(state => state.tenants)

  return useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: () => SkillLoopSettingsService.get(),
    enabled: isAuthenticated && tenants.length > 0,
    retry: false,
  })
}

export const useUpdateSkillLoopSettings = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (update: SkillLoopSettingsUpdate) => SkillLoopSettingsService.update(update),
    onSuccess: (data) => {
      queryClient.setQueryData(SETTINGS_QUERY_KEY, data)
      queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY })
      showToast.success('Settings saved')
    },
    onError: (error) => {
      showToast.error(error instanceof Error ? error.message : 'Failed to save settings')
    },
  })
}
