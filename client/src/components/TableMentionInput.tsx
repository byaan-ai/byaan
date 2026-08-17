import { forwardRef, type HTMLAttributes, useRef, useEffect, useCallback, useMemo } from 'react'
import { Table, Columns, Search, ChevronRight, Database } from 'lucide-react'
import { useTableMentions, type ColumnInfo } from '../hooks/useTableMentions'
import { cn } from '../utils/cn'

// Helper to parse database name from table name format: "[DatabaseName] table_name"
interface ParsedTableName {
  databaseName: string | null
  tableName: string
  fullName: string
}

function parseTableName(fullName: string): ParsedTableName {
  const match = fullName.match(/^\[([^\]]+)\]\s+(.+)$/)
  if (match) {
    return {
      databaseName: match[1],
      tableName: match[2],
      fullName
    }
  }
  return {
    databaseName: null,
    tableName: fullName,
    fullName
  }
}

// Group tables by database
interface DatabaseGroup {
  databaseName: string
  tables: ParsedTableName[]
}

function groupTablesByDatabase(tableNames: string[]): DatabaseGroup[] {
  const groups = new Map<string, ParsedTableName[]>()

  tableNames.forEach(fullName => {
    const parsed = parseTableName(fullName)
    const dbKey = parsed.databaseName || '__single__'

    if (!groups.has(dbKey)) {
      groups.set(dbKey, [])
    }
    groups.get(dbKey)!.push(parsed)
  })

  // Convert to array and sort
  return Array.from(groups.entries())
    .map(([dbName, tables]) => ({
      databaseName: dbName === '__single__' ? '' : dbName,
      tables
    }))
    .sort((a, b) => a.databaseName.localeCompare(b.databaseName))
}

interface TableMentionInputProps extends Omit<HTMLAttributes<HTMLTextAreaElement>, 'onChange' | 'onKeyDown'> {
  placeholder?: string
  disabled?: boolean
  datasources: Array<{ id: string; name: string; tables: Record<string, any> }>
  tableNames: string[]
  onValueChange: (value: string) => void
  onSubmit?: () => void
  value?: string
  getTableColumns?: (tableName: string, datasourceName?: string) => ColumnInfo[]
  singleLine?: boolean
  onHeightChange?: (height: number) => void
}

export const TableMentionInput = forwardRef<HTMLTextAreaElement, TableMentionInputProps>(
  ({ className, placeholder, disabled, datasources, tableNames, onValueChange, onSubmit, value, getTableColumns, singleLine = false, onHeightChange, ...props }, forwardedRef) => {
    const datasourceItemRefs = useRef<(HTMLDivElement | null)[]>([])
    const itemRefs = useRef<(HTMLDivElement | null)[]>([])
    const columnItemRefs = useRef<(HTMLDivElement | null)[]>([])
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    
    const {
      inputValue,
      setInputValue,
      showDropdown,
      filteredDatasources,
      filteredTables,
      selectedIndex,
      handleKeyDown,
      handleDatasourceHover,
      handleDatasourceSelect,
      handleTableSelect,
      handleColumnSelect,
      inputRef,
      setSelectedIndex,
      // Datasource states
      hoveredDatasource,
      selectedDatasource,
      isDatasourceMode,
      // Column states
      hoveredTable,
      columnsForHoveredTable,
      selectedColumnIndex,
      setSelectedColumnIndex,
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
    } = useTableMentions({
      datasources,
      tableNames,
      onInputChange: onValueChange,
      onSubmit,
      externalValue: value,
      getTableColumns
    })

    const fieldItemRefs = useRef<(HTMLDivElement | null)[]>([])

    // Auto-resize textarea function
    const adjustTextareaHeight = useCallback(() => {
      const textarea = textareaRef.current || inputRef.current
      if (textarea) {
        const minH = singleLine ? 56 : 40
        const maxH = singleLine ? 120 : 128
        textarea.style.height = 'auto'
        const target = Math.max(minH, Math.min(textarea.scrollHeight, maxH))
        textarea.style.height = `${target}px`
        textarea.style.overflowY = textarea.scrollHeight > maxH ? 'auto' : 'hidden'
        if (onHeightChange) {
          try {
            onHeightChange(target)
          } catch {
            return
          }
        }
      }
    }, [singleLine, onHeightChange])

    // Auto-resize on input value change
    useEffect(() => {
      adjustTextareaHeight()
    }, [inputValue, adjustTextareaHeight])

    // Auto-scroll to selected datasource when index changes
    useEffect(() => {
      if (showDropdown && isDatasourceMode && datasourceItemRefs.current[selectedIndex]) {
        datasourceItemRefs.current[selectedIndex]?.scrollIntoView({
          block: 'nearest',
          behavior: 'auto'
        })
      }
    }, [selectedIndex, showDropdown, isDatasourceMode])

    // Auto-scroll to selected table when index changes
    useEffect(() => {
      if (showDropdown && !isDatasourceMode && !isColumnMode && itemRefs.current[selectedIndex]) {
        itemRefs.current[selectedIndex]?.scrollIntoView({
          block: 'nearest',
          behavior: 'auto'
        })
      }
    }, [selectedIndex, showDropdown, isDatasourceMode, isColumnMode])

    // Auto-scroll to selected column when index changes
    useEffect(() => {
      if (showDropdown && isColumnMode && columnItemRefs.current[selectedColumnIndex]) {
        columnItemRefs.current[selectedColumnIndex]?.scrollIntoView({
          block: 'nearest',
          behavior: 'auto'
        })
      }
    }, [selectedColumnIndex, showDropdown, isColumnMode])

    // Auto-scroll to selected nested field when index changes
    useEffect(() => {
      if (showDropdown && isFieldMode && fieldItemRefs.current[selectedFieldIndex]) {
        fieldItemRefs.current[selectedFieldIndex]?.scrollIntoView({
          block: 'nearest',
          behavior: 'auto'
        })
      }
    }, [selectedFieldIndex, showDropdown, isFieldMode])

    const currentColumn = useMemo(() =>
      columnsForHoveredTable[selectedColumnIndex],
      [columnsForHoveredTable, selectedColumnIndex]
    )

    const currentColumnSchema = useMemo(() =>
      currentColumn?.nestedSchema,
      [currentColumn]
    )

    const currentColumnHasNested = useMemo(() => {
      const schemaProps = currentColumnSchema?.properties
      const itemProps = currentColumnSchema?.items?.properties
      return Boolean(
        (schemaProps && Object.keys(schemaProps).length > 0) ||
        (itemProps && Object.keys(itemProps).length > 0)
      )
    }, [currentColumnSchema])

    const hasColumns = useMemo(() =>
      columnsForHoveredTable.length > 0,
      [columnsForHoveredTable.length]
    )

    const showColumnPanel = useMemo(() =>
      // Only show columns when in column mode or when in table mode with columns
      // Don't show when in datasource mode
      !isDatasourceMode && (isColumnMode || hasColumns),
      [isDatasourceMode, isColumnMode, hasColumns]
    )

    const hasNestedFields = useMemo(() =>
      currentColumnHasNested && fieldOptions.length > 0,
      [currentColumnHasNested, fieldOptions.length]
    )

    const showDatasourcePanel = useMemo(() =>
      datasources.length > 1,
      [datasources.length]
    )

    const showTablePanel = useMemo(() =>
      !isDatasourceMode || selectedDatasource !== null,
      [isDatasourceMode, selectedDatasource]
    )

    const showFieldPanel = useMemo(() =>
      showColumnPanel && (isFieldMode || Boolean(hasNestedFields)),
      [showColumnPanel, isFieldMode, hasNestedFields]
    )

    const dropdownWidth = useMemo(() => {
      let width = 0
      if (showDatasourcePanel) width += 200
      if (showTablePanel) width += 200
      if (showColumnPanel) width += 220
      if (showFieldPanel) width += 220
      return Math.max(width, 360)  // Minimum width
    }, [showDatasourcePanel, showTablePanel, showColumnPanel, showFieldPanel])

  // Group tables by database for display
  const databaseGroups = useMemo(() =>
    groupTablesByDatabase(filteredTables),
    [filteredTables]
  )

  // Check if we have multiple databases
  const hasMultipleDatabases = useMemo(() =>
    databaseGroups.length > 1 || (databaseGroups.length === 1 && databaseGroups[0].databaseName !== ''),
    [databaseGroups]
  )

  // Calculate total tables across all groups for proper index mapping
  const getAbsoluteTableIndex = useCallback((groupIndex: number, tableIndexInGroup: number): number => {
    let absoluteIndex = 0
    for (let i = 0; i < groupIndex; i++) {
      absoluteIndex += databaseGroups[i].tables.length
    }
    return absoluteIndex + tableIndexInGroup
  }, [databaseGroups])

  const getTableByAbsoluteIndex = useCallback((absoluteIndex: number): string | null => {
    let currentIndex = 0
    for (const group of databaseGroups) {
      if (absoluteIndex < currentIndex + group.tables.length) {
        return group.tables[absoluteIndex - currentIndex].fullName
      }
      currentIndex += group.tables.length
    }
    return null
  }, [databaseGroups])

    const tablePanelWidth = useMemo(() =>
      showColumnPanel ? 200 : dropdownWidth,
      [showColumnPanel, dropdownWidth]
    )

    const columnPanelWidth = useMemo(() =>
      showColumnPanel ? 220 : 0,
      [showColumnPanel]
    )

    const fieldPanelWidth = useMemo(() =>
      showFieldPanel ? 220 : 0,
      [showFieldPanel]
    )

    const columnPanelTitle = 'Columns'
    const fieldPanelTitle = activeColumn?.name || 'Fields'
    const fieldBreadcrumb = useMemo(() =>
      [fieldPanelTitle, ...fieldPath].filter(Boolean),
      [fieldPanelTitle, fieldPath]
    )

    return (
      <div className="relative w-full">
        <textarea
          ref={(el) => {
            inputRef.current = el
            textareaRef.current = el
            // Forward ref to parent
            if (typeof forwardedRef === 'function') {
              forwardedRef(el)
            } else if (forwardedRef) {
              forwardedRef.current = el
            }
          }}
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value)
            adjustTextareaHeight()
          }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className={cn(
            "bg-transparent border-none text-white placeholder-[#888888] focus:ring-0 focus:border-none disabled:opacity-50 w-full outline-none resize-none custom-scrollbar",
            singleLine ? 'py-1.5' : 'p-0',
            className
          )}
          style={{ height: singleLine ? '56px' : '40px', maxHeight: singleLine ? '120px' : '128px', lineHeight: '1.4' }}
          {...props}
        />

        {/* Multi-panel dropdown for datasources, tables, columns, and nested fields */}
        {showDropdown && (showDatasourcePanel || showColumnPanel || filteredTables.length > 0 || filteredDatasources.length > 0) && (
          <div className="fixed z-[9999] mb-3" style={{
            bottom: inputRef.current ? `${window.innerHeight - inputRef.current.getBoundingClientRect().top + 12}px` : '120px',
            left: inputRef.current ? `${inputRef.current.getBoundingClientRect().left}px` : '2rem',
          }}>
            <div
              className="bg-[#1a1a1a] border border-[#3a3a3a] rounded-xl shadow-2xl overflow-hidden"
              style={{ width: `${dropdownWidth}px` }}
            >
              <div className="flex h-[350px]">
                {/* Datasources Panel */}
                {showDatasourcePanel && (
                  <>
                    <div
                      className="flex flex-col flex-shrink-0"
                      style={{ width: '200px' }}
                    >
                      <div className="px-4 py-3 bg-gradient-to-r from-[#2a2a2a] to-[#252525] border-b border-[#3a3a3a]">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Database className="w-4 h-4 text-green-400" />
                            <span className="text-sm font-semibold text-white">Datasources</span>
                          </div>
                          <span className="text-xs text-[#666666] bg-[#2a2a2a] px-2 py-0.5 rounded-full">
                            {filteredDatasources.length}
                          </span>
                        </div>
                        {localSearchQuery && isDatasourceMode && (
                          <div className="mt-2 flex items-center gap-1">
                            <Search className="w-3 h-3 text-[#666666]" />
                            <span className="text-xs text-[#888888]">Searching: "{localSearchQuery}"</span>
                          </div>
                        )}
                      </div>

                      {/* Datasources List */}
                      <div className="flex-1 overflow-y-auto custom-scrollbar max-h-[250px]">
                        {filteredDatasources.map((datasource, index) => {
                          const isSelected = index === selectedIndex && isDatasourceMode
                          const isHovered = datasource.name === hoveredDatasource

                          return (
                            <div
                              key={datasource.id}
                              ref={(el) => { datasourceItemRefs.current[index] = el }}
                              className={cn(
                                "group flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-all duration-150 relative",
                                isSelected
                                  ? "bg-gradient-to-r from-green-500/20 to-transparent text-white"
                                  : isHovered
                                  ? "bg-[#2a2a2a]/50 text-white"
                                  : "text-[#aaaaaa] hover:bg-[#2a2a2a]/30 hover:text-white"
                              )}
                              onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                handleDatasourceSelect(datasource.name)
                              }}
                              onMouseEnter={() => {
                                setSelectedIndex(index)
                                handleDatasourceHover(datasource.name)
                              }}
                            >
                              {isSelected && (
                                <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-green-400" />
                              )}
                              <div className={cn(
                                "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors",
                                isSelected
                                  ? "bg-green-500/20 border border-green-500/30"
                                  : "bg-[#2a2a2a] border border-[#3a3a3a] group-hover:border-[#4a4a4a]"
                              )}>
                                <Database className={cn(
                                  "w-4 h-4",
                                  isSelected
                                    ? "text-green-400"
                                    : "text-[#666666] group-hover:text-[#888888]"
                                )} />
                              </div>
                              <div className="flex-1 min-w-0">
                                <span className="font-medium text-sm truncate block" title={datasource.name}>
                                  {datasource.name.length > 15 ? `${datasource.name.substring(0, 15)}...` : datasource.name}
                                </span>
                                <span className="text-xs text-[#666666] truncate block">
                                  {Object.keys(datasource.tables).length} tables
                                </span>
                              </div>
                              {!isDatasourceMode && datasource.name === selectedDatasource && (
                                <ChevronRight className="w-3.5 h-3.5 text-[#666666] flex-shrink-0" />
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    <div className="w-px bg-gradient-to-b from-transparent via-[#3a3a3a] to-transparent" />
                  </>
                )}

                {/* Tables Panel */}
                {showTablePanel && (
                  <div
                  className="flex flex-col"
                  style={{ width: showColumnPanel ? `${tablePanelWidth}px` : '100%' }}
                >
                  <div className="px-4 py-3 bg-gradient-to-r from-[#2a2a2a] to-[#252525] border-b border-[#3a3a3a]">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Table className="w-4 h-4 text-green-400" />
                        <span className="text-sm font-semibold text-white">Tables</span>
                      </div>
                      <span className="text-xs text-[#666666] bg-[#2a2a2a] px-2 py-0.5 rounded-full">
                        {filteredTables.length}
                      </span>
                    </div>
                    {localSearchQuery && !isColumnMode && !isFieldMode && (
                      <div className="mt-2 flex items-center gap-1">
                        <Search className="w-3 h-3 text-[#666666]" />
                        <span className="text-xs text-[#888888]">Searching: "{localSearchQuery}"</span>
                      </div>
                    )}
                  </div>
                  
                  {/* Tables List */}
                  <div className="flex-1 overflow-y-auto custom-scrollbar max-h-[250px]">
                    {databaseGroups.map((group, groupIndex) => (
                      <div key={group.databaseName || 'default'}>
                        {/* Database Header (only show if multiple databases) */}
                        {hasMultipleDatabases && (
                          <div className="sticky top-0 z-10 px-3 py-2 bg-[#1f1f1f] border-b border-[#3a3a3a]">
                            <div className="flex items-center gap-2">
                              <Database className="w-3.5 h-3.5 text-blue-400" />
                              <span className="text-xs font-semibold text-blue-400 uppercase tracking-wide">
                                {group.databaseName || 'Default'}
                              </span>
                              <span className="text-xs text-[#666666] ml-auto">
                                {group.tables.length} {group.tables.length === 1 ? 'table' : 'tables'}
                              </span>
                            </div>
                          </div>
                        )}

                        {/* Tables in this database */}
                        {group.tables.map((parsed, tableIndexInGroup) => {
                          const absoluteIndex = getAbsoluteTableIndex(groupIndex, tableIndexInGroup)
                          const isSelected = absoluteIndex === selectedIndex && !isDatasourceMode && !isColumnMode
                          const isHovered = parsed.fullName === hoveredTable

                          return (
                            <div
                              key={parsed.fullName}
                              ref={(el) => { itemRefs.current[absoluteIndex] = el }}
                              className={cn(
                                "group flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-all duration-150 relative",
                                isSelected
                                  ? "bg-gradient-to-r from-green-500/20 to-transparent text-white"
                                  : isHovered
                                  ? "bg-[#2a2a2a]/50 text-white"
                                  : "text-[#aaaaaa] hover:bg-[#2a2a2a]/30 hover:text-white"
                              )}
                              onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                handleTableSelect(parsed.fullName)
                              }}
                              onMouseEnter={() => !isDatasourceMode && !isColumnMode && setSelectedIndex(absoluteIndex)}
                            >
                              {isSelected && (
                                <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-green-400" />
                              )}
                              <div className={cn(
                                "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors",
                                isSelected
                                  ? "bg-green-500/20 border border-green-500/30"
                                  : "bg-[#2a2a2a] border border-[#3a3a3a] group-hover:border-[#4a4a4a]"
                              )}>
                                <Table className={cn(
                                  "w-4 h-4",
                                  isSelected
                                    ? "text-green-400"
                                    : "text-[#666666] group-hover:text-[#888888]"
                                )} />
                              </div>
                              <div className="flex-1 min-w-0">
                                <span className="font-medium text-sm truncate block" title={parsed.tableName}>
                                  {parsed.tableName.length > 15 ? `${parsed.tableName.substring(0, 15)}...` : parsed.tableName}
                                </span>
                                {hasMultipleDatabases && parsed.databaseName && (
                                  <span className="text-xs text-[#666666] truncate block">
                                    {parsed.databaseName}
                                  </span>
                                )}
                              </div>
                              {showColumnPanel && parsed.fullName === hoveredTable && (
                                <ChevronRight className="w-3.5 h-3.5 text-[#666666] flex-shrink-0" />
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                </div>
                )}

                {showTablePanel && showColumnPanel && <div className="w-px bg-gradient-to-b from-transparent via-[#3a3a3a] to-transparent" />}

                {/* Columns Panel */}
                {showColumnPanel && (
                  <div
                    className="flex flex-col"
                    style={{ width: `${columnPanelWidth}px` }}
                  >
                    <div className="px-4 py-3 bg-gradient-to-r from-[#252525] to-[#2a2a2a] border-b border-[#3a3a3a]">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Columns className="w-4 h-4 text-green-400" />
                          <span className="text-sm font-semibold text-white">{columnPanelTitle}</span>
                        </div>
                        <span className="text-xs text-[#666666] bg-[#2a2a2a] px-2 py-0.5 rounded-full">
                          {columnsForHoveredTable.length} {columnsForHoveredTable.length === 1 ? 'col' : 'cols'}
                        </span>
                      </div>
                      {localSearchQuery && isColumnMode && !isFieldMode && (
                        <div className="mt-2 flex items-center gap-1">
                          <Search className="w-3 h-3 text-[#666666]" />
                          <span className="text-xs text-[#888888]">Searching: "{localSearchQuery}"</span>
                        </div>
                      )}
                    </div>
                    <div className="flex-1 overflow-y-auto custom-scrollbar max-h-[250px]">
                      {columnsForHoveredTable.length > 0 ? (
                        columnsForHoveredTable.map((column, index) => {
                          const nestedProps = column.nestedSchema?.properties
                          const nestedItemProps = column.nestedSchema?.items?.properties
                          const hasNested = Boolean(column.nestedSchema && ((nestedProps && Object.keys(nestedProps).length > 0) || (nestedItemProps && Object.keys(nestedItemProps as Record<string, unknown>).length > 0)))

                          return (
                            <div
                              key={column.name}
                              ref={(el) => { columnItemRefs.current[index] = el }}
                              className={cn(
                                "group flex items-center gap-2 px-3 py-2 cursor-pointer rounded-lg transition-all duration-150 mb-1",
                                index === selectedColumnIndex && isColumnMode
                                  ? "bg-gradient-to-r from-green-500/20 to-transparent text-white ring-1 ring-green-500/30"
                                  : "text-[#aaaaaa] hover:bg-[#2a2a2a]/50 hover:text-white"
                              )}
                              onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                handleColumnSelect(column.name, hoveredTable || undefined)
                              }}
                              onMouseEnter={() => isColumnMode && setSelectedColumnIndex(index)}
                            >
                              <div className="flex-1 min-w-0">
                                <div className="font-medium text-sm truncate" title={column.name}>
                                  {column.name.length > 15 ? `${column.name.substring(0, 15)}...` : column.name}
                                </div>
                                <div className="text-xs text-[#666666] mt-0.5 truncate">
                                  {column.type}
                                  {column.nullable && <span className="ml-2 text-[#555555]">nullable</span>}
                                </div>
                              </div>
                              {hasNested && (
                                <button
                                  type="button"
                                  className={cn(
                                    "ml-2 p-1 rounded transition-colors",
                                    index === selectedColumnIndex && isFieldMode ? "text-green-400" : "text-[#666666] hover:text-green-300"
                                  )}
                                  onMouseDown={(event) => {
                                    event.preventDefault()
                                    event.stopPropagation()
                                    setSelectedColumnIndex(index)
                                    enterFieldMode()
                                  }}
                                  aria-label="Explore nested fields"
                                >
                                  <ChevronRight className="w-3 h-3" />
                                </button>
                              )}
                            </div>
                          )
                        })
                      ) : (
                        <div className="flex items-center justify-center h-full text-[#666666] text-sm">
                          {isColumnMode && localSearchQuery ?
                            `No columns matching "${localSearchQuery}"` :
                            'No columns available'}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {showFieldPanel && <div className="w-px bg-gradient-to-b from-transparent via-[#3a3a3a] to-transparent" />}

                {/* Nested Fields Panel */}
                {showFieldPanel && (
                  <div
                    className="flex flex-col"
                    style={{ width: `${fieldPanelWidth}px` }}
                  >
                    <div className="px-4 py-3 bg-gradient-to-r from-[#252525] to-[#2a2a2a] border-b border-[#3a3a3a]">
                      <div className="flex items-center justify-between">
                        <div className="flex flex-col">
                          <div className="flex items-center gap-2">
                            <Columns className="w-4 h-4 text-green-400" />
                            <span className="text-sm font-semibold text-white truncate">{fieldPanelTitle}</span>
                          </div>
                          {fieldBreadcrumb.length > 1 && (
                            <div className="mt-1 text-[11px] text-[#777777] truncate">
                              {fieldBreadcrumb.join(' › ')}
                            </div>
                          )}
                        </div>
                        <span className="text-xs text-[#666666] bg-[#2a2a2a] px-2 py-0.5 rounded-full">
                          {fieldOptions.length} {fieldOptions.length === 1 ? 'field' : 'fields'}
                        </span>
                      </div>
                      {localSearchQuery && isFieldMode && (
                        <div className="mt-2 flex items-center gap-1">
                          <Search className="w-3 h-3 text-[#666666]" />
                          <span className="text-xs text-[#888888]">Searching: "{localSearchQuery}"</span>
                        </div>
                      )}
                    </div>
                    <div className="flex-1 overflow-y-auto custom-scrollbar max-h-[250px]">
                      {(() => {
                        fieldItemRefs.current = []
                        if (fieldOptions.length === 0) {
                          return (
                            <div className="flex items-center justify-center h-full text-[#666666] text-sm">
                              {localSearchQuery
                                ? `No fields matching "${localSearchQuery}"`
                                : 'No nested fields'}
                            </div>
                          )
                        }
                        return fieldOptions.map((option, index) => (
                          <div
                            key={`${option.name}-${index}`}
                            ref={(el) => { fieldItemRefs.current[index] = el }}
                            className={cn(
                              "group flex items-center gap-2 px-3 py-2 cursor-pointer rounded-lg transition-all duration-150 mb-1",
                              index === selectedFieldIndex && isFieldMode
                                ? "bg-gradient-to-r from-green-500/20 to-transparent text-white ring-1 ring-green-500/30"
                                : "text-[#aaaaaa] hover:bg-[#2a2a2a]/50 hover:text-white"
                            )}
                            onClick={(event) => {
                              event.preventDefault()
                              event.stopPropagation()
                              handleFieldSelect(option.name)
                            }}
                            onMouseEnter={() => isFieldMode && setSelectedFieldIndex(index)}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="font-medium text-sm truncate" title={option.name}>
                                {option.name.length > 15 ? `${option.name.substring(0, 15)}...` : option.name}
                              </div>
                              <div className="text-xs text-[#666666] mt-0.5 truncate">
                                {option.displayType}
                                {option.fromArray && <span className="ml-2 text-[#555555]">array item</span>}
                              </div>
                            </div>
                            {option.hasChildren && (
                              <button
                                type="button"
                                className="ml-2 p-1 rounded text-[#666666] hover:text-green-300 transition-colors"
                                onMouseDown={(event) => {
                                  event.preventDefault()
                                  event.stopPropagation()
                                  setSelectedFieldIndex(index)
                                  drillIntoField(option.name)
                                }}
                                aria-label="Dive into nested field"
                              >
                                <ChevronRight className="w-3 h-3" />
                              </button>
                            )}
                          </div>
                        ))
                      })()}
                    </div>
                  </div>
                )}
              </div>

              {/* Unified Navigation Help */}
              <div className="px-4 py-2 bg-[#1a1a1a] border-t border-[#3a3a3a] border-l border-r border-b border-[#2a2a2a] rounded-b-lg mx-2 mb-2">
                <div className="flex items-center justify-between gap-2 text-[10px] text-[#666666] w-full flex-wrap">
                  {isDatasourceMode ? (
                    <>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">↑↓</kbd>
                        Navigate
                      </span>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">→</kbd>
                        Tables
                      </span>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">Enter</kbd>
                        Select
                      </span>
                    </>
                  ) : isFieldMode ? (
                    <>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">←</kbd>
                        Back
                      </span>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">↑↓</kbd>
                        Navigate
                      </span>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">→</kbd>
                        Dive
                      </span>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">Enter</kbd>
                        Select
                      </span>
                    </>
                  ) : showColumnPanel ? (
                    <>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">←</kbd>
                        Tables
                      </span>
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">↑↓</kbd>
                        Navigate
                      </span>
                      {currentColumnHasNested && (
                        <span className="flex items-center gap-1">
                          <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">→</kbd>
                          Fields
                        </span>
                      )}
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">Enter</kbd>
                        Select
                      </span>
                    </>
                  ) : (
                    <>
                      {showDatasourcePanel && (
                        <span className="flex items-center gap-1">
                          <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">←</kbd>
                          Datasources
                        </span>
                      )}
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">↑↓</kbd>
                        Navigate
                      </span>
                      {hasColumns && (
                        <span className="flex items-center gap-1">
                          <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">→</kbd>
                          Columns
                        </span>
                      )}
                      <span className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 bg-[#2a2a2a] rounded text-[#888888]">Enter</kbd>
                        Select
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}


        {/* No tables found */}
        {showDropdown && filteredTables.length === 0 && (
          <div className="absolute bottom-full left-0 z-50 mb-3">
            <div
              className="bg-[#1a1a1a] border border-[#3a3a3a] rounded-xl shadow-2xl overflow-hidden px-4 py-3"
              style={{ width: `${dropdownWidth}px` }}
            >
              <div className="flex items-center gap-2 text-sm text-[#666666]">
                <Search className="w-4 h-4" />
                <span>No tables found</span>
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }
)

TableMentionInput.displayName = 'TableMentionInput'
