import { expect, test } from '@playwright/test'

const tenantId = '00000000-0000-4000-8000-000000000001'
const userId = '00000000-0000-4000-8000-000000000002'

const ownerScopes = [
  'notebook.create',
  'notebook.read_own',
  'notebook.update_own',
  'notebook.delete_own',
  'connection.create',
  'connection.read',
  'connection.update',
  'connection.delete',
  'dataset.create',
  'dataset.read',
  'dataset.update',
  'dataset.delete',
  'query.create',
  'query.read',
  'query.update',
  'query.delete',
  'query.execute',
  'llm_connection.create',
  'llm_connection.read',
  'llm_connection.update',
  'llm_connection.delete',
  'dashboard.read',
  'dashboard.export',
  'dashboard.share',
  'sharing.read',
  'folder.create',
  'folder.read',
  'folder.update',
  'folder.delete',
  'folder.manage_members',
  'folder.share_notebook',
]

async function mockApi(page: import('@playwright/test').Page) {
  await page.addInitScript(({ tenantId: activeTenantId }) => {
    window.localStorage.setItem('byaan_active_tenant', activeTenantId)
    window.localStorage.setItem('byaan_mcp_setup_dismissed', 'true')
  }, { tenantId })

  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname

    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })

    if (path === '/api/app/config') {
      return json({
        success: true,
        data: {
          features: {
            worker_features_enabled: false,
            external_sharing_enabled: false,
            notebook_import_enabled: false,
            public_registration_enabled: false,
            local_auth_enabled: true,
            invitation_only: false,
            google_oauth_enabled: false,
            enterprise_licensed: false,
            team_sharing_enabled: false,
          },
          community_bootstrap: {
            user_id: userId,
            email: 'owner@example.test',
            full_name: 'Owner Example',
            tenant_id: tenantId,
          },
        },
      })
    }

    if (path === '/api/scopes/all') {
      return json({
        success: true,
        data: {
          tenants: [
            {
              tenant_id: tenantId,
              tenant_name: 'Responsive Smoke Tenant',
              role: 'owner',
              scopes: ownerScopes,
              features: {
                worker_features_enabled: false,
                external_sharing_enabled: false,
                notebook_import_enabled: false,
                public_registration_enabled: false,
                local_auth_enabled: true,
                invitation_only: false,
                google_oauth_enabled: false,
                team_sharing_enabled: false,
              },
            },
          ],
        },
      })
    }

    if (path === '/api/preferences/instructions' || path === '/api/preferences/style_guidelines') {
      return json({ success: true, data: { content: '' } })
    }

    if (path === '/api/llm-connections') {
      return json({ success: true, data: { items: [], total: 0 } })
    }

    if (path === '/api/datasources') {
      return json({ success: true, data: { items: [], total: 0 } })
    }

    if (path === '/api/github/oauth/status') {
      return json({ success: true, data: { connected: true, username: 'octocat', scopes: [], auth_method: 'oauth' } })
    }

    if (path === '/api/mcp/keys') {
      return json({ data: [{ id: 'key-1', name: 'Smoke key', key_prefix: 'byaan_', is_active: true, last_used_at: null, created_at: '2026-08-17T00:00:00Z' }] })
    }

    if (path === '/api/schedules') {
      return json({ data: [] })
    }

    if (path === '/api/notebooks') {
      return json({
        success: true,
        data: {
          items: [
            {
              id: 'notebook-1',
              notebook_name: 'Responsive smoke notebook',
              description: 'Viewport smoke validation',
              created_by: userId,
              created_at: '2026-08-17T00:00:00Z',
              updated_at: '2026-08-17T00:00:00Z',
            },
          ],
          total: 1,
        },
      })
    }

    return json({ success: true, data: { items: [], total: 0 } })
  })
}

test.describe('home responsive smoke', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('renders the authenticated community home page at required viewport', async ({ page }, testInfo) => {
    await page.goto('/')

    await expect(page.getByRole('heading', { name: /welcome back/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /new notebook/i }).first()).toBeVisible()
    await expect(page.getByRole('heading', { name: 'My Notebooks' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Responsive smoke notebook' })).toBeVisible()
    await expect(page.getByText('Something went wrong')).toHaveCount(0)
    await expect(page.getByText('Booting Byaan')).toHaveCount(0)

    expect(page.viewportSize()).toEqual(testInfo.project.name.includes('390x844')
      ? { width: 390, height: 844 }
      : { width: 1440, height: 900 })
  })
})
