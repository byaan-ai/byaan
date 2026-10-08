"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Loader2, Database } from "lucide-react"

import { Button } from "./ui/button"
import { QueryEditor } from "./QueryEditor"
import { QueryResults } from "./QueryResults"
import { SaveQueryDialog } from "./SaveQueryDialog"
import { ResizableVerticalPanel } from "./ResizableSplitPanel"
import { ApiService, type ErrorDetail } from "../services/api"
import { useStore } from "../stores/useStore"
import { useSaveQuery } from "../hooks/useQueries"
import { showToast } from "../utils/toast"

interface QueryListItem {
  id: string
  name: string
  query_type: string
  skill_name: string | null
}

interface QueryListResponse {
  queries: QueryListItem[]
}

interface QueryRead {
  id: string
  name: string
  query: string
  output_schema: string
  dataset_id: string
  notebook_id: string
  query_type: string
  skill_name: string | null
  created_at: string
  updated_at: string
}

interface ConnectionConfig {
  type: "pg" | "mongo" | "mysql" | "sqlite" | "mssql" | "csv" | "excel" | "parquet" | "json"
  host: string
  port: string
  database: string
  user: string
  password: string
  connectionString: string
  file_path?: string
  dataset_type?: string
}

interface QueryResult {
  query: string
  results: any[]
  executionTime: string
  rowCount: number
  error?: string
  errorDetail?: ErrorDetail
  rawResult?: string
  totalCount?: number
  returnedCount?: number
  limited?: boolean
  notebookId?: string
  datasourceType?: string
  connectionId?: string
}

interface NotebookQueryPanelProps {
  notebookId?: string
  initialQuery?: string
  initialQueryVersion?: number
  injectedQuery?: string
  injectedQueryVersion?: number
  injectedConnectionId?: string
  onDebugWithAssistant?: (query: string, error: string, errorDetail?: ErrorDetail) => void
}

export function NotebookQueryPanel({ notebookId, initialQuery, initialQueryVersion, injectedQuery, injectedQueryVersion, injectedConnectionId, onDebugWithAssistant }: NotebookQueryPanelProps) {
  const setSchemaNotebook = useStore(state => state.setCurrentNotebook)
  const cacheSchema = useStore(state => state.cacheSchema)
  const querySavedTrigger = useStore(state => state.querySavedTrigger)
  const notebookDatasourcesChangedTrigger = useStore(state => state.notebookDatasourcesChangedTrigger)
  const selectedConnectionIdRef = useRef<string | null>(null)
  const previousNotebookIdRef = useRef<string | undefined>(undefined)
  const injectedRetryKeyRef = useRef<string | null>(null)

  const [allNotebookConnections, setAllNotebookConnections] = useState<any[]>([])
  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null)
  const [selectedActualConnectionId, setSelectedActualConnectionId] = useState<string | null>(null)

  // Keep ref in sync with state
  useEffect(() => {
    selectedConnectionIdRef.current = selectedConnectionId
  }, [selectedConnectionId])

  const [connection, setConnection] = useState<ConnectionConfig | null>(null)
  const [notebookConnection, setNotebookConnection] = useState<any>(null)
  const [isLoadingConnection, setIsLoadingConnection] = useState(true)
  const [currentQuery, setCurrentQuery] = useState(initialQuery ?? "")
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null)
  const [isExecuting, setIsExecuting] = useState(false)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [queryToSave, setQueryToSave] = useState("")
  const [saveDialogMode, setSaveDialogMode] = useState<'save' | 'update'>('save')
  const [loadedQueryId, setLoadedQueryId] = useState<string | null>(null)
  const [loadedQueryName, setLoadedQueryName] = useState<string>("")
  const [isEditorExpanded, setIsEditorExpanded] = useState(false)
  const saveQueryMutation = useSaveQuery()
  const abortControllerRef = useRef<AbortController | null>(null)

  // Saved queries state
  const [savedQueries, setSavedQueries] = useState<QueryListItem[]>([])
  const [isLoadingSavedQueries, setIsLoadingSavedQueries] = useState(true)

  useEffect(() => {
    if (!currentQuery.trim()) {
      setQueryResult(null)
    }
  }, [currentQuery])

  useEffect(() => {
    if (typeof initialQuery === "string") {
      setCurrentQuery(initialQuery)
    }
  }, [initialQuery, initialQueryVersion])

  // Handle injected queries from tool calls
  useEffect(() => {
    if (injectedQuery !== undefined && injectedQuery !== null) {
      setCurrentQuery(injectedQuery)
      setQueryResult(null)
    }
  }, [injectedQuery, injectedQueryVersion])

  useEffect(() => {
    if (notebookId) {
      // Only reset state when notebook ID actually changes, not on every mount
      const hasNotebookChanged = previousNotebookIdRef.current !== notebookId

      setSchemaNotebook(notebookId)

      if (hasNotebookChanged && previousNotebookIdRef.current !== undefined) {
        // Reset all state when notebook changes
        setCurrentQuery("")
        setQueryResult(null)
        setConnection(null)
        setNotebookConnection(null)
        setSavedQueries([])
        setSelectedConnectionId(null)
        setSelectedActualConnectionId(null)
      }

      // Update the ref to track current notebook
      previousNotebookIdRef.current = notebookId
    }
    return () => {
      setSchemaNotebook(null)
    }
  }, [notebookId, setSchemaNotebook])

  // Load saved queries for notebook
  const loadSavedQueries = useCallback(async (retries = 3) => {
    if (!notebookId) return
    setIsLoadingSavedQueries(true)
    try {
      const response = await ApiService.getNotebookSavedQueries(notebookId)
      setSavedQueries(response.queries || [])
    } catch (error) {
      // Retry with exponential backoff for potential database transaction lag
      if (retries > 0) {
        await new Promise(resolve => setTimeout(resolve, 200))
        setIsLoadingSavedQueries(false)
        return loadSavedQueries(retries - 1)
      }
      console.error("Failed to load saved queries", error)
      setSavedQueries([])
    } finally {
      setIsLoadingSavedQueries(false)
    }
  }, [notebookId])

  const selectConnection = useCallback((connectionToSelect: any) => {
    setSelectedConnectionId(connectionToSelect.id)
    setSelectedActualConnectionId(connectionToSelect.connection_id || null)
    setNotebookConnection(connectionToSelect)

    if (connectionToSelect.connection_obj) {
      const connObj = connectionToSelect.connection_obj
      setConnection({
        type: connectionToSelect.type as "pg" | "mongo" | "mysql" | "sqlite" | "mssql" | "csv" | "excel" | "parquet" | "json",
        host: connObj.host || "",
        port: connObj.port?.toString() || "",
        database: connObj.database || "",
        user: connObj.user || "",
        password: connObj.password || "",
        connectionString: connObj.connection_string || "",
        file_path: connObj.file_path || ""
      })
    } else {
      setConnection(null)
    }

    if (connectionToSelect.schema) {
      cacheSchema(connectionToSelect.schema)
    }
  }, [cacheSchema])

  const clearConnectionState = useCallback(() => {
    setAllNotebookConnections([])
    setSelectedConnectionId(null)
    setSelectedActualConnectionId(null)
    setNotebookConnection(null)
    setConnection(null)
  }, [])

  const loadNotebookConnection = useCallback(async () => {
    if (!notebookId) return
    setIsLoadingConnection(true)
    try {
      const connections = await ApiService.getNotebookConnectionsWithDetails(notebookId)
      setAllNotebookConnections(connections)

      if (connections.length > 0) {
        const connectionToSelect = selectedConnectionIdRef.current
          ? connections.find(c => c.id === selectedConnectionIdRef.current) || connections[0]
          : connections[0]
        selectConnection(connectionToSelect)
      } else {
        // No notebook-specific connections — fall back to global datasources
        try {
          const dsResponse = await ApiService.listAllDatasources()
          const datasources = dsResponse?.items || []
          if (datasources.length > 0) {
            const fallbackConnections = datasources.map((ds: any) => ({
              id: ds.id,
              name: ds.name,
              type: ds.type || (ds.source_type === 'dataset' ? 'csv' : 'pg'),
              connection_id: ds.connection_id || ds.id,
              connection_obj: null,
              schema: null,
            }))
            setAllNotebookConnections(fallbackConnections)
            const target = injectedConnectionId
              ? fallbackConnections.find((c: any) => c.id === injectedConnectionId || c.connection_id === injectedConnectionId) || fallbackConnections[0]
              : fallbackConnections[0]
            setSelectedConnectionId(target.id)
            setSelectedActualConnectionId(target.connection_id || null)
            setNotebookConnection(target)
            const dsType = target.type || 'csv'
            setConnection({
              type: dsType as any,
              host: "",
              port: "",
              database: target.name || "",
              user: "",
              password: "",
              connectionString: "",
              file_path: ""
            })
            // Load schema for the selected datasource
            try {
              const schemaResponse = await ApiService.getDatasourceSchema(target.id)
              if (schemaResponse) {
                cacheSchema(schemaResponse)
              }
            } catch {
              // Schema load is best-effort
            }
          } else {
            clearConnectionState()
          }
        } catch {
          clearConnectionState()
        }
      }
    } catch (error) {
      console.error("Failed to load notebook connection", error)
      clearConnectionState()
    } finally {
      setIsLoadingConnection(false)
    }
  }, [notebookId, cacheSchema, injectedConnectionId, selectConnection, clearConnectionState])

  useEffect(() => {
    loadNotebookConnection()
    loadSavedQueries()
  }, [loadNotebookConnection, loadSavedQueries])

  useEffect(() => {
    if (notebookDatasourcesChangedTrigger > 0) {
      loadNotebookConnection()
    }
  }, [notebookDatasourcesChangedTrigger, loadNotebookConnection])

  const handleConnectionChange = useCallback((connectionId: string) => {
    let selectedConn = allNotebookConnections.find(c => c.id === connectionId)

    if (!selectedConn) {
      selectedConn = allNotebookConnections.find(c => c.connection_id === connectionId)
    }

    if (!selectedConn) {
      console.error('[handleConnectionChange] Dataset not found:', connectionId)
      return
    }

    setSelectedConnectionId(selectedConn.id)
    setSelectedActualConnectionId(selectedConn.connection_id || null)
    setNotebookConnection(selectedConn)

    if (selectedConn.connection_obj) {
      const connObj = selectedConn.connection_obj
      const connectionConfig = {
        type: selectedConn.type as "pg" | "mongo" | "mysql" | "sqlite" | "mssql" | "csv" | "excel" | "parquet" | "json",
        host: connObj.host || "",
        port: connObj.port?.toString() || "",
        database: connObj.database || "",
        user: connObj.user || "",
        password: connObj.password || "",
        connectionString: connObj.connection_string || "",
      }
      setConnection(connectionConfig)

      // Cache the schema for the selected connection so SchemaViewer can display it
      if (selectedConn.schema) {
        cacheSchema(selectedConn.schema)
      }
    } else {
      const dsType = selectedConn.type || 'csv'
      setConnection({
        type: dsType as any,
        host: "",
        port: "",
        database: selectedConn.name || "",
        user: "",
        password: "",
        connectionString: "",
      })
      // Load schema for fallback datasource
      ApiService.getDatasourceSchema(selectedConn.id)
        .then(schemaResponse => { if (schemaResponse) cacheSchema(schemaResponse) })
        .catch(() => {})
    }

    // Clear query results when switching connections
    setQueryResult(null)
  }, [allNotebookConnections, cacheSchema])

  // Handle injected connection ID from tool calls
  useEffect(() => {
    if (!injectedConnectionId) return

    const matched = allNotebookConnections.find(
      c => c.id === injectedConnectionId || c.connection_id === injectedConnectionId
    )

    if (matched) {
      handleConnectionChange(injectedConnectionId)
      injectedRetryKeyRef.current = null
      return
    }

    if (isLoadingConnection) return

    // Force one fresh reload per (id+version) pair to avoid loops.
    const retryKey = `${injectedConnectionId}::${injectedQueryVersion ?? 0}`
    if (injectedRetryKeyRef.current === retryKey) return
    injectedRetryKeyRef.current = retryKey
    void loadNotebookConnection()
  }, [injectedConnectionId, injectedQueryVersion, allNotebookConnections, handleConnectionChange, isLoadingConnection, loadNotebookConnection])

  // Watch for query saved trigger and reload queries
  useEffect(() => {
    if (querySavedTrigger > 0) {
      loadSavedQueries()
    }
  }, [querySavedTrigger, loadSavedQueries])

  const refreshNotebookConnectionSchema = useCallback(async () => {
    if (!notebookId || !selectedConnectionId) {
      console.error("Cannot refresh schema: missing notebookId or selectedConnectionId")
      return
    }

    // For database connections, use the actual connection_id; for file datasets, no schemas to refresh
    if (!selectedActualConnectionId) {
      console.warn("Cannot refresh schema: this is a file dataset, not a database connection")
      showToast.error("Schema refresh is only available for database connections")
      return
    }

    try {
      const refreshedConnection = await ApiService.refreshNotebookConnectionSchema(
        notebookId,
        selectedActualConnectionId  // Use the actual connection_id
      )

      // Update the connection in the list
      setAllNotebookConnections(prev =>
        prev.map(conn =>
          conn.id === selectedConnectionId
            ? { ...conn, schema: refreshedConnection.schema, schema_updated_at: refreshedConnection.schema_updated_at }
            : conn
        )
      )

      setNotebookConnection((prev: any) => {
        if (prev?.id === selectedConnectionId) {
          return {
            ...prev,
            schema: refreshedConnection.schema,
            schema_updated_at: refreshedConnection.schema_updated_at
          }
        }
        return prev
      })

      if (refreshedConnection.schema) {
        cacheSchema(refreshedConnection.schema)
      }
    } catch (error) {
      console.error("Error refreshing schema:", error)
      const errorMessage = error instanceof Error ? error.message : "Failed to refresh schema"
      showToast.error(errorMessage)
      throw error
    }
  }, [notebookId, selectedConnectionId, selectedActualConnectionId, cacheSchema])

  const datasourceType = useMemo<"pg" | "mongo" | "duckdb" | "csv" | "excel" | "parquet" | "json">(() => {
    if (notebookConnection?.connection_obj?.dataset_type === "file" || connection?.dataset_type === "file") {
      return "duckdb"
    }
    if (notebookConnection?.type === "mongo" || connection?.type === "mongo") {
      return "mongo"
    }
    if (notebookConnection?.type === "csv" || connection?.type === "csv") {
      return "csv"
    }
    if (notebookConnection?.type === "excel" || connection?.type === "excel") {
      return "excel"
    }
    if (notebookConnection?.type === "parquet" || connection?.type === "parquet") {
      return "parquet"
    }
    if (notebookConnection?.type === "json" || connection?.type === "json") {
      return "json"
    }
    return "pg"
  }, [notebookConnection?.type, connection?.type])

  const runQuery = useCallback(async () => {
    if (!notebookId || !selectedConnectionId || !currentQuery.trim()) return

    abortControllerRef.current = new AbortController()
    setIsExecuting(true)
    const startedAt = Date.now()

    try {
      // Use selectedConnectionId as the single source of truth
      const resp = await ApiService.executeRawQuery(
        notebookId,
        datasourceType,
        currentQuery,
        500,
        selectedConnectionId,
        abortControllerRef.current.signal
      )
      const executionTime = `${Date.now() - startedAt}ms`

      if (!resp.success) {
        setQueryResult({
          query: currentQuery,
          results: [],
          executionTime,
          rowCount: 0,
          error: resp.error || "Query failed",
          errorDetail: resp.error_detail,
          notebookId,
          datasourceType,
          connectionId: selectedConnectionId,
        })
        return
      }

      const payload = resp.result && (resp.result.result ?? resp.result)
      let rows: any[] = []
      let rowCount = 0

      if (payload) {
        if (Array.isArray(payload.documents)) {
          rows = payload.documents
          rowCount = payload.count || rows.length
        } else if (payload.document) {
          rows = payload.document ? [payload.document] : []
          rowCount = rows.length
        } else if (Array.isArray(payload.values)) {
          rows = payload.values.map((v: any) => ({ value: v }))
          rowCount = rows.length
        } else if (Array.isArray(payload)) {
          rows = payload
          rowCount = rows.length
        }
      }

      setQueryResult({
        query: currentQuery,
        results: rows,
        executionTime,
        rowCount,
        totalCount: resp.total_count,
        returnedCount: resp.returned_count,
        limited: resp.limited,
        notebookId,
        datasourceType,
        connectionId: selectedConnectionId,
      })
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        setQueryResult({
          query: currentQuery,
          results: [],
          executionTime: `${Date.now() - startedAt}ms`,
          rowCount: 0,
          error: "Query execution stopped by user",
          notebookId,
          datasourceType,
          connectionId: selectedConnectionId,
        })
      } else {
        setQueryResult({
          query: currentQuery,
          results: [],
          executionTime: `${Date.now() - startedAt}ms`,
          rowCount: 0,
          error: error instanceof Error ? error.message : "Network error occurred",
          notebookId,
          datasourceType,
          connectionId: selectedConnectionId,
        })
      }
    } finally {
      setIsExecuting(false)
      abortControllerRef.current = null
    }
  }, [notebookId, currentQuery, datasourceType, selectedConnectionId])

  const stopQuery = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
  }, [])

  const handleClearQuery = useCallback(() => {
    setLoadedQueryId(null)
    setLoadedQueryName("")
    setQueryResult(null)
  }, [])

  const handleSaveQuery = useCallback(() => {
    setQueryToSave(currentQuery)
    if (loadedQueryId) {
      setSaveDialogMode('update')
    } else {
      setSaveDialogMode('save')
    }
    setSaveDialogOpen(true)
  }, [currentQuery, loadedQueryId])

  const handleSaveQueryConfirm = useCallback(
    async (name: string, saveAsNew: boolean = false) => {
      if (!notebookConnection?.id || !queryToSave || !notebookId) return

      if (saveDialogMode === 'update' && loadedQueryId && !saveAsNew) {
        try {
          await ApiService.updateQuery(loadedQueryId, {
            query: queryToSave,
            name: name,
          })
          showToast.success('Query updated successfully')
          setLoadedQueryName(name)
          setSaveDialogOpen(false)
          setQueryToSave("")
          loadSavedQueries()
        } catch (error) {
          showToast.error('Failed to update query')
          console.error('Error updating query:', error)
        }
      } else {
        saveQueryMutation.mutate(
          {
            query: queryToSave,
            connectionId: notebookConnection.id,
            notebookId,
            datasourceType,
            name,
          },
          {
            onSuccess: (response) => {
              setSaveDialogOpen(false)
              setQueryToSave("")
              if (saveAsNew) {
                setLoadedQueryId(response.query_id || null)
                setLoadedQueryName(name)
              }
              loadSavedQueries()
            },
          }
        )
      }
    },
    [datasourceType, notebookConnection?.id, notebookId, queryToSave, saveQueryMutation, loadSavedQueries, saveDialogMode, loadedQueryId]
  )

  const handleLoadSavedQuery = useCallback(async (queryItem: QueryListItem) => {
    try {
      const queryDetails = await ApiService.getQuery(queryItem.id)

      if (queryDetails.query_type === 'skill_api') {
        setIsExecuting(true)
        try {
          const result = await ApiService.executeSavedQuery(queryItem.id)
          const startedAt = Date.now()
          setQueryResult({
            query: `[${queryDetails.skill_name || 'API'}] ${queryDetails.name}`,
            results: Array.isArray(result.data) ? result.data : result.data ? [result.data] : [],
            executionTime: `${Date.now() - startedAt}ms`,
            rowCount: Array.isArray(result.data) ? result.data.length : result.data ? 1 : 0,
          })
          setLoadedQueryId(null)
          setLoadedQueryName("")
        } finally {
          setIsExecuting(false)
        }
      } else {
        setCurrentQuery(queryDetails.query)
        setLoadedQueryId(queryItem.id)
        setLoadedQueryName(queryDetails.name)
        if (queryDetails.dataset_id && allNotebookConnections.length > 0) {
          handleConnectionChange(queryDetails.dataset_id)
          setQueryResult(null)
        }
      }
    } catch (error) {
      console.error("Failed to load saved query", error)
      showToast.error("Failed to load saved query")
    }
  }, [allNotebookConnections, handleConnectionChange])

  if (!notebookId) {
    return (
      <div className="h-full flex items-center justify-center text-[#808080]">
        Notebook not found.
      </div>
    )
  }

  if (isLoadingConnection || isLoadingSavedQueries) {
    return (
      <div className="h-full flex items-center justify-center text-[#c0c0c0]">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading...
      </div>
    )
  }

  if (!notebookConnection && allNotebookConnections.length === 0 && !injectedConnectionId) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-[#808080] px-6 text-center">
        <div className="w-16 h-16 rounded-full bg-[#333333] flex items-center justify-center mb-4">
          <Database className="w-8 h-8 text-white" />
        </div>
        <p className="text-lg text-white mb-2">No database connection</p>
        <p className="text-sm">
          Connect a database to this notebook to run exploratory queries.
        </p>
      </div>
    )
  }

  return (
    <div className="h-full text-white overflow-hidden">
      <div className="h-full px-3">
        <div className="h-full overflow-hidden">
          <section className="flex flex-col min-h-0 h-full border border-[#232323] bg-[#151515] overflow-hidden">
            <ResizableVerticalPanel
              defaultTopHeight={isEditorExpanded ? 55 : 35}
              minTopHeight={25}
              maxTopHeight={70}
              topPanel={
                <div className="h-full bg-[#151515]">
                  <QueryEditor
                    query={currentQuery}
                    onQueryChange={setCurrentQuery}
                    onExecute={runQuery}
                    onStop={stopQuery}
                    isExecuting={isExecuting}
                    datasourceType={datasourceType}
                    savedQueries={savedQueries}
                    onLoadSavedQuery={handleLoadSavedQuery}
                    onSaveQuery={handleSaveQuery}
                    onClear={handleClearQuery}
                    currentQueryId={loadedQueryId || undefined}
                    currentQueryName={loadedQueryName}
                    isExpanded={isEditorExpanded}
                    onExpandChange={setIsEditorExpanded}
                  />
                </div>
              }
              bottomPanel={
                <div className="h-full flex flex-col bg-[#151515]">
                  <div className="pt-4 px-4 text-xs text-[#8d8d8d] flex-shrink-0">
                    {queryResult
                      ? `Rows: ${queryResult.rowCount} • Time: ${queryResult.executionTime}`
                      : "Awaiting execution"}
                  </div>
                  <div className="mt-4 mx-4 mb-4 border border-[#232323] bg-[#1a1a1a] flex-1 min-h-0">
                    <QueryResults
                      queryResult={queryResult}
                      onDebugWithAssistant={onDebugWithAssistant}
                    />
                  </div>
                </div>
              }
            />
          </section>

        </div>
      </div>

      <SaveQueryDialog
        open={saveDialogOpen}
        onOpenChange={setSaveDialogOpen}
        onSave={handleSaveQueryConfirm}
        isLoading={saveQueryMutation.isPending}
        mode={saveDialogMode}
        currentQueryName={loadedQueryName}
        currentQueryId={loadedQueryId || undefined}
      />
    </div>
  )
}
