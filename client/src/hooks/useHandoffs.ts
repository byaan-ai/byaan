import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  HandoffsApi,
  TERMINAL_HANDOFF_STATUSES,
  isActiveHandoff,
  type HandoffDetail,
  type HandoffEvent,
} from '../services/handoffs'

export const handoffKeys = {
  all: ['handoffs'] as const,
  lists: ['handoffs', 'list'] as const,
  page: ['handoffs', 'list', 'page'] as const,
  detail: (id: string) => ['handoffs', 'detail', id] as const,
  requirements: ['handoffs', 'requirements'] as const,
  repoSettings: (repoId: string) => ['handoffs', 'repo-settings', repoId] as const,
}

export function refreshHandoff(queryClient: QueryClient, detail: HandoffDetail) {
  queryClient.setQueryData(handoffKeys.detail(detail.id), detail)
  queryClient.invalidateQueries({ queryKey: handoffKeys.lists })
}

export function useHandoff(id: string) {
  return useQuery({
    queryKey: handoffKeys.detail(id),
    queryFn: () => HandoffsApi.get(id),
    refetchInterval: (query) => {
      const handoff = query.state.data as HandoffDetail | undefined
      return handoff && isActiveHandoff(handoff.status) ? 5000 : false
    },
    staleTime: 4000,
  })
}

export function useHandoffRequirements(refetchOnWindowFocus: boolean | 'always' = false) {
  return useQuery({
    queryKey: handoffKeys.requirements,
    queryFn: HandoffsApi.requirements,
    staleTime: 60000,
    refetchOnWindowFocus,
  })
}

export function useHandoffAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, action }: { id: string; action: 'start' | 'dismiss' | 'cancel' | 'takeOver' | 'restart' }) =>
      HandoffsApi[action](id),
    onSuccess: (detail) => refreshHandoff(queryClient, detail),
  })
}

export function useHandoffMessage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) => HandoffsApi.message(id, text),
    onSuccess: (_result, { id }) => {
      queryClient.invalidateQueries({ queryKey: handoffKeys.detail(id) })
      queryClient.invalidateQueries({ queryKey: handoffKeys.lists })
    },
  })
}

export const HANDOFF_REFRESH_EVENT = 'byaan:handoff-messages-refresh'
const lastNotifiedSeq = new Map<string, number>()

const CHAT_MESSAGE_STATUSES = new Set<string>(['queued', 'fixing', 'ci_running', ...TERMINAL_HANDOFF_STATUSES])

const addsChatMessage = (event: HandoffEvent) =>
  event.kind === 'question' ||
  event.kind === 'review' ||
  (event.kind === 'status' && CHAT_MESSAGE_STATUSES.has(String(event.data?.status)))

export function useHandoffMessagesRefresh(handoff: HandoffDetail | undefined) {
  const posted = handoff?.events.filter(addsChatMessage)
  const seq = posted?.length ? posted[posted.length - 1].seq : 0
  const id = handoff?.id
  const finished = !!handoff && !isActiveHandoff(handoff.status) && handoff.status !== 'proposed'
  useEffect(() => {
    if (!id) return
    const previous = lastNotifiedSeq.get(id)
    if (previous !== undefined && seq > previous) {
      window.dispatchEvent(new CustomEvent(HANDOFF_REFRESH_EVENT, { detail: { handoffId: id } }))
    }
    if (finished) lastNotifiedSeq.delete(id)
    else lastNotifiedSeq.set(id, Math.max(previous ?? 0, seq))
  }, [id, seq, finished])
}
