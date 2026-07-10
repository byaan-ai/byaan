import type { StateCreator } from 'zustand'
import type { StoreState } from '../useStore'
import { ApiService } from '../../services/api'

export interface FeatureFlags {
  worker_features_enabled: boolean
  external_sharing_enabled: boolean
  notebook_import_enabled: boolean
  public_registration_enabled: boolean
  local_auth_enabled: boolean
  invitation_only: boolean
  google_oauth_enabled: boolean
  team_sharing_enabled: boolean
}

export interface TenantInfo {
  tenant_id: string
  tenant_name: string
  role: 'owner' | 'admin' | 'member' | 'viewer'
  scopes: string[]
  features?: FeatureFlags
}

export interface TenantSlice {
  tenants: TenantInfo[]
  activeTenantId: string | null
  isLoadingTenants: boolean
  tenantsFetched: boolean
  tenantError: string | null

  fetchTenants: () => Promise<void>
  switchTenant: (tenantId: string) => void
  getActiveTenant: () => TenantInfo | null
  clearTenants: () => void
}

const ACTIVE_TENANT_KEY = 'byaan_active_tenant'

const getStoredActiveTenant = (): string | null => {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(ACTIVE_TENANT_KEY)
}

const setStoredActiveTenant = (tenantId: string | null) => {
  if (typeof window === 'undefined') return
  if (tenantId) {
    localStorage.setItem(ACTIVE_TENANT_KEY, tenantId)
  } else {
    localStorage.removeItem(ACTIVE_TENANT_KEY)
  }
}

const initialState = {
  tenants: [] as TenantInfo[],
  activeTenantId: getStoredActiveTenant(),
  isLoadingTenants: false,
  tenantsFetched: false,
  tenantError: null,
}

export const createTenantSlice: StateCreator<
  StoreState,
  [],
  [],
  TenantSlice
> = (set, get) => ({
  ...initialState,

  fetchTenants: async () => {
    const { isAuthenticated } = get()
    if (!isAuthenticated) {
      return
    }

    set({ isLoadingTenants: true, tenantError: null })
    try {
      const tenants = await ApiService.getTenants()

      // Auto-select first tenant if no active tenant or stored tenant not in list
      const storedTenantId = getStoredActiveTenant()
      const storedTenantExists = tenants.some(t => t.tenant_id === storedTenantId)

      let activeTenantId = storedTenantId
      if (!storedTenantExists && tenants.length > 0) {
        activeTenantId = tenants[0].tenant_id
        setStoredActiveTenant(activeTenantId)
      }

      set({
        tenants: tenants as TenantInfo[],
        activeTenantId,
        isLoadingTenants: false,
        tenantsFetched: true
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to fetch tenants'
      set({ tenantError: message, isLoadingTenants: false, tenantsFetched: true })
      console.error('Error fetching tenants:', {
        error,
        isAuthenticated: get().isAuthenticated,
        storedTenantId: getStoredActiveTenant(),
      })
    }
  },

  switchTenant: (tenantId: string) => {
    const { tenants, activeTenantId } = get()
    const tenantExists = tenants.some(t => t.tenant_id === tenantId)

    if (tenantExists && tenantId !== activeTenantId) {
      // Update active tenant in state and localStorage
      set({ activeTenantId: tenantId })
      setStoredActiveTenant(tenantId)

      // Small delay to ensure localStorage is flushed, then reload
      setTimeout(() => {
        window.location.href = '/'
      }, 50)
    }
  },

  getActiveTenant: () => {
    const { tenants, activeTenantId } = get()
    return tenants.find(t => t.tenant_id === activeTenantId) || null
  },

  clearTenants: () => {
    set({ tenants: [], activeTenantId: null, tenantError: null, tenantsFetched: false })
    setStoredActiveTenant(null)
  },
})
