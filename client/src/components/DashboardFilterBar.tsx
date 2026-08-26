import React, { useEffect, useMemo, useRef, useState } from "react"
import {
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Filter,
  RotateCcw,
  Search,
  X,
} from "lucide-react"

import type { DashboardFilterDefinition, FilterOption } from "../services/api"
import { cn } from "../lib/utils"
import { isEmptyFilterValue } from "../utils/dashboardFilters"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Input } from "./ui/input"
import { Label } from "./ui/label"

interface DashboardFilterBarProps {
  filters: DashboardFilterDefinition[]
  values: Record<string, unknown>
  onChange: (nextValues: Record<string, unknown>) => void
  storageKey?: string
  className?: string
  headerActions?: React.ReactNode
  variant?: "topbar" | "sidebar"
  showHeader?: boolean
  collapsible?: boolean
  defaultCollapsed?: boolean
}

interface SearchableSingleSelectProps {
  value: string
  options: NormalizedOption[]
  placeholder: string
  onChange: (nextValue: string) => void
}

interface SearchableMultiSelectProps {
  values: string[]
  options: NormalizedOption[]
  placeholder: string
  onChange: (nextValues: string[]) => void
}

interface DatePickerFieldProps {
  value: string
  placeholder?: string
  onChange: (nextValue: string) => void
}

const getFilterKeys = (filter: DashboardFilterDefinition): string[] => {
  if (filter.filter_type === "date_range") {
    return [`${filter.id}_start`, `${filter.id}_end`]
  }
  if (filter.filter_type === "number_range") {
    return [`${filter.id}_min`, `${filter.id}_max`]
  }
  return [filter.id]
}

const getAppliedCount = (
  filters: DashboardFilterDefinition[],
  values: Record<string, unknown>,
): number => {
  let count = 0
  for (const filter of filters) {
    const keys = getFilterKeys(filter)
    if (keys.some((key) => !isEmptyFilterValue(values[key]))) {
      count += 1
    }
  }
  return count
}

const getRenderableFilters = (
  filters: DashboardFilterDefinition[],
): DashboardFilterDefinition[] => {
  const byId = new Map<string, DashboardFilterDefinition[]>()

  for (const filter of filters) {
    const group = byId.get(filter.id) ?? []
    group.push(filter)
    byId.set(filter.id, group)
  }

  const getTypePriority = (filterType: DashboardFilterDefinition["filter_type"]): number => {
    if (filterType === "select") return 50
    if (filterType === "multiselect") return 45
    if (filterType === "date_range") return 40
    if (filterType === "number_range") return 35
    if (filterType === "text") return 30
    return 10
  }

  const merged = Array.from(byId.values()).map((group) => {
    const representative = [...group]
      .sort((a, b) => {
        const typeDelta = getTypePriority(b.filter_type) - getTypePriority(a.filter_type)
        if (typeDelta !== 0) return typeDelta
        const optionDelta =
          (Array.isArray(b.options) ? b.options.length : 0) -
          (Array.isArray(a.options) ? a.options.length : 0)
        if (optionDelta !== 0) return optionDelta

        const aGeneric = !a.display_label || a.display_label.trim() === a.field_name
        const bGeneric = !b.display_label || b.display_label.trim() === b.field_name
        if (aGeneric !== bGeneric) {
          return aGeneric ? 1 : -1
        }
        return 0
      })[0]

    const mergedOptions: Array<string | number | boolean | FilterOption> = []
    const seenOptions = new Set<string>()
    for (const candidate of group) {
      const candidateOptions = Array.isArray(candidate.options) ? candidate.options : []
      for (const option of candidateOptions) {
        if (typeof option === "string" && option.trim() === "") {
          continue
        }
        const signature = typeof option === "object" && option !== null && "label" in option && "value" in option
          ? `obj:${option.label}:${option.value}`
          : `${typeof option}:${String(option)}`
        if (seenOptions.has(signature)) {
          continue
        }
        seenOptions.add(signature)
        mergedOptions.push(option)
      }
    }

    const explicitLabel = group.find((candidate) => {
      const label = (candidate.display_label || "").trim()
      return label.length > 0 && label !== candidate.field_name
    })?.display_label

    const representativeOptions = Array.isArray(representative.options)
      ? representative.options.filter((option) => !(typeof option === "string" && option.trim() === ""))
      : null

    const mergedOptionsOrNull = mergedOptions.length > 0
      ? mergedOptions
      : representativeOptions && representativeOptions.length > 0
        ? representativeOptions
        : null

    const needsTextFallback =
      (representative.filter_type === "select" || representative.filter_type === "multiselect")
      && (!mergedOptionsOrNull || mergedOptionsOrNull.length === 0)

    return {
      ...representative,
      display_label: explicitLabel || representative.display_label || representative.field_name,
      filter_type: needsTextFallback ? "text" : representative.filter_type,
      operator: needsTextFallback ? "contains" : representative.operator,
      options: needsTextFallback ? null : mergedOptionsOrNull,
    }
  })

  return merged.sort((a, b) => {
    const labelA = (a.display_label || a.field_name || "").toLowerCase()
    const labelB = (b.display_label || b.field_name || "").toLowerCase()
    if (labelA === labelB) {
      return a.id.localeCompare(b.id)
    }
    return labelA.localeCompare(labelB)
  })
}

interface NormalizedOption {
  label: string
  value: string
}

const normalizeFilterOptions = (
  options: Array<string | number | boolean | FilterOption>,
): NormalizedOption[] => {
  const normalized: NormalizedOption[] = []
  const seen = new Set<string>()

  for (const option of options) {
    let label: string
    let value: string

    if (typeof option === "object" && option !== null && "label" in option && "value" in option) {
      label = String(option.label).trim()
      value = String(option.value).trim()
    } else {
      const stringValue = String(option)
      label = typeof option === "string" ? option.trim() : stringValue
      value = label
    }

    if (!value || !label) {
      continue
    }
    const signature = `${label}:${value}`
    if (seen.has(signature)) {
      continue
    }
    seen.add(signature)
    normalized.push({ label, value })
  }

  return normalized
}

const toIsoDate = (date: Date): string => {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, "0")
  const day = `${date.getDate()}`.padStart(2, "0")
  return `${year}-${month}-${day}`
}

const startOfMonthIso = (): string => {
  const date = new Date()
  date.setDate(1)
  return toIsoDate(date)
}

const endOfMonthIso = (): string => {
  const date = new Date()
  date.setMonth(date.getMonth() + 1, 0)
  return toIsoDate(date)
}

const daysAgoIso = (days: number): string => {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return toIsoDate(date)
}

const todayIso = (): string => toIsoDate(new Date())

const getFilterTypeLabel = (filterType: DashboardFilterDefinition["filter_type"]): string => {
  if (filterType === "date_range") return "Date"
  if (filterType === "number_range") return "Range"
  if (filterType === "multiselect") return "Multi"
  if (filterType === "select") return "Select"
  return "Text"
}

const useDismissableDropdown = (
  isOpen: boolean,
  onClose: () => void,
): React.RefObject<HTMLDivElement | null> => {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isOpen) {
      return
    }

    const handleDocumentMouseDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        onClose()
      }
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose()
      }
    }

    document.addEventListener("mousedown", handleDocumentMouseDown)
    document.addEventListener("keydown", handleEscape)

    return () => {
      document.removeEventListener("mousedown", handleDocumentMouseDown)
      document.removeEventListener("keydown", handleEscape)
    }
  }, [isOpen, onClose])

  return containerRef
}

function SearchableSingleSelect({
  value,
  options,
  placeholder,
  onChange,
}: SearchableSingleSelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [searchValue, setSearchValue] = useState("")
  const closeDropdown = () => setIsOpen(false)
  const containerRef = useDismissableDropdown(isOpen, closeDropdown)

  useEffect(() => {
    if (!isOpen) {
      setSearchValue("")
    }
  }, [isOpen])

  const selectedOption = useMemo(() => {
    return options.find((opt) => opt.value === value)
  }, [options, value])

  const displayValue = selectedOption ? selectedOption.label : value

  const filteredOptions = useMemo(() => {
    const query = searchValue.trim().toLowerCase()
    if (!query) {
      return options
    }
    return options.filter((option) => option.label.toLowerCase().includes(query))
  }, [options, searchValue])

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex h-8 w-full items-center justify-between rounded-md border border-[#3a424d] bg-[#10141a] px-2.5 text-xs text-gray-100 transition-colors hover:border-[#4b5563]"
      >
        <span className={cn("truncate", value ? "text-gray-100" : "text-gray-500")}>
          {displayValue || placeholder}
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 text-gray-400 transition-transform", isOpen && "rotate-180")} />
      </button>

      {isOpen && (
        <div className="absolute left-0 top-full z-50 mt-1.5 w-full overflow-hidden rounded-lg border border-[#39414c] bg-[#151a21] shadow-[0_14px_24px_rgba(0,0,0,0.45)]">
          <div className="border-b border-[#27303b] p-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
              <Input
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder="Search options"
                className="h-8 border-[#313a45] bg-[#10141a] pl-7 text-xs text-gray-100"
              />
            </div>
          </div>

          <div className="custom-scrollbar max-h-48 overflow-y-auto p-1.5">
            <button
              type="button"
              onClick={() => {
                onChange("")
                setIsOpen(false)
              }}
              className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs text-gray-200 transition-colors hover:bg-[#222a34]"
            >
              <span>All values</span>
              {!value && <Check className="h-3.5 w-3.5 text-brand-orange" />}
            </button>

            {filteredOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  onChange(option.value)
                  setIsOpen(false)
                }}
                className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs text-gray-200 transition-colors hover:bg-[#222a34]"
              >
                <span className="truncate">{option.label}</span>
                {value === option.value && <Check className="h-3.5 w-3.5 text-brand-orange" />}
              </button>
            ))}

            {filteredOptions.length === 0 && (
              <div className="px-2 py-2 text-xs text-gray-500">No matching options</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function SearchableMultiSelect({
  values,
  options,
  placeholder,
  onChange,
}: SearchableMultiSelectProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [searchValue, setSearchValue] = useState("")
  const closeDropdown = () => setIsOpen(false)
  const containerRef = useDismissableDropdown(isOpen, closeDropdown)

  useEffect(() => {
    if (!isOpen) {
      setSearchValue("")
    }
  }, [isOpen])

  const selectedSet = useMemo(() => new Set(values), [values])

  const selectedLabels = useMemo(() => {
    return values.map((val) => {
      const option = options.find((opt) => opt.value === val)
      return option ? option.label : val
    })
  }, [values, options])

  const filteredOptions = useMemo(() => {
    const query = searchValue.trim().toLowerCase()
    if (!query) {
      return options
    }
    return options.filter((option) => option.label.toLowerCase().includes(query))
  }, [options, searchValue])

  const triggerLabel =
    values.length === 0
      ? placeholder
      : values.length === 1
        ? selectedLabels[0]
        : `${values.length} selected`

  const toggleOption = (optionValue: string) => {
    if (selectedSet.has(optionValue)) {
      onChange(values.filter((item) => item !== optionValue))
      return
    }
    onChange([...values, optionValue])
  }

  const selectFiltered = () => {
    if (!filteredOptions.length) {
      return
    }
    const nextValues = [...values]
    const existing = new Set(values)
    for (const option of filteredOptions) {
      if (!existing.has(option.value)) {
        nextValues.push(option.value)
      }
    }
    onChange(nextValues)
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex h-8 w-full items-center justify-between rounded-md border border-[#3a424d] bg-[#10141a] px-2.5 text-xs text-gray-100 transition-colors hover:border-[#4b5563]"
      >
        <span className={cn("truncate", values.length > 0 ? "text-gray-100" : "text-gray-500")}>
          {triggerLabel}
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 text-gray-400 transition-transform", isOpen && "rotate-180")} />
      </button>

      {isOpen && (
        <div className="absolute left-0 top-full z-50 mt-1.5 w-full overflow-hidden rounded-lg border border-[#39414c] bg-[#151a21] shadow-[0_14px_24px_rgba(0,0,0,0.45)]">
          <div className="border-b border-[#27303b] p-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
              <Input
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder="Search options"
                className="h-8 border-[#313a45] bg-[#10141a] pl-7 text-xs text-gray-100"
              />
            </div>
            <div className="mt-1.5 flex items-center justify-between text-[10px] text-gray-400">
              <span>{values.length} selected</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => onChange([])}
                  className="text-gray-400 transition-colors hover:text-white"
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={selectFiltered}
                  className="text-brand-orange transition-colors hover:text-brand-orange-hover"
                >
                  Select filtered
                </button>
              </div>
            </div>
          </div>

          <div className="custom-scrollbar max-h-56 overflow-y-auto p-1.5">
            {filteredOptions.map((option) => {
              const isSelected = selectedSet.has(option.value)
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => toggleOption(option.value)}
                  className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs text-gray-200 transition-colors hover:bg-[#222a34]"
                >
                  <span className="truncate">{option.label}</span>
                  {isSelected && <Check className="h-3.5 w-3.5 text-brand-orange" />}
                </button>
              )
            })}

            {filteredOptions.length === 0 && (
              <div className="px-2 py-2 text-xs text-gray-500">No matching options</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function DatePickerField({
  value,
  placeholder = "mm/dd/yyyy",
  onChange,
}: DatePickerFieldProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const openPicker = () => {
    const input = inputRef.current as
      | (HTMLInputElement & { showPicker?: () => void })
      | null
    if (!input) {
      return
    }

    if (typeof input.showPicker === "function") {
      input.showPicker()
      return
    }

    input.focus()
    input.click()
  }

  return (
    <div className="relative">
      <CalendarDays className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
      <Input
        ref={inputRef}
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-8 rounded-lg border-[#3a424d] bg-[#0f141d] pl-8 pr-14 text-xs text-gray-100 transition-colors focus-visible:ring-brand-orange/40 [color-scheme:dark] [&::-webkit-calendar-picker-indicator]:absolute [&::-webkit-calendar-picker-indicator]:right-0 [&::-webkit-calendar-picker-indicator]:h-full [&::-webkit-calendar-picker-indicator]:w-9 [&::-webkit-calendar-picker-indicator]:cursor-pointer [&::-webkit-calendar-picker-indicator]:opacity-0"
      />
      <div className="absolute inset-y-0 right-1 flex items-center gap-0.5">
        {value && (
          <button
            type="button"
            onClick={() => onChange("")}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-[#1c232d] hover:text-white"
            aria-label="Clear date"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
        <button
          type="button"
          onClick={openPicker}
          className="rounded-md p-1 text-gray-500 transition-colors hover:bg-[#1c232d] hover:text-brand-orange"
          aria-label="Open date picker"
        >
          <CalendarDays className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}

export function DashboardFilterBar({
  filters,
  values,
  onChange,
  storageKey,
  className,
  headerActions,
  variant = "topbar",
  showHeader = true,
  collapsible = true,
  defaultCollapsed = true,
}: DashboardFilterBarProps) {
  const collapseStorageKey =
    collapsible && storageKey ? `${storageKey}:collapsed` : null

  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    if (!collapsible) {
      return false
    }
    if (!collapseStorageKey || typeof window === "undefined") {
      return defaultCollapsed
    }
    const persisted = window.localStorage.getItem(collapseStorageKey)
    if (persisted === null) {
      return defaultCollapsed
    }
    return persisted === "1"
  })

  useEffect(() => {
    if (!collapsible || !collapseStorageKey || typeof window === "undefined") return
    window.localStorage.setItem(collapseStorageKey, isCollapsed ? "1" : "0")
  }, [collapsible, collapseStorageKey, isCollapsed])

  const renderableFilters = useMemo(
    () => getRenderableFilters(filters),
    [filters],
  )
  const latestValuesRef = useRef(values)
  useEffect(() => {
    latestValuesRef.current = values
  }, [values])

  const appliedCount = useMemo(
    () => getAppliedCount(renderableFilters, values),
    [renderableFilters, values],
  )

  const updateValues = (patch: Record<string, unknown>) => {
    const nextValues = { ...latestValuesRef.current }
    for (const [key, rawValue] of Object.entries(patch)) {
      if (isEmptyFilterValue(rawValue)) {
        delete nextValues[key]
      } else {
        nextValues[key] = rawValue
      }
    }
    onChange(nextValues)
  }

  const updateValue = (key: string, rawValue: unknown) => {
    updateValues({ [key]: rawValue })
  }

  const clearAll = () => {
    onChange({})
  }

  if (!renderableFilters.length) {
    return null
  }

  const showBody = collapsible ? !isCollapsed : true

  return (
    <div
      className={cn(
        variant === "topbar"
          ? "relative z-20 overflow-visible border-b border-[#2b2f35] bg-[#101215]"
          : "relative z-20 overflow-visible",
        className,
      )}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(251,146,60,0.14),transparent_55%)]" />
      <div
        className={cn(
          "relative",
          variant === "topbar" ? "px-3.5 py-2.5" : "p-0",
        )}
      >
        {showHeader && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => setIsCollapsed((prev) => !prev)}
              className="inline-flex items-center gap-2 rounded-md px-1.5 py-1 text-xs text-gray-200 transition-colors hover:bg-[#1a1f25] hover:text-white"
            >
              {isCollapsed ? (
                <ChevronRight className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
              <Filter className="h-3.5 w-3.5 text-brand-orange" />
              <span className="font-medium tracking-wide">Filters</span>
            </button>

            <div className="flex flex-wrap items-center gap-2">
              <Badge className="border border-[#39414c] bg-[#181c22] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-gray-300">
                {appliedCount} active
              </Badge>
              <Badge className="border border-brand-orange/30 bg-brand-orange/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-brand-orange">
                Auto apply
              </Badge>
              {headerActions}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={clearAll}
                disabled={appliedCount === 0}
                className="h-7 border-[#3b414b] bg-[#13171c] px-2.5 text-[11px] text-gray-200 hover:border-[#596272] hover:bg-[#1c222b] hover:text-white"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Clear
              </Button>
            </div>
          </div>
        )}

        {showBody && (
          <div
            className={cn(
              "grid grid-cols-1 items-start gap-2.5",
              showHeader && "mt-2.5",
              variant === "topbar" && "md:grid-cols-2 xl:grid-cols-3",
            )}
          >
            {renderableFilters.map((filter) => {
              const filterLabel = filter.display_label || filter.field_name
              const options = normalizeFilterOptions(
                Array.isArray(filter.options) ? filter.options : [],
              )
              const filterIsActive = getFilterKeys(filter).some(
                (key) => !isEmptyFilterValue(values[key]),
              )

              return (
                <div
                  key={filter.id}
                  className={cn(
                    "rounded-xl border bg-[#14181e] p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-colors",
                    filterIsActive
                      ? "border-brand-orange/50"
                      : "border-[#2f353f]",
                  )}
                >
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <Label className="text-[11px] font-medium tracking-wide text-gray-200">
                      {filterLabel}
                    </Label>
                    <Badge className="border border-[#3b414a] bg-[#1d232c] px-1.5 py-0 text-[9px] font-semibold uppercase tracking-[0.09em] text-gray-400">
                      {getFilterTypeLabel(filter.filter_type)}
                    </Badge>
                  </div>

                  {filter.filter_type === "select" && (
                    <SearchableSingleSelect
                      value={String(values[filter.id] ?? "")}
                      options={options}
                      placeholder="All values"
                      onChange={(nextValue) => updateValue(filter.id, nextValue)}
                    />
                  )}

                  {filter.filter_type === "multiselect" && (
                    <div className="space-y-1.5">
                      <SearchableMultiSelect
                        values={
                          Array.isArray(values[filter.id])
                            ? (values[filter.id] as unknown[]).map((value) =>
                                String(value),
                              )
                            : []
                        }
                        options={options}
                        placeholder="Select values"
                        onChange={(nextValues) => updateValue(filter.id, nextValues)}
                      />
                    </div>
                  )}

                  {filter.filter_type === "date_range" && (
                    <div className="space-y-2 rounded-lg border border-[#2f3845] bg-[#10161f]/80 p-2">
                      <div className="grid grid-cols-2 gap-1.5">
                        <div>
                          <DatePickerField
                            value={String(values[`${filter.id}_start`] ?? "")}
                            onChange={(nextValue) =>
                              updateValue(`${filter.id}_start`, nextValue)
                            }
                          />
                        </div>
                        <div>
                          <DatePickerField
                            value={String(values[`${filter.id}_end`] ?? "")}
                            onChange={(nextValue) =>
                              updateValue(`${filter.id}_end`, nextValue)
                            }
                          />
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-1">
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            updateValues({
                              [`${filter.id}_start`]: todayIso(),
                              [`${filter.id}_end`]: todayIso(),
                            })
                          }
                          className="h-5 rounded-full border border-[#3b424d] px-2 text-[10px] text-gray-300 hover:border-brand-orange/50 hover:bg-brand-orange/10 hover:text-brand-orange"
                        >
                          Today
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            updateValues({
                              [`${filter.id}_start`]: daysAgoIso(7),
                              [`${filter.id}_end`]: todayIso(),
                            })
                          }
                          className="h-5 rounded-full border border-[#3b424d] px-2 text-[10px] text-gray-300 hover:border-brand-orange/50 hover:bg-brand-orange/10 hover:text-brand-orange"
                        >
                          Last 7d
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            updateValues({
                              [`${filter.id}_start`]: daysAgoIso(30),
                              [`${filter.id}_end`]: todayIso(),
                            })
                          }
                          className="h-5 rounded-full border border-[#3b424d] px-2 text-[10px] text-gray-300 hover:border-brand-orange/50 hover:bg-brand-orange/10 hover:text-brand-orange"
                        >
                          Last 30d
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            updateValues({
                              [`${filter.id}_start`]: startOfMonthIso(),
                              [`${filter.id}_end`]: endOfMonthIso(),
                            })
                          }
                          className="h-5 rounded-full border border-[#3b424d] px-2 text-[10px] text-gray-300 hover:border-brand-orange/50 hover:bg-brand-orange/10 hover:text-brand-orange"
                        >
                          This month
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            updateValues({
                              [`${filter.id}_start`]: "",
                              [`${filter.id}_end`]: "",
                            })
                          }
                          className="h-5 rounded-full border border-[#3b424d] px-2 text-[10px] text-gray-300 hover:border-[#8b93a1] hover:bg-[#212834] hover:text-white"
                        >
                          Clear
                        </Button>
                      </div>
                    </div>
                  )}

                  {filter.filter_type === "number_range" && (
                    <div className="grid grid-cols-2 gap-1.5">
                      <Input
                        type="number"
                        value={String(values[`${filter.id}_min`] ?? "")}
                        onChange={(event) =>
                          updateValue(`${filter.id}_min`, event.target.value)
                        }
                        placeholder="Min"
                        className="h-8 border-[#3a424d] bg-[#10141a] text-xs text-gray-100"
                      />
                      <Input
                        type="number"
                        value={String(values[`${filter.id}_max`] ?? "")}
                        onChange={(event) =>
                          updateValue(`${filter.id}_max`, event.target.value)
                        }
                        placeholder="Max"
                        className="h-8 border-[#3a424d] bg-[#10141a] text-xs text-gray-100"
                      />
                    </div>
                  )}

                  {(filter.filter_type === "text" ||
                    ![
                      "select",
                      "multiselect",
                      "date_range",
                      "number_range",
                    ].includes(filter.filter_type)) && (
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
                      <Input
                        type="text"
                        value={String(values[filter.id] ?? "")}
                        onChange={(event) =>
                          updateValue(filter.id, event.target.value)
                        }
                        placeholder={`Search ${filterLabel}`}
                        className="h-8 border-[#3a424d] bg-[#10141a] pl-7 text-xs text-gray-100"
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
