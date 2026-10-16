import * as React from "react"

interface TooltipProps {
  children: React.ReactElement
  content: string
  side?: "top" | "bottom" | "left" | "right"
  align?: "start" | "center" | "end"
  delayDuration?: number
}

export function Tooltip({ children, content, side = "top", align = "center", delayDuration = 200 }: TooltipProps) {
  const [isVisible, setIsVisible] = React.useState(false)
  const [isMounted, setIsMounted] = React.useState(false)
  const timeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleMouseEnter = () => {
    timeoutRef.current = setTimeout(() => {
      setIsMounted(true)
      // Small delay for animation
      requestAnimationFrame(() => {
        setIsVisible(true)
      })
    }, delayDuration)
  }

  const handleMouseLeave = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
    }
    setIsVisible(false)
    // Wait for animation to finish before unmounting
    setTimeout(() => {
      setIsMounted(false)
    }, 150)
  }

  React.useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  const getPositionClasses = () => {
    const alignClasses = {
      start: "left-0",
      center: "left-1/2 -translate-x-1/2",
      end: "right-0",
    }

    switch (side) {
      case "top":
        return `bottom-full ${alignClasses[align]} mb-2`
      case "bottom":
        return `top-full ${alignClasses[align]} mt-2`
      case "left":
        return "right-full top-1/2 -translate-y-1/2 mr-2"
      case "right":
        return "left-full top-1/2 -translate-y-1/2 ml-2"
    }
  }

  return (
    <div className="relative inline-flex">
      {React.cloneElement(children as React.ReactElement<Record<string, unknown>>, {
        onMouseEnter: handleMouseEnter,
        onMouseLeave: handleMouseLeave,
      })}
      {isMounted && (
        <div
          className={`
            absolute z-50 pointer-events-none whitespace-nowrap
            ${getPositionClasses()}
          `}
        >
          <div
            className={`
              px-2 py-1 text-[11px] font-medium
              bg-[#0a0a0a] text-gray-200
              border border-[#404040] rounded-md shadow-lg
              transition-opacity duration-150
              ${isVisible ? "opacity-100" : "opacity-0"}
            `}
          >
            {content}
          </div>
        </div>
      )}
    </div>
  )
}
