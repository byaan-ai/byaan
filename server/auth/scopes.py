"""
Role-based permission scopes for the Byaan application.

This module defines all available scopes and maps them to user roles.
Scopes follow the pattern: {resource}.{action}
"""

from enum import Enum

from server.models.tenant_member import TenantRole


class Scope(str, Enum):
    """All available permission scopes in the application."""

    # Notebook scopes
    NOTEBOOK_CREATE = "notebook.create"
    NOTEBOOK_READ = "notebook.read"
    NOTEBOOK_READ_OWN = "notebook.read_own"
    NOTEBOOK_UPDATE = "notebook.update"
    NOTEBOOK_UPDATE_OWN = "notebook.update_own"
    NOTEBOOK_DELETE = "notebook.delete"
    NOTEBOOK_DELETE_OWN = "notebook.delete_own"

    # Connection scopes (database connections)
    CONNECTION_CREATE = "connection.create"
    CONNECTION_READ = "connection.read"
    CONNECTION_UPDATE = "connection.update"
    CONNECTION_UPDATE_OWN = "connection.update_own"
    CONNECTION_DELETE = "connection.delete"
    CONNECTION_DELETE_OWN = "connection.delete_own"

    # Dataset scopes
    DATASET_CREATE = "dataset.create"
    DATASET_READ = "dataset.read"
    DATASET_UPDATE = "dataset.update"
    DATASET_UPDATE_OWN = "dataset.update_own"
    DATASET_DELETE = "dataset.delete"
    DATASET_DELETE_OWN = "dataset.delete_own"

    # Query scopes
    QUERY_CREATE = "query.create"
    QUERY_READ = "query.read"
    QUERY_READ_OWN = "query.read_own"
    QUERY_UPDATE = "query.update"
    QUERY_UPDATE_OWN = "query.update_own"
    QUERY_DELETE = "query.delete"
    QUERY_DELETE_OWN = "query.delete_own"
    QUERY_EXECUTE = "query.execute"

    # LLM Connection scopes
    LLM_CONNECTION_CREATE = "llm_connection.create"
    LLM_CONNECTION_READ = "llm_connection.read"
    LLM_CONNECTION_UPDATE = "llm_connection.update"
    LLM_CONNECTION_UPDATE_OWN = "llm_connection.update_own"
    LLM_CONNECTION_DELETE = "llm_connection.delete"
    LLM_CONNECTION_DELETE_OWN = "llm_connection.delete_own"

    # Dashboard scopes
    DASHBOARD_READ = "dashboard.read"
    DASHBOARD_EXPORT = "dashboard.export"
    DASHBOARD_SHARE = "dashboard.share"

    # Sharing scopes
    SHARING_READ = "sharing.read"

    # Annotation scopes
    ANNOTATION_CREATE = "annotation.create"
    ANNOTATION_READ = "annotation.read"
    ANNOTATION_UPDATE = "annotation.update"
    ANNOTATION_DELETE = "annotation.delete"

    # Tenant/Team management scopes
    TENANT_READ = "tenant.read"
    TENANT_UPDATE = "tenant.update"
    TENANT_INVITE = "tenant.invite"
    TENANT_REMOVE_MEMBER = "tenant.remove_member"
    TENANT_MANAGE_ROLES = "tenant.manage_roles"

    # User scopes
    USER_READ = "user.read"
    USER_UPDATE = "user.update"

    # Settings scopes
    SETTINGS_READ = "settings.read"
    SETTINGS_UPDATE = "settings.update"

    # Folder scopes
    FOLDER_CREATE = "folder.create"
    FOLDER_READ = "folder.read"
    FOLDER_UPDATE = "folder.update"
    FOLDER_DELETE = "folder.delete"
    FOLDER_MANAGE_MEMBERS = "folder.manage_members"
    FOLDER_SHARE_NOTEBOOK = "folder.share_notebook"

    # Viewer-specific scopes
    VIEWER_DASHBOARD_READ = "viewer.dashboard_read"

    # Schedule scopes
    SCHEDULE_CREATE = "schedule.create"
    SCHEDULE_READ = "schedule.read"
    SCHEDULE_READ_OWN = "schedule.read_own"
    SCHEDULE_UPDATE = "schedule.update"
    SCHEDULE_UPDATE_OWN = "schedule.update_own"
    SCHEDULE_DELETE = "schedule.delete"
    SCHEDULE_DELETE_OWN = "schedule.delete_own"


# Owner has full access to everything
OWNER_SCOPES: list[str] = [
    # Notebook (private - own only)
    Scope.NOTEBOOK_CREATE.value,
    Scope.NOTEBOOK_READ_OWN.value,
    Scope.NOTEBOOK_UPDATE_OWN.value,
    Scope.NOTEBOOK_DELETE_OWN.value,
    # Connection
    Scope.CONNECTION_CREATE.value,
    Scope.CONNECTION_READ.value,
    Scope.CONNECTION_UPDATE.value,
    Scope.CONNECTION_DELETE.value,
    # Dataset
    Scope.DATASET_CREATE.value,
    Scope.DATASET_READ.value,
    Scope.DATASET_UPDATE.value,
    Scope.DATASET_DELETE.value,
    # Query
    Scope.QUERY_CREATE.value,
    Scope.QUERY_READ.value,
    Scope.QUERY_UPDATE.value,
    Scope.QUERY_DELETE.value,
    Scope.QUERY_EXECUTE.value,
    # LLM Connection
    Scope.LLM_CONNECTION_CREATE.value,
    Scope.LLM_CONNECTION_READ.value,
    Scope.LLM_CONNECTION_UPDATE.value,
    Scope.LLM_CONNECTION_DELETE.value,
    # Dashboard
    Scope.DASHBOARD_READ.value,
    Scope.DASHBOARD_EXPORT.value,
    Scope.DASHBOARD_SHARE.value,
    # Sharing
    Scope.SHARING_READ.value,
    # Annotation
    Scope.ANNOTATION_CREATE.value,
    Scope.ANNOTATION_READ.value,
    Scope.ANNOTATION_UPDATE.value,
    Scope.ANNOTATION_DELETE.value,
    # Tenant Management
    Scope.TENANT_READ.value,
    Scope.TENANT_UPDATE.value,
    Scope.TENANT_INVITE.value,
    Scope.TENANT_REMOVE_MEMBER.value,
    Scope.TENANT_MANAGE_ROLES.value,
    # User
    Scope.USER_READ.value,
    Scope.USER_UPDATE.value,
    # Settings
    Scope.SETTINGS_READ.value,
    Scope.SETTINGS_UPDATE.value,
    # Folder (full access)
    Scope.FOLDER_CREATE.value,
    Scope.FOLDER_READ.value,
    Scope.FOLDER_UPDATE.value,
    Scope.FOLDER_DELETE.value,
    Scope.FOLDER_MANAGE_MEMBERS.value,
    Scope.FOLDER_SHARE_NOTEBOOK.value,
    # Schedule (private - own only)
    Scope.SCHEDULE_CREATE.value,
    Scope.SCHEDULE_READ_OWN.value,
    Scope.SCHEDULE_UPDATE_OWN.value,
    Scope.SCHEDULE_DELETE_OWN.value,
]

# Admin has management access but cannot transfer ownership or promote to owner
ADMIN_SCOPES: list[str] = [
    # Notebook (private - own only)
    Scope.NOTEBOOK_CREATE.value,
    Scope.NOTEBOOK_READ_OWN.value,
    Scope.NOTEBOOK_UPDATE_OWN.value,
    Scope.NOTEBOOK_DELETE_OWN.value,
    # Connection
    Scope.CONNECTION_CREATE.value,
    Scope.CONNECTION_READ.value,
    Scope.CONNECTION_UPDATE.value,
    Scope.CONNECTION_DELETE.value,
    # Dataset
    Scope.DATASET_CREATE.value,
    Scope.DATASET_READ.value,
    Scope.DATASET_UPDATE.value,
    Scope.DATASET_DELETE.value,
    # Query
    Scope.QUERY_CREATE.value,
    Scope.QUERY_READ.value,
    Scope.QUERY_UPDATE.value,
    Scope.QUERY_DELETE.value,
    Scope.QUERY_EXECUTE.value,
    # LLM Connection
    Scope.LLM_CONNECTION_CREATE.value,
    Scope.LLM_CONNECTION_READ.value,
    Scope.LLM_CONNECTION_UPDATE.value,
    Scope.LLM_CONNECTION_DELETE.value,
    # Dashboard
    Scope.DASHBOARD_READ.value,
    Scope.DASHBOARD_EXPORT.value,
    Scope.DASHBOARD_SHARE.value,
    # Sharing
    Scope.SHARING_READ.value,
    # Annotation
    Scope.ANNOTATION_CREATE.value,
    Scope.ANNOTATION_READ.value,
    Scope.ANNOTATION_UPDATE.value,
    Scope.ANNOTATION_DELETE.value,
    # Tenant Management (can manage roles but with restrictions)
    Scope.TENANT_READ.value,
    Scope.TENANT_INVITE.value,
    Scope.TENANT_REMOVE_MEMBER.value,
    Scope.TENANT_MANAGE_ROLES.value,
    # User
    Scope.USER_READ.value,
    Scope.USER_UPDATE.value,
    # Settings
    Scope.SETTINGS_READ.value,
    Scope.SETTINGS_UPDATE.value,
    # Folder (full access)
    Scope.FOLDER_CREATE.value,
    Scope.FOLDER_READ.value,
    Scope.FOLDER_UPDATE.value,
    Scope.FOLDER_DELETE.value,
    Scope.FOLDER_MANAGE_MEMBERS.value,
    Scope.FOLDER_SHARE_NOTEBOOK.value,
    # Schedule (private - own only)
    Scope.SCHEDULE_CREATE.value,
    Scope.SCHEDULE_READ_OWN.value,
    Scope.SCHEDULE_UPDATE_OWN.value,
    Scope.SCHEDULE_DELETE_OWN.value,
]

# Member has basic usage access
# - Notebooks, Queries, Connections, Datasets: Private (CRUD own only)
# - LLM Connections: Read-only (cannot create, only admins/owners can create)
MEMBER_SCOPES: list[str] = [
    # Notebook (private - CRUD own only)
    Scope.NOTEBOOK_CREATE.value,
    Scope.NOTEBOOK_READ_OWN.value,
    Scope.NOTEBOOK_UPDATE_OWN.value,
    Scope.NOTEBOOK_DELETE_OWN.value,
    # Connection (private - CRUD own only)
    Scope.CONNECTION_CREATE.value,
    Scope.CONNECTION_READ.value,
    Scope.CONNECTION_UPDATE_OWN.value,
    Scope.CONNECTION_DELETE_OWN.value,
    # Dataset (private - CRUD own only)
    Scope.DATASET_CREATE.value,
    Scope.DATASET_READ.value,
    Scope.DATASET_UPDATE_OWN.value,
    Scope.DATASET_DELETE_OWN.value,
    # Query (private - CRUD own only)
    Scope.QUERY_CREATE.value,
    Scope.QUERY_READ_OWN.value,
    Scope.QUERY_UPDATE_OWN.value,
    Scope.QUERY_DELETE_OWN.value,
    Scope.QUERY_EXECUTE.value,
    # LLM Connection (read-only - only owner/admin can create)
    Scope.LLM_CONNECTION_READ.value,
    # Dashboard
    Scope.DASHBOARD_READ.value,
    Scope.DASHBOARD_EXPORT.value,
    # Sharing
    Scope.SHARING_READ.value,
    # Annotation (read-only)
    Scope.ANNOTATION_READ.value,
    # Tenant (read-only)
    Scope.TENANT_READ.value,
    # User
    Scope.USER_READ.value,
    Scope.USER_UPDATE.value,
    # Settings (user can update own settings like preferred model)
    Scope.SETTINGS_READ.value,
    Scope.SETTINGS_UPDATE.value,
    # Folder (can create, read, and share notebooks to folders)
    Scope.FOLDER_CREATE.value,
    Scope.FOLDER_READ.value,
    Scope.FOLDER_SHARE_NOTEBOOK.value,
    # Schedule (private - CRUD own only)
    Scope.SCHEDULE_CREATE.value,
    Scope.SCHEDULE_READ_OWN.value,
    Scope.SCHEDULE_UPDATE_OWN.value,
    Scope.SCHEDULE_DELETE_OWN.value,
]

# Viewer has minimal access - only dashboards via folder membership
VIEWER_SCOPES: list[str] = [
    # Viewer-specific dashboard access
    Scope.VIEWER_DASHBOARD_READ.value,
    # Dashboard read for the unified dashboards page
    Scope.DASHBOARD_READ.value,
    # Query execution for dashboard data
    Scope.QUERY_EXECUTE.value,
    # Basic tenant/user read for UI context
    Scope.TENANT_READ.value,
    Scope.USER_READ.value,
    # Folder read access to see folders they're members of
    Scope.FOLDER_READ.value,
]

# Map roles to their scopes
ROLE_SCOPES: dict[TenantRole, list[str]] = {
    TenantRole.OWNER: OWNER_SCOPES,
    TenantRole.ADMIN: ADMIN_SCOPES,
    TenantRole.MEMBER: MEMBER_SCOPES,
    TenantRole.VIEWER: VIEWER_SCOPES,
}


def get_scopes_for_role(role: TenantRole | str) -> list[str]:
    """
    Get the list of scopes for a given role.

    Args:
        role: The TenantRole enum or string value

    Returns:
        List of scope strings for that role
    """
    if isinstance(role, str):
        try:
            role = TenantRole(role)
        except ValueError:
            return []

    return ROLE_SCOPES.get(role, [])


def has_scope(user_scopes: list[str], required_scope: str | Scope) -> bool:
    """
    Check if a user has a specific scope.

    Args:
        user_scopes: List of scopes the user has
        required_scope: The scope to check for

    Returns:
        True if the user has the scope, False otherwise
    """
    if isinstance(required_scope, Scope):
        required_scope = required_scope.value

    return required_scope in user_scopes
