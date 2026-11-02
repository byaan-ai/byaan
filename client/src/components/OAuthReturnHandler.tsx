import { useEffect } from 'react'

import { showToast } from '../utils/toast'

export function OAuthReturnHandler() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('mcp_oauth') !== '1') return

    const status = params.get('oauth')
    const name = params.get('name')
    const msg = params.get('msg')

    if (status === 'success') {
      showToast.success(name ? `External MCP "${name}" connected via OAuth` : 'External MCP connected via OAuth')
    } else if (status === 'error') {
      showToast.error(msg ? `OAuth connect failed: ${msg}` : 'OAuth connect failed')
    }

    window.dispatchEvent(new CustomEvent('open-mcp-modal'))

    const clean = new URL(window.location.href)
    clean.searchParams.delete('mcp_oauth')
    clean.searchParams.delete('oauth')
    clean.searchParams.delete('name')
    clean.searchParams.delete('msg')
    window.history.replaceState({}, '', clean.toString())
  }, [])

  return null
}
