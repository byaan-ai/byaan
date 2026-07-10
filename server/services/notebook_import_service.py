"""
Notebook Import Service

Handles importing notebooks from shared JSON exports via share IDs.
This service fetches notebook data, validates connections, and creates
new notebooks with mapped datasets.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.constants.models import MODELS_BY_PROVIDER
from server.models.connections import Connection
from server.models.datasets import Dataset
from server.models.notebooks import NotebookDataset
from server.repositories import NotebookRepository
from server.repositories.dashboard import DashboardRepository
from server.repositories.llm_connections import LLMConnectionRepository
from server.repositories.messages import MessageRepository
from server.repositories.queries import QueryRepository
from server.repositories.threads import ThreadRepository
from server.schemas.notebook_export import NotebookExport
from server.schemas.notebook_import import DatasetMapping, ImportedCounts, NotebookSummary
from server.services.crypto_service import CryptoService
from server.services.dataset import DatasetService
from server.services.file_operations import DataFrameFileService
from server.services.raw_query import AsyncRawQueryService
from server.services.settings import SettingsService
from server.utils.config_loader import get_waitlist_config
from server.utils.custom_logger import get_logger

# Settings keys for preferred model (same as in settings router)
PREFERRED_MODEL_PROVIDER_KEY = "preferred_model_provider"
PREFERRED_MODEL_KEY = "preferred_model"

logger = get_logger(__name__)


class NotebookImportService:
    """Service for importing notebooks from shared JSON exports."""

    @staticmethod
    async def _get_default_model(session: AsyncSession) -> tuple[str | None, str | None]:
        """
        Get the default model for the imported notebook.

        First checks for user's preferred model, then falls back to the first
        available model from LLM connections.

        Returns:
            Tuple of (provider, model) or (None, None) if no model available
        """
        try:
            # First, check for user's preferred model
            provider_setting = await SettingsService.get_setting_by_key(session, PREFERRED_MODEL_PROVIDER_KEY)
            model_setting = await SettingsService.get_setting_by_key(session, PREFERRED_MODEL_KEY)

            if provider_setting and model_setting:
                logger.info(f"Using preferred model: {provider_setting.setting_value}/{model_setting.setting_value}")
                return provider_setting.setting_value, model_setting.setting_value

            # No preferred model, get first available from LLM connections
            llm_repo = LLMConnectionRepository(session)
            connections = await llm_repo.list()

            if not connections:
                logger.info("No LLM connections available")
                return None, None

            # Get the first connection and its first model
            for connection in connections:
                provider = connection.type

                # For Azure/Bedrock, get models from config
                if provider in {"azure", "bedrock"} and connection.config:
                    try:
                        decrypted = await CryptoService.decrypt_config(connection.config, session)
                        models = decrypted.get("models", [])

                        if isinstance(models, str):
                            models = [m.strip() for m in models.split(",") if m.strip()]
                        elif not isinstance(models, list):
                            models = []

                        if models:
                            logger.info(f"Using first available model: {provider}/{models[0]}")
                            return provider, models[0]
                    except Exception as e:
                        logger.warning(f"Failed to get models from {provider}: {e}")
                        continue
                else:
                    # For other providers, use catalog models
                    catalog_models = MODELS_BY_PROVIDER.get(provider, [])
                    if catalog_models:
                        # Remove provider prefix if present
                        model = catalog_models[0]
                        if "/" in model:
                            model = model.split("/", 1)[1]
                        logger.info(f"Using first available model: {provider}/{model}")
                        return provider, model

            logger.info("No models available from LLM connections")
            return None, None

        except Exception as e:
            logger.error(f"Error getting default model: {e}")
            return None, None

    @staticmethod
    async def fetch_from_worker(share_id: str, password: str | None = None) -> tuple[NotebookExport, NotebookSummary]:
        """
        Fetch notebook JSON from worker using share ID.

        Args:
            share_id: UUID of the shared notebook
            password: Optional password for protected shares

        Returns:
            Tuple of (NotebookExport, NotebookSummary)

        Raises:
            ValueError: If share ID is invalid, share not found, or password incorrect
        """
        # Validate share ID format (UUID: 36 chars with 4 dashes)
        share_id = share_id.strip()
        if not (len(share_id) == 36 and share_id.count("-") == 4):
            raise ValueError("Invalid share ID format. Expected a UUID like xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")

        worker_base = get_waitlist_config().get("worker_url")
        if not worker_base:
            raise ValueError("Notebook import is not enabled in this deployment.")
        api_url = f"{worker_base}/api/notebook/{share_id}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # First, try to fetch without password
            response = await client.get(api_url)

            if response.status_code == 404:
                raise ValueError("Notebook share not found. Please check the share ID.")

            if response.status_code != 200:
                raise ValueError(f"Failed to fetch notebook: {response.text}")

            data = response.json()

            # Check if password protected
            if data.get("protected"):
                if not password:
                    raise ValueError("This notebook is password protected. Please provide the password.")

                # Verify password
                verify_url = f"{api_url}/verify"
                verify_response = await client.post(verify_url, json={"password": password})

                if verify_response.status_code == 401:
                    raise ValueError("Invalid password. Please try again.")

                if verify_response.status_code != 200:
                    raise ValueError(f"Failed to verify password: {verify_response.text}")

                data = verify_response.json()

            # Parse notebook JSON
            notebook_json = data.get("notebook_json")
            if not notebook_json:
                raise ValueError("Invalid notebook data: missing notebook_json")

            # Parse into NotebookExport model
            notebook_export = NotebookExport.model_validate(notebook_json)

            # Create summary
            total_queries = sum(len(ds.queries) for ds in notebook_export.datasets)
            summary = NotebookSummary(
                title=notebook_export.title,
                description=notebook_export.description,
                datasets_count=len(notebook_export.datasets),
                queries_count=total_queries,
                messages_count=len(notebook_export.chat_history),
                dashboards_count=len(notebook_export.dashboards),
            )

            logger.info(f"Fetched notebook '{notebook_export.title}' with {summary.datasets_count} datasets")
            return notebook_export, summary

    @staticmethod
    async def test_query_on_connection(
        session: AsyncSession,
        connection_id: str,
        query: str,
    ) -> tuple[bool, str | None]:
        """
        Run a test query on an existing connection.

        Args:
            session: Database session
            connection_id: ID of the connection to test
            query: SQL query to execute

        Returns:
            Tuple of (success, error_message)
        """
        # Fetch connection
        result = await session.execute(select(Connection).where(Connection.id == connection_id))
        connection = result.scalar_one_or_none()

        if not connection:
            return False, "Connection not found"

        # Get decrypted connection object
        connection_obj = await connection.get_decrypted_connection_obj(session)
        if not connection_obj:
            return False, "Could not decrypt connection credentials"

        # Execute test query
        try:
            query_result = await AsyncRawQueryService.execute_raw_query(
                query=query,
                db_type=connection.type,
                connection_id=connection_id,
                connection_obj=connection_obj,
                limit=1,
            )

            if query_result.get("error"):
                return False, query_result["error"]

            return True, None

        except Exception as e:
            logger.error(f"Test query failed: {e}")
            return False, str(e)

    @staticmethod
    async def test_query_on_dataset(
        session: AsyncSession,
        dataset_id: str,
        query: str,
    ) -> tuple[bool, str | None]:
        """
        Run a test query on a file-based dataset using DuckDB.

        Args:
            session: Database session
            dataset_id: ID of the dataset to test
            query: SQL query to execute

        Returns:
            Tuple of (success, error_message)
        """
        # Fetch dataset
        result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
        dataset = result.scalar_one_or_none()

        if not dataset:
            return False, "Dataset not found"

        if dataset.type != "file":
            return False, "Dataset is not a file-based dataset"

        # Execute test query using DuckDB
        try:
            query_result = await DataFrameFileService.execute_duckdb_query_on_dataset(
                session=session,
                dataset_id=dataset_id,
                query=query,
                limit=1,
            )

            if not query_result.get("success"):
                return False, query_result.get("error", "Query execution failed")

            return True, None

        except Exception as e:
            logger.error(f"Test query on dataset failed: {e}")
            return False, str(e)

    @staticmethod
    async def test_query(
        session: AsyncSession,
        connection_id: str | None,
        dataset_id: str | None,
        query: str,
    ) -> tuple[bool, str | None]:
        """
        Run a test query on either a connection or a dataset.

        This unified method determines the appropriate handler based on which ID is provided.
        If dataset_id is provided and points to a file-based dataset, uses DuckDB.
        If dataset_id points to a connection-based dataset, uses the connection.
        If connection_id is provided, uses the connection directly.

        Args:
            session: Database session
            connection_id: Optional ID of a connection to test
            dataset_id: Optional ID of a dataset to test
            query: SQL query to execute

        Returns:
            Tuple of (success, error_message)
        """
        # If dataset_id is provided, check what type of dataset it is
        if dataset_id:
            result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
            dataset = result.scalar_one_or_none()

            if dataset:
                if dataset.type == "file":
                    # File-based dataset - use DuckDB
                    return await NotebookImportService.test_query_on_dataset(session, dataset_id, query)
                elif dataset.type == "connection" and dataset.connection_id:
                    # Connection-based dataset - use the underlying connection
                    return await NotebookImportService.test_query_on_connection(session, dataset.connection_id, query)
                else:
                    return False, "Invalid dataset type or missing connection"
            else:
                return False, "Dataset not found"

        # If connection_id is provided, test on the connection
        if connection_id:
            return await NotebookImportService.test_query_on_connection(session, connection_id, query)

        return False, "Either connection_id or dataset_id must be provided"

    @staticmethod
    async def import_notebook(
        session: AsyncSession,
        notebook_export: NotebookExport,
        dataset_mappings: list[DatasetMapping],
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> tuple[str, ImportedCounts]:
        """
        Import a notebook from an export with mapped connections.

        Args:
            session: Database session
            notebook_export: The exported notebook data
            dataset_mappings: Mappings of export dataset indices to local connection IDs

        Returns:
            Tuple of (new_notebook_id, imported_counts)
        """
        logger.info(f"Importing notebook '{notebook_export.title}'")

        # Create mapping dict for quick lookup
        mapping_dict: dict[int, DatasetMapping] = {m.dataset_index: m for m in dataset_mappings}

        # Get the default model (preferred or first available)
        default_provider, default_model = await NotebookImportService._get_default_model(session)

        # 1. Create the new notebook with default model
        notebook_repo = NotebookRepository(session)
        notebook_data = {
            "notebook_name": f"{notebook_export.title} (Imported)",
            "description": notebook_export.description,
        }
        if default_provider and default_model:
            notebook_data["last_used_provider"] = default_provider
            notebook_data["last_used_model"] = default_model
        if tenant_id:
            notebook_data["tenant_id"] = tenant_id
        if user_id:
            notebook_data["created_by"] = user_id

        notebook = await notebook_repo.create(notebook_data)
        notebook_id = notebook.id

        # 2. Create thread for the notebook
        thread_repo = ThreadRepository(session)
        thread = await thread_repo.create(
            {
                "id": notebook_id,
                "notebook_id": notebook_id,
                "thread_title": None,
            }
        )

        # 3. Create datasets and map to connections (or attach existing datasets)
        query_repo = QueryRepository(session)
        old_to_new_dataset: dict[int, str] = {}  # Map export index to dataset ID
        old_to_new_query_id: dict[str, str] = {}  # Map old query ID to new query ID
        datasets_imported = 0
        queries_imported = 0

        for idx, exported_dataset in enumerate(notebook_export.datasets):
            mapping = mapping_dict.get(idx)

            # Skip if not mapped or explicitly skipped
            if not mapping or mapping.skipped:
                logger.info(f"Skipping dataset {idx}: {exported_dataset.original_name}")
                continue

            # Check if neither dataset_id nor connection_id is provided
            if not mapping.dataset_id and not mapping.connection_id:
                logger.info(f"Skipping dataset {idx}: {exported_dataset.original_name} (no mapping)")
                continue

            dataset_id_to_use: str | None = None

            if mapping.dataset_id:
                # EXISTING DATASET - just attach it to the notebook (no creation)
                existing_result = await session.execute(select(Dataset).where(Dataset.id == mapping.dataset_id))
                existing_dataset = existing_result.scalar_one_or_none()

                if not existing_dataset:
                    logger.warning(f"Dataset {mapping.dataset_id} not found, skipping dataset {idx}")
                    continue

                # Check if association already exists before creating
                existing_assoc = await session.execute(
                    select(NotebookDataset).where(
                        NotebookDataset.notebook_id == notebook_id, NotebookDataset.dataset_id == mapping.dataset_id
                    )
                )
                if not existing_assoc.scalar_one_or_none():
                    # Create NotebookDataset association to attach existing dataset
                    nb_dataset = NotebookDataset(
                        notebook_id=notebook_id,
                        dataset_id=mapping.dataset_id,
                    )
                    session.add(nb_dataset)

                dataset_id_to_use = mapping.dataset_id
                datasets_imported += 1
                logger.info(f"Attached existing dataset {mapping.dataset_id} to notebook {notebook_id}")

            elif mapping.connection_id:
                # DATABASE CONNECTION - use DatasetService which handles deduplication
                # It will reuse existing dataset for the same connection_id if one exists
                try:
                    dataset = await DatasetService.create_dataset(
                        session=session,
                        type="connection",
                        connection_id=mapping.connection_id,
                        notebook_id=notebook_id,  # This creates the NotebookDataset association
                        name=exported_dataset.original_name,
                    )
                    dataset_id_to_use = dataset.id
                    datasets_imported += 1
                    logger.info(f"Using dataset {dataset.id} for connection {mapping.connection_id}")
                except ValueError as e:
                    logger.warning(f"Connection {mapping.connection_id} not found, skipping dataset {idx}: {e}")
                    continue

            # 4. Create queries for this dataset
            if dataset_id_to_use:
                old_to_new_dataset[idx] = dataset_id_to_use

                for exported_query in exported_dataset.queries:
                    new_query = await query_repo.create(
                        {
                            "name": exported_query.name,
                            "query": exported_query.query,
                            "output_schema": exported_query.output_schema or "{}",
                            "dataset_id": dataset_id_to_use,
                            "notebook_id": notebook_id,
                        }
                    )
                    # Map old query ID to new query ID for dashboard HTML replacement
                    old_to_new_query_id[exported_query.id] = new_query.id
                    queries_imported += 1

        await session.commit()

        # 5. Restore chat history
        message_repo = MessageRepository(session)
        messages_imported = 0

        for exported_msg in notebook_export.chat_history:
            message_data = {
                "thread_id": thread.id,
                "role": exported_msg.role,
                "content": exported_msg.content,
            }

            if exported_msg.created_at:
                message_data["created_at"] = datetime.fromisoformat(exported_msg.created_at)

            await message_repo.create(message_data)
            messages_imported += 1

        # 6. Restore dashboards (with query ID replacement)
        dashboard_repo = DashboardRepository(session)
        dashboards_imported = 0

        for exported_dashboard in notebook_export.dashboards:
            # Replace old query IDs with new query IDs in dashboard HTML
            html_content = exported_dashboard.html_content
            for old_query_id, new_query_id in old_to_new_query_id.items():
                html_content = html_content.replace(str(old_query_id), str(new_query_id))

            await dashboard_repo.create_with_version(
                notebook_id,
                html_content,
                tenant_id,
            )
            dashboards_imported += 1

        logger.info(f"Replaced {len(old_to_new_query_id)} query IDs in dashboard HTML")

        await session.commit()

        skipped_count = len(notebook_export.datasets) - datasets_imported

        logger.info(
            f"Imported notebook {notebook_id}: "
            f"{datasets_imported} datasets, {queries_imported} queries, "
            f"{messages_imported} messages, {dashboards_imported} dashboards, "
            f"{skipped_count} skipped"
        )

        return str(notebook_id), ImportedCounts(
            datasets=datasets_imported,
            queries=queries_imported,
            messages=messages_imported,
            dashboards=dashboards_imported,
        )
