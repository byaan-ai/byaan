import { useState, useEffect, useCallback } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ApiService } from '../services/api'

export interface SlackConfig {
  id: string
  slack_team_id: string
  slack_team_name: string | null
  is_active: boolean
  default_llm_connection_id: string | null
  default_model: string | null
  created_at: string
}

interface ConnectSlackData {
  bot_token: string
  signing_secret: string
  default_llm_connection_id?: string | null
  default_model?: string | null
}

interface UpdateSlackData {
  bot_token?: string
  signing_secret?: string
  default_llm_connection_id?: string | null
  default_model?: string | null
}

export function useSlackConfig(enabled = true) {
  const [slackConfig, setSlackConfig] = useState<SlackConfig | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [saving, setSaving] = useState(false)

  const loadSlackConfig = useCallback(async () => {
    if (!enabled) return
    try {
      setLoading(true)
      const config = await ApiService.getSlackConfig()
      setSlackConfig(config)
    } catch (error) {
      console.error('Failed to load Slack config:', error)
      setSlackConfig(null)
    } finally {
      setLoading(false)
    }
  }, [enabled])

  useEffect(() => {
    loadSlackConfig()
  }, [loadSlackConfig])

  const connect = useCallback(async (data: ConnectSlackData): Promise<SlackConfig> => {
    setSaving(true)
    try {
      const config = await ApiService.connectSlack({
        bot_token: data.bot_token,
        signing_secret: data.signing_secret,
        default_llm_connection_id: data.default_llm_connection_id || null,
        default_model: data.default_model || null,
      })
      setSlackConfig(config)
      return config
    } finally {
      setSaving(false)
    }
  }, [])

  const disconnect = useCallback(async (): Promise<void> => {
    setSaving(true)
    try {
      await ApiService.disconnectSlack()
      setSlackConfig(null)
    } finally {
      setSaving(false)
    }
  }, [])

  const updateSettings = useCallback(async (data: UpdateSlackData): Promise<SlackConfig> => {
    setSaving(true)
    try {
      const config = await ApiService.updateSlackConfig({
        default_llm_connection_id: data.default_llm_connection_id,
        default_model: data.default_model,
      })
      setSlackConfig(config)
      return config
    } finally {
      setSaving(false)
    }
  }, [])

  return {
    slackConfig,
    isConnected: !!slackConfig,
    loading,
    saving,
    connect,
    disconnect,
    updateSettings,
    refresh: loadSlackConfig,
  }
}

export function useTestSlackChannel() {
  return useMutation({
    mutationFn: (channelId: string) => ApiService.testSlackChannel(channelId),
  })
}
