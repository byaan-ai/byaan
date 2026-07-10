import { useState, useRef, useEffect } from 'react'
import { Check, LogOut, ChevronUp, Building2, Users, Bot, MessageSquare, Clock, Key, Github, BarChart3, Sparkles } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useStore } from '../stores/useStore'
import { useScopes } from '@/hooks/useScopes'
import { useSlackConfig } from '@/hooks/useSlackConfig'
import { useSchedules } from '@/hooks/useSchedules'
import { usePendingSuggestionCount } from '../hooks/useSkillSuggestions'
import { ApiService } from '@/services/api'
import { useAppConfig } from '@/hooks/useAppConfig'
import { isTauriApp } from '@/lib/tauri-api'
import { isAnalyticsOptedOut, setAnalyticsOptedOut } from '@/lib/analyticsPreference'
import { Switch } from './ui/switch'
import { SlackIntegrationModal } from './slack/SlackIntegrationModal'
import { SchedulesPanel } from './schedules/SchedulesPanel'
import { MCPKeysModal } from './MCPKeysModal'

interface ProfileDropdownProps {
  isExpanded: boolean
  onExpandSidebar?: () => void
}

export function ProfileDropdown({ isExpanded, onExpandSidebar }: ProfileDropdownProps) {
  const { isSelfHosted } = useAppConfig()
  const user = useStore(state => state.user)
  const logout = useStore(state => state.logout)
  const tenants = useStore(state => state.tenants)
  const activeTenantId = useStore(state => state.activeTenantId)
  const switchTenant = useStore(state => state.switchTenant)
  const isLoadingTenants = useStore(state => state.isLoadingTenants)
  const { canManageTeam, isViewer } = useScopes()
  const slackConfig = useSlackConfig(isSelfHosted)
  const { isConnected: isSlackConnected, loading: slackLoading } = isSelfHosted
    ? slackConfig
    : { isConnected: false, loading: false }
  const { data: schedules = [] } = useSchedules()
  const { data: pendingSuggestionCount = 0 } = usePendingSuggestionCount()
  const navigate = useNavigate()
  const [isOpen, setIsOpen] = useState(false)
  const [slackModalOpen, setSlackModalOpen] = useState(false)
  const [schedulesPanelOpen, setSchedulesPanelOpen] = useState(false)
  const [mcpKeysModalOpen, setMcpKeysModalOpen] = useState(false)
  const isDesktop = isTauriApp()
  const [analyticsEnabled, setAnalyticsEnabled] = useState(() => !isAnalyticsOptedOut())
  const dropdownRef = useRef<HTMLDivElement>(null)
  const activeScheduleCount = schedules.filter(s => s.is_enabled).length

  const handleAnalyticsToggle = (next: boolean) => {
    setAnalyticsEnabled(next)
    setAnalyticsOptedOut(!next)
  }

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  if (!user) return null

  const getInitials = (): string => {
    if (user.full_name) {
      return user.full_name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase()
    }
    return user.email[0].toUpperCase()
  }

  const handleClick = () => {
    // If sidebar is collapsed, expand it first
    if (!isExpanded && onExpandSidebar) {
      onExpandSidebar()
      // Small delay to let sidebar expand, then open dropdown
      setTimeout(() => setIsOpen(true), 150)
    } else {
      setIsOpen(!isOpen)
    }
  }

  const handleTenantSwitch = (tenantId: string) => {
    switchTenant(tenantId)
    setIsOpen(false)
  }

  const handleLogout = async () => {
    setIsOpen(false)

    if (isSelfHosted) {
      // Self-hosted mode: use existing JWT logout
      logout()
    } else {
      // Desktop/community mode: clear localStorage (the session identifier) and reload
      // Backend call is optional (no-op) but kept for any future cleanup needs
      localStorage.removeItem('byaan_active_tenant')
      try {
        await ApiService.logout()
      } catch (error) {
        console.error('Error calling logout endpoint:', error)
      }
      // Reload to trigger WaitlistGate to show login screen
      window.location.reload()
    }
  }

  const handleTeamClick = () => {
    navigate('/team')
    setIsOpen(false)
  }

  const handleAIModelsClick = () => {
    navigate('/llm-connections')
    setIsOpen(false)
  }

  const handleSlackClick = () => {
    setSlackModalOpen(true)
    setIsOpen(false)
  }

  const handleSchedulesClick = () => {
    setSchedulesPanelOpen(true)
    setIsOpen(false)
  }

  const handleMCPKeysClick = () => {
    setMcpKeysModalOpen(true)
    setIsOpen(false)
  }

  const handleGitHubClick = () => {
    navigate('/github')
    setIsOpen(false)
  }

  const handleSkillReviewClick = () => {
    navigate('/skill-review')
    setIsOpen(false)
  }

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Profile Button */}
      <button
        onClick={handleClick}
        className={`flex items-center w-full rounded-lg hover:bg-[#333333] transition-colors ${
          isExpanded ? 'px-3 py-2 gap-3' : 'px-0.3 py-2 justify-center'
        }`}
      >
        {/* Avatar/Initials */}
        {user.avatar_url ? (
          <img
            src={user.avatar_url}
            alt=""
            className={`rounded-full flex-shrink-0 ${isExpanded ? 'w-8 h-8' : 'w-8 h-8'}`}
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className={`rounded-full bg-brand-orange/20 flex items-center justify-center text-brand-orange font-medium flex-shrink-0 ${
            isExpanded ? 'w-8 h-8 text-sm' : 'w-8 h-8 text-xs'
          }`}>
            {getInitials()}
          </div>
        )}

        {/* Name & Email (expanded only) */}
        {isExpanded && (
          <>
            <div className="flex-1 text-left min-w-0">
              <div className="text-sm font-medium text-white truncate">
                {user.full_name || user.email.split('@')[0]}
              </div>
              <div className="text-xs text-gray-400 truncate">{user.email}</div>
            </div>
            <ChevronUp className={`w-4 h-4 text-gray-400 transition-transform flex-shrink-0 ${isOpen ? '' : 'rotate-180'}`} />
          </>
        )}
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="absolute bottom-full left-0 right-0 mb-2 bg-[#2a2a2a] border border-[#555555] rounded-lg shadow-2xl z-50 min-w-[240px]">
          {/* User Info Header */}
          <div className="p-3 border-b border-[#555555]">
            <div className="flex items-center gap-3">
              {user.avatar_url ? (
                <img src={user.avatar_url} alt="" className="w-10 h-10 rounded-full flex-shrink-0" referrerPolicy="no-referrer" />
              ) : (
                <div className="w-10 h-10 rounded-full bg-brand-orange/20 flex items-center justify-center text-brand-orange font-medium flex-shrink-0">
                  {getInitials()}
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-white truncate">
                  {user.full_name || 'User'}
                </div>
                <div className="text-xs text-gray-400 truncate">{user.email}</div>
              </div>
            </div>
          </div>

          {/* Tenant Section (self-hosted mode only) */}
          {isSelfHosted && (
            <div className="py-2">
              <div className="px-3 py-1 text-xs text-gray-500 uppercase font-medium tracking-wider">
                Workspaces
              </div>
              {isLoadingTenants ? (
                <div className="px-3 py-2 text-sm text-gray-400">Loading...</div>
              ) : tenants.length === 0 ? (
                <div className="px-3 py-2 text-sm text-gray-400">No workspaces yet</div>
              ) : (
                tenants.map(tenant => (
                  <button
                    key={tenant.tenant_id}
                    onClick={() => handleTenantSwitch(tenant.tenant_id)}
                    className="w-full px-3 py-2 flex items-center gap-3 hover:bg-[#333333] transition-colors"
                  >
                    <Building2 className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    <span className="flex-1 text-left text-sm text-white truncate">
                      {tenant.tenant_name}
                    </span>
                    <span className="text-xs text-gray-500 capitalize flex-shrink-0">
                      {tenant.role}
                    </span>
                    {tenant.tenant_id === activeTenantId && (
                      <Check className="w-4 h-4 text-brand-orange flex-shrink-0" />
                    )}
                  </button>
                ))
              )}
            </div>
          )}

          {/* Settings & Logout */}
          <div className="border-t border-[#555555]">
            {!isViewer && (
              <button
                onClick={handleAIModelsClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <Bot className="w-4 h-4" />
                <span className="text-sm">AI Models</span>
              </button>
            )}
            {!isViewer && (
              <button
                onClick={handleSchedulesClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <Clock className="w-4 h-4" />
                <span className="text-sm flex-1 text-left">Schedules</span>
                {activeScheduleCount > 0 && (
                  <span className="text-xs bg-brand-orange/20 text-brand-orange px-1.5 py-0.5 rounded-full">
                    {activeScheduleCount}
                  </span>
                )}
              </button>
            )}
            {!isViewer && (
              <button
                onClick={handleMCPKeysClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <Key className="w-4 h-4" />
                <span className="text-sm">MCP</span>
              </button>
            )}
            {!isViewer && (
              <button
                onClick={handleGitHubClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <Github className="w-4 h-4" />
                <span className="text-sm">GitHub</span>
              </button>
            )}
            {!isViewer && (
              <button
                onClick={handleSkillReviewClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <Sparkles className="w-4 h-4" />
                <span className="text-sm flex-1 text-left">Skill Review</span>
                {pendingSuggestionCount > 0 && (
                  <span className="text-xs bg-brand-orange/20 text-brand-orange px-1.5 py-0.5 rounded-full">
                    {pendingSuggestionCount}
                  </span>
                )}
              </button>
            )}
            {isSelfHosted && canManageTeam && (
              <button
                onClick={handleSlackClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <MessageSquare className="w-4 h-4" />
                <span className="text-sm flex-1 text-left">Slack</span>
                {!slackLoading && (
                  <span className={`text-xs ${isSlackConnected ? 'text-green-400' : 'text-gray-500'}`}>
                    {isSlackConnected ? '● Connected' : '○ Not connected'}
                  </span>
                )}
              </button>
            )}
            {isSelfHosted && canManageTeam && (
              <button
                onClick={handleTeamClick}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-white"
              >
                <Users className="w-4 h-4" />
                <span className="text-sm">Team</span>
              </button>
            )}
            {isSelfHosted && (
              <button
                onClick={handleLogout}
                className="w-full px-3 py-2.5 flex items-center gap-3 hover:bg-[#333333] transition-colors text-red-400 hover:text-red-300"
              >
                <LogOut className="w-4 h-4" />
                <span className="text-sm">Log out</span>
              </button>
            )}
            {isDesktop && (
              <div className="px-3 py-2.5 flex items-center gap-3 border-t border-[#555555]">
                <BarChart3 className="w-4 h-4 text-gray-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white">PostHog Tracking</div>
                  <div className="text-xs text-gray-500 truncate">Help improve Byaan</div>
                </div>
                <Switch
                  size="sm"
                  checked={analyticsEnabled}
                  onCheckedChange={handleAnalyticsToggle}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {isSelfHosted && (
        <SlackIntegrationModal
          open={slackModalOpen}
          onClose={() => setSlackModalOpen(false)}
        />
      )}

      <SchedulesPanel
        open={schedulesPanelOpen}
        onClose={() => setSchedulesPanelOpen(false)}
      />

      <MCPKeysModal
        open={mcpKeysModalOpen}
        onClose={() => setMcpKeysModalOpen(false)}
      />
    </div>
  )
}
