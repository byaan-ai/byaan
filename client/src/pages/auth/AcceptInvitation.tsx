import { useEffect, useState, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { ApiService } from '../../services/api'
import { useStore } from '../../stores/useStore'
import { useAppConfig } from '../../hooks/useAppConfig'

export default function AcceptInvitation() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { fetchTenants, switchTenant, user, logout } = useStore()
  const { features } = useAppConfig()
  const [status, setStatus] = useState<'loading' | 'success' | 'error' | 'redirecting' | 'email_mismatch'>('loading')
  const [errorMessage, setErrorMessage] = useState('')
  const [invitationInfo, setInvitationInfo] = useState<{
    email: string
    tenant_name: string
    role: string
    user_exists: boolean
  } | null>(null)
  const hasProcessed = useRef(false)

  useEffect(() => {
    if (hasProcessed.current) return

    const token = searchParams.get('token')

    if (!token) {
      setStatus('error')
      setErrorMessage('Invalid invitation link - no token provided')
      return
    }

    hasProcessed.current = true
    handleInvitation(token)
  }, [searchParams])

  const handleInvitation = async (token: string) => {
    try {
      setStatus('loading')

      // Verify invitation first to get invitation details
      const invitationData = await ApiService.verifyInvitation(token)
      setInvitationInfo({
        email: invitationData.email,
        tenant_name: invitationData.tenant_name,
        role: invitationData.role,
        user_exists: invitationData.user_exists,
      })

      // Get current auth state from store
      const currentUser = useStore.getState().user
      const currentIsAuthenticated = useStore.getState().isAuthenticated

      // If user is already authenticated, check if email matches
      if (currentIsAuthenticated && currentUser) {
        if (currentUser.email.toLowerCase() !== invitationData.email.toLowerCase()) {
          setStatus('email_mismatch')
          return
        }

        await acceptInvitationDirectly(token)
        return
      }

      // If not authenticated, store token and redirect to login/register
      localStorage.setItem('pendingInvitationToken', token)
      localStorage.setItem('pendingInvitationTenantName', invitationData.tenant_name)

      // Determine where to redirect based on user existence
      setStatus('redirecting')

      const target = invitationData.user_exists
        ? (features.google_oauth_enabled ? '/login?from=invitation' : '/set-password?from=invitation')
        : '/register?from=invitation'
      setTimeout(() => {
        navigate(target, {
          state: {
            invitationEmail: invitationData.email,
            tenantName: invitationData.tenant_name
          }
        })
      }, 1500)
    } catch (error) {
      setStatus('error')
      setErrorMessage(error instanceof Error ? error.message : 'Failed to process invitation')
      localStorage.removeItem('pendingInvitationToken')
      localStorage.removeItem('pendingInvitationTenantName')
    }
  }

  const acceptInvitationDirectly = async (token: string) => {
    try {
      setStatus('loading')

      const result = await ApiService.acceptInvitation(token)

      // Successfully accepted - refresh tenants and switch
      if (result.tenant_id) {
        localStorage.removeItem('pendingInvitationToken')
        localStorage.removeItem('pendingInvitationTenantName')

        // IMPORTANT: Set the new tenant ID in localStorage BEFORE fetching tenants
        // This ensures the X-Tenant-ID header is correct for subsequent API calls
        localStorage.setItem('byaan_active_tenant', result.tenant_id)

        // Refresh tenant list
        await fetchTenants()

        // Show success message briefly before switching
        setStatus('success')

        // Wait a bit for user to see the success message, then switch tenant
        // switchTenant will handle the navigation via hard reload
        setTimeout(() => {
          switchTenant(result.tenant_id)
        }, 1000)
      } else {
        throw new Error('No tenant ID returned from invitation acceptance')
      }
    } catch (error) {
      setStatus('error')
      setErrorMessage(error instanceof Error ? error.message : 'Failed to accept invitation')

      // If error is about needing to register, redirect to register
      if (error instanceof Error && error.message.toLowerCase().includes('register')) {
        localStorage.setItem('pendingInvitationToken', token)
        setTimeout(() => {
          navigate('/register?from=invitation')
        }, 2000)
      } else {
        localStorage.removeItem('pendingInvitationToken')
        localStorage.removeItem('pendingInvitationTenantName')
      }
    }
  }

  return (
    <div className="min-h-screen bg-[#0d0d0d] flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-[#1a1a1a] border border-gray-800 rounded-lg p-8">
        {status === 'loading' && (
          <div className="text-center">
            <Loader2 className="w-12 h-12 animate-spin text-brand-orange mx-auto mb-4" />
            <h2 className="text-xl font-semibold text-white mb-2">Processing Invitation</h2>
            <p className="text-gray-400">Please wait while we verify your invitation...</p>
          </div>
        )}

        {status === 'redirecting' && (
          <div className="text-center">
            <Loader2 className="w-12 h-12 animate-spin text-brand-orange mx-auto mb-4" />
            <h2 className="text-xl font-semibold text-white mb-2">Invitation Verified!</h2>
            {invitationInfo && (
              <>
                <p className="text-gray-400 mb-4">
                  You've been invited to join <span className="text-white font-semibold">{invitationInfo.tenant_name}</span> as a {invitationInfo.role}.
                </p>
                <p className="text-gray-500 text-sm">Redirecting you to complete the process...</p>
              </>
            )}
          </div>
        )}

        {status === 'success' && (
          <div className="text-center">
            <div className="w-16 h-16 bg-green-500/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-white mb-2">Invitation Accepted!</h2>
            <p className="text-gray-400">Switching to your new workspace...</p>
          </div>
        )}

        {status === 'email_mismatch' && invitationInfo && (
          <div className="text-center">
            <div className="w-16 h-16 bg-yellow-500/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-yellow-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <p className="text-gray-400 mb-2">
              This invitation is for <span className="text-white font-semibold">{invitationInfo.email}</span>
            </p>
            <p className="text-gray-400 mb-6">
              You're currently signed in as <span className="text-white font-semibold">{user?.email}</span>
            </p>
            <div className="flex flex-col gap-3 w-full max-w-xs mx-auto">
              <button
                onClick={() => {
                  const token = searchParams.get('token')

                  logout()

                  if (token) {
                    localStorage.setItem('pendingInvitationToken', token)
                    localStorage.setItem('pendingInvitationTenantName', invitationInfo.tenant_name)
                  }

                  setTimeout(() => {
                    const target = invitationInfo.user_exists
                      ? (features.google_oauth_enabled ? '/login?from=invitation' : '/set-password?from=invitation')
                      : '/register?from=invitation'
                    navigate(target, {
                      state: {
                        invitationEmail: invitationInfo.email,
                        tenantName: invitationInfo.tenant_name
                      }
                    })
                  }, 100)
                }}
                className="w-full px-6 py-3 bg-brand-orange text-white rounded-md hover:bg-brand-orange/90 transition-colors font-medium"
              >
                Logout & Accept Invite
              </button>
              <button
                onClick={() => navigate('/')}
                className="w-full px-6 py-3 bg-gray-700 text-white rounded-md hover:bg-gray-600 transition-colors font-medium"
              >
                Go to Dashboard
              </button>
            </div>
          </div>
        )}

        {status === 'error' && (
          <div className="text-center">
            <div className="w-16 h-16 bg-red-500/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-white mb-2">Unable to Process Invitation</h2>
            <p className="text-gray-400 mb-6">{errorMessage}</p>
            <button
              onClick={() => navigate('/login')}
              className="px-4 py-2 bg-brand-orange text-white rounded-md hover:bg-brand-orange/90 transition-colors"
            >
              Go to Login
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
