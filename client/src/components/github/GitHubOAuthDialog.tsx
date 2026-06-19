import { useState, useEffect, useRef } from 'react'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/dialog'
import { Input } from '../ui/input'
import { Loader2, ExternalLink, Check, Github, KeyRound, Lock, Copy, CheckCheck } from 'lucide-react'
import { openExternalUrl, isTauriApp } from '../../lib/tauri-api'
import { GitHubService } from '../../services/github'

interface GitHubOAuthDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: () => void
  oauthAvailable: boolean
}

type OAuthStep = 'idle' | 'device_flow' | 'success' | 'error'

export function GitHubOAuthDialog({ open, onOpenChange, onSuccess, oauthAvailable }: GitHubOAuthDialogProps) {
  const [step, setStep] = useState<OAuthStep>('idle')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [authMethod, setAuthMethod] = useState<'oauth' | 'pat_classic' | 'pat_fine_grained'>(
    oauthAvailable ? 'oauth' : 'pat_classic',
  )
  const [patToken, setPatToken] = useState('')
  const [patLoading, setPatLoading] = useState(false)
  const [userCode, setUserCode] = useState('')
  const [verificationUri, setVerificationUri] = useState('https://github.com/login/device')
  const [copied, setCopied] = useState(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    setAuthMethod(oauthAvailable ? 'oauth' : 'pat_classic')
  }, [oauthAvailable])

  useEffect(() => {
    return () => { if (pollRef.current) clearTimeout(pollRef.current) }
  }, [])

  const resetDialog = () => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null }
    setStep('idle')
    setError('')
    setUserCode('')
    setCopied(false)
    setPatToken('')
    setAuthMethod(oauthAvailable ? 'oauth' : 'pat_classic')
  }

  const handleClose = (newOpen: boolean) => {
    if (!newOpen) resetDialog()
    onOpenChange(newOpen)
  }

  const startDeviceFlow = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await GitHubService.startDeviceFlow()
      setUserCode(data.user_code)
      setVerificationUri(data.verification_uri)
      setStep('device_flow')

      let intervalMs = data.interval * 1000

      const schedulePoll = () => {
        pollRef.current = setTimeout(async () => {
          try {
            const result = await GitHubService.pollDeviceToken(data.device_code)
            if (result.status === 'success') {
              pollRef.current = null
              setStep('success')
              onSuccess?.()
              return
            }
            if (result.status === 'slow_down') {
              intervalMs = Math.min(intervalMs + 5000, 30000)
            } else if (result.status === 'denied') {
              setError('Authorization denied.')
              setStep('error')
              return
            } else if (result.status === 'expired') {
              setError('Code expired. Please try again.')
              setStep('error')
              return
            }
            schedulePoll()
          } catch {
            schedulePoll()
          }
        }, intervalMs)
      }

      schedulePoll()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start device flow')
      setStep('error')
    } finally {
      setLoading(false)
    }
  }

  const connectWithPAT = async () => {
    setPatLoading(true)
    setError('')
    try {
      await GitHubService.connectWithPAT(patToken)
      setStep('success')
      onSuccess?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect with PAT')
      setStep('error')
    } finally {
      setPatLoading(false)
    }
  }

  const copyUserCode = async () => {
    await navigator.clipboard.writeText(userCode)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const openVerificationUrl = () => {
    if (isTauriApp()) {
      openExternalUrl(verificationUri)
    } else {
      window.open(verificationUri, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg bg-[#2a2a2a] border-[#444444]">
        <DialogHeader>
          <DialogTitle className="text-white flex items-center gap-2">
            <Github className="w-5 h-5" />
            {step === 'success' ? 'GitHub Connected' : 'Connect GitHub'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {step === 'idle' && (
            <>
              <p className="text-sm text-gray-400">
                Connect your GitHub account to analyze repositories and build codebase skills.
              </p>

              <div className="flex gap-1 bg-[#1a1a1a] rounded-lg p-1">
                {oauthAvailable && (
                  <button
                    onClick={() => setAuthMethod('oauth')}
                    className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm transition-colors ${
                      authMethod === 'oauth'
                        ? 'bg-[#3a3a3a] text-white'
                        : 'text-gray-400 hover:text-gray-300'
                    }`}
                  >
                    <Github className="w-4 h-4" />
                    OAuth
                  </button>
                )}
                <button
                  onClick={() => setAuthMethod('pat_classic')}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm transition-colors ${
                    authMethod === 'pat_classic'
                      ? 'bg-[#3a3a3a] text-white'
                      : 'text-gray-400 hover:text-gray-300'
                  }`}
                >
                  <KeyRound className="w-4 h-4" />
                  Classic PAT
                </button>
                <button
                  onClick={() => setAuthMethod('pat_fine_grained')}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm transition-colors ${
                    authMethod === 'pat_fine_grained'
                      ? 'bg-[#3a3a3a] text-white'
                      : 'text-gray-400 hover:text-gray-300'
                  }`}
                >
                  <KeyRound className="w-4 h-4" />
                  Fine-grained
                </button>
              </div>

              {authMethod === 'oauth' && oauthAvailable ? (
                <>
                  <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-3">
                    <div className="flex items-center gap-1.5 mb-2">
                      <Lock className="w-3 h-3 text-gray-500" />
                      <span className="text-xs text-gray-500">Permissions requested:</span>
                    </div>
                    <ul className="text-xs text-gray-500 space-y-1 ml-[18px]">
                      <li><span className="text-gray-400 font-mono">repo</span> — Full access to public and private repositories <span className="text-gray-600">(required)</span></li>
                      <li><span className="text-gray-400 font-mono">read:user</span> — Read your GitHub profile info <span className="text-gray-600">(optional)</span></li>
                    </ul>
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <Button variant="outline" onClick={() => handleClose(false)} className="border-[#555555] text-white hover:bg-[#3a3a3a]">
                      Cancel
                    </Button>
                    <Button onClick={startDeviceFlow} disabled={loading} className="bg-brand-orange hover:bg-brand-orange/90 flex items-center gap-2">
                      {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                      <Github className="w-4 h-4" />
                      Authorize with GitHub
                    </Button>
                  </div>
                </>
              ) : (
                <div className="space-y-3">
                  <Input
                    type="password"
                    value={patToken}
                    onChange={(e) => setPatToken(e.target.value)}
                    placeholder={
                      authMethod === 'pat_fine_grained'
                        ? 'github_pat_11ABCDEFG0xxxxxxxxxxxx'
                        : 'ghp_xxxxxxxxxxxxxxxxxxxx'
                    }
                    className="bg-[#1a1a1a] border-gray-700 text-white font-mono text-sm"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && patToken.trim()) connectWithPAT()
                    }}
                  />
                  {authMethod === 'pat_fine_grained' ? (
                    <>
                      <p className="text-xs text-gray-500">
                        Create a token at{' '}
                        <a
                          href="https://github.com/settings/personal-access-tokens"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-brand-orange hover:underline"
                        >
                          github.com/settings/personal-access-tokens
                        </a>
                        {' '}with these Repository permissions:
                      </p>
                      <ul className="text-xs text-gray-500 space-y-1 mt-1 ml-3">
                        <li><span className="text-gray-400 font-mono">Contents</span> — Read-only <span className="text-gray-600">(required)</span></li>
                        <li><span className="text-gray-400 font-mono">Metadata</span> — Read-only <span className="text-gray-600">(required, auto-selected)</span></li>
                      </ul>
                      <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-3 space-y-2">
                        <p className="text-xs text-gray-400 font-medium">Setup checklist</p>
                        <ul className="text-xs text-gray-500 space-y-1.5 list-disc ml-4">
                          <li>
                            <span className="text-gray-400">Resource owner</span> — choose your personal account for personal repos, or the organization for org repos.
                          </li>
                          <li>
                            <span className="text-gray-400">Repository access</span> — pick &quot;Only select repositories&quot; (or &quot;All repositories&quot;) and tick the repos you want Byaan to see. Only those will appear in the repo list.
                          </li>
                          <li>
                            <span className="text-gray-400">Repository permissions</span> — you must explicitly tick <span className="font-mono text-gray-400">Contents</span> Read-only. If the token page later shows &quot;does not have any repository permissions,&quot; nothing was selected — edit the token and try again.
                          </li>
                          <li>
                            <span className="text-gray-400">Org tokens need approval</span> — when the resource owner is an organization, the token is <span className="text-brand-orange">pending</span> until an org admin approves it under <span className="font-mono text-gray-400">Settings → Third-party Access → Personal access tokens → Pending requests</span>. Until then it has zero access. The org must also have fine-grained PATs allowed in its policy.
                          </li>
                        </ul>
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="text-xs text-gray-500">
                        Create a token at{' '}
                        <a
                          href="https://github.com/settings/tokens"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-brand-orange hover:underline"
                        >
                          github.com/settings/tokens
                        </a>
                        {' '}with these scopes:
                      </p>
                      <ul className="text-xs text-gray-500 space-y-1 mt-1 ml-3">
                        <li><span className="text-gray-400 font-mono">repo</span> — Full access to public and private repositories <span className="text-gray-600">(required)</span></li>
                        <li><span className="text-gray-400 font-mono">read:user</span> — Read your GitHub profile info <span className="text-gray-600">(optional)</span></li>
                      </ul>
                    </>
                  )}
                  <div className="flex justify-end gap-2 pt-1">
                    <Button variant="outline" onClick={() => handleClose(false)} className="border-[#555555] text-white hover:bg-[#3a3a3a]">
                      Cancel
                    </Button>
                    <Button
                      onClick={connectWithPAT}
                      disabled={patLoading || !patToken.trim()}
                      className="bg-brand-orange hover:bg-brand-orange/90 flex items-center gap-2"
                    >
                      {patLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                      Connect
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}

          {step === 'device_flow' && (
            <div className="space-y-4">
              <p className="text-sm text-gray-400">
                Enter this code on GitHub to authorize Byaan:
              </p>
              <div className="flex items-center gap-2">
                <div className="flex-1 bg-[#1a1a1a] border border-gray-700 rounded-lg px-4 py-3 text-center">
                  <span className="text-2xl font-mono font-bold tracking-[0.25em] text-white">{userCode}</span>
                </div>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={copyUserCode}
                  className="border-[#555555] text-white hover:bg-[#3a3a3a] shrink-0"
                  title="Copy code"
                >
                  {copied ? <CheckCheck className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                </Button>
              </div>
              <Button
                onClick={openVerificationUrl}
                className="w-full bg-[#1a1a1a] hover:bg-[#2a2a2a] border border-gray-700 text-white flex items-center justify-center gap-2"
              >
                <ExternalLink className="w-4 h-4" />
                Open github.com/login/device
              </Button>
              <div className="flex items-center gap-2 text-center justify-center">
                <Loader2 className="w-4 h-4 animate-spin text-brand-orange" />
                <p className="text-xs text-gray-500">Waiting for authorization...</p>
              </div>
              <div className="flex justify-end pt-1">
                <Button variant="outline" onClick={() => handleClose(false)} className="border-[#555555] text-white hover:bg-[#3a3a3a]">
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {step === 'success' && (
            <>
              <div className="p-4 bg-green-900/20 border border-green-500 rounded-md text-center">
                <Check className="w-12 h-12 text-green-400 mx-auto mb-3" />
                <p className="text-green-400 font-medium mb-2">GitHub connected successfully!</p>
                <p className="text-sm text-gray-400">You can now connect and analyze repositories.</p>
              </div>
              <div className="flex justify-end pt-2">
                <Button onClick={() => handleClose(false)} className="bg-brand-orange hover:bg-brand-orange/90">Done</Button>
              </div>
            </>
          )}

          {step === 'error' && (
            <>
              <div className="p-4 bg-red-900/20 border border-red-500 rounded-md text-center">
                <p className="text-red-400 font-medium mb-2">Connection Failed</p>
                <p className="text-sm text-gray-400">{error || 'An error occurred.'}</p>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => handleClose(false)} className="border-[#555555] text-white hover:bg-[#3a3a3a]">Cancel</Button>
                <Button onClick={() => { resetDialog() }} className="bg-brand-orange hover:bg-brand-orange/90">Try Again</Button>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
