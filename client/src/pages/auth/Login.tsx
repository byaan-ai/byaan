import { useState } from 'react'
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { useStore } from '../../stores/useStore'
import { ApiService } from '../../services/api'
import GoogleSignInButton from '../../components/GoogleSignInButton'
import { useAppConfig } from '../../hooks/useAppConfig'

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const { login, googleLogin, authError, setAuthError, isLoading, fetchTenants, switchTenant } = useStore()

  // Check if user is coming from an invitation
  const isFromInvitation = searchParams.get('from') === 'invitation'
  const fromInvitationRegistration = location.state?.fromInvitationRegistration
  const invitationEmail = location.state?.invitationEmail
  const tenantName = location.state?.tenantName
  const registrationDisabled = location.state?.registrationDisabled
  const noAccess = location.state?.noAccess // User doesn't have access to any workspace

  const { features, isSelfHosted } = useAppConfig()

  const [email, setEmail] = useState(invitationEmail || '')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [isGoogleLoading, setIsGoogleLoading] = useState(false)
  const [isProcessingInvitation, setIsProcessingInvitation] = useState(false)

  // Get the redirect path from location state, default to home
  const from = location.state?.from?.pathname || '/'

  // Combined loading state for UI
  const isSubmitting = isLoading || isProcessingInvitation

  const handleGoogleSuccess = async (credential: string) => {
    setAuthError(null)

    setIsGoogleLoading(true)

    try {
      await googleLogin(credential)

      // Check for pending invitation (same flow as handleSubmit for email/password)
      const pendingToken = localStorage.getItem('pendingInvitationToken')

      if (pendingToken) {
        setIsProcessingInvitation(true)
        try {
          const result = await ApiService.acceptInvitation(pendingToken)
          localStorage.removeItem('pendingInvitationToken')
          localStorage.removeItem('pendingInvitationTenantName')

          if (result.tenant_id) {
            await fetchTenants()
            const updatedTenants = useStore.getState().tenants

            if (updatedTenants.length === 1) {
              setTimeout(() => navigate('/', { replace: true }), 0)
            } else {
              switchTenant(result.tenant_id)
            }
          }
          return
        } catch (error) {
          console.error('Failed to accept invitation:', error)
          setAuthError('Failed to accept invitation. Please try again.')
          localStorage.removeItem('pendingInvitationToken')
          localStorage.removeItem('pendingInvitationTenantName')
          return
        } finally {
          setIsProcessingInvitation(false)
        }
      }

      // Normal flow (no pending invitation)
      // In self-hosted mode, never navigate to /setup-workspace - just go to home
      // AuthGuard will handle the case where user has no tenants
      const tenants = useStore.getState().tenants
      if (tenants.length === 0 && !isSelfHosted) {
        navigate('/setup-workspace', { replace: true })
      } else {
        navigate(from, { replace: true })
      }
    } catch (error: any) {
      console.error('Google login failed:', error)
      // Check for 403 error (not part of organization / no invitation)
      if (error?.response?.status === 403 || error?.message?.includes('invitation only')) {
        setAuthError('You are not part of this organization. Please contact your administrator for an invitation.')
      }
    } finally {
      setIsGoogleLoading(false)
    }
  }

  const handleGoogleError = (error: string) => {
    // Check for 403/invitation error in the error message
    if (error.includes('403') || error.includes('invitation')) {
      setAuthError('You are not part of this organization. Please contact your administrator for an invitation.')
    } else {
      setAuthError(error)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setAuthError(null)


    // Check if there's a pending invitation before login
    const pendingToken = localStorage.getItem('pendingInvitationToken')

    try {
      await login(email, password)

      if (pendingToken) {
        setIsProcessingInvitation(true)

        // Accept the invitation (for both new users and existing users)
        try {
          const result = await ApiService.acceptInvitation(pendingToken)
          localStorage.removeItem('pendingInvitationToken')
          localStorage.removeItem('pendingInvitationTenantName')

          if (result.tenant_id) {
            // IMPORTANT: Set the new tenant ID BEFORE fetching tenants
            // This ensures the X-Tenant-ID header is correct for subsequent API calls
            localStorage.setItem('byaan_active_tenant', result.tenant_id)

            // Refresh tenant list to get updated workspaces
            await fetchTenants()

            const updatedTenants = useStore.getState().tenants

            // If user has only 1 tenant (new user joining via invitation)
            // Just navigate to app - no switch needed
            if (updatedTenants.length === 1) {
              // Use setTimeout to defer navigation to next tick
              setTimeout(() => {
                navigate('/', { replace: true })
              }, 0)
            } else {
              // User has multiple tenants (existing user) - switch to invited workspace
              switchTenant(result.tenant_id)
            }
          }
          return
        } catch (error) {
          console.error('Failed to accept invitation:', error)
          setAuthError('Failed to accept invitation. Please try again or contact support.')
          localStorage.removeItem('pendingInvitationToken')
          localStorage.removeItem('pendingInvitationTenantName')
          return
        } finally {
          setIsProcessingInvitation(false)
        }
      }

      // Normal flow (no pending invitation)
      const tenants = useStore.getState().tenants

      // If user has no tenants, they need to create personal workspace first
      // In self-hosted mode, never navigate to /setup-workspace - AuthGuard will handle
      if (tenants.length === 0 && !isSelfHosted) {
        navigate('/setup-workspace', {
          replace: true,
        })
        return
      }

      // Normal flow: Navigate to wherever they were trying to go
      navigate(from, { replace: true })
    } catch {
      setIsProcessingInvitation(false)
    }
  }

  return (
    <div className="flex min-h-screen w-full">
      {/* Left side - Login form */}
      <div className="flex-1 flex items-center justify-center p-8 bg-white">
        <div className="w-full max-w-[380px]">
          {/* Logo */}
          <div className="mb-10">
            <span className="text-3xl font-bold text-gray-900 tracking-tight">Byaan</span>
          </div>

          {/* Header */}
          <h1 className="text-2xl font-semibold text-gray-900 mb-2">
            {fromInvitationRegistration ? 'Welcome' : 'Welcome back'}
          </h1>
          <p className="text-gray-500 mb-8">Sign in to your account</p>

          {/* Registration success message for invited users */}
          {fromInvitationRegistration && tenantName && (
            <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-md">
              <div className="flex items-start gap-3">
                <svg className="flex-shrink-0 w-5 h-5 text-green-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-green-800">Registration Successful!</p>
                  <p className="text-sm text-green-700 mt-1">
                    Your account has been created. Sign in to access <span className="font-semibold">{tenantName}</span>.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Invitation context message for existing users */}
          {isFromInvitation && !fromInvitationRegistration && tenantName && (
            <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-md">
              <div className="flex items-start gap-3">
                <svg className="flex-shrink-0 w-5 h-5 text-blue-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 8h2a2 2 0 012 2v6a2 2 0 01-2 2h-2v4l-4-4H9a1.994 1.994 0 01-1.414-.586m0 0L11 14h4a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2v4l.586-.586z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-blue-800">Team Invitation</p>
                  <p className="text-sm text-blue-700 mt-1">
                    You've been invited to join <span className="font-semibold">{tenantName}</span>. Sign in to accept the invitation.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* No access message (redirected from AuthGuard) */}
          {noAccess && (
            <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-md">
              <div className="flex items-start gap-3">
                <svg className="flex-shrink-0 w-5 h-5 text-red-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-red-800">No Workspace Access</p>
                  <p className="text-sm text-red-700 mt-1">
                    You don't have access to any workspace. Please contact your administrator for an invitation.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Invitation-only message (self-hosted mode) */}
          {(registrationDisabled || features.invitation_only) && !fromInvitationRegistration && !isFromInvitation && !noAccess && (
            <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-md">
              <div className="flex items-start gap-3">
                <svg className="flex-shrink-0 w-5 h-5 text-blue-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-blue-800">Invitation Only</p>
                  <p className="text-sm text-blue-700 mt-1">
                    Access is by invitation only. Please contact your administrator if you need an invitation.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Error message */}
          {authError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md">
              <p className="text-sm text-red-600">{authError}</p>
            </div>
          )}

          {features.google_oauth_enabled && (
            <div className={features.local_auth_enabled ? "mb-6" : ""}>
              <GoogleSignInButton
                onSuccess={handleGoogleSuccess}
                onError={handleGoogleError}
                disabled={isSubmitting || isGoogleLoading}
              />
            </div>
          )}

          {features.local_auth_enabled && (
            <>
              {features.google_oauth_enabled && (
                <div className="flex items-center mb-6">
                  <div className="flex-1 border-t border-gray-200"></div>
                  <span className="px-4 text-sm text-gray-500">or</span>
                  <div className="flex-1 border-t border-gray-200"></div>
                </div>
              )}

              {/* Login form */}
              <form onSubmit={handleSubmit}>
            {/* Email field */}
            <div className="mb-4">
              <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1.5">
                Email
              </label>
              <div className="flex items-center gap-3 py-3 px-4 border border-gray-200 rounded-md transition-all focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/10">
                <svg
                  className="flex-shrink-0 text-gray-400 w-5 h-5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="2" y="4" width="20" height="16" rx="2" />
                  <path d="M22 6L12 13L2 6" />
                </svg>
                <input
                  id="email"
                  type="email"
                  placeholder="yours@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  disabled={isSubmitting || (isFromInvitation && !!invitationEmail) || (fromInvitationRegistration && !!invitationEmail)}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400 disabled:opacity-60"
                />
              </div>
            </div>

            {/* Password field */}
            <div className="mb-4">
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1.5">
                Password
              </label>
              <div className="flex items-center gap-3 py-3 px-4 border border-gray-200 rounded-md transition-all focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/10">
                <svg
                  className="flex-shrink-0 text-gray-400 w-5 h-5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0110 0v4" />
                </svg>
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  disabled={isSubmitting}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="text-gray-400 hover:text-gray-600"
                >
                  {showPassword ? (
                    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24" />
                      <line x1="1" y1="1" x2="23" y2="23" />
                    </svg>
                  ) : (
                    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
            </div>

            {/* Forgot password link */}
            <div className="mb-6 text-right">
              <Link
                to="/forgot-password"
                className="text-sm text-blue-600 hover:text-blue-700 hover:underline"
              >
                Forgot password?
              </Link>
            </div>

            {/* Submit button */}
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex items-center justify-center gap-2 w-full py-3 px-6 bg-brand-orange border-none rounded-md text-base font-semibold text-white cursor-pointer transition-all hover:bg-brand-orange-hover disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSubmitting ? (
                <>
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/30 border-t-white"></div>
                  {isProcessingInvitation ? 'Accepting invitation...' : 'Signing in...'}
                </>
              ) : (
                <>
                  Sign in
                  <svg
                    className="flex-shrink-0"
                    viewBox="0 0 24 24"
                    width="20"
                    height="20"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M9 18l6-6-6-6" />
                  </svg>
                </>
              )}
            </button>
              </form>
            </>
          )}

          {/* Sign up link (hidden when public registration is disabled) */}
          {features.public_registration_enabled && (
            <p className="mt-8 text-center text-sm text-gray-500">
              Don't have an account?{' '}
              <Link
                to="/register"
                className="font-medium text-blue-600 hover:text-blue-700 hover:underline"
              >
                Sign up
              </Link>
            </p>
          )}
        </div>
      </div>

      {/* Right side - Branding */}
      <div className="flex-1 hidden md:flex items-center justify-center p-8 bg-[#0f0f0f]">
        <div className="max-w-[500px] text-center">
          <p className="text-xs font-semibold tracking-[0.15em] text-brand-orange mb-6">
            AI-POWERED DATA ANALYTICS
          </p>
          <h1 className="text-5xl font-bold text-white leading-tight tracking-tight">
            Your data companion
            <br />
            for insights at scale
          </h1>
        </div>
      </div>
    </div>
  )
}
