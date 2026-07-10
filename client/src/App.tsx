import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { ToastContainer } from 'react-toastify'
import Layout from './components/Layout'
import ChatPreview from './pages/ChatPreview'
import LLMConnectionsPage from './pages/LLMConnections'
import DatabasesPage from './pages/Databases'
import TeamMembersPage from './pages/TeamMembers'
import NotebooksPage from './pages/NotebooksPage'
import SkillReviewPage from './pages/SkillReviewPage'
import HomePage from './pages/HomePage'
import FoldersPage from './pages/FoldersPage'
import FolderDetailPage from './pages/FolderDetailPage'
import GitHubIntegrations from './pages/GitHubIntegrations'
import { Login, Register, ForgotPassword, ResetPassword, CheckEmail, AcceptInvitation, SetPassword } from './pages/auth'
import SetupWorkspace from './pages/auth/SetupWorkspace'
import { AuthGuard } from './components/AuthGuard'
import { RoleGuard } from './components/RoleGuard'
import { ViewerRedirect } from './components/ViewerRedirect'
import { DownloadNotification } from './components/DownloadNotification'
import { UpdatePopup } from './components/UpdatePopup'
import { WaitlistGate } from './components/WaitlistGate'
import { isTauriApp } from './lib/tauri-api'
import { useAppUpdater } from './hooks/useAppUpdater'
import { useAppConfig } from './hooks/useAppConfig'
import { useStore } from './stores/useStore'
import './App.css'
import 'react-toastify/dist/ReactToastify.css'

function App() {
  const { available, update, downloading, installUpdate, dismissUpdate } = useAppUpdater()
  const loadPreferencesFromBackend = useStore(state => state.loadPreferencesFromBackend)
  const initAuth = useStore(state => state.initAuth)
  const setLocalUser = useStore(state => state.setLocalUser)
  const fetchTenants = useStore(state => state.fetchTenants)
  const { isLoading: isConfigLoading, features, isLocal, config } = useAppConfig()

  const isEnterprise = features.enterprise_licensed

  useEffect(() => {
    const switchingFlag = localStorage.getItem('byaan_switching_tenant')
    if (switchingFlag === 'true') {
      const timer = setTimeout(() => {
        localStorage.removeItem('byaan_switching_tenant')
        const htmlOverlay = document.getElementById('tenant-switching-overlay')
        if (htmlOverlay) {
          htmlOverlay.classList.remove('active')
        }
      }, 1000)
      return () => clearTimeout(timer)
    }
  }, [])

  useEffect(() => {
    if (isConfigLoading) return

    if (isEnterprise) {
      initAuth()
        .then(() => loadPreferencesFromBackend())
        .catch(error => {
          console.error('Failed to initialize auth or load preferences:', error)
        })
    } else if (config?.community_bootstrap && !isTauriApp()) {
      const { user_id, email, full_name, tenant_id } = config.community_bootstrap
      localStorage.setItem('byaan_active_tenant', tenant_id)
      setLocalUser({ id: user_id, email, fullName: full_name })
      fetchTenants()
        .then(() => loadPreferencesFromBackend())
        .catch(error => {
          console.error('Failed to bootstrap community mode:', error)
        })
    } else {
      loadPreferencesFromBackend().catch(error => {
        console.error('Failed to load user preferences on startup:', error)
      })
    }
  }, [initAuth, loadPreferencesFromBackend, setLocalUser, fetchTenants, isEnterprise, isLocal, isConfigLoading, config])

  if (isConfigLoading) {
    return null
  }

  // Sync runtime config with backend — community Docker behaves like Mac app
  if (!isEnterprise) {
    window.__RUNTIME_CONFIG__ = {
      ...window.__RUNTIME_CONFIG__,
      isHosted: false,
      isSelfHosted: false,
    }
  }

  // Enterprise mode (licensed) - JWT auth with protected routes
  if (isEnterprise) {
    return (
      <>
        <Routes>
          {/* Guest-only auth routes - redirect to home if already logged in */}
          <Route path="/login" element={<AuthGuard guestOnly><Login /></AuthGuard>} />
          <Route path="/register" element={<AuthGuard guestOnly><Register /></AuthGuard>} />
          <Route path="/forgot-password" element={<AuthGuard guestOnly><ForgotPassword /></AuthGuard>} />
          <Route path="/reset-password" element={<AuthGuard guestOnly><ResetPassword /></AuthGuard>} />
          <Route path="/check-email" element={<AuthGuard guestOnly><CheckEmail /></AuthGuard>} />
          <Route path="/set-password" element={<AuthGuard guestOnly><SetPassword /></AuthGuard>} />
          <Route path="/accept-invitation" element={<AcceptInvitation />} />

          {/* Onboarding route - protected but skips tenant check */}
          <Route
            path="/setup-workspace"
            element={
              <AuthGuard skipOnboarding>
                <SetupWorkspace />
              </AuthGuard>
            }
          />

          {/* Protected routes */}
          <Route
            path="/"
            element={
              <AuthGuard>
                <Layout>
                  <HomePage />
                </Layout>
              </AuthGuard>
            }
          />
          <Route
            path="/notebook/new"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <ChatPreview />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/notebook/:id"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <ChatPreview />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/notebook/:id/preview"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <ChatPreview />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/notebooks"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <NotebooksPage />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/skill-review"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <SkillReviewPage />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/llm-connections"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <LLMConnectionsPage />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/databases"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <DatabasesPage />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/github"
            element={
              <AuthGuard>
                <ViewerRedirect>
                  <Layout>
                    <GitHubIntegrations />
                  </Layout>
                </ViewerRedirect>
              </AuthGuard>
            }
          />
          <Route
            path="/folders"
            element={
              <AuthGuard>
                <Layout>
                  <FoldersPage />
                </Layout>
              </AuthGuard>
            }
          />
          <Route
            path="/folders/:id"
            element={
              <AuthGuard>
                <Layout>
                  <FolderDetailPage />
                </Layout>
              </AuthGuard>
            }
          />
          <Route
            path="/dashboard/:dashboardId"
            element={
              <AuthGuard>
                <Layout>
                  <HomePage />
                </Layout>
              </AuthGuard>
            }
          />
          <Route
            path="/team"
            element={
              <AuthGuard>
                <RoleGuard requireOwnerOrAdmin>
                  <Layout>
                    <TeamMembersPage />
                  </Layout>
                </RoleGuard>
              </AuthGuard>
            }
          />

          {/* Fallback - redirect to login */}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
        <ToastContainer
          position="bottom-right"
          autoClose={3000}
          hideProgressBar={false}
          newestOnTop={true}
          closeOnClick
          rtl={false}
          pauseOnFocusLoss
          draggable
          pauseOnHover
          theme="dark"
          style={{
            zIndex: 9999
          }}
        />
      </>
    )
  }

  // Community mode or local browser dev - no auth, no waitlist, direct access
  if (!isLocal || !isTauriApp()) {
    return (
      <>
        <Layout>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/notebook/new" element={<ChatPreview />} />
            <Route path="/notebook/:id" element={<ChatPreview />} />
            <Route path="/notebook/:id/preview" element={<ChatPreview />} />
            <Route path="/notebooks" element={<NotebooksPage />} />
            <Route path="/skill-review" element={<SkillReviewPage />} />
            <Route path="/llm-connections" element={<LLMConnectionsPage />} />
            <Route path="/databases" element={<DatabasesPage />} />
            <Route path="/github" element={<GitHubIntegrations />} />
            <Route path="/folders" element={<FoldersPage />} />
            <Route path="/folders/:id" element={<FolderDetailPage />} />
            <Route path="/dashboard/:dashboardId" element={<HomePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <ToastContainer
            position="bottom-right"
            autoClose={3000}
            hideProgressBar={false}
            newestOnTop={true}
            closeOnClick
            rtl={false}
            pauseOnFocusLoss
            draggable
            pauseOnHover
            theme="dark"
            style={{
              zIndex: 9999
            }}
          />
        </Layout>
      </>
    )
  }

  // Local mode (Mac app) - WaitlistGate flow
  return (
    <>
      <WaitlistGate>
        <Layout>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/notebook/new" element={<ChatPreview />} />
            <Route path="/notebook/:id" element={<ChatPreview />} />
            <Route path="/notebook/:id/preview" element={<ChatPreview />} />
            <Route path="/notebooks" element={<NotebooksPage />} />
            <Route path="/skill-review" element={<SkillReviewPage />} />
            <Route path="/llm-connections" element={<LLMConnectionsPage />} />
            <Route path="/databases" element={<DatabasesPage />} />
            <Route path="/github" element={<GitHubIntegrations />} />
            <Route path="/folders" element={<FoldersPage />} />
            <Route path="/folders/:id" element={<FolderDetailPage />} />
            <Route path="/dashboard/:dashboardId" element={<HomePage />} />
          </Routes>
          <ToastContainer
            position="bottom-right"
            autoClose={3000}
            hideProgressBar={false}
            newestOnTop={true}
            closeOnClick
            rtl={false}
            pauseOnFocusLoss
            draggable
            pauseOnHover
            theme="dark"
            style={{
              zIndex: 9999
            }}
          />
          <DownloadNotification />
        </Layout>
      </WaitlistGate>

      {/* Update notification - Always visible on top of everything */}
      <UpdatePopup
        isOpen={available}
        version={update?.version || ''}
        notes={update?.body}
        downloading={downloading}
        onInstall={installUpdate}
        onDismiss={dismissUpdate}
      />
    </>
  )
}

export default App
