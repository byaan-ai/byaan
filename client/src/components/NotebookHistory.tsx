import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { MoreVertical, Edit3, Trash2, Share2 } from 'lucide-react'
import { useNotebooks, useRenameNotebook, useDeleteNotebook } from '../hooks/useNotebooks'
import { ConfirmationModal } from '../components/ConfirmationModal'
import ShareModal from '../components/ShareModal'
import ShareNotebookToFolderModal from '../components/ShareNotebookToFolderModal'
import ShareDashboardToFolderModal from '../components/ShareDashboardToFolderModal'
import { ApiService, type Notebook } from '../services/api'
import { useScopes } from '../hooks/useScopes'

interface NotebookHistoryProps {
  onNotebookClick?: () => void
}

export default function NotebookHistory({ onNotebookClick }: NotebookHistoryProps = {}) {
  const location = useLocation()
  const navigate = useNavigate()
  const { data: notebooks = [], isLoading } = useNotebooks()
  const renameNotebookMutation = useRenameNotebook()
  const deleteNotebookMutation = useDeleteNotebook()
  const { canShareExternally } = useScopes()

  // Track notebook selection history for smart navigation
  const notebookHistoryRef = useRef<string[]>([])
  const currentNotebookId = location.pathname.match(/\/notebook\/([^/]+)/)?.[1]

  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [originalName, setOriginalName] = useState('')
  const [deleteProject, setDeleteProject] = useState<any>(null)
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false)
  const [shareModalOpen, setShareModalOpen] = useState(false)
  const [shareNotebookToFolderOpen, setShareNotebookToFolderOpen] = useState(false)
  const [shareDashboardToFolderOpen, setShareDashboardToFolderOpen] = useState(false)
  const [notebookToShare, setNotebookToShare] = useState<Notebook | null>(null)
  const [latestDashboardVersion, setLatestDashboardVersion] = useState<number>(1)
  const [latestDashboardId, setLatestDashboardId] = useState<string | undefined>(undefined)
  const [pendingShareNotebook, setPendingShareNotebook] = useState<Notebook | null>(null)

  // Track notebook selection history for smart navigation
  useEffect(() => {
    if (currentNotebookId && notebooks.some(nb => nb.id === currentNotebookId)) {
      const history = notebookHistoryRef.current
      // Remove the current notebook from history if it exists
      const filteredHistory = history.filter(id => id !== currentNotebookId)
      // Add current notebook to the front of history
      notebookHistoryRef.current = [currentNotebookId, ...filteredHistory].slice(0, 10) // Keep last 10
    }
  }, [currentNotebookId, notebooks])

  // Open share modal after version is fetched and state is updated
  useEffect(() => {
    if (pendingShareNotebook) {
      setNotebookToShare(pendingShareNotebook)
      setShareModalOpen(true)
      setPendingShareNotebook(null)
    }
  }, [pendingShareNotebook, latestDashboardVersion])

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Element
      // Don't close if clicking on menu button or menu itself
      if (!target.closest('[data-menu]') && !target.closest('[data-menu-button]')) {
        setOpenMenuId(null)
      }
    }

    if (openMenuId) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [openMenuId])

  const handleMenuToggle = (projectId: string, event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    setOpenMenuId(openMenuId === projectId ? null : projectId)
  }

  const handleRename = (notebook: any, event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    setEditingProjectId(notebook.id)
    setEditingName(notebook.notebook_name)
    setOriginalName(notebook.notebook_name)
    setOpenMenuId(null)
  }

  const handleDelete = (notebook: any, event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    setDeleteProject(notebook)
    setIsDeleteModalOpen(true)
    setOpenMenuId(null)
  }

  const handleShare = async (notebook: Notebook, event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()

    // Fetch latest dashboard version for this notebook
    try {
      const versions = await ApiService.getNotebookDashboardVersions(notebook.id)
      if (versions.length > 0) {
        const sortedVersions = versions.sort((a, b) => b.version_num - a.version_num)
        setLatestDashboardVersion(sortedVersions[0].version_num)
        setLatestDashboardId(sortedVersions[0].id)
      } else {
        setLatestDashboardVersion(1)
        setLatestDashboardId(undefined)
      }
    } catch (error) {
      console.error('Error loading dashboard versions:', error)
      setLatestDashboardVersion(1)
      setLatestDashboardId(undefined)
    }

    setPendingShareNotebook(notebook)
    setOpenMenuId(null)
  }

  const handleRenameSubmit = (notebookId: string, event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      const trimmedName = editingName.trim()
      if (trimmedName !== '' && trimmedName !== originalName) {
        renameNotebookMutation.mutate({ notebookId, newName: trimmedName })
      }
      setEditingProjectId(null)
      setEditingName('')
      setOriginalName('')
    } else if (event.key === 'Escape') {
      setEditingProjectId(null)
      setEditingName('')
      setOriginalName('')
    }
  }

  const handleRenameBlur = (notebookId: string) => {
    const trimmedName = editingName.trim()
    if (trimmedName !== '' && trimmedName !== originalName) {
      renameNotebookMutation.mutate({ notebookId, newName: trimmedName })
    }
    setEditingProjectId(null)
    setEditingName('')
    setOriginalName('')
  }

  const handleDeleteConfirm = () => {
    if (deleteProject) {
      const deletingNotebookId = deleteProject.id
      const isCurrentlyViewingDeletedNotebook = currentNotebookId === deletingNotebookId

      const findFallbackNotebook = () => {
        if (isCurrentlyViewingDeletedNotebook) {
          const sortedNotebooks = [...notebooks].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
          )

          const deletedIndex = sortedNotebooks.findIndex(nb => nb.id === deletingNotebookId)

          if (deletedIndex !== -1 && deletedIndex < sortedNotebooks.length - 1) {
            return sortedNotebooks[deletedIndex + 1].id
          }

          if (deletedIndex > 0) {
            return sortedNotebooks[deletedIndex - 1].id
          }

          return null
        }
        return currentNotebookId || null
      }

      const fallbackNotebookId = findFallbackNotebook()

      deleteNotebookMutation.mutate(deletingNotebookId, {
        onSuccess: () => {
          notebookHistoryRef.current = notebookHistoryRef.current.filter(id => id !== deletingNotebookId)

          if (isCurrentlyViewingDeletedNotebook) {
            if (fallbackNotebookId) {
              navigate(`/notebook/${fallbackNotebookId}`)
            } else {
              navigate('/')
            }
          }

          setDeleteProject(null)
          setIsDeleteModalOpen(false)
        }
      })
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-2 px-3">
        {[...Array(3)].map((_, i) => (
          <div
            key={i}
            className="animate-pulse bg-[#333333] h-8 rounded-md"
          />
        ))}
      </div>
    )
  }

  if (notebooks.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center text-gray-500 text-xs">
          No notebooks yet
        </div>
      </div>
    )
  }

  const sortedNotebooks = [...notebooks].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )

  return (
    <div className="flex flex-col h-full">
      {/* Scrollable Projects Section */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="space-y-0.5">
          {sortedNotebooks.map((notebook) => (
                    <div
                      key={notebook.id}
                      className={`relative group flex items-center gap-1 px-3 py-1 transition-colors ${
                        openMenuId === notebook.id
                          ? 'bg-[#2a2a2a] text-white'
                          : location.pathname === `/notebook/${notebook.id}` || location.pathname === `/notebook/${notebook.id}/preview`
                          ? 'bg-[#2a2a2a] text-white'
                          : 'text-gray-300 hover:bg-[#2a2a2a] hover:text-white'
                      }`}
                    >
                      <Link
                        to={`/notebook/${notebook.id}`}
                        className="flex-1 min-w-0"
                        onClick={(e) => {
                          if (openMenuId === notebook.id) {
                            e.preventDefault()
                            setOpenMenuId(null)
                          } else if (onNotebookClick) {
                            onNotebookClick()
                          }
                        }}
                      >
                        {editingProjectId === notebook.id ? (
                          <input
                            type="text"
                            value={editingName}
                            onChange={(e) => setEditingName(e.target.value)}
                            onKeyDown={(e) => handleRenameSubmit(notebook.id, e)}
                            onBlur={() => handleRenameBlur(notebook.id)}
                            disabled={renameNotebookMutation.isPending}
                            className="bg-[#555555] text-white text-sm font-medium px-2 py-1 rounded w-full focus:outline-none focus:ring-1 focus:ring-blue-500"
                            autoFocus
                            onClick={(e) => e.preventDefault()}
                          />
                        ) : (
                          <div className="flex items-center gap-1.5 min-w-0">
                            {notebook.source === 'slack' && (
                              <span className="flex-shrink-0 px-1 py-0.5 rounded text-[9px] font-medium bg-purple-500/10 text-purple-400 border border-purple-500/20">
                                Slack
                              </span>
                            )}
                            <div
                              className="text-sm font-medium truncate"
                              title={notebook.source === 'slack' ? (notebook.slack_thread_title || notebook.notebook_name) : notebook.notebook_name}
                            >
                              {notebook.source === 'slack' ? (notebook.slack_thread_title || notebook.notebook_name) : notebook.notebook_name}
                            </div>
                          </div>
                        )}
                      </Link>

                      {/* Three dots menu */}
                      <div className="relative">
                        <button
                          data-menu-button
                          onClick={(e) => handleMenuToggle(notebook.id, e)}
                          disabled={renameNotebookMutation.isPending || deleteNotebookMutation.isPending}
                          className={`transition-opacity p-1 hover:bg-[#555555] rounded disabled:opacity-30 disabled:cursor-not-allowed ${
                            openMenuId === notebook.id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                          }`}
                        >
                          <MoreVertical className="h-4 w-4" />
                        </button>

                        {/* Dropdown menu */}
                        {openMenuId === notebook.id && (
                          <div
                            data-menu
                            className="absolute right-0 top-full mt-1 bg-[#2a2a2a] border border-[#444444] rounded-lg shadow-lg py-1 z-50 min-w-[120px]"
                          >
                            <button
                              onClick={(e) => handleShare(notebook, e)}
                              disabled={renameNotebookMutation.isPending || deleteNotebookMutation.isPending}
                              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-300 hover:bg-[#333333] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              <Share2 className="h-4 w-4" />
                              Share
                            </button>
                            <button
                              onClick={(e) => handleRename(notebook, e)}
                              disabled={renameNotebookMutation.isPending || deleteNotebookMutation.isPending}
                              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-300 hover:bg-[#333333] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              <Edit3 className="h-4 w-4" />
                              Rename
                            </button>
                            <button
                              onClick={(e) => handleDelete(notebook, e)}
                              disabled={renameNotebookMutation.isPending || deleteNotebookMutation.isPending}
                              className="flex items-center gap-2 w-full px-3 py-2 text-sm text-red-400 hover:bg-[#333333] hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              <Trash2 className="h-4 w-4" />
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
          ))}
        </div>
      </div>


      {/* Delete Notebook Modal */}
      <ConfirmationModal
        isOpen={isDeleteModalOpen}
        onClose={() => {
          if (!deleteNotebookMutation.isPending) {
            setIsDeleteModalOpen(false)
            setDeleteProject(null)
          }
        }}
        onConfirm={handleDeleteConfirm}
        title="Delete Notebook?"
        message={
          <>
            This action will permanently delete <span className="font-semibold text-white">"{deleteProject?.notebook_name && deleteProject.notebook_name.length > 40 ? deleteProject.notebook_name.slice(0, 40) + '...' : deleteProject?.notebook_name}"</span> notebook.
          </>
        }
        confirmText="Delete"
        type="danger"
        loading={deleteNotebookMutation.isPending}
      />

      {/* Share Modal (for external sharing and folder sharing) */}
      {notebookToShare && (
        <>
          <ShareModal
            open={shareModalOpen}
            onOpenChange={setShareModalOpen}
            notebookId={notebookToShare.id}
            dashboardId={latestDashboardId}
            version={latestDashboardVersion}
            onShareNotebookToFolder={() => {
              setShareModalOpen(false)
              setShareNotebookToFolderOpen(true)
            }}
            onShareDashboardToFolder={() => {
              setShareModalOpen(false)
              setShareDashboardToFolderOpen(true)
            }}
            canShareExternally={canShareExternally}
          />

          <ShareNotebookToFolderModal
            open={shareNotebookToFolderOpen}
            onOpenChange={setShareNotebookToFolderOpen}
            notebookId={notebookToShare.id}
            notebookName={notebookToShare.notebook_name}
          />

          {latestDashboardId && (
            <ShareDashboardToFolderModal
              open={shareDashboardToFolderOpen}
              onOpenChange={setShareDashboardToFolderOpen}
              dashboardId={latestDashboardId}
              dashboardVersion={latestDashboardVersion}
              notebookId={notebookToShare.id}
              notebookName={notebookToShare.notebook_name}
            />
          )}
        </>
      )}
    </div>
  )
}
