import React, { useState, useRef, useEffect, useCallback } from 'react'

interface ResizableSplitPanelProps {
  leftPanel: React.ReactNode
  rightPanel: React.ReactNode
  defaultLeftWidth?: number // percentage (0-100)
  minLeftWidth?: number // percentage
  maxLeftWidth?: number // percentage
  isRightPanelOpen?: boolean
}

interface ResizableVerticalPanelProps {
  topPanel: React.ReactNode
  bottomPanel: React.ReactNode
  defaultTopHeight?: number // percentage (0-100)
  minTopHeight?: number // percentage
  maxTopHeight?: number // percentage
}

export function ResizableSplitPanel({
  leftPanel,
  rightPanel,
  defaultLeftWidth = 40,
  minLeftWidth = 25,
  maxLeftWidth = 60,
  isRightPanelOpen = true,
}: ResizableSplitPanelProps) {
  const clampLeftWidth = useCallback(
    (width: number) => Math.min(Math.max(width, minLeftWidth), maxLeftWidth),
    [minLeftWidth, maxLeftWidth]
  )

  const [leftWidth, setLeftWidth] = useState(() => clampLeftWidth(defaultLeftWidth))
  const isDraggingRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const leftPanelRef = useRef<HTMLDivElement>(null)
  const currentWidthRef = useRef(defaultLeftWidth)

  useEffect(() => {
    setLeftWidth((w) => clampLeftWidth(w))
  }, [clampLeftWidth])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDraggingRef.current = true
    if (leftPanelRef.current) leftPanelRef.current.style.transition = 'none'
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!isDraggingRef.current || !containerRef.current) return

      const containerRect = containerRef.current.getBoundingClientRect()
      const mouseX = e.clientX - containerRect.left
      const clamped = clampLeftWidth((mouseX / containerRect.width) * 100)

      currentWidthRef.current = clamped
      if (leftPanelRef.current) leftPanelRef.current.style.flexBasis = `${clamped}%`
    },
    [clampLeftWidth]
  )

  const handleMouseUp = useCallback(() => {
    if (!isDraggingRef.current) return
    isDraggingRef.current = false
    if (leftPanelRef.current) leftPanelRef.current.style.transition = ''
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    setLeftWidth(currentWidthRef.current)
  }, [])

  useEffect(() => {
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [handleMouseMove, handleMouseUp])

  return (
    <div ref={containerRef} className="resizable-split-panel flex h-full w-full min-w-0 relative">
      {/* Left Panel */}
      <div
        ref={leftPanelRef}
        style={{
          flexGrow: isRightPanelOpen ? 0 : 1,
          flexShrink: isRightPanelOpen ? 0 : 1,
          flexBasis: isRightPanelOpen ? `${leftWidth}%` : '100%',
          maxWidth: isRightPanelOpen ? `${maxLeftWidth}%` : '100%',
          minWidth: isRightPanelOpen ? `${minLeftWidth}%` : 0,
          transition: 'flex-basis var(--split-panel-duration) var(--split-panel-easing), max-width var(--split-panel-duration) var(--split-panel-easing), min-width var(--split-panel-duration) var(--split-panel-easing)',
        }}
        className="flex-shrink-0 h-full overflow-hidden"
      >
        {leftPanel}
      </div>

      {/* Resizable Divider */}
      <div
        onMouseDown={handleMouseDown}
        aria-label="Resize preview"
        role="separator"
        style={{
          flexBasis: isRightPanelOpen ? 4 : 0,
          width: isRightPanelOpen ? 4 : 0,
          opacity: isRightPanelOpen ? 1 : 0,
          pointerEvents: isRightPanelOpen ? 'auto' : 'none',
          transition: 'flex-basis var(--split-panel-duration) var(--split-panel-easing), width var(--split-panel-duration) var(--split-panel-easing), opacity 160ms ease',
        }}
        className="bg-[#2a2a2a] hover:bg-[#404040] active:bg-[#4a9eff] cursor-col-resize flex-shrink-0 relative group overflow-hidden transition-colors"
      >
        <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-1 group-hover:w-1 transition-all">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="w-0.5 h-1 bg-gray-500 rounded-full"></div>
            <div className="w-0.5 h-1 bg-gray-500 rounded-full"></div>
            <div className="w-0.5 h-1 bg-gray-500 rounded-full"></div>
          </div>
        </div>
      </div>

      {/* Right Panel */}
      <div
        className="h-full overflow-hidden"
        style={{
          flexGrow: isRightPanelOpen ? 1 : 0,
          flexShrink: 1,
          flexBasis: 0,
          minWidth: 0,
          opacity: isRightPanelOpen ? 1 : 0,
          pointerEvents: isRightPanelOpen ? 'auto' : 'none',
          transition: 'opacity 180ms ease',
        }}
      >
        {rightPanel}
      </div>
    </div>
  )
}

export function ResizableVerticalPanel({
  topPanel,
  bottomPanel,
  defaultTopHeight = 40,
  minTopHeight = 20,
  maxTopHeight = 70,
}: ResizableVerticalPanelProps) {
  const [topHeight, setTopHeight] = useState(defaultTopHeight)
  const isDraggingRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const topPanelRef = useRef<HTMLDivElement>(null)
  const currentHeightRef = useRef(defaultTopHeight)

  useEffect(() => {
    setTopHeight(defaultTopHeight)
    currentHeightRef.current = defaultTopHeight
  }, [defaultTopHeight])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDraggingRef.current = true
    if (topPanelRef.current) topPanelRef.current.style.transition = 'none'
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!isDraggingRef.current || !containerRef.current) return

      const containerRect = containerRef.current.getBoundingClientRect()
      const mouseY = e.clientY - containerRect.top
      const clamped = Math.min(Math.max((mouseY / containerRect.height) * 100, minTopHeight), maxTopHeight)

      currentHeightRef.current = clamped
      if (topPanelRef.current) topPanelRef.current.style.height = `${clamped}%`
    },
    [minTopHeight, maxTopHeight]
  )

  const handleMouseUp = useCallback(() => {
    if (!isDraggingRef.current) return
    isDraggingRef.current = false
    if (topPanelRef.current) topPanelRef.current.style.transition = ''
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    setTopHeight(currentHeightRef.current)
  }, [])

  useEffect(() => {
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [handleMouseMove, handleMouseUp])

  return (
    <div ref={containerRef} className="flex flex-col h-full w-full relative">
      <div
        ref={topPanelRef}
        style={{ height: `${topHeight}%` }}
        className="flex-shrink-0 w-full overflow-hidden"
      >
        {topPanel}
      </div>

      <div
        onMouseDown={handleMouseDown}
        className="h-1 bg-[#232323] hover:bg-[#404040] active:bg-[#4a9eff] cursor-row-resize flex-shrink-0 relative group transition-colors"
      >
        <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-1 group-hover:h-1 transition-all">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="w-1 h-0.5 bg-gray-500 rounded-full"></div>
            <div className="w-1 h-0.5 bg-gray-500 rounded-full"></div>
            <div className="w-1 h-0.5 bg-gray-500 rounded-full"></div>
          </div>
        </div>
      </div>

      <div className="flex-1 w-full overflow-hidden">
        {bottomPanel}
      </div>
    </div>
  )
}
