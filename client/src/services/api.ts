// (types for query requests are inlined within methods)
import type { NotebookFolder, DashboardFolder, ViewerDashboard, ViewerDashboardDetail, DashboardsByFolder, NotebooksByFolder } from '../types/folder'
import { clearAccessToken, clearRefreshToken, getAccessToken, getRefreshToken, setAccessToken, setRefreshToken } from './tokenStore'
import { getBackendUrl, isTauriApp } from '../lib/tauri-api'
import { getApiBaseUrl as getRuntimeApiBaseUrl, isHostedMode as getRuntimeIsHosted } from '../lib/runtime-config'
import { isAnalyticsOptedOut } from '../lib/analyticsPreference'

export interface CodeAssistantRequest {
  message: string
  llm_connection_id?: string
  model?: string
  query_id?: string  // Kept for backward compatibility
  query_ids?: string[]  // New field for multiple queries
  project_id?: string
}

export interface AgentRequest {
  message: string
  attachments?: Array<{
    file_name: string
    mime_type: "image/png" | "image/jpeg" | "image/webp"
    file_data: string
  }>
  notebook_id?: string
  llm_connection_id?: string
  model?: string
  db_type?: string
  current_version?: number
  datasource_ids?: string[]
  create_notebook?: boolean
  is_preview?: boolean
  plan_mode?: boolean
}

export interface HtmlEditDetectedEvent {
  type: 'html_edit_detected'
  message?: string
  tool_name?: string
  edit_session_id?: string
}

export interface HtmlEditPatchEvent {
  type: 'html_edit_patch'
  tool_name?: string
  edit_session_id?: string
  payload?: Record<string, any>
}

export interface HtmlEditCompleteEvent {
  type: 'html_edit_complete'
  message?: string
  tool_name?: string
  edit_session_id?: string | null
}

export interface HtmlContextRefreshEvent {
  type: 'html_context_refresh'
  stage: 'start' | 'complete'
  message?: string
  tool_name?: string
  context_id?: string
  edit_session_id?: string | null
  is_initial_fetch?: boolean
}

export interface DatasourceSelectedEvent {
  type: 'datasource_selected'
  datasource_id: string
  datasource_name: string
  datasource_type: string
}

export interface PlanCreatedEvent {
  type: 'plan_created'
  plan_id: string
  notebook_id: string
  steps: Array<{
    id: string
    name: string
    description: string
    status: 'pending' | 'running' | 'completed' | 'failed'
  }>
}

export interface PlanStepUpdateEvent {
  type: 'plan_step_update'
  plan_id?: string
  step_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

export interface PlanStatusEvent {
  type: 'plan_status'
  action: 'start_plan' | 'start_step' | 'complete_step' | 'fail_step' | 'complete_plan'
  notebook_id?: string
  steps?: Array<{ name: string; description?: string }>
  step_number?: number
  total_steps?: number
}

export interface SessionCorruptedEvent {
  type: 'session_corrupted'
  session_id: string
  message: string
}

export interface InstructionUpdatedEvent {
  type: 'memory_updated'
}

export interface Project {
  id: string
  name: string
  path: string
  created_at: string
  updated_at: string
}

export interface ProjectCreateRequest {
  name: string
}

export interface ProjectListResponse {
  items: Project[]
  total: number
}

// Standardized API response format
export interface StandardResponse<T = any> {
  success: boolean
  message: string
  data?: T
}

// Helper function to extract data from standardized or legacy response
function extractData<T>(response: any): T {
  // Check if it's a standardized response
  if (response && typeof response === 'object' && 'success' in response && 'data' in response) {
    return response.data as T
  }
  // Legacy response format - return as-is
  return response as T
}

// Helper function to handle error responses
function extractErrorMessage(errorData: any): string {
  // Check if it's a standardized error response
  if (errorData && typeof errorData === 'object') {
    if ('message' in errorData) {
      return errorData.message
    }
    if ('detail' in errorData) {
      // Legacy error format or complex error object
      if (typeof errorData.detail === 'string') {
        return errorData.detail
      }
      if (typeof errorData.detail === 'object' && 'message' in errorData.detail) {
        return errorData.detail.message
      }
      return JSON.stringify(errorData.detail)
    }
  }
  return 'An unknown error occurred'
}

function normalizeDashboardFilterConfig(responseData: any): DashboardFilterConfigResponse {
  const extracted = extractData<any>(responseData)
  const rawFilters = Array.isArray(extracted?.filters) ? extracted.filters : []
  const filters: DashboardFilterDefinition[] = rawFilters
    .filter((item: any) => item && typeof item === 'object' && item.id && item.query_id && item.field_name)
    .map((item: any) => ({
      id: String(item.id),
      query_id: String(item.query_id),
      field_name: String(item.field_name),
      display_label: String(item.display_label || item.field_name),
      filter_type: String(item.filter_type || 'text') as DashboardFilterDefinition['filter_type'],
      operator: String(item.operator || 'eq'),
      options: Array.isArray(item.options) ? item.options : null,
      data_type: item.data_type ?? null,
      source: item.source ?? null,
      auto_generated: Boolean(item.auto_generated),
      created_at: item.created_at ?? null,
    }))

  return {
    filters,
    version: typeof extracted?.version === 'number' ? extracted.version : 1,
    created_at: typeof extracted?.created_at === 'string' ? extracted.created_at : null,
  }
}

interface QueryResponse {
  result: string
  success: boolean
  error?: string
}

// Enhanced error types for raw query
export type ErrorSeverity = 'warning' | 'error' | 'critical'
export type ErrorCategory = 'syntax' | 'connection' | 'permission' | 'timeout' | 'resource' | 'validation' | 'unknown'

export interface ErrorDetail {
  message: string
  category: ErrorCategory
  severity: ErrorSeverity
  original_query: string
  error_code?: string
  position?: { line: number; column: number }
  suggestions?: string[]
  stack_trace?: string
  context?: Record<string, any>
}

export interface RawQueryResponse {
  success: boolean
  result?: any
  error?: string
  error_detail?: ErrorDetail
  total_count?: number
  returned_count?: number
  limited?: boolean
}

export interface FilterOption {
  label: string
  value: string | number | boolean
}

export interface DashboardFilterDefinition {
  id: string
  query_id: string
  field_name: string
  display_label: string
  filter_type: 'select' | 'multiselect' | 'date_range' | 'number_range' | 'text'
  operator: string
  options?: Array<string | number | boolean | FilterOption> | null
  data_type?: string | null
  source?: string | null
  auto_generated?: boolean
  created_at?: string | null
}

export interface DashboardFilterConfigResponse {
  filters: DashboardFilterDefinition[]
  version?: number
  created_at?: string | null
}

export interface FilterPreflightCompiledFilter {
  field: string
  operator: string
  value: unknown
  ui_type?: string | null
  ui_label?: string | null
}

export interface FilterPreflightResult {
  query_id: string
  query_name: string
  success: boolean
  compiled_filters: FilterPreflightCompiledFilter[]
  available_filter_ids: string[]
  available_filter_fields: string[]
  warnings: string[]
  error?: string | null
}

export interface BatchFilterPreflightResponse {
  success: boolean
  message: string
  data: FilterPreflightResult[]
  partial_success?: boolean | null
  total_queries?: number | null
  successful_queries?: number | null
  failed_queries?: number | null
}

export interface QueryWithFilterValuesPayload {
  query_id: string
  filters?: Array<{
    field: string
    operator: string
    value: unknown
  }>
  filter_values?: Record<string, unknown>
}

// Schedules
export interface ScheduleCreate {
  name: string
  cron_expression: string
  timezone?: string
  is_enabled?: boolean
  webhook_url?: string | null
  slack_channel_id?: string | null
  instruction?: string | null
}

export interface ScheduleUpdate {
  name?: string
  cron_expression?: string
  timezone?: string
  is_enabled?: boolean
  webhook_url?: string | null
  slack_channel_id?: string | null
  instruction?: string | null
}

export interface ScheduleRead {
  id: string
  notebook_id: string
  notebook_name?: string | null
  name: string
  cron_expression: string
  timezone: string
  is_enabled: boolean
  webhook_url?: string | null
  slack_channel_id?: string | null
  instruction?: string | null
  next_run_at?: string | null
  is_running: boolean
  created_by?: string | null
  created_at: string
  updated_at: string
}

export interface ScheduleTestResult {
  success: boolean
  summary?: string | null
  error?: string | null
  queries_total?: number | null
  queries_succeeded?: number | null
  queries_failed?: number | null
}

export interface ExecuteQueryRequest {
  query: string
  connection_id: string
  notebook_id: string
  db_type: string
  name: string
}

export interface ExecuteQueryResponse {
  success: boolean
  result?: any
  error?: string
  generated_schema?: Record<string, any>
  query_id?: string
}

interface ToolsResponse {
  tools: Array<{
    name: string
    description: string
    parameters: any
  }>
  success: boolean
}

// Conversations / Threads
export interface ThreadRead {
  id: string
  thread_title?: string
  notebook_id: string
  created_at: string
}

export interface MessageAttachment {
  id: string
  file_name: string
  mime_type: string
  file_data: string
}

export interface MessageRead {
  id: string
  thread_id: string
  role: string
  content: string
  tool_call_id?: string
  metadata_?: Record<string, any>
  created_at: string
  attachments?: MessageAttachment[]
}

export interface ConversationQueryRequest {
  notebook_id: string
  thread_id?: string
  message: string
  db_type: string
  llm_connection_id?: string  // Add LLM connection support
  model?: string  // Model name to use with the connection
}

export interface ConversationResponse {
  thread: ThreadRead
  message: MessageRead
}

export interface ThreadCreateRequest {
  thread_title?: string
  notebook_id: string
}

// Notebooks
export interface NotebookCreateRequest {
  notebook_name: string
  description?: string
}

export interface NotebookUpdateRequest {
  notebook_name?: string
  description?: string
  last_used_provider?: string
  last_used_model?: string
}

export interface Notebook {
  id: string
  notebook_name: string
  description?: string
  last_used_provider?: string
  last_used_model?: string
  memory?: string | null
  created_by?: string
  created_at: string
  updated_at: string
}

export interface NotebookListResponse {
  items: Notebook[]
  total?: number
}

export interface QueryListItem {
  id: string
  name: string
  query_type: string  // "sql" | "duckdb" | "skill_api"
  skill_name: string | null  // Only for skill_api queries
}

export interface QueryListResponse {
  queries: QueryListItem[]
}

export interface DashboardVersion {
  version_num: number
  created_at: string
  id: string
}

export interface QueryRead {
  id: string
  name: string
  query: string
  output_schema: string
  dataset_id: string  // Now queries reference datasets (unified abstraction)
  notebook_id: string
  query_type: string  // "sql" | "duckdb" | "skill_api"
  skill_name: string | null  // Only for skill_api queries
  created_at: string
  updated_at: string
}

export interface ExecuteSavedQueryResponse {
  success: boolean
  message?: string
  data?: any[]
  query_name?: string
  query_id?: string
  cached?: boolean
  stale?: boolean
}

// Connections - Only database connections now, files are handled by datasets
export type ConnectionType = 'pg' | 'mongo' | 'mysql' | 'sqlite' | 'mssql' | 'dynamodb' | 'databricks'
export type FileType = 'csv' | 'excel' | 'parquet' | 'json'
export type DatasourceType = ConnectionType | FileType | 'duckdb'

export interface ConnectionCreateRequest {
  type: ConnectionType
  name?: string
  connection_obj: Record<string, any>
  is_public?: boolean
}

export interface NotebookDatasetRead {
  id: string
  notebook_id: string
  dataset_id: string
  dataset_type: string
  connection_id: string | null
  created_at: string
}

export interface ConnectionRead {
  id: string
  type: ConnectionType
  name?: string
  created_at: string
  connection_obj?: Record<string, any>
}

export interface ConnectionUpdateResponse extends ConnectionRead {
  schema_updated_at?: string
  database_schema?: DatabaseSchemaResponse
}

export interface DatasetConnectRequest {
  connection_id?: string
  connection?: ConnectionCreateRequest
  // Support for multiple connections
  connection_ids?: string[]
  connections?: ConnectionCreateRequest[]
}

export interface DatasetAssociateRequest {
  connection_id?: string
  dataset_id?: string
  // Support for multiple connections
  connection_ids?: string[]
  dataset_ids?: string[]
}

export interface DatasetConnectResponse {
  dataset: NotebookDatasetRead | null
  connection: ConnectionRead | null
  // Support for multiple datasets response (multi-database)
  datasets?: NotebookDatasetRead[]
  connections?: ConnectionRead[]
}

export interface ConnectionListResponse {
  items: ConnectionRead[]
  total?: number
}

export interface ConnectionListItem {
  id: string
  name: string
  host?: string
  type: string
  created_at: string
}

export interface ConnectionListSimpleResponse {
  items: ConnectionListItem[]
  total: number
}

export interface DatabricksCatalog {
  name: string
  schemas: string[]
}

export interface DatabricksDiscoverResponse {
  catalogs: DatabricksCatalog[]
}

export interface DatabricksDiscoverRequest {
  server_hostname: string
  http_path: string
  access_token: string
}


// LLM Connections
export interface LLMConnection {
  id: string
  type: string
  name?: string
  config: Record<string, any>
  created_by?: string
  created_at: string
}

export interface LLMConnectionCreateRequest {
  type: string
  name?: string
  config: Record<string, any>
}

export interface LLMConnectionListResponse {
  items: LLMConnection[]
  total?: number
}

export interface ProviderConfig {
  required_fields: string[]
  optional_fields: string[]
  example_config: Record<string, any>
}

export type SupportedProviders = Record<string, ProviderConfig>

// Database Schema Types
export interface NestedSchemaNode {
  type: string | string[]
  properties?: Record<string, NestedSchemaNode>
  items?: NestedSchemaNode
}

export interface DatabaseColumn {
  name: string
  type: string
  nullable: boolean
  nested_schema?: NestedSchemaNode
  redacted?: boolean
}

export interface DatabaseForeignKey {
  column: string[]
  ref_table: string
}

export interface DatabaseTable {
  columns: DatabaseColumn[]
  foreign_keys?: DatabaseForeignKey[]
  row_count?: number
  filename?: string  // For file-based connections (CSV/Excel/Parquet/JSON) - shows original filename
  sheet_name?: string  // For Excel files with multiple sheets - shows the sheet name
}

export interface MongoCollection {
  sample_fields: string[]
  nested_schema?: NestedSchemaNode
}

// Single database/datasource schema
export interface SingleDatabaseSchema {
  datasource_type: DatasourceType
  datasource_name: string
  schema: Record<string, DatabaseTable | MongoCollection>
  dataset_id?: string
  connection_id?: string
  connection_name?: string
  database_name?: string
  sample_data?: Record<string, any>
}

// Database info in multi-database response
export interface DatabaseInfo {
  database_number: number
  dataset_id: string  // Always present for both file and connection datasets
  connection_id?: string  // Only present for connection-type datasets
  connection_name?: string  // Only present for connection-type datasets
  dataset_type: 'connection' | 'file'  // Type of dataset
  database_type: DatasourceType  // Includes 'duckdb' for files, 'pg', 'mysql', etc for connections
  database_name: string
  schema: Record<string, DatabaseTable | MongoCollection>
}

// Multi-database schema response
export interface MultiDatabaseSchema {
  notebook_id: string
  total_databases: number
  databases: DatabaseInfo[]
}

// Union type for schema response (supports both single and multiple databases)
export type DatabaseSchemaResponse = SingleDatabaseSchema | MultiDatabaseSchema

// Type guard to check if response is multi-database
export function isMultiDatabaseSchema(schema: DatabaseSchemaResponse): schema is MultiDatabaseSchema {
  return 'databases' in schema && Array.isArray((schema as MultiDatabaseSchema).databases)
}

// Datasource Annotation Types
export interface DatasourceAnnotation {
  id: string
  datasource_id: string
  table_name: string
  column_name: string | null
  annotation_type: 'table_description' | 'column_annotation' | 'column_redaction' | 'table_redaction'
  content: string
  created_at: string
  updated_at: string
}

// Dataset Types - NEW for file upload architecture
export interface DatasetFile {
  id: string
  filename: string
  size: number
  uploaded_at: string
}

export interface Dataset {
  id: string
  notebook_id: string
  type: 'file' | 'connection'
  connection_id?: string
  created_at: string
  files?: DatasetFile[]
}

export interface DatasetUploadResponse {
  dataset_id: string
  notebook_id: string
  type: string
  files_count: number
  file_type: string
  files: DatasetFile[]
  schema: any
}

export interface DatasetListResponse {
  items: Dataset[]
  total: number
}

// Unified Datasource Types - combines Connections and Datasets
export interface Datasource {
  id: string
  name: string
  type: DatasourceType
  db_type?: string
  source_type: 'connection' | 'dataset'
  connection_id?: string  // Only for connection-type datasources
  files_count?: number  // Only for datasets
  files?: Array<{  // File metadata for datasets (no schema - fast!)
    id: string
    file_id: string
    name: string
    filename: string
    type: string
    size: number
    uploaded_at: string | null
    storage_path: string | null
    alias: string
  }>
  created_by?: string
  created_at: string
  is_public?: boolean
}

export interface DatasourceListResponse {
  items: Datasource[]
  total: number
}

export interface DatabaseHealthResponse {
  notebook_id: string
  datasource_type?: string
  datasource_name?: string
  status: 'healthy' | 'unhealthy'
  message: string
}

// Dynamic API URL based on whether running in Tauri or browser
let cachedApiBaseUrl: string | null = null;
let cachedApiRootUrl: string | null = null;

// Helper to get current API base URL with caching
const getApiBaseUrl = async (): Promise<string> => {
  if (cachedApiBaseUrl) return cachedApiBaseUrl;

  if (isTauriApp()) {
    const backendUrl = await getBackendUrl();
    cachedApiBaseUrl = `${backendUrl}/api`;
    cachedApiRootUrl = backendUrl;
    return cachedApiBaseUrl;
  }

  const runtimeApiBase = getRuntimeApiBaseUrl();
  if (runtimeApiBase && runtimeApiBase !== '/api') {
    cachedApiBaseUrl = runtimeApiBase;
    cachedApiRootUrl = runtimeApiBase.replace('/api', '');
    return cachedApiBaseUrl;
  }

  if (getRuntimeIsHosted()) {
    cachedApiBaseUrl = '/api';
    cachedApiRootUrl = '';
    return cachedApiBaseUrl;
  }

  cachedApiBaseUrl = "/api";
  cachedApiRootUrl = "";
  return cachedApiBaseUrl;
};

// Helper to get current API root URL
const getApiRootUrl = async (): Promise<string> => {
  if (cachedApiRootUrl) return cachedApiRootUrl;
  await getApiBaseUrl(); // This will set both caches
  return cachedApiRootUrl!;
};

const isHostedMode = getRuntimeIsHosted();

const getActiveTenantId = (): string | null => {
  if (typeof window === 'undefined') return null;
  if (isHostedMode) {
    const token = getAccessToken();
    if (!token) return null;
  }
  return localStorage.getItem('byaan_active_tenant');
};

const getAuthToken = (): string | null => {
  if (typeof window === 'undefined') return null;
  return getAccessToken();
};

let isRefreshing = false
let refreshPromise: Promise<boolean> | null = null

function getCsrfToken(): string | undefined {
  if (typeof document === 'undefined') return undefined
  const match = document.cookie.split('; ').find(c => c.startsWith('csrf_token='))
  return match?.split('=')[1]
}

async function doRefreshTokens(): Promise<boolean> {
  try {
    const apiUrl = await getApiBaseUrl()
    const refreshToken = isTauriApp() ? getRefreshToken() : undefined

    const headers: Record<string, string> = { 'Content-Type': 'application/json' }

    if (!isTauriApp()) {
      const csrfToken = getCsrfToken()
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken
      }
    }

    const response = await fetch(`${apiUrl}/auth/refresh`, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: refreshToken ? JSON.stringify({ refresh_token: refreshToken }) : undefined,
    })

    if (!response.ok) {
      clearAccessToken()
      clearRefreshToken()
      return false
    }

    const json = await response.json()
    const data = json.data || json
    setAccessToken(data.access_token)
    if (isTauriApp() && data.refresh_token) {
      setRefreshToken(data.refresh_token)
    }
    return true
  } catch {
    clearAccessToken()
    clearRefreshToken()
    return false
  }
}

async function handleTokenRefresh(): Promise<boolean> {
  if (isRefreshing && refreshPromise) {
    return refreshPromise
  }

  isRefreshing = true
  refreshPromise = doRefreshTokens().finally(() => {
    isRefreshing = false
    refreshPromise = null
  })

  return refreshPromise
}

const apiFetch = async (url: string, init?: RequestInit): Promise<Response> => {
  let finalUrl = url;

  if (isTauriApp() && finalUrl.startsWith('/')) {
    const backendUrl = await getBackendUrl();
    finalUrl = `${backendUrl}${finalUrl}`;
  }

  const headers = new Headers(init?.headers);

  const token = getAuthToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const activeTenantId = getActiveTenantId();
  if (activeTenantId) {
    headers.set('X-Tenant-ID', activeTenantId);
  }

  if (isAnalyticsOptedOut()) {
    headers.set('X-Analytics-Opt-Out', '1');
  }

  let response = await fetch(finalUrl, { ...init, headers, credentials: 'include' });

  if (isHostedMode && response.status === 401 && !finalUrl.includes('/auth/refresh')) {
    const refreshed = await handleTokenRefresh()
    if (refreshed) {
      const newToken = getAuthToken()
      if (newToken) {
        headers.set('Authorization', `Bearer ${newToken}`)
        response = await fetch(finalUrl, { ...init, headers, credentials: 'include' })
      }
    }
  }

  return response;
};

const API_BASE_URL = "/api";
const API_ROOT_URL = "";

export class ApiService {
  static getProjectFilePath(projectId: string, fileName: string): string {
    const normalizedProjectId = encodeURIComponent(projectId.replace(/^\/+/, ""))
    const normalizedFileName = fileName
      .replace(/^\/+/, "")
      .split('/')
      .map(segment => encodeURIComponent(segment))
      .join('/')
    return `/projects/${normalizedProjectId}/${normalizedFileName}`
  }

  static getProjectFileUrl(projectId: string, fileName: string, cacheBust: boolean = false): string {
    const path = ApiService.getProjectFilePath(projectId, fileName)
    const isBrowser = typeof window !== 'undefined' && typeof window.location !== 'undefined'
    const baseUrl = isBrowser ? path : `${API_ROOT_URL}${path}`
    if (!cacheBust) {
      return baseUrl
    }

    const separator = baseUrl.includes('?') ? '&' : '?'
    return `${baseUrl}${separator}t=${Date.now()}`
  }

  static async executeQuery(query: string, db_type: string): Promise<QueryResponse> {
    try {
      const apiUrl = await getApiBaseUrl();
      const response = await apiFetch(`${apiUrl}/query`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query,
          db_type
        }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<QueryResponse>(responseData)
    } catch (error) {
      console.error('Error executing query:', error)
      throw error
    }
  }

  static async executeRawQuery(
    notebookId: string,
    dbType: string,
    query: string,
    limit: number = 500,
    connectionId?: string,
    signal?: AbortSignal
  ): Promise<RawQueryResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/raw-query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: notebookId,
          db_type: dbType,
          query,
          limit,
          connection_id: connectionId,
        }),
        signal,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<RawQueryResponse>(responseData)
    } catch (error) {
      console.error('Error executing raw query:', error)
      throw error
    }
  }

  static async exportRawQueryCSV(
    notebookId: string,
    dbType: string,
    query: string,
    connectionId?: string
  ): Promise<Blob> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/raw-query/export/csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: notebookId,
          db_type: dbType,
          query,
          connection_id: connectionId,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.blob()
    } catch (error) {
      console.error('Error exporting raw query CSV:', error)
      throw error
    }
  }

  static async getTools(db_type: string = 'pg'): Promise<ToolsResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/tools?db_type=${db_type}`)
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<ToolsResponse>(responseData)
    } catch (error) {
      console.error('Error getting tools:', error)
      throw error
    }
  }

  // ----------------------------
  // Notebooks API
  // ----------------------------
  static async listNotebooks(): Promise<NotebookListResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<NotebookListResponse>(responseData)
    } catch (error) {
      console.error('Error listing notebooks:', error)
      throw error
    }
  }

  static async createNotebook(payload: NotebookCreateRequest): Promise<Notebook> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<Notebook>(responseData)
    } catch (error) {
      console.error('Error creating notebook:', error)
      throw error
    }
  }

  static async renameNotebook(notebookId: string, newName: string): Promise<Notebook> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          notebook_name: newName,
        }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const result = await response.json()
      return result.data
    } catch (error) {
      console.error('Error renaming notebook:', error)
      throw error
    }
  }

  static async updateNotebook(notebookId: string, payload: NotebookUpdateRequest): Promise<Notebook> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const result = await response.json()
      return result.data
    } catch (error) {
      console.error('Error updating notebook:', error)
      throw error
    }
  }


  static async deleteNotebook(notebookId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error deleting notebook:', error)
      throw error
    }
  }

  // ----------------------------
  // Notebook Connections API
  // ----------------------------
  static async connectNotebook(
    notebookId: string,
    payload: DatasetConnectRequest
  ): Promise<DatasetConnectResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/connections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatasetConnectResponse>(responseData)
    } catch (error) {
      console.error('Error connecting notebook:', error)
      throw error
    }
  }

  static async associateNotebookConnection(
    notebookId: string,
    payload: DatasetAssociateRequest
  ): Promise<DatasetConnectResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/connections/associate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatasetConnectResponse>(responseData)
    } catch (error) {
      console.error('Error associating notebook connection:', error)
      throw error
    }
  }

  static async getNotebookConnections(notebookId: string): Promise<NotebookDatasetRead[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/connections`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<NotebookDatasetRead[]>(responseData)
    } catch (error) {
      console.error('Error fetching notebook connections:', error)
      throw error
    }
  }

  static async getNotebookConnectionsWithDetails(notebookId: string): Promise<any[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/connections/details`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<any[]>(responseData)
    } catch (error) {
      console.error('Error fetching notebook connections with details:', error)
      throw error
    }
  }

  static async refreshNotebookConnectionSchema(notebookId: string, connectionId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/connections/${connectionId}/refresh-schema`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<any>(responseData)
    } catch (error) {
      console.error('Error refreshing notebook connection schema:', error)
      throw error
    }
  }

  static async removeNotebookConnection(notebookId: string, connectionId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/connections/${connectionId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error removing notebook connection:', error)
      throw error
    }
  }


  static async getNotebookSavedQueries(notebookId: string): Promise<QueryListResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/queries`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      // Extract data from standardized response format
      return extractData<QueryListResponse>(responseData)
    } catch (error) {
      console.error('Error fetching notebook saved queries:', error)
      throw error
    }
  }

  static async getQuery(queryId: string): Promise<QueryRead> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/queries/${queryId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      // Extract data from standardized response format
      return extractData<QueryRead>(responseData)
    } catch (error) {
      console.error('Error fetching query:', error)
      throw error
    }
  }

  static async executeSavedQuery(queryId: string): Promise<ExecuteSavedQueryResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/queries/${queryId}/execute`, {
        method: 'POST',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return responseData as ExecuteSavedQueryResponse
    } catch (error) {
      console.error('Error executing saved query:', error)
      throw error
    }
  }

  static async updateQuery(
    queryId: string,
    payload: { name?: string; query?: string }
  ): Promise<{ success: boolean; message?: string; query_id?: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/queries/${queryId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return responseData
    } catch (error) {
      console.error('Error updating query:', error)
      throw error
    }
  }

  static async getConnectionDetails(connectionId: string): Promise<ConnectionUpdateResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections/${connectionId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ConnectionUpdateResponse>(responseData)
    } catch (error) {
      console.error('Error fetching connection details:', error)
      throw error
    }
  }

  static async updateConnection(connectionId: string, payload: ConnectionCreateRequest): Promise<ConnectionUpdateResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections/${connectionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ConnectionUpdateResponse>(responseData)
    } catch (error) {
      console.error('Error updating connection:', error)
      throw error
    }
  }
  
  static async listAllConnections(): Promise<ConnectionListSimpleResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ConnectionListSimpleResponse>(responseData)
    } catch (error) {
      console.error('Error listing all connections:', error)
      throw error
    }
  }

  static async deleteConnection(connectionId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections/${connectionId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error deleting connection:', error)
      throw error
    }
  }

  static async refreshConnectionSchema(connectionId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections/${connectionId}/refresh-schema`, {
        method: 'POST',
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(extractErrorMessage(data) || `HTTP error! status: ${response.status}`)
      }
      return data.data
    } catch (error) {
      console.error('Error refreshing connection schema:', error)
      throw error
    }
  }

  static async discoverDatabricks(payload: DatabricksDiscoverRequest): Promise<DatabricksDiscoverResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections/databricks/discover`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatabricksDiscoverResponse>(responseData)
    } catch (error) {
      console.error('Error discovering Databricks catalogs:', error)
      throw error
    }
  }

  static async createConnection(payload: ConnectionCreateRequest): Promise<ConnectionRead> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/connections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ConnectionRead>(responseData)
    } catch (error) {
      console.error('Error creating connection:', error)
      throw error
    }
  }

  static async uploadMultipleFiles(
    files: File[],
    name: string,
    fileType: FileType = 'csv',
    notebookId?: string,
    aliases?: Record<string, string>
  ): Promise<DatasetUploadResponse> {
    try {
      const formData = new FormData()

      // Append each file
      files.forEach(file => {
        formData.append('files', file)
      })

      // Only append notebook_id if provided
      if (notebookId) {
        formData.append('notebook_id', notebookId)
      }
      formData.append('name', name)
      formData.append('file_type', fileType)

      // Build aliases string (comma-separated or JSON)
      if (aliases && Object.keys(aliases).length > 0) {
        // Remove file extensions based on file type
        const extensions =
          fileType === 'csv' ? ['.csv'] :
          fileType === 'excel' ? ['.xlsx', '.xls'] :
          fileType === 'parquet' ? ['.parquet'] :
          ['.json']
        const aliasArray = files.map(file => {
          let alias = aliases[file.name] || file.name
          extensions.forEach(ext => {
            if (alias.toLowerCase().endsWith(ext)) {
              alias = alias.slice(0, -ext.length)
            }
          })
          return alias
        })
        formData.append('aliases', aliasArray.join(','))
      }

      const response = await apiFetch(`${API_BASE_URL}/datasets/upload-files`, {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<DatasetUploadResponse>(responseData)
    } catch (error) {
      console.error(`Error uploading multiple ${fileType.toUpperCase()} files:`, error)
      throw error
    }
  }

  static async uploadFromURL(
    urls: string[],
    name: string,
    fileType?: FileType,
    notebookId?: string,
    signal?: AbortSignal
  ): Promise<DatasetUploadResponse> {
    try {
      const formData = new FormData()

      // Append each URL
      urls.forEach(url => {
        formData.append('urls', url)
      })

      formData.append('name', name)

      if (fileType) {
        formData.append('file_type', fileType)
      }

      if (notebookId) {
        formData.append('notebook_id', notebookId)
      }

      const response = await apiFetch(`${API_BASE_URL}/datasets/upload-from-url`, {
        method: 'POST',
        body: formData,
        signal, // Pass abort signal
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<DatasetUploadResponse>(responseData)
    } catch (error) {
      console.error('Error uploading from URL:', error)
      throw error
    }
  }

  // ----------------------------
  // Datasets API - NEW
  // ----------------------------
  static async listDatasets(notebookId: string): Promise<DatasetListResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/datasets`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatasetListResponse>(responseData)
    } catch (error) {
      console.error('Error listing datasets:', error)
      throw error
    }
  }

  static async getDataset(datasetId: string): Promise<Dataset> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasets/${datasetId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<Dataset>(responseData)
    } catch (error) {
      console.error('Error fetching dataset:', error)
      throw error
    }
  }

  static async getDatasetSchema(datasetId: string): Promise<DatabaseSchemaResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasets/${datasetId}/schema`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatabaseSchemaResponse>(responseData)
    } catch (error) {
      console.error('Error fetching dataset schema:', error)
      throw error
    }
  }

  static async getDatasourceSchema(datasourceId: string): Promise<DatabaseSchemaResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasources/${datasourceId}/schema`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatabaseSchemaResponse>(responseData)
    } catch (error) {
      console.error('Error fetching datasource schema:', error)
      throw error
    }
  }

  // Datasource Annotations API
  static async getDatasourceAnnotations(datasourceId: string): Promise<StandardResponse<DatasourceAnnotation[]>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasources/${datasourceId}/annotations`)
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error(`Error fetching annotations for datasource ${datasourceId}:`, error)
      throw error
    }
  }

  static async createDatasourceAnnotation(
    datasourceId: string,
    payload: {
      table_name: string
      column_name?: string | null
      annotation_type: 'table_description' | 'column_annotation' | 'column_redaction' | 'table_redaction'
      content: string
    }
  ): Promise<StandardResponse<DatasourceAnnotation>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasources/${datasourceId}/annotations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error(`Error creating annotation for datasource ${datasourceId}:`, error)
      throw error
    }
  }

  static async updateDatasourceAnnotation(
    datasourceId: string,
    annotationId: string,
    content: string
  ): Promise<StandardResponse<DatasourceAnnotation>> {
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/datasources/${datasourceId}/annotations/${annotationId}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        }
      )
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error(`Error updating annotation ${annotationId}:`, error)
      throw error
    }
  }

  static async deleteDatasourceAnnotation(
    datasourceId: string,
    annotationId: string
  ): Promise<StandardResponse<{ id: string }>> {
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/datasources/${datasourceId}/annotations/${annotationId}`,
        {
          method: 'DELETE',
        }
      )
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error(`Error deleting annotation ${annotationId}:`, error)
      throw error
    }
  }

  static async unredactColumn(
    datasourceId: string,
    tableName: string,
    columnName: string
  ): Promise<StandardResponse<{ id: string }>> {
    const params = new URLSearchParams({ table_name: tableName, column_name: columnName })
    const response = await apiFetch(
      `${API_BASE_URL}/datasources/${datasourceId}/redactions?${params}`,
      { method: 'DELETE' }
    )
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
    }
    return await response.json()
  }

  static async unredactTable(
    datasourceId: string,
    tableName: string
  ): Promise<StandardResponse<{ id: string }>> {
    const params = new URLSearchParams({ table_name: tableName })
    const response = await apiFetch(
      `${API_BASE_URL}/datasources/${datasourceId}/redactions?${params}`,
      { method: 'DELETE' }
    )
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
    }
    return await response.json()
  }

  static async toggleColumnRedaction(
    datasourceId: string,
    tableName: string,
    columnName: string,
    enable: boolean
  ): Promise<StandardResponse<DatasourceAnnotation | { id: string }>> {
    if (enable) {
      return ApiService.createDatasourceAnnotation(datasourceId, {
        table_name: tableName,
        column_name: columnName,
        annotation_type: 'column_redaction',
        content: 'redacted',
      })
    } else {
      return ApiService.unredactColumn(datasourceId, tableName, columnName)
    }
  }

  static async toggleTableRedaction(
    datasourceId: string,
    tableName: string,
    enable: boolean
  ): Promise<StandardResponse<DatasourceAnnotation | { id: string }>> {
    if (enable) {
      return ApiService.createDatasourceAnnotation(datasourceId, {
        table_name: tableName,
        column_name: null,
        annotation_type: 'table_redaction',
        content: 'redacted',
      })
    } else {
      return ApiService.unredactTable(datasourceId, tableName)
    }
  }

  static async updateDataset(
    datasetId: string,
    payload: { name?: string; files: any[]; newFiles?: File[]; is_public?: boolean }
  ): Promise<any> {
    try {
      // If newFiles are provided, use FormData (multipart/form-data)
      if (payload.newFiles && payload.newFiles.length > 0) {
        const formData = new FormData()

        if (payload.name !== undefined && payload.name !== null) {
          formData.append('name', payload.name)
        }

        const filesToKeep = payload.files.map((f) => f.file_id || f.id)
        formData.append('files_to_keep', JSON.stringify(filesToKeep))

        payload.newFiles.forEach((file) => {
          formData.append('new_files', file)
        })

        if (payload.is_public !== undefined) {
          formData.append('is_public', String(payload.is_public))
        }

        const response = await apiFetch(`${API_BASE_URL}/datasets/${datasetId}`, {
          method: 'PUT',
          body: formData,
        })

        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
        }
        const responseData = await response.json()
        return extractData(responseData)
      } else {
        const jsonPayload: any = { files: payload.files }
        if (payload.name !== undefined) {
          jsonPayload.name = payload.name
        }
        if (payload.is_public !== undefined) {
          jsonPayload.is_public = payload.is_public
        }

        const response = await apiFetch(`${API_BASE_URL}/datasets/${datasetId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(jsonPayload),
        })

        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
        }
        const responseData = await response.json()
        return extractData(responseData)
      }
    } catch (error) {
      console.error('Error updating dataset:', error)
      throw error
    }
  }

  static async deleteDataset(datasetId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasets/${datasetId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error deleting dataset:', error)
      throw error
    }
  }

  static async updateDatasourceVisibility(datasourceId: string, isPublic: boolean): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasources/${datasourceId}/visibility?is_public=${isPublic}`, {
        method: 'PATCH',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error updating datasource visibility:', error)
      throw error
    }
  }

  static async associateDatasetWithNotebook(datasetId: string, notebookId: string): Promise<void> {
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/datasets/${datasetId}/notebooks/${notebookId}`,
        { method: 'POST' }
      )
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error associating dataset with notebook:', error)
      throw error
    }
  }

  static async batchAssociateDatasetsWithNotebook(notebookId: string, datasetIds: string[]): Promise<void> {
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/notebooks/${notebookId}/datasets/associate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dataset_ids: datasetIds }),
        }
      )
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error batch associating datasets with notebook:', error)
      throw error
    }
  }


  static async dissociateDatasetFromNotebook(datasetId: string, notebookId: string): Promise<void> {
    try {
      const response = await apiFetch(
        `${API_BASE_URL}/datasets/${datasetId}/notebooks/${notebookId}`,
        { method: 'DELETE' }
      )
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error dissociating dataset from notebook:', error)
      throw error
    }
  }

  // ----------------------------
  // Datasources API - Unified (Connections + Datasets)
  // ----------------------------
  static async listAllDatasources(): Promise<DatasourceListResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/datasources`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatasourceListResponse>(responseData)
    } catch (error) {
      console.error('Error listing datasources:', error)
      throw error
    }
  }

  // ----------------------------
  // Conversations / Threads API
  // ----------------------------
  static async createConversation(
    notebookId: string,
    payload: ConversationQueryRequest
  ): Promise<ConversationResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ConversationResponse>(responseData)
    } catch (error) {
      console.error('Error creating conversation:', error)
      throw error
    }
  }

  static async createConversationStream(
    notebookId: string,
    payload: ConversationQueryRequest,
    onMessage: (chunk: string, isDone: boolean) => void,
    onError?: (error: Error) => void,
    onDone?: () => void,
    onTitleGenerated?: (title: string, threadId: string) => void,
    onToolCall?: (toolName: string, description: string, query?: string) => void,
    onToolOutput?: () => void
  ): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/conversations/stream`, {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      if (!response.body) {
        throw new Error('No response body received from server.')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      let buffer = ''

      try {
        while (true) {
          const { value, done } = await reader.read()

          if (done) {
            break
          }

          // Decode with streaming support to handle multi-byte UTF-8 characters split across chunks
          const chunk = decoder.decode(value, { stream: !done })

          buffer += chunk
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6).trim()
              if (data) {
                try {
                  const event = JSON.parse(data)
                  
                  if (event.type === 'content') {
                    onMessage(event.text, false)
                  } else if (event.type === 'title_generation') {
                    if (onTitleGenerated) {
                      onTitleGenerated(event.title, event.thread_id)
                    }
                  } else if (event.type === 'tool_call') {
                    if (onToolCall) {
                      onToolCall(event.tool_name, event.description, event.query)
                    }
                  } else if (event.type === 'tool_output') {
                    if (onToolOutput) {
                      onToolOutput()
                    }
                  } else if (event.type === 'done') {
                    onMessage('', true) // Signal completion
                  } else if (event.type === 'error') {
                    if (onError) {
                      onError(new Error(event.text))
                    }
                  }
                } catch (parseError) {
                  console.error('Error parsing event data:', parseError, data)
                }
              }
            }
          }
        }
        
        if (onDone) onDone()
      } catch (readerError) {
        if (onError) onError(readerError as Error)
      } finally {
        reader.releaseLock()
      }
    } catch (error) {
      console.error('Error creating streaming conversation:', error)
      if (onError) onError(error as Error)
    }
  }

  static async getNotebookThreads(notebookId: string): Promise<ThreadRead[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/threads`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ThreadRead[]>(responseData)
    } catch (error) {
      console.error('Error fetching threads:', error)
      throw error
    }
  }

  static async getNotebookMessages(notebookId: string): Promise<MessageRead[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/messages`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<MessageRead[]>(responseData)
    } catch (error) {
      console.error('Error fetching notebook messages:', error)
      throw error
    }
  }

  static async clearNotebookConversation(notebookId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/messages`, {
        method: 'DELETE'
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error clearing notebook conversation:', error)
      throw error
    }
  }

  static async getNotebookHtml(notebookId: string, version?: number): Promise<string> {
    try {
      const url = version
        ? `${API_BASE_URL}/notebooks/${notebookId}/html?version=${version}`
        : `${API_BASE_URL}/notebooks/${notebookId}/html`
      const response = await apiFetch(url)
      if (!response.ok) {
        if (response.status === 404) {
          // Return empty string if notebook doesn't have HTML yet
          return ''
        }
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      return await response.text()
    } catch (error) {
      console.error('Error fetching notebook HTML:', error)
      throw error
    }
  }

  static async getNotebookDashboardVersions(notebookId: string): Promise<DashboardVersion[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/dashboards/versions`)
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      const result = await response.json()
      return extractData<DashboardVersion[]>(result)
    } catch (error) {
      console.error('Error fetching dashboard versions:', error)
      throw error
    }
  }

  static async getNotebookDashboardFilters(notebookId: string): Promise<DashboardFilterConfigResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/filters`)
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch notebook filters')
      }
      const json = await response.json()
      return normalizeDashboardFilterConfig(json)
    } catch (error) {
      console.error('Error fetching notebook dashboard filters:', error)
      throw error
    }
  }

  static async preflightNotebookQueryFilters(payload: {
    query_ids?: string[]
    queries_with_filters?: QueryWithFilterValuesPayload[]
    max_parallel?: number
  }): Promise<BatchFilterPreflightResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/queries/batch/preflight`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || 'Failed to preflight dashboard filters')
      }
      return await response.json() as BatchFilterPreflightResponse
    } catch (error) {
      console.error('Error preflighting notebook query filters:', error)
      throw error
    }
  }

  static async getNotebookHtmlVersion(notebookId: string, version: number): Promise<string> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/dashboards/versions/${version}`)
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      const result = await response.json()
      const data = extractData<{ html_content: string }>(result)
      return data.html_content
    } catch (error) {
      console.error('Error fetching dashboard version:', error)
      throw error
    }
  }

  static async createThread(payload: ThreadCreateRequest): Promise<ThreadRead> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/threads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ThreadRead>(responseData)
    } catch (error) {
      console.error('Error creating thread:', error)
      throw error
    }
  }

  static async getThreadMessages(threadId: string): Promise<MessageRead[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/threads/${threadId}/messages`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<MessageRead[]>(responseData)
    } catch (error) {
      console.error('Error fetching thread messages:', error)
      throw error
    }
  }

  static async deleteThread(threadId: string): Promise<{ message: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/threads/${threadId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<any>(responseData)
    } catch (error) {
      console.error('Error deleting thread:', error)
      throw error
    }
  }

  static async streamCodeAssistant(
    request: CodeAssistantRequest,
    options: {
      onChunk?: (chunk: string) => void
      onError?: (error: string) => void
      onFileOperations?: (text: string) => void
      onDone?: () => void
    }
  ): Promise<void> {
    const { onChunk, onError, onFileOperations, onDone } = options

    try {
      const response = await apiFetch(`${API_BASE_URL}/code-assistant/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        const errorMessage = extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`
        if (onError) onError(errorMessage)
        throw new Error(errorMessage)
      }

      if (!response.body) {
        throw new Error('Response body is null')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      // Buffer to handle incomplete SSE lines across chunks
      let buffer = ''

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value)

          buffer += chunk

          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6)
              if (data) {
                try {
                  const event = JSON.parse(data)
                  if (event.type === 'content' && event.text && onChunk) {
                    onChunk(event.text)
                  } else if (event.type === 'error' && onError) {
                    onError(event.text)
                  } else if (event.type === 'file_operations' && onFileOperations) {
                    onFileOperations(event.text)
                  } else if (event.type === 'done' && onDone) {
                    onDone()
                  }
                } catch (parseError) {
                  console.error('Error parsing SSE data:', parseError)
                }
              }
            }
          }
        }
      } finally {
        reader.releaseLock()
      }
    } catch (error) {
      const isAbortError =
        (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') ||
        (error instanceof Error && error.name === 'AbortError')

      if (isAbortError) {
        console.info('Agent stream aborted by client request')
        return
      }

      console.error('Error streaming code assistant:', error)
      if (onError) onError(error instanceof Error ? error.message : 'Unknown error')
      throw error
    }
  }

  static async streamAgent(
    request: AgentRequest,
    options: {
      onChunk?: (chunk: string) => void
      onError?: (error: string) => void
      onDone?: () => void
      onToolCall?: (toolName: string, description: string, query?: string) => void
      onToolOutput?: () => void
      onHtmlEditDetected?: (event: HtmlEditDetectedEvent) => void
      onHtmlEditPatch?: (event: HtmlEditPatchEvent) => void
      onHtmlEditComplete?: (event: HtmlEditCompleteEvent) => void
      onHtmlContextEvent?: (event: HtmlContextRefreshEvent) => void
      onDatasourceSelected?: (event: DatasourceSelectedEvent) => void
      onQuerySaved?: () => void
      onInstructionUpdated?: () => void
      onLearningUpdated?: () => void
      onNotebookCreated?: (notebookId: string, notebookName: string) => void
      onTitleGenerated?: (title: string, threadId: string) => void
      onPlanCreated?: (event: PlanCreatedEvent) => void
      onPlanStepUpdate?: (event: PlanStepUpdateEvent) => void
      onPlanStatus?: (event: PlanStatusEvent) => void
      onSessionCorrupted?: (event: SessionCorruptedEvent) => void
      signal?: AbortSignal
    }
  ): Promise<void> {
    const {
      onChunk,
      onError,
      onDone,
      onToolCall,
      onToolOutput,
      onHtmlEditDetected,
      onHtmlEditPatch,
      onHtmlEditComplete,
      onHtmlContextEvent,
      onDatasourceSelected,
      onQuerySaved,
      onInstructionUpdated,
      onLearningUpdated,
      onNotebookCreated,
      onTitleGenerated,
      onPlanCreated,
      onPlanStepUpdate,
      onPlanStatus,
      onSessionCorrupted,
      signal
    } = options

    try {
      const response = await apiFetch(`${API_BASE_URL}/unified-agent/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify(request),
        signal,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        const errorMessage = extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`
        if (onError) onError(errorMessage)
        throw new Error(errorMessage)
      }

      if (!response.body) {
        throw new Error('Response body is null')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      // Buffer to handle incomplete SSE lines across chunks
      let buffer = ''

      try {
        while (true) {
          const { value, done } = await reader.read()
          if (done) break

          // Decode with streaming support to handle multi-byte UTF-8 characters split across chunks
          const chunk = decoder.decode(value, { stream: !done })

          buffer += chunk

          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const data = line.slice(6)
            if (!data) continue
            try {
              const event = JSON.parse(data)
              if (event.type === 'content' && event.text && onChunk) {
                onChunk(event.text)
              } else if (event.type === 'notebook_created' && onNotebookCreated) {
                onNotebookCreated(event.notebook_id, event.notebook_name)
              } else if (event.type === 'tool_call') {
                if (onToolCall) onToolCall(event.tool_name, event.description, event.query)
              } else if (event.type === 'tool_output') {
                if (onToolOutput) onToolOutput()
              } else if (event.type === 'html_edit_detected' && onHtmlEditDetected) {
                console.log('[API] Received html_edit_detected event from backend')
                onHtmlEditDetected(event as HtmlEditDetectedEvent)
              } else if (event.type === 'html_edit_patch' && onHtmlEditPatch) {
                onHtmlEditPatch(event as HtmlEditPatchEvent)
              } else if (event.type === 'html_edit_complete' && onHtmlEditComplete) {
                console.log('[API] Received html_edit_complete event from backend')
                onHtmlEditComplete(event as HtmlEditCompleteEvent)
              } else if (event.type === 'html_context_refresh' && onHtmlContextEvent) {
                onHtmlContextEvent(event as HtmlContextRefreshEvent)
              } else if (event.type === 'datasource_selected' && onDatasourceSelected) {
                onDatasourceSelected(event as DatasourceSelectedEvent)
              } else if (event.type === 'query_saved' && onQuerySaved) {
                onQuerySaved()
              } else if (event.type === 'memory_updated' && onInstructionUpdated) {
                onInstructionUpdated()
              } else if (event.type === 'learning_updated' && onLearningUpdated) {
                onLearningUpdated()
              } else if (event.type === 'title_generation' && onTitleGenerated) {
                onTitleGenerated(event.title, event.thread_id)
              } else if (event.type === 'plan_created' && onPlanCreated) {
                onPlanCreated(event as PlanCreatedEvent)
              } else if (event.type === 'plan_step_update' && onPlanStepUpdate) {
                onPlanStepUpdate(event as PlanStepUpdateEvent)
              } else if (event.type === 'plan_status' && onPlanStatus) {
                onPlanStatus(event as PlanStatusEvent)
              } else if (event.type === 'session_corrupted' && onSessionCorrupted) {
                onSessionCorrupted(event as SessionCorruptedEvent)
              } else if (event.type === 'error' && onError) {
                onError(event.text)
              } else if (event.type === 'done' && onDone) {
                onDone()
              }
            } catch (parseError) {
              console.error('Error parsing SSE data:', parseError)
            }
          }
        }
      } finally {
        reader.releaseLock()
      }
    } catch (error) {
      const isAbortError =
        (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') ||
        (error instanceof Error && error.name === 'AbortError')

      if (isAbortError) {
        console.info('Agent stream aborted by client request')
        return
      }

      console.error('Error streaming agent:', error)
      if (onError) onError(error instanceof Error ? error.message : 'Unknown error')
      throw error
    }
  }

  static async abortGeneration(notebookId: string): Promise<{ aborted: boolean; reason?: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/unified-agent/abort/${notebookId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<{ aborted: boolean; reason?: string }>(responseData)
    } catch (error) {
      console.error('Error aborting generation:', error)
      throw error
    }
  }

  // ----------------------------
  // LLM Connections API
  // ----------------------------
  static async listLLMConnections(): Promise<LLMConnectionListResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<LLMConnectionListResponse>(responseData)
    } catch (error) {
      console.error('Error listing LLM connections:', error)
      throw error
    }
  }

  static async createLLMConnection(payload: LLMConnectionCreateRequest): Promise<LLMConnection> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<LLMConnection>(responseData)
    } catch (error) {
      console.error('Error creating LLM connection:', error)
      throw error
    }
  }

  static async updateLLMConnection(connectionId: string, payload: LLMConnectionCreateRequest): Promise<LLMConnection> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections/${connectionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<LLMConnection>(responseData)
    } catch (error) {
      console.error('Error updating LLM connection:', error)
      throw error
    }
  }

  static async deleteLLMConnection(connectionId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections/${connectionId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error deleting LLM connection:', error)
      throw error
    }
  }

  static async getLLMConnection(connectionId: string): Promise<LLMConnection> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections/${connectionId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<LLMConnection>(responseData)
    } catch (error) {
      console.error('Error fetching LLM connection:', error)
      throw error
    }
  }

  static async getAvailableModels(provider?: string): Promise<{ models_by_provider: Record<string, string[]> } | { provider: string; models: string[] }> {
    try {
      const url = provider ? `${API_BASE_URL}/llm-connections/models?provider=${provider}` : `${API_BASE_URL}/llm-connections/models`
      const response = await apiFetch(url)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      if (provider) {
        return extractData<{ provider: string; models: string[] }>(responseData)
      } else {
        return extractData<{ models_by_provider: Record<string, string[]> }>(responseData)
      }
    } catch (error) {
      console.error('Error fetching available models:', error)
      throw error
    }
  }

  static async getConnectionModels(connectionId: string): Promise<{ connection_id: string; provider: string; models: Array<{ id: string; name: string; source: string }> }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections/${connectionId}/models`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<{ connection_id: string; provider: string; models: Array<{ id: string; name: string; source: string }> }>(responseData)
    } catch (error) {
      console.error('Error fetching connection models:', error)
      throw error
    }
  }

  static async getSupportedProviders(): Promise<SupportedProviders> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/llm-connections/providers`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<SupportedProviders>(responseData)
    } catch (error) {
      console.error('Error fetching supported providers:', error)
      throw error
    }
  }

  // ----------------------------
  // Database Operations API
  // ----------------------------
  static async getDatabaseSchema(notebookId: string, dbType?: string): Promise<DatabaseSchemaResponse> {
    try {
      const url = dbType
        ? `${API_BASE_URL}/connections/schema/${notebookId}?db_type=${dbType}`
        : `${API_BASE_URL}/connections/schema/${notebookId}`
      
      const response = await apiFetch(url)
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }))
        
        // Handle structured error response from backend
        if (typeof errorData.detail === 'object' && errorData.detail.message) {
          throw new Error(errorData.detail.message)
        } else if (typeof errorData.detail === 'string') {
          throw new Error(errorData.detail)
        } else {
          throw new Error(`HTTP error! status: ${response.status}`)
        }
      }
      const responseData = await response.json()
      return extractData<DatabaseSchemaResponse>(responseData)
    } catch (error) {
      console.error('Error fetching database schema:', error)
      throw error
    }
  }

  static async checkDatabaseHealth(notebookId: string, dbType?: string): Promise<DatabaseHealthResponse> {
    try {
      const url = dbType
        ? `${API_BASE_URL}/connections/health/${notebookId}?db_type=${dbType}`
        : `${API_BASE_URL}/connections/health/${notebookId}`
      
      const response = await apiFetch(url)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<DatabaseHealthResponse>(responseData)
    } catch (error) {
      console.error('Error checking database health:', error)
      throw error
    }
  }

  static async listQueries(): Promise<{ queries: { id: string; name: string }[] }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/queries`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<{ queries: { id: string; name: string }[] }>(responseData)
    } catch (error) {
      console.error('Error listing queries:', error)
      throw error
    }
  }

  static async executeAndSaveQuery(
    query: string,
    connectionId: string,
    notebookId: string,
    dbType: string,
    name: string
  ): Promise<ExecuteQueryResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/execute-query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          connection_id: connectionId,
          notebook_id: notebookId,
          db_type: dbType,
          name,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      const responseData = await response.json()
      return extractData<ExecuteQueryResponse>(responseData)
    } catch (error) {
      console.error('Error executing and saving query:', error)
      throw error
    }
  }

  // ----------------------------
  // Projects API
  // ----------------------------
  static async listProjects(): Promise<ProjectListResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/projects`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<ProjectListResponse>(responseData)
    } catch (error) {
      console.error('Error fetching projects:', error)
      throw error
    }
  }

  static async getProject(projectId: string): Promise<Project> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/projects/${projectId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<Project>(responseData)
    } catch (error) {
      console.error('Error fetching project:', error)
      throw error
    }
  }

  static async createProject(payload: ProjectCreateRequest): Promise<Project> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<Project>(responseData)
    } catch (error) {
      console.error('Error creating project:', error)
      throw error
    }
  }

  static async deleteProject(projectId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/projects/${projectId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error deleting project:', error)
      throw error
    }
  }

  static async readProjectFile(projectId: string, fileName: string): Promise<{ content: string | null; exists: boolean }> {
    const staticUrl = ApiService.getProjectFileUrl(projectId, fileName)

    try {
      const response = await apiFetch(staticUrl)

      if (response.status === 404) {
        return { content: null, exists: false }
      }

      if (!response.ok) {
        const errorText = await response.text()
        throw new Error(`Static fetch failed with status ${response.status}: ${errorText}`)
      }

      const content = await response.text()
      return { content, exists: true }
    } catch (error) {
      console.error('Error reading project file:', error)
      throw error
    }
  }

  static async exportNotebookPdf(notebookId: string, version?: number): Promise<Blob> {
    try {
      const versionParam = version !== undefined ? `?version=${version}` : ''
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/export/pdf${versionParam}`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.blob()
    } catch (error) {
      console.error('Error exporting PDF:', error)
      throw error
    }
  }

  static async exportNotebookCompiledHtml(notebookId: string, version?: number): Promise<Blob> {
    try {
      const versionParam = version !== undefined ? `?version=${version}` : ''
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/export/compiled-html${versionParam}`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.blob()
    } catch (error) {
      console.error('Error exporting compiled HTML:', error)
      throw error
    }
  }

  static async shareNotebook(
    notebookId: string,
    version?: number,
    password?: string,
    updatePassword?: boolean
  ): Promise<StandardResponse<{ share_id: string; share_url: string; is_update: boolean }>> {
    try {
      const params = new URLSearchParams()
      if (version !== undefined) params.set('version', String(version))
      if (password) params.set('password', password)
      if (updatePassword !== undefined) params.set('update_password', String(updatePassword))
      const queryString = params.toString() ? `?${params.toString()}` : ''

      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/share${queryString}`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error sharing notebook:', error)
      throw error
    }
  }

  static async getNotebookShare(notebookId: string): Promise<StandardResponse<{
    share: { id: string; share_url: string; created_at: string; updated_at?: string; has_password?: boolean; password?: string | null } | null
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/share`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error getting notebook share:', error)
      throw error
    }
  }

  static async deleteShare(notebookId: string): Promise<StandardResponse<null>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/share`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error deleting share:', error)
      throw error
    }
  }

  // Notebook JSON Share API (shares complete notebook as JSON)
  static async shareNotebookJson(notebookId: string, password?: string): Promise<StandardResponse<{ share_id: string; share_url: string }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/share/notebook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(password ? { password } : {}),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error sharing notebook JSON:', error)
      throw error
    }
  }

  static async listNotebookJsonShares(notebookId: string): Promise<StandardResponse<{
    shares: Array<{ id: string; share_url: string; created_at: string; has_password?: boolean }>
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/shares/notebook`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error listing notebook JSON shares:', error)
      throw error
    }
  }

  static async updateNotebookJsonSharePassword(
    notebookId: string,
    shareId: string,
    password: string | null
  ): Promise<StandardResponse<{ success: boolean; has_password: boolean }>> {
    try {
      const params = new URLSearchParams()
      if (password) {
        params.set('password', password)
      }
      const url = `${API_BASE_URL}/notebooks/${notebookId}/shares/notebook/${shareId}/password${params.toString() ? `?${params.toString()}` : ''}`

      const response = await apiFetch(url, {
        method: 'PUT',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error updating notebook share password:', error)
      throw error
    }
  }

  static async deleteNotebookJsonShare(notebookId: string, shareId: string): Promise<StandardResponse<null>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/shares/notebook/${shareId}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error deleting notebook JSON share:', error)
      throw error
    }
  }

  // ----------------------------
  // Notebook Import API
  // ----------------------------

  /**
   * Fetch a shared notebook using its share ID for import.
   * Returns the notebook export data and summary statistics.
   */
  static async fetchSharedNotebook(shareId: string, password?: string): Promise<StandardResponse<{
    notebook_export: any
    summary: {
      title: string
      description: string | null
      datasets_count: number
      queries_count: number
      messages_count: number
      dashboards_count: number
    }
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/imports/fetch-notebook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ share_id: shareId, password: password || null }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error fetching shared notebook:', error)
      throw error
    }
  }

  /**
   * Test a query on an existing connection or dataset to validate compatibility.
   * Used during import to verify connections/datasets can run the notebook's queries.
   */
  static async testImportQuery(
    params: { connectionId?: string; datasetId?: string },
    query: string
  ): Promise<StandardResponse<{
    success: boolean
    error: string | null
  }>> {
    try {
      const body: Record<string, string> = { query }
      if (params.connectionId) {
        body.connection_id = params.connectionId
      }
      if (params.datasetId) {
        body.dataset_id = params.datasetId
      }

      const response = await apiFetch(`${API_BASE_URL}/imports/test-query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error testing import query:', error)
      throw error
    }
  }

  /**
   * Import a notebook with mapped dataset connections or existing datasets.
   * Creates a new notebook with the imported data.
   */
  static async importNotebook(
    notebookExport: any,
    datasetMappings: Array<{
      dataset_index: number
      connection_id: string | null
      dataset_id?: string | null
      skipped: boolean
    }>
  ): Promise<StandardResponse<{
    notebook_id: string
    imported: {
      datasets: number
      queries: number
      messages: number
      dashboards: number
    }
    skipped_datasets: number
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/imports/import-notebook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_export: notebookExport,
          dataset_mappings: datasetMappings,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error importing notebook:', error)
      throw error
    }
  }

  // User Preferences API
  static async getPreference(type: 'instructions' | 'style_guidelines'): Promise<StandardResponse<{
    preference_type: string
    content: string
    is_default?: boolean
    id?: string
    created_at?: string
    updated_at?: string
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/preferences/${type}`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error fetching ${type} preference:`, error)
      throw error
    }
  }

  static async updatePreference(type: 'instructions' | 'style_guidelines', content: string): Promise<StandardResponse<{
    id: string
    preference_type: string
    content: string
    created_at: string
    updated_at: string
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/preferences/${type}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error updating ${type} preference:`, error)
      throw error
    }
  }

  static async resetPreferenceToDefault(type: 'instructions' | 'style_guidelines'): Promise<StandardResponse<{
    id: string
    preference_type: string
    content: string
    created_at: string
    updated_at: string
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/preferences/${type}/reset`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error resetting ${type} preference to default:`, error)
      throw error
    }
  }

  // ============================================
  // Learnings API
  // ============================================

  static async getLearnings(): Promise<StandardResponse<{
    id: string
    title: string
    learning: string
    context: string | null
    tags: string | null
    datasource_id: string | null
    created_at: string | null
    updated_at: string | null
  }[]>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/learnings`)
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error('Error fetching learnings:', error)
      throw error
    }
  }

  static async deleteLearning(learningId: string): Promise<StandardResponse<{ id: string }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/learnings/${learningId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error('Error deleting learning:', error)
      throw error
    }
  }

  // ============================================
  // Skills API
  // ============================================

  static async getSkills(): Promise<StandardResponse<Array<{
    skill_name: string
    display_name: string
    description: string
    is_configured: boolean
    required_credentials: string[]
    scopes_configured: Array<'user' | 'org'>
    user_scope_created_by: string | null
    org_scope_created_by: string | null
  }>>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/skills`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error fetching skills:', error)
      throw error
    }
  }

  static async saveSkillCredentials(
    skillName: string,
    credentials: Record<string, string>,
    scope: 'user' | 'org' = 'user'
  ): Promise<StandardResponse<{ id: string; skill_name: string; scope: string }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/skills/${skillName}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials, scope }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error saving ${skillName} credentials:`, error)
      throw error
    }
  }

  static async deleteSkillCredentials(skillName: string, scope: 'user' | 'org' = 'user'): Promise<StandardResponse<void>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/skills/${skillName}/credentials?scope=${scope}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error deleting ${skillName} credentials:`, error)
      throw error
    }
  }

  static async shareSkillWithTeam(skillName: string): Promise<StandardResponse<{ id: string; skill_name: string; scope: string }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/skills/${skillName}/share`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error sharing ${skillName} with team:`, error)
      throw error
    }
  }

  static async toggleSkillDomain(
    skillName: string,
    active: boolean,
    scope: 'user' | 'org' = 'user',
  ): Promise<StandardResponse<void>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/skills/${skillName}/domain-toggle`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active, scope }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error toggling ${skillName} domain:`, error)
      throw error
    }
  }

  // ============================================
  // Custom Skills API
  // ============================================

  static async getCustomSkills(): Promise<StandardResponse<Array<{
    id: string
    name: string
    description: string
    scope: 'user' | 'org'
    skill_type: 'general' | 'slack_inbound' | 'slack_outbound'
    is_active: boolean
    created_by: string
    created_by_name: string
    created_at: string
    updated_at: string
    can_execute_api: boolean
  }>>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error fetching custom skills:', error)
      throw error
    }
  }

  static async getCustomSkill(id: string): Promise<StandardResponse<{
    id: string
    name: string
    description: string
    instructions: string
    scope: 'user' | 'org'
    skill_type: 'general' | 'slack_inbound' | 'slack_outbound'
    is_active: boolean
    created_by: string
    created_by_name: string
    created_at: string
    updated_at: string
    can_execute_api: boolean
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills/${id}`)

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error fetching custom skill:', error)
      throw error
    }
  }

  static async createCustomSkill(data: {
    name: string
    description: string
    instructions: string
    scope?: 'user' | 'org'
    skill_type?: 'general' | 'slack_inbound' | 'slack_outbound'
    api_config?: {
      api_base_url: string
      api_type: 'rest' | 'graphql'
      api_auth_type: 'bearer' | 'custom'
      api_domain: string
      api_key: string
    }
  }): Promise<StandardResponse<{
    id: string
    name: string
    description: string
    instructions: string
    scope: 'user' | 'org'
    skill_type: 'general' | 'slack_inbound' | 'slack_outbound'
    is_active: boolean
    created_by: string
    created_by_name: string
    created_at: string
    updated_at: string
    can_execute_api: boolean
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error creating custom skill:', error)
      throw error
    }
  }

  static async updateCustomSkill(id: string, data: {
    name?: string
    description?: string
    instructions?: string
    is_active?: boolean
    api_config?: {
      api_base_url: string
      api_type: 'rest' | 'graphql'
      api_auth_type: 'bearer' | 'custom'
      api_domain: string
      api_key: string
    }
    remove_api_config?: boolean
  }): Promise<StandardResponse<{
    id: string
    name: string
    description: string
    instructions: string
    scope: 'user' | 'org'
    skill_type: 'general' | 'slack_inbound' | 'slack_outbound'
    is_active: boolean
    created_by: string
    created_by_name: string
    created_at: string
    updated_at: string
    can_execute_api: boolean
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error updating custom skill:', error)
      throw error
    }
  }

  static async deleteCustomSkill(id: string): Promise<StandardResponse<void>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills/${id}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error deleting custom skill:', error)
      throw error
    }
  }

  static async shareCustomSkill(id: string): Promise<StandardResponse<{
    id: string
    name: string
    scope: 'user' | 'org'
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills/${id}/share`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error sharing custom skill:', error)
      throw error
    }
  }

  static async unshareCustomSkill(id: string): Promise<StandardResponse<{
    id: string
    name: string
    scope: 'user' | 'org'
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills/${id}/unshare`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error unsharing custom skill:', error)
      throw error
    }
  }

  static async toggleCustomSkillDomain(
    id: string,
    active: boolean,
  ): Promise<StandardResponse<void>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/custom-skills/${id}/domain-toggle`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error(`Error toggling custom skill domain:`, error)
      throw error
    }
  }

  // ============================================
  // Waitlist Methods
  // ============================================

  static async joinWaitlist(email: string, name?: string): Promise<StandardResponse<{
    apiKey: string | null
    userId?: string
    userName?: string | null
    email: string
    tenantId?: string
    tenantName?: string
    hasCredits: boolean
    openrouterKey: string | null
    onboarded: boolean
    hasAccess: boolean
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/waitlist/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, name }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error registering:', error)
      throw error
    }
  }

  static async getStoredCredentials(): Promise<StandardResponse<{
    userId?: number
    userName?: string
    email: string
    apiKey: string
    hasCredits?: boolean
    tenantId?: string
    tenantName?: string
  } | null>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/credentials/get`, {
        method: 'GET',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error getting stored credentials:', error)
      throw error
    }
  }

  /**
   * Logout the current user (local mode only)
   * Clears the active user session to allow switching accounts
   */
  static async logout(): Promise<StandardResponse<null>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/logout`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error logging out:', error)
      throw error
    }
  }


  // ----------------------------
  // Preferred Model Settings API
  // ----------------------------
  static async getPreferredModel(): Promise<{ provider: string | null; model: string | null }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/settings/preferred-model`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
      const responseData = await response.json()
      return extractData<{ provider: string | null; model: string | null }>(responseData)
    } catch (error) {
      console.error('Error fetching preferred model:', error)
      // Return null values on error - don't break the app
      return { provider: null, model: null }
    }
  }

  static async setPreferredModel(provider: string, model: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/settings/preferred-model`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, model }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error setting preferred model:', error)
      throw error
    }
  }

  static async clearPreferredModel(): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/settings/preferred-model`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error clearing preferred model:', error)
      throw error
    }
  }

  static async getAnalyticsOptOut(): Promise<boolean> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/settings/analytics-opt-out`)
      if (!response.ok) return false
      const responseData = await response.json()
      const data = extractData<{ opt_out: boolean }>(responseData)
      return Boolean(data?.opt_out)
    } catch (error) {
      console.error('Error fetching analytics opt-out:', error)
      return false
    }
  }

  static async setAnalyticsOptOut(optOut: boolean): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/settings/analytics-opt-out`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ opt_out: optOut }),
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || `HTTP error! status: ${response.status}`)
      }
    } catch (error) {
      console.error('Error setting analytics opt-out:', error)
      // best-effort: don't throw — local flag still controls client tracking
    }
  }

  // ============================================
  // Auth API Methods (Hosted Mode JWT Auth)
  // ============================================

  private static getAuthToken(): string | null {
    if (typeof window === 'undefined') return null
    return getAccessToken()
  }

  private static getAuthHeaders(): Record<string, string> {
    const token = ApiService.getAuthToken()
    if (token) {
      return { 'Authorization': `Bearer ${token}` }
    }
    return {}
  }

  static async authLogin(email: string, password: string): Promise<{ access_token: string; refresh_token: string; token_type: string }> {
    try {
      const formData = new URLSearchParams()
      formData.append('username', email)
      formData.append('password', password)

      const response = await apiFetch(`${API_BASE_URL}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: formData.toString(),
      })

      if (!response.ok) {
        const errorData = await response.json()
        if (response.status === 400 && errorData.detail === 'LOGIN_BAD_CREDENTIALS') {
          throw new Error('Invalid email or password')
        }
        if (response.status === 400 && errorData.detail === 'LOGIN_USER_NOT_VERIFIED') {
          throw new Error('Please verify your email before logging in')
        }
        if (response.status === 400 && errorData.detail === 'EMAIL_NOT_VERIFIED') {
          const error = new Error('EMAIL_NOT_VERIFIED')
          ;(error as any).code = 'EMAIL_NOT_VERIFIED'
          throw error
        }
        throw new Error(extractErrorMessage(errorData) || `Login failed`)
      }

      const json = await response.json()
      return json.data || json
    } catch (error) {
      console.error('Error logging in:', error)
      throw error
    }
  }

  static async authGoogleLogin(credential: string): Promise<{ access_token: string; refresh_token: string; token_type: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/auth/google`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ credential }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        const errorMessage = extractErrorMessage(errorData)
        const error = new Error(errorMessage || 'Google authentication failed')
        ;(error as any).code = errorData.detail
        throw error
      }

      const json = await response.json()
      return json.data || json
    } catch (error) {
      console.error('Error with Google login:', error)
      throw error
    }
  }

  static async authRefreshToken(refreshToken?: string): Promise<{ access_token: string; refresh_token: string; token_type: string }> {
    try {
      const apiUrl = await getApiBaseUrl()

      const headers: Record<string, string> = { 'Content-Type': 'application/json' }

      if (!refreshToken) {
        const csrfToken = getCsrfToken()
        if (csrfToken) {
          headers['X-CSRF-Token'] = csrfToken
        }
      }

      const response = await fetch(`${apiUrl}/auth/refresh`, {
        method: 'POST',
        headers,
        body: refreshToken ? JSON.stringify({ refresh_token: refreshToken }) : undefined,
        credentials: 'include',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Token refresh failed')
      }

      const json = await response.json()
      return json.data || json
    } catch (error) {
      console.error('Error refreshing token:', error)
      throw error
    }
  }

  static async authLogout(): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Logout failed')
      }
    } catch (error) {
      console.error('Error logging out:', error)
      throw error
    }
  }

  static async authRegister(email: string, password: string, fullName?: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/auth/register`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email,
          password,
          full_name: fullName || null,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        if (response.status === 400 && errorData.detail === 'REGISTER_USER_ALREADY_EXISTS') {
          throw new Error('An account with this email already exists')
        }
        if (response.status === 400 && errorData.detail?.code === 'REGISTER_INVALID_PASSWORD') {
          throw new Error(errorData.detail.reason || 'Invalid password')
        }
        throw new Error(extractErrorMessage(errorData) || `Registration failed`)
      }
    } catch (error) {
      console.error('Error registering:', error)
      throw error
    }
  }

  static async authRegisterWithInvitation(email: string, password: string, fullName: string, invitationToken: string): Promise<{ access_token: string; refresh_token: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/auth/register-with-invitation`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email,
          password,
          full_name: fullName,
          invitation_token: invitationToken,
        }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Registration with invitation failed')
      }

      const data = await response.json()
      return {
        access_token: data.access_token,
        refresh_token: data.refresh_token,
      }
    } catch (error) {
      console.error('Error registering with invitation:', error)
      throw error
    }
  }

  static async authGetMe(): Promise<{
    id: string
    email: string
    full_name: string | null
    avatar_url: string | null
    is_verified: boolean
    is_active: boolean
    is_superuser: boolean
  }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/users/me`, {
        method: 'GET',
        headers: {
          ...ApiService.getAuthHeaders(),
        },
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `Failed to fetch user`)
      }

      // Backend wraps responses in { success, message, data } format
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching user:', error)
      throw error
    }
  }

  static async authForgotPassword(email: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/auth/forgot-password`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email }),
      })

      // FastAPI Users returns 202 for forgot-password regardless of email existence (security)
      if (!response.ok && response.status !== 202) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || `Failed to send reset email`)
      }
    } catch (error) {
      console.error('Error sending forgot password:', error)
      throw error
    }
  }

  static async authResetPassword(token: string, password: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/auth/reset-password`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ token, password }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        if (response.status === 400 && errorData.detail === 'RESET_PASSWORD_BAD_TOKEN') {
          throw new Error('Invalid or expired reset link')
        }
        if (response.status === 400 && errorData.detail?.code === 'RESET_PASSWORD_INVALID_PASSWORD') {
          throw new Error(errorData.detail.reason || 'Invalid password')
        }
        throw new Error(extractErrorMessage(errorData) || `Failed to reset password`)
      }
    } catch (error) {
      console.error('Error resetting password:', error)
      throw error
    }
  }

  // ==================== Tenant/Scopes API ====================

  static async getTenants(): Promise<Array<{
    tenant_id: string
    tenant_name: string
    role: string
    scopes: string[]
  }>> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/scopes/all`, {
        method: 'GET',
        headers: {
          ...ApiService.getAuthHeaders(),
        },
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch tenants')
      }

      // Backend wraps responses in { success, message, data: { tenants: [...] } } format
      const json = await response.json()
      return json.data.tenants
    } catch (error) {
      console.error('Error fetching tenants:', error)
      throw error
    }
  }

  static async createTenant(name: string): Promise<{
    tenant_id: string
    tenant_name: string
    role: string
    scopes: string[]
  }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/tenants`, {
        method: 'POST',
        headers: {
          ...ApiService.getAuthHeaders(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to create workspace')
      }

      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error creating workspace:', error)
      throw error
    }
  }

  // ==================== Team Management API ====================

  static async getTeamMembers(): Promise<any[]> {
    try {
      const tenantId = getActiveTenantId()
      if (!tenantId) {
        throw new Error('No active tenant')
      }

      const response = await apiFetch(`${API_BASE_URL}/tenants/${tenantId}/members`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch team members')
      }

      const json = await response.json()
      return json.data.items
    } catch (error) {
      console.error('Error fetching team members:', error)
      throw error
    }
  }

  static async getTeamMemberStats(): Promise<any> {
    try {
      const tenantId = getActiveTenantId()
      if (!tenantId) {
        throw new Error('No active tenant')
      }

      const response = await apiFetch(`${API_BASE_URL}/tenants/${tenantId}/stats/members`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch team stats')
      }

      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching team stats:', error)
      throw error
    }
  }

  static async getPendingInvitations(): Promise<any[]> {
    try {
      const tenantId = getActiveTenantId()
      if (!tenantId) {
        throw new Error('No active tenant')
      }

      const response = await apiFetch(`${API_BASE_URL}/tenants/${tenantId}/invitations`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch pending invitations')
      }

      const json = await response.json()
      return json.data.items
    } catch (error) {
      console.error('Error fetching pending invitations:', error)
      throw error
    }
  }

  static async inviteTeamMember(data: { email: string; role: 'admin' | 'member'; message?: string }): Promise<any> {
    try {
      const tenantId = getActiveTenantId()
      if (!tenantId) {
        throw new Error('No active tenant')
      }

      const response = await apiFetch(`${API_BASE_URL}/tenants/${tenantId}/invitations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to send invitation')
      }

      const json = await response.json()
      return json
    } catch (error) {
      console.error('Error inviting team member:', error)
      throw error
    }
  }

  static async updateMemberRole(memberId: string, role: string): Promise<any> {
    try {
      const tenantId = getActiveTenantId()
      if (!tenantId) {
        throw new Error('No active tenant')
      }

      const response = await apiFetch(`${API_BASE_URL}/tenants/${tenantId}/members/${memberId}/role`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to update member role')
      }

      const json = await response.json()
      return json
    } catch (error) {
      console.error('Error updating member role:', error)
      throw error
    }
  }

  static async removeMember(memberId: string): Promise<void> {
    try {
      const tenantId = getActiveTenantId()
      if (!tenantId) {
        throw new Error('No active tenant')
      }

      const response = await apiFetch(`${API_BASE_URL}/tenants/${tenantId}/members/${memberId}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to remove member')
      }
    } catch (error) {
      console.error('Error removing member:', error)
      throw error
    }
  }

  static async cancelInvitation(invitationId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/invitations/${invitationId}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to cancel invitation')
      }
    } catch (error) {
      console.error('Error canceling invitation:', error)
      throw error
    }
  }

  static async resendInvitation(invitationId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/invitations/${invitationId}/resend`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to resend invitation')
      }

      const json = await response.json()
      return json
    } catch (error) {
      console.error('Error resending invitation:', error)
      throw error
    }
  }

  static async getInvitationLink(invitationId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/invitations/${invitationId}/link`, {
        method: 'POST',
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to get invitation link')
      }

      const json = await response.json()
      return json
    } catch (error) {
      console.error('Error getting invitation link:', error)
      throw error
    }
  }

  static async verifyInvitation(token: string): Promise<{
    email: string;
    tenant_name: string;
    tenant_id: string;
    role: string;
    user_exists: boolean;
    user_verified: boolean
  }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/invitations/verify?token=${encodeURIComponent(token)}`)

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to verify invitation')
      }

      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error verifying invitation:', error)
      throw error
    }
  }

  static async acceptInvitation(token: string): Promise<{ success: boolean; member_id: string; tenant_id: string; role: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/invitations/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to accept invitation')
      }

      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error accepting invitation:', error)
      throw error
    }
  }

  // ============================================
  // Folder API Methods
  // ============================================

  static async getFolders(): Promise<{ items: any[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch folders')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching folders:', error)
      throw error
    }
  }

  static async getFolder(folderId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch folder')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching folder:', error)
      throw error
    }
  }

  static async createFolder(data: { name: string; description?: string; is_public?: boolean }): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to create folder')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error creating folder:', error)
      throw error
    }
  }

  static async updateFolder(folderId: string, data: { name?: string; description?: string; is_public?: boolean }): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to update folder')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error updating folder:', error)
      throw error
    }
  }

  static async deleteFolder(folderId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to delete folder')
      }
    } catch (error) {
      console.error('Error deleting folder:', error)
      throw error
    }
  }

  static async getFolderMembers(folderId: string): Promise<{ items: any[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/members`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch folder members')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching folder members:', error)
      throw error
    }
  }

  static async addFolderMember(folderId: string, userId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/members`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to add folder member')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error adding folder member:', error)
      throw error
    }
  }

  static async removeFolderMember(folderId: string, memberId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/members/${memberId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to remove folder member')
      }
    } catch (error) {
      console.error('Error removing folder member:', error)
      throw error
    }
  }

  static async getFolderNotebooks(folderId: string): Promise<{ items: any[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/notebooks`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch folder notebooks')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching folder notebooks:', error)
      throw error
    }
  }

  static async shareNotebookToFolder(folderId: string, notebookId: string, isSnapshot: boolean = false): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/notebooks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notebook_id: notebookId, is_snapshot: isSnapshot }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to share notebook to folder')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error sharing notebook to folder:', error)
      throw error
    }
  }

  static async unshareNotebookFromFolder(folderId: string, notebookId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/notebooks/${notebookId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to unshare notebook from folder')
      }
    } catch (error) {
      console.error('Error unsharing notebook from folder:', error)
      throw error
    }
  }

  static async cloneNotebookFromFolder(folderId: string, notebookId: string, newName?: string): Promise<{
    notebook_id: string;
    notebook_name: string;
    messages_cloned: number;
    queries_cloned: number;
    dashboards_cloned: number;
    datasets_cloned: number;
    connection_access_warnings: string[] | null;
  }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/notebooks/${notebookId}/clone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to clone notebook')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error cloning notebook from folder:', error)
      throw error
    }
  }

  static async updateSnapshot(folderId: string, notebookId: string): Promise<{ snapshot_updated_at: string }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/notebooks/${notebookId}/snapshot`, {
        method: 'PUT',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to update snapshot')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error updating snapshot:', error)
      throw error
    }
  }

  static async getNotebookFolders(notebookId: string): Promise<{ items: NotebookFolder[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/folders`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch notebook folders')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching notebook folders:', error)
      throw error
    }
  }

  // Dashboard Folder methods
  static async getFolderDashboards(folderId: string): Promise<{ items: any[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/dashboards`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch folder dashboards')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching folder dashboards:', error)
      throw error
    }
  }

  static async shareDashboardToFolder(folderId: string, dashboardId: string): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/dashboards`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dashboard_id: dashboardId }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to share dashboard to folder')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error sharing dashboard to folder:', error)
      throw error
    }
  }

  static async updateFolderDashboardVersion(
    folderId: string,
    oldDashboardId: string,
    newDashboardId: string
  ): Promise<any> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/dashboards/${oldDashboardId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_dashboard_id: newDashboardId }),
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to update dashboard version')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error updating dashboard version:', error)
      throw error
    }
  }

  static async unshareDashboardFromFolder(folderId: string, dashboardId: string): Promise<void> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/${folderId}/dashboards/${dashboardId}`, {
        method: 'DELETE',
      })
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to unshare dashboard from folder')
      }
    } catch (error) {
      console.error('Error unsharing dashboard from folder:', error)
      throw error
    }
  }

  static async getDashboardFolders(dashboardId: string): Promise<{ items: DashboardFolder[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/dashboards/${dashboardId}/folders`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch dashboard folders')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching dashboard folders:', error)
      throw error
    }
  }

  static async getNotebookDashboardFolders(notebookId: string): Promise<{ items: DashboardFolder[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/dashboard-folders`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch notebook dashboard folders')
      }
      const json = await response.json()
      return json.data || { items: [], total: 0 }
    } catch (error) {
      console.error('Error getting notebook dashboard folders:', error)
      return { items: [], total: 0 }
    }
  }

  // ============================================
  // All Dashboards API Methods
  // ============================================

  static async getAllDashboards(): Promise<DashboardsByFolder> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/dashboards`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch dashboards')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching all dashboards:', error)
      throw error
    }
  }

  // ============================================
  // All Notebooks API Methods
  // ============================================

  static async getAllSharedNotebooks(): Promise<NotebooksByFolder> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/folders/all-notebooks`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch notebooks')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching all shared notebooks:', error)
      throw error
    }
  }

  // ============================================
  // Viewer API Methods
  // ============================================

  static async getViewerDashboards(): Promise<{ items: ViewerDashboard[]; total: number }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/viewer/dashboards`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch viewer dashboards')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching viewer dashboards:', error)
      throw error
    }
  }

  static async getViewerDashboard(dashboardId: string): Promise<ViewerDashboardDetail> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/viewer/dashboards/${dashboardId}`)
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch viewer dashboard')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching viewer dashboard:', error)
      throw error
    }
  }

  static async getViewerDashboardFilters(dashboardId: string): Promise<DashboardFilterConfigResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/viewer/dashboards/${dashboardId}/filters`)
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch dashboard filters')
      }
      const json = await response.json()
      return normalizeDashboardFilterConfig(json)
    } catch (error) {
      console.error('Error fetching viewer dashboard filters:', error)
      throw error
    }
  }

  static async preflightViewerDashboardQueries(
    dashboardId: string,
    payload: {
      query_ids?: string[]
      queries_with_filters?: QueryWithFilterValuesPayload[]
      max_parallel?: number
    }
  ): Promise<BatchFilterPreflightResponse> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/viewer/dashboards/${dashboardId}/queries/batch/preflight`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(extractErrorMessage(errorData) || 'Failed to preflight viewer dashboard filters')
      }
      return await response.json() as BatchFilterPreflightResponse
    } catch (error) {
      console.error('Error preflighting viewer dashboard filters:', error)
      throw error
    }
  }

  static async getDashboardThumbnail(dashboardId: string): Promise<string> {
    try {
      const detail = await this.getViewerDashboard(dashboardId)
      return detail.html_content
    } catch (error) {
      console.error('Error fetching dashboard thumbnail:', error)
      throw error
    }
  }

  // ============================================
  // Cache Management
  // ============================================

  static async getDashboardCacheStatus(dashboardId: string): Promise<{
    dashboard_id: string
    last_refreshed_at: string | null
    refreshed_by: string | null
    query_count: number | null
    is_stale: boolean
  }> {
    const response = await apiFetch(`${API_BASE_URL}/cache/dashboards/${dashboardId}/status`)
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to fetch cache status')
    }
    const json = await response.json()
    return json.data
  }

  static async refreshDashboardCache(dashboardId: string): Promise<{
    success: boolean
    refreshed_queries: number
    failed_queries: number
    total_queries: number
    refreshed_at: string
  }> {
    const response = await apiFetch(`${API_BASE_URL}/cache/dashboards/${dashboardId}/refresh`, {
      method: 'POST',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to refresh cache')
    }
    const json = await response.json()
    return json.data
  }

  // ============================================
  // Slack Integration
  // ============================================

  static async getSlackConfig(): Promise<{
    id: string
    slack_team_id: string
    slack_team_name: string | null
    is_active: boolean
    default_llm_connection_id: string | null
    created_at: string
  } | null> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/slack/config`)
      if (!response.ok) {
        if (response.status === 404) {
          return null
        }
        const errorData = await response.json()
        throw new Error(extractErrorMessage(errorData) || 'Failed to fetch Slack config')
      }
      const json = await response.json()
      return json.data
    } catch (error) {
      console.error('Error fetching Slack config:', error)
      throw error
    }
  }

  static async connectSlack(data: {
    bot_token: string
    signing_secret: string
    default_llm_connection_id?: string | null
  }): Promise<{
    id: string
    slack_team_id: string
    slack_team_name: string | null
    is_active: boolean
    default_llm_connection_id: string | null
    created_at: string
  }> {
    const response = await apiFetch(`${API_BASE_URL}/slack/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to connect Slack')
    }
    const json = await response.json()
    return json.data
  }

  static async disconnectSlack(): Promise<void> {
    const response = await apiFetch(`${API_BASE_URL}/slack/config`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to disconnect Slack')
    }
  }

  static async updateSlackConfig(data: {
    bot_token?: string
    signing_secret?: string
    default_llm_connection_id?: string | null
  }): Promise<{
    id: string
    slack_team_id: string
    slack_team_name: string | null
    is_active: boolean
    default_llm_connection_id: string | null
    created_at: string
  }> {
    const response = await apiFetch(`${API_BASE_URL}/slack/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to update Slack config')
    }
    const json = await response.json()
    return json.data
  }

  static async getSlackChannels(): Promise<Array<{ id: string; name: string }>> {
    const response = await apiFetch(`${API_BASE_URL}/slack/channels`)
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to fetch Slack channels')
    }
    const json = await response.json()
    return json.data || []
  }

  static async testSlackChannel(channelId: string): Promise<{ success: boolean; message: string }> {
    const response = await apiFetch(`${API_BASE_URL}/slack/test-channel/${channelId}`, {
      method: 'POST',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to send test message')
    }
    const json = await response.json()
    return { success: true, message: json.message || 'Test message sent successfully' }
  }

  // ============================================
  // Schedules
  // ============================================

  static async listSchedules(): Promise<ScheduleRead[]> {
    const response = await apiFetch(`${API_BASE_URL}/schedules`)
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to fetch schedules')
    }
    const json = await response.json()
    return json.data || []
  }

  static async getNotebookSchedules(notebookId: string): Promise<ScheduleRead[]> {
    const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/schedules`)
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to fetch schedules')
    }
    const json = await response.json()
    return json.data || []
  }

  static async createSchedule(notebookId: string, data: ScheduleCreate): Promise<ScheduleRead> {
    const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/schedules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to create schedule')
    }
    const json = await response.json()
    return json.data
  }

  static async updateSchedule(scheduleId: string, data: ScheduleUpdate): Promise<ScheduleRead> {
    const response = await apiFetch(`${API_BASE_URL}/schedules/${scheduleId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to update schedule')
    }
    const json = await response.json()
    return json.data
  }

  static async deleteSchedule(scheduleId: string): Promise<void> {
    const response = await apiFetch(`${API_BASE_URL}/schedules/${scheduleId}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to delete schedule')
    }
  }

  static async testSchedule(scheduleId: string): Promise<ScheduleTestResult> {
    const response = await apiFetch(`${API_BASE_URL}/schedules/${scheduleId}/test`, {
      method: 'POST',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to test schedule')
    }
    const json = await response.json()
    return json.data
  }

  static async previewSchedule(notebookId: string): Promise<ScheduleTestResult> {
    const response = await apiFetch(`${API_BASE_URL}/notebooks/${notebookId}/schedules/preview`, {
      method: 'POST',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to preview schedule')
    }
    const json = await response.json()
    return json.data
  }

  // MCP Keys
  static async listMCPKeys(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/mcp/keys`, {
      method: 'GET',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to fetch MCP keys')
    }
    return response.json()
  }

  static async createMCPKey(name: string): Promise<any> {
    const body = { name }
    const response = await apiFetch(`${API_BASE_URL}/mcp/keys`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to create MCP key')
    }
    return response.json()
  }

  static async getMCPStdioConfig(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/mcp/stdio-config`, {
      method: 'GET',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to fetch MCP stdio config')
    }
    return response.json()
  }

  static async revokeMCPKey(keyId: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/mcp/keys/${keyId}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(extractErrorMessage(errorData) || 'Failed to revoke MCP key')
    }
    return response.json()
  }

}
