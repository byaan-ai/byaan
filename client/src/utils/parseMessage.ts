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

export type MessagePart = TextPart | ToolCallPart

// Tools that should be hidden from chat (handled by separate UI)
const HIDDEN_TOOLS = ['emit_plan_status']

export function parseMessage(text: string): MessagePart[] {
  // b64-encoded args form (current backend): args payload is `b64:<base64>` which contains only [A-Za-z0-9+/=],
  // so matching up to the closing `]]` is unambiguous. Legacy form (pre-b64) kept as fallback for old sessions.
  const regex = /\[\[TOOL_CALL:(.*?):(.*?)\|(.*?)\|(b64:[A-Za-z0-9+/=]+|.*?)\]\](?=\[\[TOOL_CALL|[^[\]]|$)/g
  const parts: MessagePart[] = []
  let lastIndex = 0
  let match
  let hasMatches = false

  while ((match = regex.exec(text)) !== null) {
    hasMatches = true

    // Push text before marker
    if (match.index > lastIndex) {
      const textContent = text.slice(lastIndex, match.index)
      const trimmed = textContent.trim()
      if (trimmed && trimmed !== "{}") {
        parts.push({ type: "text", content: textContent })
      }
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

  // Push remaining text
  if (lastIndex < text.length) {
    const remainingText = text.slice(lastIndex)
    const trimmed = remainingText.trim()
    if (trimmed && trimmed !== "{}") {
      parts.push({ type: "text", content: remainingText })
    }
  }

  // If no tool calls were found at all, return the original text
  // But if we found matches (even hidden ones), return empty or filtered parts
  if (!hasMatches && parts.length === 0) {
    parts.push({ type: "text", content: text })
  }

  return parts
}
