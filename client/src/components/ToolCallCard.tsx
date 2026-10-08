import { useState } from "react"
import { ChevronDown, Copy, Play, Database, Leaf, Save, Search, Sparkles } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Button } from "./ui/button"
import { CodeHighlight } from "./CodeHighlight"
import { Tooltip } from "./ui/tooltip"

interface ToolCallCardProps {
  tool_name: string
  description: string
  arguments: unknown
  id?: string
  onCodeInject?: (data: { query: string; connectionId?: string }) => void
}

const safeJsonParse = (input: string) => {
  try {
    return JSON.parse(input)
  } catch {
    return null
  }
}

const stripConnMarkers = (text: string): string =>
  text
    .replace(/<<<conn-[a-z]+-[a-f0-9-]+>>>\n?/gi, "")
    .replace(/<<<[a-f0-9-]+>>>\n?/gi, "")

const extractConn = (text: string): { datasourceType?: string; connectionId?: string } => {
  const m = text.match(/<<<conn-([a-z]+)-([a-f0-9-]+)>>>/i)
  if (m) {
    const t = m[1].toLowerCase()
    const validTypes = ['pg', 'mongo', 'mysql', 'sqlite', 'mssql', 'csv', 'excel', 'parquet', 'json']
    return {
      datasourceType: validTypes.includes(t) ? (t as any) : undefined,
      connectionId: m[2]
    }
  }
  const u = text.match(/<<<([a-f0-9-]+)>>>/i)
  if (u) return { connectionId: u[1] }
  return {}
}

// Tool configuration with icons and colors
const TOOL_CONFIGS = {
  execute_sql_query: {
    icon: Database,
    label: 'SQL Query',
    color: 'text-blue-400',
    borderColor: 'border-l-blue-400',
    bgColor: 'bg-blue-500/5'
  },
  execute_mongo_query: {
    icon: Leaf,
    label: 'MongoDB',
    color: 'text-green-400',
    borderColor: 'border-l-green-400',
    bgColor: 'bg-green-500/5'
  },
  execute_duckdb_query: {
    icon: Database,
    label: 'DuckDB',
    color: 'text-cyan-400',
    borderColor: 'border-l-cyan-400',
    bgColor: 'bg-cyan-500/5'
  },
  save_query: {
    icon: Save,
    label: 'Save Query',
    color: 'text-purple-400',
    borderColor: 'border-l-purple-400',
    bgColor: 'bg-purple-500/5'
  },
  search_datasets: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  },
  search_learnings: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  },
  search_enabled_skills: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  },
  search_memory: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  },
  search_instructions: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  },
  search_local_repo_files: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  },
  grep_local_repo: {
    icon: Search,
    label: 'Search',
    color: 'text-amber-400',
    borderColor: 'border-l-amber-400',
    bgColor: 'bg-amber-500/5'
  }
}

interface SkillMetadata {
  skill_name?: string
  display_name?: string
  emoji?: string
  is_graphql?: boolean
}

interface ToolConfig {
  icon: LucideIcon
  label: string
  color: string
  borderColor: string
  bgColor: string
  emoji?: string
}

const getToolConfig = (toolName: string, skillMetadata?: SkillMetadata): ToolConfig => {
  if (toolName === 'execute_skill_api' && skillMetadata?.skill_name) {
    const apiType = skillMetadata.is_graphql ? 'GraphQL' : 'REST'
    return {
      icon: Sparkles,
      label: `${skillMetadata.display_name || skillMetadata.skill_name} - ${apiType} API`,
      emoji: skillMetadata.emoji || '🔧',
      color: 'text-gray-400',
      borderColor: 'border-l-gray-400',
      bgColor: 'bg-gray-500/5'
    }
  }
  return TOOL_CONFIGS[toolName as keyof typeof TOOL_CONFIGS] || {
    icon: Sparkles,
    label: 'Tool',
    color: 'text-gray-400',
    borderColor: 'border-l-gray-400',
    bgColor: 'bg-gray-500/5'
  }
}

export function ToolCallCard({ tool_name, description, arguments: args, onCodeInject }: ToolCallCardProps) {
  const hasDescription = description && description.trim().length > 0
  const hasArguments = Boolean(args && (
    typeof args === 'string' ? args.trim().length > 0 :
    typeof args === 'object' ? Object.keys(args).length > 0 : false
  ))

  const isSkillApi = tool_name === 'execute_skill_api'
  const isQueryTool = ['execute_sql_query', 'execute_mongo_query', 'execute_duckdb_query', 'save_query'].includes(tool_name)
  const [open, setOpen] = useState(isQueryTool && hasArguments)
  const [copied, setCopied] = useState(false)

  let parsedArgs: Record<string, unknown> | null = null
  let fallbackArgs: string | null = null

  if (typeof args === 'string') {
    const trimmed = args.trim()
    if (trimmed) {
      const firstPass = safeJsonParse(trimmed)
      if (firstPass && typeof firstPass === 'object') {
        parsedArgs = firstPass as Record<string, unknown>
      } else if (typeof firstPass === 'string') {
        const secondPass = safeJsonParse(firstPass)
        if (secondPass && typeof secondPass === 'object') {
          parsedArgs = secondPass as Record<string, unknown>
        } else {
          fallbackArgs = firstPass
        }
      } else {
        fallbackArgs = trimmed
      }
    }
  } else if (args && typeof args === 'object') {
    parsedArgs = args as Record<string, unknown>
  }

  if (!parsedArgs && typeof args === 'string' && !fallbackArgs) {
    fallbackArgs = args
  }

  const skillMetadata: SkillMetadata | undefined = tool_name === 'execute_skill_api' && parsedArgs?.skill_name
    ? parsedArgs as SkillMetadata
    : undefined
  const toolConfig = getToolConfig(tool_name, skillMetadata)
  const IconComponent = toolConfig.icon

  const SKILL_METADATA_KEYS = ['skill_name', 'display_name', 'emoji', 'is_graphql']
  const displayArgs = skillMetadata && parsedArgs
    ? Object.fromEntries(Object.entries(parsedArgs).filter(([key]) => !SKILL_METADATA_KEYS.includes(key)))
    : parsedArgs

  const isExecuteQueryTool = ['execute_sql_query', 'execute_mongo_query', 'execute_duckdb_query', 'save_query'].includes(tool_name)
  const hasQuery = parsedArgs && Object.keys(parsedArgs).length > 0 && 'query' in parsedArgs
  const rawQueryContent = hasQuery && parsedArgs ? String(parsedArgs.query) : ''
  const queryContent = stripConnMarkers(rawQueryContent)
  // Check for both connection_id (SQL/Mongo) and dataset_id (DuckDB/files)
  const connectionId = parsedArgs && ('connection_id' in parsedArgs || 'dataset_id' in parsedArgs)
    ? String(parsedArgs.connection_id || parsedArgs.dataset_id)
    : undefined

  let queryLanguage: 'sql' | 'javascript' = 'sql'
  if (tool_name === 'execute_sql_query' || tool_name === 'execute_duckdb_query') {
    queryLanguage = 'sql'
  } else if (tool_name === 'execute_mongo_query') {
    queryLanguage = 'javascript'
  } else if (tool_name === 'save_query') {
    const { datasourceType } = extractConn(rawQueryContent)
    queryLanguage = datasourceType === 'mongo' ? 'javascript' : 'sql'
  }

  const skillApiUrl = isSkillApi && displayArgs ? String(displayArgs.url || '') : ''
  const skillApiMethod = isSkillApi && displayArgs ? String(displayArgs.method || 'GET') : ''
  const skillApiBody = isSkillApi && displayArgs && displayArgs.body ? String(displayArgs.body) : ''
  const skillApiQuery = isSkillApi && displayArgs && displayArgs.query ? String(displayArgs.query) : ''
  const hasSkillApiContent = skillApiUrl || skillApiBody || skillApiQuery

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => {
      setCopied(false)
    }, 2000)
  }

  const handleInject = () => {
    if (onCodeInject && queryContent) {
      onCodeInject({ query: queryContent, connectionId })
    }
  }

  return (
    <div className="w-full">
      {/* Compact header — entire row is clickable */}
      <div
        onClick={() => setOpen(!open)}
        className={`
          flex items-center justify-between gap-2
          bg-[#1f1f1f] border border-[#2a2a2a]
          px-3 py-2 cursor-pointer select-none
          transition-all duration-200
          ${open ? 'rounded-t-lg border-b-0' : 'rounded-lg hover:border-[#404040]'}
        `}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {'emoji' in toolConfig && toolConfig.emoji ? (
            <span className="w-3 h-3 flex-shrink-0 text-xs leading-none">{String(toolConfig.emoji)}</span>
          ) : (
            <IconComponent className={`w-3 h-3 flex-shrink-0 ${String(toolConfig.color)}`} />
          )}

          <span className={`
            flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded
            font-medium tracking-wide
            ${toolConfig.bgColor} ${toolConfig.color}
          `}>
            {toolConfig.label}
          </span>

          {!skillMetadata && (
            <div className="min-w-0 flex-1">
              <span className="text-sm font-medium text-gray-200 block truncate">
                {description || tool_name}
              </span>
            </div>
          )}
        </div>

        <ChevronDown
          className={`w-3.5 h-3.5 text-gray-400 flex-shrink-0 transition-transform duration-200 ${
            open ? '' : '-rotate-90'
          }`}
        />
      </div>

      {/* Expandable content with smooth animation */}
      {open && (
        <div className="
          bg-[#1f1f1f] border border-t-0 border-[#2a2a2a]
          rounded-b-lg px-3 pb-3 pt-2
        ">
          {/* Query display with improved styling */}
          {hasQuery ? (
            <div className="space-y-2">
              <div className={`
                bg-[#0f0f0f] border border-[#2a2a2a]
                rounded-md overflow-hidden
                border-l-2 ${toolConfig.borderColor}
              `}>
                {/* Header with always-visible buttons */}
                <div className="flex items-center justify-between px-3 py-1.5 bg-[#1a1a1a]">
                  <div className="flex items-center gap-1.5">
                    <IconComponent className={`w-3 h-3 ${toolConfig.color}`} />
                    <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">
                      Query
                    </span>
                  </div>

                  {isExecuteQueryTool && (
                    <div className="flex gap-1.5">
                      <Tooltip content={copied ? 'Copied!' : 'Copy query'} side="bottom">
                        <Button
                          onClick={() => copyToClipboard(queryContent)}
                          size="sm"
                          className="h-6 w-6 p-0 bg-[#232323] hover:bg-[#2a2a2a] text-white border border-[#404040] transition-all"
                        >
                          <Copy className="w-3 h-3" />
                        </Button>
                      </Tooltip>
                      {onCodeInject && (
                        <Tooltip content="Run query" side="bottom">
                          <Button
                            onClick={handleInject}
                            size="sm"
                            className="h-6 w-6 p-0 bg-[#232323] hover:bg-[#2a2a2a] text-brand-orange hover:text-brand-orange-hover border border-brand-orange/30 hover:border-brand-orange/50 transition-all"
                          >
                            <Play className="w-3 h-3" />
                          </Button>
                        </Tooltip>
                      )}
                    </div>
                  )}
                </div>

                {/* Code content */}
                <CodeHighlight
                  code={queryContent}
                  language={queryLanguage}
                  customStyle={{
                    margin: 0,
                    background: '#0f0f0f',
                    fontSize: '0.7rem',
                    padding: '0.75rem',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    overflow: 'hidden',
                  }}
                />
              </div>
            </div>
          ) : isSkillApi && hasSkillApiContent ? (
            <div className="space-y-2">
              {skillApiUrl && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="px-1.5 py-0.5 rounded bg-gray-700 text-gray-300 font-mono font-medium">
                    {skillApiMethod}
                  </span>
                  <span className="text-gray-400 font-mono truncate">{skillApiUrl}</span>
                </div>
              )}
              {(skillApiBody || skillApiQuery) && (
                <div className={`
                  bg-[#0f0f0f] border border-[#2a2a2a]
                  rounded-md overflow-hidden
                  border-l-2 ${toolConfig.borderColor}
                `}>
                  <div className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1a1a1a]">
                    <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">
                      {skillApiQuery ? 'Query' : 'Body'}
                    </span>
                  </div>
                  <CodeHighlight
                    code={skillApiQuery || skillApiBody}
                    language={skillApiQuery ? 'graphql' : 'json'}
                    customStyle={{
                      margin: 0,
                      background: '#0f0f0f',
                      fontSize: '0.7rem',
                      padding: '0.75rem'
                    }}
                  />
                </div>
              )}
            </div>
          ) : displayArgs && Object.keys(displayArgs).length > 0 ? (
            <div className="bg-[#0f0f0f] border border-[#2a2a2a] p-2.5 rounded-md text-xs space-y-2">
              {Object.entries(displayArgs).map(([key, value]) => (
                <div key={key}>
                  <p className="font-semibold text-gray-300 mb-1 text-[11px]">{key}</p>
                  <pre className="whitespace-pre-wrap break-words text-gray-400 font-mono text-[11px]">
                    {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          ) : fallbackArgs ? (
            <div className="bg-[#0f0f0f] border border-[#2a2a2a] p-2.5 rounded-md text-xs">
              <pre className="whitespace-pre-wrap break-words text-gray-400 font-mono text-[11px]">
                {fallbackArgs}
              </pre>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
