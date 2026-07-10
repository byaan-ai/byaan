import { useQuery } from '@tanstack/react-query'

type DeploymentMode = 'local' | 'self_hosted'

interface FeatureFlags {
  worker_features_enabled: boolean
  external_sharing_enabled: boolean
  notebook_import_enabled: boolean
  public_registration_enabled: boolean
  local_auth_enabled: boolean
  invitation_only: boolean
  google_oauth_enabled: boolean
  enterprise_licensed: boolean
  team_sharing_enabled: boolean
}

interface CommunityBootstrap {
  user_id: string
  email: string
  full_name: string | null
  tenant_id: string
}

interface AppConfig {
  features: FeatureFlags
  org_name?: string
  community_bootstrap?: CommunityBootstrap
}

const defaultFeatures: FeatureFlags = {
  worker_features_enabled: false,
  external_sharing_enabled: false,
  notebook_import_enabled: false,
  public_registration_enabled: false,
  local_auth_enabled: true,
  invitation_only: false,
  google_oauth_enabled: false,
  enterprise_licensed: false,
  team_sharing_enabled: false,
}

async function fetchAppConfig(): Promise<AppConfig | null> {
  try {
    const response = await fetch('/api/app/config')
    const data = await response.json()
    if (data.success && data.data) {
      return data.data as AppConfig
    }
  } catch {
    // Ignore errors - return null
  }
  return null
}

export function useAppConfig() {
  const { data: config, isLoading } = useQuery({
    queryKey: ['appConfig'],
    queryFn: fetchAppConfig,
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: 'always',
  })

  const features = config?.features ?? defaultFeatures

  // Derive deployment mode from features
  const isSelfHosted = features.team_sharing_enabled
  const deploymentMode: DeploymentMode = isSelfHosted ? 'self_hosted' : 'local'

  return {
    config,
    isLoading,
    deploymentMode,
    isLocal: !isSelfHosted,
    isSelfHosted,
    features,
    orgName: config?.org_name,
  }
}
