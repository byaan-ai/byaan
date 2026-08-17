import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiService } from '../services/api'
import type { NotebookCreateRequest } from '../services/api'
import { useStore } from '../stores/useStore'
import { showToast } from '../utils/toast'

export const useNotebooks = () => {
  const setNotebooks = useStore(state => state.setNotebooks)
  const isAuthenticated = useStore(state => state.isAuthenticated)
  const tenants = useStore(state => state.tenants)
  const hasPendingInvitation = typeof window !== 'undefined' && localStorage.getItem('pendingInvitationToken') !== null

  return useQuery({
    queryKey: ['notebooks'],
    queryFn: async () => {
      const response = await ApiService.listNotebooks()
      setNotebooks(response.items)
      return response.items
    },
    // Only fetch notebooks if user is authenticated, has tenants, and no pending invitation
    enabled: isAuthenticated && tenants.length > 0 && !hasPendingInvitation,
    retry: false,
  })
}

export const useCreateNotebook = () => {
  const queryClient = useQueryClient()
  const addNotebook = useStore(state => state.addNotebook)
  
  return useMutation({
    mutationFn: (data: NotebookCreateRequest) => ApiService.createNotebook(data),
    onSuccess: (notebook) => {
      queryClient.invalidateQueries({ queryKey: ['notebooks'] })
      addNotebook(notebook)
      showToast.success('Notebook created successfully')
    },
    onError: (error) => {
      console.error('Error creating notebook:', error)
      showToast.error('Failed to create notebook')
    },
  })
}

export const useRenameNotebook = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ notebookId, newName }: { notebookId: string; newName: string }) =>
      ApiService.renameNotebook(notebookId, newName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notebooks'] })
      showToast.success('Notebook renamed successfully')
    },
    onError: (error) => {
      console.error('Error renaming notebook:', error)
      showToast.error('Failed to rename notebook')
    },
  })
}

export const useDeleteNotebook = () => {
  const queryClient = useQueryClient()
  const deleteNotebook = useStore(state => state.deleteNotebook)

  return useMutation({
    mutationFn: (notebookId: string) => ApiService.deleteNotebook(notebookId),
    onSuccess: (_, notebookId) => {
      queryClient.invalidateQueries({ queryKey: ['notebooks'] })
      deleteNotebook(notebookId)
      showToast.success('Notebook deleted successfully')
    },
    onError: (error) => {
      console.error('Error deleting notebook:', error)
      showToast.error('Failed to delete notebook')
    },
  })
}

export const useNotebookThreads = (notebookId: string | null) => {
  return useQuery({
    queryKey: ['threads', notebookId],
    queryFn: async () => {
      if (!notebookId) return []
      const threads = await ApiService.getNotebookThreads(notebookId)
      return threads
    },
    enabled: !!notebookId,
  })
}
