import { useMemo } from 'react'
import { useStore } from '../stores/useStore'
import { Scopes, type ScopeType } from '../constants/scopes'
import type { FeatureFlags } from '../stores/slices/tenantSlice'

const defaultFeatures: FeatureFlags = {
  worker_features_enabled: false,
  external_sharing_enabled: false,
  notebook_import_enabled: false,
  public_registration_enabled: false,
  local_auth_enabled: true,
  invitation_only: false,
  google_oauth_enabled: false,
  team_sharing_enabled: false,
}

export function useScopes() {
  const tenants = useStore((state) => state.tenants)
  const activeTenantId = useStore((state) => state.activeTenantId)
  const user = useStore((state) => state.user)
  const activeTenant = useMemo(
    () => tenants.find(t => t.tenant_id === activeTenantId) ?? null,
    [tenants, activeTenantId]
  )

  const scopes = useMemo(() => activeTenant?.scopes ?? [], [activeTenant])
  const features = useMemo(() => activeTenant?.features ?? defaultFeatures, [activeTenant])
  const role = activeTenant?.role
  const userId = user?.id

  const isOwner = role === 'owner'
  const isAdmin = role === 'admin'
  const isMember = role === 'member'
  const isViewer = role === 'viewer'
  // canManageTeam: true only if role is owner or admin
  const canManageTeam = isOwner || isAdmin

  // Check if user has a specific scope
  const hasScope = (scope: ScopeType): boolean => {
    return scopes.includes(scope)
  }

  // Check if user has any of the given scopes
  const hasAnyScope = (...requiredScopes: ScopeType[]): boolean => {
    return requiredScopes.some((scope) => scopes.includes(scope))
  }

  // Check if user has all of the given scopes
  const hasAllScopes = (...requiredScopes: ScopeType[]): boolean => {
    return requiredScopes.every((scope) => scopes.includes(scope))
  }

  // Check if user can perform action on a resource (handles *_own scopes)
  // fullScope: e.g., 'notebook.update'
  // ownScope: e.g., 'notebook.update_own'
  // resourceOwnerId: the created_by field of the resource
  const canPerformOnResource = (
    fullScope: ScopeType,
    ownScope: ScopeType,
    resourceOwnerId: string | null | undefined
  ): boolean => {
    // If user has full scope, they can do it on any resource
    if (hasScope(fullScope)) {
      return true
    }
    // If user has own scope AND they own the resource
    if (hasScope(ownScope) && resourceOwnerId && userId === resourceOwnerId) {
      return true
    }
    return false
  }

  // Notebook-specific helpers
  const canCreateNotebook = hasScope(Scopes.NOTEBOOK_CREATE)

  const canEditNotebook = (createdBy: string | null | undefined): boolean => {
    return canPerformOnResource(Scopes.NOTEBOOK_UPDATE, Scopes.NOTEBOOK_UPDATE_OWN, createdBy)
  }

  const canDeleteNotebook = (createdBy: string | null | undefined): boolean => {
    return canPerformOnResource(Scopes.NOTEBOOK_DELETE, Scopes.NOTEBOOK_DELETE_OWN, createdBy)
  }

  // Datasource helpers (covers both connections and datasets)
  const canCreateDatasource = hasAnyScope(Scopes.CONNECTION_CREATE, Scopes.DATASET_CREATE)

  const canEditDatasource = (createdBy: string | null | undefined): boolean => {
    // User can edit if they have full connection/dataset update OR own scope + ownership
    return (
      canPerformOnResource(Scopes.CONNECTION_UPDATE, Scopes.CONNECTION_UPDATE_OWN, createdBy) ||
      canPerformOnResource(Scopes.DATASET_UPDATE, Scopes.DATASET_UPDATE_OWN, createdBy)
    )
  }

  const canDeleteDatasource = (createdBy: string | null | undefined): boolean => {
    return (
      canPerformOnResource(Scopes.CONNECTION_DELETE, Scopes.CONNECTION_DELETE_OWN, createdBy) ||
      canPerformOnResource(Scopes.DATASET_DELETE, Scopes.DATASET_DELETE_OWN, createdBy)
    )
  }

  // LLM Connection helpers
  const canCreateLLMConnection = hasScope(Scopes.LLM_CONNECTION_CREATE)

  const canEditLLMConnection = (createdBy: string | null | undefined): boolean => {
    return canPerformOnResource(Scopes.LLM_CONNECTION_UPDATE, Scopes.LLM_CONNECTION_UPDATE_OWN, createdBy)
  }

  const canDeleteLLMConnection = (createdBy: string | null | undefined): boolean => {
    return canPerformOnResource(Scopes.LLM_CONNECTION_DELETE, Scopes.LLM_CONNECTION_DELETE_OWN, createdBy)
  }

  // Folder helpers
  const canCreateFolder = hasScope(Scopes.FOLDER_CREATE)
  const canManageFolderMembers = hasScope(Scopes.FOLDER_MANAGE_MEMBERS)
  const canShareNotebookToFolder = hasScope(Scopes.FOLDER_SHARE_NOTEBOOK)

  const canEditFolder = (createdBy: string | null | undefined): boolean => {
    // User can edit if they have FOLDER_UPDATE scope or are the creator with admin/owner role
    if (hasScope(Scopes.FOLDER_UPDATE)) {
      return true
    }
    // Creator can edit their own folder if they're admin/owner
    if (createdBy && userId === createdBy && (isOwner || isAdmin)) {
      return true
    }
    return false
  }

  const canDeleteFolder = (createdBy: string | null | undefined): boolean => {
    // User can delete if they have FOLDER_DELETE scope or are the creator with admin/owner role
    if (hasScope(Scopes.FOLDER_DELETE)) {
      return true
    }
    // Creator can delete their own folder if they're admin/owner
    if (createdBy && userId === createdBy && (isOwner || isAdmin)) {
      return true
    }
    return false
  }

  // Viewer helper
  const canViewDashboards = hasScope(Scopes.VIEWER_DASHBOARD_READ)

  // Feature-gated permissions (combines feature flags with scope checks)
  const canImportNotebook = features.notebook_import_enabled && hasScope(Scopes.NOTEBOOK_CREATE)
  const canShareExternally = features.external_sharing_enabled

  return {
    scopes,
    userId,
    role,
    hasScope,
    hasAnyScope,
    hasAllScopes,
    canPerformOnResource,
    isOwner,
    isAdmin,
    isMember,
    isViewer,
    canManageTeam,
    // Notebook helpers
    canCreateNotebook,
    canEditNotebook,
    canDeleteNotebook,
    // Datasource helpers
    canCreateDatasource,
    canEditDatasource,
    canDeleteDatasource,
    // LLM Connection helpers
    canCreateLLMConnection,
    canEditLLMConnection,
    canDeleteLLMConnection,
    // Folder helpers
    canCreateFolder,
    canManageFolderMembers,
    canShareNotebookToFolder,
    canEditFolder,
    canDeleteFolder,
    // Viewer helpers
    canViewDashboards,
    // Feature flags and feature-gated permissions
    features,
    canImportNotebook,
    canShareExternally,
  }
}
