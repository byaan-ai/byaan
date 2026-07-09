import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useStore } from '../stores/useStore'
import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { Input } from '../components/ui/input'
import { Trash2, Pencil, Search, BookOpen, Download, Share2 } from 'lucide-react'
import { useNotebooks, useRenameNotebook, useDeleteNotebook } from '../hooks/useNotebooks'
import { ApiService, type Notebook } from '../services/api'
import CreateNotebook from '../components/CreateNotebook'
import ImportNotebookModal from '../components/ImportNotebookModal'
import ShareModal from '../components/ShareModal'
import ShareNotebookToFolderModal from '../components/ShareNotebookToFolderModal'
import { useScopes } from '../hooks/useScopes'
import { useAppConfig } from '../hooks/useAppConfig'

export default function NotebooksPage() {
  const navigate = useNavigate()
  const importShareId = useStore((s) => s.importShareId)
  const setImportShareId = useStore((s) => s.setImportShareId)
  const { canCreateNotebook, canEditNotebook, canDeleteNotebook, canImportNotebook, canShareExternally } = useScopes()
  const { isSelfHosted } = useAppConfig()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [notebookToDelete, setNotebookToDelete] = useState<Notebook | null>(null)
  const [editingNotebook, setEditingNotebook] = useState<Notebook | null>(null)
  const [editingName, setEditingName] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [showEditDialog, setShowEditDialog] = useState(false)
  const [importModalOpen, setImportModalOpen] = useState(false)
  const [shareModalOpen, setShareModalOpen] = useState(false)
  const [shareNotebookToFolderOpen, setShareNotebookToFolderOpen] = useState(false)
  const [notebookToShare, setNotebookToShare] = useState<Notebook | null>(null)
  const [latestDashboardVersion, setLatestDashboardVersion] = useState<number>(1)
  const [pendingShareNotebook, setPendingShareNotebook] = useState<Notebook | null>(null)

  // Auto-open import modal when importShareId is set (from deep link)
  useEffect(() => {
    if (importShareId) {
      setImportModalOpen(true)
    }
  }, [importShareId])

  // Open share modal after version is fetched and state is updated
  useEffect(() => {
    if (pendingShareNotebook) {
      setNotebookToShare(pendingShareNotebook)
      setShareModalOpen(true)
      setPendingShareNotebook(null)
    }
  }, [pendingShareNotebook, latestDashboardVersion])

  // Use React Query hooks
  const { data: notebooks = [], isLoading: loading, error } = useNotebooks()
  const renameMutation = useRenameNotebook()
  const deleteMutation = useDeleteNotebook()

  const formatTimeAgo = (dateString: string): string => {
    const date = new Date(dateString)
    const now = new Date()
    const diffInMs = now.getTime() - date.getTime()
    const diffInMinutes = Math.floor(diffInMs / (1000 * 60))
    const diffInHours = Math.floor(diffInMs / (1000 * 60 * 60))
    const diffInDays = Math.floor(diffInMs / (1000 * 60 * 60 * 24))
    const diffInMonths = Math.floor(diffInDays / 30)
    const diffInYears = Math.floor(diffInDays / 365)

    if (diffInMinutes < 1) return 'just now'
    if (diffInMinutes < 60) return `${diffInMinutes} minute${diffInMinutes > 1 ? 's' : ''} ago`
    if (diffInHours < 24) return `${diffInHours} hour${diffInHours > 1 ? 's' : ''} ago`
    if (diffInDays < 30) return `${diffInDays} day${diffInDays > 1 ? 's' : ''} ago`
    if (diffInMonths < 12) return `${diffInMonths} month${diffInMonths > 1 ? 's' : ''} ago`
    return `${diffInYears} year${diffInYears > 1 ? 's' : ''} ago`
  }

  // Filter and sort notebooks
  const displayNotebooks = notebooks
    .filter(notebook => {
      if (!searchQuery) return true
      const query = searchQuery.toLowerCase()
      return (
        notebook.notebook_name.toLowerCase().includes(query) ||
        (notebook.description && notebook.description.toLowerCase().includes(query))
      )
    })
    .sort((a, b) => {
      // Sort by most recent updated_at
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })

  const handleDeleteClick = (notebook: Notebook, event: React.MouseEvent) => {
    event.stopPropagation()
    setNotebookToDelete(notebook)
    setDeleteDialogOpen(true)
  }

  const confirmDelete = () => {
    if (!notebookToDelete) return

    deleteMutation.mutate(notebookToDelete.id, {
      onSuccess: () => {
        setDeleteDialogOpen(false)
        setNotebookToDelete(null)
      },
    })
  }

  const cancelDelete = () => {
    setDeleteDialogOpen(false)
    setNotebookToDelete(null)
  }

  const handleEditClick = (notebook: Notebook, event: React.MouseEvent) => {
    event.stopPropagation()
    setEditingNotebook(notebook)
    setEditingName(notebook.notebook_name)
    setShowEditDialog(true)
  }

  const handleShareClick = async (notebook: Notebook, event: React.MouseEvent) => {
    event.stopPropagation()

    // Fetch latest dashboard version for this notebook
    try {
      const versions = await ApiService.getNotebookDashboardVersions(notebook.id)
      if (versions.length > 0) {
        const latest = Math.max(...versions.map(v => v.version_num))
        setLatestDashboardVersion(latest)
      } else {
        setLatestDashboardVersion(1)
      }
    } catch (error) {
      console.error('Error loading dashboard versions:', error)
      setLatestDashboardVersion(1)
    }

    setPendingShareNotebook(notebook)
  }

  const handleRenameSubmit = () => {
    if (!editingNotebook || !editingName.trim()) return

    renameMutation.mutate(
      { notebookId: editingNotebook.id, newName: editingName.trim() },
      {
        onSuccess: () => {
          setShowEditDialog(false)
          setEditingNotebook(null)
          setEditingName('')
        }
      }
    )
  }

  const handleCardClick = (notebookId: string) => {
    navigate(`/notebook/${notebookId}`)
  }

  return (
    <div className="bg-[#0d0d0d] w-full h-full flex flex-col">
      {/* Header Section */}
      <div className="w-full px-8 pt-[50px] pb-8">
        <div className="max-w-[850px] mx-auto">
          {/* Title and Buttons */}
          <div className="flex items-center justify-between mb-8">
            <h1 className="text-2xl font-bold text-white tracking-tight">Notebooks</h1>
            <div className="flex items-center gap-2">
              {canImportNotebook && (
                <Button
                  variant="outline"
                  onClick={() => setImportModalOpen(true)}
                  className="font-medium px-4 py-2.5 rounded-md text-sm border-gray-700 text-gray-300 hover:bg-gray-800 hover:text-white"
                >
                  <Download className="w-4 h-4 mr-1.5" />
                  Import
                </Button>
              )}
              {canCreateNotebook && (
                <CreateNotebook
                  trigger={
                    <Button variant="brand-primary" className="font-medium px-5 py-2.5 rounded-md text-sm">
                      + New notebook
                    </Button>
                  }
                />
              )}
            </div>
          </div>

          {/* Search Bar */}
          <div className="relative">
            <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-500" />
            <Input
              type="text"
              placeholder="Search notebooks..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-12 pr-4 py-6 bg-transparent border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-brand-orange focus:ring-1 focus:ring-brand-orange/50"
            />
          </div>
        </div>
      </div>

      {/* Scrollable Content Section */}
      <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
        <div className="w-full px-8 pb-6">
          {/* Error Message */}
          {error && (
            <div className="bg-red-900/20 border border-red-500 text-red-400 px-4 py-3 rounded-md mb-6">
              {error.message || 'An error occurred'}
            </div>
          )}

          {/* Loading State */}
          {loading ? (
            <div className="text-center py-12">
              <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto mb-4"></div>
              <p className="text-gray-400">Loading notebooks...</p>
            </div>
          ) : (
            <>
              {/* Empty State */}
              {displayNotebooks.length === 0 ? (
                <div className="max-w-[850px] mx-auto">
                  <Card className="p-12 text-center bg-[#1a1a1a] border-gray-800">
                    <div className="max-w-md mx-auto">
                      <div className="w-16 h-16 bg-brand-orange/10 rounded-full flex items-center justify-center mx-auto mb-4">
                        <BookOpen className="w-8 h-8 text-brand-orange" />
                      </div>
                      <h3 className="text-xl font-semibold text-white mb-2">No Notebooks</h3>
                      <p className="text-gray-400 mb-6">
                        Get started by creating your first notebook. Organize your data analysis and queries in notebooks.
                      </p>
                    </div>
                  </Card>
                </div>
              ) : (
                <>
                  {/* Notebook Cards */}
                  <div className="max-w-[850px] mx-auto grid grid-cols-1 md:grid-cols-2 gap-6">
                    {displayNotebooks.map(notebook => {
                      const timeAgo = formatTimeAgo(notebook.updated_at)
                      const isSlack = notebook.source === 'slack'
                      const displayName = isSlack ? (notebook.slack_thread_title || notebook.notebook_name) : notebook.notebook_name
                      return (
                        <Card
                          key={notebook.id}
                          className="p-6 bg-[#1a1a1a] border-gray-800 hover:border-gray-700 transition-colors cursor-pointer"
                          onClick={() => handleCardClick(notebook.id)}
                        >
                          <div className="flex items-start justify-between mb-3 gap-4">
                            <div className="flex-1 min-w-0">
                              {/* Notebook Name */}
                              <div className="flex items-center gap-2 mb-2 flex-wrap">
                                {isSlack && (
                                  <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-500/10 text-purple-400 border border-purple-500/20">
                                    Slack
                                  </span>
                                )}
                                <h3
                                  className="text-lg font-normal text-white break-words"
                                  title={displayName.length > 40 ? displayName : undefined}
                                >
                                  {displayName.length > 40
                                    ? `${displayName.substring(0, 40)}...`
                                    : displayName}
                                </h3>
                              </div>

                              {/* Description */}
                              <p className="text-sm text-gray-400 mb-3">
                                {notebook.description || 'No description'}
                              </p>

                              {/* Timestamp */}
                              <p className="text-xs text-gray-500">
                                Updated {timeAgo}
                              </p>
                            </div>

                            {/* Action Buttons */}
                            <div className="flex gap-2 shrink-0">
                              {isSelfHosted && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={(e) => handleShareClick(notebook, e)}
                                  disabled={renameMutation.isPending || deleteMutation.isPending}
                                  className="text-gray-400 hover:text-white hover:bg-gray-800"
                                  title="Share notebook"
                                >
                                  <Share2 className="w-4 h-4" />
                                </Button>
                              )}
                              {canEditNotebook(notebook.created_by) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={(e) => handleEditClick(notebook, e)}
                                  disabled={renameMutation.isPending || deleteMutation.isPending}
                                  className="text-gray-400 hover:text-white hover:bg-gray-800"
                                  title="Edit notebook"
                                >
                                  <Pencil className="w-4 h-4" />
                                </Button>
                              )}
                              {canDeleteNotebook(notebook.created_by) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={(e) => handleDeleteClick(notebook, e)}
                                  disabled={renameMutation.isPending || deleteMutation.isPending}
                                  className="text-gray-400 hover:text-red-400 hover:bg-gray-800"
                                  title="Delete notebook"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </Button>
                              )}
                            </div>
                          </div>
                        </Card>
                      )
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* Edit/Rename Dialog */}
      <Dialog open={showEditDialog} onOpenChange={(open) => {
        if (!open && renameMutation.isPending) return
        setShowEditDialog(open)
        if (!open) {
          setEditingNotebook(null)
          setEditingName('')
        }
      }}>
        <DialogContent className="max-w-md bg-[#2a2a2a] border-[#444444]">
          <DialogHeader>
            <DialogTitle className="text-white">Rename Notebook</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div>
              <label htmlFor="notebook-name" className="block text-sm font-medium text-white mb-2">
                Notebook Name
              </label>
              <Input
                id="notebook-name"
                value={editingName}
                onChange={(e) => setEditingName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleRenameSubmit()
                  }
                }}
                placeholder="Enter notebook name"
                className="bg-[#1a1a1a] border-[#555555] text-white focus:border-brand-orange focus:ring-1 focus:ring-brand-orange/50"
                autoFocus
              />
            </div>

            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setShowEditDialog(false)
                  setEditingNotebook(null)
                  setEditingName('')
                }}
                disabled={renameMutation.isPending}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                variant="brand-primary"
                onClick={handleRenameSubmit}
                disabled={renameMutation.isPending || !editingName.trim()}
              >
                {renameMutation.isPending ? 'Renaming...' : 'Rename'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={(open) => {
        if (!open && deleteMutation.isPending) return
        setDeleteDialogOpen(open)
      }}>
        <DialogContent className="max-w-md bg-[#2a2a2a] border-[#444444]">
          <DialogHeader>
            <DialogTitle className="text-white">Delete Notebook?</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <p className="text-sm text-[#aaaaaa]">
              This action will permanently delete <span className="font-semibold text-white">"{notebookToDelete?.notebook_name && notebookToDelete.notebook_name.length > 40 ? notebookToDelete.notebook_name.slice(0, 40) + '...' : notebookToDelete?.notebook_name}"</span> notebook and all its contents.
            </p>

            <div className="flex justify-end gap-2 mt-6">
              <Button
                variant="outline"
                onClick={cancelDelete}
                disabled={deleteMutation.isPending}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                onClick={confirmDelete}
                disabled={deleteMutation.isPending}
                className="bg-red-800 hover:bg-red-900 text-white"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Import Notebook Modal */}
      {canImportNotebook && (
        <ImportNotebookModal
          open={importModalOpen}
          onOpenChange={(open) => {
            setImportModalOpen(open)
            // Clear the importShareId when modal closes
            if (!open && importShareId) {
              setImportShareId(null)
            }
          }}
          initialShareId={importShareId || undefined}
        />
      )}

      {/* Share Modal (for external sharing and folder sharing) */}
      {notebookToShare && (
        <>
          <ShareModal
            open={shareModalOpen}
            onOpenChange={setShareModalOpen}
            notebookId={notebookToShare.id}
            version={latestDashboardVersion}
            onShareNotebookToFolder={isSelfHosted ? () => {
              setShareModalOpen(false)
              setShareNotebookToFolderOpen(true)
            } : undefined}
            canShareExternally={canShareExternally}
          />

          <ShareNotebookToFolderModal
            open={shareNotebookToFolderOpen}
            onOpenChange={setShareNotebookToFolderOpen}
            notebookId={notebookToShare.id}
            notebookName={notebookToShare.notebook_name}
          />
        </>
      )}
    </div>
  )
}
