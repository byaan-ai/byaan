import React, { useState, useRef, useCallback, useEffect } from 'react'
import { useStore } from '../stores/useStore'
import type { NestedSchemaNode } from '../services/api'

export interface TableMention {
  name: string
  position: number
}

export interface ColumnInfo {
  name: string
  type: string
  nullable: boolean
  nestedSchema?: NestedSchemaNode
}

interface SchemaChild {
  key: string
  schema: NestedSchemaNode
  fromArray: boolean
}

export interface FieldOption {
  name: string
  schema: NestedSchemaNode
  displayType: string
  fromArray: boolean
  hasChildren: boolean
}

const toTypeArray = (type?: string | string[]): string[] => {
  if (!type) return []
  return Array.isArray(type) ? type : [type]
}

const formatSchemaType = (schema?: NestedSchemaNode | null): string => {
  if (!schema) return 'mixed'
  const types = toTypeArray(schema.type)
  if (types.length === 0) return 'mixed'
  return types.join(' | ')
}

const collectSchemaChildren = (schema?: NestedSchemaNode | null): SchemaChild[] => {
  if (!schema) return []
  const children: SchemaChild[] = []

  if (schema.properties) {
    for (const [key, value] of Object.entries(schema.properties)) {
      children.push({ key, schema: value, fromArray: false })
    }
  }

  if (toTypeArray(schema.type).includes('array') && schema.items) {
    const itemSchema = schema.items
    if (itemSchema.properties) {
      for (const [key, value] of Object.entries(itemSchema.properties)) {
        children.push({ key, schema: value, fromArray: true })
      }
    }
  }

  return children
}

const schemaHasChildren = (schema?: NestedSchemaNode | null): boolean => {
  return collectSchemaChildren(schema).length > 0
}

const getSchemaForPath = (schema: NestedSchemaNode | undefined, path: string[]): NestedSchemaNode | undefined => {
  if (!schema) return undefined
  if (path.length === 0) return schema

  let current: NestedSchemaNode | undefined = schema

  for (const segment of path) {
    if (!current) {
      return undefined
    }

    if (current.properties && current.properties[segment]) {
      current = current.properties[segment]
      continue
    }

    let traversed = false
    let candidate: NestedSchemaNode | undefined = current

    while (candidate && toTypeArray(candidate.type).includes('array') && candidate.items) {
      candidate = candidate.items
      if (candidate.properties && candidate.properties[segment]) {
        current = candidate.properties[segment]
        traversed = true
        break
      }
    }

    if (!traversed) {
      current = undefined
      break
    }
  }

  return current
}

export interface UseTableMentionsProps {
  datasources: Array<{ id: string; name: string; tables: Record<string, any> }>
  tableNames: string[]
  onInputChange: (value: string) => void
  onSubmit?: () => void
  externalValue?: string
  getTableColumns?: (tableName: string, datasourceName?: string) => ColumnInfo[]
}

export interface UseTableMentionsReturn {
  inputValue: string
  setInputValue: (value: string) => void
  showDropdown: boolean
  filteredDatasources: Array<{ id: string; name: string; tables: Record<string, any> }>
  filteredTables: string[]
  unifiedItems: Array<{type: 'table' | 'column', name: string, tableName?: string, displayName: string}>
  selectedIndex: number
  setSelectedIndex: (index: number) => void
  cursorPosition: number
  handleKeyDown: (e: React.KeyboardEvent) => void
  handleDatasourceHover: (datasourceName: string) => void
  handleDatasourceSelect: (datasourceName: string) => void
  handleTableSelect: (tableName: string) => void
  handleColumnSelect: (columnName: string, tableName?: string) => void
  handleUnifiedSelect: (item: {type: 'table' | 'column', name: string, tableName?: string, displayName: string}) => void
  closeDropdown: () => void
  inputRef: React.RefObject<HTMLTextAreaElement | null>
  // Datasource selection states
  hoveredDatasource: string | null
  selectedDatasource: string | null
  isDatasourceMode: boolean
  navigateToDatasources: () => void
  navigateToTablesFromDatasource: () => void
  // Column selection states
  showColumnDropdown: boolean
  selectedTable: string | null
  filteredColumns: ColumnInfo[]
  selectedColumnIndex: number
  setSelectedColumnIndex: (index: number) => void
  navigateToColumns: () => void
  navigateBackToTables: () => void
  hoveredTable: string | null
  columnsForHoveredTable: ColumnInfo[]
  isColumnMode: boolean
  columnSearchQuery: string
  activeColumn: ColumnInfo | null
  isFieldMode: boolean
  fieldPath: string[]
  fieldOptions: FieldOption[]
  selectedFieldIndex: number
  setSelectedFieldIndex: (index: number) => void
  enterFieldMode: () => void
  drillIntoField: (fieldName: string) => void
  navigateBackFromField: () => void
  handleFieldSelect: (fieldName: string) => void
  fieldSearchQuery: string
  searchQuery: string
  localSearchQuery: string
}

export function useTableMentions({
  datasources,
  tableNames,
  onInputChange,
  onSubmit,
  externalValue,
  getTableColumns
}: UseTableMentionsProps): UseTableMentionsReturn {
  // Get state from Zustand store - SELECTIVE subscriptions to prevent re-renders on message updates
  const inputValue = useStore(state => state.inputValue)
  const showDropdown = useStore(state => state.showDropdown)
  const selectedIndex = useStore(state => state.selectedIndex)
  const cursorPosition = useStore(state => state.cursorPosition)
  const mentionStart = useStore(state => state.mentionStart)
  const searchQuery = useStore(state => state.searchQuery)

  // Get stable function references
  const setStoreInputValue = useStore.getState().setInputValue
  const setShowDropdown = useStore.getState().setShowDropdown
  const setSelectedIndex = useStore.getState().setSelectedIndex
  const setCursorPosition = useStore.getState().setCursorPosition
  const setMentionStart = useStore.getState().setMentionStart
  const setSearchQuery = useStore.getState().setSearchQuery
  const resetTableMentions = useStore.getState().resetTableMentions

  // Datasource selection states (local state for UI interactions)
  const [isDatasourceMode, setIsDatasourceMode] = useState(() => datasources.length > 1)
  const [selectedDatasource, setSelectedDatasource] = useState<string | null>(() =>
    datasources.length > 0 ? datasources[0].name : null
  )

  // Update datasource mode when datasources change
  React.useEffect(() => {
    if (datasources.length === 1) {
      setIsDatasourceMode(false)
      setSelectedDatasource(datasources[0].name)
    } else if (datasources.length > 1) {
      setIsDatasourceMode(true)
      setSelectedDatasource(datasources[0].name)
    }
  }, [datasources])

  // Column selection states (local state for UI interactions)
  const [showColumnDropdown, setShowColumnDropdown] = useState(false)
  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  const [selectedColumnIndex, setSelectedColumnIndex] = useState(0)
  const [columnSearchQuery, setColumnSearchQuery] = useState('')
  const [isColumnMode, setIsColumnMode] = useState(false)
  const [isFieldMode, setIsFieldMode] = useState(false)
  const [fieldPath, setFieldPath] = useState<string[]>([])
  const [selectedFieldIndex, setSelectedFieldIndex] = useState(0)
  const [fieldSearchQuery, setFieldSearchQuery] = useState('')
  const [activeColumnName, setActiveColumnName] = useState<string | null>(null)
  const [localSearchQuery, setLocalSearchQuery] = useState('') // Local search for current context
  const columnModeBaseQueryRef = useRef('')
  const localSearchDebounceRef = useRef<NodeJS.Timeout | null>(null)

  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  // Debounced version of setLocalSearchQuery to reduce recalculations
  const setLocalSearchQueryDebounced = useCallback((query: string) => {
    if (localSearchDebounceRef.current) {
      clearTimeout(localSearchDebounceRef.current)
    }
    localSearchDebounceRef.current = setTimeout(() => {
      setLocalSearchQuery(query)
    }, 150)
  }, [])

  const resetFieldState = useCallback(() => {
    setIsFieldMode(false)
    setFieldPath([])
    setSelectedFieldIndex(0)
    setFieldSearchQuery('')
    setActiveColumnName(null)
    setLocalSearchQuery('')
  }, [])

  // Cleanup debounce timeout on unmount
  useEffect(() => {
    return () => {
      if (localSearchDebounceRef.current) {
        clearTimeout(localSearchDebounceRef.current)
      }
    }
  }, [])

  // Close dropdown and reset related state
  const closeDropdown = useCallback(() => {
    setShowDropdown(false)
    setShowColumnDropdown(false)
    setMentionStart(-1)
    setSearchQuery('')
    setColumnSearchQuery('')

    if (datasources.length === 1) {
      setIsDatasourceMode(false)
      setSelectedDatasource(datasources[0].name)
    } else if (datasources.length > 1) {
      setIsDatasourceMode(true)
      setSelectedDatasource(datasources[0].name)
    }

    setSelectedTable(null)
    setSelectedIndex(0)
    setSelectedColumnIndex(0)
    setIsColumnMode(false)
    setActiveColumnName(null)
    resetFieldState()
    columnModeBaseQueryRef.current = ''
  }, [resetFieldState, setShowDropdown, setMentionStart, setSearchQuery, setSelectedIndex, datasources])

  // Filter datasources based on search query (only in datasource mode)
  const filteredDatasources = React.useMemo(() => {
    if (!isDatasourceMode) {
      if (selectedDatasource) {
        const ds = datasources.find(d => d.name === selectedDatasource)
        return ds ? [ds] : datasources
      }
      return datasources
    }

    // When IN datasource mode, show all datasources (filtered by search if applicable)
    if (localSearchQuery) {
      return datasources.filter(ds =>
        ds.name.toLowerCase().includes(localSearchQuery.toLowerCase())
      )
    }
    return datasources
  }, [datasources, isDatasourceMode, selectedDatasource, localSearchQuery])


  const tableSearchTerm = React.useMemo(() => {
    // When in column or field mode, don't filter tables - show selected table
    if (isColumnMode || isFieldMode) {
      return selectedTable || ''
    }
    // In table mode, use the local search query
    return localSearchQuery
  }, [isColumnMode, isFieldMode, selectedTable, localSearchQuery])

  const filteredTables = React.useMemo(() => {
    if (isDatasourceMode && !selectedDatasource) {
      return []
    }

    const datasource = selectedDatasource
      ? datasources.find(ds => ds.name === selectedDatasource)
      : (datasources.length === 1 ? datasources[0] : null)

    if (!datasource) {
      return []
    }

    const tables = Object.keys(datasource.tables)

    const baseTables = tableSearchTerm
      ? tables.filter(table =>
          table.toLowerCase().includes(tableSearchTerm.toLowerCase())
        )
      : tables

    if (isColumnMode && selectedTable && !baseTables.includes(selectedTable)) {
      return [selectedTable, ...baseTables]
    }

    return baseTables
  }, [datasources, isDatasourceMode, selectedDatasource, tableSearchTerm, isColumnMode, selectedTable])

  // Create unified list of tables and all columns for @ dropdown
  const unifiedItems = React.useMemo(() => {
    // Early return if dropdown not shown - avoid expensive calculation
    if (!showDropdown) {
      return []
    }

    const items: Array<{type: 'table' | 'column', name: string, tableName?: string, displayName: string}> = []
    const normalizedTableSearch = tableSearchTerm.toLowerCase()
    const normalizedColumnSearch = columnSearchQuery.toLowerCase()
    const normalizedRawSearch = searchQuery.toLowerCase()

    // Add filtered tables
    filteredTables.forEach(tableName => {
      if (!tableSearchTerm || tableName.toLowerCase().includes(normalizedTableSearch)) {
        items.push({
          type: 'table',
          name: tableName,
          displayName: tableName
        })
      }
    })

    // Only compute columns if function is available
    if (getTableColumns) {
      tableNames.forEach(tableName => {
        const columns = getTableColumns(tableName)
        const tableMatches = !tableSearchTerm || tableName.toLowerCase().includes(normalizedTableSearch)

        // Skip column iteration if table doesn't match
        if (!tableMatches) return

        columns.forEach(column => {
          const columnMatches = columnSearchQuery
            ? column.name.toLowerCase().includes(normalizedColumnSearch)
            : !searchQuery || `${tableName}:${column.name}`.toLowerCase().includes(normalizedRawSearch)

          if (columnMatches) {
            items.push({
              type: 'column',
              name: column.name,
              tableName: tableName,
              displayName: `${tableName}:${column.name}`
            })
          }
        })
      })
    }

    return items
  }, [showDropdown, filteredTables, tableNames, searchQuery, getTableColumns, tableSearchTerm, columnSearchQuery])

  const hoveredDatasource = React.useMemo(() => {
    if (showDropdown && isDatasourceMode && filteredDatasources[selectedIndex]) {
      return filteredDatasources[selectedIndex].name
    }
    if (selectedDatasource) {
      return selectedDatasource
    }
    return null
  }, [showDropdown, isDatasourceMode, selectedDatasource, filteredDatasources, selectedIndex])

  // Get the currently hovered/selected table for showing columns
  const hoveredTable = React.useMemo(() => {
    if (showDropdown && !isDatasourceMode && !isColumnMode && filteredTables[selectedIndex]) {
      return filteredTables[selectedIndex]
    }
    if (isColumnMode && selectedTable) {
      return selectedTable
    }
    return null
  }, [showDropdown, isDatasourceMode, filteredTables, selectedIndex, selectedTable, isColumnMode])

  // Get columns for the hovered table (with filtering if in column mode)
  const columnsForHoveredTable = React.useMemo(() => {
    if (!hoveredTable || !getTableColumns) return []
    const datasourceName = selectedDatasource || (datasources.length === 1 ? datasources[0].name : null)
    const columns = getTableColumns(hoveredTable, datasourceName || undefined)

    // If we're in column mode (but not field mode) and have a local search, filter the columns
    if (isColumnMode && !isFieldMode && localSearchQuery) {
      return columns.filter(col =>
        col.name.toLowerCase().includes(localSearchQuery.toLowerCase())
      )
    }

    return columns
  }, [hoveredTable, getTableColumns, isColumnMode, isFieldMode, localSearchQuery, selectedDatasource, datasources])

  const buildFieldOptions = useCallback((schema?: NestedSchemaNode | null): FieldOption[] => {
    const children = collectSchemaChildren(schema)
    return children.map(child => ({
      name: child.key,
      schema: child.schema,
      displayType: formatSchemaType(child.schema),
      fromArray: child.fromArray,
      hasChildren: schemaHasChildren(child.schema)
    }))
  }, [])

  const activeColumn = React.useMemo(() => {
    if (!isColumnMode) return null

    // When in field mode with an active column name, find it from all columns (not filtered)
    if (isFieldMode && activeColumnName && hoveredTable && getTableColumns) {
      const allColumns = getTableColumns(hoveredTable)
      return allColumns.find(col => col.name === activeColumnName) ?? null
    }

    // Otherwise use the selected column from filtered list
    return columnsForHoveredTable[selectedColumnIndex] ?? null
  }, [isColumnMode, isFieldMode, activeColumnName, hoveredTable, getTableColumns, columnsForHoveredTable, selectedColumnIndex])

  const activeColumnSchema = React.useMemo(() => activeColumn?.nestedSchema, [activeColumn])

  const currentFieldSchema = React.useMemo(() => {
    if (!activeColumnSchema) return undefined
    return getSchemaForPath(activeColumnSchema, fieldPath)
  }, [activeColumnSchema, fieldPath])

  const fieldOptions = React.useMemo(() => {
    if (!activeColumnSchema) return []
    const baseSchema = currentFieldSchema ?? (fieldPath.length > 0 ? undefined : activeColumnSchema)
    if (!baseSchema) return []
    const options = buildFieldOptions(baseSchema)
    // Use local search query when in field mode
    if (!isFieldMode || !localSearchQuery) {
      return options
    }
    const needle = localSearchQuery.toLowerCase()
    return options.filter(option => option.name.toLowerCase().includes(needle))
  }, [activeColumnSchema, buildFieldOptions, currentFieldSchema, fieldPath.length, isFieldMode, localSearchQuery])

  // Filter columns for selected table
  const filteredColumns = React.useMemo(() => {
    if (!selectedTable || !getTableColumns) return []
    const columns = getTableColumns(selectedTable)
    return columnSearchQuery
      ? columns.filter(column => 
          column.name.toLowerCase().includes(columnSearchQuery.toLowerCase())
        )
      : columns
  }, [selectedTable, columnSearchQuery, getTableColumns])
  
  // Reset selected index when filtered tables change (only in table mode, not in datasource mode)
  useEffect(() => {
    if (showDropdown && !isDatasourceMode && !isColumnMode && !isFieldMode) {
      setSelectedIndex(0)
    }
  }, [filteredTables.length, showDropdown, isDatasourceMode, isColumnMode, isFieldMode, setSelectedIndex])
  
  // Reset column selected index when columns for hovered table change (only in column mode)
  useEffect(() => {
    if (isColumnMode && !isFieldMode) {
      setSelectedColumnIndex(0)
    }
  }, [columnsForHoveredTable.length, isColumnMode, isFieldMode])

  useEffect(() => {
    if (!isColumnMode) {
      resetFieldState()
    }
  }, [isColumnMode, resetFieldState])

  // Reset field selected index when field options change (only in field mode)
  useEffect(() => {
    if (isFieldMode) {
      setSelectedFieldIndex(0)
    }
  }, [fieldOptions.length, isFieldMode])

  // Handle external value changes (like clearing input)
  useEffect(() => {
    if (externalValue !== undefined && externalValue !== inputValue) {
      setStoreInputValue(externalValue)
      setShowDropdown(false)
      setMentionStart(-1)
      setSearchQuery('')
    }
  }, [externalValue, inputValue, setStoreInputValue, setShowDropdown, setMentionStart, setSearchQuery])

  // Update cursor position when input changes
  useEffect(() => {
    if (inputRef.current) {
      setCursorPosition(inputRef.current.selectionStart || 0)
    }
  }, [inputValue, setCursorPosition])

  // Check for @ mentions (simplified - only detects @ position)
  const checkForMention = useCallback((value: string, cursorPos: number) => {
    const beforeCursor = value.slice(0, cursorPos)
    const lastAtIndex = beforeCursor.lastIndexOf('@')

    if (lastAtIndex === -1) {
      closeDropdown()
      return
    }

    const afterAt = beforeCursor.slice(lastAtIndex + 1)
    if (afterAt.includes(' ')) {
      closeDropdown()
      return
    }

    // Set the basics - open dropdown and set mention position
    setMentionStart(lastAtIndex)
    setSearchQuery(afterAt)
    setShowDropdown(true)
    setLocalSearchQuery(afterAt)

    // Show all panels: datasources (if multiple) | tables | columns
    if (datasources.length === 1) {
      setIsDatasourceMode(false)
      setSelectedDatasource(datasources[0].name)
    } else if (datasources.length > 1) {
      setIsDatasourceMode(true)
      setSelectedDatasource(datasources[0].name)  // Auto-select first so tables show
    }

    setIsColumnMode(false)
    setSelectedTable(null)
    setSelectedIndex(0)
  }, [closeDropdown, setShowDropdown, setMentionStart, setSearchQuery, datasources])

  // Handle input value changes
  const handleInputValueChange = useCallback((value: string) => {
    setStoreInputValue(value)
    onInputChange(value)

    const cursorPos = inputRef.current?.selectionStart ?? value.length

    // When we already have an active mention, make sure it is still valid
    if (mentionStart >= 0) {
      if (
        mentionStart >= value.length ||
        cursorPos <= mentionStart ||
        value.charAt(mentionStart) !== '@'
      ) {
        closeDropdown()
        return
      }

      const beforeCursor = value.slice(0, cursorPos)
      const afterAt = beforeCursor.slice(mentionStart + 1)

      // Close dropdown if whitespace was typed (breaks mention)
      if (afterAt.includes(' ') || afterAt.includes('\n')) {
        closeDropdown()
        return
      }

      // Update search query for display
      setSearchQuery(afterAt)

      if (isFieldMode) {
        let fieldSearch = afterAt
        const base = columnModeBaseQueryRef.current
        if (base && fieldSearch.startsWith(base)) {
          fieldSearch = fieldSearch.slice(base.length)
        }
        while (fieldSearch.startsWith(':')) {
          fieldSearch = fieldSearch.slice(1)
        }
        if (activeColumnName && fieldSearch.startsWith(activeColumnName)) {
          fieldSearch = fieldSearch.slice(activeColumnName.length)
        }
        while (fieldSearch.startsWith(':')) {
          fieldSearch = fieldSearch.slice(1)
        }
        setLocalSearchQueryDebounced(fieldSearch)
      } else if (isColumnMode) {
        let columnSearch = afterAt
        const base = columnModeBaseQueryRef.current
        if (base && columnSearch.startsWith(base)) {
          columnSearch = columnSearch.slice(base.length)
        }
        while (columnSearch.startsWith(':')) {
          columnSearch = columnSearch.slice(1)
        }
        setLocalSearchQueryDebounced(columnSearch)
      } else {
        // In table mode, typing searches tables
        const colonIndex = afterAt.indexOf(':')
        const tableSearch = colonIndex === -1 ? afterAt : afterAt.slice(0, colonIndex)
        setLocalSearchQueryDebounced(tableSearch)
      }

      if (!showDropdown) {
        setShowDropdown(true)
      }
      return
    }

    // No active mention yet - look for one after the DOM updates the cursor
    setTimeout(() => {
      if (inputRef.current) {
        const updatedCursorPos = inputRef.current.selectionStart || 0
        checkForMention(value, updatedCursorPos)
      }
    }, 0)
  }, [
    onInputChange,
    setStoreInputValue,
    mentionStart,
    setSearchQuery,
    isFieldMode,
    isColumnMode,
    showDropdown,
    setShowDropdown,
    closeDropdown,
    checkForMention,
    activeColumnName,
    setLocalSearchQueryDebounced
  ])

  // Navigate from datasource to tables
  const navigateToTablesFromDatasource = useCallback(() => {
    if (filteredDatasources.length > 0) {
      const datasource = filteredDatasources[selectedIndex]
      setSelectedDatasource(datasource.name)
      setIsDatasourceMode(false)
      setLocalSearchQuery('')
      setSelectedIndex(0)
    }
  }, [filteredDatasources, selectedIndex])

  const navigateToDatasources = useCallback(() => {
    if (datasources.length > 1) {
      setIsDatasourceMode(true)
      setSelectedDatasource(datasources[0].name)
      setSelectedTable(null)
      setLocalSearchQuery('')
      setSelectedIndex(0)
      setIsColumnMode(false)
      resetFieldState()
    }
  }, [datasources.length, datasources, resetFieldState])

  // Navigate to column selection
  const navigateToColumns = useCallback(() => {
    if (filteredTables.length > 0 && columnsForHoveredTable.length > 0) {
      const tableName = filteredTables[selectedIndex]
      setSelectedTable(tableName)
      setIsColumnMode(true)
      setLocalSearchQuery('')  // Clear search when entering new context
      setSelectedColumnIndex(0)
      columnModeBaseQueryRef.current = searchQuery ?? ''
      resetFieldState()
    }
  }, [columnsForHoveredTable.length, filteredTables, resetFieldState, searchQuery, selectedIndex])
  
  // Navigate back to table selection
  const navigateBackToTables = useCallback(() => {
    setIsColumnMode(false)
    setSelectedTable(null)
    setActiveColumnName(null)
    setLocalSearchQuery('')  // Clear search when changing context
    setSelectedColumnIndex(0)
    columnModeBaseQueryRef.current = ''
    resetFieldState()
  }, [resetFieldState])

  const handleDatasourceHover = useCallback((datasourceName: string) => {
    setSelectedDatasource(datasourceName)
  }, [])

  const handleDatasourceSelect = useCallback((datasourceName: string) => {
    setSelectedDatasource(datasourceName)
    setIsDatasourceMode(false)
    setSelectedIndex(0)
    setLocalSearchQuery('')  // Clear search when selecting datasource
  }, [])

  // Handle table selection - works identically for both Enter key and mouse clicks
  const handleTableSelect = useCallback((tableName: string) => {
    // Find the current @ mention position
    let actualMentionStart = mentionStart
    let actualSearchQuery = searchQuery
    
    if (inputRef.current && actualMentionStart === -1) {
      const cursorPos = inputRef.current.selectionStart || 0
      const beforeCursor = inputValue.slice(0, cursorPos)
      const lastAtIndex = beforeCursor.lastIndexOf('@')
      
      if (lastAtIndex !== -1) {
        const afterAt = beforeCursor.slice(lastAtIndex + 1)
        if (!afterAt.includes(' ')) {
          actualMentionStart = lastAtIndex
          actualSearchQuery = afterAt
        }
      }
    }
    
    if (actualMentionStart === -1) return

    // Get the datasource name
    const datasourceName = selectedDatasource || (datasources.length === 1 ? datasources[0].name : null)
    if (!datasourceName) return  // Should not happen, but safety check

    // Replace current @mention with @datasourceName:tablename followed by space
    const beforeMention = inputValue.slice(0, actualMentionStart)
    const afterMention = inputValue.slice(actualMentionStart + actualSearchQuery.length + 1)
    const mentionText = `${datasourceName}:${tableName}`
    const newValue = `${beforeMention}@${mentionText} ${afterMention}`
    const newCursorPos = actualMentionStart + mentionText.length + 2

    setStoreInputValue(newValue)
    onInputChange(newValue)
    closeDropdown()
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus()
        inputRef.current.setSelectionRange(newCursorPos, newCursorPos)
      }
    }, 0)
  }, [inputValue, setStoreInputValue, onInputChange, closeDropdown, mentionStart, searchQuery, selectedDatasource, datasources])

  // Handle unified selection (both tables and columns)
  const handleUnifiedSelect = useCallback((item: {type: 'table' | 'column', name: string, tableName?: string, displayName: string}) => {

    if (item.type === 'table') {
      handleTableSelect(item.name)
    } else if (item.type === 'column' && item.tableName) {
      // For column selection, we need to replace the entire @ mention with @table:column
      let actualMentionStart = -1
      
      if (inputRef.current) {
        const cursorPos = inputRef.current.selectionStart || 0
        const beforeCursor = inputValue.slice(0, cursorPos)
        const lastAtIndex = beforeCursor.lastIndexOf('@')
        
        if (lastAtIndex !== -1) {
          const afterAt = beforeCursor.slice(lastAtIndex + 1)
          if (!afterAt.includes(' ')) {
            actualMentionStart = lastAtIndex
          }
        }
      }
      
      let newValue = ''
      let newCursorPos = 0
      
      if (actualMentionStart !== -1) {
        // Replace current @mention with @table:column
        const beforeMention = inputValue.slice(0, actualMentionStart)
        const afterAtPos = inputValue.indexOf(' ', actualMentionStart)
        const afterMention = afterAtPos !== -1 ? inputValue.slice(afterAtPos) : ''
        newValue = `${beforeMention}@${item.tableName}:${item.name} ${afterMention}`
        newCursorPos = actualMentionStart + item.tableName.length + item.name.length + 3 // +3 for @, :, space
      } else {
        // Append to end
        newValue = `${inputValue}@${item.tableName}:${item.name} `
        newCursorPos = newValue.length
      }
      
      setStoreInputValue(newValue)
      onInputChange(newValue)
      closeDropdown()
      
      setTimeout(() => {
        if (inputRef.current) {
          inputRef.current.focus()
          inputRef.current.setSelectionRange(newCursorPos, newCursorPos)
        }
      }, 0)
    }
  }, [inputValue, handleTableSelect, setStoreInputValue, onInputChange, closeDropdown])
  
  // Handle column selection - works identically for both Enter key and mouse clicks
  const handleColumnSelect = useCallback((columnName: string, tableName?: string) => {
    // Find the current @ mention position
    let actualMentionStart = mentionStart
    let actualSearchQuery = searchQuery ?? ''
    const actualSelectedTable = tableName || hoveredTable || selectedTable

    if (inputRef.current && actualMentionStart === -1) {
      const cursorPos = inputRef.current.selectionStart || 0
      const beforeCursor = inputValue.slice(0, cursorPos)
      const lastAtIndex = beforeCursor.lastIndexOf('@')

      if (lastAtIndex !== -1) {
        const afterAt = beforeCursor.slice(lastAtIndex + 1)
        if (!afterAt.includes(' ')) {
          actualMentionStart = lastAtIndex
          actualSearchQuery = afterAt
        }
      }
    }

    if (actualMentionStart === -1 || !actualSelectedTable) {
      return
    }

    // Get the datasource name
    const datasourceName = selectedDatasource || (datasources.length === 1 ? datasources[0].name : null)
    if (!datasourceName) return  // Should not happen, but safety check

    // Replace current @mention with @datasourceName:table:column followed by space
    const beforeMention = inputValue.slice(0, actualMentionStart)
    const afterMention = inputValue.slice(actualMentionStart + actualSearchQuery.length + 1)
    const mentionText = `${datasourceName}:${actualSelectedTable}:${columnName}`
    const newValue = `${beforeMention}@${mentionText} ${afterMention}`
    const newCursorPos = actualMentionStart + mentionText.length + 2

    setStoreInputValue(newValue)
    onInputChange(newValue)
    setActiveColumnName(columnName)  // Set the active column name
    closeDropdown()

    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus()
        inputRef.current.setSelectionRange(newCursorPos, newCursorPos)
      }
    }, 0)
  }, [inputValue, setStoreInputValue, onInputChange, closeDropdown, mentionStart, searchQuery, hoveredTable, selectedTable, selectedDatasource, datasources])

  const enterFieldMode = useCallback(() => {
    const column = columnsForHoveredTable[selectedColumnIndex]
    if (!column?.nestedSchema) return
    setActiveColumnName(column.name)
    setIsFieldMode(true)
    setFieldPath([])
    setSelectedFieldIndex(0)
    setLocalSearchQuery('')  // Clear search when entering new context
  }, [columnsForHoveredTable, selectedColumnIndex])

  const drillIntoField = useCallback((fieldName: string) => {
    if (!activeColumnSchema) return
    const nextSchema = getSchemaForPath(activeColumnSchema, [...fieldPath, fieldName])
    if (!nextSchema || !schemaHasChildren(nextSchema)) {
      return
    }
    const newPath = [...fieldPath, fieldName]
    setFieldPath(newPath)
    setLocalSearchQuery('')  // Clear search when drilling deeper
    setSelectedFieldIndex(0)
  }, [activeColumnSchema, fieldPath])

  const navigateBackFromField = useCallback(() => {
    if (fieldPath.length === 0) {
      // Going back from field mode to column mode
      setIsFieldMode(false)
      setFieldPath([])
      setSelectedFieldIndex(0)
      setLocalSearchQuery('')  // Clear search when changing context
      return
    }

    // Going back within field navigation
    const nextPath = fieldPath.slice(0, -1)
    setFieldPath(nextPath)
    setLocalSearchQuery('')  // Clear search
    setSelectedFieldIndex(0)
  }, [fieldPath])

  const handleFieldSelect = useCallback((fieldName: string) => {
    if (!activeColumn) return
    const actualSelectedTable = selectedTable || hoveredTable
    if (!actualSelectedTable) return

    let actualMentionStart = mentionStart
    let actualSearchQuery = searchQuery ?? ''

    if (inputRef.current && actualMentionStart === -1) {
      const cursorPos = inputRef.current.selectionStart || 0
      const beforeCursor = inputValue.slice(0, cursorPos)
      const lastAtIndex = beforeCursor.lastIndexOf('@')

      if (lastAtIndex !== -1) {
        const afterAt = beforeCursor.slice(lastAtIndex + 1)
        if (!afterAt.includes(' ')) {
          actualMentionStart = lastAtIndex
          actualSearchQuery = afterAt
        }
      }
    }

    if (actualMentionStart === -1) {
      return
    }

    // Get the datasource name
    const datasourceName = selectedDatasource || (datasources.length === 1 ? datasources[0].name : null)
    if (!datasourceName) return

    const beforeMention = inputValue.slice(0, actualMentionStart)
    const afterMention = inputValue.slice(actualMentionStart + actualSearchQuery.length + 1)
    const fullPath = [...fieldPath, fieldName]
    const replacementCore = `${datasourceName}:${actualSelectedTable}:${activeColumn.name}:${fullPath.join(':')}`
    const newValue = `${beforeMention}@${replacementCore} ${afterMention}`
    const newCursorPos = beforeMention.length + replacementCore.length + 2

    setStoreInputValue(newValue)
    onInputChange(newValue)
    closeDropdown()

    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus()
        inputRef.current.setSelectionRange(newCursorPos, newCursorPos)
      }
    }, 0)
  }, [activeColumn, closeDropdown, fieldPath, hoveredTable, inputValue, mentionStart, onInputChange, searchQuery, selectedTable, setStoreInputValue, selectedDatasource, datasources])

  // Handle keyboard navigation
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (showDropdown && isDatasourceMode && filteredDatasources.length > 0) {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          e.stopPropagation()
          {
            const newIndex = (selectedIndex + 1) % filteredDatasources.length
            setSelectedIndex(newIndex)
            setSelectedDatasource(filteredDatasources[newIndex].name)
          }
          return

        case 'ArrowUp':
          e.preventDefault()
          e.stopPropagation()
          {
            const newIndex = selectedIndex === 0 ? filteredDatasources.length - 1 : selectedIndex - 1
            setSelectedIndex(newIndex)
            setSelectedDatasource(filteredDatasources[newIndex].name)
          }
          return

        case 'ArrowRight':
          e.preventDefault()
          e.stopPropagation()
          navigateToTablesFromDatasource()
          return

        case 'Enter':
        case 'Tab':
          if (filteredDatasources[selectedIndex]) {
            e.preventDefault()
            e.stopPropagation()
            handleDatasourceSelect(filteredDatasources[selectedIndex].name)
            return
          }
          break

        case 'Escape':
          e.preventDefault()
          e.stopPropagation()
          closeDropdown()
          return
      }
      return
    }

    if (showDropdown && !isDatasourceMode && filteredTables.length > 0) {
      if (isColumnMode && isFieldMode) {
        const hasOptions = fieldOptions.length > 0
        switch (e.key) {
          case 'ArrowDown':
            if (hasOptions) {
              e.preventDefault()
              e.stopPropagation()
              setSelectedFieldIndex(prev => (prev + 1) % fieldOptions.length)
              return
            }
            break

          case 'ArrowUp':
            if (hasOptions) {
              e.preventDefault()
              e.stopPropagation()
              setSelectedFieldIndex(prev => prev === 0 ? fieldOptions.length - 1 : prev - 1)
              return
            }
            break

          case 'ArrowRight':
            if (hasOptions) {
              const option = fieldOptions[selectedFieldIndex]
              if (option?.hasChildren) {
                e.preventDefault()
                e.stopPropagation()
                drillIntoField(option.name)
                return
              }
            }
            break

          case 'ArrowLeft':
            e.preventDefault()
            e.stopPropagation()
            navigateBackFromField()
            return

          case 'Enter':
          case 'Tab':
            if (hasOptions) {
              e.preventDefault()
              e.stopPropagation()
              handleFieldSelect(fieldOptions[selectedFieldIndex].name)
              return
            }
            break

          case 'Escape':
            e.preventDefault()
            e.stopPropagation()
            closeDropdown()
            return
        }
      } else if (isColumnMode && columnsForHoveredTable.length > 0) {
        switch (e.key) {
          case 'ArrowDown':
            e.preventDefault()
            e.stopPropagation()
            setSelectedColumnIndex(prev => (prev + 1) % columnsForHoveredTable.length)
            return

          case 'ArrowUp':
            e.preventDefault()
            e.stopPropagation()
            setSelectedColumnIndex(prev => prev === 0 ? columnsForHoveredTable.length - 1 : prev - 1)
            return

          case 'ArrowRight':
            {
              const currentColumn = columnsForHoveredTable[selectedColumnIndex]
              if (currentColumn?.nestedSchema && schemaHasChildren(currentColumn.nestedSchema)) {
                e.preventDefault()
                e.stopPropagation()
                enterFieldMode()
                return
              }
            }
            break

          case 'ArrowLeft':
            e.preventDefault()
            e.stopPropagation()
            navigateBackToTables()
            return

          case 'Enter':
          case 'Tab':
            if (columnsForHoveredTable[selectedColumnIndex]) {
              e.preventDefault()
              e.stopPropagation()
              handleColumnSelect(columnsForHoveredTable[selectedColumnIndex].name, hoveredTable || undefined)
              return
            }
            break

          case 'Escape':
            e.preventDefault()
            e.stopPropagation()
            closeDropdown()
            return
        }
      } else {
        switch (e.key) {
          case 'ArrowDown':
            e.preventDefault()
            e.stopPropagation()
            setSelectedIndex(prev => (prev + 1) % filteredTables.length)
            return

          case 'ArrowUp':
            e.preventDefault()
            e.stopPropagation()
            setSelectedIndex(prev => prev === 0 ? filteredTables.length - 1 : prev - 1)
            return

          case 'ArrowRight':
            if (getTableColumns && columnsForHoveredTable.length > 0) {
              e.preventDefault()
              e.stopPropagation()
              navigateToColumns()
              return
            }
            break

          case 'ArrowLeft':
            if (datasources.length > 1) {
              e.preventDefault()
              e.stopPropagation()
              navigateToDatasources()
              return
            }
            break

          case 'Enter':
          case 'Tab':
            if (filteredTables[selectedIndex]) {
              e.preventDefault()
              e.stopPropagation()
              handleTableSelect(filteredTables[selectedIndex])
              return
            }
            break

          case 'Escape':
            e.preventDefault()
            e.stopPropagation()
            closeDropdown()
            return
        }
      }
    }

    if (showColumnDropdown && filteredColumns.length > 0) {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          e.stopPropagation()
          setSelectedColumnIndex(prev => (prev + 1) % filteredColumns.length)
          return

        case 'ArrowUp':
          e.preventDefault()
          e.stopPropagation()
          setSelectedColumnIndex(prev => prev === 0 ? filteredColumns.length - 1 : prev - 1)
          return

        case 'ArrowLeft':
          e.preventDefault()
          e.stopPropagation()
          navigateBackToTables()
          return

        case 'Enter':
        case 'Tab':
          if (filteredColumns[selectedColumnIndex]) {
            e.preventDefault()
            e.stopPropagation()
            handleColumnSelect(filteredColumns[selectedColumnIndex].name, selectedTable || '')
            return
          }
          break

        case 'Escape':
          e.preventDefault()
          e.stopPropagation()
          closeDropdown()
          return
      }
    }

    if (e.key === 'Enter' && !e.shiftKey && !showDropdown && !showColumnDropdown) {
      e.preventDefault()
      onSubmit?.()
    }
  }, [
    showDropdown,
    isDatasourceMode,
    filteredDatasources,
    selectedIndex,
    navigateToTablesFromDatasource,
    handleDatasourceSelect,
    filteredTables,
    isColumnMode,
    isFieldMode,
    fieldOptions,
    selectedFieldIndex,
    drillIntoField,
    navigateBackFromField,
    handleFieldSelect,
    columnsForHoveredTable,
    selectedColumnIndex,
    enterFieldMode,
    navigateBackToTables,
    handleColumnSelect,
    hoveredTable,
    getTableColumns,
    navigateToColumns,
    handleTableSelect,
    closeDropdown,
    showColumnDropdown,
    filteredColumns,
    selectedTable,
    onSubmit,
    datasources.length,
    navigateToDatasources
  ])

  // Handle clicks outside (will be used by the component)
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (inputRef.current && !inputRef.current.contains(event.target as Node)) {
        closeDropdown()
      }
    }

    if (showDropdown) {
      // Use 'click' instead of 'mousedown' to let onClick handlers fire first
      document.addEventListener('click', handleClickOutside)
      return () => document.removeEventListener('click', handleClickOutside)
    }
  }, [showDropdown, closeDropdown])

  return {
    inputValue,
    setInputValue: handleInputValueChange,
    showDropdown,
    filteredDatasources,
    filteredTables,
    unifiedItems,
    selectedIndex,
    setSelectedIndex,
    cursorPosition,
    handleKeyDown,
    handleDatasourceHover,
    handleDatasourceSelect,
    handleTableSelect,
    handleColumnSelect,
    handleUnifiedSelect,
    closeDropdown,
    inputRef,
    // Datasource selection
    hoveredDatasource,
    selectedDatasource,
    isDatasourceMode,
    navigateToDatasources,
    navigateToTablesFromDatasource,
    // Column selection
    showColumnDropdown,
    selectedTable,
    filteredColumns,
    selectedColumnIndex,
    setSelectedColumnIndex,
    navigateToColumns,
    navigateBackToTables,
    hoveredTable,
    columnsForHoveredTable,
    isColumnMode,
    columnSearchQuery,
    activeColumn,
    isFieldMode,
    fieldPath,
    fieldOptions,
    selectedFieldIndex,
    setSelectedFieldIndex,
    enterFieldMode,
    drillIntoField,
    navigateBackFromField,
    handleFieldSelect,
    fieldSearchQuery,
    searchQuery,
    localSearchQuery
  }
}
