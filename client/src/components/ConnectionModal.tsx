"use client"

import { useState, useEffect } from "react"
import { Button } from "./ui/button"
import { Dialog, DialogTrigger } from "./ui/dialog"
import { Database, ChevronLeft, ChevronRight } from "lucide-react"
import { showToast } from "../utils/toast"
import { toast } from "react-toastify"
import { useUpdateConnection, useConnectNotebook, useAssociateNotebookConnection } from "../hooks/useConnections"
import { useDatasources, useCreateDBConnection, useUploadMultipleFiles, useUploadFromURL } from "../hooks/useDBConnections"
import { ApiService, type ConnectionCreateRequest } from "../services/api"
import { useStore } from "../stores/useStore"
import { DatabaseConnectionDialog } from "./DatabaseConnectionDialog"

interface ConnectionConfig {
  type: 'pg' | 'mongo' | 'mysql' | 'sqlite' | 'mssql' | 'csv' | 'excel' | 'parquet' | 'json'
  name: string
  host: string
  port: string
  database: string
  user: string
  password: string
  connectionString: string
  file_path?: string
}

interface NotebookConnection {
  id: string
  type: string
  name: string | null
  connection_obj: any
  created_at: string
  notebook_connection_id: string
  connection_id?: string
}

interface ConnectionModalProps {
  onConnectionChange: (config: ConnectionConfig | null) => void
  notebookConnection?: NotebookConnection | null
  notebookId?: string
  autoOpen?: boolean
  onConnectionUpdate?: () => void
  requireConnection?: boolean
  onCancel?: () => void
  // Multi-db props
  multiSelect?: boolean
  notebookConnections?: any[]
  showManageView?: boolean
}

export function ConnectionModal({
  onConnectionChange,
  notebookConnection,
  notebookId,
  autoOpen,
  onConnectionUpdate,
  requireConnection,
  onCancel,
  // Multi-db props with defaults
  multiSelect = false,
  notebookConnections = [],
  showManageView = false
}: ConnectionModalProps) {
  const { data: datasourcesResponse } = useDatasources()
  const allDatasources = datasourcesResponse?.items || []
  const updateConnectionMutation = useUpdateConnection()
  const connectNotebookMutation = useConnectNotebook()
  const associateNotebookMutation = useAssociateNotebookConnection()
  const createMutation = useCreateDBConnection()
  const uploadMultipleFilesMutation = useUploadMultipleFiles()
  const uploadFromURLMutation = useUploadFromURL()
  const { cacheSchema } = useStore()
  const [isOpen, setIsOpen] = useState(false)

  // Include mutation loading state in overall loading state
  const isUpdatingConnection = updateConnectionMutation.isPending || connectNotebookMutation.isPending || associateNotebookMutation.isPending || createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending

  // Dialog mode: 'select' for choosing previous connections, 'create' for new connections, 'manage' for managing existing
  const [dialogMode, setDialogMode] = useState<'select' | 'create' | 'manage'>('select')
  const [selectedConnectionId, setSelectedConnectionId] = useState<string>('')
  // Multi-db: array for multi-select
  const [selectedConnectionIds, setSelectedConnectionIds] = useState<string[]>([])

  // Filter out already connected datasources for multi-db
  const availableDatasources = multiSelect
    ? allDatasources.filter(ds => {
        const connectedIds = notebookConnections.map(conn => conn.connection_id || conn.id).filter(Boolean)
        return !connectedIds.includes(ds.id)
      })
    : allDatasources

  // Set default view based on available datasources and notebook connection status
  useEffect(() => {
    if (showManageView && notebookConnections.length > 0) {
      // If showing manage view (notebook with existing connections)
      setDialogMode('manage')
    } else if (notebookConnection) {
      // If notebook already has a connection, don't show dialog (handled elsewhere)
      setDialogMode('create')
    } else if (allDatasources.length > 0) {
      // If notebook not connected but datasources exist, show previous datasources list
      setDialogMode('select')
    } else {
      // No datasources, show create form
      setDialogMode('create')
    }
  }, [allDatasources.length, notebookConnection, showManageView, notebookConnections.length])

  useEffect(() => {
    if (autoOpen) {
      setIsOpen(true)
    }
  }, [autoOpen])

  useEffect(() => {
    if (availableDatasources.length > 0 && dialogMode === 'select' && !selectedConnectionId && !multiSelect) {
      // Sort to get the most recent one first (only for single select)
      const firstDatasource = [...availableDatasources].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )[0]
      if (firstDatasource) {
        setSelectedConnectionId(firstDatasource.id)
      }
    }
  }, [availableDatasources, dialogMode, selectedConnectionId, multiSelect])


  // Handle connection selection (for select mode)
  const handleConnectionSelect = async () => {
    if (!notebookId) return

    // Multi-db: handle multiple selections
    if (multiSelect && selectedConnectionIds.length > 0) {
      const loadingToastId = showToast.loading(`Connecting ${selectedConnectionIds.length} datasource(s)...`)

      try {
        // Accepts Dataset IDs directly (handles both connections and file datasets)
        await ApiService.batchAssociateDatasetsWithNotebook(notebookId, selectedConnectionIds)

        toast.dismiss(loadingToastId)
        showToast.success(`${selectedConnectionIds.length} datasource(s) added successfully`)

        if (onConnectionUpdate) {
          onConnectionUpdate()
        }

        setSelectedConnectionIds([])
        setIsOpen(false)
      } catch (error) {
        console.error('Failed to connect:', error)
        toast.dismiss(loadingToastId)
        showToast.error('Failed to connect to datasources')
      }
      return
    }

    // Single selection
    if (!selectedConnectionId) return

    const loadingToastId = showToast.loading('Connecting to datasource...')

    try {
      const selectedDatasource = allDatasources.find(d => d.id === selectedConnectionId)

      if (selectedDatasource?.source_type === 'connection') {
        // Database connection - use associate endpoint
        await associateNotebookMutation.mutateAsync({
          notebookId,
          data: { connection_id: selectedDatasource.connection_id! }
        })
      } else if (selectedDatasource?.source_type === 'dataset') {
        // File dataset - use dataset association endpoint
        await ApiService.associateDatasetWithNotebook(selectedConnectionId, notebookId)
      } else {
        throw new Error('Invalid datasource type')
      }

      toast.dismiss(loadingToastId)
      showToast.success('Connection created successfully')

      if (onConnectionUpdate) {
        onConnectionUpdate()
      }

      setIsOpen(false)
    } catch (error) {
      console.error('Failed to connect:', error)
      toast.dismiss(loadingToastId)
      showToast.error('Failed to connect to datasource')
    }
  }

  // Handle connection creation (for create mode)
  const handleCreateConnection = async (data: {
    type: any
    name: string
    connectionObj?: any
    files?: File[]
    aliases?: Record<string, string>
    fileType?: 'csv' | 'excel' | 'parquet' | 'json'
    urls?: string[]
  }) => {
    const loadingToastId = showToast.loading('Creating connection...')

    try {
      if (data.type === 'upload') {
        // File upload
        await uploadMultipleFilesMutation.mutateAsync({
          files: data.files!,
          name: data.name,
          aliases: data.aliases!,
          fileType: data.fileType!
        })

        // If this is for a notebook, associate it
        if (notebookId) {
          // The mutation should return the created dataset ID, but for now we'll refresh
          if (onConnectionUpdate) {
            onConnectionUpdate()
          }
        }
      } else if (data.type === 'url') {
        // URL upload
        await uploadFromURLMutation.mutateAsync({
          urls: data.urls!,
          name: data.name,
          fileType: data.fileType
        })

        if (notebookId && onConnectionUpdate) {
          onConnectionUpdate()
        }
      } else {
        // Database connection
        const connectionData: ConnectionCreateRequest = {
          type: data.type,
          name: data.name,
          connection_obj: data.connectionObj
        }

        if (notebookId) {
          // Create and associate with notebook
          await connectNotebookMutation.mutateAsync({
            notebookId,
            data: { connection: connectionData }
          })
        } else {
          // Just create the connection
          await createMutation.mutateAsync(connectionData)
        }
      }

      toast.dismiss(loadingToastId)
      showToast.success('Connection created successfully')

      if (onConnectionUpdate) {
        onConnectionUpdate()
      }

      setIsOpen(false)
    } catch (error) {
      console.error('Failed to create connection:', error)
      toast.dismiss(loadingToastId)

      let errorMessage = 'Failed to create connection. '
      if (error instanceof Error) {
        errorMessage += error.message
      }
      showToast.error(errorMessage)

      // Re-throw to let the dialog handle it
      throw error
    }
  }

  // Multi-db: toggle selection for multi-select mode
  const toggleConnectionSelection = (connectionId: string) => {
    setSelectedConnectionIds(prev => {
      if (prev.includes(connectionId)) {
        return prev.filter(id => id !== connectionId)
      } else {
        return [...prev, connectionId]
      }
    })
  }

  // Handle modal close
  const handleModalClose = (open: boolean) => {
    if (!open && isUpdatingConnection) {
      return // Prevent closing during save
    }

    if (!open) {
      // If connection is required and user is trying to close, call onCancel if provided
      if (requireConnection && !notebookConnection && onCancel) {
        onCancel()
        return
      }

      // Prevent closing if connection is required but no cancel handler
      if (requireConnection && !notebookConnection && !onCancel) {
        return
      }

      // Reset state
      setSelectedConnectionId('')
      setSelectedConnectionIds([])
      // Reset to appropriate mode
      if (showManageView && notebookConnections.length > 0) {
        setDialogMode('manage')
      } else if (notebookConnection) {
        setDialogMode('create')
      } else if (allDatasources.length > 0) {
        setDialogMode('select')
      } else {
        setDialogMode('create')
      }
    }
    setIsOpen(open)
  }

  // Simple info modal state
  const [showInfoModal, setShowInfoModal] = useState(false)
  const [currentConnectionIndex, setCurrentConnectionIndex] = useState(0)

  return (
    <>
      {/* Connection Info Button */}
      <button
        onClick={() => {
          setCurrentConnectionIndex(0)
          setShowInfoModal(true)
        }}
        className="px-3 py-1.5 bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors flex items-center gap-2 text-sm"
      >
        <Database className="w-4 h-4" />
        {notebookConnections.length > 1
          ? 'Connections'
          : notebookConnection
            ? (notebookConnection.name || notebookConnection.type)
            : 'No Connection'}
      </button>

      {/* Simple Info Modal */}
      {showInfoModal && (notebookConnection || notebookConnections.length > 0) && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowInfoModal(false)}>
          <div className="bg-[#1a1a1a] border border-[#333] rounded-xl p-6 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                <Database className="w-5 h-5 text-brand-orange" />
                Connected Datasource
                {notebookConnections.length > 1 && (
                  <span className="text-sm text-gray-400 font-normal">
                    ({currentConnectionIndex + 1}/{notebookConnections.length})
                  </span>
                )}
              </h3>
              <button
                onClick={() => setShowInfoModal(false)}
                className="text-gray-400 hover:text-white transition-colors"
              >
                ✕
              </button>
            </div>

            {(() => {
              const currentConnection = notebookConnections.length > 0
                ? notebookConnections[currentConnectionIndex]
                : notebookConnection

              if (!currentConnection) return null

              return (
                <div className="space-y-3 text-sm">
                  <div>
                    <div className="text-gray-400 mb-1">Name</div>
                    <div className="text-white font-medium">{currentConnection.name || 'Unnamed'}</div>
                  </div>

                  <div>
                    <div className="text-gray-400 mb-1">Type</div>
                    <div className="text-white font-medium capitalize">
                      {currentConnection.connection_obj?.dataset_type === 'file' ? 'File Upload (DuckDB)' : currentConnection.type}
                    </div>
                  </div>

                  {currentConnection.connection_obj?.dataset_type === 'file' && (
                    <div>
                      <div className="text-gray-400 mb-1">File Type</div>
                      <div className="text-white font-medium uppercase">
                        {currentConnection.type || 'N/A'}
                      </div>
                    </div>
                  )}

                  {currentConnection.connection_obj?.host && (
                    <div>
                      <div className="text-gray-400 mb-1">Host</div>
                      <div className="text-white font-medium">{currentConnection.connection_obj.host}</div>
                    </div>
                  )}

                  {currentConnection.connection_obj?.database && (
                    <div>
                      <div className="text-gray-400 mb-1">Database</div>
                      <div className="text-white font-medium">{currentConnection.connection_obj.database}</div>
                    </div>
                  )}

                  {currentConnection.connection_obj?.ssl_mode && (
                    <div>
                      <div className="text-gray-400 mb-1">SSL</div>
                      <div className="text-white font-medium">
                        {currentConnection.connection_obj.ssl_mode === 'verify-full' ? 'Encrypted + verified' : 'Encrypted'}
                      </div>
                    </div>
                  )}

                  <div>
                    <div className="text-gray-400 mb-1">Connected At</div>
                    <div className="text-white font-medium">
                      {new Date(currentConnection.created_at).toLocaleString()}
                    </div>
                  </div>
                </div>
              )
            })()}

            {/* Navigation and Close buttons */}
            <div className="mt-6 flex items-center gap-2">
              {notebookConnections.length > 1 ? (
                <>
                  <button
                    onClick={() => setCurrentConnectionIndex(prev => prev - 1)}
                    disabled={currentConnectionIndex === 0}
                    className="px-3 py-2 bg-transparent hover:bg-accent text-white border border-[#404040] rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                    title="Previous connection"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setShowInfoModal(false)}
                    className="flex-1 px-4 py-2 bg-brand-orange hover:bg-brand-orange-hover text-white rounded-lg transition-colors"
                  >
                    Close
                  </button>
                  <button
                    onClick={() => setCurrentConnectionIndex(prev => prev + 1)}
                    disabled={currentConnectionIndex === notebookConnections.length - 1}
                    className="px-3 py-2 bg-transparent hover:bg-accent text-white border border-[#404040] rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                    title="Next connection"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </>
              ) : (
                <button
                  onClick={() => setShowInfoModal(false)}
                  className="w-full px-4 py-2 bg-brand-orange hover:bg-brand-orange-hover text-white rounded-lg transition-colors"
                >
                  Close
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <DatabaseConnectionDialog
        open={isOpen}
        onOpenChange={handleModalClose}
        mode={dialogMode === 'manage' ? 'select' : dialogMode}
        datasources={availableDatasources}
        selectedConnectionId={multiSelect ? '' : selectedConnectionId}
        selectedConnectionIds={multiSelect ? selectedConnectionIds : undefined}
        onConnectionSelect={multiSelect ? toggleConnectionSelection : (id) => setSelectedConnectionId(id)}
        onConfirmConnection={handleConnectionSelect}
        onCreateConnection={handleCreateConnection}
        showPreviousConnectionsOption={availableDatasources.length > 0}
        isLoading={isUpdatingConnection}
        onSwitchToCreate={() => setDialogMode('create')}
        onSwitchToSelect={() => setDialogMode('select')}
        submitButtonText={
          multiSelect && selectedConnectionIds.length > 0
            ? `Connect ${selectedConnectionIds.length} Database(s)`
            : dialogMode === 'select'
              ? 'Connect'
              : 'Create Datasource'
        }
        multiSelect={multiSelect}
      />
    </>
  )
}
