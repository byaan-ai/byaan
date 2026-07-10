import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dialog, DialogPortal, DialogOverlay } from './ui/dialog'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { Search, Plus, BookOpen, LayoutDashboard, X, Sparkles } from 'lucide-react'
import { useNotebooks } from '../hooks/useNotebooks'
import { ApiService, type Notebook } from '../services/api'
import { useQuery } from '@tanstack/react-query'
import type { DashboardsByFolder } from '../types/folder'
import { rewriteDashboardHtmlForBackend, ensureBaseHref, getBackendUrlForHtmlProcessing, injectViewerConfig } from '../utils/dashboardHtml'

interface CommandPaletteProps {
  isOpen: boolean
  onClose: () => void
}

interface SearchResult {
  id: string
  type: 'action' | 'notebook' | 'dashboard'
  name: string
  groupLabel?: string
  navigateTo?: string
  folderName?: string
  dashboardId?: string
}

function getDateGroup(dateString: string): string {
  const date = new Date(dateString)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000)
  const lastWeek = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000)

  if (date >= today) return 'Today'
  if (date >= yesterday) return 'Yesterday'
  if (date >= lastWeek) return 'Last 7 days'
  return 'Older'
}

export default function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const { data: notebooks = [] } = useNotebooks()

  const { data: dashboardsData } = useQuery<DashboardsByFolder>({
    queryKey: ['all-dashboards'],
    queryFn: () => ApiService.getAllDashboards(),
    enabled: isOpen,
    staleTime: 30000,
  })

  const [viewingDashboard, setViewingDashboard] = useState<{
    id: string
    name: string
    html: string
  } | null>(null)
  const [loadingDashboard, setLoadingDashboard] = useState(false)

  const results = useMemo(() => {
    const items: SearchResult[] = []
    const query = searchQuery.toLowerCase().trim()
    const isSearching = query.length > 0
    const MAX_INITIAL_NOTEBOOKS = 10
    const MAX_INITIAL_DASHBOARDS = 5

    items.push({
      id: 'new-notebook',
      type: 'action',
      name: 'New Notebook',
      navigateTo: '/notebook/new',
    })

    items.push({
      id: 'skill-review',
      type: 'action',
      name: 'Skill Review',
      navigateTo: '/skill-review',
    })

    const sortedNotebooks = [...notebooks].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    )

    const filteredNotebooks = isSearching
      ? sortedNotebooks.filter(n => n.notebook_name.toLowerCase().includes(query))
      : sortedNotebooks.slice(0, MAX_INITIAL_NOTEBOOKS)

    const groupedNotebooks: Record<string, Notebook[]> = {}
    filteredNotebooks.forEach(notebook => {
      const group = getDateGroup(notebook.updated_at)
      if (!groupedNotebooks[group]) groupedNotebooks[group] = []
      groupedNotebooks[group].push(notebook)
    })

    const groupOrder = ['Today', 'Yesterday', 'Last 7 days', 'Older']
    groupOrder.forEach(group => {
      if (groupedNotebooks[group]) {
        groupedNotebooks[group].forEach(notebook => {
          items.push({
            id: notebook.id,
            type: 'notebook',
            name: notebook.notebook_name,
            groupLabel: group,
            navigateTo: `/notebook/${notebook.id}`,
          })
        })
      }
    })

    const allDashboards: SearchResult[] = []
    if (dashboardsData?.folders) {
      dashboardsData.folders.forEach(folder => {
        folder.dashboards.forEach(dashboard => {
          if (!isSearching || dashboard.notebook_name?.toLowerCase().includes(query)) {
            allDashboards.push({
              id: `dashboard-${folder.folder_id}-${dashboard.id}`,
              type: 'dashboard',
              name: dashboard.notebook_name || 'Untitled Dashboard',
              groupLabel: 'Dashboards',
              folderName: folder.folder_name,
              dashboardId: dashboard.id,
            })
          }
        })
      })
    }

    const dashboardsToShow = isSearching ? allDashboards : allDashboards.slice(0, MAX_INITIAL_DASHBOARDS)
    items.push(...dashboardsToShow)

    const hasMoreNotebooks = !isSearching && sortedNotebooks.length > MAX_INITIAL_NOTEBOOKS
    const hasMoreDashboards = !isSearching && allDashboards.length > MAX_INITIAL_DASHBOARDS

    return { items, hasMoreNotebooks, hasMoreDashboards, totalNotebooks: sortedNotebooks.length, totalDashboards: allDashboards.length }
  }, [notebooks, dashboardsData, searchQuery])

  const { items: resultItems, hasMoreNotebooks, hasMoreDashboards, totalNotebooks, totalDashboards } = results

  useEffect(() => {
    if (isOpen) {
      setSearchQuery('')
      setSelectedIndex(0)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [isOpen])

  useEffect(() => {
    setSelectedIndex(0)
  }, [searchQuery])

  const scrollToSelected = useCallback((index: number) => {
    const list = listRef.current
    if (!list) return

    const items = list.querySelectorAll('[data-result-item]')
    const item = items[index] as HTMLElement
    if (item) {
      item.scrollIntoView({ block: 'nearest' })
    }
  }, [])

  const handleDashboardClick = useCallback(async (dashboardId: string, name: string) => {
    try {
      setLoadingDashboard(true)
      const detail = await ApiService.getViewerDashboard(dashboardId)

      const backendUrl = await getBackendUrlForHtmlProcessing()
      const rewritten = rewriteDashboardHtmlForBackend(detail.html_content, backendUrl)
      const withViewer = injectViewerConfig(rewritten, dashboardId)
      const withBase = ensureBaseHref(withViewer, backendUrl)

      setViewingDashboard({
        id: dashboardId,
        name,
        html: withBase,
      })
    } catch (err) {
      console.error('Failed to load dashboard:', err)
    } finally {
      setLoadingDashboard(false)
    }
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setSelectedIndex(prev => {
          const next = Math.min(prev + 1, resultItems.length - 1)
          scrollToSelected(next)
          return next
        })
        break
      case 'ArrowUp':
        e.preventDefault()
        setSelectedIndex(prev => {
          const next = Math.max(prev - 1, 0)
          scrollToSelected(next)
          return next
        })
        break
      case 'Enter': {
        e.preventDefault()
        const selected = resultItems[selectedIndex]
        if (selected) {
          if (selected.type === 'dashboard' && selected.dashboardId) {
            handleDashboardClick(selected.dashboardId, selected.name)
          } else if (selected.navigateTo) {
            navigate(selected.navigateTo)
            onClose()
          }
        }
        break
      }
      case 'Escape':
        e.preventDefault()
        onClose()
        break
    }
  }, [resultItems, selectedIndex, navigate, onClose, scrollToSelected, handleDashboardClick])

  const handleItemClick = useCallback((result: SearchResult) => {
    if (result.type === 'dashboard' && result.dashboardId) {
      handleDashboardClick(result.dashboardId, result.name)
      return
    }
    if (result.navigateTo) {
      navigate(result.navigateTo)
      onClose()
    }
  }, [navigate, onClose, handleDashboardClick])

  const getIcon = (result: SearchResult) => {
    if (result.id === 'skill-review') {
      return <Sparkles className="h-4 w-4" />
    }
    switch (result.type) {
      case 'action':
        return <Plus className="h-4 w-4" />
      case 'notebook':
        return <BookOpen className="h-4 w-4" />
      case 'dashboard':
        return <LayoutDashboard className="h-4 w-4" />
    }
  }

  let lastGroup = ''

  const closeDashboardViewer = useCallback(() => {
    setViewingDashboard(null)
  }, [])

  return (
    <>
    <Dialog open={isOpen && !viewingDashboard} onOpenChange={(open) => !open && onClose()}>
      <DialogPortal>
        <DialogOverlay className="bg-black/60" />
        <DialogPrimitive.Content
          className="fixed left-[50%] top-[15%] z-50 w-full max-w-[560px] translate-x-[-50%] rounded-xl border border-[#444444] bg-[#2a2a2a] shadow-2xl focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95"
          onKeyDown={handleKeyDown}
        >
          <div className="flex items-center gap-3 border-b border-[#444444] px-4 py-3">
            <Search className="h-5 w-5 text-[#888888]" />
            <input
              ref={inputRef}
              type="text"
              placeholder="Search notebooks..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="flex-1 bg-transparent text-white text-base placeholder-[#666666] focus:outline-none"
            />
            <kbd className="hidden sm:inline-flex h-5 select-none items-center gap-1 rounded border border-[#555555] bg-[#1a1a1a] px-1.5 font-mono text-[10px] font-medium text-[#888888]">
              ESC
            </kbd>
          </div>

          <div ref={listRef} className="max-h-[400px] overflow-y-auto py-2 custom-scrollbar">
            {resultItems.length === 0 ? (
              <div className="px-4 py-8 text-center text-[#888888]">
                No results found
              </div>
            ) : (
              <>
              {resultItems.map((result, index) => {
                const showGroup = result.groupLabel && result.groupLabel !== lastGroup
                if (showGroup) lastGroup = result.groupLabel!

                return (
                  <div key={result.id}>
                    {showGroup && (
                      <div className="px-4 py-1.5 text-xs font-medium text-[#888888]">
                        {result.groupLabel}
                      </div>
                    )}
                    <button
                      data-result-item
                      onClick={() => handleItemClick(result)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                        index === selectedIndex
                          ? 'bg-[#404040] text-white'
                          : 'text-[#cccccc] hover:bg-[#333333]'
                      }`}
                    >
                      <span className={`flex-shrink-0 ${
                        result.type === 'action' ? 'text-brand-orange' : 'text-[#888888]'
                      }`}>
                        {getIcon(result)}
                      </span>
                      <span className="flex-1 truncate">
                        {result.name}
                      </span>
                      {result.folderName && (
                        <span className="text-xs text-[#666666] truncate max-w-[120px]">
                          {result.folderName}
                        </span>
                      )}
                      {result.type === 'action' && index === 0 && (
                        <kbd className="hidden sm:inline-flex h-5 select-none items-center gap-1 rounded border border-[#555555] bg-[#1a1a1a] px-1.5 font-mono text-[10px] font-medium text-[#888888]">
                          Enter
                        </kbd>
                      )}
                    </button>
                  </div>
                )
              })}
              {(hasMoreNotebooks || hasMoreDashboards) && (
                <div className="px-4 py-3 text-xs text-[#666666] text-center border-t border-[#333333] mt-2">
                  Type to search all {totalNotebooks} notebooks{totalDashboards > 0 ? ` and ${totalDashboards} dashboards` : ''}
                </div>
              )}
              </>
            )}
          </div>

          <div className="flex items-center justify-between border-t border-[#444444] px-4 py-2 text-xs text-[#666666]">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1">
                <kbd className="inline-flex h-4 items-center rounded border border-[#555555] bg-[#1a1a1a] px-1 font-mono text-[10px]">
                  <span className="text-[10px]">↑</span>
                </kbd>
                <kbd className="inline-flex h-4 items-center rounded border border-[#555555] bg-[#1a1a1a] px-1 font-mono text-[10px]">
                  <span className="text-[10px]">↓</span>
                </kbd>
                <span className="ml-1">navigate</span>
              </span>
              <span className="flex items-center gap-1">
                <kbd className="inline-flex h-4 items-center rounded border border-[#555555] bg-[#1a1a1a] px-1 font-mono text-[10px]">
                  Enter
                </kbd>
                <span className="ml-1">select</span>
              </span>
            </div>
          </div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>

    {/* Dashboard Viewer Modal */}
    <Dialog open={!!viewingDashboard} onOpenChange={(open) => !open && closeDashboardViewer()}>
      <DialogPortal>
        <DialogOverlay className="bg-black/80" />
        <DialogPrimitive.Content
          className="fixed inset-4 z-50 flex flex-col rounded-xl border border-[#444444] bg-[#1a1a1a] shadow-2xl focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0"
        >
          <div className="flex items-center justify-between border-b border-[#444444] px-4 py-3">
            <div className="flex items-center gap-3">
              <LayoutDashboard className="h-5 w-5 text-brand-orange" />
              <span className="text-white font-medium">{viewingDashboard?.name}</span>
            </div>
            <button
              onClick={closeDashboardViewer}
              className="text-gray-400 hover:text-white p-1.5 rounded-lg hover:bg-[#333333] transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
          <div className="flex-1 overflow-hidden">
            {loadingDashboard ? (
              <div className="flex items-center justify-center h-full">
                <div className="w-8 h-8 border-2 border-brand-orange/30 border-t-brand-orange rounded-full animate-spin" />
              </div>
            ) : viewingDashboard?.html ? (
              <iframe
                srcDoc={viewingDashboard.html}
                className="w-full h-full border-0"
                sandbox="allow-scripts allow-same-origin"
                title={viewingDashboard.name}
              />
            ) : null}
          </div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
    </>
  )
}
