import React, { useState, useRef, useCallback } from 'react'
import { Eye, Code, RefreshCw, Maximize2, Check, Copy, Database, ChevronDown, FileDown, FileCode2, Loader2, Sparkles, Share2, Hand, X, MoreHorizontal } from 'lucide-react'
import { showToast } from '../utils/toast'
import { CodeHighlight } from './CodeHighlight'
import { NotebookQueryPanel } from './NotebookQueryPanel'
import { getElementContext, freeze, unfreeze } from 'react-grab/primitives'
type TabKey = 'preview' | 'code' | 'queries'

interface HtmlEditTimelineEntry {
  id: string
  sessionId: string
  toolName: string
  stage: 'start' | 'patch' | 'complete' | 'context'
  message: string
  timestamp: number
}

interface DashboardVersion {
  version_num: number
  created_at: string
}

interface DashboardPreviewPanelProps {
  processedHtmlContent: string
  iframeKey?: number
  generatedCode: string
  isRefreshing?: boolean
  onRefresh?: () => void
  availableVersions?: DashboardVersion[]
  selectedVersion?: number | null
  latestVersionNum?: number
  onVersionChange?: (version: number | null) => void
  isExportingPdf?: boolean
  onExportPdf?: () => void
  isExportingHtml?: boolean
  onExportHtml?: () => void
  onShare?: () => void
  onOpenFullscreen?: () => void
  onOpenQueryPanel?: () => void
  onClose?: () => void
  notebookId?: string
  onDebugWithAssistant?: (query: string, error: string, errorDetail?: any) => void
  injectedQuery?: string
  injectedQueryVersion?: number
  injectedConnectionId?: string
  isCodeLoading?: boolean
  codeLoadingMessage?: string
  iframeErrors?: unknown[]
  activeTab?: TabKey
  onActiveTabChange?: (tab: TabKey) => void
  iframeRef?: React.RefObject<HTMLIFrameElement | null>
  htmlEditTimeline?: HtmlEditTimelineEntry[]
  liveCodeOverride?: string | null
  isLiveStreamEnabled?: boolean
  onToggleLiveStream?: () => void
  showGenerationIndicator?: boolean
  generationIndicatorMessage?: string
  activeFiltersBar?: React.ReactNode
  filterSidebar?: React.ReactNode
  onIframeLoad?: () => void
  onElementGrabbed?: (htmlContent: string, elementInfo: Array<{ tagName: string; className: string; id: string; textContent: string }>) => void
}

export function DashboardPreviewPanel({
  processedHtmlContent,
  iframeKey = 0,
  generatedCode,
  isRefreshing,
  onRefresh,
  availableVersions = [],
  selectedVersion = null,
  latestVersionNum = 1,
  onVersionChange,
  isExportingPdf,
  onExportPdf,
  isExportingHtml,
  onExportHtml,
  onShare,
  onOpenFullscreen,
  onOpenQueryPanel,
  onClose,
  notebookId,
  onDebugWithAssistant,
  injectedQuery,
  injectedQueryVersion,
  injectedConnectionId,
  isCodeLoading = false,
  codeLoadingMessage,
  iframeErrors = [],
  activeTab: controlledActiveTab,
  onActiveTabChange,
  iframeRef,
  htmlEditTimeline = [],
  liveCodeOverride,
  isLiveStreamEnabled = true,
  onToggleLiveStream,
  showGenerationIndicator = false,
  generationIndicatorMessage = '',
  activeFiltersBar,
  filterSidebar,
  onIframeLoad,
  onElementGrabbed,
}: DashboardPreviewPanelProps) {
  const [internalActiveTab, setInternalActiveTab] = useState<TabKey>('preview')
  const [copied, setCopied] = useState(false)
  const [showVersionDropdown, setShowVersionDropdown] = useState(false)
  const [isGrabMode, setIsGrabMode] = useState(false)
  const [isSelecting, setIsSelecting] = useState(false)
  const [showMoreMenu, setShowMoreMenu] = useState(false)
  const highlightOverlayRef = useRef<HTMLDivElement | null>(null)
  const teardownGrabRef = useRef<(() => void) | null>(null)
  const codeForDisplay = liveCodeOverride ?? generatedCode

  // Use controlled tab if provided, otherwise use internal state
  const activeTab = controlledActiveTab !== undefined ? controlledActiveTab : internalActiveTab
  const setActiveTab = (tab: TabKey) => {
    if (onActiveTabChange) {
      onActiveTabChange(tab)
    } else {
      setInternalActiveTab(tab)
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(codeForDisplay || '')
      setCopied(true)
      showToast.success('Code copied to clipboard')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      showToast.error('Failed to copy code')
    }
  }

  const startElementGrab = useCallback(() => {
    // Toggle off if already grabbing
    if (isGrabMode || isSelecting) {
      teardownGrabRef.current?.()
      return
    }

    if (!iframeRef?.current?.contentDocument || !iframeRef?.current?.contentWindow) {
      showToast.error('Dashboard not ready')
      return
    }

    setIsGrabMode(true)
    setIsSelecting(true)

    const iframeDoc = iframeRef.current.contentDocument
    const iframeWin = iframeRef.current.contentWindow

    // Create highlight overlay inside iframe
    const highlightOverlay = iframeDoc.createElement('div')
    Object.assign(highlightOverlay.style, {
      position: 'fixed',
      pointerEvents: 'none',
      zIndex: '999999',
      border: '1px dashed #60a5fa',
      borderRadius: '2px',
      backgroundColor: 'rgba(96, 165, 250, 0.05)',
      transition: 'all 75ms ease-out',
      display: 'none',
      boxShadow: 'none',
    })
    iframeDoc.body.appendChild(highlightOverlay)
    highlightOverlayRef.current = highlightOverlay

    // Change cursor to grab
    iframeDoc.body.style.cursor = 'grab'

    const handleMouseMove = (event: MouseEvent) => {
      if (!highlightOverlay) return

      highlightOverlay.style.display = 'none'
      const target = iframeDoc.elementFromPoint(event.clientX, event.clientY)
      if (!target || target === highlightOverlay) return

      const { top, left, width, height } = target.getBoundingClientRect()
      Object.assign(highlightOverlay.style, {
        top: `${top}px`,
        left: `${left}px`,
        width: `${width}px`,
        height: `${height}px`,
        display: 'block',
      })
    }

    const handleOutsideClick = (event: MouseEvent) => {
      const iframe = iframeRef.current
      if (iframe) {
        const iframeRect = iframe.getBoundingClientRect()
        const clickedOutside =
          event.clientX < iframeRect.left ||
          event.clientX > iframeRect.right ||
          event.clientY < iframeRect.top ||
          event.clientY > iframeRect.bottom

        if (clickedOutside) {
          teardown()
        }
      }
    }

    const teardown = () => {
      if (iframeDoc) {
        iframeDoc.removeEventListener('mousemove', handleMouseMove as any)
        iframeDoc.removeEventListener('click', handleClick as any, true)
        iframeDoc.removeEventListener('keydown', handleEscape as any)
        iframeDoc.body.style.cursor = 'default'
      }
      document.removeEventListener('click', handleOutsideClick)
      if (highlightOverlay && highlightOverlay.parentNode) {
        highlightOverlay.remove()
      }
      highlightOverlayRef.current = null
      teardownGrabRef.current = null
      setIsSelecting(false)
      setIsGrabMode(false)
    }
    teardownGrabRef.current = teardown

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        teardown()
      }
    }

    const handleClick = async (event: MouseEvent) => {
      event.preventDefault()
      event.stopPropagation()

      highlightOverlay.style.display = 'none'
      const target = iframeDoc.elementFromPoint(event.clientX, event.clientY)

      if (!target || target === highlightOverlay) {
        teardown()
        return
      }

      // Change cursor to grabbing
      iframeDoc.body.style.cursor = 'grabbing'

      try {
        freeze()
        await getElementContext(target as HTMLElement)

        // Extract HTML context and element info
        const htmlContent = target.outerHTML
        const elementInfo = [{
          tagName: target.tagName,
          className: (target as HTMLElement).className || '',
          id: (target as HTMLElement).id || '',
          textContent: target.textContent?.substring(0, 100) || '',
        }]

        // Call the callback with grabbed data
        if (onElementGrabbed) {
          onElementGrabbed(htmlContent, elementInfo)
        }

        unfreeze()
      } catch (error) {
        console.error('Error grabbing element:', error)
        showToast.error('Failed to grab element')
        unfreeze()
      }

      teardown()
    }

    // Add event listeners
    iframeDoc.addEventListener('mousemove', handleMouseMove as any)
    iframeDoc.addEventListener('click', handleClick as any, true)
    iframeDoc.addEventListener('keydown', handleEscape as any)

    // Add outside click listener after a small delay to avoid catching the button click
    const timeoutId = setTimeout(() => {
      document.addEventListener('click', handleOutsideClick)
    }, 100)

    return () => {
      clearTimeout(timeoutId)
      document.removeEventListener('click', handleOutsideClick)
    }
  }, [iframeRef, onElementGrabbed, isGrabMode, isSelecting])

  const currentVersion = selectedVersion || latestVersionNum
  const isDefaultVersion = currentVersion === 1
  const isBlankCanvas = !processedHtmlContent || processedHtmlContent.trim().length < 200 || isDefaultVersion
  const hasPreview = !!processedHtmlContent && !isBlankCanvas
  const hasCode = !!codeForDisplay

  const versionInfo = selectedVersion
    ? `v${selectedVersion}`
    : `v${latestVersionNum}`

  const selectedVersionData = availableVersions.find(v => v.version_num === (selectedVersion || latestVersionNum))
  const versionTimeAgo = selectedVersionData ? (() => {
    const diff = Date.now() - new Date(selectedVersionData.created_at).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `${hours}h ago`
    return `${Math.floor(hours / 24)}d ago`
  })() : ''

  return (
    <div className="h-full flex flex-col bg-[#1a1a1a] border-l border-[#2a2a2a] relative">

      {/* === SINGLE-ROW TOOLBAR === */}
      <div className="flex items-center justify-between px-3 h-[42px] border-b border-[#2a2a2a] flex-shrink-0 relative z-10">
        {/* Left: Close + Tabs */}
        <div className="flex items-center gap-1">
          {onClose && (
            <>
              <button
                onClick={onClose}
                className="w-[26px] h-[26px] rounded-[5px] flex items-center justify-center text-gray-400 hover:text-white hover:bg-[#2a2a2a] transition-colors"
                title="Close preview (Esc)"
              >
                <X className="w-3.5 h-3.5" />
              </button>
              <div className="w-px h-[18px] bg-[#262626] mx-1.5" />
            </>
          )}
          <div className="flex gap-[2px]">
            <button
              onClick={() => setActiveTab('preview')}
              className={`px-2.5 py-[5px] rounded-[5px] text-xs font-medium transition-colors inline-flex items-center gap-1.5 ${
                activeTab === 'preview'
                  ? 'text-white bg-[#2a2a2a]'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <span className={activeTab === 'preview' ? 'text-brand-orange' : ''}>
                <Eye className="w-3 h-3" />
              </span>
              Preview
            </button>
            <button
              onClick={() => setActiveTab('code')}
              className={`px-2.5 py-[5px] rounded-[5px] text-xs font-medium transition-colors inline-flex items-center gap-1.5 ${
                activeTab === 'code'
                  ? 'text-white bg-[#2a2a2a]'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <Code className="w-3 h-3" />
              Code
              {liveCodeOverride && <Sparkles className="w-2.5 h-2.5 text-purple-300" />}
            </button>
            <button
              onClick={() => setActiveTab('queries')}
              className={`px-2.5 py-[5px] rounded-[5px] text-xs font-medium transition-colors inline-flex items-center gap-1.5 ${
                activeTab === 'queries'
                  ? 'text-white bg-[#2a2a2a]'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <Database className="w-3 h-3" />
              Queries
            </button>
          </div>
        </div>

        {/* Right: Version chip + Actions */}
        <div className="flex items-center gap-1.5">
          {/* Version chip */}
          {availableVersions.length > 0 && (
            <div className="relative">
              <button
                onClick={() => setShowVersionDropdown(!showVersionDropdown)}
                className="flex items-center gap-1.5 bg-[#232323] border border-[#404040] rounded-[5px] px-2.5 py-[3px] text-[11px] hover:bg-[#2a2a2a] transition-colors"
              >
                <span className="w-[5px] h-[5px] rounded-full bg-green-400 flex-shrink-0" />
                <span className="text-[#e5e5e5] font-medium">{versionInfo}</span>
                {versionTimeAgo && (
                  <span className="text-gray-500 font-mono text-[10px] hidden xl:inline">· {versionTimeAgo}</span>
                )}
                <ChevronDown className="w-2.5 h-2.5 text-gray-500" />
              </button>

              {showVersionDropdown && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowVersionDropdown(false)} />
                  <div className="absolute top-full mt-1 right-0 w-56 bg-[#1a1a1a] border border-[#404040] rounded-lg shadow-xl z-20 py-1 max-h-64 overflow-y-auto custom-scrollbar">
                    <button
                      onClick={() => { onVersionChange?.(null); setShowVersionDropdown(false) }}
                      className={`w-full text-left px-3 py-2 text-xs hover:bg-[#1f1f1f] transition-colors ${selectedVersion === null ? 'bg-[#1f1f1f] text-white' : 'text-gray-300'}`}
                    >
                      <div className="font-medium">Latest (v{latestVersionNum})</div>
                      <div className="text-[11px] text-gray-500 mt-0.5">Most recent version</div>
                    </button>
                    {availableVersions.map(v => (
                      <button
                        key={v.version_num}
                        onClick={() => { onVersionChange?.(v.version_num); setShowVersionDropdown(false) }}
                        className={`w-full text-left px-3 py-2 text-xs hover:bg-[#1f1f1f] transition-colors border-t border-[#1f1f1f] ${selectedVersion === v.version_num ? 'bg-[#1f1f1f] text-white' : 'text-gray-300'}`}
                      >
                        <div className="font-medium">v{v.version_num}</div>
                        <div className="text-[11px] text-gray-500 mt-0.5">
                          {new Date(v.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </div>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          <div className="w-px h-[18px] bg-[#262626] mx-0.5" />

          <button
            onClick={startElementGrab}
            disabled={!hasPreview && !isGrabMode && !isSelecting}
            className={`w-[26px] h-[26px] rounded-[5px] flex items-center justify-center transition-colors text-xs disabled:opacity-50 ${
              isGrabMode || isSelecting
                ? 'bg-green-500/20 text-green-400'
                : 'text-gray-400 hover:text-white hover:bg-[#2a2a2a]'
            }`}
            title={isGrabMode || isSelecting ? 'Click to cancel grab' : 'Grab element'}
          >
            <Hand className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={onRefresh}
            disabled={isRefreshing}
            className="w-[26px] h-[26px] rounded-[5px] flex items-center justify-center text-gray-400 hover:text-white hover:bg-[#2a2a2a] transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>

          {/* More menu (PDF, HTML, Fullscreen grouped) */}
          <div className="relative">
            <button
              onClick={() => setShowMoreMenu(!showMoreMenu)}
              className="w-[26px] h-[26px] rounded-[5px] flex items-center justify-center text-gray-400 hover:text-white hover:bg-[#2a2a2a] transition-colors"
              title="More actions"
            >
              <MoreHorizontal className="w-3.5 h-3.5" />
            </button>
            {showMoreMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowMoreMenu(false)} />
                <div className="absolute top-full mt-1 right-0 w-44 bg-[#1a1a1a] border border-[#404040] rounded-lg shadow-xl z-20 py-1">
                  {onExportPdf && (
                    <button
                      onClick={() => { onExportPdf?.(); setShowMoreMenu(false) }}
                      disabled={isExportingPdf}
                      className="w-full text-left px-3 py-2 text-xs text-gray-300 hover:bg-[#1f1f1f] transition-colors flex items-center gap-2 disabled:opacity-50"
                    >
                      {isExportingPdf ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
                      Export PDF
                    </button>
                  )}
                  <button
                    onClick={() => { onExportHtml?.(); setShowMoreMenu(false) }}
                    disabled={isExportingHtml}
                    className="w-full text-left px-3 py-2 text-xs text-gray-300 hover:bg-[#1f1f1f] transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    <FileCode2 className="w-3.5 h-3.5" />
                    Export HTML
                  </button>
                  <button
                    onClick={() => { onOpenFullscreen?.(); setShowMoreMenu(false) }}
                    className="w-full text-left px-3 py-2 text-xs text-gray-300 hover:bg-[#1f1f1f] transition-colors flex items-center gap-2"
                  >
                    <Maximize2 className="w-3.5 h-3.5" />
                    Fullscreen
                  </button>
                </div>
              </>
            )}
          </div>

          {onShare && (
            <button
              onClick={onShare}
              className="bg-brand-orange hover:bg-brand-orange/90 text-white border-none rounded-[5px] px-3 py-[5px] text-xs font-medium transition-colors flex items-center gap-1.5"
              title="Share dashboard"
            >
              <Share2 className="w-3 h-3" />
              Share
            </button>
          )}
        </div>
      </div>

      {/* === FILTER CHIP STRIP === */}
      {activeFiltersBar && (
        <div className="flex-shrink-0 border-b border-[#2a2a2a] relative z-10">
          {activeFiltersBar}
        </div>
      )}

      {/* === CONTENT AREA === */}
      <div className="flex-1 min-h-0 overflow-hidden relative z-0">
        {activeTab === 'preview' && (
          <div className="h-full bg-[#0a0a0a] flex min-h-0 flex-col">
            <div className="flex min-h-0 flex-1">
              {filterSidebar}
              <div className="flex-1 min-h-0 min-w-0 relative z-0">
                {hasPreview ? (
                  <div className="relative h-full">
                    <iframe
                      ref={iframeRef}
                      key={iframeKey}
                      srcDoc={processedHtmlContent}
                      onLoad={onIframeLoad}
                      className="w-full h-full border-0"
                      title="Dashboard Preview"
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
                    />
                    {iframeErrors.length > 0 && (
                      <div className="absolute top-3 right-3 px-2 py-1 bg-red-500/90 text-white text-xs rounded-md">
                        {iframeErrors.length} error{iframeErrors.length > 1 ? 's' : ''}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full relative overflow-hidden">
                    <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-purple-500/5 rounded-full blur-3xl animate-pulse" />
                    <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-brand-orange/5 rounded-full blur-3xl animate-pulse" style={{ animationDelay: '1s' }} />
                    <div className="text-center relative z-10 px-8">
                      {isBlankCanvas && !isCodeLoading ? (
                        <>
                          <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-purple-500/10 to-brand-orange/10 border border-purple-500/20 mb-6">
                            <Eye className="w-10 h-10 text-purple-400/40" />
                          </div>
                          <h3 className="text-lg font-semibold text-gray-400 mb-2">Preview Area</h3>
                          <p className="text-sm text-gray-600 max-w-sm mx-auto leading-relaxed">Your preview will appear here</p>
                        </>
                      ) : (
                        <>
                          <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-purple-500/10 to-brand-orange/10 border border-purple-500/20 mb-6">
                            <Eye className="w-10 h-10 text-purple-400/60" />
                          </div>
                          <h3 className="text-lg font-semibold text-gray-300 mb-2">
                            {isCodeLoading ? 'Creating your dashboard...' : 'Blank Canvas'}
                          </h3>
                          <p className="text-sm text-gray-500 max-w-sm mx-auto leading-relaxed">
                            {isCodeLoading
                              ? codeLoadingMessage || 'Your dashboard is being generated. This will appear here momentarily.'
                              : 'Your dashboard preview will appear here as soon as it\'s generated. Start chatting to bring your data to life.'}
                          </p>
                          {isCodeLoading && (
                            <div className="mt-6 flex items-center justify-center gap-3">
                              <div className="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '0s' }} />
                              <div className="w-2 h-2 bg-brand-orange rounded-full animate-bounce" style={{ animationDelay: '0.2s' }} />
                              <div className="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '0.4s' }} />
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'code' && (
          <div className="relative h-full bg-[#0a0a0a] flex flex-col">
            <div className="flex-shrink-0 px-4 py-3 border-b border-[#1f1f1f] flex items-center justify-between">
              <div>
                <p className="text-xs text-gray-400">Dashboard HTML</p>
                {liveCodeOverride && (
                  <p className="text-[11px] text-purple-300 flex items-center gap-1 mt-1">
                    <Sparkles className="w-3 h-3" /> Streaming live edits
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                {onToggleLiveStream && (
                  <button
                    onClick={onToggleLiveStream}
                    className={`px-2 py-1 rounded-md text-[11px] font-semibold transition-colors ${
                      isLiveStreamEnabled ? 'bg-purple-500/20 text-purple-200' : 'bg-gray-700 text-gray-300'
                    }`}
                  >
                    {isLiveStreamEnabled ? 'Live updates on' : 'Live updates off'}
                  </button>
                )}
                <button
                  onClick={handleCopy}
                  className="p-2 bg-[#1a1a1a] hover:bg-[#2a2a2a] text-white rounded-md transition-colors border border-[#404040]"
                  title="Copy code"
                >
                  {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {htmlEditTimeline.length > 0 && (
              <div className="px-4 py-3 border-b border-[#1f1f1f] bg-[#0f0f0f]">
                <div className="text-[11px] text-gray-400 mb-2 uppercase tracking-wide">Live HTML updates</div>
                <div className="max-h-32 overflow-auto custom-scrollbar space-y-1 text-xs text-gray-300">
                  {htmlEditTimeline.map(entry => (
                    <div key={entry.id} className="flex items-center justify-between gap-2">
                      <div className="flex-1 truncate">
                        <span className="text-[10px] text-gray-500 mr-2">{entry.stage}</span>
                        {entry.message}
                      </div>
                      <span className="text-[10px] text-gray-500 whitespace-nowrap">
                        {new Date(entry.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex-1 overflow-y-auto custom-scrollbar">
              {hasCode ? (
                <CodeHighlight
                  code={codeForDisplay || ''}
                  language="html"
                  customStyle={{ padding: '1.5rem', minHeight: '100%' }}
                />
              ) : (
                <div className="p-4 text-gray-400 text-sm font-mono">// No HTML code available</div>
              )}
            </div>
            {isCodeLoading && (
              <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
                <div className="flex items-center gap-3 px-4 py-2 rounded-lg bg-[#1a1a1a] border border-[#404040]">
                  <div className="w-4 h-4 border-2 border-gray-600 border-t-white rounded-full animate-spin" />
                  <span className="text-xs text-[#e5e5e5]">{codeLoadingMessage || 'Updating HTML...'}</span>
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'queries' && (
          <div className="h-full overflow-hidden">
            <NotebookQueryPanel
              notebookId={notebookId}
              onDebugWithAssistant={onDebugWithAssistant}
              injectedQuery={injectedQuery}
              injectedQueryVersion={injectedQueryVersion}
              injectedConnectionId={injectedConnectionId}
            />
          </div>
        )}
      </div>

      {/* === STATUS RAIL (persistent bottom bar) === */}
      <div className="flex items-center gap-2 px-3 py-[6px] border-t border-[#2a2a2a] flex-shrink-0 text-[11px] relative z-10">
        <span className={`w-[5px] h-[5px] rounded-full flex-shrink-0 ${showGenerationIndicator ? 'bg-purple-400 animate-pulse' : isCodeLoading ? 'bg-yellow-400 animate-pulse' : 'bg-green-400'}`} />
        <span className="text-gray-400">
          {showGenerationIndicator ? 'Updating...' : isCodeLoading ? 'Generating...' : 'Ready'}
        </span>
        {showGenerationIndicator && generationIndicatorMessage && (
          <span className="text-gray-500 font-mono text-[10px] truncate">{generationIndicatorMessage}</span>
        )}
        <div className="flex-1" />
        {onExportPdf && (
          <button
            onClick={onExportPdf}
            disabled={isExportingPdf}
            className="text-gray-400 hover:text-white transition-colors px-2 py-0.5 rounded disabled:opacity-50 inline-flex items-center gap-1"
            title="Export PDF"
          >
            {isExportingPdf ? <Loader2 className="w-3 h-3 animate-spin" /> : <FileDown className="w-3 h-3" />}
            PDF
          </button>
        )}
        <button
          onClick={onExportHtml}
          disabled={isExportingHtml}
          className="text-gray-400 hover:text-white transition-colors px-2 py-0.5 rounded disabled:opacity-50 inline-flex items-center gap-1"
          title="Export HTML"
        >
          <FileCode2 className="w-3 h-3" />
          HTML
        </button>
        <button
          onClick={onOpenFullscreen}
          className="text-gray-400 hover:text-white transition-colors px-2 py-0.5 rounded inline-flex items-center gap-1"
          title="Fullscreen"
        >
          <Maximize2 className="w-3 h-3" />
          Focus
        </button>
      </div>
    </div>
  )
}
