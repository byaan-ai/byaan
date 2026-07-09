import type { StateCreator } from 'zustand'
import { ApiService } from '@/services/api'

export type SkillScope = 'user' | 'org'

export interface CredentialField {
  key: string
  label: string
  placeholder: string
  help: string
  optional?: boolean
  type?: string
  options?: { value: string; label: string }[]
  default?: string
  depends_on?: { key: string; value: string }
}

export interface SkillStatus {
  skill_name: string
  display_name: string
  description: string
  is_configured: boolean
  required_credentials: string[]
  credential_fields: CredentialField[]
  emoji: string
  homepage: string
  domain: string
  scopes_configured: SkillScope[]
  user_scope_created_by: string | null
  org_scope_created_by: string | null
  org_scope_created_by_name: string | null
  domain_active: boolean
}

export type SkillType = 'general' | 'slack_inbound' | 'slack_outbound' | 'github_analysis'

export interface CustomSkill {
  id: string
  name: string
  description: string
  instructions: string
  scope: SkillScope
  skill_type: SkillType
  is_active: boolean
  created_by: string
  created_by_name: string
  created_at: string
  updated_at: string
  can_execute_api: boolean
  api_base_url: string | null
  api_type: string | null
  api_auth_type: string | null
  api_domain: string | null
  domain_active: boolean
  has_credentials: boolean
  github_repo_id: string | null
  github_analysis_type: string | null
  github_repo_name: string | null
}

export interface ApiConfig {
  api_base_url: string
  api_type: 'rest' | 'graphql'
  api_auth_type: 'bearer' | 'custom'
  api_domain: string
  api_key: string
}

export interface CreateCustomSkillData {
  name: string
  description: string
  instructions: string
  scope?: SkillScope
  skill_type?: SkillType
  api_config?: ApiConfig
  remove_api_config?: boolean
}

export interface LearningRecord {
  id: string
  title: string
  learning: string
  context: string | null
  tags: string | null
  datasource_id: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ContextSlice {
  isSidebarOpen: boolean
  activeSection: 'instructions' | 'skills' | 'style' | 'database' | 'learnings'
  selectedDatasourceId: string | null
  databaseContext: DatabaseContext[]
  datasourceSchemas: Record<string, any>
  datasourceAnnotations: Record<string, any[]>
  globalInstructions: string
  styleGuidelines: string
  isLoadingPreferences: boolean
  learnings: LearningRecord[]
  isLoadingLearnings: boolean
  skills: SkillStatus[]
  isLoadingSkills: boolean
  customSkills: CustomSkill[]
  isLoadingCustomSkills: boolean
  pendingSkillId: string | null

  toggleSidebar: () => void
  openSidebar: (section?: 'instructions' | 'skills' | 'style' | 'database' | 'learnings', skillId?: string) => void
  setPendingSkillId: (id: string | null) => void
  closeSidebar: () => void
  setActiveSection: (section: 'instructions' | 'skills' | 'style' | 'database' | 'learnings') => void
  setSelectedDatasource: (datasourceId: string) => void

  loadDatasourceSchema: (datasourceId: string, schema: any) => void
  loadDatasourceAnnotations: (datasourceId: string) => Promise<void>
  updateTableDescription: (datasourceId: string, tableName: string, description: string) => Promise<void>
  updateColumnAnnotation: (datasourceId: string, tableName: string, columnName: string, annotation: string) => Promise<void>
  toggleColumnRedaction: (datasourceId: string, tableName: string, columnName: string, enable: boolean) => Promise<void>
  toggleTableRedaction: (datasourceId: string, tableName: string, enable: boolean) => Promise<void>

  loadPreferencesFromBackend: () => Promise<void>
  updateInstructions: (content: string) => Promise<void>
  updateStyleGuidelines: (content: string) => Promise<void>
  resetInstructionsToDefault: () => Promise<void>
  resetStyleGuidelinesToDefault: () => Promise<void>

  loadLearnings: () => Promise<void>
  deleteLearning: (id: string) => Promise<void>

  clearContext: () => void

  loadSkills: () => Promise<void>
  saveSkillCredentials: (skillName: string, credentials: Record<string, string>, scope?: SkillScope) => Promise<void>
  deleteSkillCredentials: (skillName: string, scope?: SkillScope) => Promise<void>
  shareSkillWithTeam: (skillName: string) => Promise<void>
  toggleSkillDomain: (skillName: string, active: boolean, scope?: SkillScope) => Promise<void>

  loadCustomSkills: () => Promise<void>
  createCustomSkill: (data: CreateCustomSkillData) => Promise<CustomSkill>
  updateCustomSkill: (id: string, data: Partial<CreateCustomSkillData>) => Promise<CustomSkill>
  deleteCustomSkill: (id: string) => Promise<void>
  shareCustomSkill: (id: string) => Promise<void>
  unshareCustomSkill: (id: string) => Promise<void>
  toggleCustomSkillDomain: (id: string, active: boolean) => Promise<void>
}

// Types
export interface TableAnnotation {
  tableName: string;
  semanticDescription: string;
  redacted?: boolean;
  columns: {
    name: string;
    type: string;
    annotation?: string;
    redacted?: boolean;
  }[];
}

export interface DatabaseContext {
  datasourceId: string;
  datasourceName: string;
  datasourceType: string;
  tables: TableAnnotation[];
}

const dummyDatabaseContext: DatabaseContext[] = [
  {
    datasourceId: 'ds-1',
    datasourceName: 'PostgreSQL - Production DB',
    datasourceType: 'postgres',
    tables: [
      {
        tableName: 'patients',
        semanticDescription: 'Core patient records containing demographic information, medical history identifiers, and contact details. This is the central table that most other tables reference.',
        columns: [
          { name: 'id', type: 'UUID', annotation: 'Primary key, auto-generated patient identifier' },
          { name: 'first_name', type: 'VARCHAR(100)' },
          { name: 'last_name', type: 'VARCHAR(100)' },
          { name: 'date_of_birth', type: 'DATE', annotation: 'Used for age calculations in reports' },
          { name: 'gender', type: 'VARCHAR(20)', annotation: 'Values: Male, Female, Other, Not Specified' },
          { name: 'email', type: 'VARCHAR(255)', annotation: 'May be null for some patients' },
          { name: 'phone', type: 'VARCHAR(20)' },
          { name: 'client_id', type: 'UUID', annotation: 'Foreign key to clients table' },
          { name: 'created_at', type: 'TIMESTAMP' },
        ],
      },
      {
        tableName: 'appointments',
        semanticDescription: 'Scheduled appointments for patients, including past, current, and future appointments. Links patients to providers and tracks appointment status.',
        columns: [
          { name: 'id', type: 'UUID' },
          { name: 'patient_id', type: 'UUID', annotation: 'References patients.id' },
          { name: 'provider_id', type: 'UUID' },
          { name: 'appointment_date', type: 'TIMESTAMP', annotation: 'Scheduled date and time' },
          { name: 'status', type: 'VARCHAR(50)', annotation: 'Values: scheduled, completed, cancelled, no-show' },
          { name: 'appointment_type', type: 'VARCHAR(100)' },
          { name: 'notes', type: 'TEXT' },
        ],
      },
      {
        tableName: 'patient_address',
        semanticDescription: 'Physical addresses for patients. One-to-many relationship as patients can have multiple addresses (home, work, billing).',
        columns: [
          { name: 'id', type: 'UUID' },
          { name: 'patient_id', type: 'UUID', annotation: 'Foreign key to patients table' },
          { name: 'address_type', type: 'VARCHAR(50)', annotation: 'home, work, billing, etc.' },
          { name: 'street', type: 'VARCHAR(255)' },
          { name: 'city', type: 'VARCHAR(100)' },
          { name: 'state', type: 'VARCHAR(50)' },
          { name: 'zip_code', type: 'VARCHAR(20)' },
        ],
      },
    ],
  },
  {
    datasourceId: 'ds-2',
    datasourceName: 'MongoDB - Analytics',
    datasourceType: 'mongodb',
    tables: [
      {
        tableName: 'user_events',
        semanticDescription: 'Event tracking collection for user interactions and behavioral analytics. Contains rich nested documents with event metadata.',
        columns: [
          { name: '_id', type: 'ObjectId', annotation: 'Unique event identifier' },
          { name: 'userId', type: 'String' },
          { name: 'eventType', type: 'String', annotation: 'click, view, purchase, etc.' },
          { name: 'timestamp', type: 'Date' },
          { name: 'metadata', type: 'Object', annotation: 'Flexible event properties' },
          { name: 'sessionId', type: 'String' },
        ],
      },
    ],
  },
  {
    datasourceId: 'ds-3',
    datasourceName: 'CSV - Sales Data',
    datasourceType: 'csv',
    tables: [
      {
        tableName: 'sales_2024.csv',
        semanticDescription: 'Annual sales transactions with product details, customer information, and revenue metrics. Primary dataset for sales analysis and forecasting.',
        columns: [
          { name: 'transaction_id', type: 'string' },
          { name: 'date', type: 'date', annotation: 'Transaction date in YYYY-MM-DD format' },
          { name: 'customer_name', type: 'string' },
          { name: 'product_name', type: 'string' },
          { name: 'quantity', type: 'integer' },
          { name: 'unit_price', type: 'float', annotation: 'Price in USD' },
          { name: 'total_amount', type: 'float', annotation: 'Calculated: quantity * unit_price' },
          { name: 'region', type: 'string', annotation: 'Sales region: North, South, East, West' },
        ],
      },
    ],
  },
];

export const createContextSlice: StateCreator<ContextSlice> = (set, get) => ({
  isSidebarOpen: false,
  activeSection: 'instructions',
  selectedDatasourceId: null,
  databaseContext: [],
  datasourceSchemas: {},
  datasourceAnnotations: {},
  globalInstructions: '',
  styleGuidelines: '',
  isLoadingPreferences: false,
  learnings: [],
  isLoadingLearnings: false,
  skills: [],
  isLoadingSkills: false,
  customSkills: [],
  isLoadingCustomSkills: false,
  pendingSkillId: null,

  // UI Actions
  toggleSidebar: () => set((state) => ({ isSidebarOpen: !state.isSidebarOpen })),

  setPendingSkillId: (id) => set({ pendingSkillId: id }),

  openSidebar: (section, skillId) => {
    set((state) => ({
      isSidebarOpen: true,
      activeSection: section || state.activeSection,
      pendingSkillId: skillId ?? state.pendingSkillId,
    }))

    // Load preferences
    const { globalInstructions, styleGuidelines, isLoadingPreferences, loadPreferencesFromBackend } = get()
    if (!isLoadingPreferences && globalInstructions === '' && styleGuidelines === '') {
      loadPreferencesFromBackend().catch(error => {
        console.error('Failed to load preferences on sidebar open:', error)
      })
    }
  },

  closeSidebar: () => set({ isSidebarOpen: false }),

  setActiveSection: (section) => set({ activeSection: section }),

  setSelectedDatasource: (datasourceId) => set({ selectedDatasourceId: datasourceId }),

  // Load schema for a datasource
  loadDatasourceSchema: (datasourceId, schema) =>
    set((state) => ({
      datasourceSchemas: {
        ...state.datasourceSchemas,
        [datasourceId]: schema,
      },
    })),

  // Load annotationsfor a datasource
  loadDatasourceAnnotations: async (datasourceId) => {
    try {
      const response = await ApiService.getDatasourceAnnotations(datasourceId)
      const annotations = response.data || []

      set((state) => ({
        datasourceAnnotations: {
          ...state.datasourceAnnotations,
          [datasourceId]: annotations,
        },
      }))

      // Also update databaseContext with loaded annotations
      const schema = get().datasourceSchemas[datasourceId]
      if (schema) {
        const dbContext: DatabaseContext = {
          datasourceId,
          datasourceName: schema.datasource_name || datasourceId,
          datasourceType: schema.datasource_type || 'unknown',
          tables: Object.entries(schema.schema || {}).map(([tableName, tableData]: [string, any]) => {
            const tableDescAnnotation = annotations.find(
              (ann: any) =>
                ann.table_name === tableName &&
                ann.annotation_type === 'table_description' &&
                ann.column_name === null
            )

            const isTableRedacted = annotations.some(
              (ann: any) =>
                ann.table_name === tableName &&
                ann.annotation_type === 'table_redaction' &&
                ann.column_name === null
            )

            return {
              tableName,
              semanticDescription: tableDescAnnotation?.content || '',
              redacted: isTableRedacted || undefined,
              columns: (tableData.columns || tableData.sample_fields || []).map((col: any) => {
                const colName = typeof col === 'string' ? col : col.name
                const colType = typeof col === 'string' ? 'unknown' : col.type

                const colAnnotation = annotations.find(
                  (ann: any) =>
                    ann.table_name === tableName &&
                    ann.column_name === colName &&
                    ann.annotation_type === 'column_annotation'
                )

                const isRedacted = annotations.some(
                  (ann: any) =>
                    ann.table_name === tableName &&
                    ann.column_name === colName &&
                    ann.annotation_type === 'column_redaction'
                )

                return {
                  name: colName,
                  type: colType,
                  annotation: colAnnotation?.content || '',
                  redacted: isRedacted || undefined,
                }
              }),
            }
          }),
        }

        // Update or add to databaseContext
        set((state) => ({
          databaseContext: [
            ...state.databaseContext.filter((db) => db.datasourceId !== datasourceId),
            dbContext,
          ],
        }))
      }
    } catch (error) {
      console.error(`Failed to load annotations for datasource ${datasourceId}:`, error)
      throw error
    }
  },

  // Database Understanding Actions
  updateTableDescription: async (datasourceId, tableName, description) => {
    // Optimistically update UI
    set((state) => {
      // Find existing datasource context
      const dbIndex = state.databaseContext.findIndex((db) => db.datasourceId === datasourceId);

      // If datasource doesn't exist in context, create it from schema
      if (dbIndex === -1) {
        const schema = state.datasourceSchemas[datasourceId];
        if (!schema) return state;

        const newDb: DatabaseContext = {
          datasourceId,
          datasourceName: schema.datasource_name || datasourceId,
          datasourceType: schema.datasource_type || 'unknown',
          tables: Object.entries(schema.schema || {}).map(([tName, tData]: [string, any]) => ({
            tableName: tName,
            semanticDescription: tName === tableName ? description : '',
            columns: (tData.columns || []).map((col: any) => ({
              name: col.name,
              type: col.type,
              annotation: '',
            })),
          })),
        };

        return {
          databaseContext: [...state.databaseContext, newDb],
        };
      }

      // Update existing datasource
      return {
        databaseContext: state.databaseContext.map((db) =>
          db.datasourceId === datasourceId
            ? {
                ...db,
                tables: db.tables.map((table) =>
                  table.tableName === tableName
                    ? { ...table, semanticDescription: description }
                    : table
                ),
              }
            : db
        ),
      };
    })

    try {
      await ApiService.createDatasourceAnnotation(datasourceId, {
        table_name: tableName,
        column_name: null,
        annotation_type: 'table_description',
        content: description,
      })
    } catch (error) {
      console.error('Failed to save table description to backend:', error)
      throw error
    }
  },

  updateColumnAnnotation: async (datasourceId, tableName, columnName, annotation) => {
    set((state) => {
      // Find existing datasource context
      const dbIndex = state.databaseContext.findIndex((db) => db.datasourceId === datasourceId);

      // If datasource doesn't exist in context, create it from schema
      if (dbIndex === -1) {
        const schema = state.datasourceSchemas[datasourceId];
        if (!schema) return state;

        const newDb: DatabaseContext = {
          datasourceId,
          datasourceName: schema.datasource_name || datasourceId,
          datasourceType: schema.datasource_type || 'unknown',
          tables: Object.entries(schema.schema || {}).map(([tName, tData]: [string, any]) => ({
            tableName: tName,
            semanticDescription: '',
            columns: (tData.columns || []).map((col: any) => ({
              name: col.name,
              type: col.type,
              annotation: tName === tableName && col.name === columnName ? annotation : '',
            })),
          })),
        };

        return {
          databaseContext: [...state.databaseContext, newDb],
        };
      }

      // Update existing datasource
      return {
        databaseContext: state.databaseContext.map((db) =>
          db.datasourceId === datasourceId
            ? {
                ...db,
                tables: db.tables.map((table) =>
                  table.tableName === tableName
                    ? {
                        ...table,
                        columns: table.columns.map((col) =>
                          col.name === columnName
                            ? { ...col, annotation }
                            : col
                        ),
                      }
                    : table
                ),
              }
            : db
        ),
      };
    })

    try {
      await ApiService.createDatasourceAnnotation(datasourceId, {
        table_name: tableName,
        column_name: columnName,
        annotation_type: 'column_annotation',
        content: annotation,
      })
    } catch (error) {
      console.error('Failed to save column annotation to backend:', error)
      throw error
    }
  },

  toggleColumnRedaction: async (datasourceId, tableName, columnName, enable) => {
    set((state) => ({
      databaseContext: state.databaseContext.map((db) =>
        db.datasourceId === datasourceId
          ? {
              ...db,
              tables: db.tables.map((table) =>
                table.tableName === tableName
                  ? {
                      ...table,
                      columns: table.columns.map((col) =>
                        col.name === columnName
                          ? { ...col, redacted: enable || undefined }
                          : col
                      ),
                    }
                  : table
              ),
            }
          : db
      ),
    }))

    try {
      await ApiService.toggleColumnRedaction(datasourceId, tableName, columnName, enable)
    } catch (error) {
      console.error('Failed to toggle column redaction:', error)
      set((state) => ({
        databaseContext: state.databaseContext.map((db) =>
          db.datasourceId === datasourceId
            ? {
                ...db,
                tables: db.tables.map((table) =>
                  table.tableName === tableName
                    ? {
                        ...table,
                        columns: table.columns.map((col) =>
                          col.name === columnName
                            ? { ...col, redacted: !enable || undefined }
                            : col
                        ),
                      }
                    : table
                ),
              }
            : db
        ),
      }))
      throw error
    }
  },

  toggleTableRedaction: async (datasourceId, tableName, enable) => {
    set((state) => ({
      databaseContext: state.databaseContext.map((db) =>
        db.datasourceId === datasourceId
          ? {
              ...db,
              tables: db.tables.map((table) =>
                table.tableName === tableName
                  ? { ...table, redacted: enable || undefined }
                  : table
              ),
            }
          : db
      ),
    }))

    try {
      await ApiService.toggleTableRedaction(datasourceId, tableName, enable)
    } catch (error) {
      console.error('Failed to toggle table redaction:', error)
      set((state) => ({
        databaseContext: state.databaseContext.map((db) =>
          db.datasourceId === datasourceId
            ? {
                ...db,
                tables: db.tables.map((table) =>
                  table.tableName === tableName
                    ? { ...table, redacted: !enable || undefined }
                    : table
                ),
              }
            : db
        ),
      }))
      throw error
    }
  },

  // Load default preferences from backend on app startup
  loadPreferencesFromBackend: async () => {
    const { isAuthenticated } = get()

    // Only load preferences if authenticated
    if (!isAuthenticated) {
      return
    }

    set({ isLoadingPreferences: true })
    try {
      const [instructionsRes, styleGuidelinesRes] = await Promise.all([
        ApiService.getPreference('instructions'),
        ApiService.getPreference('style_guidelines'),
      ])

      set({
        globalInstructions: instructionsRes.data?.content || '',
        styleGuidelines: styleGuidelinesRes.data?.content || '',
        isLoadingPreferences: false,
      })
    } catch (error) {
      console.error('Failed to load preferences from backend:', error)
      set({ isLoadingPreferences: false })
    }
  },

  // Instructions & Style Actions
  updateInstructions: async (content) => {
    // Optimistically update UI
    set({ globalInstructions: content })

    try {
      await ApiService.updatePreference('instructions', content)
    } catch (error) {
      console.error('Failed to save instructions:', error)
      throw error
    }
  },

  updateStyleGuidelines: async (content) => {
    set({ styleGuidelines: content })

    try {
      // Save to backend
      await ApiService.updatePreference('style_guidelines', content)
    } catch (error) {
      console.error('Failed to save style guidelines:', error)
      throw error
    }
  },

  resetInstructionsToDefault: async () => {
    try {
      const response = await ApiService.resetPreferenceToDefault('instructions')
      set({ globalInstructions: response.data?.content || dummyInstructions })
    } catch (error) {
      console.error('Failed to reset instructions to default:', error)
      throw error
    }
  },

  resetStyleGuidelinesToDefault: async () => {
    try {
      const response = await ApiService.resetPreferenceToDefault('style_guidelines')
      set({ styleGuidelines: response.data?.content || dummyStyleGuidelines })
    } catch (error) {
      console.error('Failed to reset style guidelines to default:', error)
      throw error
    }
  },

  loadLearnings: async () => {
    set({ isLoadingLearnings: true })
    try {
      const response = await ApiService.getLearnings()
      set({ learnings: response.data || [], isLoadingLearnings: false })
    } catch (error) {
      console.error('Failed to load learnings:', error)
      set({ isLoadingLearnings: false })
    }
  },

  deleteLearning: async (id: string) => {
    const prev = get().learnings
    set({ learnings: prev.filter(l => l.id !== id) })
    try {
      await ApiService.deleteLearning(id)
    } catch (error) {
      set({ learnings: prev })
      console.error('Failed to delete learning:', error)
      throw error
    }
  },

  clearContext: () =>
    set(() => ({
      databaseContext: [],
      datasourceSchemas: {},
      datasourceAnnotations: {},
      globalInstructions: '',
      styleGuidelines: '',
      learnings: [],
      selectedDatasourceId: null,
      skills: [],
      customSkills: [],
    })),

  loadSkills: async () => {
    set({ isLoadingSkills: true })
    try {
      const response = await ApiService.getSkills()
      set({ skills: response.data || [], isLoadingSkills: false })
    } catch (error) {
      console.error('Failed to load skills:', error)
      set({ isLoadingSkills: false })
    }
  },

  saveSkillCredentials: async (skillName: string, credentials: Record<string, string>, scope: 'user' | 'org' = 'user') => {
    try {
      await ApiService.saveSkillCredentials(skillName, credentials, scope)
      await get().loadSkills()
    } catch (error) {
      console.error('Failed to save skill credentials:', error instanceof Error ? error.message : 'Unknown error')
      throw error
    }
  },

  deleteSkillCredentials: async (skillName: string, scope: 'user' | 'org' = 'user') => {
    try {
      await ApiService.deleteSkillCredentials(skillName, scope)
      await get().loadSkills()
    } catch (error) {
      console.error('Failed to delete skill credentials:', error instanceof Error ? error.message : 'Unknown error')
      throw error
    }
  },

  shareSkillWithTeam: async (skillName: string) => {
    try {
      await ApiService.shareSkillWithTeam(skillName)
      await get().loadSkills()
    } catch (error) {
      console.error('Failed to share skill with team:', error)
      throw error
    }
  },

  toggleSkillDomain: async (skillName: string, active: boolean, scope: SkillScope = 'user') => {
    const prev = get().skills
    set({
      skills: prev.map(s => s.skill_name === skillName ? {
        ...s,
        domain_active: active,
      } : s),
    })
    try {
      await ApiService.toggleSkillDomain(skillName, active, scope)
    } catch (error) {
      set({ skills: prev })
      console.error('Failed to toggle skill domain:', error)
      throw error
    }
  },

  loadCustomSkills: async () => {
    set({ isLoadingCustomSkills: true })
    try {
      const response = await ApiService.getCustomSkills()
      set({ customSkills: response.data || [], isLoadingCustomSkills: false })
    } catch (error) {
      console.error('Failed to load custom skills:', error)
      set({ isLoadingCustomSkills: false })
    }
  },

  createCustomSkill: async (data: CreateCustomSkillData) => {
    try {
      const response = await ApiService.createCustomSkill(data)
      await get().loadCustomSkills()
      return response.data
    } catch (error) {
      console.error('Failed to create custom skill:', error)
      throw error
    }
  },

  updateCustomSkill: async (id: string, data: Partial<CreateCustomSkillData>) => {
    try {
      const response = await ApiService.updateCustomSkill(id, data)
      await get().loadCustomSkills()
      return response.data
    } catch (error) {
      console.error('Failed to update custom skill:', error)
      throw error
    }
  },

  deleteCustomSkill: async (id: string) => {
    const prev = get().customSkills
    set({ customSkills: prev.filter(s => s.id !== id) })
    try {
      await ApiService.deleteCustomSkill(id)
    } catch (error) {
      set({ customSkills: prev })
      console.error('Failed to delete custom skill:', error)
      throw error
    }
  },

  shareCustomSkill: async (id: string) => {
    try {
      await ApiService.shareCustomSkill(id)
      await get().loadCustomSkills()
    } catch (error) {
      console.error('Failed to share custom skill:', error)
      throw error
    }
  },

  unshareCustomSkill: async (id: string) => {
    try {
      await ApiService.unshareCustomSkill(id)
      await get().loadCustomSkills()
    } catch (error) {
      console.error('Failed to unshare custom skill:', error)
      throw error
    }
  },

  toggleCustomSkillDomain: async (id: string, active: boolean) => {
    const prev = get().customSkills
    set({
      customSkills: prev.map(s => s.id === id ? { ...s, domain_active: active } : s),
    })
    try {
      await ApiService.toggleCustomSkillDomain(id, active)
    } catch (error) {
      set({ customSkills: prev })
      console.error('Failed to toggle custom skill domain:', error)
      throw error
    }
  },
});
