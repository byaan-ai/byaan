import { useState, useRef, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '../components/ui/button'
import { Badge } from '../components/ui/badge'
import { Card } from '../components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { Input } from '../components/ui/input'
import { Label } from '../components/ui/label'
import { Trash2, Loader2, Database, Pencil, Upload, FileText, X, Search, Link as LinkIcon, Leaf, Cylinder, Server, HardDrive, Users, Lock, Cloud, ChevronDown, ChevronRight, CheckCircle2, AlertCircle } from 'lucide-react'
import { ApiService, type ConnectionCreateRequest, type ConnectionType, type Datasource, type DatabricksCatalog } from '../services/api'

type DatabricksPair = { catalog: string; schema: string | null }
const pairKey = (p: DatabricksPair) => `${p.catalog}::${p.schema ?? '*'}`
import { useDatasources, useCreateDBConnection, useDeleteDBConnection, useUploadMultipleFiles, useUploadFromURL } from '../hooks/useDBConnections'
import { showToast } from '../utils/toast'
import { useStore } from '../stores/useStore'
import { useScopes } from '../hooks/useScopes'
import { useAppConfig } from '../hooks/useAppConfig'
import { isTauriApp } from '../lib/tauri-api'

export default function DatabasesPage() {
  const queryClient = useQueryClient()
  const openSidebar = useStore(state => state.openSidebar)
  const setActiveSection = useStore(state => state.setActiveSection)
  const setSelectedDatasource = useStore(state => state.setSelectedDatasource)
  const { canCreateDatasource, canEditDatasource, canDeleteDatasource } = useScopes()
  const { isSelfHosted } = useAppConfig()

  const showSharingFeatures = !isTauriApp() && isSelfHosted

  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const [showUploadDialog, setShowUploadDialog] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [connectionToDelete, setConnectionToDelete] = useState<Datasource | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [csvFile, setCsvFile] = useState<File | null>(null)
  const [csvFiles, setCsvFiles] = useState<File[]>([])
  const [fileAliases, setFileAliases] = useState<Record<string, string>>({})
  const [fileType, setFileType] = useState<'csv' | 'excel' | 'parquet' | 'json'>('csv')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Upload dialog states
  const [uploadFiles, setUploadFiles] = useState<File[]>([])
  const [uploadFileAliases, setUploadFileAliases] = useState<Record<string, string>>({})
  const [uploadFileType, setUploadFileType] = useState<'csv' | 'excel' | 'parquet' | 'json' | ''>('')
  const [uploadConnectionName, setUploadConnectionName] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const uploadFileInputRef = useRef<HTMLInputElement>(null)

  // URL upload states
  const [uploadMode, setUploadMode] = useState<'file' | 'url'>('file')
  const [uploadURLs, setUploadURLs] = useState<string[]>([''])
  const [urlAbortController, setUrlAbortController] = useState<AbortController | null>(null)

  // Form state for create dialog
  const [selectedType, setSelectedType] = useState<ConnectionType | 'upload' | 'url'>('upload')
  const [connectionConfig, setConnectionConfig] = useState({
    name: '',
    host: 'localhost',
    port: '5432',
    database: '',
    user: '',
    password: '',
    connectionString: '',
    region: '',
    accessKeyId: '',
    secretAccessKey: '',
    endpointUrl: '',
    queryMode: 'partiql' as 'partiql' | 'native',
    serverHostname: '',
    httpPath: '',
    accessToken: '',
    catalog: '',
    databricksSchema: '',
  })

  const [togglingVisibility, setTogglingVisibility] = useState<string | null>(null)

  // Databricks 2-step wizard state
  const [databricksStep, setDatabricksStep] = useState<1 | 2>(1)
  const [discoveredCatalogs, setDiscoveredCatalogs] = useState<DatabricksCatalog[] | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [discoverError, setDiscoverError] = useState<string | null>(null)
  const [selectedPairs, setSelectedPairs] = useState<DatabricksPair[]>([])
  const [expandedCatalogs, setExpandedCatalogs] = useState<Set<string>>(new Set())
  const [databricksNamePrefix, setDatabricksNamePrefix] = useState('')
  const [batchProgress, setBatchProgress] = useState<{
    done: number
    total: number
    failures: Array<{ pair: DatabricksPair; error: string }>
  } | null>(null)
  const [databricksCatalogFilter, setDatabricksCatalogFilter] = useState('')

  // Use React Query hooks
  const { data: datasourcesResponse, isLoading: loading, error } = useDatasources()
  const createMutation = useCreateDBConnection()
  const deleteMutation = useDeleteDBConnection()
  const uploadMultipleFilesMutation = useUploadMultipleFiles()
  const uploadFromURLMutation = useUploadFromURL()

  const formatDbType = (type: string): string => {
    switch (type) {
      case 'pg':
        return 'PostgreSQL'
      case 'mongo':
        return 'MongoDB'
      case 'mysql':
        return 'MySQL'
      case 'sqlite':
        return 'SQLite'
      case 'mssql':
        return 'SQL Server'
      case 'dynamodb':
        return 'DynamoDB'
      case 'databricks':
        return 'Databricks'
      case 'csv':
        return 'CSV File'
      case 'excel':
        return 'Excel File'
      case 'parquet':
        return 'Parquet File'
    case 'json':
      return 'JSON File'
    case 'duckdb':
      return 'DuckDB File Dataset'
    default:
      return type.toUpperCase()
  }
}

  const getBadgeVariant = (type: string): "postgres" | "mongodb" | "mysql" | "sqlite" | "csv" | "default" => {
    switch (type) {
      case 'pg':
        return 'postgres'
      case 'mongo':
        return 'mongodb'
      case 'mysql':
        return 'mysql'
      case 'sqlite':
        return 'sqlite'
      case 'csv':
      case 'excel':
      case 'parquet':
      case 'json':
        return 'csv'
      default:
        return 'default'
    }
  }

  // Filter and sort datasources
  const displayDatasources = (datasourcesResponse?.items || [])
    .filter(datasource => {
      if (!searchQuery) return true
      const query = searchQuery.toLowerCase()
      return (
        (datasource.name || '').toLowerCase().includes(query) ||
        formatDbType(datasource.type).toLowerCase().includes(query)
      )
    })
    .sort((a, b) => {
      // Sort by activity (most recent first)
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    })

  // Validation for create form
  const isCreateFormValid = useMemo(() => {
    // Databricks wizard uses its own validation (name is auto-generated server-side).
    if (selectedType === 'databricks') {
      const credsOk =
        connectionConfig.serverHostname.trim().length > 0 &&
        connectionConfig.httpPath.trim().length > 0 &&
        connectionConfig.accessToken.trim().length > 0
      if (databricksStep === 1) return credsOk
      return credsOk && selectedPairs.length > 0
    }

    // Connection name is always required
    if (!connectionConfig.name.trim()) return false

    // Validate based on connection type
    if (selectedType === 'mongo') {
      return connectionConfig.connectionString.trim().length > 0
    } else if (selectedType === 'sqlite') {
      return connectionConfig.database.trim().length > 0
    } else if (selectedType === 'dynamodb') {
      return (
        connectionConfig.region.trim().length > 0 &&
        connectionConfig.accessKeyId.trim().length > 0 &&
        connectionConfig.secretAccessKey.trim().length > 0
      )
    } else {
      // PostgreSQL, MySQL, MSSQL
      return (
        connectionConfig.host.trim().length > 0 &&
        connectionConfig.port.trim().length > 0 &&
        connectionConfig.database.trim().length > 0 &&
        connectionConfig.user.trim().length > 0 &&
        connectionConfig.password.trim().length > 0
      )
    }
  }, [selectedType, connectionConfig, databricksStep, selectedPairs])

  const resetDatabricksWizard = () => {
    setDatabricksStep(1)
    setDiscoveredCatalogs(null)
    setDiscovering(false)
    setDiscoverError(null)
    setSelectedPairs([])
    setExpandedCatalogs(new Set())
    setDatabricksNamePrefix('')
    setBatchProgress(null)
    setDatabricksCatalogFilter('')
  }

  const togglePair = (pair: DatabricksPair) => {
    setSelectedPairs(prev => {
      const key = pairKey(pair)
      const exists = prev.some(p => pairKey(p) === key)
      if (exists) return prev.filter(p => pairKey(p) !== key)
      const cleaned = pair.schema === null
        ? prev.filter(p => p.catalog !== pair.catalog)
        : prev.filter(p => !(p.catalog === pair.catalog && p.schema === null))
      return [...cleaned, pair]
    })
  }

  const isPairSelected = (pair: DatabricksPair) =>
    selectedPairs.some(p => pairKey(p) === pairKey(pair))

  const handleDatabricksDiscover = async () => {
    setDiscoverError(null)
    setDiscovering(true)
    try {
      const res = await ApiService.discoverDatabricks({
        server_hostname: connectionConfig.serverHostname,
        http_path: connectionConfig.httpPath,
        access_token: connectionConfig.accessToken,
      })
      setDiscoveredCatalogs(res.catalogs)
      setDatabricksStep(2)
    } catch (err: any) {
      setDiscoverError(err?.message || 'Failed to discover Databricks catalogs')
    } finally {
      setDiscovering(false)
    }
  }

  const handleDatabricksBatchCreate = async () => {
    if (selectedPairs.length === 0) return
    const pairs = selectedPairs
    const failures: Array<{ pair: DatabricksPair; error: string }> = []
    let done = 0
    setBatchProgress({ done: 0, total: pairs.length, failures: [] })

    for (const pair of pairs) {
      try {
        const prefix = databricksNamePrefix.trim()
        const suffix = `${pair.catalog}.${pair.schema ?? '*'}`
        const name = prefix ? `${prefix} · ${suffix}` : ''
        await ApiService.createConnection({
          type: 'databricks',
          name: name || undefined,
          connection_obj: {
            server_hostname: connectionConfig.serverHostname,
            http_path: connectionConfig.httpPath,
            access_token: connectionConfig.accessToken,
            catalog: pair.catalog,
            schema: pair.schema ?? undefined,
          },
        })
      } catch (err: any) {
        failures.push({ pair, error: err?.message || 'Unknown error' })
      }
      done += 1
      setBatchProgress({ done, total: pairs.length, failures: [...failures] })
    }

    const succeededCount = pairs.length - failures.length
    if (succeededCount > 0) {
      queryClient.invalidateQueries({ queryKey: ['datasources'] })
      showToast.success(`Created ${succeededCount} Databricks connection${succeededCount !== 1 ? 's' : ''}`)
    }
    if (failures.length === 0) {
      setShowCreateDialog(false)
      resetForm()
    } else {
      setSelectedPairs(failures.map(f => f.pair))
      showToast.error(`${failures.length} connection${failures.length !== 1 ? 's' : ''} failed`)
    }
  }

  const handleCreateConnection = async () => {
    // Validate connection name
    if (!connectionConfig.name.trim()) {
      alert('Please provide a connection name')
      return
    }

    let connectionObj: Record<string, any>

    if (selectedType === 'mongo') {
      // MongoDB uses user-provided connection string
      connectionObj = {
        connection_string: connectionConfig.connectionString
      }
    } else if (selectedType === 'sqlite') {
      // SQLite only needs the database file path
      connectionObj = {
        database: connectionConfig.database
      }
    } else if (selectedType === 'dynamodb') {
      connectionObj = {
        region: connectionConfig.region,
        access_key_id: connectionConfig.accessKeyId,
        secret_access_key: connectionConfig.secretAccessKey,
        endpoint_url: connectionConfig.endpointUrl || '',
        query_mode: connectionConfig.queryMode,
      }
    } else if (selectedType === 'databricks') {
      connectionObj = {
        server_hostname: connectionConfig.serverHostname,
        http_path: connectionConfig.httpPath,
        access_token: connectionConfig.accessToken,
        catalog: connectionConfig.catalog || undefined,
        schema: connectionConfig.databricksSchema || undefined,
      }
    } else {
      // PostgreSQL, MySQL, MSSQL - send components, backend builds URL with driver
      connectionObj = {
        host: connectionConfig.host,
        port: parseInt(connectionConfig.port),
        database: connectionConfig.database,
        user: connectionConfig.user,
        password: connectionConfig.password
      }
    }

    const connectionData: ConnectionCreateRequest = {
      type: selectedType,
      name: connectionConfig.name,
      connection_obj: connectionObj
    }

    createMutation.mutate(connectionData, {
      onSuccess: () => {
        setShowCreateDialog(false)
        resetForm()
      }
    })
  }

  const resetForm = () => {
    setSelectedType('upload')
    setConnectionConfig({
      name: '',
      host: 'localhost',
      port: '5432',
      database: '',
      user: '',
      password: '',
      connectionString: '',
      region: '',
      accessKeyId: '',
      secretAccessKey: '',
      endpointUrl: '',
      queryMode: 'partiql',
      serverHostname: '',
      httpPath: '',
      accessToken: '',
      catalog: '',
      databricksSchema: '',
    })
    resetCSVForm()
    resetUploadForm()
    resetDatabricksWizard()
  }

  const handleDeleteClick = (datasource: Datasource) => {
    setConnectionToDelete(datasource)
    setDeleteDialogOpen(true)
  }

  const confirmDelete = async () => {
    if (!connectionToDelete) return

    // Handle deletion based on source type
    if (connectionToDelete.source_type === 'connection') {
      deleteMutation.mutate(connectionToDelete.connection_id!, {
        onSuccess: () => {
          // Invalidate notebook connections so it refetch
          queryClient.invalidateQueries({ queryKey: ['notebook-connections'] })
          setDeleteDialogOpen(false)
          setConnectionToDelete(null)
        },
      })
    } else {
      // Delete dataset
      try {
        await ApiService.deleteDataset(connectionToDelete.id)
        setDeleteDialogOpen(false)
        setConnectionToDelete(null)
        // Invalidate queries to refresh the list
        queryClient.invalidateQueries({ queryKey: ['datasources'] })
        // Invalidate notebook connections so it refetch
        queryClient.invalidateQueries({ queryKey: ['notebook-connections'] })
        showToast.success('File datasource deleted successfully')
      } catch (error: any) {
        console.error('Error deleting dataset:', error)
        showToast.error(`Failed to delete datasource: ${error.message}`)
      }
    }
  }

  const cancelDelete = () => {
    setDeleteDialogOpen(false)
    setConnectionToDelete(null)
  }

  const handleEditClick = (datasource: Datasource) => {
    setSelectedDatasource(datasource.id)
    setActiveSection('database')
    openSidebar('database')
  }

  const handleQuickToggleVisibility = async (datasource: Datasource) => {
    const newIsPublic = !datasource.is_public
    setTogglingVisibility(datasource.id)

    try {
      await ApiService.updateDatasourceVisibility(datasource.id, newIsPublic)
      queryClient.invalidateQueries({ queryKey: ['datasources'] })
      showToast.success(newIsPublic ? 'Datasource shared with team' : 'Datasource set to private')
    } catch (error: any) {
      console.error('Error toggling visibility:', error)
      showToast.error(`Failed to update visibility: ${error.message}`)
    } finally {
      setTogglingVisibility(null)
    }
  }

  const handleTypeChange = (newType: ConnectionType | 'upload' | 'url') => {
    resetDatabricksWizard()
    setSelectedType(newType)
    const defaultPorts: Record<string, string> = {
      pg: '5432',
      mysql: '3306',
      mssql: '1433',
      sqlite: '',
      mongo: '27017',
      dynamodb: '',
      csv: '',
      excel: '',
      parquet: '',
      json: '',
      upload: '',
      url: ''
    }
    setConnectionConfig(prev => ({
      ...prev,
      port: defaultPorts[newType] || '',
      connectionString: ''
    }))
  }

  const handleCSVFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || [])

    if (selectedFiles.length === 0) return

    const allowedExtensions: Record<'csv' | 'excel' | 'parquet' | 'json', string[]> = {
      csv: ['.csv'],
      excel: ['.xlsx', '.xls'],
      parquet: ['.parquet'],
      json: ['.json']
    }
    const extensions = allowedExtensions[fileType]
    const typeLabel = fileType.toUpperCase()

    // Validate each file
    for (const file of selectedFiles) {
      const isValidExtension = extensions.some(ext => file.name.toLowerCase().endsWith(ext))
      if (!isValidExtension) {
        alert(`File "${file.name}" is not a ${typeLabel} file. Allowed extensions: ${extensions.join(', ')}`)
        return
      }
    }

    // If single file, use legacy single-file upload
    if (selectedFiles.length === 1) {
      setCsvFile(selectedFiles[0])
      setCsvFiles([])
      setFileAliases({})
    } else {
      // Multiple files - use multi-file upload
      setCsvFile(null)
      setCsvFiles(selectedFiles)

      // Auto-generate aliases from filenames
      const aliases: Record<string, string> = {}
      selectedFiles.forEach(file => {
        let alias = file.name
        extensions.forEach(ext => {
          if (alias.toLowerCase().endsWith(ext)) {
            alias = alias.slice(0, -ext.length)
          }
        })
        aliases[file.name] = alias
      })
      setFileAliases(aliases)
    }
  }

  const resetCSVForm = () => {
    setCsvFile(null)
    setCsvFiles([])
    setFileAliases({})
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

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

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
  }

  const truncateFilename = (filename: string, maxLength: number = 25): string => {
    if (filename.length <= maxLength) return filename
    const extension = filename.split('.').pop() || ''
    const nameWithoutExt = filename.substring(0, filename.lastIndexOf('.'))
    const truncatedName = nameWithoutExt.substring(0, maxLength - extension.length - 4) + '...'
    return extension ? `${truncatedName}.${extension}` : truncatedName
  }

  // Upload dialog handlers
  const handleUploadFilesDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)

    const droppedFiles = Array.from(e.dataTransfer.files)
    handleUploadFilesSelection(droppedFiles)
  }

  const handleUploadFilesChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || [])
    handleUploadFilesSelection(selectedFiles)
  }

  const detectFileType = (filename: string): 'csv' | 'excel' | 'parquet' | 'json' | null => {
    const lowerName = filename.toLowerCase()
    if (lowerName.endsWith('.csv')) return 'csv'
    if (lowerName.endsWith('.xlsx') || lowerName.endsWith('.xls')) return 'excel'
    if (lowerName.endsWith('.parquet')) return 'parquet'
    if (lowerName.endsWith('.json')) return 'json'
    return null
  }

  const handleUploadFilesSelection = (selectedFiles: File[]) => {
    if (selectedFiles.length === 0) return

    const allowedExtensions: Record<'csv' | 'excel' | 'parquet' | 'json', string[]> = {
      csv: ['.csv'],
      excel: ['.xlsx', '.xls'],
      parquet: ['.parquet'],
      json: ['.json']
    }

    // Auto-detect file type from first file if no files uploaded yet
    let currentFileType = uploadFileType
    if (uploadFiles.length === 0 && selectedFiles.length > 0) {
      const detectedType = detectFileType(selectedFiles[0].name)
      if (detectedType) {
        currentFileType = detectedType
        setUploadFileType(detectedType)
      } else {
        alert(`Unable to detect file type from "${selectedFiles[0].name}". Supported: .csv, .xlsx, .xls, .parquet, .json`)
        return
      }
    }

    // Validate we have a file type
    if (!currentFileType) {
      alert('Please upload files to detect type')
      return
    }

    const extensions = allowedExtensions[currentFileType as 'csv' | 'excel' | 'parquet' | 'json']
    const typeLabel = currentFileType.toUpperCase()

    // Check for duplicate file names
    const existingFileNames = new Set(uploadFiles.map(f => f.name))
    const duplicates: string[] = []

    // Validate each file
    for (const file of selectedFiles) {
      const isValidExtension = extensions.some(ext => file.name.toLowerCase().endsWith(ext))
      if (!isValidExtension) {
        alert(`File "${file.name}" doesn't match detected type (${typeLabel}). All files must be ${extensions.join(' or ')} files.`)
        return
      }
      if (existingFileNames.has(file.name)) {
        duplicates.push(file.name)
      }
    }

    if (duplicates.length > 0) {
      alert(`The following file(s) are already added: ${duplicates.join(', ')}`)
      return
    }

    // Add files to existing files (instead of replacing)
    setUploadFiles(prev => [...prev, ...selectedFiles])

    // Auto-populate datasource name from first file if currently empty
    if (uploadConnectionName === '' && uploadFiles.length === 0) {
      let autoName = selectedFiles[0].name
      extensions.forEach(ext => {
        if (autoName.toLowerCase().endsWith(ext)) {
          autoName = autoName.slice(0, -ext.length)
        }
      })
      setUploadConnectionName(autoName)
    }

    // Auto-generate aliases from filenames and merge with existing aliases
    const newAliases: Record<string, string> = {}
    selectedFiles.forEach(file => {
      let alias = file.name
      extensions.forEach(ext => {
        if (alias.toLowerCase().endsWith(ext)) {
          alias = alias.slice(0, -ext.length)
        }
      })
      newAliases[file.name] = alias
    })
    setUploadFileAliases(prev => ({ ...prev, ...newAliases }))
  }

  const handleCreateDialogSubmit = async () => {
    if (!uploadConnectionName.trim()) {
      alert('Please provide a datasource name')
      return
    }

    if (selectedType === 'upload') {
      // File upload mode
      if (uploadFiles.length === 0) {
        alert('Please select at least one file')
        return
      }

      uploadMultipleFilesMutation.mutate(
        { files: uploadFiles, name: uploadConnectionName, aliases: uploadFileAliases, fileType: uploadFileType },
        {
          onSuccess: () => {
            setShowCreateDialog(false)
            resetUploadForm()
          }
        }
      )
    } else if (selectedType === 'url') {
      // URL upload mode
      const validURLs = uploadURLs.filter(url => url.trim().length > 0)
      if (validURLs.length === 0) {
        alert('Please provide at least one URL')
        return
      }

      uploadFromURLMutation.mutate(
        {
          urls: validURLs,
          name: uploadConnectionName,
          fileType: uploadFileType || undefined,
        },
        {
          onSuccess: () => {
            setShowCreateDialog(false)
            resetUploadForm()
          }
        }
      )
    }
  }

  const handleUploadFilesSubmit = async () => {
    if (!uploadConnectionName.trim()) {
      alert('Please provide a connection name')
      return
    }

    if (uploadMode === 'file') {
      // File upload mode
      if (uploadFiles.length === 0) {
        alert('Please select at least one file')
        return
      }

      uploadMultipleFilesMutation.mutate(
        { files: uploadFiles, name: uploadConnectionName, aliases: uploadFileAliases, fileType: uploadFileType },
        {
          onSuccess: () => {
            setShowUploadDialog(false)
            resetUploadForm()
          }
        }
      )
    } else {
      // URL upload mode
      const validURLs = uploadURLs.filter(url => url.trim().length > 0)
      if (validURLs.length === 0) {
        alert('Please provide at least one URL')
        return
      }

      // Create abort controller for this download
      const controller = new AbortController()
      setUrlAbortController(controller)

      uploadFromURLMutation.mutate(
        {
          urls: validURLs,
          name: uploadConnectionName,
          fileType: uploadFileType || undefined,
          signal: controller.signal
        },
        {
          onSuccess: () => {
            setShowUploadDialog(false)
            resetUploadForm()
            setUrlAbortController(null)
          },
          onError: () => {
            setUrlAbortController(null)
          }
        }
      )
    }
  }

  const resetUploadForm = () => {
    setUploadFiles([])
    setUploadFileAliases({})
    setUploadFileType('')
    setUploadConnectionName('')
    setUploadMode('file')
    setUploadURLs([''])
    if (uploadFileInputRef.current) {
      uploadFileInputRef.current.value = ''
    }
  }

  return (
    <div className="bg-[#0d0d0d] w-full h-full flex flex-col">
      {/* Header Section */}
      <div className="w-full px-8 pt-[50px] pb-8">
        <div className="max-w-[850px] mx-auto">
          {/* Title and Buttons */}
          <div className="flex items-center justify-between mb-8">
            <h1 className="text-2xl font-bold text-white tracking-tight">Datasources</h1>
            {canCreateDatasource && (
              <Button
                variant="brand-primary"
                onClick={() => setShowCreateDialog(true)}
                disabled={createMutation.isPending}
                className="font-medium px-5 py-2.5 rounded-md text-sm"
              >
                + New datasource
              </Button>
            )}
          </div>

          {/* Search Bar */}
          <div className="relative">
            <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-500" />
            <Input
              type="text"
              placeholder="Search datasources..."
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
              <div className="animate-spin w-8 h-8 border-2 border-brand-orange border-t-transparent rounded-full mx-auto mb-4"></div>
              <p className="text-gray-400">Loading database connections...</p>
            </div>
          ) : (
            <>
              {/* Empty State */}
              {displayDatasources.length === 0 ? (
                <div className="max-w-[850px] mx-auto">
                  <Card className="p-12 text-center bg-[#1a1a1a] border-gray-800">
                    <div className="max-w-md mx-auto">
                      <div className="w-16 h-16 bg-brand-orange/10 rounded-full flex items-center justify-center mx-auto mb-4">
                        <Database className="w-8 h-8 text-brand-orange" />
                      </div>
                      <h3 className="text-xl font-semibold text-white mb-2">No Database Connections</h3>
                      <p className="text-gray-400 mb-6">
                        Get started by adding your first database connection. Connect to PostgreSQL, MySQL, MongoDB, SQL Server, SQLite, or upload data files (CSV, Excel, Parquet, JSON).
                      </p>
                    </div>
                  </Card>
                </div>
              ) : (
                <>
                  {/* Connection Cards */}
                  <div className="max-w-[850px] mx-auto grid grid-cols-1 md:grid-cols-2 gap-6">
                    {displayDatasources.map(datasource => {
                      const timeAgo = formatTimeAgo(datasource.created_at)
                      return (
                        <Card
                          key={datasource.id}
                          className="p-6 bg-[#1a1a1a] border-gray-800 hover:border-gray-700 transition-colors cursor-pointer"
                          onClick={() => handleEditClick(datasource)}
                        >
                          <div className="flex items-start justify-between mb-3 gap-4">
                            <div className="flex-1 min-w-0">
                              {/* Database Name and Type Badge */}
                              <div className="flex items-center gap-3 mb-2 flex-wrap">
                                <h3 className="text-lg font-normal text-white truncate" title={datasource.name}>
                                  {datasource.name}
                                </h3>
                                <Badge variant={getBadgeVariant(datasource.type)} className="shrink-0">
                                  {formatDbType(datasource.type)}
                                </Badge>
                                {datasource.source_type === 'dataset' && datasource.files_count && (
                                  <span className="text-xs bg-brand-orange/20 text-brand-orange px-2 py-0.5 rounded shrink-0">
                                    {datasource.files_count} file{datasource.files_count !== 1 ? 's' : ''}
                                  </span>
                                )}
                                {showSharingFeatures && (datasource.is_public ? (
                                  <span className="text-xs bg-green-500/20 text-green-400 px-2 py-0.5 rounded shrink-0 flex items-center gap-1">
                                    <Users className="w-3 h-3" />
                                    Shared
                                  </span>
                                ) : (
                                  <span className="text-xs bg-gray-500/20 text-gray-400 px-2 py-0.5 rounded shrink-0 flex items-center gap-1">
                                    <Lock className="w-3 h-3" />
                                    Private
                                  </span>
                                ))}
                              </div>

                              {/* Description */}
                              <p className="text-sm text-gray-400 mb-3">
                                {datasource.source_type === 'dataset' ? 'File datasource' : 'Database connection'} for {formatDbType(datasource.type)}
                              </p>

                              {/* Timestamp */}
                              <p className="text-xs text-gray-500">
                                Updated {timeAgo}
                              </p>
                            </div>

                            {/* Action Buttons */}
                            <div className="flex gap-2 shrink-0" onClick={(e) => e.stopPropagation()}>
                              {showSharingFeatures && canEditDatasource(datasource.created_by) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => handleQuickToggleVisibility(datasource)}
                                  disabled={togglingVisibility === datasource.id}
                                  className={`${datasource.is_public ? 'text-green-400 hover:text-green-300' : 'text-gray-400 hover:text-white'} hover:bg-gray-800`}
                                  title={datasource.is_public ? 'Make private' : 'Share with team'}
                                >
                                  {togglingVisibility === datasource.id ? (
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                  ) : datasource.is_public ? (
                                    <Users className="w-4 h-4" />
                                  ) : (
                                    <Lock className="w-4 h-4" />
                                  )}
                                </Button>
                              )}
                              {canEditDatasource(datasource.created_by) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => handleEditClick(datasource)}
                                  className="text-gray-400 hover:text-white hover:bg-gray-800"
                                  title="Edit datasource"
                                >
                                  <Pencil className="w-4 h-4" />
                                </Button>
                              )}
                              {canDeleteDatasource(datasource.created_by) && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => handleDeleteClick(datasource)}
                                  disabled={deleteMutation.isPending}
                                  className="text-gray-400 hover:text-red-400 hover:bg-gray-800"
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

        {/* Create Dialog with Sidebar */}
        <Dialog open={showCreateDialog} onOpenChange={(open) => {
          if (!open && (createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending)) return
          setShowCreateDialog(open)
          if (!open) resetForm()
        }}>
          <DialogContent className="max-w-4xl bg-[#2a2a2a] border-[#444444] p-0 gap-0">
            <DialogHeader className="px-6 pt-6 pb-4 border-b border-[#444444]">
              <DialogTitle className="text-white text-xl">Add Database Connection</DialogTitle>
            </DialogHeader>

            <div className="flex h-[600px]">
              {/* Sidebar */}
              <div className="w-52 bg-[#1a1a1a] border-r border-[#444444] p-3 overflow-y-auto custom-scrollbar">
                <div className="space-y-1">
                  {/* Upload Files */}
                  <button
                    onClick={() => handleTypeChange('upload')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'upload'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Upload className={`w-5 h-5 flex-shrink-0 ${selectedType === 'upload' ? 'text-brand-orange' : ''}`} />
                    <span className="text-sm font-medium">Upload Files</span>
                  </button>

                  {/* Import from URL */}
                  <button
                    onClick={() => handleTypeChange('url')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'url'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <LinkIcon className={`w-5 h-5 flex-shrink-0 ${selectedType === 'url' ? 'text-brand-orange' : ''}`} />
                    <span className="text-sm font-medium">Import from URL</span>
                  </button>

                  {/* Divider */}
                  <div className="my-2 border-t border-[#444444]"></div>

                  {/* PostgreSQL */}
                  <button
                    onClick={() => handleTypeChange('pg')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'pg'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Cylinder className="w-5 h-5 flex-shrink-0 text-blue-400" />
                    <span className="text-sm font-medium">PostgreSQL</span>
                  </button>

                  {/* MongoDB */}
                  <button
                    onClick={() => handleTypeChange('mongo')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'mongo'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Leaf className="w-5 h-5 flex-shrink-0 text-green-500" />
                    <span className="text-sm font-medium">MongoDB</span>
                  </button>

                  {/* MySQL */}
                  <button
                    onClick={() => handleTypeChange('mysql')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'mysql'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Database className="w-5 h-5 flex-shrink-0 text-orange-400" />
                    <span className="text-sm font-medium">MySQL</span>
                  </button>

                  {/* SQL Server */}
                  <button
                    onClick={() => handleTypeChange('mssql')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'mssql'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Server className="w-5 h-5 flex-shrink-0 text-red-400" />
                    <span className="text-sm font-medium">SQL Server</span>
                  </button>

                  {/* SQLite */}
                  <button
                    onClick={() => handleTypeChange('sqlite')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'sqlite'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <HardDrive className="w-5 h-5 flex-shrink-0 text-cyan-400" />
                    <span className="text-sm font-medium">SQLite</span>
                  </button>

                  {/* DynamoDB */}
                  <button
                    onClick={() => handleTypeChange('dynamodb')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'dynamodb'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Cloud className="w-5 h-5 flex-shrink-0 text-amber-400" />
                    <span className="text-sm font-medium">DynamoDB</span>
                  </button>

                  {/* Databricks */}
                  <button
                    onClick={() => handleTypeChange('databricks')}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'databricks'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Database className="w-5 h-5 flex-shrink-0 text-red-400" />
                    <span className="text-sm font-medium">Databricks</span>
                  </button>
                </div>
              </div>

              {/* Form Content Area */}
              <div className="flex-1 flex flex-col overflow-hidden">
                <div className="flex-1 overflow-y-auto custom-scrollbar p-6">
                  <div className="space-y-4">
                    {/* Connection/Datasource Name - Always shown (hidden for Databricks wizard which auto-names) */}
                    {selectedType !== 'databricks' && (
                      <div>
                        <Label htmlFor="connection-name" className="text-white">
                          {selectedType === 'upload' || selectedType === 'url' ? 'Datasource Name' : 'Connection Name'} <span className="text-red-400">*</span>
                        </Label>
                        <Input
                          id="connection-name"
                          value={selectedType === 'upload' || selectedType === 'url' ? uploadConnectionName : connectionConfig.name}
                          onChange={(e) => {
                            if (selectedType === 'upload' || selectedType === 'url') {
                              setUploadConnectionName(e.target.value)
                            } else {
                              setConnectionConfig(prev => ({ ...prev, name: e.target.value }))
                            }
                          }}
                          placeholder={selectedType === 'upload' || selectedType === 'url' ? 'My File Datasource' : 'My Database Connection'}
                          disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                          className="mt-1 bg-[#1a1a1a] border-[#555555] text-white placeholder-[#888888]"
                        />
                      </div>
                    )}

                    {/* Upload Files Form */}
                    {selectedType === 'upload' && (
                      <>
                        {/* Progress Indicator */}
                        {uploadMultipleFilesMutation.isPending && (
                          <div className="bg-orange-900/20 border border-brand-orange rounded-lg p-4">
                            <div className="flex items-center gap-3">
                              <Loader2 className="w-5 h-5 text-brand-orange animate-spin flex-shrink-0" />
                              <div className="flex-1">
                                <p className="text-sm font-medium text-brand-orange">Uploading files...</p>
                                <p className="text-xs text-gray-400 mt-1">
                                  Uploading {uploadFiles.length} file(s). Please wait.
                                </p>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* File Type Display (Auto-detected) */}
                        {uploadFiles.length > 0 && uploadFileType && (
                          <div className="bg-[#1a1a1a] border border-[#555555] rounded-md px-4 py-2 flex items-center justify-between">
                            <span className="text-sm text-white">
                              File Type: <span className="font-medium">{uploadFileType.toUpperCase()}</span>
                            </span>
                            <span className="text-sm text-gray-400">
                              {uploadFiles.length} file{uploadFiles.length !== 1 ? 's' : ''}
                            </span>
                          </div>
                        )}

                        {/* Drag and Drop Area */}
                        <div
                          className={`border-2 border-dashed rounded-lg transition-colors ${
                            isDragging
                              ? 'border-brand-orange bg-brand-orange/10'
                              : 'border-[#555555] hover:border-[#777777] hover:bg-[#333333]'
                          } ${uploadMultipleFilesMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                          onDragOver={(e) => {
                            if (!uploadMultipleFilesMutation.isPending) {
                              e.preventDefault()
                              setIsDragging(true)
                            }
                          }}
                          onDragLeave={(e) => {
                            e.preventDefault()
                            setIsDragging(false)
                          }}
                          onDrop={(e) => {
                            if (!uploadMultipleFilesMutation.isPending) {
                              handleUploadFilesDrop(e)
                            }
                          }}
                        >
                          <input
                            ref={uploadFileInputRef}
                            type="file"
                            accept=".csv,.xlsx,.xls,.parquet,.json"
                            multiple
                            onChange={handleUploadFilesChange}
                            disabled={uploadMultipleFilesMutation.isPending}
                            className="hidden"
                          />

                          {/* Upload Prompt */}
                          <div
                            className={`p-6 text-center ${uploadMultipleFilesMutation.isPending ? 'cursor-not-allowed' : 'cursor-pointer'}`}
                            onClick={() => {
                              if (!uploadMultipleFilesMutation.isPending) {
                                uploadFileInputRef.current?.click()
                              }
                            }}
                          >
                            <Upload className={`${uploadFiles.length > 0 ? 'w-8 h-8' : 'w-12 h-12'} mx-auto mb-3 text-brand-orange`} />
                            <p className={`text-white font-medium ${uploadFiles.length > 0 ? 'text-sm mb-1' : 'mb-2'}`}>
                              {uploadFiles.length > 0
                                ? `Drag & drop more ${uploadFileType.toUpperCase()} files here`
                                : 'Drag & drop your data files here'}
                            </p>
                            <p className={`text-gray-400 ${uploadFiles.length > 0 ? 'text-xs mb-2' : 'text-sm mb-4'}`}>
                              or click to browse
                            </p>
                            <p className="text-xs text-gray-500">
                              {uploadFiles.length > 0
                                ? 'All files must be the same type'
                                : 'Supported: CSV, Excel, Parquet, JSON • Type auto-detected'}
                            </p>
                          </div>

                          {/* Uploaded Files List */}
                          {uploadFiles.length > 0 && (
                            <div className="px-6 pb-6">
                              <div className="flex items-center justify-between mb-3 pb-3 border-t border-[#555555] pt-3">
                                <Label className="text-white text-sm">{uploadFiles.length} file(s) selected</Label>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setUploadFiles([])
                                    setUploadFileAliases({})
                                    setUploadFileType('')
                                    if (uploadFileInputRef.current) {
                                      uploadFileInputRef.current.value = ''
                                    }
                                  }}
                                  disabled={uploadMultipleFilesMutation.isPending}
                                  className="text-red-400 hover:text-red-300 hover:bg-red-900/20 h-7 text-xs"
                                >
                                  Clear All
                                </Button>
                              </div>

                              <div className="max-h-[200px] overflow-y-auto custom-scrollbar space-y-2 pr-1">
                                {uploadFiles.map((file, index) => (
                                  <div
                                    key={index}
                                    className="p-3 bg-[#1a1a1a] border border-[#555555] rounded-md"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <div className="flex items-center justify-between gap-3">
                                      <div className="flex items-center gap-2 flex-1 min-w-0">
                                        <FileText className="w-4 h-4 text-brand-orange flex-shrink-0" />
                                        <p className="text-sm text-white font-medium flex-1 truncate" title={file.name}>
                                          {truncateFilename(file.name)}
                                        </p>
                                        <p className="text-xs text-gray-400 flex-shrink-0">
                                          {formatFileSize(file.size)}
                                        </p>
                                      </div>
                                      <Button
                                        size="sm"
                                        variant="ghost"
                                        onClick={(e) => {
                                          e.stopPropagation()
                                          const newFiles = uploadFiles.filter((_, i) => i !== index)
                                          setUploadFiles(newFiles)
                                          const newAliases = { ...uploadFileAliases }
                                          delete newAliases[file.name]
                                          setUploadFileAliases(newAliases)
                                          // Reset file type if no files left
                                          if (newFiles.length === 0) {
                                            setUploadFileType('')
                                          }
                                        }}
                                        disabled={uploadMultipleFilesMutation.isPending}
                                        className="text-red-400 hover:text-red-300 hover:bg-red-900/20 h-8 w-8 p-0 flex-shrink-0"
                                      >
                                        <X className="w-4 h-4" />
                                      </Button>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </>
                    )}

                    {/* Import from URL Form */}
                    {selectedType === 'url' && (
                      <>
                        {/* Progress Indicator */}
                        {uploadFromURLMutation.isPending && (
                          <div className="bg-orange-900/20 border border-brand-orange rounded-lg p-4">
                            <div className="flex items-center gap-3">
                              <Loader2 className="w-5 h-5 text-brand-orange animate-spin flex-shrink-0" />
                              <div className="flex-1">
                                <p className="text-sm font-medium text-brand-orange">
                                  Downloading files from URLs...
                                </p>
                                <p className="text-xs text-gray-400 mt-1">
                                  Downloading {uploadURLs.filter(u => u.trim()).length} file(s). This may take a while for large files.
                                </p>
                              </div>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => {
                                  if (urlAbortController) {
                                    urlAbortController.abort()
                                    setUrlAbortController(null)
                                  }
                                  uploadFromURLMutation.reset()
                                }}
                                className="text-red-400 hover:text-red-300 hover:bg-red-900/20 h-8 w-8 p-0 flex-shrink-0"
                                title="Cancel download"
                              >
                                <X className="w-4 h-4" />
                              </Button>
                            </div>
                          </div>
                        )}

                        <div className="space-y-3">
                          <Label className="text-white">File URLs</Label>
                          {uploadURLs.map((url, index) => (
                            <div key={index} className="flex gap-2">
                              <Input
                                value={url}
                                onChange={(e) => {
                                  const newURLs = [...uploadURLs]
                                  newURLs[index] = e.target.value
                                  if (index === 0 && !uploadFileType && e.target.value) {
                                    const urlFileName = e.target.value.split('/').pop() || ''
                                    const detectedType = detectFileType(urlFileName)
                                    if (detectedType) {
                                      setUploadFileType(detectedType)
                                    }
                                  }
                                  setUploadURLs(newURLs)
                                }}
                                placeholder={`https://example.com/data${uploadFileType ? '.' + (uploadFileType === 'excel' ? 'xlsx' : uploadFileType) : ''}`}
                                disabled={uploadFromURLMutation.isPending}
                                className="flex-1 bg-[#1a1a1a] border-[#555555] text-white placeholder-[#888888]"
                              />
                              {uploadURLs.length > 1 && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => {
                                    setUploadURLs(uploadURLs.filter((_, i) => i !== index))
                                  }}
                                  disabled={uploadFromURLMutation.isPending}
                                  className="text-red-400 hover:text-red-300 hover:bg-red-900/20"
                                >
                                  <X className="w-4 h-4" />
                                </Button>
                              )}
                            </div>
                          ))}
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => setUploadURLs([...uploadURLs, ''])}
                            disabled={uploadFromURLMutation.isPending}
                            className="w-full border-[#555555] text-white hover:bg-[#3a3a3a]"
                          >
                            + Add Another URL
                          </Button>
                          <p className="text-xs text-gray-400">
                            Enter public URLs to data files (CSV, Excel, Parquet, JSON) or ZIP archives of these types.
                          </p>
                        </div>
                      </>
                    )}

                    {/* Database Connection Forms */}
                    {selectedType === 'mongo' && (
                      <div>
                        <Label htmlFor="conn-string" className="text-white">
                          Connection String <span className="text-red-400">*</span>
                        </Label>
                        <Input
                          id="conn-string"
                          placeholder="mongodb://username:password@host:port/database"
                          value={connectionConfig.connectionString}
                          onChange={(e) => setConnectionConfig(prev => ({ ...prev, connectionString: e.target.value }))}
                          disabled={createMutation.isPending}
                          className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                        />
                      </div>
                    )}

                    {selectedType === 'sqlite' && (
                      <div>
                        <Label htmlFor="database" className="text-white">
                          Database File Path <span className="text-red-400">*</span>
                        </Label>
                        <Input
                          id="database"
                          placeholder="/path/to/database.db"
                          value={connectionConfig.database}
                          onChange={(e) => setConnectionConfig(prev => ({ ...prev, database: e.target.value }))}
                          disabled={createMutation.isPending}
                          className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                        />
                        <p className="text-xs text-gray-400 mt-1">Enter the full path to your SQLite database file</p>
                      </div>
                    )}

                    {selectedType === 'dynamodb' && (
                      <div className="space-y-4">
                        <div>
                          <Label htmlFor="region" className="text-white">AWS Region <span className="text-red-400">*</span></Label>
                          <Input
                            id="region"
                            placeholder="us-east-1"
                            value={connectionConfig.region}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, region: e.target.value }))}
                            disabled={createMutation.isPending}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                        </div>
                        <div>
                          <Label htmlFor="accessKeyId" className="text-white">Access Key ID <span className="text-red-400">*</span></Label>
                          <Input
                            id="accessKeyId"
                            placeholder="AKIA..."
                            value={connectionConfig.accessKeyId}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, accessKeyId: e.target.value }))}
                            disabled={createMutation.isPending}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                        </div>
                        <div>
                          <Label htmlFor="secretAccessKey" className="text-white">Secret Access Key <span className="text-red-400">*</span></Label>
                          <Input
                            id="secretAccessKey"
                            type="password"
                            placeholder="Secret access key"
                            value={connectionConfig.secretAccessKey}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, secretAccessKey: e.target.value }))}
                            disabled={createMutation.isPending}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                        </div>
                        <div>
                          <Label htmlFor="endpointUrl" className="text-white">Endpoint URL <span className="text-gray-500">(optional)</span></Label>
                          <Input
                            id="endpointUrl"
                            placeholder="http://localhost:8000 (for local DynamoDB)"
                            value={connectionConfig.endpointUrl}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, endpointUrl: e.target.value }))}
                            disabled={createMutation.isPending}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                          <p className="text-xs text-gray-400 mt-1">Only needed for local DynamoDB or custom endpoints</p>
                        </div>
                        <div>
                          <Label htmlFor="queryMode" className="text-white">Query Mode <span className="text-red-400">*</span></Label>
                          <select
                            id="queryMode"
                            value={connectionConfig.queryMode}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, queryMode: e.target.value as 'partiql' | 'native' }))}
                            disabled={createMutation.isPending}
                            className="mt-1 w-full rounded-md bg-[#1a1a1a] border border-[#555555] text-white px-3 py-2 text-sm"
                          >
                            <option value="partiql">PartiQL (SQL-like syntax)</option>
                            <option value="native">Native API (scan/query/get)</option>
                          </select>
                          <p className="text-xs text-gray-400 mt-1">PartiQL uses SQL-like syntax. Native API uses JSON-based operations.</p>
                        </div>
                      </div>
                    )}

                    {selectedType === 'databricks' && (
                      <div className="mb-4 flex items-center gap-3">
                        <div className="flex items-center gap-2 flex-1">
                          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold ${databricksStep === 1 ? 'bg-brand-orange text-white' : 'bg-green-600/20 text-green-400 border border-green-600/40'}`}>
                            {databricksStep === 1 ? '1' : <CheckCircle2 className="w-4 h-4" />}
                          </div>
                          <span className={`text-sm font-medium ${databricksStep === 1 ? 'text-white' : 'text-gray-400'}`}>Credentials</span>
                        </div>
                        <div className={`h-px flex-1 ${databricksStep === 2 ? 'bg-brand-orange' : 'bg-[#444444]'}`} />
                        <div className="flex items-center gap-2 flex-1 justify-end">
                          <span className={`text-sm font-medium ${databricksStep === 2 ? 'text-white' : 'text-gray-500'}`}>Pick catalogs & schemas</span>
                          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold ${databricksStep === 2 ? 'bg-brand-orange text-white' : 'bg-[#2a2a2a] text-gray-500 border border-[#444444]'}`}>2</div>
                        </div>
                      </div>
                    )}

                    {selectedType === 'databricks' && databricksStep === 1 && (
                      <div className="space-y-4">
                        <p className="text-xs text-gray-400">Enter your Databricks workspace credentials. Next, we'll list available catalogs and schemas to pick from.</p>
                        <div>
                          <Label htmlFor="serverHostname" className="text-white">Server Hostname <span className="text-red-400">*</span></Label>
                          <Input
                            id="serverHostname"
                            placeholder="adb-1234.azuredatabricks.net"
                            value={connectionConfig.serverHostname}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, serverHostname: e.target.value }))}
                            disabled={discovering}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                        </div>
                        <div>
                          <Label htmlFor="httpPath" className="text-white">HTTP Path <span className="text-red-400">*</span></Label>
                          <Input
                            id="httpPath"
                            placeholder="/sql/1.0/warehouses/abc123"
                            value={connectionConfig.httpPath}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, httpPath: e.target.value }))}
                            disabled={discovering}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                          <p className="text-xs text-gray-400 mt-1">Find in Databricks: SQL Warehouse → Connection Details → HTTP Path</p>
                        </div>
                        <div>
                          <Label htmlFor="accessToken" className="text-white">Access Token (PAT) <span className="text-red-400">*</span></Label>
                          <Input
                            id="accessToken"
                            type="password"
                            placeholder="dapi..."
                            value={connectionConfig.accessToken}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, accessToken: e.target.value }))}
                            disabled={discovering}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                          />
                        </div>
                        {discoverError && (
                          <div className="flex items-start gap-2 bg-red-900/20 border border-red-700/50 rounded-md p-3 text-sm text-red-200">
                            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                            <span>{discoverError}</span>
                          </div>
                        )}
                      </div>
                    )}

                    {selectedType === 'databricks' && databricksStep === 2 && discoveredCatalogs && (
                      <div className="space-y-4">
                        {batchProgress ? (
                          <div className="bg-[#1a1a1a] border border-[#444444] rounded-lg p-5 space-y-4">
                            <div className="flex items-center gap-3">
                              {batchProgress.done < batchProgress.total ? (
                                <Loader2 className="w-5 h-5 animate-spin text-brand-orange" />
                              ) : batchProgress.failures.length === 0 ? (
                                <CheckCircle2 className="w-5 h-5 text-green-400" />
                              ) : (
                                <AlertCircle className="w-5 h-5 text-red-400" />
                              )}
                              <div className="flex-1">
                                <div className="text-sm text-white font-medium">
                                  {batchProgress.done < batchProgress.total
                                    ? 'Creating connections…'
                                    : batchProgress.failures.length === 0
                                      ? 'All connections created'
                                      : `${batchProgress.total - batchProgress.failures.length} of ${batchProgress.total} created · ${batchProgress.failures.length} failed`}
                                </div>
                                <div className="text-xs text-gray-400 mt-0.5">{batchProgress.done} / {batchProgress.total} processed</div>
                              </div>
                            </div>
                            <div className="w-full h-2 bg-[#333333] rounded overflow-hidden">
                              <div
                                className={`h-2 rounded transition-all ${batchProgress.failures.length > 0 && batchProgress.done === batchProgress.total ? 'bg-red-500' : 'bg-brand-orange'}`}
                                style={{ width: `${(batchProgress.done / batchProgress.total) * 100}%` }}
                              />
                            </div>
                            {batchProgress.failures.length > 0 && (
                              <div className="space-y-1 max-h-[180px] overflow-y-auto custom-scrollbar pr-1">
                                {batchProgress.failures.map((f, i) => (
                                  <div key={i} className="flex items-start gap-2 bg-red-900/20 border border-red-700/40 rounded px-2 py-1.5 text-xs">
                                    <AlertCircle className="w-3 h-3 mt-0.5 text-red-400 flex-shrink-0" />
                                    <div className="flex-1">
                                      <div className="font-mono text-red-200">{f.pair.catalog}.{f.pair.schema ?? '*'}</div>
                                      <div className="text-red-300/80">{f.error}</div>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ) : (
                          <>
                            <div className="grid grid-cols-2 gap-3">
                              <div>
                                <Label htmlFor="databricksNamePrefix" className="text-white text-sm">Name prefix <span className="text-gray-500">(optional)</span></Label>
                                <Input
                                  id="databricksNamePrefix"
                                  placeholder="My Workspace"
                                  value={databricksNamePrefix}
                                  onChange={(e) => setDatabricksNamePrefix(e.target.value)}
                                  className="mt-1 bg-[#1a1a1a] border-[#555555] text-white text-sm"
                                />
                              </div>
                              <div>
                                <Label htmlFor="databricksFilter" className="text-white text-sm">Filter catalogs</Label>
                                <div className="relative mt-1">
                                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
                                  <Input
                                    id="databricksFilter"
                                    placeholder="Search…"
                                    value={databricksCatalogFilter}
                                    onChange={(e) => setDatabricksCatalogFilter(e.target.value)}
                                    className="bg-[#1a1a1a] border-[#555555] text-white text-sm pl-8"
                                  />
                                </div>
                              </div>
                            </div>
                            <p className="text-xs text-gray-400 -mt-2">Each pick becomes its own connection sharing this access token. Rotate later by editing each.</p>

                            <div className="border border-[#444444] rounded-md divide-y divide-[#3a3a3a] max-h-[300px] overflow-y-auto custom-scrollbar">
                              {discoveredCatalogs.length === 0 && (
                                <div className="p-6 text-sm text-gray-400 text-center">No catalogs visible to this token.</div>
                              )}
                              {discoveredCatalogs
                                .filter(c => !databricksCatalogFilter.trim() || c.name.toLowerCase().includes(databricksCatalogFilter.toLowerCase().trim()))
                                .map(cat => {
                                  const isExpanded = expandedCatalogs.has(cat.name)
                                  const allPair: DatabricksPair = { catalog: cat.name, schema: null }
                                  const allSelected = isPairSelected(allPair)
                                  const specificCount = selectedPairs.filter(p => p.catalog === cat.name && p.schema !== null).length
                                  const isCatalogActive = allSelected || specificCount > 0
                                  return (
                                    <div key={cat.name} className={`${isCatalogActive ? 'bg-brand-orange/5' : 'bg-[#1a1a1a]'} transition-colors`}>
                                      <div className="flex items-center gap-2 px-3 py-2.5">
                                        <button
                                          type="button"
                                          onClick={() => setExpandedCatalogs(prev => {
                                            const next = new Set(prev)
                                            if (next.has(cat.name)) next.delete(cat.name)
                                            else next.add(cat.name)
                                            return next
                                          })}
                                          className="text-gray-400 hover:text-white"
                                        >
                                          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                        </button>
                                        <Database className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                                        <span className="text-white text-sm font-mono flex-1 truncate">{cat.name}</span>
                                        {specificCount > 0 && !allSelected && (
                                          <span className="text-[10px] uppercase tracking-wide bg-brand-orange/15 text-brand-orange px-1.5 py-0.5 rounded">{specificCount} picked</span>
                                        )}
                                        <label className="flex items-center gap-1.5 text-xs text-gray-300 cursor-pointer hover:text-white">
                                          <input
                                            type="checkbox"
                                            checked={allSelected}
                                            onChange={() => togglePair(allPair)}
                                            className="accent-brand-orange"
                                          />
                                          All ({cat.schemas.length})
                                        </label>
                                      </div>
                                      {isExpanded && (
                                        <div className="px-3 pb-2.5 pl-11 grid grid-cols-2 gap-x-3 gap-y-1">
                                          {cat.schemas.length === 0 && <div className="text-xs text-gray-500 col-span-2">No accessible schemas.</div>}
                                          {cat.schemas.map(s => {
                                            const pair: DatabricksPair = { catalog: cat.name, schema: s }
                                            const checked = isPairSelected(pair)
                                            return (
                                              <label key={s} className={`flex items-center gap-2 text-sm cursor-pointer py-0.5 ${allSelected ? 'text-gray-500' : checked ? 'text-brand-orange' : 'text-gray-200 hover:text-white'}`}>
                                                <input
                                                  type="checkbox"
                                                  checked={checked}
                                                  disabled={allSelected}
                                                  onChange={() => togglePair(pair)}
                                                  className="accent-brand-orange"
                                                />
                                                <span className="font-mono truncate">{s}</span>
                                              </label>
                                            )
                                          })}
                                        </div>
                                      )}
                                    </div>
                                  )
                                })}
                            </div>

                            <div className="flex items-center justify-between bg-[#1a1a1a] border border-[#444444] rounded-md px-3 py-2 text-sm">
                              <span className="text-gray-300">
                                {selectedPairs.length === 0
                                  ? <span className="text-gray-500">No selection yet — pick at least one catalog or schema</span>
                                  : <><span className="text-white font-semibold">{selectedPairs.length}</span> connection{selectedPairs.length !== 1 ? 's' : ''} will be created</>}
                              </span>
                              {selectedPairs.length > 0 && (
                                <button type="button" onClick={() => setSelectedPairs([])} className="text-xs text-gray-400 hover:text-white">Clear</button>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    )}

                    {(selectedType === 'pg' || selectedType === 'mysql' || selectedType === 'mssql') && (
                      <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <Label htmlFor="host" className="text-white">Host <span className="text-red-400">*</span></Label>
                            <Input
                              id="host"
                              placeholder="localhost"
                              value={connectionConfig.host}
                              onChange={(e) => setConnectionConfig(prev => ({ ...prev, host: e.target.value }))}
                              disabled={createMutation.isPending}
                              className="mt-1 bg-[#1a1a1a] border-[#555555] text-white"
                            />
                          </div>
                          <div>
                            <Label htmlFor="port" className="text-white">Port <span className="text-red-400">*</span></Label>
                            <Input
                              id="port"
                              placeholder={selectedType === 'mysql' ? '3306' : selectedType === 'mssql' ? '1433' : '5432'}
                              value={connectionConfig.port}
                              onChange={(e) => setConnectionConfig(prev => ({ ...prev, port: e.target.value }))}
                              disabled={createMutation.isPending}
                              className="mt-1 bg-[#1a1a1a] border-[#555555] text-white"
                            />
                          </div>
                        </div>

                        <div>
                          <Label htmlFor="database" className="text-white">Database <span className="text-red-400">*</span></Label>
                          <Input
                            id="database"
                            placeholder="database name"
                            value={connectionConfig.database}
                            onChange={(e) => setConnectionConfig(prev => ({ ...prev, database: e.target.value }))}
                            disabled={createMutation.isPending}
                            className="mt-1 bg-[#1a1a1a] border-[#555555] text-white"
                          />
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <Label htmlFor="user" className="text-white">User <span className="text-red-400">*</span></Label>
                            <Input
                              id="user"
                              placeholder="user"
                              value={connectionConfig.user}
                              onChange={(e) => setConnectionConfig(prev => ({ ...prev, user: e.target.value }))}
                              disabled={createMutation.isPending}
                              className="mt-1 bg-[#1a1a1a] border-[#555555] text-white"
                            />
                          </div>
                          <div>
                            <Label htmlFor="password" className="text-white">Password <span className="text-red-400">*</span></Label>
                            <Input
                              id="password"
                              type="password"
                              placeholder="password"
                              value={connectionConfig.password}
                              onChange={(e) => setConnectionConfig(prev => ({ ...prev, password: e.target.value }))}
                              disabled={createMutation.isPending}
                              className="mt-1 bg-[#1a1a1a] border-[#555555] text-white"
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Action Buttons */}
                <div className="border-t border-[#444444] p-6 flex justify-end gap-2">
                  {selectedType === 'databricks' ? (
                    <>
                      <Button
                        variant="outline"
                        onClick={() => {
                          if (batchProgress && batchProgress.done < batchProgress.total) return
                          if (databricksStep === 2) {
                            setDatabricksStep(1)
                            setBatchProgress(null)
                          } else {
                            setShowCreateDialog(false)
                            resetForm()
                          }
                        }}
                        disabled={!!(batchProgress && batchProgress.done < batchProgress.total) || discovering}
                        className="border-[#555555] text-white hover:bg-[#3a3a3a]"
                      >
                        {databricksStep === 2 ? 'Back' : 'Cancel'}
                      </Button>
                      {databricksStep === 1 ? (
                        <Button
                          onClick={handleDatabricksDiscover}
                          disabled={!isCreateFormValid || discovering}
                          className={`${isCreateFormValid && !discovering ? 'bg-brand-orange hover:bg-brand-orange/90' : 'bg-gray-500 cursor-not-allowed'} flex items-center gap-2`}
                        >
                          {discovering && <Loader2 className="w-4 h-4 animate-spin" />}
                          Next →
                        </Button>
                      ) : (
                        <Button
                          onClick={handleDatabricksBatchCreate}
                          disabled={!isCreateFormValid || !!(batchProgress && batchProgress.done < batchProgress.total)}
                          className={`${isCreateFormValid && !batchProgress ? 'bg-brand-orange hover:bg-brand-orange/90' : 'bg-gray-500 cursor-not-allowed'} flex items-center gap-2`}
                        >
                          {batchProgress && batchProgress.done < batchProgress.total && <Loader2 className="w-4 h-4 animate-spin" />}
                          {batchProgress && batchProgress.failures.length > 0 && batchProgress.done === batchProgress.total
                            ? `Retry ${batchProgress.failures.length} failed`
                            : `Create ${selectedPairs.length} connection${selectedPairs.length !== 1 ? 's' : ''}`}
                        </Button>
                      )}
                    </>
                  ) : (
                  <>
                  <Button
                    variant="outline"
                    onClick={() => {
                      setShowCreateDialog(false)
                      resetForm()
                    }}
                    disabled={createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className="border-[#555555] text-white hover:bg-[#3a3a3a]"
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={() => {
                      if (selectedType === 'upload' || selectedType === 'url') {
                        handleCreateDialogSubmit()
                      } else {
                        handleCreateConnection()
                      }
                    }}
                    disabled={
                      (selectedType === 'upload' && (!uploadConnectionName.trim() || !uploadFileType || uploadFiles.length === 0)) ||
                      (selectedType === 'url' && (!uploadConnectionName.trim() || uploadURLs.filter(u => u.trim()).length === 0)) ||
                      (selectedType !== 'upload' && selectedType !== 'url' && !isCreateFormValid) ||
                      createMutation.isPending ||
                      uploadMultipleFilesMutation.isPending ||
                      uploadFromURLMutation.isPending
                    }
                    className={`${
                      ((selectedType === 'upload' && uploadConnectionName.trim() && uploadFileType && uploadFiles.length > 0) ||
                       (selectedType === 'url' && uploadConnectionName.trim() && uploadURLs.filter(u => u.trim()).length > 0) ||
                       (selectedType !== 'upload' && selectedType !== 'url' && isCreateFormValid)) &&
                      !createMutation.isPending &&
                      !uploadMultipleFilesMutation.isPending &&
                      !uploadFromURLMutation.isPending
                        ? 'bg-brand-orange hover:bg-brand-orange/90'
                        : 'bg-gray-500 cursor-not-allowed'
                    } flex items-center gap-2`}
                  >
                    {(createMutation.isPending || uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending) && (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    )}
                    {uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'Creating...' : 'Create Datasource'}
                  </Button>
                  </>
                  )}
                </div>
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
              <DialogTitle className="text-white">Delete Database Connection?</DialogTitle>
            </DialogHeader>

            <div className="space-y-4">
              <p className="text-sm text-[#aaaaaa]">
              This action will permanently delete <span className="font-semibold text-white">"{connectionToDelete?.name}"</span> database.
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

        {/* Delete Confirmation Dialog */}
        <Dialog open={deleteDialogOpen} onOpenChange={(open) => {
          if (!open && deleteMutation.isPending) return
          setDeleteDialogOpen(open)
        }}>
          <DialogContent className="max-w-md bg-[#2a2a2a] border-[#444444]">
            <DialogHeader>
              <DialogTitle className="text-white">Delete Database Connection?</DialogTitle>
            </DialogHeader>
            
            <div className="space-y-4">
              <p className="text-sm text-[#aaaaaa]">
              This action will permanently delete <span className="font-semibold text-white">"{connectionToDelete?.name}"</span> database.
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

        {/* Upload Files Dialog */}
        <Dialog open={showUploadDialog} onOpenChange={(open) => {
          if (!open && (uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending)) return
          setShowUploadDialog(open)
          if (!open) resetUploadForm()
        }}>
          <DialogContent className="max-w-2xl bg-[#2a2a2a] border-[#444444]">
            <DialogHeader>
              <DialogTitle className="text-white">Upload Data Files</DialogTitle>
            </DialogHeader>

            <div className="space-y-4">
              {/* Progress Indicator - Only show for URL downloads */}
              {uploadFromURLMutation.isPending && (
                <div className="bg-orange-900/20 border border-brand-orange rounded-lg p-4">
                  <div className="flex items-center gap-3">
                    <Loader2 className="w-5 h-5 text-brand-orange animate-spin flex-shrink-0" />
                    <div className="flex-1">
                      <p className="text-sm font-medium text-brand-orange">
                        Downloading files from URLs...
                      </p>
                      <p className="text-xs text-gray-400 mt-1">
                        Downloading {uploadURLs.filter(u => u.trim()).length} file(s). This may take a while for large files.
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        // Abort the ongoing request
                        if (urlAbortController) {
                          urlAbortController.abort()
                          setUrlAbortController(null)
                        }
                        uploadFromURLMutation.reset()
                        // Don't close dialog, just reset to initial state
                      }}
                      className="text-red-400 hover:text-red-300 hover:bg-red-900/20 h-8 w-8 p-0 flex-shrink-0"
                      title="Cancel download"
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              )}

              {/* Connection Name */}
              <div>
                <Label htmlFor="upload-connection-name" className="text-white">
                  Datasource Name <span className="text-red-400">*</span>
                </Label>
                <Input
                  id="upload-connection-name"
                  value={uploadConnectionName}
                  onChange={(e) => setUploadConnectionName(e.target.value)}
                  placeholder="My File Datasource"
                  disabled={uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                  className="mt-1 bg-[#1a1a1a] border-[#555555] text-white placeholder-[#888888]"
                />
              </div>

              {/* File Type Display (Auto-detected) */}
              {uploadFiles.length > 0 && uploadFileType && (
                <div className="bg-[#1a1a1a] border border-[#555555] rounded-md px-4 py-2 flex items-center justify-between">
                  <span className="text-sm text-white">
                    File Type: <span className="font-medium">{uploadFileType.toUpperCase()}</span>
                  </span>
                  <span className="text-sm text-gray-400">
                    {uploadFiles.length} file{uploadFiles.length !== 1 ? 's' : ''}
                  </span>
                </div>
              )}

              {/* Upload Mode Toggle */}
              <div>
                <Label className="text-white">Upload Method</Label>
                <div className="flex gap-2 mt-2">
                  <Button
                    type="button"
                    variant={uploadMode === 'file' ? 'default' : 'outline'}
                    onClick={() => setUploadMode('file')}
                    disabled={uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className="flex-1"
                  >
                    Upload Files
                  </Button>
                  <Button
                    type="button"
                    variant={uploadMode === 'url' ? 'default' : 'outline'}
                    onClick={() => setUploadMode('url')}
                    disabled={uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                    className="flex-1"
                  >
                    From URL
                  </Button>
                </div>
              </div>

              {/* Conditional Rendering based on mode */}
              {uploadMode === 'file' ? (
              /* Drag and Drop Area with Files Inside */
              <div
                className={`border-2 border-dashed rounded-lg transition-colors ${
                  isDragging
                    ? 'border-brand-orange bg-brand-orange/10'
                    : 'border-[#555555] hover:border-[#777777] hover:bg-[#333333]'
                } ${uploadMultipleFilesMutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                onDragOver={(e) => {
                  if (!uploadMultipleFilesMutation.isPending) {
                    e.preventDefault()
                    setIsDragging(true)
                  }
                }}
                onDragLeave={(e) => {
                  e.preventDefault()
                  setIsDragging(false)
                }}
                onDrop={(e) => {
                  if (!uploadMultipleFilesMutation.isPending) {
                    handleUploadFilesDrop(e)
                  }
                }}
              >
                <input
                  ref={uploadFileInputRef}
                  type="file"
                  accept=".csv,.xlsx,.xls,.parquet,.json"
                  multiple
                  onChange={handleUploadFilesChange}
                  disabled={uploadMultipleFilesMutation.isPending}
                  className="hidden"
                />

                {/* Upload Prompt - Clickable area at top */}
                <div
                  className={`p-6 text-center ${uploadMultipleFilesMutation.isPending ? 'cursor-not-allowed' : 'cursor-pointer'}`}
                  onClick={() => {
                    if (!uploadMultipleFilesMutation.isPending) {
                      uploadFileInputRef.current?.click()
                    }
                  }}
                >
                  <Upload className={`${uploadFiles.length > 0 ? 'w-8 h-8' : 'w-12 h-12'} mx-auto mb-3 text-brand-orange`} />
                  <p className={`text-white font-medium ${uploadFiles.length > 0 ? 'text-sm mb-1' : 'mb-2'}`}>
                    {uploadFiles.length > 0
                      ? `Drag & drop more ${uploadFileType.toUpperCase()} files here`
                      : 'Drag & drop your data files here'}
                  </p>
                  <p className={`text-gray-400 ${uploadFiles.length > 0 ? 'text-xs mb-2' : 'text-sm mb-4'}`}>
                    or click to browse
                  </p>
                  <p className="text-xs text-gray-500">
                    {uploadFiles.length > 0
                      ? 'All files must be the same type'
                      : 'Supported: CSV, Excel, Parquet, JSON • Type auto-detected'}
                  </p>
                </div>

                {/* Uploaded Files List - Inside dotted area */}
                {uploadFiles.length > 0 && (
                  <div className="px-6 pb-6">
                    {/* Header with file count and clear button */}
                    <div className="flex items-center justify-between mb-3 pb-3 border-t border-[#555555] pt-3">
                      <Label className="text-white text-sm">{uploadFiles.length} file(s) selected</Label>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation()
                          setUploadFiles([])
                          setUploadFileAliases({})
                          if (uploadFileInputRef.current) {
                            uploadFileInputRef.current.value = ''
                          }
                        }}
                        disabled={uploadMultipleFilesMutation.isPending}
                        className="text-red-400 hover:text-red-300 hover:bg-red-900/20 h-7 text-xs"
                      >
                        Clear All
                      </Button>
                    </div>

                    {/* Files list with scroll */}
                    <div className="max-h-[200px] overflow-y-auto custom-scrollbar space-y-2 pr-1">
                      {uploadFiles.map((file, index) => (
                        <div
                          key={index}
                          className="p-3 bg-[#1a1a1a] border border-[#555555] rounded-md"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div className="flex items-center gap-2 flex-1 min-w-0">
                              <FileText className="w-4 h-4 text-brand-orange flex-shrink-0" />
                              <p className="text-sm text-white font-medium flex-1 truncate" title={file.name}>
                                {truncateFilename(file.name)}
                              </p>
                              <p className="text-xs text-gray-400 flex-shrink-0">
                                {formatFileSize(file.size)}
                              </p>
                            </div>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={(e) => {
                                e.stopPropagation()
                                const newFiles = uploadFiles.filter((_, i) => i !== index)
                                setUploadFiles(newFiles)
                                const newAliases = { ...uploadFileAliases }
                                delete newAliases[file.name]
                                setUploadFileAliases(newAliases)
                              }}
                              disabled={uploadMultipleFilesMutation.isPending}
                              className="text-red-400 hover:text-red-300 hover:bg-red-900/20 h-8 w-8 p-0 flex-shrink-0"
                            >
                              <X className="w-4 h-4" />
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              ) : (
              /* URL Input Mode */
              <div className="space-y-3">
                <Label className="text-white">File URLs</Label>
                {uploadURLs.map((url, index) => (
                  <div key={index} className="flex gap-2">
                    <Input
                      value={url}
                      onChange={(e) => {
                        const newURLs = [...uploadURLs]
                        newURLs[index] = e.target.value
                        // Try to detect file type from URL
                        if (index === 0 && !uploadFileType && e.target.value) {
                          const urlFileName = e.target.value.split('/').pop() || ''
                          const detectedType = detectFileType(urlFileName)
                          if (detectedType) {
                            setUploadFileType(detectedType)
                          }
                        }
                        setUploadURLs(newURLs)
                      }}
                      placeholder={`https://example.com/data${uploadFileType ? '.' + (uploadFileType === 'excel' ? 'xlsx' : uploadFileType) : ''}`}
                      disabled={uploadFromURLMutation.isPending}
                      className="flex-1 bg-[#1a1a1a] border-[#555555] text-white placeholder-[#888888]"
                    />
                    {uploadURLs.length > 1 && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setUploadURLs(uploadURLs.filter((_, i) => i !== index))
                        }}
                        disabled={uploadFromURLMutation.isPending}
                        className="text-red-400 hover:text-red-300 hover:bg-red-900/20"
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    )}
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setUploadURLs([...uploadURLs, ''])}
                  disabled={uploadFromURLMutation.isPending}
                  className="w-full border-[#555555] text-white hover:bg-[#3a3a3a]"
                >
                  + Add Another URL
                </Button>
                <p className="text-xs text-gray-400">
                  Enter public URLs to data files (CSV, Excel, Parquet, JSON) or ZIP archives of these types.
                </p>
              </div>
              )}

              {/* Action Buttons */}
              <div className="flex justify-end gap-2 mt-6">
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowUploadDialog(false)
                    resetUploadForm()
                  }}
                  disabled={uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending}
                  className="border-[#555555] text-white hover:bg-[#3a3a3a]"
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleUploadFilesSubmit}
                  disabled={
                    !uploadConnectionName.trim() ||
                    (uploadMode === 'file' && (!uploadFileType || uploadFiles.length === 0)) ||
                    (uploadMode === 'url' && uploadURLs.filter(u => u.trim()).length === 0) ||
                    uploadMultipleFilesMutation.isPending ||
                    uploadFromURLMutation.isPending
                  }
                  className={`${
                    uploadConnectionName.trim() &&
                    ((uploadMode === 'file' && uploadFileType && uploadFiles.length > 0) ||
                     (uploadMode === 'url' && uploadURLs.filter(u => u.trim()).length > 0)) &&
                    !uploadMultipleFilesMutation.isPending &&
                    !uploadFromURLMutation.isPending
                      ? 'bg-brand-orange hover:bg-brand-orange/90'
                      : 'bg-gray-500 cursor-not-allowed'
                  } flex items-center gap-2`}
                >
                  {(uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending) && (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  )}
                  {uploadMultipleFilesMutation.isPending || uploadFromURLMutation.isPending ? 'Creating...' : 'Create Datasource'}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>
  )
}
