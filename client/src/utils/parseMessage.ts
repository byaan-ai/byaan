export interface ToolCallPart {
  type: "tool_call"
  id: string
  tool_name: string
  description: string
  args: unknown
}

export interface TextPart {
  type: "text"
  content: string
}

export interface MonitorCardPart {
  type: "monitor_card"
  monitor_id: string
  deleted: boolean
}

export type MessagePart = TextPart | ToolCallPart | MonitorCardPart

// Tools that should be hidden from chat (handled by separate UI)
const HIDDEN_TOOLS = ['emit_plan_status']

export function parseMessage(text: string): MessagePart[] {
  // Extract monitor-card markers first so they survive the tool-call regex.
  const monitorMarkerRegex = /\[\[MONITOR_(CARD|DELETED):([a-f0-9-]+)\]\]/gi
  const monitorIntervals: Array<{ start: number; end: number; monitor_id: string; deleted: boolean }> = []
  let mm: RegExpExecArray | null
  while ((mm = monitorMarkerRegex.exec(text)) !== null) {
    monitorIntervals.push({
      start: mm.index,
      end: mm.index + mm[0].length,
      monitor_id: mm[2],
      deleted: mm[1].toUpperCase() === "DELETED",
    })
  }

  const regex = /\[\[TOOL_CALL:(.*?):(.*?)\|(.*?)\|(.*?)\]\](?=\[\[TOOL_CALL|[^[\]]|$)/g
  const parts: MessagePart[] = []
  let lastIndex = 0
  let match
  let hasMatches = false

  const pushRangeAsText = (from: number, to: number) => {
    const slice = text.slice(from, to)
    // Interleave any monitor markers that live inside this range.
    let cursor = from
    for (const iv of monitorIntervals) {
      if (iv.start < from || iv.end > to) continue
      if (iv.start > cursor) {
        const chunk = text.slice(cursor, iv.start)
        const trimmed = chunk.trim()
        if (trimmed && trimmed !== "{}") parts.push({ type: "text", content: chunk })
      }
      parts.push({ type: "monitor_card", monitor_id: iv.monitor_id, deleted: iv.deleted })
      cursor = iv.end
    }
    if (cursor < to) {
      const tail = text.slice(cursor, to)
      const trimmedTail = tail.trim()
      if (trimmedTail && trimmedTail !== "{}") parts.push({ type: "text", content: tail })
    }
    if (!monitorIntervals.some((iv) => iv.start >= from && iv.end <= to)) {
      // Fast path: no markers in this range — restore original behaviour.
      const trimmed = slice.trim()
      if (trimmed && trimmed !== "{}" && parts[parts.length - 1]?.type !== "text") {
        parts.push({ type: "text", content: slice })
      }
    }
  }

  while ((match = regex.exec(text)) !== null) {
    hasMatches = true

    if (match.index > lastIndex) {
      pushRangeAsText(lastIndex, match.index)
    }

    // Parse and push tool_call
    try {
      const id = match[1]
      const tool_name = match[2]
      const description = match[3]
      const argsString = match[4]

      // Parse JSON arguments safely
      let args: unknown = {}
      try {
        const firstParse = JSON.parse(argsString)
        if (typeof firstParse === "string") {
          try {
            const secondParse = JSON.parse(firstParse)
            args = secondParse
          } catch {
            args = firstParse
          }
        } else {
          args = firstParse
        }
      } catch {
        console.warn('Failed to parse tool call arguments:', argsString)
        args = argsString
      }

      // Skip hidden tools (they have separate UI)
      if (!HIDDEN_TOOLS.includes(tool_name)) {
        parts.push({
          type: "tool_call",
          id,
          tool_name,
          description,
          args
        })
      }
    } catch {
      console.warn('Failed to parse tool call marker:', match[0])
      // Add as text if parsing fails
      parts.push({ type: "text", content: match[0] })
    }

    lastIndex = regex.lastIndex
  }

  if (lastIndex < text.length) {
    pushRangeAsText(lastIndex, text.length)
  }

  // If no tool calls OR monitor markers were found, keep the original text.
  if (!hasMatches && monitorIntervals.length === 0 && parts.length === 0) {
    parts.push({ type: "text", content: text })
  }

  return parts
}
