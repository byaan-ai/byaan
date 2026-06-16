import { useState, useEffect } from 'react'
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { useStore } from '../../stores/useStore'
import { ApiService } from '../../services/api'
import GoogleSignInButton from '../../components/GoogleSignInButton'
import { useAppConfig } from '../../hooks/useAppConfig'

export default function Register() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const { register, googleLogin, authError, setAuthError, isLoading, isAuthenticated, fetchTenants, switchTenant } = useStore()

  // Check if user is coming from an invitation
  const isFromInvitation = searchParams.get('from') === 'invitation'
  const invitationEmail = location.state?.invitationEmail
  const tenantName = location.state?.tenantName

  const { features, isLoading: isConfigLoading, isSelfHosted } = useAppConfig()

  // State declarations
  const [email, setEmail] = useState(invitationEmail || '')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [isGoogleLoading, setIsGoogleLoading] = useState(false)
  const [isProcessingInvitation, setIsProcessingInvitation] = useState(false)

  // Redirect if already authenticated
  useEffect(() => {
    if (isAuthenticated && !isProcessingInvitation) {
      navigate('/', { replace: true })
    }
  }, [isAuthenticated, isProcessingInvitation, navigate])

  // Redirect if public registration is disabled (unless from invitation)
  useEffect(() => {
    if (isFromInvitation || isConfigLoading) return

    if (!features.public_registration_enabled) {
      navigate('/login', {
        replace: true,
        state: { registrationDisabled: true }
      })
    }
  }, [isFromInvitation, isConfigLoading, features.public_registration_enabled, navigate])

  const handleGoogleSuccess = async (credential: string) => {
    setAuthError(null)
    setLocalError(null)
    setIsGoogleLoading(true)

    try {
      const pendingToken = localStorage.getItem('pendingInvitationToken')

      if (pendingToken) {
        setIsProcessingInvitation(true)
      }

      await googleLogin(credential)

      if (pendingToken) {
        try {
          const result = await ApiService.acceptInvitation(pendingToken)
          localStorage.removeItem('pendingInvitationToken')
          localStorage.removeItem('pendingInvitationTenantName')

          if (result.tenant_id) {
            // IMPORTANT: Set the new tenant ID BEFORE fetching tenants
            localStorage.setItem('byaan_active_tenant', result.tenant_id)

            await fetchTenants()

            const updatedTenants = useStore.getState().tenants

            // Just navigate to app - no workspace creation needed
            if (updatedTenants.length === 1) {
              setTimeout(() => {
                navigate('/', { replace: true })
              }, 0)
            } else {
              // User has multiple tenants - switch to invited workspace
              switchTenant(result.tenant_id)
            }
          }
          return
        } catch (error) {
          console.error('Failed to accept invitation:', error)
          setLocalError('Failed to accept invitation. Please try again.')
          localStorage.removeItem('pendingInvitationToken')
          localStorage.removeItem('pendingInvitationTenantName')
          return
        } finally {
          setIsProcessingInvitation(false)
        }
      }

      // Normal flow (no pending invitation)
      // In self-hosted mode, never navigate to /setup-workspace
      const tenants = useStore.getState().tenants
      if (tenants.length === 0 && !isSelfHosted) {
        navigate('/setup-workspace', { replace: true })
      } else {
        navigate('/', { replace: true })
      }
    } catch (error: any) {
      console.error('Google signup failed:', error)
      setIsProcessingInvitation(false)
    } finally {
      setIsGoogleLoading(false)
    }
  }

  const handleGoogleError = (error: string) => {
    setLocalError(error)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setAuthError(null)
    setLocalError(null)

    // Validate passwords match
    if (password !== confirmPassword) {
      setLocalError('Passwords do not match')
      return
    }

    // Validate password strength
    if (password.length < 8) {
      setLocalError('Password must be at least 8 characters')
      return
    }

    // Validate full name is provided
    if (!fullName.trim()) {
      setLocalError('Full name is required')
      return
    }

    try {
      // If coming from invitation, use invitation registration endpoint
      if (isFromInvitation) {
        const invitationToken = localStorage.getItem('pendingInvitationToken')
        if (invitationToken) {
          await ApiService.authRegisterWithInvitation(email, password, fullName, invitationToken)
          // Redirect to login (user is already verified, no email check needed)
          navigate('/login?from=invitation', {
            state: {
              fromInvitationRegistration: true,
              invitationEmail: email,
              tenantName
            }
          })
          return
        }
      }

      // Normal registration flow — register() auto-logs in after creating account
      await register(email, password, fullName)
      const tenants = useStore.getState().tenants
      if (tenants.length === 0 && !isSelfHosted) {
        navigate('/setup-workspace', { replace: true })
      } else {
        navigate('/', { replace: true })
      }
    } catch (error: any) {
      setAuthError(error instanceof Error ? error.message : 'Registration failed')
    }
  }

  const displayError = localError || authError

  return (
    <div className="flex min-h-screen w-full">
      {/* Left side - Register form */}
      <div className="flex-1 flex items-center justify-center p-8 bg-white">
        <div className="w-full max-w-[380px]">
          {/* Logo */}
          <div className={isFromInvitation ? "mb-6" : "mb-10"}>
            <span className="text-3xl font-bold text-gray-900 tracking-tight">Byaan</span>
          </div>

          {/* Header */}
          <h1 className="text-2xl font-semibold text-gray-900 mb-2">Create your account</h1>
          <p className={`text-gray-500 ${isFromInvitation ? "mb-4" : "mb-8"}`}>Start analyzing your data with AI</p>

          {/* Invitation context message */}
          {isFromInvitation && (
            <div className="mb-3 p-4 bg-blue-50 border border-blue-200 rounded-md">
              <div className="flex items-start gap-3">
                <svg className="flex-shrink-0 w-5 h-5 text-blue-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 8h2a2 2 0 012 2v6a2 2 0 01-2 2h-2v4l-4-4H9a1.994 1.994 0 01-1.414-.586m0 0L11 14h4a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2v4l.586-.586z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-blue-800">Team Invitation</p>
                  <p className="text-sm text-blue-700 mt-1">
                    {tenantName ? (
                      <>You've been invited to join <span className="font-semibold">{tenantName}</span>.</>
                    ) : (
                      <>You're creating an account to accept a team invitation.</>
                    )}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Error message */}
          {displayError && (
            <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded-md">
              <p className="text-sm text-red-600">{displayError}</p>
            </div>
          )}

          {features.google_oauth_enabled && (
            <div className={features.local_auth_enabled ? (isFromInvitation ? "mb-4" : "mb-6") : ""}>
              <GoogleSignInButton
                onSuccess={handleGoogleSuccess}
                onError={handleGoogleError}
                disabled={isLoading || isGoogleLoading}
              />
            </div>
          )}

          {features.local_auth_enabled && (
            <>
              {features.google_oauth_enabled && (
                <div className={`flex items-center ${isFromInvitation ? "mb-4" : "mb-6"}`}>
                  <div className="flex-1 border-t border-gray-200"></div>
                  <span className="px-4 text-sm text-gray-500">or</span>
                  <div className="flex-1 border-t border-gray-200"></div>
                </div>
              )}

              {/* Register form */}
              <form onSubmit={handleSubmit}>
            {/* Full name field */}
            <div className={isFromInvitation ? "mb-3" : "mb-4"}>
              <label htmlFor="fullName" className="block text-sm font-medium text-gray-700 mb-1.5">
                Full name
              </label>
              <div className="flex items-center gap-3 py-3 px-4 border border-gray-200 rounded-md transition-all focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/10">
                <svg
                  className="flex-shrink-0 text-gray-400 w-5 h-5"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
                <input
                  id="fullName"
                  type="text"
                  placeholder="John Doe"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  required
                  disabled={isLoading}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400"
                />
              </div>
            </div>

            {/* Email field */}
            <div className={isFromInvitation ? "mb-3" : "mb-4"}>
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
                  disabled={isLoading || (isFromInvitation && !!invitationEmail)}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400 disabled:opacity-60"
                />
              </div>
            </div>

            {/* Password field */}
            <div className={isFromInvitation ? "mb-3" : "mb-4"}>
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
                  placeholder="At least 8 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  disabled={isLoading}
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

            {/* Confirm password field */}
            <div className={isFromInvitation ? "mb-3" : "mb-6"}>
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-700 mb-1.5">
                Confirm password
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
                  id="confirmPassword"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Confirm your password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  disabled={isLoading}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400"
                />
              </div>
            </div>

            {/* Terms */}
            <p className={`text-xs text-gray-500 ${isFromInvitation ? "mb-3" : "mb-4"}`}>
              By signing up, you agree to our{' '}
              <a href="#" className="text-blue-600 hover:underline">
                Terms of Service
              </a>{' '}
              and{' '}
              <a href="#" className="text-blue-600 hover:underline">
                Privacy Policy
              </a>
              .
            </p>

            {/* Submit button */}
            <button
              type="submit"
              disabled={isLoading}
              className="flex items-center justify-center gap-2 w-full py-3 px-6 bg-brand-orange border-none rounded-md text-base font-semibold text-white cursor-pointer transition-all hover:bg-brand-orange-hover disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <>
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/30 border-t-white"></div>
                  Creating account...
                </>
              ) : (
                <>
                  Create account
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

          {/* Sign in link */}
          <p className={`${isFromInvitation ? "mt-4" : "mt-8"} text-center text-sm text-gray-500`}>
            Already have an account?{' '}
            <Link
              to="/login"
              className="font-medium text-blue-600 hover:text-blue-700 hover:underline"
            >
              Sign in
            </Link>
          </p>
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
