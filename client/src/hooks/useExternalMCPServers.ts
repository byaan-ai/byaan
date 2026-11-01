import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiService } from '../services/api'
import type { ExternalMCPServerPayload } from '../services/api'
import { showToast } from '../utils/toast'

const QUERY_KEY = ['external-mcp-servers'] as const

export const useExternalMCPServers = () =>
  useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      const response = await ApiService.listExternalMCPServers()
      return response.items
    },
  })

export const useCreateExternalMCPServer = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ExternalMCPServerPayload) => ApiService.createExternalMCPServer(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      showToast.success('External MCP server created')
    },
    onError: (error: Error) => {
      showToast.error(error.message || 'Failed to create external MCP server')
    },
  })
}

export const useUpdateExternalMCPServer = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<ExternalMCPServerPayload> }) =>
      ApiService.updateExternalMCPServer(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      showToast.success('External MCP server updated')
    },
    onError: (error: Error) => {
      showToast.error(error.message || 'Failed to update external MCP server')
    },
  })
}

export const useDeleteExternalMCPServer = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => ApiService.deleteExternalMCPServer(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      showToast.success('External MCP server deleted')
    },
    onError: (error: Error) => {
      showToast.error(error.message || 'Failed to delete external MCP server')
    },
  })
}

export const useTestExternalMCPServer = () =>
  useMutation({
    mutationFn: (id: string) => ApiService.testExternalMCPServer(id),
  })
