from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from server.repositories.connections import ConnectionRepository
from server.repositories.message_attachments import MessageAttachmentRepository
from server.repositories.messages import MessageRepository
from server.repositories.threads import ThreadRepository
from server.services.dataset import DatasetService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class MessageService:
    """Service for handling message and conversation operations."""

    @staticmethod
    async def get_database_connection(session: AsyncSession, notebook_id: str) -> tuple[dict[str, Any], str] | None:
        """
        Get database connection OR file dataset info for a notebook.

        Returns:
            For connection datasets: (connection_obj, connection_id)
            For file datasets: (connection_obj with files, dataset_id)
        """
        try:
            datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)

            if not datasets:
                logger.warning(f"No datasets found for notebook {notebook_id}")
                return None

            # Get the first dataset (connection or file)
            dataset = datasets[0]

            if dataset.type == "connection" and dataset.connection_id:
                # Connection-type dataset
                conn_repo = ConnectionRepository(session)
                connection = await conn_repo.get(dataset.connection_id)
                if connection:
                    connection_obj = await connection.get_decrypted_connection_obj(session)
                    logger.info(f"Successfully retrieved database connection for notebook {notebook_id}")
                    return connection_obj, connection.id

            elif dataset.type == "file":
                # File-type dataset
                dataset_with_files = await DatasetService.get_dataset(session, dataset.id)
                if dataset_with_files and dataset_with_files.files:
                    # Build connection_obj that contains file info
                    connection_obj = {
                        "dataset_id": dataset.id,
                        "dataset_type": "file",
                        "db_type": "duckdb",
                        "files": [
                            {"id": f.id, "name": f.name, "type": f.type, "size": f.size}
                            for f in dataset_with_files.files
                        ],
                    }
                    logger.info(
                        f"Successfully retrieved file dataset for notebook {notebook_id} with {len(dataset_with_files.files)} files"
                    )
                    return connection_obj, dataset.id
                else:
                    logger.warning(f"File dataset {dataset.id} has no files")
                    return None

            logger.warning(f"No valid datasets found for notebook {notebook_id}")
            return None

        except Exception as e:
            logger.error(
                f"Error getting database connection for notebook {notebook_id}: {str(e)}",
                posthog_context={"function": "MessageService.get_database_connection", "notebook_id": notebook_id},
            )
            return None

    @staticmethod
    async def get_notebook_conversation_history(
        session: AsyncSession, notebook_id: str, limit: int = 5
    ) -> list[dict[str, str]]:
        """
        Fetch last N messages for a notebook in chronological order.
        Only includes user/assistant roles.

        Args:
            session: Database session
            notebook_id: The notebook ID to fetch history for
            limit: Number of recent messages to fetch (default: 5)

        Returns:
            List of message dicts (role/content) in chronological order
        """
        message_repo = MessageRepository(session)

        try:
            messages = await message_repo.get_recent_messages(notebook_id, limit=limit)

            return [{"role": m.role, "content": m.content} for m in messages if m.role in ("user", "assistant")]

        except Exception as e:
            logger.error(
                f"Error fetching conversation history for notebook {notebook_id}: {e}",
                posthog_context={
                    "function": "MessageService.get_notebook_conversation_history",
                    "notebook_id": notebook_id,
                    "limit": limit,
                },
            )
            return []

    @staticmethod
    async def ensure_thread_for_notebook(session: AsyncSession, notebook_id: str) -> str:
        """Guarantee a thread exists for the notebook and return its id.

        Used by scheduled monitor runs that need somewhere for the assistant
        response to land, but shouldn't persist a user-message row (the system
        prompt isn't from a real user).
        """
        thread_repo = ThreadRepository(session)
        thread = await thread_repo.get(notebook_id)
        if thread:
            return str(notebook_id)
        existing = await thread_repo.list(filters={"notebook_id": notebook_id})
        if existing:
            return str(existing[0].id)
        await thread_repo.create(
            {
                "id": notebook_id,
                "notebook_id": notebook_id,
                "thread_title": None,
            }
        )
        return str(notebook_id)

    @staticmethod
    async def save_agent_user_message(
        session: AsyncSession,
        notebook_id: str,
        user_message: str,
        db_type: str = None,
        attachments: list[dict[str, str]] | None = None,
    ) -> str:
        """Save user message for agent workflow and return thread_id. Creates thread if needed."""
        message_repo = MessageRepository(session)
        attachment_repo = MessageAttachmentRepository(session)
        thread_repo = ThreadRepository(session)

        try:
            # First, check if thread with notebook_id exists
            thread = await thread_repo.get(notebook_id)
            thread_id = notebook_id

            if not thread:
                # If no thread with notebook_id, check if notebook has any threads
                existing_threads = await thread_repo.list(filters={"notebook_id": notebook_id})
                if existing_threads:
                    # Use the first existing thread
                    thread_id = existing_threads[0].id
                    logger.info(f"Using existing thread {thread_id} for notebook {notebook_id}")
                else:
                    # Create new thread with notebook_id
                    await thread_repo.create(
                        {
                            "id": notebook_id,
                            "notebook_id": notebook_id,
                            "thread_title": None,
                        }
                    )
                    logger.info(f"Created new thread {notebook_id} for notebook {notebook_id}")

            user_msg_data = {
                "role": "user",
                "content": user_message,
                "tool_call_id": None,
                "metadata_": {"source": "agent_stream", "type": "agent", "db_type": db_type},
                "thread_id": thread_id,
            }
            message = await message_repo.create(user_msg_data)

            if attachments:
                for attachment in attachments:
                    attachment_data = {
                        "message_id": message.id,
                        "file_name": attachment["file_name"],
                        "mime_type": attachment["mime_type"],
                        "file_data": attachment["file_data"],
                    }
                    await attachment_repo.create(attachment_data)
                logger.info(f"Saved {len(attachments)} attachments for message {message.id}")

            logger.info(f"Successfully saved user message for notebook {notebook_id} in thread {thread_id}")

            return thread_id

        except Exception as e:
            logger.error(
                f"Error saving user message: {str(e)}",
                posthog_context={
                    "function": "MessageService.save_agent_user_message",
                    "notebook_id": notebook_id,
                    "db_type": db_type,
                },
            )
            raise

    @staticmethod
    async def save_agent_assistant_message(
        session: AsyncSession,
        thread_id: str,
        assistant_message: str,
        db_type: str = None,
        metadata_extra: dict[str, Any] | None = None,
    ) -> None:
        """Save assistant message for agent workflow to existing thread."""
        message_repo = MessageRepository(session)

        try:
            metadata: dict[str, Any] = {
                "source": "agent_stream",
                "type": "agent",
                "db_type": db_type,
            }

            if metadata_extra:
                metadata.update(metadata_extra)

            assistant_msg_data = {
                "role": "assistant",
                "content": assistant_message,
                "tool_call_id": None,
                "metadata_": metadata,
                "thread_id": thread_id,
            }
            await message_repo.create(assistant_msg_data)
            logger.info(f"Successfully saved assistant message in thread {thread_id}")

        except Exception as e:
            logger.error(
                f"Error saving assistant message: {str(e)}",
                posthog_context={
                    "function": "MessageService.save_agent_assistant_message",
                    "thread_id": thread_id,
                    "db_type": db_type,
                },
            )
            raise
