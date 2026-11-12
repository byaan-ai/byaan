import { useState } from 'react'
import { AlertTriangle, Bot, Loader2 } from 'lucide-react'
import { Card } from '../ui/card'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { useHandoffRequirements } from '../../hooks/useHandoffs'
import { HandoffsApi } from '../../services/handoffs'
import { showToast } from '../../utils/toast'

export function GitHubAppCard({ canManage }: { canManage: boolean }) {
  const { data: requirements, refetch } = useHandoffRequirements('always')
  const [organization, setOrganization] = useState('')
  const [busy, setBusy] = useState(false)

  if (!requirements?.enabled) return null
  const app = requirements.github_app
  const problems = [
    !requirements.git && 'git is not installed on the Byaan host',
    !requirements.claude_cli && (requirements.claude_cli_error || 'Claude Code CLI not found'),
    !requirements.claude_auth && 'Claude Code is not connected (Settings → AI Models)',
  ].filter(Boolean) as string[]

  const createApp = async () => {
    const tabName = `byaan-github-app-${Date.now()}`
    const tab = window.open('about:blank', tabName)
    if (!tab) {
      showToast.error('Allow pop-ups for Byaan to open GitHub in a new tab')
      return
    }
    tab.opener = null
    setBusy(true)
    try {
      const { post_url, manifest } = await HandoffsApi.githubAppManifest({ organization: organization.trim() || undefined })
      const form = document.createElement('form')
      form.method = 'POST'
      form.action = post_url
      form.target = tabName
      const input = document.createElement('input')
      input.type = 'hidden'
      input.name = 'manifest'
      input.value = JSON.stringify(manifest)
      form.appendChild(input)
      document.body.appendChild(form)
      form.submit()
      form.remove()
      showToast.success('Finish creating and installing the app in the GitHub tab')
    } catch (error) {
      tab.close()
      showToast.error(error instanceof Error ? error.message : 'Could not start GitHub App setup')
    } finally {
      setBusy(false)
    }
  }

  const removeApp = async () => {
    setBusy(true)
    try {
      await HandoffsApi.deleteGithubApp()
      await refetch()
    } catch (error) {
      showToast.error(error instanceof Error ? error.message : 'Could not remove the GitHub App')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="bg-[#1a1a1a] border-gray-800 p-5 space-y-3 text-sm">
      <div className="flex items-center gap-2 text-white font-medium">
        <Bot className="w-4 h-4" /> Handoffs to Claude Code
      </div>
      {problems.map((problem) => (
        <div key={problem} className="flex items-center gap-2 text-yellow-400 text-xs">
          <AlertTriangle className="w-3 h-3" /> {problem}
        </div>
      ))}
      {!requirements.os_isolation && (
        <div className="text-xs text-gray-400">
          Claude runs as the same OS user as Byaan on this host. Docker deployments isolate it in a separate user.
        </div>
      )}
      {app.configured ? (
        <div className="flex items-center justify-between gap-3">
          <span className="text-gray-300">
            PRs are opened as <code>{app.slug}[bot]</code>.{' '}
            {app.install_url && (
              <a className="text-brand-orange" href={app.install_url} target="_blank" rel="noreferrer">
                Choose repositories
              </a>
            )}
          </span>
          {canManage && (
            <Button size="sm" variant="ghost" className="text-red-400" onClick={removeApp} disabled={busy}>
              Remove
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-gray-400 text-xs">
            Without a Byaan GitHub App, handoff PRs are opened with the connected GitHub account. Create one so PRs show
            as a Byaan bot. It can push branches and open PRs, never merge.
          </p>
          {canManage && (
            <div className="flex gap-2">
              <Input placeholder="GitHub organization (optional)" value={organization} onChange={(e) => setOrganization(e.target.value)} />
              <Button size="sm" variant="brand-primary" onClick={createApp} disabled={busy}>
                {busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Create Byaan GitHub App
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
