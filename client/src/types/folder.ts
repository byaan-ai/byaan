export interface UserInfo {
  id: string
  email: string
  full_name: string | null
}

export interface Folder {
  id: string
  tenant_id: string
  created_by: string
  name: string
  description: string | null
  is_public: boolean
  created_at: string
  updated_at: string
  creator?: UserInfo
  member_count?: number
  notebook_count?: number
}

export interface FolderMember {
  id: string
  folder_id: string
  user_id: string
  added_by: string | null
  created_at: string
  user?: UserInfo
  added_by_user?: UserInfo
}

export interface FolderNotebook {
  id: string
  folder_id: string
  notebook_id: string
  shared_by: string | null
  created_at: string
  notebook_name: string | null
  notebook_description: string | null
  notebook_created_by: string | null
  shared_by_user?: UserInfo
  is_snapshot: boolean
  snapshot_updated_at: string | null
}

export interface FolderCreateRequest {
  name: string
  description?: string
  is_public?: boolean
}

export interface FolderUpdateRequest {
  name?: string
  description?: string
  is_public?: boolean
}

export interface FolderMemberAddRequest {
  user_id: string
}

export interface FolderNotebookShareRequest {
  notebook_id: string
  is_snapshot?: boolean
}

export interface CloneNotebookRequest {
  new_name?: string
}

export interface CloneNotebookResponse {
  notebook_id: string
  notebook_name: string
  messages_cloned: number
  queries_cloned: number
  dashboards_cloned: number
  datasets_cloned: number
  connection_access_warnings: string[] | null
}

export interface FolderListResponse {
  items: Folder[]
  total: number
}

export interface FolderMemberListResponse {
  items: FolderMember[]
  total: number
}

export interface FolderNotebookListResponse {
  items: FolderNotebook[]
  total: number
}

export interface NotebookFolder {
  id: string
  folder_id: string
  folder_name: string
  folder_description: string | null
  shared_by: string | null
  shared_by_user?: UserInfo
  created_at: string
  is_snapshot: boolean
  snapshot_updated_at: string | null
}

export interface NotebookFolderListResponse {
  items: NotebookFolder[]
  total: number
}

// Dashboard folder types
export interface FolderDashboard {
  id: string
  folder_id: string
  dashboard_id: string
  shared_by: string | null
  shared_by_user?: UserInfo
  created_at: string
  is_snapshot: boolean
  snapshot_updated_at: string | null
  // Dashboard info
  dashboard_version: number | null
  dashboard_notebook_id: string | null
  dashboard_notebook_name: string | null
}

export interface FolderDashboardShareRequest {
  dashboard_id: string
  is_snapshot?: boolean
}

export interface FolderDashboardListResponse {
  items: FolderDashboard[]
  total: number
}

export interface DashboardFolder {
  id: string
  folder_id: string
  folder_name: string
  folder_description: string | null
  shared_by: string | null
  shared_by_user?: UserInfo
  created_at: string
  is_snapshot: boolean
  snapshot_updated_at: string | null
  dashboard_id?: string
  shared_version?: number | null
}

export interface DashboardFolderListResponse {
  items: DashboardFolder[]
  total: number
}

// Unified content type for folder detail page (Google Drive-like view)
export type FolderContentType = 'notebook' | 'dashboard'

export interface FolderContentItem {
  id: string
  type: FolderContentType
  name: string
  description: string | null
  isSnapshot: boolean
  sharedBy: string | null
  sharedByUser: UserInfo | null
  createdAt: string
  snapshotUpdatedAt: string | null
  // Type-specific fields
  notebookId?: string
  dashboardId?: string
  dashboardVersion?: number
  dashboardNotebookId?: string
}

// Viewer dashboard types
export interface ViewerDashboard {
  id: string
  folder_id: string
  folder_name: string | null
  version: number | null
  notebook_id: string | null
  notebook_name: string | null
  shared_by: string | null
  shared_at: string | null
}

export interface ViewerDashboardDetail {
  id: string
  html_content: string
  version: number
  notebook_id: string
  notebook_name?: string | null
  created_at: string | null
}

export interface ViewerDashboardListResponse {
  items: ViewerDashboard[]
  total: number
}

// All Dashboards page types (grouped by folder)
export interface DashboardListItem {
  id: string
  notebook_name: string | null
  version: number | null
  shared_by: string | null
  shared_at: string | null
  html_content?: string
  notebook_id: string | null
  notebook_created_by: string | null
  folder_id: string
}

export interface FolderWithDashboards {
  folder_id: string
  folder_name: string
  is_public: boolean
  dashboards: DashboardListItem[]
}

export interface DashboardsByFolder {
  folders: FolderWithDashboards[]
  total_dashboards: number
}

// All Notebooks page types (grouped by folder)
export interface NotebookListItem {
  id: string
  notebook_name: string | null
  description: string | null
  shared_by: string | null
  shared_at: string | null
  is_snapshot: boolean
}

export interface FolderWithNotebooks {
  folder_id: string
  folder_name: string
  is_public: boolean
  notebooks: NotebookListItem[]
}

export interface NotebooksByFolder {
  folders: FolderWithNotebooks[]
  total_notebooks: number
}
