"use client"

import { useState, useRef, useMemo, useEffect } from 'react'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog'
import { Loader2, Upload, FileText, X, Link as LinkIcon, Leaf, Cylinder, Server, HardDrive, Database, Cloud, ChevronDown, ChevronRight, CheckCircle2, AlertCircle, Search } from 'lucide-react'
import { ApiService } from '../services/api'
import type { ConnectionType, Datasource, DatabricksCatalog, DatabricksOAuthTokens, DatabricksWarehouse } from '../services/api'
import { DatabricksOAuthSettings } from './databricks/DatabricksOAuthSettings'
import { useAppConfig } from '../hooks/useAppConfig'
import { openExternalUrl } from '../lib/tauri-api'

type DatabricksPair = { catalog: string; schema: string | null }
const pairKey = (p: DatabricksPair) => `${p.catalog}::${p.schema ?? '*'}`

export interface DatabaseConnectionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  mode?: 'create' | 'select'

  // For select mode (show previous connections)
  datasources?: Datasource[]
  selectedConnectionId?: string
  selectedConnectionIds?: string[]  // Multi-select support
  onConnectionSelect?: (id: string) => void
  onConfirmConnection?: () => void | Promise<void>
  multiSelect?: boolean  // Enable multi-select mode

  // For create mode
  onCreateConnection?: (data: {
    type: ConnectionType | 'upload' | 'url'
    name: string
    connectionObj?: any
    files?: File[]
    aliases?: Record<string, string>
    fileType?: 'csv' | 'excel' | 'parquet' | 'json'
    urls?: string[]
  }) => Promise<void>

  // Optional customization
  title?: string
  submitButtonText?: string
  showPreviousConnectionsOption?: boolean
  isLoading?: boolean

  // Callbacks
  onSwitchToCreate?: () => void
  onSwitchToSelect?: () => void

  // Called after a successful Databricks batch-create. Receives count of created rows.
  onDatabricksConnectionsCreated?: (count: number) => void
}

export function DatabaseConnectionDialog({
  open,
  onOpenChange,
  mode = 'create',
  datasources = [],
  selectedConnectionId,
  selectedConnectionIds = [],
  onConnectionSelect,
  onConfirmConnection,
  onCreateConnection,
  title,
  submitButtonText,
  showPreviousConnectionsOption = true,
  isLoading = false,
  onSwitchToCreate,
  onSwitchToSelect,
  multiSelect = false,
  onDatabricksConnectionsCreated,
}: DatabaseConnectionDialogProps) {
  const { isSelfHosted } = useAppConfig()
  // Handle dialog close - only close if in select mode or no previous connections
  const handleClose = () => {
    if (isLoading) return

    // If we're in create mode and have previous connections, go back to select mode
    if (mode === 'create' && showPreviousConnectionsOption && datasources.length > 0 && onSwitchToSelect) {
      onSwitchToSelect()
    } else {
      // Otherwise close the dialog
      onOpenChange(false)
    }
  }
  // Connection type selection
  const [selectedType, setSelectedType] = useState<ConnectionType | 'upload' | 'url'>('upload')

  // Form state for database connections
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

  // Upload files state
  const [uploadFiles, setUploadFiles] = useState<File[]>([])
  const [uploadFileAliases, setUploadFileAliases] = useState<Record<string, string>>({})
  const [uploadFileType, setUploadFileType] = useState<'csv' | 'excel' | 'parquet' | 'json' | ''>('')
  const [isDragging, setIsDragging] = useState(false)
  const uploadFileInputRef = useRef<HTMLInputElement>(null)

  // URL upload state
  const [uploadURLs, setUploadURLs] = useState<string[]>([''])

  // Databricks 3-step wizard state (Sign in → Pick warehouse → Pick catalogs/schemas)
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

  const [databricksOAuthConfigured, setDatabricksOAuthConfigured] = useState<boolean>(!isSelfHosted)
  const [databricksOAuthCanConfigure, setDatabricksOAuthCanConfigure] = useState<boolean>(false)
  const [showManageDatabricksOAuth, setShowManageDatabricksOAuth] = useState<boolean>(false)
  const [oauthTokens, setOauthTokens] = useState<DatabricksOAuthTokens | null>(null)
  const [, setOauthState] = useState<string | null>(null)
  const oauthStateRef = useRef<string | null>(null)
  const oauthAbortRef = useRef<AbortController | null>(null)
  const [oauthSigningIn, setOauthSigningIn] = useState(false)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const [warehouses, setWarehouses] = useState<DatabricksWarehouse[] | null>(null)
  const [selectedWarehouseId, setSelectedWarehouseId] = useState<string | null>(null)
  const [loadingWarehouses, setLoadingWarehouses] = useState(false)

  const refreshDatabricksAuthStatus = () => {
    ApiService.getDatabricksAuthStatus()
      .then(s => {
        setDatabricksOAuthConfigured(!!s.configured)
        setDatabricksOAuthCanConfigure(!!s.can_configure)
      })
      .catch(() => {
        setDatabricksOAuthConfigured(!isSelfHosted)
        setDatabricksOAuthCanConfigure(false)
      })
  }

  useEffect(() => {
    if (open) refreshDatabricksAuthStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    return () => {
      if (oauthAbortRef.current) {
        oauthAbortRef.current.abort()
        oauthAbortRef.current = null
      }
      const pendingState = oauthStateRef.current
      if (pendingState) {
        ApiService.cancelDatabricksOAuth(pendingState).catch(() => {})
        oauthStateRef.current = null
      }
    }
  }, [])

  const databricksTileVisible = !isSelfHosted || databricksOAuthConfigured || databricksOAuthCanConfigure

  // Helper functions
  const formatDbType = (type: string): string => {
    switch (type) {
      case 'pg': return 'PostgreSQL'
      case 'mongo': return 'MongoDB'
      case 'mysql': return 'MySQL'
      case 'sqlite': return 'SQLite'
      case 'mssql': return 'SQL Server'
      case 'dynamodb': return 'DynamoDB'
      case 'databricks': return 'Databricks'
      case 'csv': return 'CSV File'
      case 'excel': return 'Excel File'
      case 'parquet': return 'Parquet File'
      case 'json': return 'JSON File'
      case 'duckdb': return 'DuckDB File Dataset'
      default: return type.toUpperCase()
    }
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

  const detectFileType = (filename: string): 'csv' | 'excel' | 'parquet' | 'json' | null => {
    const lowerName = filename.toLowerCase()
    if (lowerName.endsWith('.csv')) return 'csv'
    if (lowerName.endsWith('.xlsx') || lowerName.endsWith('.xls')) return 'excel'
    if (lowerName.endsWith('.parquet')) return 'parquet'
    if (lowerName.endsWith('.json')) return 'json'
    return null
  }

  // Form validation
  const isFormValid = useMemo(() => {
    if (selectedType !== 'databricks' && !connectionConfig.name.trim()) return false

    if (selectedType === 'upload') {
      return uploadFileType !== '' && uploadFiles.length > 0
    }

    if (selectedType === 'url') {
      return uploadURLs.filter(u => u.trim()).length > 0
    }

    if (selectedType === 'mongo') {
      return connectionConfig.connectionString.trim().length > 0
    }

    if (selectedType === 'sqlite') {
      return connectionConfig.database.trim().length > 0
    }

    if (selectedType === 'dynamodb') {
      return (
        connectionConfig.region.trim().length > 0 &&
        connectionConfig.accessKeyId.trim().length > 0 &&
        connectionConfig.secretAccessKey.trim().length > 0
      )
    }

    if (selectedType === 'databricks') {
      const signedIn = !!oauthTokens?.access_token
      const warehousePicked = !!selectedWarehouseId
      if (databricksStep === 1) return signedIn && warehousePicked
      return signedIn && warehousePicked && selectedPairs.length > 0
    }

    // PostgreSQL, MySQL, MSSQL
    return (
      connectionConfig.host.trim().length > 0 &&
      connectionConfig.port.trim().length > 0 &&
      connectionConfig.database.trim().length > 0 &&
      connectionConfig.user.trim().length > 0 &&
      connectionConfig.password.trim().length > 0
    )
  }, [selectedType, connectionConfig, uploadFileType, uploadFiles, uploadURLs, databricksStep, selectedPairs, oauthTokens, selectedWarehouseId])

  const resetDatabricksWizard = () => {
    if (oauthAbortRef.current) {
      oauthAbortRef.current.abort()
      oauthAbortRef.current = null
    }
    const pendingState = oauthStateRef.current
    if (pendingState) {
      ApiService.cancelDatabricksOAuth(pendingState).catch(() => {})
    }
    oauthStateRef.current = null
    setDatabricksStep(1)
    setOauthTokens(null)
    setOauthState(null)
    setOauthError(null)
    setOauthSigningIn(false)
    setWarehouses(null)
    setSelectedWarehouseId(null)
    setLoadingWarehouses(false)
    setDiscoveredCatalogs(null)
    setDiscovering(false)
    setDiscoverError(null)
    setSelectedPairs([])
    setExpandedCatalogs(new Set())
    setDatabricksNamePrefix('')
    setBatchProgress(null)
    setDatabricksCatalogFilter('')
  }

  // Handle type change
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

  // Handle file upload
  const handleUploadFilesChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || [])
    handleUploadFilesSelection(selectedFiles)
  }

  const handleUploadFilesDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
    const droppedFiles = Array.from(e.dataTransfer.files)
    handleUploadFilesSelection(droppedFiles)
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

    if (!currentFileType) {
      alert('Please upload files to detect type')
      return
    }

    const extensions = allowedExtensions[currentFileType as 'csv' | 'excel' | 'parquet' | 'json']
    const typeLabel = currentFileType.toUpperCase()

    // Check for duplicates
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

    // Add files
    setUploadFiles(prev => [...prev, ...selectedFiles])

    // Auto-generate aliases
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

  const togglePair = (pair: DatabricksPair) => {
    setSelectedPairs(prev => {
      const key = pairKey(pair)
      const exists = prev.some(p => pairKey(p) === key)
      if (exists) return prev.filter(p => pairKey(p) !== key)
      // Selecting "all schemas" for a catalog clears any specific-schema picks for it
      // Selecting a specific schema clears the "all" pick for that catalog
      const cleaned = pair.schema === null
        ? prev.filter(p => p.catalog !== pair.catalog)
        : prev.filter(p => !(p.catalog === pair.catalog && p.schema === null))
      return [...cleaned, pair]
    })
  }

  const isPairSelected = (pair: DatabricksPair) =>
    selectedPairs.some(p => pairKey(p) === pairKey(pair))

  const handleCancelDatabricksSignIn = () => {
    const pendingState = oauthStateRef.current
    if (oauthAbortRef.current) {
      oauthAbortRef.current.abort()
      oauthAbortRef.current = null
    }
    if (pendingState) {
      ApiService.cancelDatabricksOAuth(pendingState).catch(() => {})
    }
    oauthStateRef.current = null
    setOauthState(null)
    setOauthSigningIn(false)
    setOauthError(null)
  }

  const normalizeDatabricksHost = (raw: string): string => {
    let h = raw.trim()
    const schemeIdx = h.indexOf('://')
    if (schemeIdx !== -1) h = h.slice(schemeIdx + 3)
    h = h.split('/')[0].split('?')[0]
    return h.trim().replace(/\.+$/, '')
  }

  const handleDatabricksSignIn = async () => {
    const normalizedHost = normalizeDatabricksHost(connectionConfig.serverHostname)
    if (!normalizedHost) {
      setOauthError('Enter your Databricks workspace URL first')
      return
    }
    if (normalizedHost !== connectionConfig.serverHostname) {
      setConnectionConfig(prev => ({ ...prev, serverHostname: normalizedHost }))
    }
    if (oauthAbortRef.current) {
      oauthAbortRef.current.abort()
    }
    const controller = new AbortController()
    oauthAbortRef.current = controller
    const { signal } = controller
    setOauthError(null)
    setOauthSigningIn(true)
    try {
      const { auth_url, state } = await ApiService.startDatabricksOAuth(normalizedHost)
      if (signal.aborted) return
      oauthStateRef.current = state
      setOauthState(state)
      await openExternalUrl(auth_url)
      if (signal.aborted) return

      const sleep = (ms: number) => new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', onAbort)
          resolve()
        }, ms)
        const onAbort = () => {
          clearTimeout(timer)
          reject(new DOMException('aborted', 'AbortError'))
        }
        signal.addEventListener('abort', onAbort, { once: true })
      })

      const deadline = Date.now() + 5 * 60 * 1000
      while (Date.now() < deadline) {
        await sleep(2000)
        if (signal.aborted) return
        try {
          const res = await ApiService.pollDatabricksOAuthResult(state)
          if (signal.aborted) return
          if (res.status === 'success' && res.tokens) {
            oauthStateRef.current = null
            setOauthTokens(res.tokens)
            setConnectionConfig(prev => ({ ...prev, serverHostname: res.tokens!.server_hostname }))
            await loadWarehouses(res.tokens.server_hostname, res.tokens.access_token)
            return
          }
        } catch (err: any) {
          if (signal.aborted) return
          console.warn('OAuth poll failed:', err?.message)
        }
      }
      if (!signal.aborted) setOauthError('Sign-in timed out. Please try again.')
    } catch (err: any) {
      if (err?.name === 'AbortError' || signal.aborted) return
      setOauthError(err?.message || 'Failed to start Databricks sign-in')
    } finally {
      if (!signal.aborted) {
        setOauthSigningIn(false)
      }
      if (oauthAbortRef.current === controller) {
        oauthAbortRef.current = null
      }
    }
  }

  const loadWarehouses = async (host: string, token: string) => {
    setLoadingWarehouses(true)
    try {
      const ws = await ApiService.listDatabricksWarehouses(host, token)
      setWarehouses(ws)
      if (ws.length === 1) setSelectedWarehouseId(ws[0].id)
    } catch (err: any) {
      setOauthError(err?.message || 'Failed to list warehouses')
    } finally {
      setLoadingWarehouses(false)
    }
  }

  const handleDatabricksDiscover = async () => {
    if (!oauthTokens || !selectedWarehouseId) return
    const warehouse = warehouses?.find(w => w.id === selectedWarehouseId)
    if (!warehouse) return
    setDiscoverError(null)
    setDiscovering(true)
    try {
      const res = await ApiService.discoverDatabricks({
        server_hostname: oauthTokens.server_hostname,
        access_token: oauthTokens.access_token,
        http_path: warehouse.http_path,
      })
      setDiscoveredCatalogs(res.catalogs)
      setConnectionConfig(prev => ({ ...prev, httpPath: warehouse.http_path }))
      setDatabricksStep(2)
    } catch (err: any) {
      setDiscoverError(err?.message || 'Failed to discover Databricks catalogs')
    } finally {
      setDiscovering(false)
    }
  }

  const handleDatabricksBatchCreate = async () => {
    const pairs = selectedPairs
    if (pairs.length === 0) return
    const failures: Array<{ pair: DatabricksPair; error: string }> = []
    let done = 0
    setBatchProgress({ done: 0, total: pairs.length, failures: [] })

    for (const pair of pairs) {
      try {
        const prefix = databricksNamePrefix.trim()
        const suffix = `${pair.catalog}.${pair.schema ?? '*'}`
        const name = prefix ? `${prefix} · ${suffix}` : ''
        if (!oauthTokens) throw new Error('Not signed in to Databricks')
        await ApiService.createConnection({
          type: 'databricks',
          name: name || undefined,
          connection_obj: {
            server_hostname: oauthTokens.server_hostname,
            http_path: connectionConfig.httpPath,
            catalog: pair.catalog,
            schema: pair.schema ?? undefined,
            oauth: {
              access_token: oauthTokens.access_token,
              refresh_token: oauthTokens.refresh_token,
              expires_at: oauthTokens.expires_at,
              scope: oauthTokens.scope,
              server_hostname: oauthTokens.server_hostname,
            },
          },
        })
      } catch (err: any) {
        failures.push({ pair, error: err?.message || 'Unknown error' })
      }
      done += 1
      setBatchProgress({ done, total: pairs.length, failures: [...failures] })
    }

    const succeededCount = pairs.length - failures.length
    if (failures.length === 0) {
      onDatabricksConnectionsCreated?.(succeededCount)
      onOpenChange(false)
    } else {
      // Keep dialog open; reduce selection to failures so user can retry.
      setSelectedPairs(failures.map(f => f.pair))
      if (succeededCount > 0) onDatabricksConnectionsCreated?.(succeededCount)
    }
  }

  // Handle form submit
  const handleSubmit = async () => {
    if (!onCreateConnection) return

    if (selectedType === 'upload') {
      await onCreateConnection({
        type: 'upload',
        name: connectionConfig.name,
        files: uploadFiles,
        aliases: uploadFileAliases,
        fileType: uploadFileType as 'csv' | 'excel' | 'parquet' | 'json'
      })
    } else if (selectedType === 'url') {
      const validURLs = uploadURLs.filter(u => u.trim().length > 0)
      await onCreateConnection({
        type: 'url',
        name: connectionConfig.name,
        urls: validURLs,
        fileType: uploadFileType || undefined
      })
    } else {
      // Database connection
      let connectionObj: Record<string, any>

      if (selectedType === 'mongo') {
        connectionObj = { connection_string: connectionConfig.connectionString }
      } else if (selectedType === 'sqlite') {
        connectionObj = { database: connectionConfig.database }
      } else if (selectedType === 'dynamodb') {
        connectionObj = {
          region: connectionConfig.region,
          access_key_id: connectionConfig.accessKeyId,
          secret_access_key: connectionConfig.secretAccessKey,
          endpoint_url: connectionConfig.endpointUrl || '',
          query_mode: connectionConfig.queryMode,
        }
      } else if (selectedType === 'databricks') {
        if (!oauthTokens) throw new Error('Sign in to Databricks first')
        connectionObj = {
          server_hostname: oauthTokens.server_hostname,
          http_path: connectionConfig.httpPath,
          catalog: connectionConfig.catalog || undefined,
          schema: connectionConfig.databricksSchema || undefined,
          oauth: {
            access_token: oauthTokens.access_token,
            refresh_token: oauthTokens.refresh_token,
            expires_at: oauthTokens.expires_at,
            scope: oauthTokens.scope,
            server_hostname: oauthTokens.server_hostname,
          },
        }
      } else {
        connectionObj = {
          host: connectionConfig.host,
          port: parseInt(connectionConfig.port),
          database: connectionConfig.database,
          user: connectionConfig.user,
          password: connectionConfig.password
        }
      }

      await onCreateConnection({
        type: selectedType as ConnectionType,
        name: connectionConfig.name,
        connectionObj
      })
    }
  }

  // Reset form when dialog closes
  useEffect(() => {
    if (!open) {
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
      setUploadFiles([])
      setUploadFileAliases({})
      setUploadFileType('')
      setUploadURLs([''])
      setIsDragging(false)
      resetDatabricksWizard()
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-4xl bg-[#2a2a2a] border-[#444444] p-0 gap-0">
        <DialogHeader className="px-6 pt-6 pb-4 border-b border-[#444444]">
          <DialogTitle className="text-white text-xl">
            {title || (mode === 'select' ? 'Select Database Connection' : 'Add Database Connection')}
          </DialogTitle>
        </DialogHeader>

        {mode === 'select' ? (
          // Previous Connections List
          <div className="p-6">
            <div className="flex items-center justify-between mb-5">
              <Label className="text-white text-base">
                Previous Connections {multiSelect && selectedConnectionIds.length > 0 && `(${selectedConnectionIds.length} selected)`}
              </Label>
              {showPreviousConnectionsOption && onSwitchToCreate && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={onSwitchToCreate}
                  disabled={isLoading}
                  className="text-blue-500 hover:text-blue-400 hover:bg-blue-500/10 text-sm h-auto py-1"
                >
                  + Add New Connection
                </Button>
              )}
            </div>

            <div className="max-h-[400px] overflow-y-auto custom-scrollbar space-y-3">
              {datasources
                .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                .map((datasource) => {
                  const isSelected = multiSelect
                    ? selectedConnectionIds.includes(datasource.id)
                    : selectedConnectionId === datasource.id
                  return (
                  <div
                    key={datasource.id}
                    onClick={() => !isLoading && onConnectionSelect?.(datasource.id)}
                    className={`p-4 rounded-lg border-2 transition-all ${
                      isLoading ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
                    } ${
                      isSelected
                        ? 'bg-blue-600/10 border-blue-500'
                        : 'bg-[#1a1a1a] border-[#555555] hover:border-[#777777]'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-white text-sm font-medium truncate">
                        {datasource.name || 'Unknown Datasource'}
                      </span>
                      <div className="flex items-center gap-2">
                        {datasource.source_type === 'dataset' && datasource.files_count && (
                          <span className="text-[#aaaaaa] text-xs bg-[#333333] px-2 py-0.5 rounded">
                            {datasource.files_count} file{datasource.files_count > 1 ? 's' : ''}
                          </span>
                        )}
                        <span className="text-[#aaaaaa] text-xs">
                          {formatDbType(datasource.type)}
                        </span>
                      </div>
                    </div>
                  </div>
                  )
                })}
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <Button
                variant="outline"
                onClick={handleClose}
                disabled={isLoading}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                onClick={onConfirmConnection}
                disabled={multiSelect ? selectedConnectionIds.length === 0 || isLoading : !selectedConnectionId || isLoading}
                className={`${
                  (multiSelect ? selectedConnectionIds.length > 0 : selectedConnectionId) && !isLoading
                    ? 'bg-brand-orange hover:bg-brand-orange/90'
                    : 'bg-gray-500 cursor-not-allowed'
                }`}
              >
                {isLoading && <Loader2 className="w-4 h-4 animate-spin mr-2" />}
                {submitButtonText || 'Connect'}
              </Button>
            </div>
          </div>
        ) : (
          // Create Connection with Sidebar
          <div className="flex h-[600px]">
            {/* Sidebar */}
            <div className="w-52 bg-[#1a1a1a] border-r border-[#444444] p-3 overflow-y-auto custom-scrollbar">
              <div className="space-y-1">
                {/* Upload Files */}
                <button
                  onClick={() => handleTypeChange('upload')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'upload'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <Upload className={`w-5 h-5 flex-shrink-0 ${selectedType === 'upload' ? 'text-brand-orange' : ''}`} />
                  <span className="text-sm font-medium">Upload Files</span>
                </button>

                {/* Import from URL */}
                <button
                  onClick={() => handleTypeChange('url')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'url'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <LinkIcon className={`w-5 h-5 flex-shrink-0 ${selectedType === 'url' ? 'text-brand-orange' : ''}`} />
                  <span className="text-sm font-medium">Import from URL</span>
                </button>

                {/* Divider */}
                <div className="my-2 border-t border-[#444444]"></div>

                {/* PostgreSQL */}
                <button
                  onClick={() => handleTypeChange('pg')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'pg'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <Cylinder className="w-5 h-5 flex-shrink-0 text-blue-400" />
                  <span className="text-sm font-medium">PostgreSQL</span>
                </button>

                {/* MongoDB */}
                <button
                  onClick={() => handleTypeChange('mongo')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'mongo'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <Leaf className="w-5 h-5 flex-shrink-0 text-green-500" />
                  <span className="text-sm font-medium">MongoDB</span>
                </button>

                {/* MySQL */}
                <button
                  onClick={() => handleTypeChange('mysql')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'mysql'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <Database className="w-5 h-5 flex-shrink-0 text-orange-400" />
                  <span className="text-sm font-medium">MySQL</span>
                </button>

                {/* SQL Server */}
                <button
                  onClick={() => handleTypeChange('mssql')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'mssql'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <Server className="w-5 h-5 flex-shrink-0 text-red-400" />
                  <span className="text-sm font-medium">SQL Server</span>
                </button>

                {/* SQLite */}
                <button
                  onClick={() => handleTypeChange('sqlite')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'sqlite'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <HardDrive className="w-5 h-5 flex-shrink-0 text-cyan-400" />
                  <span className="text-sm font-medium">SQLite</span>
                </button>

                {/* DynamoDB */}
                <button
                  onClick={() => handleTypeChange('dynamodb')}
                  disabled={isLoading}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                    selectedType === 'dynamodb'
                      ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                      : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                  } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <Cloud className="w-5 h-5 flex-shrink-0 text-amber-400" />
                  <span className="text-sm font-medium">DynamoDB</span>
                </button>

                {/* Databricks: hidden for non-admin team members until admin configures OAuth */}
                {databricksTileVisible && (
                  <button
                    onClick={() => handleTypeChange('databricks')}
                    disabled={isLoading}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-md transition-all text-left ${
                      selectedType === 'databricks'
                        ? 'bg-brand-orange/10 text-white border-l-3 border-brand-orange'
                        : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
                    } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Database className="w-5 h-5 flex-shrink-0 text-red-400" />
                    <span className="text-sm font-medium">Databricks</span>
                  </button>
                )}
              </div>
            </div>

            {/* Form Content Area */}
            <div className="flex-1 flex flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto custom-scrollbar p-6">
                <div className="space-y-4">
                  {/* Connection/Datasource Name - Always shown (hidden for Databricks wizard) */}
                  {selectedType !== 'databricks' && (
                    <div>
                      <Label htmlFor="connection-name" className="text-white">
                        {selectedType === 'upload' || selectedType === 'url' ? 'Datasource Name' : 'Connection Name'} <span className="text-red-400">*</span>
                      </Label>
                      <Input
                        id="connection-name"
                        value={connectionConfig.name}
                        onChange={(e) => setConnectionConfig(prev => ({ ...prev, name: e.target.value }))}
                        placeholder={selectedType === 'upload' || selectedType === 'url' ? 'My File Datasource' : 'My Database Connection'}
                        disabled={isLoading}
                        className="mt-1 bg-[#1a1a1a] border-[#555555] text-white placeholder-[#888888]"
                      />
                    </div>
                  )}

                  {/* Upload Files Form */}
                  {selectedType === 'upload' && (
                    <>
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

                      <div
                        className={`border-2 border-dashed rounded-lg transition-colors ${
                          isDragging
                            ? 'border-brand-orange bg-brand-orange/10'
                            : 'border-[#555555] hover:border-[#777777] hover:bg-[#333333]'
                        } ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                        onDragOver={(e) => {
                          if (!isLoading) {
                            e.preventDefault()
                            setIsDragging(true)
                          }
                        }}
                        onDragLeave={(e) => {
                          e.preventDefault()
                          setIsDragging(false)
                        }}
                        onDrop={(e) => {
                          if (!isLoading) {
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
                          disabled={isLoading}
                          className="hidden"
                        />

                        <div
                          className={`p-6 text-center ${isLoading ? 'cursor-not-allowed' : 'cursor-pointer'}`}
                          onClick={() => {
                            if (!isLoading) {
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
                                disabled={isLoading}
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
                                        if (newFiles.length === 0) {
                                          setUploadFileType('')
                                        }
                                      }}
                                      disabled={isLoading}
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
                            disabled={isLoading}
                            className="flex-1 bg-[#1a1a1a] border-[#555555] text-white placeholder-[#888888]"
                          />
                          {uploadURLs.length > 1 && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setUploadURLs(uploadURLs.filter((_, i) => i !== index))
                              }}
                              disabled={isLoading}
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
                        disabled={isLoading}
                        className="w-full border-[#555555] text-white hover:bg-[#3a3a3a]"
                      >
                        + Add Another URL
                      </Button>
                      <p className="text-xs text-gray-400">
                        Enter public URLs to data files (CSV, Excel, Parquet, JSON) or ZIP archives of these types.
                      </p>
                    </div>
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
                        disabled={isLoading}
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
                        disabled={isLoading}
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
                          disabled={isLoading}
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
                          disabled={isLoading}
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
                          disabled={isLoading}
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
                          disabled={isLoading}
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
                          disabled={isLoading}
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

                  {selectedType === 'databricks' && databricksStep === 1 && isSelfHosted && !databricksOAuthConfigured && !databricksOAuthCanConfigure && (
                    <div className="bg-amber-900/20 border border-amber-700/40 rounded-md p-3 text-sm text-amber-200">
                      Databricks OAuth isn't configured for this workspace. Ask your admin to register a custom OAuth app in the Databricks Account Console and add the credentials in Settings.
                    </div>
                  )}

                  {selectedType === 'databricks' && databricksStep === 1 && isSelfHosted && !databricksOAuthConfigured && databricksOAuthCanConfigure && (
                    <div className="space-y-3">
                      <div className="bg-amber-900/20 border border-amber-700/40 rounded-md p-3 text-sm text-amber-200">
                        Databricks OAuth isn't configured yet. Register Byaan as a custom OAuth app in the Databricks Account Console and paste the credentials below. After saving, every user can sign in with Databricks.
                      </div>
                      <DatabricksOAuthSettings onConfigChanged={refreshDatabricksAuthStatus} />
                    </div>
                  )}

                  {selectedType === 'databricks' && databricksStep === 1 && databricksOAuthConfigured && (
                    <div className="space-y-4">
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-xs text-gray-400 flex-1">
                          Sign in with your Databricks account. Byaan will list available warehouses, then catalogs and schemas to pick from.
                        </p>
                        {databricksOAuthCanConfigure && (
                          <button
                            type="button"
                            onClick={() => setShowManageDatabricksOAuth(v => !v)}
                            className="text-xs text-brand-orange hover:underline whitespace-nowrap"
                          >
                            {showManageDatabricksOAuth ? 'Hide OAuth settings' : 'Manage OAuth credentials'}
                          </button>
                        )}
                      </div>
                      {showManageDatabricksOAuth && databricksOAuthCanConfigure && (
                        <DatabricksOAuthSettings onConfigChanged={refreshDatabricksAuthStatus} />
                      )}
                      <div>
                        <Label htmlFor="serverHostname" className="text-white">Workspace URL <span className="text-red-400">*</span></Label>
                        <Input
                          id="serverHostname"
                          placeholder="adb-1234.azuredatabricks.net"
                          value={connectionConfig.serverHostname}
                          onChange={(e) => setConnectionConfig(prev => ({ ...prev, serverHostname: e.target.value }))}
                          disabled={isLoading || oauthSigningIn || !!oauthTokens}
                          className="mt-1 bg-[#1a1a1a] border-[#555555] text-white font-mono text-sm"
                        />
                        <p className="text-xs text-gray-400 mt-1">
                          Enter the <strong>Server hostname</strong> only — no <code>https://</code>, no path. Example: <code>adb-1234.azuredatabricks.net</code>.
                        </p>
                      </div>

                      {!oauthTokens ? (
                        oauthSigningIn ? (
                          <div className="flex gap-2">
                            <Button
                              disabled
                              className="flex-1 bg-brand-orange/70 hover:bg-brand-orange/70 cursor-default"
                            >
                              <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Waiting for sign-in…
                            </Button>
                            <Button
                              type="button"
                              onClick={handleCancelDatabricksSignIn}
                              className="bg-[#2a2a2a] hover:bg-[#3a3a3a] border border-[#555555] text-white"
                            >
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            onClick={handleDatabricksSignIn}
                            disabled={isLoading || !connectionConfig.serverHostname.trim()}
                            className="w-full bg-brand-orange hover:bg-brand-orange/90"
                          >
                            Sign in with Databricks
                          </Button>
                        )
                      ) : (
                        <div className="flex items-start gap-2 bg-green-900/20 border border-green-700/50 rounded-md p-3 text-sm text-green-200">
                          <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                          <div className="flex-1">
                            <div className="font-medium">Signed in to {oauthTokens.server_hostname}</div>
                            <button
                              type="button"
                              onClick={resetDatabricksWizard}
                              className="text-xs text-green-300/80 hover:underline mt-0.5"
                            >
                              Sign out
                            </button>
                          </div>
                        </div>
                      )}

                      {oauthTokens && (
                        <div>
                          <Label className="text-white">SQL Warehouse <span className="text-red-400">*</span></Label>
                          {loadingWarehouses ? (
                            <div className="mt-2 flex items-center gap-2 text-sm text-gray-400">
                              <Loader2 className="w-4 h-4 animate-spin" /> Loading warehouses…
                            </div>
                          ) : warehouses && warehouses.length > 0 ? (
                            <div className="mt-1 border border-[#444444] rounded-md divide-y divide-[#3a3a3a] max-h-[220px] overflow-y-auto custom-scrollbar">
                              {warehouses.map(w => (
                                <button
                                  key={w.id}
                                  type="button"
                                  onClick={() => setSelectedWarehouseId(w.id)}
                                  className={`w-full text-left px-3 py-2 text-sm hover:bg-[#2a2a2a] ${selectedWarehouseId === w.id ? 'bg-[#2a2a2a] border-l-2 border-brand-orange' : ''}`}
                                >
                                  <div className="flex items-center justify-between">
                                    <span className="text-white font-medium">{w.name || w.id}</span>
                                    <span className="text-xs text-gray-400">{w.state}{w.size ? ` · ${w.size}` : ''}</span>
                                  </div>
                                  <div className="text-xs text-gray-500 font-mono mt-0.5">{w.http_path}</div>
                                </button>
                              ))}
                            </div>
                          ) : (
                            <div className="mt-1 p-3 text-sm text-gray-400 border border-[#444444] rounded-md">
                              No SQL warehouses visible to this account.
                            </div>
                          )}
                        </div>
                      )}

                      {(oauthError || discoverError) && (
                        <div className="flex items-start gap-2 bg-red-900/20 border border-red-700/50 rounded-md p-3 text-sm text-red-200">
                          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                          <span>{oauthError || discoverError}</span>
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
                                disabled={isLoading}
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
                            disabled={isLoading}
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
                            disabled={isLoading}
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
                          disabled={isLoading}
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
                            disabled={isLoading}
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
                            disabled={isLoading}
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
                        } else if (showPreviousConnectionsOption && datasources.length > 0 && onSwitchToSelect) {
                          onSwitchToSelect()
                        } else {
                          handleClose()
                        }
                      }}
                      disabled={!!(batchProgress && batchProgress.done < batchProgress.total) || discovering}
                      className="border-[#555555] text-white hover:bg-[#3a3a3a]"
                    >
                      {databricksStep === 2 ? 'Back' : (showPreviousConnectionsOption && datasources.length > 0 ? 'Back' : 'Cancel')}
                    </Button>
                    {databricksStep === 1 ? (
                      <Button
                        onClick={handleDatabricksDiscover}
                        disabled={!isFormValid || discovering}
                        className={`${isFormValid && !discovering ? 'bg-brand-orange hover:bg-brand-orange/90' : 'bg-gray-500 cursor-not-allowed'} flex items-center gap-2`}
                      >
                        {discovering && <Loader2 className="w-4 h-4 animate-spin" />}
                        Next →
                      </Button>
                    ) : (
                      <Button
                        onClick={handleDatabricksBatchCreate}
                        disabled={!isFormValid || !!(batchProgress && batchProgress.done < batchProgress.total)}
                        className={`${isFormValid && !batchProgress ? 'bg-brand-orange hover:bg-brand-orange/90' : 'bg-gray-500 cursor-not-allowed'} flex items-center gap-2`}
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
                        if (showPreviousConnectionsOption && datasources.length > 0 && onSwitchToSelect) {
                          onSwitchToSelect()
                        } else {
                          handleClose()
                        }
                      }}
                      disabled={isLoading}
                      className="border-[#555555] text-white hover:bg-[#3a3a3a]"
                    >
                      {showPreviousConnectionsOption && datasources.length > 0 ? 'Back' : 'Cancel'}
                    </Button>
                    <Button
                      onClick={handleSubmit}
                      disabled={!isFormValid || isLoading}
                      className={`${
                        isFormValid && !isLoading
                          ? 'bg-brand-orange hover:bg-brand-orange/90'
                          : 'bg-gray-500 cursor-not-allowed'
                      } flex items-center gap-2`}
                    >
                      {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                      {submitButtonText || 'Create Datasource'}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
