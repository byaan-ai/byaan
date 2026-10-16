import React, { memo } from 'react'
import { Highlight, themes } from 'prism-react-renderer'

interface CodeHighlightProps {
  code: string
  language: 'html' | 'sql' | 'javascript' | 'typescript' | 'json' | 'graphql'
  showLineNumbers?: boolean
  customStyle?: React.CSSProperties
  className?: string
}

export const CodeHighlight = memo(function CodeHighlight({
  code,
  language,
  showLineNumbers = false,
  customStyle = {},
  className = ''
}: CodeHighlightProps) {
  return (
    <Highlight
      theme={themes.vsDark}
      code={code || ''}
      language={language}
    >
      {({ className: preClassName, style, tokens, getLineProps, getTokenProps }) => (
        <pre
          className={`${preClassName} ${className}`}
          style={{
            ...style,
            margin: 0,
            padding: '1rem',
            background: '#1e1e1e',
            fontSize: '0.875rem',
            borderRadius: '0',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            overflow: 'auto',
            userSelect: 'text',
            WebkitUserSelect: 'text',
            cursor: 'text',
            ...customStyle
          }}
        >
          {tokens.map((line, i) => {
            const lineProps = getLineProps({ line })
            return (
              <div
                key={i}
                {...lineProps}
                style={{ ...lineProps.style, userSelect: 'text', WebkitUserSelect: 'text' }}
              >
                {showLineNumbers && (
                  <span style={{
                    display: 'inline-block',
                    width: '2em',
                    userSelect: 'none',
                    opacity: 0.5,
                    marginRight: '1em'
                  }}>
                    {i + 1}
                  </span>
                )}
                {line.map((token, key) => {
                  const tokenProps = getTokenProps({ token })
                  return (
                    <span
                      key={key}
                      {...tokenProps}
                      style={{ ...tokenProps.style, userSelect: 'text', WebkitUserSelect: 'text' }}
                    />
                  )
                })}
              </div>
            )
          })}
        </pre>
      )}
    </Highlight>
  )
})
