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

export interface MonitorRunHeaderPart {
  type: "monitor_run_header"
  monitor_id: string
  monitor_name: string
  when: string
  trigger_phrase: string
}

export type MessagePart = TextPart | ToolCallPart | MonitorCardPart | MonitorRunHeaderPart

// Tools that should be hidden from chat (handled by separate UI)
const HIDDEN_TOOLS = ['emit_plan_status']

export function parseMessage(text: string): MessagePart[] {
  // Extract monitor-card markers first so they survive the tool-call regex.
  const monitorMarkerRegex = /\[\[MONITOR_(CARD|DELETED):([a-f0-9-]+)\]\]/gi
  const monitorIntervals: Array<{
    start: number
    end: number
    kind: "card" | "header"
    monitor_id: string
    deleted: boolean
    monitor_name?: string
    when?: string
    trigger_phrase?: string
  }> = []
  let mm: RegExpExecArray | null
  while ((mm = monitorMarkerRegex.exec(text)) !== null) {
    monitorIntervals.push({
      start: mm.index,
      end: mm.index + mm[0].length,
      kind: "card",
      monitor_id: mm[2],
      deleted: mm[1].toUpperCase() === "DELETED",
    })
  }

  const headerRegex = /\[\[MONITOR_RUN_HEADER:([a-f0-9-]+)\|([^|\]]*)\|([^|\]]*)\|([^\]]*)\]\]/gi
  let hm: RegExpExecArray | null
  while ((hm = headerRegex.exec(text)) !== null) {
    monitorIntervals.push({
      start: hm.index,
      end: hm.index + hm[0].length,
      kind: "header",
      monitor_id: hm[1],
      monitor_name: hm[2],
      when: hm[3],
      trigger_phrase: hm[4],
      deleted: false,
    })
  }
  monitorIntervals.sort((a, b) => a.start - b.start)

  // b64-encoded args form (current backend): args payload is `b64:<base64>` which contains only [A-Za-z0-9+/=],
  // so matching up to the closing `]]` is unambiguous. Legacy form (pre-b64) kept as fallback for old sessions.
  const regex = /\[\[TOOL_CALL:(.*?):(.*?)\|(.*?)\|(b64:[A-Za-z0-9+/=]+|.*?)\]\](?=\[\[TOOL_CALL|[^[\]]|$)/g
  const parts: MessagePart[] = []
  let lastIndex = 0
  let match
  let hasMatches = false

  const pushRangeAsText = (from: number, to: number) => {
    // Walk the range and interleave any monitor markers; emits the final tail
    // text once (never duplicated). Also covers the no-markers case naturally.
    let cursor = from
    for (const iv of monitorIntervals) {
      if (iv.start < from || iv.end > to) continue
      if (iv.start > cursor) {
        const chunk = text.slice(cursor, iv.start)
        const trimmed = chunk.trim()
        if (trimmed && trimmed !== "{}") parts.push({ type: "text", content: chunk })
      }
      if (iv.kind === "header") {
        parts.push({
          type: "monitor_run_header",
          monitor_id: iv.monitor_id,
          monitor_name: iv.monitor_name || "",
          when: iv.when || "",
          trigger_phrase: iv.trigger_phrase || "",
        })
      } else {
        parts.push({ type: "monitor_card", monitor_id: iv.monitor_id, deleted: iv.deleted })
      }
      cursor = iv.end
    }
    if (cursor < to) {
      const tail = text.slice(cursor, to)
      const trimmed = tail.trim()
      if (trimmed && trimmed !== "{}") parts.push({ type: "text", content: tail })
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
      let argsString = match[4]

      if (argsString.startsWith("b64:")) {
        try {
          argsString = atob(argsString.slice(4))
        } catch {
          console.warn('Failed to base64-decode tool call arguments')
        }
      }

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
