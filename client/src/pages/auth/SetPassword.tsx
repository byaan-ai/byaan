import { useState, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useStore } from '../../stores/useStore'
import { ApiService } from '../../services/api'
import { useAppConfig } from '../../hooks/useAppConfig'

export default function SetPassword() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login, fetchTenants, switchTenant } = useStore()
  const { features } = useAppConfig()

  const invitationEmail = location.state?.invitationEmail as string | undefined
  const tenantName = location.state?.tenantName as string | undefined

  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (features.google_oauth_enabled) {
      navigate('/login?from=invitation', { replace: true, state: { invitationEmail, tenantName } })
      return
    }
    const token = localStorage.getItem('pendingInvitationToken')
    if (!token || !invitationEmail) {
      navigate('/login', { replace: true })
    }
  }, [features.google_oauth_enabled, invitationEmail, tenantName, navigate])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters')
      return
    }

    const invitationToken = localStorage.getItem('pendingInvitationToken')
    if (!invitationToken || !invitationEmail) {
      setError('Missing invitation context. Please re-open the invitation link.')
      return
    }

    setIsSubmitting(true)
    try {
      await ApiService.setPasswordWithInvitation(invitationToken, password)
      await login(invitationEmail, password)
      const result = await ApiService.acceptInvitation(invitationToken)
      localStorage.removeItem('pendingInvitationToken')
      localStorage.removeItem('pendingInvitationTenantName')

      if (result.tenant_id) {
        localStorage.setItem('byaan_active_tenant', result.tenant_id)
        await fetchTenants()
        const updatedTenants = useStore.getState().tenants
        if (updatedTenants.length === 1) {
          setTimeout(() => navigate('/', { replace: true }), 0)
        } else {
          switchTenant(result.tenant_id)
        }
      } else {
        navigate('/', { replace: true })
      }
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Failed to set password')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen w-full">
      <div className="flex-1 flex items-center justify-center p-8 bg-white">
        <div className="w-full max-w-[380px]">
          <div className="mb-6">
            <span className="text-3xl font-bold text-gray-900 tracking-tight">Byaan</span>
          </div>

          <h1 className="text-2xl font-semibold text-gray-900 mb-2">Set your password</h1>
          <p className="text-gray-500 mb-4">Choose a password to finish joining the team.</p>

          <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-md">
            <p className="text-sm font-medium text-blue-800">Team Invitation</p>
            <p className="text-sm text-blue-700 mt-1">
              {tenantName ? (
                <>You've been invited to join <span className="font-semibold">{tenantName}</span>{invitationEmail ? <> as <span className="font-semibold">{invitationEmail}</span></> : null}.</>
              ) : (
                <>You're setting a password to accept a team invitation.</>
              )}
            </p>
          </div>

          {error && (
            <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded-md">
              <p className="text-sm text-red-600">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="mb-3">
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1.5">
                New password
              </label>
              <div className="flex items-center gap-3 py-3 px-4 border border-gray-200 rounded-md focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/10">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="At least 8 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  disabled={isSubmitting}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="text-sm text-gray-500 hover:text-gray-700"
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>

            <div className="mb-4">
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-700 mb-1.5">
                Confirm password
              </label>
              <div className="flex items-center gap-3 py-3 px-4 border border-gray-200 rounded-md focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/10">
                <input
                  id="confirmPassword"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Confirm your password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  disabled={isSubmitting}
                  className="flex-1 border-none outline-none text-[15px] text-gray-900 bg-transparent placeholder:text-gray-400"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="flex items-center justify-center gap-2 w-full py-3 px-6 bg-brand-orange rounded-md text-base font-semibold text-white transition-all hover:bg-brand-orange-hover disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSubmitting ? 'Setting password…' : 'Set password & continue'}
            </button>
          </form>
        </div>
      </div>

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
