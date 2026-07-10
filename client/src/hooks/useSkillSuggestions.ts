import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { SkillSuggestionsService, type SuggestionStatus } from '../services/skillSuggestions'
import { useStore } from '../stores/useStore'
import { showToast } from '../utils/toast'

export const useSkillSuggestions = (status?: SuggestionStatus) => {
  const isAuthenticated = useStore(state => state.isAuthenticated)
  const tenants = useStore(state => state.tenants)

  return useQuery({
    queryKey: ['skill-suggestions', status ?? 'all'],
    queryFn: () => SkillSuggestionsService.list(status),
    enabled: isAuthenticated && tenants.length > 0,
    retry: false,
  })
}

export const usePendingSuggestionCount = () => {
  const isAuthenticated = useStore(state => state.isAuthenticated)
  const tenants = useStore(state => state.tenants)

  return useQuery({
    queryKey: ['skill-suggestions', 'pending-count'],
    queryFn: () => SkillSuggestionsService.getPendingCount(),
    enabled: isAuthenticated && tenants.length > 0,
    refetchInterval: 60000,
    retry: false,
  })
}

export const useApproveSuggestion = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, finalInstructions }: { id: string; finalInstructions?: string }) =>
      SkillSuggestionsService.approve(id, finalInstructions),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['skill-suggestions'] })
      if (result.new_version !== null) {
        showToast.success(`Applied as v${result.new_version}`)
      } else {
        showToast.success('Suggestion approved')
      }
    },
    onError: (error) => {
      showToast.error(error instanceof Error ? error.message : 'Failed to approve suggestion')
    },
  })
}

export const useRejectSuggestion = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      SkillSuggestionsService.reject(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skill-suggestions'] })
      showToast.success('Suggestion rejected')
    },
    onError: (error) => {
      showToast.error(error instanceof Error ? error.message : 'Failed to reject suggestion')
    },
  })
}
