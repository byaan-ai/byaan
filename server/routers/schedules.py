from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_any_scope, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.repositories.schedules import ScheduleRepository, ScheduleRunRepository
from server.schemas.schedules import (
    ScheduleCreate,
    ScheduleRead,
    ScheduleRunRead,
    ScheduleTestResult,
    ScheduleUpdate,
)
from server.schemas.standard_response import success_response
from server.services.notebook import NotebookService
from server.services.schedule_service import ScheduleService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/schedules")
async def list_all_schedules(
    status_filter: str | None = Query(None, alias="status"),
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_READ, Scope.SCHEDULE_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ScheduleRepository(session)
        schedules = await repo.get_all_for_tenant(auth.tenant_id)

        if not auth.has_scope(Scope.SCHEDULE_READ):
            schedules = [s for s in schedules if s.created_by is not None and str(s.created_by) == str(auth.user_id)]

        if status_filter:
            from server.models.schedules import MONITOR_STATUSES

            if status_filter not in MONITOR_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status; must be one of {sorted(MONITOR_STATUSES)}",
                )
            schedules = [s for s in schedules if s.status == status_filter]

        data = []
        for s in schedules:
            schedule_data = ScheduleRead.model_validate(s).model_dump()
            schedule_data["notebook_name"] = s.notebook.notebook_name if s.notebook else None
            data.append(schedule_data)

        return success_response(data=data, message=f"Retrieved {len(data)} schedule(s)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in list_all_schedules: {str(e)}",
            posthog_context={"function": "list_all_schedules"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing schedules",
        )


@router.get("/notebooks/{notebook_id}/schedules")
async def list_notebook_schedules(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_READ, Scope.SCHEDULE_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        repo = ScheduleRepository(session)
        schedules = await repo.get_for_notebook(notebook_id)

        if not auth.has_scope(Scope.SCHEDULE_READ):
            schedules = [s for s in schedules if s.created_by is not None and str(s.created_by) == str(auth.user_id)]

        data = [ScheduleRead.model_validate(s).model_dump() for s in schedules]
        return success_response(data=data, message=f"Retrieved {len(data)} schedule(s) for notebook")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in list_notebook_schedules: {str(e)}",
            posthog_context={"function": "list_notebook_schedules", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing schedules",
        )


@router.post("/notebooks/{notebook_id}/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    notebook_id: str,
    payload: ScheduleCreate,
    auth: AuthContext = Depends(require_scope(Scope.SCHEDULE_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        if not ScheduleService.validate_cron_expression(payload.cron_expression):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cron expression",
            )

        next_run = ScheduleService.calculate_next_run(payload.cron_expression, payload.timezone)

        repo = ScheduleRepository(session)
        schedule = await repo.create(
            {
                "notebook_id": notebook_id,
                "created_by": auth.user_id,
                "name": payload.name,
                "cron_expression": payload.cron_expression,
                "timezone": payload.timezone,
                "is_enabled": payload.is_enabled,
                "webhook_url": payload.webhook_url,
                "slack_channel_id": payload.slack_channel_id,
                "instruction": payload.instruction,
                "next_run_at": next_run,
            }
        )

        data = ScheduleRead.model_validate(schedule).model_dump()
        data["notebook_name"] = notebook.notebook_name
        return success_response(data=data, message=f"Schedule '{payload.name}' created successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_schedule: {str(e)}",
            posthog_context={"function": "create_schedule", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating schedule",
        )


@router.get("/schedules/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_READ, Scope.SCHEDULE_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(schedule_id)

        if schedule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

        if not auth.has_scope(Scope.SCHEDULE_READ):
            if schedule.created_by is None or str(schedule.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access schedules you created",
                )

        data = ScheduleRead.model_validate(schedule).model_dump()
        data["notebook_name"] = schedule.notebook.notebook_name if schedule.notebook else None
        return success_response(data=data, message="Schedule retrieved successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_schedule: {str(e)}",
            posthog_context={"function": "get_schedule", "schedule_id": schedule_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving schedule",
        )


@router.patch("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_UPDATE, Scope.SCHEDULE_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(schedule_id)

        if schedule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

        if not auth.has_scope(Scope.SCHEDULE_UPDATE):
            if schedule.created_by is None or str(schedule.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only update schedules you created",
                )

        from server.models.schedules import MONITOR_STATUSES, NOTIFY_POLICIES

        update_data = {}
        if payload.name is not None:
            update_data["name"] = payload.name
        if payload.cron_expression is not None:
            if not ScheduleService.validate_cron_expression(payload.cron_expression):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cron expression",
                )
            update_data["cron_expression"] = payload.cron_expression
        if payload.timezone is not None:
            update_data["timezone"] = payload.timezone
        if payload.is_enabled is not None:
            update_data["is_enabled"] = payload.is_enabled
        if payload.webhook_url is not None:
            update_data["webhook_url"] = payload.webhook_url
        if payload.slack_channel_id is not None:
            update_data["slack_channel_id"] = payload.slack_channel_id
        if payload.slack_thread_ts is not None:
            update_data["slack_thread_ts"] = payload.slack_thread_ts or None
        if payload.broadcast_to_channel is not None:
            update_data["broadcast_to_channel"] = payload.broadcast_to_channel
        if payload.status is not None:
            if payload.status not in MONITOR_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"status must be one of {sorted(MONITOR_STATUSES)}",
                )
            update_data["status"] = payload.status
            if payload.status == "active":
                update_data["is_enabled"] = True
                update_data["paused_until"] = None
            elif payload.status in {"paused", "auto_paused"}:
                update_data["is_enabled"] = False
            elif payload.status == "draft":
                update_data["is_enabled"] = False
        if payload.notify_policy is not None:
            if payload.notify_policy not in NOTIFY_POLICIES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"notify_policy must be one of {sorted(NOTIFY_POLICIES)}",
                )
            update_data["notify_policy"] = payload.notify_policy
        if payload.paused_until is not None:
            update_data["paused_until"] = payload.paused_until
        if payload.llm_connection_id is not None:
            update_data["llm_connection_id"] = payload.llm_connection_id
        if payload.model is not None:
            update_data["model"] = payload.model
        if payload.instruction is not None:
            update_data["instruction"] = payload.instruction

        cron = payload.cron_expression or schedule.cron_expression
        tz = payload.timezone or schedule.timezone
        update_data["next_run_at"] = ScheduleService.calculate_next_run(cron, tz)

        updated_schedule = await repo.update(schedule_id, update_data)
        if updated_schedule is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update schedule",
            )

        updated_schedule = await repo.get_with_notebook(schedule_id)
        data = ScheduleRead.model_validate(updated_schedule).model_dump()
        data["notebook_name"] = updated_schedule.notebook.notebook_name if updated_schedule.notebook else None
        return success_response(data=data, message="Schedule updated successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in update_schedule: {str(e)}",
            posthog_context={"function": "update_schedule", "schedule_id": schedule_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating schedule",
        )


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_DELETE, Scope.SCHEDULE_DELETE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ScheduleRepository(session)
        schedule = await repo.get(schedule_id)

        if schedule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

        if not auth.has_scope(Scope.SCHEDULE_DELETE):
            if schedule.created_by is None or str(schedule.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only delete schedules you created",
                )

        deleted = await repo.delete(schedule_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete schedule",
            )

        return success_response(data=None, message="Schedule deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_schedule: {str(e)}",
            posthog_context={"function": "delete_schedule", "schedule_id": schedule_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting schedule",
        )


@router.post("/schedules/{schedule_id}/test")
async def test_run_schedule(
    schedule_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_UPDATE, Scope.SCHEDULE_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(schedule_id)

        if schedule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

        if not auth.has_scope(Scope.SCHEDULE_UPDATE):
            if schedule.created_by is None or str(schedule.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only test schedules you created",
                )

        result = await ScheduleService.test_schedule(session, schedule)
        return success_response(
            data=ScheduleTestResult(**result).model_dump(),
            message="Test run completed",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in test_run_schedule: {str(e)}",
            posthog_context={"function": "test_run_schedule", "schedule_id": schedule_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while testing schedule",
        )


@router.post("/notebooks/{notebook_id}/schedules/preview")
async def preview_schedule_run(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_CREATE, Scope.SCHEDULE_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        result = await ScheduleService.preview_notebook_schedule(session, notebook_id, notebook.notebook_name)
        return success_response(
            data=ScheduleTestResult(**result).model_dump(),
            message="Preview run completed",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in preview_schedule_run: {str(e)}",
            posthog_context={"function": "preview_schedule_run", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while previewing schedule",
        )


@router.get("/schedules/{schedule_id}/runs")
async def get_schedule_runs(
    schedule_id: str,
    limit: int = 20,
    auth: AuthContext = Depends(require_any_scope(Scope.SCHEDULE_READ, Scope.SCHEDULE_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        schedule_repo = ScheduleRepository(session)
        schedule = await schedule_repo.get(schedule_id)

        if schedule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

        if not auth.has_scope(Scope.SCHEDULE_READ):
            if schedule.created_by is None or str(schedule.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access schedules you created",
                )

        run_repo = ScheduleRunRepository(session)
        runs = await run_repo.get_recent_runs(schedule_id, limit=min(limit, 100))

        data = [ScheduleRunRead.model_validate(r).model_dump() for r in runs]
        return success_response(data=data, message=f"Retrieved {len(data)} run(s)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_schedule_runs: {str(e)}",
            posthog_context={"function": "get_schedule_runs", "schedule_id": schedule_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving schedule runs",
        )
