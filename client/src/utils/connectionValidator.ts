import { type LLMConnection } from '../services/api'
import { type LLMProvider } from '../types/llm'

export interface ValidationResult {
  isValid: boolean
  connectionId?: string
  provider?: LLMProvider
  model?: string
  reason?: string
}

export interface ConnectionSelection {
  provider: LLMProvider
  model: string
  connectionId: string
}

class ConnectionValidator {
  /**
   * Validates if a connection ID exists in the available connections
   */
  static validateConnectionId(
    connectionId: string,
    availableConnections: LLMConnection[]
  ): boolean {
    return availableConnections.some(conn => conn.id === connectionId)
  }

  /**
   * Finds a compatible connection for a given provider
   */
  static findConnectionForProvider(
    provider: LLMProvider,
    availableConnections: LLMConnection[]
  ): LLMConnection | null {
    return availableConnections.find(conn => conn.type === provider) || null
  }

  /**
   * Validates a complete selection (provider, model, connectionId)
   */
  static validateSelection(
    provider: LLMProvider | undefined,
    model: string | undefined,
    connectionId: string | undefined,
    availableConnections: LLMConnection[],
    availableModels: Record<string, string[]>
  ): ValidationResult {
    // Check if all required fields are present
    if (!provider || !model || !connectionId) {
      return {
        isValid: false,
        reason: 'Missing required selection fields'
      }
    }

    // Check if connection exists
    const connectionExists = this.validateConnectionId(connectionId, availableConnections)
    if (!connectionExists) {
      return {
        isValid: false,
        reason: 'Connection no longer exists'
      }
    }

    // Check if the connection matches the provider
    const connection = availableConnections.find(conn => conn.id === connectionId)
    if (connection?.type !== provider) {
      return {
        isValid: false,
        reason: 'Connection provider mismatch'
      }
    }

    // Check if the model is available for the provider
    const providerModels = availableModels[provider] || []
    if (!providerModels.includes(model)) {
      return {
        isValid: false,
        reason: 'Model not available for provider'
      }
    }

    return {
      isValid: true,
      connectionId,
      provider,
      model
    }
  }

  /**
   * Attempts to resolve a valid connection based on saved preferences
   * This should only be called when saved preferences are invalid
   */
  static resolveConnection(
    savedProvider: LLMProvider | undefined,
    savedModel: string | undefined,
    availableConnections: LLMConnection[],
    availableModels: Record<string, string[]>
  ): ConnectionSelection | null {
    // If we have valid saved preferences, try to use them
    if (savedProvider && savedModel) {
      // Check if the model is still available for the provider
      const providerModels = availableModels[savedProvider] || []
      if (providerModels.includes(savedModel)) {
        // Find a connection for this provider
        const connection = this.findConnectionForProvider(savedProvider, availableConnections)
        if (connection) {
          return {
            provider: savedProvider,
            model: savedModel,
            connectionId: connection.id
          }
        }
      }

      // Try to keep the same provider if possible, just with a different model
      if (savedProvider) {
        const connection = this.findConnectionForProvider(savedProvider, availableConnections)
        const providerModels = availableModels[savedProvider] || []
        if (connection && providerModels.length > 0) {
          // Try to find a similar model name or use the first available
          const similarModel = providerModels.find(m => m.includes(savedModel.split('-')[0])) || providerModels[0]
          return {
            provider: savedProvider,
            model: similarModel,
            connectionId: connection.id
          }
        }
      }
    }

    // Fallback: Auto-select first available connection and model
    return this.autoSelectConnection(availableConnections, availableModels)
  }

  /**
   * Auto-selects the first available connection and model
   * Prefers Anthropic Claude models over others
   */
  static autoSelectConnection(
    availableConnections: LLMConnection[],
    availableModels: Record<string, string[]>
  ): ConnectionSelection | null {
    if (availableConnections.length === 0) {
      return null
    }

    // Priority order: Anthropic > OpenAI > OpenRouter
    const providerPriority: LLMProvider[] = ['anthropic', 'openai', 'openrouter']

    // Preferred models in order
    const preferredModels = [
      'anthropic/claude-opus-4-8',
      'anthropic/claude-opus-4-7',
      'anthropic/claude-sonnet-4-6',
    ]

    // First, try to find a connection with a preferred model
    for (const preferredModel of preferredModels) {
      for (const connection of availableConnections) {
        const provider = connection.type as LLMProvider
        const models = availableModels[provider] || []

        if (models.includes(preferredModel)) {
          return {
            provider,
            model: preferredModel,
            connectionId: connection.id
          }
        }
      }
    }

    // Fallback: Try providers in priority order
    for (const preferredProvider of providerPriority) {
      const connection = availableConnections.find(c => c.type === preferredProvider)
      if (connection) {
        const models = availableModels[preferredProvider] || []
        if (models.length > 0) {
          return {
            provider: preferredProvider,
            model: models[0],
            connectionId: connection.id
          }
        }
      }
    }

    // Last resort: First available connection with models
    for (const connection of availableConnections) {
      const provider = connection.type as LLMProvider
      const models = availableModels[provider] || []

      if (models.length > 0) {
        return {
          provider,
          model: models[0],
          connectionId: connection.id
        }
      }
    }

    return null
  }

  /**
   * Checks if the current selection needs to be updated
   */
  static needsUpdate(
    currentConnectionId: string | undefined,
    availableConnections: LLMConnection[]
  ): boolean {
    if (!currentConnectionId) {
      return availableConnections.length > 0
    }

    return !this.validateConnectionId(currentConnectionId, availableConnections)
  }

  /**
   * Gets a user-friendly message for validation failure
   */
  static getValidationMessage(result: ValidationResult): string {
    if (result.isValid) {
      return 'Connection is valid'
    }

    switch (result.reason) {
      case 'Missing required selection fields':
        return 'Please select a provider and model'
      case 'Connection no longer exists':
        return 'The selected connection has been removed. Please select a new one.'
      case 'Connection provider mismatch':
        return 'The connection provider has changed. Please reselect.'
      case 'Model not available for provider':
        return 'The selected model is no longer available. Please choose another.'
      default:
        return 'Invalid connection configuration'
    }
  }
}

export default ConnectionValidator