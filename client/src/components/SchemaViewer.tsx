"use client"

import React from "react"
import { Button } from "./ui/button"
import { Card } from "./ui/card"
import { Table, Database, AlertCircle, Loader2, RefreshCw, ChevronDown, ChevronRight } from "lucide-react"
import { Switch } from './ui/switch'
import { type DatabaseTable, type MongoCollection, type DatabaseColumn, isMultiDatabaseSchema } from "../services/api"
import { useStore } from "../stores/useStore"
import { showToast } from "../utils/toast"
import { toast } from "react-toastify"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select"

interface ConnectionConfig {
  type: string
  host: string
  port: string
  database: string
  user: string
  password: string
  connectionString: string
}

interface SchemaViewerProps {
  connection: ConnectionConfig | null
  onQueryChange: (query: string) => void
  notebookId?: string
  onRefreshSchema?: () => Promise<void>
  allConnections?: any[]
  selectedConnectionId?: string | null
  onConnectionChange?: (connectionId: string) => void
}

export function SchemaViewer({ connection, onQueryChange, notebookId, onRefreshSchema, allConnections = [], selectedConnectionId, onConnectionChange }: SchemaViewerProps) {
  // Use schema state from Zustand store
  const {
    schema,
    isLoadingSchema: globalIsLoading,
    schemaError: error,
    forceLoadSchema
  } = useStore()

  // Local loading state for refresh button
  const [isRefreshing, setIsRefreshing] = React.useState(false)

  // State for expanded tables
  const [expandedTables, setExpandedTables] = React.useState<Set<string>>(new Set())

  // Use local loading state for refresh, global for initial load
  const isLoading = isRefreshing || globalIsLoading

  const toggleTable = (tableName: string) => {
    setExpandedTables(prev => {
      const next = new Set(prev);
      if (next.has(tableName)) {
        next.delete(tableName);
      } else {
        next.add(tableName);
      }
      return next;
    });
  }

  const loadDatabaseSchema = async (forceRefresh = false) => {
    if (!notebookId || !connection) {
      return
    }

    if (forceRefresh && onRefreshSchema) {
      // Set local loading state
      setIsRefreshing(true)
      
      // Show loading toast
      const loadingToastId = showToast.loading('Refreshing schema from database...')
      
      // Force refresh by reloading connection details which includes schema
      try {
        await onRefreshSchema()
        // Dismiss loading toast
        if (loadingToastId) {
          toast.dismiss(loadingToastId)
        }
        // Removed success notification - only show errors
      } catch (err) {
        // Dismiss loading toast on error
        if (loadingToastId) {
          toast.dismiss(loadingToastId)
        }
        const errorMessage = err instanceof Error ? err.message : 'Failed to refresh schema'
        showToast.error(errorMessage)
      } finally {
        setIsRefreshing(false)
      }
    } else if (forceRefresh) {
      // Fallback to old method if onRefreshSchema not provided
      const loadingToastId = showToast.loading('Loading schema...')
      try {
        await forceLoadSchema(notebookId, connection.type)
        // Dismiss loading toast
        if (loadingToastId) {
          toast.dismiss(loadingToastId)
        }
        // Removed success notification - only show errors
      } catch (err) {
        if (loadingToastId) {
          toast.dismiss(loadingToastId)
        }
        const errorMessage = err instanceof Error ? err.message : 'Failed to load database schema'
        showToast.error(errorMessage)
      }
    }
  }

  // No longer need to set current notebook or load schema - QueryTool handles this centrally

  const generateBrowseQuery = (tableName: string, datasourceType: string) => {
    if (datasourceType === 'mongo') {
      return `db.${tableName}.find({}).limit(10)`
    } else {
      return `SELECT * FROM ${tableName} LIMIT 10;`
    }
  }

  const isTable = (item: DatabaseTable | MongoCollection): item is DatabaseTable => {
    return 'columns' in item
  }

  const isCollection = (item: DatabaseTable | MongoCollection): item is MongoCollection => {
    return 'sample_fields' in item
  }

  const singleSchema = schema && !isMultiDatabaseSchema(schema) ? schema : null
  const schemaEntries = singleSchema ? Object.entries(singleSchema.schema) : []

  return (
    <div className="m-0 p-4 bg-[#1a1a1a] h-full overflow-auto">
      <div className="space-y-6">
        {/* Connection Selector */}
        {allConnections.length > 1 && onConnectionChange && (
          <div>
            <label className="text-xs text-gray-400 mb-2 block">Select Datasource</label>
            <Select value={selectedConnectionId || ''} onValueChange={onConnectionChange}>
              <SelectTrigger className="w-full bg-[#2a2a2a] border-gray-700 text-white">
                <SelectValue placeholder="Choose a datasource" />
              </SelectTrigger>
              <SelectContent className="bg-[#1a1a1a] border-gray-700">
                {allConnections.map((conn) => (
                  <SelectItem
                    key={conn.id}
                    value={conn.id}
                    className="text-white hover:bg-[#333333]"
                  >
                    {conn.name || conn.connection_obj?.database || conn.type} ({conn.type})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-medium text-white text-lg">Database Schema</h3>
            <p className="text-sm text-[#888888]">
              {singleSchema ? `${singleSchema.datasource_name || 'Database'} (${(singleSchema.datasource_type || connection?.type || 'UNKNOWN').toUpperCase()})` : 'Explore your database structure'}
            </p>
          </div>
          {connection && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => loadDatabaseSchema(true)}
              disabled={isLoading}
              className="bg-[#404040] hover:bg-[#4a4a4a] text-white"
            >
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              ) : (
                <RefreshCw className="w-4 h-4 mr-2" />
              )}
              Refresh
            </Button>
          )}
        </div>

        {!connection ? (
          <div className="text-center py-12 text-[#888888]">
            <div className="w-16 h-16 bg-[#333333] rounded-full flex items-center justify-center mx-auto mb-4">
              <Database className="w-8 h-8" />
            </div>
            <p className="text-lg font-medium text-[#cccccc] mb-2">No Connection</p>
            <p className="text-sm">Connect to a database to view schema</p>
          </div>
        ) : isLoading ? (
          <div className="text-center py-12 text-[#888888]">
            <div className="w-16 h-16 bg-[#333333] rounded-full flex items-center justify-center mx-auto mb-4">
              <Loader2 className="w-8 h-8 animate-spin" />
            </div>
            <p className="text-lg font-medium text-[#cccccc] mb-2">Loading Schema</p>
            <p className="text-sm">Fetching database structure...</p>
          </div>
        ) : error ? (
          <div className="text-center py-12 text-[#888888]">
            <div className="w-16 h-16 bg-[#333333] rounded-full flex items-center justify-center mx-auto mb-4">
              <AlertCircle className="w-8 h-8 text-red-400" />
            </div>
            <p className="text-lg font-medium text-[#cccccc] mb-2">Error Loading Schema</p>
            <p className="text-sm text-red-400">{error}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => loadDatabaseSchema(true)}
              className="bg-[#404040] hover:bg-[#4a4a4a] text-white mt-4"
            >
              <RefreshCw className="w-4 h-4 mr-2" />
              Try Again
            </Button>
          </div>
        ) : schemaEntries.length > 0 ? (
          <div className="space-y-4 max-h-full overflow-y-auto custom-scrollbar">
            {schemaEntries.map(([tableName, tableData]) => {
              const table = tableData as DatabaseTable;
              const isExpanded = expandedTables.has(tableName);

              return (
                <Card key={tableName} className="p-4 bg-[#333333] border-[#404040]">
                  <div className="mb-3">
                    <div className="flex items-center justify-between mb-2">
                      <button
                        onClick={() => toggleTable(tableName)}
                        className="flex items-center gap-2 text-left flex-1 hover:text-brand-orange transition-colors"
                      >
                        {isExpanded ? (
                          <ChevronDown className="w-4 h-4 text-gray-400" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-gray-400" />
                        )}
                        <h4 className="text-sm font-mono font-semibold text-white">{tableName}</h4>
                        <span className="text-xs text-[#888888]">
                          {isCollection(tableData)
                            ? `${tableData.sample_fields?.length || 0} fields`
                            : `${table.columns?.length || 0} columns`}
                        </span>
                        {(tableData as any).redacted_table && (
                          <Switch size="sm" variant="destructive" checked disabled />
                        )}
                      </button>
                    </div>

                    {/* Additional info when collapsed */}
                    {!isExpanded && (
                      <p className="text-xs text-[#888888] ml-6">
                        {(singleSchema?.datasource_type === 'csv' || singleSchema?.datasource_type === 'excel' || singleSchema?.datasource_type === 'parquet' || singleSchema?.datasource_type === 'json') && isTable(tableData) && tableData.filename
                          ? `${singleSchema.datasource_type.toUpperCase()} File: ${tableData.filename}`
                          : singleSchema?.datasource_type === 'mongo'
                          ? 'MongoDB Collection'
                          : 'Database Table'}
                        {table.row_count !== undefined && ` • ${table.row_count} rows`}
                      </p>
                    )}
                  </div>

                  {/* Columns / Fields */}
                  {isExpanded && (
                    <div className="space-y-1.5">
                      {isCollection(tableData) ? (
                        <>
                          <div className="flex items-center gap-2 mb-2">
                            <div className="h-px flex-1 bg-[#404040]" />
                            <span className="text-[10px] text-[#888888] uppercase tracking-wider">
                              {tableData.sample_fields.length} fields
                            </span>
                            <div className="h-px flex-1 bg-[#404040]" />
                          </div>

                          {tableData.sample_fields.map((fieldName: string) => (
                            <div
                              key={fieldName}
                              className="flex items-start gap-2 p-2 rounded hover:bg-[#404040] transition-colors"
                            >
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-mono text-white">{fieldName}</span>
                                  <span className="text-[10px] text-[#888888]">mongodb field</span>
                                </div>
                              </div>
                            </div>
                          ))}
                        </>
                      ) : (
                        <>
                          <div className="flex items-center gap-2 mb-2">
                            <div className="h-px flex-1 bg-[#404040]" />
                            <span className="text-[10px] text-[#888888] uppercase tracking-wider">
                              {table.columns?.length || 0} columns
                            </span>
                            <div className="h-px flex-1 bg-[#404040]" />
                          </div>

                          {table.columns?.map((column: DatabaseColumn) => (
                            <div
                              key={column.name}
                              className="flex items-start gap-2 p-2 rounded hover:bg-[#404040] transition-colors"
                            >
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-mono text-white">{column.name}</span>
                                  <span className="text-[10px] text-[#888888]">{column.type}</span>
                                  {column.nullable === false && (
                                    <span className="text-[10px] text-red-400">NOT NULL</span>
                                  )}
                                  {column.redacted && (
                                    <Switch size="sm" variant="destructive" checked disabled />
                                  )}
                                </div>
                              </div>
                            </div>
                          ))}
                        </>
                      )}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        ) : (
          <div className="text-center py-12 text-[#888888]">
            <div className="w-16 h-16 bg-[#333333] rounded-full flex items-center justify-center mx-auto mb-4">
              <Database className="w-8 h-8" />
            </div>
            <p className="text-lg font-medium text-[#cccccc] mb-2">No Tables Found</p>
            <p className="text-sm">This database appears to be empty or has no accessible tables</p>
          </div>
        )}
      </div>
    </div>
  )
}
