import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/dialog'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { ConfirmationModal } from '../ConfirmationModal'
import { LayoutDashboard, X, Search, ChevronDown, ChevronRight, RefreshCw, Download, FileDown, Pencil, UserX, Link, SquarePen } from 'lucide-react'
import {
  ApiService,
  type BatchFilterPreflightResponse,
  type DashboardFilterDefinition,
} from '../../services/api'
import type { DashboardsByFolder, ViewerDashboardDetail, DashboardListItem, FolderWithDashboards } from '../../types/folder'
import { rewriteDashboardHtmlForBackend, ensureBaseHref, getBackendUrlForHtmlProcessing, injectViewerConfig } from '../../utils/dashboardHtml'
import { useStore } from '../../stores/useStore'
import { isTauriApp, saveBlobToFile } from '../../lib/tauri-api'
import { showToast } from '../../utils/toast'
import { useAppConfig } from '../../hooks/useAppConfig'
import { DashboardFilterSidebar } from '../DashboardFilterSidebar'
import { FilterPreflightPanel } from '../FilterPreflightPanel'
import { ActiveFiltersBar } from '../ActiveFiltersBar'
import {
  buildPreflightQueriesWithFilters,
  countActiveFilterValues,
  getAllowedFilterKeys,
  parseStoredFilterValues,
} from '../../utils/dashboardFilters'
import { buildActiveFilterChips, removeActiveFilterChip } from '../../utils/filterDisplay'

interface SharedDashboardsSectionProps {
  onLoadingChange?: (loading: boolean) => void
  onError?: (error: string | null) => void
  deepLinkDashboardId?: string
}
const SHARED_PREFLIGHT_DEBUG_STORAGE_KEY = 'shared_dashboard_filter_preflight_debug'

interface DashboardThumbnailCardProps {
  dashboard: DashboardListItem
  onClick: () => void
  formatTimeAgo: (dateString: string | null) => string
  currentUserId: string | undefined
  onEditTitle: (dashboard: DashboardListItem) => void
  onUnshare: (dashboard: DashboardListItem) => void
  onOpenNotebook: (dashboard: DashboardListItem) => void
}

function DashboardThumbnailCard({ dashboard, onClick, formatTimeAgo, currentUserId, onEditTitle, onUnshare, onOpenNotebook }: DashboardThumbnailCardProps) {
  const [htmlContent, setHtmlContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)

  const truncateText = (text: string, maxLength: number = 45) => {
    if (text.length <= maxLength) return text
    return text.substring(0, maxLength) + '...'
  }

  const dashboardName = dashboard.notebook_name || 'Untitled Dashboard'

  const loadThumbnail = useCallback(async () => {
    try {
      setLoading(true)
      const content = await ApiService.getDashboardThumbnail(dashboard.id)

      // Process thumbnail HTML same as full dashboard
      const backendUrl = await getBackendUrlForHtmlProcessing()
      const rewritten = rewriteDashboardHtmlForBackend(content, backendUrl)
      const withViewer = injectViewerConfig(rewritten, dashboard.id)
      const withBase = ensureBaseHref(withViewer, backendUrl)
      setHtmlContent(withBase)
    } catch (err) {
      console.error('Failed to load dashboard thumbnail:', err)
    } finally {
      setLoading(false)
    }
  }, [dashboard.id])

  useEffect(() => {
    if (hasLoaded) return

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !hasLoaded) {
            setHasLoaded(true)
            loadThumbnail()
          }
        })
      },
      { threshold: 0.1, rootMargin: '100px' }
    )

    if (cardRef.current) {
      observer.observe(cardRef.current)
    }

    return () => observer.disconnect()
  }, [hasLoaded, loadThumbnail])

  return (
    <div
      ref={cardRef}
      onClick={onClick}
      className="group bg-[#1a1a1a] border border-gray-800 rounded-xl overflow-hidden cursor-pointer hover:border-brand-orange/50 transition-all hover:shadow-lg hover:shadow-brand-orange/5"
    >
      <div className="relative w-full h-40 bg-[#0d0d0d] overflow-hidden">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-8 h-8 border-2 border-brand-orange/30 border-t-brand-orange rounded-full animate-spin" />
          </div>
        ) : htmlContent ? (
          <div className="absolute inset-0 overflow-hidden">
            <iframe
              srcDoc={htmlContent}
              className="absolute top-0 left-0 w-[500%] h-[500%] origin-top-left scale-[0.2] pointer-events-none border-0"
              sandbox="allow-scripts allow-same-origin"
              title={`${dashboard.notebook_name || 'Dashboard'} preview`}
            />
          </div>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-brand-orange/10 to-brand-orange/5">
            <LayoutDashboard className="w-12 h-12 text-brand-orange/40" />
          </div>
        )}
        {dashboard.notebook_created_by === currentUserId && (
          <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3 z-10">
            <button
              onClick={(e) => { e.stopPropagation(); onEditTitle(dashboard) }}
              className="p-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
              title="Edit title"
            >
              <Pencil className="w-5 h-5 text-white" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onUnshare(dashboard) }}
              className="p-2 bg-white/10 hover:bg-red-500/50 rounded-lg transition-colors"
              title="Unshare"
            >
              <UserX className="w-5 h-5 text-white" />
            </button>
            {dashboard.notebook_id && (
              <button
                onClick={(e) => { e.stopPropagation(); onOpenNotebook(dashboard) }}
                className="p-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
                title="Go to notebook"
              >
                <SquarePen className="w-5 h-5 text-white" />
              </button>
            )}
          </div>
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-[#1a1a1a] via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>

      <div className="p-4">
        <h3 className="text-white font-medium text-sm group-hover:text-brand-orange transition-colors" title={dashboardName}>
          {truncateText(dashboardName)}
        </h3>
        <div className="flex items-center gap-2 mt-2 text-xs text-gray-500">
          <span className="px-1.5 py-0.5 bg-gray-800 rounded text-gray-400">v{dashboard.version || 1}</span>
          <span>•</span>
          <span>{formatTimeAgo(dashboard.shared_at)}</span>
        </div>
      </div>
    </div>
  )
}

interface FolderAccordionProps {
  folder: FolderWithDashboards
  isExpanded: boolean
  onToggle: () => void
  onDashboardClick: (id: string, dashboard: DashboardListItem) => void
  formatTimeAgo: (dateString: string | null) => string
  currentUserId: string | undefined
  onEditTitle: (dashboard: DashboardListItem) => void
  onUnshare: (dashboard: DashboardListItem) => void
  onOpenNotebook: (dashboard: DashboardListItem) => void
}

function FolderAccordion({ folder, isExpanded, onToggle, onDashboardClick, formatTimeAgo, currentUserId, onEditTitle, onUnshare, onOpenNotebook }: FolderAccordionProps) {
  return (
    <div className="mb-4">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 w-full text-left py-2 px-1 hover:bg-gray-800/30 rounded-lg transition-colors"
      >
        {isExpanded ? (
          <ChevronDown className="w-4 h-4 text-gray-400" />
        ) : (
          <ChevronRight className="w-4 h-4 text-gray-400" />
        )}
        <span className="text-sm font-medium text-white">{folder.folder_name}</span>
        <span className="text-xs text-gray-500">({folder.dashboards.length})</span>
      </button>

      {isExpanded && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-3 pl-6">
          {folder.dashboards.map((dashboard) => (
            <DashboardThumbnailCard
              key={dashboard.id}
              dashboard={dashboard}
              onClick={() => onDashboardClick(dashboard.id, dashboard)}
              formatTimeAgo={formatTimeAgo}
              currentUserId={currentUserId}
              onEditTitle={onEditTitle}
              onUnshare={onUnshare}
              onOpenNotebook={onOpenNotebook}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default function SharedDashboardsSection({ onLoadingChange, onError, deepLinkDashboardId }: SharedDashboardsSectionProps) {
  const navigate = useNavigate()
  const { features } = useAppConfig()
  const [dashboardData, setDashboardData] = useState<DashboardsByFolder | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedDashboard, setSelectedDashboard] = useState<ViewerDashboardDetail | null>(null)
  const [processedHtml, setProcessedHtml] = useState<string>('')
  const [dashboardFilters, setDashboardFilters] = useState<DashboardFilterDefinition[]>([])
  const [isDashboardFiltersLoaded, setIsDashboardFiltersLoaded] = useState(false)
  const [dashboardFilterValues, setDashboardFilterValues] = useState<Record<string, unknown>>({})
  const [isFilterPreflightLoading, setIsFilterPreflightLoading] = useState(false)
  const [filterPreflightResponse, setFilterPreflightResponse] = useState<BatchFilterPreflightResponse | null>(null)
  const [filterPreflightError, setFilterPreflightError] = useState<string | null>(null)
  const [showFilterPreflightDebug, setShowFilterPreflightDebug] = useState<boolean>(() => {
    if (typeof window === 'undefined') {
      return false
    }
    return window.localStorage.getItem(SHARED_PREFLIGHT_DEBUG_STORAGE_KEY) === '1'
  })
  const filterPreflightRequestSeqRef = useRef(0)
  const [loadingDashboard, setLoadingDashboard] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
  const [cacheStatus, setCacheStatus] = useState<{
    last_refreshed_at: string | null
    is_stale: boolean
  } | null>(null)
  const [cacheStatusError, setCacheStatusError] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [iframeKey, setIframeKey] = useState(0)
  const [isExportingPdf, setIsExportingPdf] = useState(false)
  const [isExportingHtml, setIsExportingHtml] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const currentUserId = useStore(state => state.user?.id)
  const [clickedDashboardInfo, setClickedDashboardInfo] = useState<DashboardListItem | null>(null)
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [editingTitleValue, setEditingTitleValue] = useState('')
  const [isSavingTitle, setIsSavingTitle] = useState(false)
  const [dashboardToEdit, setDashboardToEdit] = useState<DashboardListItem | null>(null)
  const [showUnshareConfirm, setShowUnshareConfirm] = useState(false)
  const [dashboardToUnshare, setDashboardToUnshare] = useState<DashboardListItem | null>(null)
  const [isUnsharing, setIsUnsharing] = useState(false)
  const filterStorageKey = useMemo(
    () => (selectedDashboard?.id ? `shared_dashboard_filters_${selectedDashboard.id}` : null),
    [selectedDashboard?.id],
  )

  const onLoadingChangeRef = useRef(onLoadingChange)
  const onErrorRef = useRef(onError)
  onLoadingChangeRef.current = onLoadingChange
  onErrorRef.current = onError

  useEffect(() => {
    const storedValues = parseStoredFilterValues(filterStorageKey, 'shared dashboard filter values')
    setDashboardFilterValues(storedValues)
  }, [filterStorageKey])

  useEffect(() => {
    if (!filterStorageKey || typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(filterStorageKey, JSON.stringify(dashboardFilterValues))
  }, [dashboardFilterValues, filterStorageKey])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(
      SHARED_PREFLIGHT_DEBUG_STORAGE_KEY,
      showFilterPreflightDebug ? '1' : '0',
    )
  }, [showFilterPreflightDebug])

  useEffect(() => {
    if (!isDashboardFiltersLoaded) {
      return
    }

    const allowedKeys = getAllowedFilterKeys(dashboardFilters)
    if (allowedKeys.size === 0) {
      setDashboardFilterValues({})
      return
    }

    setDashboardFilterValues((previous) => {
      const next: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(previous)) {
        if (allowedKeys.has(key)) {
          next[key] = value
        }
      }
      return next
    })
  }, [dashboardFilters, isDashboardFiltersLoaded])

  const activeFilterValueCount = useMemo(
    () => countActiveFilterValues(dashboardFilterValues),
    [dashboardFilterValues],
  )
  const activeFilterChips = useMemo(
    () => buildActiveFilterChips(dashboardFilters, dashboardFilterValues),
    [dashboardFilters, dashboardFilterValues],
  )

  const preflightQueriesWithFilters = useMemo(
    () => buildPreflightQueriesWithFilters(dashboardFilters, dashboardFilterValues),
    [dashboardFilters, dashboardFilterValues],
  )

  const postFiltersToIframe = useCallback(
    (values: Record<string, unknown>, reload: boolean) => {
      const iframeWindow = iframeRef.current?.contentWindow
      if (!iframeWindow) {
        return
      }
      iframeWindow.postMessage(
        {
          type: 'dashboard.filters.update.v1',
          dashboardId: selectedDashboard?.id ?? null,
          filterValues: values,
          filterDefinitions: dashboardFilters,
          reload,
        },
        '*',
      )
    },
    [dashboardFilters, selectedDashboard?.id],
  )

  const handleIframeLoad = useCallback(() => {
    postFiltersToIframe(dashboardFilterValues, false)
  }, [dashboardFilterValues, postFiltersToIframe])

  const skipInitialAutoApplyRef = useRef(false)
  useEffect(() => {
    skipInitialAutoApplyRef.current = false
  }, [selectedDashboard?.id])

  useEffect(() => {
    if (!selectedDashboard?.id) {
      return
    }
    if (!skipInitialAutoApplyRef.current) {
      skipInitialAutoApplyRef.current = true
      return
    }
    const timeoutId = window.setTimeout(() => {
      postFiltersToIframe(dashboardFilterValues, true)
    }, 300)
    return () => window.clearTimeout(timeoutId)
  }, [dashboardFilterValues, postFiltersToIframe, selectedDashboard?.id])

  useEffect(() => {
    if (!selectedDashboard?.id) {
      return
    }
    postFiltersToIframe(dashboardFilterValues, false)
  }, [dashboardFilterValues, dashboardFilters, postFiltersToIframe, selectedDashboard?.id])

  useEffect(() => {
    if (!showFilterPreflightDebug) {
      setIsFilterPreflightLoading(false)
      setFilterPreflightError(null)
      setFilterPreflightResponse(null)
      return
    }

    if (!selectedDashboard?.id || !isDashboardFiltersLoaded) {
      setIsFilterPreflightLoading(false)
      setFilterPreflightError(null)
      setFilterPreflightResponse(null)
      return
    }

    if (preflightQueriesWithFilters.length === 0) {
      setIsFilterPreflightLoading(false)
      setFilterPreflightError(null)
      setFilterPreflightResponse(null)
      return
    }

    const requestSeq = ++filterPreflightRequestSeqRef.current
    const timeoutId = window.setTimeout(async () => {
      setIsFilterPreflightLoading(true)
      setFilterPreflightError(null)
      try {
        const response = await ApiService.preflightViewerDashboardQueries(
          selectedDashboard.id,
          {
            queries_with_filters: preflightQueriesWithFilters,
            max_parallel: 5,
          },
        )
        if (requestSeq !== filterPreflightRequestSeqRef.current) {
          return
        }
        setFilterPreflightResponse(response)
      } catch (error) {
        if (requestSeq !== filterPreflightRequestSeqRef.current) {
          return
        }
        const message = error instanceof Error ? error.message : 'Failed to validate dashboard filters'
        setFilterPreflightError(message)
        setFilterPreflightResponse(null)
      } finally {
        if (requestSeq === filterPreflightRequestSeqRef.current) {
          setIsFilterPreflightLoading(false)
        }
      }
    }, 350)

    return () => window.clearTimeout(timeoutId)
  }, [isDashboardFiltersLoaded, preflightQueriesWithFilters, selectedDashboard?.id, showFilterPreflightDebug])

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) {
        return
      }

      const data = event.data as { type?: string } | null
      if (!data || typeof data !== 'object') {
        return
      }

      if (data.type === 'dashboard.filters.ready.v1') {
        postFiltersToIframe(dashboardFilterValues, false)
      }
    }

    window.addEventListener('message', handleMessage)
    return () => {
      window.removeEventListener('message', handleMessage)
    }
  }, [dashboardFilterValues, postFiltersToIframe])

  const fetchDashboards = useCallback(async () => {
    try {
      setLoading(true)
      onLoadingChangeRef.current?.(true)
      const response = await ApiService.getAllDashboards()
      setDashboardData(response)
      if (response?.folders) {
        setExpandedFolders(new Set(response.folders.map(f => f.folder_id)))
      }
    } catch (err) {
      console.error('Failed to fetch dashboards:', err)
      onErrorRef.current?.('Failed to load dashboards')
    } finally {
      setLoading(false)
      onLoadingChangeRef.current?.(false)
    }
  }, [])

  useEffect(() => {
    fetchDashboards()
  }, [fetchDashboards])

  const hasAutoOpenedRef = useRef(false)

  useEffect(() => {
    if (deepLinkDashboardId && dashboardData && !selectedDashboard && !hasAutoOpenedRef.current) {
      const allDashboards = dashboardData.folders.flatMap(folder => folder.dashboards)
      const dashboard = allDashboards.find(d => d.id === deepLinkDashboardId)
      if (dashboard) {
        hasAutoOpenedRef.current = true
        handleDashboardClick(deepLinkDashboardId, dashboard)
      } else {
        hasAutoOpenedRef.current = true
        showToast.error('Dashboard not found or you do not have access')
        navigate('/', { replace: true })
      }
    }
    if (!deepLinkDashboardId) {
      hasAutoOpenedRef.current = false
    }
  }, [deepLinkDashboardId, dashboardData, selectedDashboard, navigate])

  const allDashboards = useMemo(() => {
    if (!dashboardData?.folders) return []
    return dashboardData.folders.flatMap(folder =>
      folder.dashboards.map(db => ({ ...db, folderName: folder.folder_name }))
    )
  }, [dashboardData])

  const filteredDashboards = useMemo(() => {
    if (!searchQuery.trim()) return []
    const query = searchQuery.toLowerCase()
    return allDashboards.filter(db =>
      (db.notebook_name || '').toLowerCase().includes(query)
    )
  }, [allDashboards, searchQuery])

  const toggleFolder = (folderId: string) => {
    setExpandedFolders(prev => {
      const next = new Set(prev)
      if (next.has(folderId)) {
        next.delete(folderId)
      } else {
        next.add(folderId)
      }
      return next
    })
  }

  const formatTimeAgo = (dateString: string | null): string => {
    if (!dateString) return 'Unknown'
    const date = new Date(dateString)
    const now = new Date()
    const diffInMs = now.getTime() - date.getTime()
    const diffInMinutes = Math.floor(diffInMs / (1000 * 60))
    const diffInHours = Math.floor(diffInMs / (1000 * 60 * 60))
    const diffInDays = Math.floor(diffInMs / (1000 * 60 * 60 * 24))

    if (diffInMinutes < 1) return 'just now'
    if (diffInMinutes < 60) return `${diffInMinutes}m ago`
    if (diffInHours < 24) return `${diffInHours}h ago`
    if (diffInDays < 30) return `${diffInDays}d ago`
    return date.toLocaleDateString()
  }

  const handleDashboardClick = async (dashboardId: string, dashboardInfo: DashboardListItem) => {
    try {
      navigate(`/dashboard/${dashboardId}`, { replace: true })
      setLoadingDashboard(true)
      setCacheStatus(null)
      setCacheStatusError(false)
      filterPreflightRequestSeqRef.current += 1
      setIsFilterPreflightLoading(false)
      setFilterPreflightError(null)
      setFilterPreflightResponse(null)
      setClickedDashboardInfo(dashboardInfo)
      setIsDashboardFiltersLoaded(false)

      const [detail, filterConfig] = await Promise.all([
        ApiService.getViewerDashboard(dashboardId),
        ApiService.getViewerDashboardFilters(dashboardId).catch(() => ({ filters: [] })),
      ])

      const storedValues = parseStoredFilterValues(
        `shared_dashboard_filters_${dashboardId}`,
        'shared dashboard filter values',
      )
      setDashboardFilterValues(storedValues)
      setDashboardFilters(filterConfig.filters || [])
      setIsDashboardFiltersLoaded(true)
      setSelectedDashboard(detail)

      // Process HTML same as ChatPreview: rewrite URLs, inject auth, add base href
      const backendUrl = await getBackendUrlForHtmlProcessing()
      const rewritten = rewriteDashboardHtmlForBackend(detail.html_content, backendUrl)
      const withViewer = injectViewerConfig(
        rewritten,
        dashboardId,
        '/api/viewer',
        'same-origin',
        null,
        storedValues,
        filterConfig.filters || [],
      )
      const withBase = ensureBaseHref(withViewer, backendUrl)
      setProcessedHtml(withBase)
      fetchCacheStatus(dashboardId)
    } catch (err) {
      console.error('Failed to load dashboard:', err)
      setDashboardFilters([])
      setIsDashboardFiltersLoaded(true)
      setFilterPreflightResponse(null)
      onError?.('Failed to load dashboard content')
    } finally {
      setLoadingDashboard(false)
    }
  }

  const closeDashboard = () => {
    navigate('/', { replace: true })
    filterPreflightRequestSeqRef.current += 1
    setSelectedDashboard(null)
    setProcessedHtml('')
    setDashboardFilters([])
    setIsDashboardFiltersLoaded(false)
    setDashboardFilterValues({})
    setIsFilterPreflightLoading(false)
    setFilterPreflightError(null)
    setFilterPreflightResponse(null)
    setCacheStatus(null)
    setCacheStatusError(false)
    setIframeKey(0)
    setClickedDashboardInfo(null)
    setIsEditingTitle(false)
    setDashboardToEdit(null)
  }

  const handleEditTitleClick = (dashboard: DashboardListItem) => {
    setDashboardToEdit(dashboard)
    setEditingTitleValue(dashboard.notebook_name || '')
    setIsEditingTitle(true)
  }

  const handleSaveTitle = async () => {
    const editingDashboard = dashboardToEdit
    const notebookId = editingDashboard?.notebook_id || selectedDashboard?.notebook_id
    if (!notebookId) {
      showToast.error('Missing notebook information')
      return
    }
    if (!editingDashboard) {
      showToast.error('Missing dashboard information')
      return
    }
    if (!editingTitleValue.trim()) return
    try {
      setIsSavingTitle(true)
      await ApiService.renameNotebook(notebookId, editingTitleValue.trim())
      setDashboardData(prev => {
        if (!prev) return prev
        return {
          ...prev,
          folders: prev.folders.map(folder => ({
            ...folder,
            dashboards: folder.dashboards.map(db =>
              db.id === editingDashboard.id ? { ...db, notebook_name: editingTitleValue.trim() } : db
            )
          }))
        }
      })
      setClickedDashboardInfo(prev => prev?.id === editingDashboard.id ? { ...prev, notebook_name: editingTitleValue.trim() } : prev)
      setIsEditingTitle(false)
      setDashboardToEdit(null)
      showToast.success('Title updated')
    } catch {
      showToast.error('Failed to update title')
    } finally {
      setIsSavingTitle(false)
    }
  }

  const handleUnshareClick = (dashboard: DashboardListItem) => {
    setDashboardToUnshare(dashboard)
    setShowUnshareConfirm(true)
  }

  const handleConfirmUnshare = async () => {
    if (!dashboardToUnshare || !dashboardToUnshare.folder_id) {
      showToast.error('Missing folder information')
      return
    }
    try {
      setIsUnsharing(true)
      await ApiService.unshareDashboardFromFolder(dashboardToUnshare.folder_id, dashboardToUnshare.id)
      if (selectedDashboard?.id === dashboardToUnshare.id) {
        closeDashboard()
      }
      await fetchDashboards()
      showToast.success('Dashboard unshared')
    } catch {
      showToast.error('Failed to unshare dashboard')
    } finally {
      setIsUnsharing(false)
      setShowUnshareConfirm(false)
      setDashboardToUnshare(null)
    }
  }

  const handleCopyDashboardLink = async () => {
    if (!selectedDashboard) return
    try {
      const url = `${window.location.origin}/dashboard/${selectedDashboard.id}`
      await navigator.clipboard.writeText(url)
      showToast.success('Dashboard link copied to clipboard')
    } catch (error) {
      console.error('Failed to copy link:', error)
      showToast.error('Failed to copy link')
    }
  }

  const handleOpenNotebook = () => {
    if (!selectedDashboard?.notebook_id) return
    navigate(`/notebook/${selectedDashboard.notebook_id}`)
  }

  const handleOpenNotebookFromCard = (dashboard: DashboardListItem) => {
    if (!dashboard.notebook_id) return
    navigate(`/notebook/${dashboard.notebook_id}`)
  }

  const fetchCacheStatus = useCallback(async (dashboardId: string) => {
    try {
      setCacheStatusError(false)
      const status = await ApiService.getDashboardCacheStatus(dashboardId)
      setCacheStatus({
        last_refreshed_at: status.last_refreshed_at,
        is_stale: status.is_stale,
      })
    } catch (err) {
      console.error('Failed to fetch cache status:', err)
      setCacheStatus(null)
      setCacheStatusError(true)
    }
  }, [])

  const handleRefreshCache = async () => {
    if (!selectedDashboard || isRefreshing) return

    try {
      setIsRefreshing(true)
      await ApiService.refreshDashboardCache(selectedDashboard.id)

      const detail = await ApiService.getViewerDashboard(selectedDashboard.id)
      setSelectedDashboard(detail)

      // Process HTML same as ChatPreview
      const backendUrl = await getBackendUrlForHtmlProcessing()
      const rewritten = rewriteDashboardHtmlForBackend(detail.html_content, backendUrl)
      const withViewer = injectViewerConfig(
        rewritten,
        selectedDashboard.id,
        '/api/viewer',
        'same-origin',
        null,
        dashboardFilterValues,
        dashboardFilters,
      )
      const withBase = ensureBaseHref(withViewer, backendUrl)
      setProcessedHtml(withBase)

      await fetchCacheStatus(selectedDashboard.id)

      setIframeKey(prev => prev + 1)
    } catch (err) {
      console.error('Failed to refresh dashboard:', err)
    } finally {
      setIsRefreshing(false)
    }
  }

  const handleDownloadHtml = async () => {
    if (!selectedDashboard || !iframeRef.current) {
      showToast.error('Dashboard not ready')
      return
    }

    setIsExportingHtml(true)
    try {
      const iframe = iframeRef.current
      const iframeDoc = iframe.contentDocument || iframe.contentWindow?.document

      if (!iframeDoc) {
        throw new Error('Cannot access iframe content')
      }

      const clonedDoc = iframeDoc.documentElement.cloneNode(true) as HTMLElement
      const scripts = clonedDoc.querySelectorAll('script')
      scripts.forEach(script => script.remove())
      const staticHtml = '<!DOCTYPE html>\n' + clonedDoc.outerHTML

      const dashboardName = selectedDashboard.notebook_name || 'dashboard'
      const sanitizedName = dashboardName.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase()
      const fileName = `${sanitizedName}_v${selectedDashboard.version || 1}.html`

      const blob = new Blob([staticHtml], { type: 'text/html' })

      if (isTauriApp()) {
        const filePath = await saveBlobToFile(blob, fileName)
        useStore.getState().addDownload({
          fileName,
          fileType: 'html',
          filePath,
          status: 'success',
        })
      } else {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = fileName
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)

        useStore.getState().addDownload({
          fileName,
          fileType: 'html',
          status: 'success',
        })
      }
    } catch (error) {
      console.error('Failed to download HTML:', error)
      showToast.error('Failed to download HTML')
    } finally {
      setIsExportingHtml(false)
    }
  }

  const handleDownloadPdf = async () => {
    if (!selectedDashboard?.notebook_id) {
      showToast.error('PDF export is not available for this dashboard')
      return
    }

    setIsExportingPdf(true)
    try {
      const dashboardName = selectedDashboard.notebook_name || 'dashboard'
      const sanitizedName = dashboardName.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase()
      const now = new Date()
      const timestamp = now.toISOString().slice(0, 10)
      const fileName = `${sanitizedName}_v${selectedDashboard.version || 1}_${timestamp}.pdf`

      const blob = await ApiService.exportNotebookPdf(selectedDashboard.notebook_id, selectedDashboard.version)

      if (isTauriApp()) {
        const filePath = await saveBlobToFile(blob, fileName)
        useStore.getState().addDownload({ fileName, fileType: 'pdf', filePath, status: 'success' })
      } else {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = fileName
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
        useStore.getState().addDownload({ fileName, fileType: 'pdf', status: 'success' })
      }
    } catch (error) {
      console.error('Failed to generate PDF:', error)
      showToast.error(error instanceof Error ? error.message : 'Failed to generate PDF')
    } finally {
      setIsExportingPdf(false)
    }
  }

  if (loading) {
    return (
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Shared Dashboards</h2>
        </div>
        <div className="space-y-4">
          {[1, 2].map((i) => (
            <div key={i} className="animate-pulse">
              <div className="h-6 w-32 bg-gray-800/50 rounded mb-3" />
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 pl-6">
                {[1, 2, 3].map((j) => (
                  <div key={j} className="bg-[#1a1a1a] rounded-xl overflow-hidden">
                    <div className="h-40 bg-gray-800/30" />
                    <div className="p-4 space-y-2">
                      <div className="h-4 bg-gray-800/50 rounded w-3/4" />
                      <div className="h-3 bg-gray-800/30 rounded w-1/2" />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (!dashboardData || allDashboards.length === 0) {
    return null
  }

  const isSearching = searchQuery.trim().length > 0

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-white">Shared Dashboards</h2>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500" />
          <input
            type="text"
            placeholder="Search dashboards..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setSearchQuery('')
              }
            }}
            className="w-56 pl-8 pr-3 py-1.5 bg-transparent border border-gray-700/50 hover:border-gray-600 focus:border-brand-orange/50 focus:bg-[#1a1a1a] rounded-md text-sm text-white placeholder-gray-500 focus:outline-none transition-colors"
          />
        </div>
      </div>

      {isSearching ? (
        filteredDashboards.length === 0 ? (
          <div className="text-center py-8 text-gray-500 text-sm">
            No dashboards found matching "{searchQuery}"
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredDashboards.map((dashboard) => (
              <DashboardThumbnailCard
                key={dashboard.id}
                dashboard={dashboard}
                onClick={() => handleDashboardClick(dashboard.id, dashboard)}
                formatTimeAgo={formatTimeAgo}
                currentUserId={currentUserId}
                onEditTitle={handleEditTitleClick}
                onUnshare={handleUnshareClick}
                onOpenNotebook={handleOpenNotebookFromCard}
              />
            ))}
          </div>
        )
      ) : (
        dashboardData.folders.map((folder) => (
          <FolderAccordion
            key={folder.folder_id}
            folder={folder}
            isExpanded={expandedFolders.has(folder.folder_id)}
            onToggle={() => toggleFolder(folder.folder_id)}
            onDashboardClick={handleDashboardClick}
            formatTimeAgo={formatTimeAgo}
            currentUserId={currentUserId}
            onEditTitle={handleEditTitleClick}
            onUnshare={handleUnshareClick}
            onOpenNotebook={handleOpenNotebookFromCard}
          />
        ))
      )}

      {loadingDashboard && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="flex items-center gap-3 bg-[#1a1a1a] px-6 py-4 rounded-lg">
            <div className="w-5 h-5 border-2 border-brand-orange border-t-transparent rounded-full animate-spin" />
            <span className="text-white">Loading dashboard...</span>
          </div>
        </div>
      )}

      <Dialog open={!!selectedDashboard} onOpenChange={(open) => !open && closeDashboard()}>
        <DialogContent className="max-w-[99vw] w-[99vw] h-[96vh] border-[#1f2430] p-0 bg-[#0f1217] overflow-hidden [&>button]:hidden">
          <div className="relative w-full h-full flex flex-col">
            <div className="flex items-center justify-between px-4 py-2 bg-[#1a1a1a] border-b border-gray-800">
              <div className="flex items-center gap-3">
                {cacheStatusError ? (
                  <span className="text-xs text-gray-500">Cache status unavailable</span>
                ) : cacheStatus ? (
                  cacheStatus.last_refreshed_at ? (
                    <span className="text-xs text-gray-400">
                      Last refreshed: {new Date(cacheStatus.last_refreshed_at + 'Z').toLocaleString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                      {cacheStatus.is_stale && (
                        <span className="ml-2 text-yellow-500">(stale)</span>
                      )}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">Not yet cached</span>
                  )
                ) : (
                  <span className="text-xs text-gray-500">Loading cache status...</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleCopyDashboardLink}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors"
                  title="Copy dashboard link"
                >
                  <Link className="w-4 h-4" />
                  <span>Copy Link</span>
                </button>
                <button
                  onClick={handleDownloadHtml}
                  disabled={isExportingHtml}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Download HTML"
                >
                  <FileDown className={`w-4 h-4 ${isExportingHtml ? 'animate-pulse' : ''}`} />
                  <span>HTML</span>
                </button>
                {features.external_sharing_enabled && (
                  <button
                    onClick={handleDownloadPdf}
                    disabled={isExportingPdf}
                    className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    title="Download PDF"
                  >
                    <Download className={`w-4 h-4 ${isExportingPdf ? 'animate-pulse' : ''}`} />
                    <span>PDF</span>
                  </button>
                )}
                <button
                  onClick={handleRefreshCache}
                  disabled={isRefreshing}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Refresh dashboard data"
                >
                  <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
                  <span>{isRefreshing ? 'Refreshing...' : 'Refresh'}</span>
                </button>
                {clickedDashboardInfo && clickedDashboardInfo.notebook_created_by === currentUserId && (
                  <>
                    <button
                      onClick={() => handleEditTitleClick(clickedDashboardInfo)}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors"
                    >
                      <Pencil className="w-4 h-4" />
                      <span>Edit Title</span>
                    </button>
                    <button
                      onClick={() => handleUnshareClick(clickedDashboardInfo)}
                      className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-red-500/20 text-white border border-[#404040] rounded-lg transition-colors"
                    >
                      <UserX className="w-4 h-4" />
                      <span>Unshare</span>
                    </button>
                    {selectedDashboard?.notebook_id && (
                      <button
                        onClick={handleOpenNotebook}
                        className="flex items-center gap-2 px-3 py-1.5 text-xs bg-transparent hover:bg-[#2a2a2a] text-white border border-[#404040] rounded-lg transition-colors"
                        title="Open in Notebook"
                      >
                        <SquarePen className="w-4 h-4" />
                        <span>Open Notebook</span>
                      </button>
                    )}
                  </>
                )}
                <button
                  onClick={closeDashboard}
                  className="p-1.5 hover:bg-[#2a2a2a] rounded-lg transition-colors text-gray-400 hover:text-white"
                  title="Close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="flex-1 min-h-0 flex flex-col bg-[#0b0f15]">
              <ActiveFiltersBar
                chips={activeFilterChips}
                onRemoveChip={(chip) =>
                  setDashboardFilterValues((previous) =>
                    removeActiveFilterChip(previous, chip),
                  )
                }
                onClearAll={() => setDashboardFilterValues({})}
              />

              <div className="flex min-h-0 flex-1 bg-[#0b0f15]">
                <DashboardFilterSidebar
                  filters={dashboardFilters}
                  values={dashboardFilterValues}
                  onChange={setDashboardFilterValues}
                  storageKey={selectedDashboard ? `shared_dashboard_filter_sidebar_${selectedDashboard.id}` : 'shared_dashboard_filter_sidebar'}
                  actions={
                    <button
                      type="button"
                      onClick={() => setShowFilterPreflightDebug((prev) => !prev)}
                      className="h-7 rounded-md border border-[#39414c] bg-[#13171c] px-2.5 text-[10px] uppercase tracking-[0.08em] text-gray-400 transition-colors hover:border-brand-orange/50 hover:text-brand-orange"
                    >
                      {showFilterPreflightDebug ? 'Hide Debug' : 'Debug Preflight'}
                    </button>
                  }
                  diagnosticsPanel={showFilterPreflightDebug ? (
                    <FilterPreflightPanel
                      loading={isFilterPreflightLoading}
                      response={filterPreflightResponse}
                      error={filterPreflightError}
                      activeFilterCount={activeFilterValueCount}
                    />
                  ) : null}
                />

                <div className="flex-1 relative min-h-0 min-w-0">
                  {selectedDashboard && processedHtml && (
                    <iframe
                      ref={iframeRef}
                      key={iframeKey}
                      srcDoc={processedHtml}
                      onLoad={handleIframeLoad}
                      className="w-full h-full border-0"
                      title="Dashboard Preview"
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
                    />
                  )}
                </div>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={isEditingTitle} onOpenChange={(open) => {
        if (!open && isSavingTitle) return
        if (!open) { setIsEditingTitle(false); setDashboardToEdit(null) }
      }}>
        <DialogContent className="max-w-md bg-[#2a2a2a] border-[#444444]">
          <DialogHeader>
            <DialogTitle className="text-white">Edit Dashboard Title</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div>
              <label htmlFor="dashboard-title" className="block text-sm font-medium text-white mb-2">
                Dashboard Title
              </label>
              <Input
                id="dashboard-title"
                value={editingTitleValue}
                onChange={(e) => setEditingTitleValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveTitle()
                }}
                placeholder="Enter dashboard title"
                className="bg-[#1a1a1a] border-[#555555] text-white focus:border-brand-orange focus:ring-1 focus:ring-brand-orange/50"
                autoFocus
              />
            </div>

            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => { setIsEditingTitle(false); setDashboardToEdit(null) }}
                disabled={isSavingTitle}
                className="border-[#555555] text-white hover:bg-[#3a3a3a]"
              >
                Cancel
              </Button>
              <Button
                variant="brand-primary"
                onClick={handleSaveTitle}
                disabled={isSavingTitle || !editingTitleValue.trim()}
              >
                {isSavingTitle ? 'Saving...' : 'Save'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmationModal
        isOpen={showUnshareConfirm}
        onClose={() => { setShowUnshareConfirm(false); setDashboardToUnshare(null) }}
        onConfirm={handleConfirmUnshare}
        title="Unshare Dashboard"
        message={<>Are you sure you want to unshare "{dashboardToUnshare?.notebook_name}"? Others will no longer be able to view it.</>}
        confirmText="Unshare"
        type="danger"
        loading={isUnsharing}
      />
    </div>
  )
}
