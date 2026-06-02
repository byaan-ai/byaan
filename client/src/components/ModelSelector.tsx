import { useState, useEffect } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { AlertCircle, Brain, Zap, Globe, Star } from "lucide-react";
import { type LLMConnection } from "../services/api";
import { PROVIDER_CONFIGS, type LLMProvider } from "../types/llm";
import ConnectionValidator from "../utils/connectionValidator";
import { LLMConfig } from "./LLMConfig";
import { Tooltip } from "./ui/tooltip";
import { useScopes } from "../hooks/useScopes";

interface ProviderModels {
  [provider: string]: string[];
}

interface ModelSelectorProps {
  selectedProvider?: LLMProvider;
  selectedModel?: string;
  selectedConnectionId?: string;
  connections: LLMConnection[];
  availableModels: ProviderModels;
  onSelectionChange: (
    data:
      | { provider: LLMProvider; model: string; connectionId: string }
      | undefined,
  ) => void;
  onConnectionCreated?: () => void;
  placeholder?: string;
  className?: string;
  compact?: boolean;
  // Preferred model props
  preferredProvider?: string | null;
  preferredModel?: string | null;
  onSetPreferred?: (provider: string, model: string) => void;
  onClearPreferred?: () => void;
}

// Function to get provider icon
function getProviderIcon(provider: string, sizeClass: string = "w-4 h-4") {
  const iconProps = { className: sizeClass };
  switch (provider) {
    case "openai":
      return <Zap {...iconProps} />;
    case "anthropic":
      return <Brain {...iconProps} />;
    case "claude_code":
      return <Brain {...iconProps} />;
    case "codex":
      return <Zap {...iconProps} />;
    case "openrouter":
      return <Globe {...iconProps} />;
    default:
      return <Brain {...iconProps} />;
  }
}

// Function to format model names for display
function formatModelName(modelPath: string): string {
  // Remove the provider prefix (e.g., "openai/", "anthropic/")
  const modelName = modelPath.split("/").pop() || modelPath;

  // Format specific model names for better readability
  const formatMap: Record<string, string> = {
    "claude-opus-4.8": "Claude Opus 4.8",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-opus-4.7": "Claude Opus 4.7",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-sonnet-4.6": "Claude Sonnet 4.6",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-sonnet-4.5": "Claude Sonnet 4.5",
    "claude-haiku-4.5": "Claude Haiku 4.5",
    "grok-code-fast-1": "Grok Code Fast 1",
    "glm-4.5": "GLM 4.5",
    "glm-5.1": "GLM 5.1",
  };

  return formatMap[modelName] || modelName;
}

export function ModelSelector({
  selectedProvider,
  selectedModel,
  selectedConnectionId,
  connections,
  availableModels,
  onSelectionChange,
  onConnectionCreated,
  placeholder = "Select provider and model...",
  className = "",
  compact = false,
  preferredProvider,
  preferredModel,
  onSetPreferred,
  onClearPreferred,
}: ModelSelectorProps) {
  const { canCreateLLMConnection } = useScopes();
  const [validationWarning, setValidationWarning] = useState<string | null>(
    null,
  );
  const sizeIcon = compact ? "w-3.5 h-3.5" : "w-4 h-4";
  const sizeText = compact ? "text-xs" : "text-sm";

  // Check if a model is the preferred model
  const isPreferredModel = (provider: string, model: string) => {
    return preferredProvider === provider && preferredModel === model;
  };

  // Handle star click to set/clear preferred model
  const handleStarClick = (
    e: React.MouseEvent,
    provider: string,
    model: string,
  ) => {
    e.stopPropagation();
    e.preventDefault();

    if (isPreferredModel(provider, model)) {
      // Clear if already preferred
      onClearPreferred?.();
    } else {
      // Set as preferred
      onSetPreferred?.(provider, model);
    }
  };

  // Validate current selection when props change
  useEffect(() => {
    if (
      selectedProvider &&
      selectedModel &&
      selectedConnectionId &&
      connections.length > 0
    ) {
      const validation = ConnectionValidator.validateSelection(
        selectedProvider,
        selectedModel,
        selectedConnectionId,
        connections,
        availableModels,
      );

      if (!validation.isValid) {
        setValidationWarning(
          ConnectionValidator.getValidationMessage(validation),
        );
        // Don't auto-resolve here - let the parent component handle it
        // This prevents duplicate resolution attempts
      } else {
        setValidationWarning(null);
      }
    } else {
      setValidationWarning(null);
    }
  }, [
    connections,
    availableModels,
    selectedProvider,
    selectedModel,
    selectedConnectionId,
  ]);

  const getConnectionForProvider = (provider: LLMProvider) => {
    return connections.find((c) => c.type === provider);
  };

  const handleSelectionChange = (selectedModel: string) => {
    if (!selectedModel) {
      onSelectionChange(undefined);
      return;
    }

    // Find the provider that has this model
    const provider = Object.entries(availableModels).find(
      ([_, models]) => models.includes(selectedModel)
    )?.[0] as LLMProvider | undefined;

    if (provider) {
      const connection = getConnectionForProvider(provider);
      if (connection) {
        onSelectionChange({
          provider,
          model: selectedModel,
          connectionId: connection.id,
        });
      }
    }
  };

  const getCurrentValue = () => {
    if (!selectedModel) return "";
    return selectedModel;
  };

  // Get existing provider types to prevent duplicates
  const existingProviderTypes = new Set(connections.map(c => c.type))

  if (connections.length === 0) {
    if (canCreateLLMConnection) {
      return (
        <>
          <div className="flex items-center gap-1 mr-2">
            <AlertCircle className="w-3.5 h-3.5 text-yellow-500" />
            <span className="text-gray-400 text-xs italic whitespace-nowrap">
              No LLM connection configured
            </span>
          </div>
          <LLMConfig
            trigger={
              <Button
                size="sm"
                variant="outline"
                className="shrink-0 bg-brand-orange hover:bg-brand-orange/90 text-white border-brand-orange"
              >
                Add LLM Connection
              </Button>
            }
            onSuccess={onConnectionCreated}
            existingProviderTypes={existingProviderTypes}
          />
        </>
      );
    }

    // Members see this message
    return (
      <div className="flex items-center gap-1">
        <AlertCircle className="w-3.5 h-3.5 text-yellow-500" />
        <span className="text-gray-400 text-xs italic">
          No LLM connection configured, contact your admin
        </span>
      </div>
    );
  }

  return (
    <div className="relative">
      {validationWarning && (
        <div className="absolute -top-8 left-0 right-0 flex items-center gap-2 px-2 py-1 bg-yellow-900/20 border border-yellow-900/30 rounded-md text-xs text-yellow-400">
          <AlertCircle className="w-3 h-3 shrink-0" />
          <span className="truncate">{validationWarning}</span>
        </div>
      )}
      <Select value={getCurrentValue()} onValueChange={handleSelectionChange}>
        <SelectTrigger
          className={`w-auto h-9 px-3 py-2 bg-transparent hover:bg-[#2a2a2a] text-white border-[#404040] ${className} ${validationWarning ? "border-yellow-500" : ""}`}
        >
          <SelectValue placeholder="AI">
            {selectedProvider && selectedModel ? (
              <div className="flex items-center gap-2">
                {getProviderIcon(selectedProvider, sizeIcon)}
                <span className={`${sizeText}`}>
                  {formatModelName(selectedModel)}
                </span>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <Brain className={sizeIcon} />
                <span className={`${sizeText}`}>Select AI</span>
              </div>
            )}
          </SelectValue>
        </SelectTrigger>
        <SelectContent className="min-w-[200px] bg-[#2a2a2a] border-[#404040]">
          <div className="border-b border-[#404040] mb-2 pt-2">
            <button
              onClick={() => (window.location.href = "/llm-connections")}
              className={`w-full px-3 py-2 text-left ${sizeText} text-white hover:bg-[#333333] rounded-sm transition-colors`}
            >
              + Manage Connections
            </button>
          </div>
          {Object.entries(availableModels).map(([provider, models]) => {
            const connection = getConnectionForProvider(
              provider as LLMProvider,
            );
            const providerConfig = PROVIDER_CONFIGS[provider as LLMProvider];

            if (!connection || !providerConfig) return null;

            // Remove duplicates and empty strings from models array
            const uniqueModels = Array.from(new Set(models)).filter(m => m && m.trim() !== '');

            return uniqueModels.map((model, index) => {
              const isPreferred = isPreferredModel(provider, model);
              return (
                <div
                  key={`${provider}:${model}:${connection.id}:${index}`}
                  className="relative flex items-center"
                >
                  <SelectItem
                    value={model}
                    className="text-white hover:bg-[#333333] cursor-pointer flex-1 pr-10 [&>span:first-child]:hidden"
                  >
                    <div className="flex items-center gap-2 min-w-0 w-full">
                      {getProviderIcon(provider, sizeIcon)}
                      <Badge
                        variant="secondary"
                        className="text-[10px] shrink-0 bg-[#404040] text-white"
                      >
                        {providerConfig.displayName}
                      </Badge>
                      <span className={`truncate ${sizeText} flex-1`}>
                        {formatModelName(model)}
                      </span>
                    </div>
                  </SelectItem>
                  {(onSetPreferred || onClearPreferred) && (
                    <Tooltip
                      content={
                        isPreferred
                          ? "Remove as default model"
                          : "Set as default model for new notebooks"
                      }
                      side="bottom"
                      align="end"
                    >
                      <button
                        onClick={(e) => handleStarClick(e, provider, model)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-[#404040] rounded transition-colors z-10"
                      >
                        <Star
                          className={`w-3.5 h-3.5 ${
                            isPreferred
                              ? "fill-yellow-400 text-yellow-400"
                              : "text-gray-500 hover:text-yellow-400"
                          }`}
                        />
                      </button>
                    </Tooltip>
                  )}
                </div>
              );
            });
          })}
        </SelectContent>
      </Select>
    </div>
  );
}
