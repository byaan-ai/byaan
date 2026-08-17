import { useCallback, useMemo, useState, type ReactNode, Children, isValidElement } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Copy, Play } from "lucide-react"
import { Button } from "./ui/button"
import { parseMessage } from "../utils/parseMessage"
import { ToolCallCard } from "./ToolCallCard"
import { CodeHighlight } from "./CodeHighlight"

interface MarkdownRendererProps {
  content: string
  onCodeInject?: (data: { query: string; connectionId?: string }) => void
  tableNames?: string[]
}

interface MarkdownComponentProps {
  children?: ReactNode
  href?: string
  src?: string
  alt?: string
  type?: string
  checked?: boolean
  className?: string
  inline?: boolean
}

const KNOWN_LANGUAGES = [
  "typescript",
  "javascript",
  "mongodb",
  "python",
  "shell",
  "bash",
  "json",
  "yaml",
  "html",
  "java",
  "ruby",
  "css",
  "sqldb",
  "sql",
  "mongo",
  "go",
  "cpp",
  "ts",
  "js",
  "py",
  "c",
]

const LANGUAGE_ALIASES: Record<string, string> = {
  sqldb: "sql",
}

const LANGUAGE_PREFIX_FIXES: Record<string, string> = {
  sqldb: "db",
}

const BLOCK_ELEMENTS = new Set([
  "div",
  "pre",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "blockquote",
  "ul",
  "ol",
  "li",
  "img",
])

const hasReactChildProps = (props: unknown): props is { children?: ReactNode; className?: string } => {
  return typeof props === "object" && props !== null
}

const SQL_CLAUSE_PATTERNS: RegExp[] = [
  /([^\n])\s*(FROM)\b/gi,
  /([^\n])\s*(WHERE)\b/gi,
  /([^\n])\s*(GROUP\s+BY)\b/gi,
  /([^\n])\s*(ORDER\s+BY)\b/gi,
  /([^\n])\s*(HAVING)\b/gi,
  /([^\n])\s*(LIMIT)\b/gi,
  /([^\n])\s*(OFFSET)\b/gi,
  /([^\n])\s*(UNION(?:\s+ALL)?)\b/gi,
  /([^\n])\s*(EXCEPT)\b/gi,
  /([^\n])\s*(INTERSECT)\b/gi,
  /([^\n])\s*((?:LEFT|RIGHT|FULL|INNER|CROSS)\s+(?:OUTER\s+)?JOIN)\b/gi,
  /([^\n])\s*(JOIN)\b/gi,
]

const normalizeClauseKeyword = (keyword: string) => keyword.replace(/\s+/g, " ").toUpperCase()

const enforceSqlFormatting = (sql: string): string => {
  if (!sql.trim()) {
    return sql
  }

  let formatted = sql

  SQL_CLAUSE_PATTERNS.forEach(pattern => {
    formatted = formatted.replace(pattern, (_match, before: string, clause: string) => {
      const normalizedClause = normalizeClauseKeyword(clause)
      return `${before}\n${normalizedClause} `
    })
  })

  // Ensure SELECT has trailing space when starting a line
  formatted = formatted.replace(/^(SELECT)(?!\s)/i, "SELECT ")
  formatted = formatted.replace(/\n(SELECT)(?!\s)/gi, "\nSELECT ")

  // Collapse excessive spaces after keywords while keeping one space
  formatted = formatted.replace(/\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION|UNION ALL|EXCEPT|INTERSECT|JOIN|LEFT JOIN|RIGHT JOIN|FULL JOIN|INNER JOIN|CROSS JOIN)\s+/gi, (match: string) => {
    const keyword = normalizeClauseKeyword(match.trim())
    return `${keyword} `
  })

  // Trim trailing spaces before newlines
  formatted = formatted.replace(/ +\n/g, "\n")

  return formatted.trimEnd()
}

// Tools that should be hidden from display (handled by separate UI)
const HIDDEN_TOOLS = ['emit_plan_status']

// Helper to strip connection markers and also extract connection info when needed
const stripConnectionMarkers = (text: string): string => {
  if (!text) return text
  // Remove markers like <<<conn-pg-uuid>>> and <<<uuid>>>
  return text
    .replace(/<<<conn-[a-z]+-[a-f0-9-]+>>>\n?/gi, "")
    .replace(/<<<[a-f0-9-]+>>>\n?/gi, "")
}

// Strip hidden tool call markers from text for display
const stripHiddenToolMarkers = (text: string): string => {
  if (!text) return text
  const hiddenToolPattern = new RegExp(
    `\\[\\[TOOL_CALL:[^:]+:(${HIDDEN_TOOLS.join('|')})\\|[^\\]]*\\]\\]\\s*`,
    'g'
  )
  return text.replace(hiddenToolPattern, '').trim()
}

const extractConnectionFromText = (text: string): { datasourceType?: string; connectionId?: string } => {
  const match = text.match(/<<<conn-([a-z]+)-([a-f0-9-]+)>>>/i)
  if (match) {
    const rawType = match[1].toLowerCase()
    // Accept all valid datasource types
    const validTypes = ['pg', 'mongo', 'mysql', 'sqlite', 'mssql', 'csv', 'excel', 'parquet', 'json']
    const datasourceType = validTypes.includes(rawType) ? (rawType as any) : undefined
    const connectionId = match[2]
    return { datasourceType, connectionId }
  }
  // Fallback: standalone UUID marker
  const uuidOnly = text.match(/<<<([a-f0-9-]+)>>>/i)
  if (uuidOnly) {
    return { connectionId: uuidOnly[1] }
  }
  return {}
}

export default function MarkdownRenderer({ content, onCodeInject }: MarkdownRendererProps) {
  const [copiedCode, setCopiedCode] = useState<string | null>(null)

  // Strip connection IDs and hidden tool markers from content before any processing
  const cleanedContent = useMemo(() => {
    let cleaned = stripConnectionMarkers(content)
    cleaned = stripHiddenToolMarkers(cleaned)
    return cleaned
  }, [content])

  // Extract connection_id from tool calls and map them to queries
  const queryConnectionMap = useMemo(() => {
    const map = new Map<string, string>()
    const parts = parseMessage(content)

    parts.forEach((part) => {
      if (part.type === 'tool_call') {
        const toolName = part.tool_name

        // Check if it's a query execution tool
        if (toolName === 'execute_sql_query' || toolName === 'execute_mongo_query' || toolName === 'execute_duckdb_query') {
          try {
            let args = part.args

            if (typeof args === 'string') {
              try {
                args = JSON.parse(args)
              } catch {
                return // Skip this tool call
              }
            }

            if (args && typeof args === 'object' && 'connection_id' in args && 'query' in args) {
              const connectionId = String(args.connection_id)
              const query = String(args.query).trim()
              map.set(query, connectionId)
            }
          } catch {
            // Skip failed tool calls
          }
        }
      }
    })

    return map
  }, [content])

  const normalizeContent = useCallback((text: string) => {
    if (!text) {
      return ""
    }

    let out = text

    // Remove connection ID markers (<<<conn-...>>> or <<<uuid>>>)
    out = stripConnectionMarkers(out)

    // Remove hidden tool call markers
    out = stripHiddenToolMarkers(out)

    // Fix markdown headings without space after hashes (e.g., ##Title → ## Title)
    out = out.replace(/^(#{1,6})([^\s#])/gm, '$1 $2')

    // Ensure triple backticks start on their own line
    out = out.replace(/([^\n])```/g, "$1\n```")

    // Ensure newline after known language identifiers or recover malformed fences
    const languagePattern = new RegExp(
      "```(" + KNOWN_LANGUAGES.join("|") + ")([^\n]*)",
      "gi"
    )
    out = out.replace(languagePattern, (_match, lang: string, remainder: string) => {
      const cleanedRemainder = remainder.replace(/^[:\s-]*/, "")
      const loweredLang = lang.toLowerCase()
      const normalizedLang = LANGUAGE_ALIASES[loweredLang] ?? loweredLang
      const prefixFix = LANGUAGE_PREFIX_FIXES[loweredLang]

      let adjustedRemainder = cleanedRemainder
      if (prefixFix && adjustedRemainder && !adjustedRemainder.toLowerCase().startsWith(prefixFix)) {
        if (adjustedRemainder.startsWith(".")) {
          adjustedRemainder = prefixFix + adjustedRemainder
        } else {
          adjustedRemainder = `${prefixFix}${adjustedRemainder}`
        }
      }

      if (!adjustedRemainder) {
        return `\`\`\`${normalizedLang}`
      }

      return `\`\`\`${normalizedLang}\n${adjustedRemainder}`
    })

    return out
  }, [])

  const normalizedContent = useMemo(() => normalizeContent(cleanedContent), [cleanedContent, normalizeContent])

  const messageParts = useMemo(() => parseMessage(cleanedContent), [cleanedContent])

  const handleCopy = useCallback((code: string) => {
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      return
    }

    navigator.clipboard
      .writeText(code)
      .then(() => {
        setCopiedCode(code)
        setTimeout(() => {
          setCopiedCode(null)
        }, 2000)
      })
      .catch(() => {
        // noop: clipboard failures should not break rendering
      })
  }, [])

  // Parse connection_id from code block content
  const parseConnectionId = useCallback((content: string): { connectionId: string | undefined, cleanQuery: string } => {
    const lines = content.split('\n')
    const firstLine = lines[0]?.trim()

    // Match <<<connection-id>>> pattern
    const match = firstLine?.match(/^<<<(.+?)>>>$/)

    if (match && match[1]) {
      const connectionId = match[1].trim()
      const cleanQuery = lines.slice(1).join('\n').trim()
      return { connectionId, cleanQuery }
    }

    return { connectionId: undefined, cleanQuery: content }
  }, [])

  const markdownComponents = useMemo(() => {
    const CodeBlock = ({ inline, children, className }: MarkdownComponentProps) => {
      const rawContent = String(children ?? "")
      const codeContent = rawContent.replace(/\n$/, "")
      const language = className?.replace("language-", "") ?? ""
      const normalizedLanguage = language.toLowerCase()

      // First, try to parse connection_id from the code block itself
      const { connectionId: parsedConnectionId, cleanQuery } = parseConnectionId(codeContent)

      // Fallback: Try to find connection_id by matching query with tool calls
      let connectionId: string | undefined = parsedConnectionId

      if (!connectionId) {
        // Check if this query matches any in our map (direct string match)
        connectionId = queryConnectionMap.get(cleanQuery.trim())
      }

      // Use cleanQuery (without connection_id line) for display
      const queryContent = cleanQuery

      const formattedContent = normalizedLanguage === "sql"
        ? enforceSqlFormatting(queryContent)
        : queryContent

      // Clean markers from the visible block
      const visibleContent = stripConnectionMarkers(formattedContent)

      const isCopied = copiedCode === visibleContent

      if (inline || !className) {
        return (
          <code className="bg-[#333333] px-2 py-1 rounded text-sm text-white font-mono inline">
            {codeContent}
          </code>
        )
      }

      const isQueryCode =
        ["sql", "javascript", "js", "mongodb", "mongo", "python", "py"].includes(normalizedLanguage) ||
        formattedContent.toLowerCase().includes("select") ||
        formattedContent.toLowerCase().includes("db.") ||
        formattedContent.toLowerCase().includes("df[") ||
        formattedContent.toLowerCase().includes("df.") ||
        formattedContent.toLowerCase().includes(".merge(") ||
        formattedContent.toLowerCase().includes(".groupby(")

      // Map language to CodeHighlight supported languages
      let highlightLanguage: "html" | "sql" | "javascript" | "typescript" | "json" | undefined
      if (["sql", "sqldb"].includes(normalizedLanguage)) {
        highlightLanguage = "sql"
      } else if (["javascript", "js", "mongodb", "mongo"].includes(normalizedLanguage)) {
        highlightLanguage = "javascript"
      } else if (["typescript", "ts"].includes(normalizedLanguage)) {
        highlightLanguage = "typescript"
      } else if (normalizedLanguage === "html") {
        highlightLanguage = "html"
      } else if (["json", "yaml"].includes(normalizedLanguage)) {
        highlightLanguage = "json"
      }

      return (
        <div className="relative group my-4">
          <div className="absolute top-2 right-2 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
            <Button
              onClick={() => handleCopy(visibleContent)}
              size="sm"
              className="h-8 px-2 bg-[#404040] hover:bg-[#4a4a4a] text-white text-xs"
            >
              <Copy className="w-3 h-3 mr-1" />
              {isCopied ? "Copied!" : "Copy"}
            </Button>
            {/* Only show inject button when we have BOTH query code AND connection_id */}
            {onCodeInject && isQueryCode && connectionId && (
              <Button
                onClick={() => {
                  onCodeInject({ query: formattedContent, connectionId })
                }}
                size="sm"
                className="h-8 px-2 bg-[#404040] hover:bg-[#4a4a4a] text-white text-xs"
                title={`Inject query (connection: ${connectionId})`}
              >
                <Play className="w-3 h-3 mr-1" />
                Run
              </Button>
            )}
          </div>
          {highlightLanguage ? (
            <CodeHighlight
              code={visibleContent}
              language={highlightLanguage}
              customStyle={{
                margin: 0,
                borderRadius: '0.5rem',
                border: '1px solid #404040'
              }}
            />
          ) : (
            <pre className="bg-[#333333] p-4 rounded-lg overflow-x-auto custom-scrollbar border border-[#404040]">
              <code className={`text-sm text-white font-mono ${className ?? ""}`}>
                {visibleContent}
              </code>
            </pre>
          )}
        </div>
      )
    }

    return {
      table: ({ children }: MarkdownComponentProps) => (
        <div className="overflow-x-auto my-4 rounded-lg border border-white/20">
          <table className="min-w-full divide-y divide-white/10">
            {children}
          </table>
        </div>
      ),
      thead: ({ children }: MarkdownComponentProps) => (
        <thead className="bg-white/5">
          {children}
        </thead>
      ),
      tbody: ({ children }: MarkdownComponentProps) => (
        <tbody className="divide-y divide-white/10 bg-transparent">
          {children}
        </tbody>
      ),
      tr: ({ children }: MarkdownComponentProps) => (
        <tr className="hover:bg-white/5 transition-colors">
          {children}
        </tr>
      ),
      th: ({ children }: MarkdownComponentProps) => (
        <th className="px-4 py-3 text-left text-xs font-semibold text-white uppercase tracking-wider">
          {children}
        </th>
      ),
      td: ({ children }: MarkdownComponentProps) => (
        <td className="px-4 py-3 text-sm text-white">
          {children}
        </td>
      ),
      code: CodeBlock,
      pre: ({ children }: MarkdownComponentProps) => children,
      blockquote: ({ children }: MarkdownComponentProps) => (
        <blockquote className="border-l-4 border-white/25 bg-white/5 pl-4 pr-4 py-3 rounded-r-lg italic my-4 text-white/70">
          {children}
        </blockquote>
      ),
      h1: ({ children }: MarkdownComponentProps) => (
        <h1 className="text-xl font-bold mb-3 mt-5 first:mt-0 text-white border-b border-[#404040] pb-2">
          {children}
        </h1>
      ),
      h2: ({ children }: MarkdownComponentProps) => (
        <h2 className="text-lg font-bold mb-2.5 mt-4 first:mt-0 text-white">
          {children}
        </h2>
      ),
      h3: ({ children }: MarkdownComponentProps) => (
        <h3 className="text-base font-semibold mb-2 mt-3 first:mt-0 text-white">
          {children}
        </h3>
      ),
      h4: ({ children }: MarkdownComponentProps) => (
        <h4 className="text-sm font-semibold mb-2 mt-2 first:mt-0 text-white">
          {children}
        </h4>
      ),
      h5: ({ children }: MarkdownComponentProps) => (
        <h5 className="text-sm font-semibold mb-1.5 mt-2 first:mt-0 text-white">
          {children}
        </h5>
      ),
      h6: ({ children }: MarkdownComponentProps) => (
        <h6 className="text-xs font-semibold mb-1.5 mt-2 first:mt-0 text-white">
          {children}
        </h6>
      ),
      p: ({ children }: MarkdownComponentProps) => {
        const childArray = Children.toArray(children)
        const containsBlock = childArray.some(child => {
          if (!isValidElement(child)) {
            return false
          }
          const childType = child.type

          // Check for native HTML block elements
          if (typeof childType === "string" && BLOCK_ELEMENTS.has(childType)) {
            return true
          }

          // Check for code blocks (which render as div)
          if (typeof childType === "function" && hasReactChildProps(child.props) && child.props.className?.includes("language-")) {
            return true
          }

          // Check if child has nested block elements (div, pre, etc.)
          const childProps = child.props
          if (hasReactChildProps(childProps)) {
            const nestedChildren = childProps.children
            if (nestedChildren) {
              const checkNested = (node: any): boolean => {
                if (!node) return false
                if (typeof node === "string") return false
                if (isValidElement(node)) {
                  const nodeType = node.type
                  if (typeof nodeType === "string" && BLOCK_ELEMENTS.has(nodeType)) {
                    return true
                  }
                  if (hasReactChildProps(node.props) && node.props.children) {
                    return checkNested(node.props.children)
                  }
                }
                if (Array.isArray(node)) {
                  return node.some(checkNested)
                }
                return false
              }

              if (checkNested(nestedChildren)) {
                return true
              }
            }
          }

          return false
        })

        // Always use div to avoid any hydration issues with block content
        if (containsBlock) {
          return <div className="mb-3 last:mb-0 leading-relaxed text-white">{children}</div>
        }

        return <p className="mb-3 last:mb-0 leading-relaxed text-white">{children}</p>
      },
      strong: ({ children }: MarkdownComponentProps) => <strong className="font-semibold text-white">{children}</strong>,
      em: ({ children }: MarkdownComponentProps) => <em className="italic text-white">{children}</em>,
      del: ({ children }: MarkdownComponentProps) => <del className="line-through text-white/60">{children}</del>,
      ul: ({ children }: MarkdownComponentProps) => (
        <ul className="list-disc pl-6 mb-3 space-y-2 text-white marker:text-white/60">
          {children}
        </ul>
      ),
      ol: ({ children }: MarkdownComponentProps) => (
        <ol className="list-decimal pl-6 mb-3 space-y-2 text-white marker:text-white/60 marker:font-semibold">
          {children}
        </ol>
      ),
      li: ({ children }: MarkdownComponentProps) => (
        <li className="leading-relaxed text-white pl-1">
          {children}
        </li>
      ),
      a: ({ children, href }: MarkdownComponentProps) => (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-white/80 hover:text-white underline underline-offset-2 decoration-white/40 hover:decoration-white/80 transition-colors font-medium"
        >
          {children}
        </a>
      ),
      hr: () => <hr className="my-5 border-white/20" />,
      img: ({ src, alt }: MarkdownComponentProps) => (
        <img src={src} alt={alt} className="max-w-full h-auto rounded-lg border border-[#404040] my-4" />
      ),
      input: ({ type, checked }: MarkdownComponentProps) => {
        if (type === "checkbox") {
          return <input type="checkbox" checked={checked} readOnly className="mr-2" />
        }
        return null
      },
    }
  }, [copiedCode, handleCopy, onCodeInject, queryConnectionMap, parseConnectionId])

  const renderMessageParts = () => {
    return messageParts.map((part, index) => {
      if (part.type === "tool_call") {
        return (
          <ToolCallCard
            key={`tool-${index}-${part.id}`}
            tool_name={part.tool_name}
            description={part.description}
            arguments={part.args}
            id={part.id}
            onCodeInject={onCodeInject}
          />
        )
      }

      const textContent = part.content
      const toolLoadingMatch = textContent.match(/\*(.*?)\.\.\.\*$/)
      const hasToolLoading = Boolean(toolLoadingMatch)

      if (hasToolLoading && toolLoadingMatch) {
        const lastIndex = textContent.lastIndexOf("\n\n*")
        const mainContentRaw = lastIndex > -1 ? textContent.substring(0, lastIndex) : ""
        const mainContent = normalizeContent(mainContentRaw)
        const toolDescription = toolLoadingMatch[1]

        return (
          <div key={`text-${index}`}>
            {mainContent && (
              <div className="prose prose-invert max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {mainContent}
                </ReactMarkdown>
              </div>
            )}
            {mainContent && <div className="mt-3" />}
            <div className="text-[#888888] italic">
              {toolDescription}
              <span className="loading-dots">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </span>
            </div>
          </div>
        )
      }

      const isLoadingState = textContent.endsWith("...")
      if (isLoadingState && !hasToolLoading) {
        const baseText = textContent.slice(0, -3)
        return (
          <div key={`text-${index}`} className="text-white">
            {baseText}
            <span className="loading-dots">
              <span>.</span>
              <span>.</span>
              <span>.</span>
            </span>
          </div>
        )
      }

      return (
        <div key={`text-${index}`} className="prose prose-invert max-w-none overflow-hidden text-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {normalizeContent(textContent)}
          </ReactMarkdown>
        </div>
      )
    })
  }

  if (messageParts.some(part => part.type === "tool_call")) {
    return <div className="space-y-2">{renderMessageParts()}</div>
  }

  const toolLoadingMatch = cleanedContent.match(/\*(.*?)\.\.\.\*$/)
  const hasToolLoading = Boolean(toolLoadingMatch)

  if (hasToolLoading && toolLoadingMatch) {
    const lastIndex = cleanedContent.lastIndexOf("\n\n*")
    const mainContentRaw = lastIndex > -1 ? cleanedContent.substring(0, lastIndex) : ""
    const mainContent = normalizeContent(mainContentRaw)
    const toolDescription = toolLoadingMatch[1]

    return (
      <div className="p-4 rounded-2xl text-sm bg-[#2a2a2a] text-white rounded-bl-md">
        {mainContent && (
          <div className="prose prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {mainContent}
            </ReactMarkdown>
          </div>
        )}
        {mainContent && <div className="mt-3" />}
        <div className="text-[#888888] italic">
          {toolDescription}
          <span className="loading-dots">
            <span>.</span>
            <span>.</span>
            <span>.</span>
          </span>
        </div>
      </div>
    )
  }

  const isLoadingState = cleanedContent.endsWith("...")
  if (isLoadingState && !hasToolLoading) {
    const baseText = cleanedContent.slice(0, -3)
    return (
      <div className="p-4 rounded-2xl text-sm bg-[#2a2a2a] text-white rounded-bl-md">
        {baseText}
        <span className="loading-dots">
          <span>.</span>
          <span>.</span>
          <span>.</span>
        </span>
      </div>
    )
  }

  // Don't render anything if content is empty after cleaning
  if (!normalizedContent.trim()) {
    return null
  }

  return (
    <div className="prose prose-invert max-w-none overflow-hidden text-sm">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {normalizedContent}
      </ReactMarkdown>
    </div>
  )
}
