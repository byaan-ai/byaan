import React, { useState, useEffect } from 'react'
import { X, AlertTriangle, ChevronUp, ChevronDown, Trash2, Wrench } from 'lucide-react'

export interface IframeError {
  id: string
  timestamp: Date
  type: 'error' | 'unhandledRejection' | 'console'
  message: string
  source?: string
  lineno?: number
  colno?: number
  stack?: string
  severity?: 'error' | 'warning' | 'info'
}

interface ErrorLogModalProps {
  errors: IframeError[]
  onClearErrors: () => void
  onFixWithAssistant?: (errors: IframeError[]) => void
}

export const ErrorLogModal: React.FC<ErrorLogModalProps> = ({ errors, onClearErrors, onFixWithAssistant }) => {
  const [isMinimized, setIsMinimized] = useState(false)
  const [selectedError, setSelectedError] = useState<IframeError | null>(null)

  useEffect(() => {
    if (errors.length > 0 && isMinimized) {
      setIsMinimized(false)
    }
  }, [errors.length])

  if (errors.length === 0) return null

  const getErrorTypeColor = (type: IframeError['type'], severity?: IframeError['severity']) => {
    // Determine color based on severity for console messages
    if (type === 'console' && severity === 'warning') {
      return 'text-yellow-400'
    }

    switch (type) {
      case 'error':
        return 'text-red-400'
      case 'unhandledRejection':
        return 'text-yellow-400'
      case 'console':
        return 'text-orange-400'
      default:
        return 'text-gray-400'
    }
  }

  const getErrorTypeBadge = (type: IframeError['type'], message: string) => {
    // Detect warning vs error from console message content
    if (type === 'console') {
      if (message.startsWith('Console Warning:')) {
        return 'Warning'
      }
      return 'Console Error'
    }

    switch (type) {
      case 'error':
        return 'JS Error'
      case 'unhandledRejection':
        return 'Promise'
      default:
        return 'Unknown'
    }
  }

  const getBadgeStyles = (type: IframeError['type'], message: string) => {
    // Different styles for warnings vs errors
    if (type === 'console' && message.startsWith('Console Warning:')) {
      return 'bg-yellow-900 text-yellow-200'
    }

    switch (type) {
      case 'error':
        return 'bg-red-900 text-red-200'
      case 'unhandledRejection':
        return 'bg-yellow-900 text-yellow-200'
      case 'console':
        return 'bg-orange-900 text-orange-200'
      default:
        return 'bg-gray-900 text-gray-200'
    }
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 max-w-lg">
      {isMinimized ? (
        <div
          className="bg-[#2a2a2a] border border-red-600 rounded-lg p-3 cursor-pointer hover:bg-[#333333] transition-colors"
          onClick={() => setIsMinimized(false)}
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-red-400" />
            <span className="text-white font-medium">Preview Issues ({errors.length})</span>
            <ChevronUp className="w-4 h-4 text-gray-400 ml-auto" />
          </div>
        </div>
      ) : (
        <div className="bg-[#2a2a2a] border border-[#404040] rounded-lg shadow-2xl">
          <div className="border-b border-[#404040] p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-red-400" />
                <h3 className="text-white font-medium">Preview Generation Errors ({errors.length})</h3>
              </div>
              <div className="flex items-center gap-1">
                {onFixWithAssistant && (
                  <button
                    onClick={() => onFixWithAssistant(errors)}
                    className="px-2 py-1 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded flex items-center gap-1 transition-colors"
                    title="Fix with AI Assistant"
                  >
                    <Wrench className="w-3 h-3" />
                    Fix with Assistant
                  </button>
                )}
                <button
                  onClick={onClearErrors}
                  className="p-1 hover:bg-[#404040] rounded transition-colors"
                  title="Clear all errors"
                >
                  <Trash2 className="w-4 h-4 text-gray-400" />
                </button>
                <button
                  onClick={() => setIsMinimized(true)}
                  className="p-1 hover:bg-[#404040] rounded transition-colors"
                  title="Minimize"
                >
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                </button>
                <button
                  onClick={onClearErrors}
                  className="p-1 hover:bg-[#404040] rounded transition-colors"
                  title="Close"
                >
                  <X className="w-4 h-4 text-gray-400" />
                </button>
              </div>
            </div>
          </div>

          <div className="max-h-96 overflow-y-auto">
            {errors.map((error) => (
              <div
                key={error.id}
                className="border-b border-[#404040] p-3 hover:bg-[#333333] cursor-pointer transition-colors"
                onClick={() => setSelectedError(selectedError?.id === error.id ? null : error)}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${getBadgeStyles(error.type, error.message)}`}>
                        {getErrorTypeBadge(error.type, error.message)}
                      </span>
                      <span className="text-xs text-gray-500">
                        {error.timestamp.toLocaleTimeString()}
                      </span>
                    </div>
                    <p className={`text-sm ${getErrorTypeColor(error.type, error.severity)} break-words whitespace-pre-wrap`}>
                      {error.message}
                    </p>
                    {error.source && (
                      <p className="text-xs text-gray-500 mt-1">
                        {error.source}
                        {error.lineno && `:${error.lineno}`}
                        {error.colno && `:${error.colno}`}
                      </p>
                    )}
                  </div>
                </div>

                {selectedError?.id === error.id && error.stack && (
                  <div className="mt-2 pt-2 border-t border-[#404040]">
                    <p className="text-xs text-gray-400 mb-1">Stack trace:</p>
                    <pre className="text-xs text-gray-500 whitespace-pre-wrap break-words overflow-x-auto">
                      {error.stack}
                    </pre>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
