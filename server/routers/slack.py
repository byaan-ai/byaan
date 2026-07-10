"""Slack integration router (enterprise feature)."""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import AsyncSessionFactory, get_async_session
from server.repositories.slack_workspace import SlackWorkspaceRepository
from server.schemas.slack import SlackConfigCreate, SlackConfigResponse, SlackConfigUpdate
from server.schemas.standard_response import success_response
from server.services.crypto_service import CryptoService
from server.services.slack_agent_service import SlackAgentService
from server.services.slack_service import SlackService
from server.services.slack_signature_service import SlackSignatureError, SlackSignatureService
from server.services.slack_suggestion_service import handle_suggestion_action
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled
from server.utils.slack_chart_detector import SlackChartDetector

logger = get_logger(__name__)


def _require_slack_enabled():
    if not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Slack integration requires enterprise version"
        )


router = APIRouter(prefix="/slack", tags=["slack"], dependencies=[Depends(_require_slack_enabled)])


@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Handle Slack events webhook.

    - Verifies Slack signature (HMAC-SHA256)
    - Handles url_verification challenge
    - Processes app_mention events in background
    - Returns 200 quickly to prevent Slack retries
    """
    try:
        body = await request.body()
    except ClientDisconnect:
        logger.info("Slack client disconnected before body read; will be retried by Slack")
        return JSONResponse(status_code=200, content={"ok": True})

    try:
        payload = json.loads(body) if body else {}
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    retry_num = request.headers.get("X-Slack-Retry-Num")
    if retry_num:
        logger.info(
            f"Slack retry received: num={retry_num} reason={request.headers.get('X-Slack-Retry-Reason')} "
            f"event_id={payload.get('event_id')} — allowing through for dedup-based processing"
        )

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    team_id = payload.get("team_id")
    if not team_id:
        logger.warning("Slack event missing team_id")
        return JSONResponse(status_code=200, content={"ok": True})

    async with AsyncSessionFactory() as session:
        repo = SlackWorkspaceRepository(session)
        workspace = await repo.get_by_team_id(team_id)

        if not workspace:
            logger.warning(f"No workspace found for Slack team: {team_id}")
            return JSONResponse(status_code=200, content={"ok": True})

        if not workspace.is_active:
            logger.info(f"Slack workspace {team_id} is inactive")
            return JSONResponse(status_code=200, content={"ok": True})

        try:
            signing_secret = await SlackAgentService._get_signing_secret(workspace, session)
            SlackSignatureService.verify_signature(
                signing_secret=signing_secret,
                timestamp=timestamp,
                body=body,
                signature=signature,
            )
        except SlackSignatureError as e:
            logger.warning(f"Slack signature verification failed: {e}")
            return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    event = payload.get("event", {})
    event_type = event.get("type")
    event_id = payload.get("event_id", "")

    if event_type == "app_mention":
        background_tasks.add_task(
            _process_app_mention,
            team_id=team_id,
            channel_id=event.get("channel"),
            thread_ts=event.get("thread_ts"),
            user_id=event.get("user"),
            text=event.get("text", ""),
            event_ts=event.get("ts"),
            event_id=event_id,
        )
    elif event_type == "message":
        if _should_route_thread_followup(event, workspace.bot_user_id):
            background_tasks.add_task(
                _process_thread_followup,
                team_id=team_id,
                channel_id=event.get("channel"),
                thread_ts=event.get("thread_ts"),
                user_id=event.get("user"),
                text=event.get("text", ""),
                event_ts=event.get("ts"),
                event_id=event_id,
            )
    return JSONResponse(status_code=200, content={"ok": True})


def _should_route_thread_followup(event: dict, bot_user_id: str | None) -> bool:
    """Cheap synchronous gate at the router edge.

    Rejects channel-level chatter, bot echoes, edits, and messages authored by
    Byaan itself before we spend any work on background processing.
    """
    if event.get("subtype") in {
        "bot_message",
        "message_changed",
        "message_deleted",
        "channel_join",
        "channel_leave",
        "thread_broadcast",
    }:
        return False
    if event.get("bot_id"):
        return False
    if not event.get("thread_ts"):
        return False
    if bot_user_id and event.get("user") == bot_user_id:
        return False
    text = event.get("text") or ""
    if not text:
        return False
    if bot_user_id and f"<@{bot_user_id}>" in text:
        return False
    return True


async def _process_app_mention(
    team_id: str,
    channel_id: str,
    thread_ts: str | None,
    user_id: str,
    text: str,
    event_ts: str,
    event_id: str,
):
    """Process app_mention event in background."""
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace:
                logger.error(f"Workspace not found for team: {team_id}")
                return

            await SlackAgentService.process_mention(
                workspace=workspace,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                text=text,
                event_ts=event_ts,
                event_id=event_id,
                session=session,
            )
    except Exception as e:
        logger.error(f"Error processing app_mention: {e}", exc_info=True)


async def _process_thread_followup(
    team_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    text: str,
    event_ts: str,
    event_id: str,
):
    """Process a non-mention thread message when auto-followup is enabled."""
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace or not workspace.is_active:
                return

            await SlackAgentService.process_thread_followup(
                workspace=workspace,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                text=text,
                event_ts=event_ts,
                event_id=event_id,
                session=session,
            )
    except Exception as e:
        logger.error(f"Error processing Slack thread followup: {e}", exc_info=True)


@router.post("/interactivity")
async def slack_interactivity(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Handle Slack interactive components (buttons, menus, etc.)."""
    import json

    body = await request.body()
    form_data = await request.form()
    payload = json.loads(form_data.get("payload", "{}"))

    team_id = payload.get("team", {}).get("id")
    if not team_id:
        logger.warning("Slack interactivity missing team_id")
        return JSONResponse(status_code=200, content={"ok": True})

    async with AsyncSessionFactory() as session:
        repo = SlackWorkspaceRepository(session)
        workspace = await repo.get_by_team_id(team_id)

        if not workspace:
            logger.warning(f"No workspace found for Slack team: {team_id}")
            return JSONResponse(status_code=200, content={"ok": True})

        if not workspace.is_active:
            logger.info(f"Slack workspace {team_id} is inactive")
            return JSONResponse(status_code=200, content={"ok": True})

        try:
            signing_secret = await SlackAgentService._get_signing_secret(workspace, session)
            SlackSignatureService.verify_signature(
                signing_secret=signing_secret,
                timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
                body=body,
                signature=request.headers.get("X-Slack-Signature", ""),
            )
        except SlackSignatureError as e:
            logger.warning(f"Slack signature verification failed: {e}")
            return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    if payload.get("type") == "block_actions":
        action = payload.get("actions", [{}])[0]
        action_id = action.get("action_id")

        if action_id == "generate_dashboard":
            value_data = json.loads(action.get("value", "{}"))
            user_id = payload.get("user", {}).get("id")

            background_tasks.add_task(
                _process_generate_dashboard_button,
                team_id=team_id,
                notebook_id=value_data.get("notebook_id"),
                channel_id=value_data.get("channel_id"),
                thread_ts=value_data.get("thread_ts"),
                user_id=user_id,
            )

            return JSONResponse(status_code=200, content={"ok": True})

        elif action_id == "auto_generate_chart":
            value_data = json.loads(action.get("value", "{}"))

            background_tasks.add_task(
                _process_auto_generate_chart,
                team_id=team_id,
                table_data=value_data,
            )

            return JSONResponse(status_code=200, content={"ok": True})

        elif action_id == "customize_chart_show_options":
            value_data = json.loads(action.get("value", "{}"))
            response_url = payload.get("response_url")

            background_tasks.add_task(
                _process_customize_chart_options,
                team_id=team_id,
                table_data=value_data,
                response_url=response_url,
            )

            return JSONResponse(status_code=200, content={"ok": True})

        elif action_id == "generate_custom_chart":
            value_data = json.loads(action.get("value", "{}"))
            selected_values = payload.get("state", {}).get("values", {})

            background_tasks.add_task(
                _process_generate_custom_chart,
                team_id=team_id,
                table_data=value_data,
                selected_values=selected_values,
            )

            return JSONResponse(status_code=200, content={"ok": True})

        elif action_id == "download_excel":
            value_data = json.loads(action.get("value", "{}"))

            background_tasks.add_task(
                _process_download_excel,
                team_id=team_id,
                table_data=value_data,
            )

            return JSONResponse(status_code=200, content={"ok": True})

        elif action_id in ("skill_suggestion_approve", "skill_suggestion_reject", "skill_suggestion_discuss"):
            value_data = json.loads(action.get("value", "{}"))

            background_tasks.add_task(
                handle_suggestion_action,
                action_id=action_id,
                suggestion_id=value_data.get("suggestion_id"),
                slack_user_id=payload.get("user", {}).get("id"),
                response_url=payload.get("response_url"),
                team_id=team_id,
            )

            return JSONResponse(status_code=200, content={"ok": True})

    return JSONResponse(status_code=200, content={"ok": True})


async def _process_generate_dashboard_button(
    team_id: str,
    notebook_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
):
    """Process generate dashboard button click in background."""
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace:
                logger.error(f"Workspace not found for team: {team_id}")
                return

            await SlackAgentService.process_generate_dashboard_request(
                workspace=workspace,
                notebook_id=notebook_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                session=session,
            )
    except Exception as e:
        logger.error(f"Error processing generate dashboard button: {e}", exc_info=True)


async def _process_auto_generate_chart(
    team_id: str,
    table_data: dict,
):
    """Process auto generate chart button click in background."""
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace:
                logger.error(f"Workspace not found for team: {team_id}")
                return

            bot_token = await SlackAgentService._get_bot_token(workspace, session)
            slack_client = SlackService(bot_token)

            channel_id = table_data.get("channel_id")
            thread_ts = table_data.get("thread_ts")

            tables = table_data.get("tables")
            if not tables:
                rows = table_data.get("rows")
                if rows:
                    tables = [rows]

            if not tables or len(tables) == 0:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="Error: Table data not found.",
                )
                return

            llm_connection_id = workspace.default_llm_connection_id

            if not llm_connection_id:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="Error: No LLM connection configured for chart generation.",
                )
                return

            await slack_client.post_message(
                channel=channel_id,
                thread_ts=thread_ts,
                text="🤖 Generating chart with LLM selected labels and data points...",
            )

            chart_urls = []
            for table_rows in tables:
                if len(table_rows) < 2:
                    continue

                markdown_table = "| " + " | ".join(table_rows[0]) + " |\n"
                markdown_table += "| " + " | ".join(["---"] * len(table_rows[0])) + " |\n"
                for row in table_rows[1:]:
                    markdown_table += "| " + " | ".join(row) + " |\n"

                table_chart_urls = await SlackChartDetector.generate_chart_with_llm(
                    markdown_table, str(llm_connection_id), session
                )
                if table_chart_urls:
                    chart_urls.extend(table_chart_urls)

            if chart_urls:
                from server.utils.slack_block_elements import SlackBlockBuilder

                chart_blocks = []
                for chart_url in chart_urls:
                    chart_blocks.append(SlackBlockBuilder.image(image_url=chart_url, alt_text="Auto-generated chart"))

                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"✅ Generated {len(chart_urls)} chart(s)",
                    blocks=chart_blocks,
                )
            else:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="❌ Could not generate chart from this data. The table may not contain suitable numeric data.",
                )

    except Exception as e:
        logger.error(f"Error processing auto generate chart: {e}", exc_info=True)


async def _process_customize_chart_options(
    team_id: str,
    table_data: dict,
    response_url: str,
):
    """Show customization options by replacing the original message."""
    logger.info(f"Processing customize chart options for team: {team_id}")
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace:
                logger.error(f"Workspace not found for team: {team_id}")
                return

            tables = table_data.get("tables")
            if not tables:
                rows = table_data.get("rows")
                if rows:
                    tables = [rows]
                else:
                    logger.error("No tables or rows in table_data")
                    return

            if not tables or len(tables) == 0:
                logger.error("No valid tables found")
                return

            first_table = tables[0]
            columns = first_table[0]
            logger.info(f"Extracted {len(columns)} columns from first table: {columns}")

            import httpx

            from server.utils.slack_block_elements import SlackBlockBuilder

            table_selector_options = []
            if len(tables) > 1:
                for idx in range(len(tables)):
                    table_selector_options.append(
                        {"text": {"type": "plain_text", "text": f"Table {idx + 1}"}, "value": str(idx)}
                    )

            x_axis_options = []
            y_axis_options = []
            for idx, col in enumerate(columns):
                option = {"text": {"type": "plain_text", "text": col[:75]}, "value": str(idx)}
                x_axis_options.append(option)
                y_axis_options.append(option)

            chart_type_options = [
                {"text": {"type": "plain_text", "text": "Bar Chart"}, "value": "bar"},
                {"text": {"type": "plain_text", "text": "Horizontal Bar Chart"}, "value": "horizontalBar"},
                {"text": {"type": "plain_text", "text": "Line Chart"}, "value": "line"},
                {"text": {"type": "plain_text", "text": "Pie Chart"}, "value": "pie"},
                {"text": {"type": "plain_text", "text": "Donut Chart"}, "value": "doughnut"},
            ]

            auto_button = SlackBlockBuilder.button(
                text="🤖 Auto Generate",
                action_id="auto_generate_chart",
                value=json.dumps(table_data),
            )

            customize_button = SlackBlockBuilder.button(
                text="⚙️ Customize",
                action_id="customize_chart_show_options",
                value=json.dumps(table_data),
            )

            notebook_id = table_data.get("notebook_id")
            thread_ts = table_data.get("thread_ts")
            channel_id = table_data.get("channel_id")

            dashboard_button = SlackBlockBuilder.button(
                text="📊 Full Dashboard",
                action_id="generate_dashboard",
                value=json.dumps(
                    {
                        "notebook_id": notebook_id,
                        "thread_ts": thread_ts,
                        "channel_id": channel_id,
                    }
                ),
            )

            top_action_buttons = [auto_button, customize_button, dashboard_button]
            download_excel_value = json.dumps(
                {
                    "tables": tables,
                    "thread_ts": thread_ts,
                    "channel_id": channel_id,
                }
            )
            if len(download_excel_value) <= 2000:
                top_action_buttons.append(
                    SlackBlockBuilder.button(
                        text="📥 Download Excel",
                        action_id="download_excel",
                        value=download_excel_value,
                    )
                )
            else:
                logger.warning(
                    f"Table data too large for download_excel button value ({len(download_excel_value)} chars), skipping button in customize view"
                )

            blocks = [
                SlackBlockBuilder.header("📊 Data and Visualization"),
                SlackBlockBuilder.actions(top_action_buttons),
                SlackBlockBuilder.divider(),
                SlackBlockBuilder.header("⚙️ Customize Your Chart"),
                SlackBlockBuilder.section("Select the columns and chart type for your visualization."),
                SlackBlockBuilder.divider(),
            ]

            if len(tables) > 1:
                blocks.append(
                    {
                        "type": "section",
                        "block_id": "table_selector_block",
                        "text": {"type": "mrkdwn", "text": "*Select Table:*"},
                        "accessory": {
                            "type": "static_select",
                            "action_id": "table_select",
                            "placeholder": {"type": "plain_text", "text": "Choose a table"},
                            "options": table_selector_options,
                            "initial_option": table_selector_options[0],
                        },
                    }
                )

            blocks.extend(
                [
                    {
                        "type": "section",
                        "block_id": "x_axis_block",
                        "text": {"type": "mrkdwn", "text": "*X-Axis (Labels):*"},
                        "accessory": {
                            "type": "static_select",
                            "action_id": "x_axis_select",
                            "placeholder": {"type": "plain_text", "text": "Select column"},
                            "options": x_axis_options,
                        },
                    },
                    {
                        "type": "section",
                        "block_id": "y_axis_block",
                        "text": {"type": "mrkdwn", "text": "*Y-Axis (Data):*"},
                        "accessory": {
                            "type": "multi_static_select",
                            "action_id": "y_axis_select",
                            "placeholder": {"type": "plain_text", "text": "Select column(s)"},
                            "options": y_axis_options,
                        },
                    },
                    {
                        "type": "section",
                        "block_id": "chart_type_block",
                        "text": {"type": "mrkdwn", "text": "*Chart Type:*"},
                        "accessory": {
                            "type": "static_select",
                            "action_id": "chart_type_select",
                            "placeholder": {"type": "plain_text", "text": "Select chart type"},
                            "options": chart_type_options,
                            "initial_option": chart_type_options[0],
                        },
                    },
                    SlackBlockBuilder.divider(),
                    SlackBlockBuilder.actions(
                        [
                            SlackBlockBuilder.button(
                                text="Generate Chart",
                                action_id="generate_custom_chart",
                                value=json.dumps(table_data),
                                style="primary",
                            ),
                        ]
                    ),
                ]
            )

            logger.info(f"Sending customization UI with {len(x_axis_options)} column options to Slack via response_url")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    response_url,
                    json={"replace_original": True, "blocks": blocks},
                    timeout=30.0,
                )
                logger.info(f"Slack response_url response: status={response.status_code}, body={response.text}")

    except Exception as e:
        logger.error(f"Error processing customize chart options: {e}", exc_info=True)


async def _process_generate_custom_chart(
    team_id: str,
    table_data: dict,
    selected_values: dict,
):
    """Generate chart based on user selections."""
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace:
                logger.error(f"Workspace not found for team: {team_id}")
                return

            bot_token = await SlackAgentService._get_bot_token(workspace, session)
            slack_client = SlackService(bot_token)

            channel_id = table_data.get("channel_id")
            thread_ts = table_data.get("thread_ts")

            tables = table_data.get("tables")
            if not tables:
                rows = table_data.get("rows")
                if rows:
                    tables = [rows]

            if not tables or len(tables) == 0:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="❌ Error: Table data not found.",
                )
                return

            x_axis_idx = None
            y_axis_indices = []
            chart_type = "bar"
            table_idx = 0

            for block_id, block_data in selected_values.items():
                if "table_select" in block_data:
                    selected = block_data["table_select"].get("selected_option")
                    if selected:
                        table_idx = int(selected["value"])
                elif "x_axis_select" in block_data:
                    selected = block_data["x_axis_select"].get("selected_option")
                    if selected:
                        x_axis_idx = int(selected["value"])
                elif "y_axis_select" in block_data:
                    selected = block_data["y_axis_select"].get("selected_options", [])
                    y_axis_indices = [int(opt["value"]) for opt in selected]
                elif "chart_type_select" in block_data:
                    selected = block_data["chart_type_select"].get("selected_option")
                    if selected:
                        chart_type = selected["value"]

            rows = tables[table_idx] if table_idx < len(tables) else tables[0]

            logger.info(f"User selections - X-axis: {x_axis_idx}, Y-axis: {y_axis_indices}, Chart type: {chart_type}")

            if x_axis_idx is None or not y_axis_indices:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="❌ Please select both X-axis and Y-axis columns.",
                )
                return

            await slack_client.post_message(
                channel=channel_id,
                thread_ts=thread_ts,
                text="⚙️ Generating chart with custom selected labels and data points...",
            )

            columns = rows[0]
            data_rows = rows[1:]

            logger.info(f"Processing {len(data_rows)} data rows with {len(columns)} columns: {columns}")

            labels = [row[x_axis_idx] if x_axis_idx < len(row) else f"Row {i + 1}" for i, row in enumerate(data_rows)]

            logger.info(f"Generated {len(labels)} labels from column index {x_axis_idx}")

            datasets = []
            for col_idx in y_axis_indices:
                if col_idx >= len(columns):
                    continue

                col_name = columns[col_idx]
                values = []
                for row in data_rows:
                    if col_idx < len(row):
                        try:
                            cleaned = str(row[col_idx])
                            cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
                            cleaned = cleaned.replace("%", "").replace(",", "")
                            for unit in [
                                " minutes",
                                " mins",
                                " min",
                                " hours",
                                " hrs",
                                " hr",
                                " seconds",
                                " secs",
                                " sec",
                                " days",
                                " day",
                                " weeks",
                                " week",
                                " months",
                                " month",
                                " years",
                                " year",
                                " kg",
                                " km",
                                " mi",
                                " ft",
                                " m",
                                " cm",
                            ]:
                                cleaned = cleaned.replace(unit, "")
                            cleaned = cleaned.strip()
                            val = float(cleaned)
                            values.append(val)
                        except (ValueError, AttributeError):
                            values.append(None)
                    else:
                        values.append(None)

                datasets.append(
                    {
                        "label": col_name,
                        "data": values,
                    }
                )

            logger.info(f"Built {len(datasets)} datasets with {len(labels)} labels each")
            for i, ds in enumerate(datasets):
                logger.info(f"Dataset {i}: {ds['label']} - {len(ds['data'])} values, sample: {ds['data'][:3]}")

            if not datasets:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="❌ Could not generate chart. Selected columns may not contain numeric data.",
                )
                return

            from server.services.slack_chart_service import ChartService
            from server.utils.slack_block_elements import SlackBlockBuilder

            MAX_CHART_ROWS = 20
            chart_url = ChartService.generate_chart_url(
                chart_type=chart_type,
                labels=labels[:MAX_CHART_ROWS],
                datasets=[{**d, "data": d["data"][:MAX_CHART_ROWS]} for d in datasets],
            )

            logger.info(f"Generated chart URL (length={len(chart_url)}): {chart_url[:200]}...")

            if not chart_url or len(chart_url) > 3000:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"❌ Generated chart URL is {'empty' if not chart_url else 'too long (' + str(len(chart_url)) + ' chars)'}. Try selecting fewer data points or columns.",
                )
                return

            chart_blocks = [SlackBlockBuilder.image(image_url=chart_url, alt_text="Custom chart")]

            await slack_client.post_message(
                channel=channel_id,
                thread_ts=thread_ts,
                text="✅ Custom chart generated",
                blocks=chart_blocks,
            )

    except Exception as e:
        logger.error(f"Error processing generate custom chart: {e}", exc_info=True)


async def _process_download_excel(
    team_id: str,
    table_data: dict,
):
    """Build xlsx from parsed query result tables and upload to Slack thread."""
    try:
        async with AsyncSessionFactory() as session:
            repo = SlackWorkspaceRepository(session)
            workspace = await repo.get_by_team_id(team_id)

            if not workspace:
                logger.error(f"Workspace not found for team: {team_id}")
                return

            bot_token = await SlackAgentService._get_bot_token(workspace, session)
            slack_client = SlackService(bot_token)

            channel_id = table_data.get("channel_id")
            thread_ts = table_data.get("thread_ts")

            tables = table_data.get("tables") or []
            if not tables:
                await slack_client.post_message(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="❌ No query result tables found to export.",
                )
                return

            from server.utils.slack_excel_builder import build_xlsx_from_tables

            xlsx_bytes = build_xlsx_from_tables(tables)

            sheet_word = "sheet" if len(tables) == 1 else "sheets"
            await slack_client.upload_file(
                channel=channel_id,
                file_bytes=xlsx_bytes,
                filename="query_results.xlsx",
                thread_ts=thread_ts,
                initial_comment=f"📥 Query results ({len(tables)} {sheet_word})",
            )
    except Exception as e:
        logger.error(f"Error processing download excel: {e}", exc_info=True)


@router.get("/config")
async def get_slack_config(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get Slack integration configuration for tenant."""
    repo = SlackWorkspaceRepository(session)
    workspace = await repo.get_by_tenant(auth.tenant_id)

    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slack not configured")

    return success_response(
        data=SlackConfigResponse.model_validate(workspace).model_dump(),
        message="Slack configuration retrieved",
    )


@router.post("/config")
async def create_slack_config(
    payload: SlackConfigCreate,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Configure Slack integration for tenant."""
    repo = SlackWorkspaceRepository(session)

    existing = await repo.get_by_tenant(auth.tenant_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack already configured. Use PUT to update or DELETE to remove.",
        )

    slack_client = SlackService(payload.bot_token)
    try:
        bot_info = await slack_client.get_bot_info()
        team_id = bot_info.get("team_id", "unknown")
        team_name = bot_info.get("team", "")
        bot_user_id = bot_info.get("user_id")
    except Exception as e:
        logger.error(f"Failed to validate Slack bot token: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid bot token. Please check the token and try again.",
        )

    bot_token_encrypted = await CryptoService.encrypt_config({"bot_token": payload.bot_token}, session)
    signing_secret_encrypted = await CryptoService.encrypt_config({"signing_secret": payload.signing_secret}, session)

    workspace = await repo.create(
        tenant_id=auth.tenant_id,
        slack_team_id=team_id,
        slack_team_name=team_name,
        bot_token_encrypted=bot_token_encrypted,
        bot_user_id=bot_user_id,
        signing_secret_encrypted=signing_secret_encrypted,
        default_llm_connection_id=payload.default_llm_connection_id,
        installed_by=auth.user_id,
    )

    return success_response(
        data=SlackConfigResponse.model_validate(workspace).model_dump(),
        message="Slack integration configured successfully",
    )


@router.put("/config")
async def update_slack_config(
    payload: SlackConfigUpdate,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update Slack integration configuration."""
    repo = SlackWorkspaceRepository(session)
    workspace = await repo.get_by_tenant(auth.tenant_id)

    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slack not configured")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return success_response(
            data=SlackConfigResponse(
                id=workspace.id,
                slack_team_id=workspace.slack_team_id,
                slack_team_name=workspace.slack_team_name,
                is_active=workspace.is_active,
                default_llm_connection_id=workspace.default_llm_connection_id,
                created_at=workspace.created_at,
            ).model_dump(),
            message="No changes made",
        )

    if payload.bot_token:
        slack_client = SlackService(payload.bot_token)
        try:
            bot_info = await slack_client.get_bot_info()
            team_id = bot_info.get("team_id", "unknown")
            team_name = bot_info.get("team", "")
            bot_user_id = bot_info.get("user_id")

            if team_id != workspace.slack_team_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bot token is for a different Slack workspace. Expected {workspace.slack_team_id}, got {team_id}",
                )

            bot_token_encrypted = await CryptoService.encrypt_config({"bot_token": payload.bot_token}, session)
            updates["bot_token_encrypted"] = bot_token_encrypted
            updates["bot_user_id"] = bot_user_id
            if team_name:
                updates["slack_team_name"] = team_name
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to validate Slack bot token: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid bot token. Please check the token and try again.",
            )
        finally:
            updates.pop("bot_token", None)

    if payload.signing_secret:
        signing_secret_encrypted = await CryptoService.encrypt_config(
            {"signing_secret": payload.signing_secret}, session
        )
        updates["signing_secret_encrypted"] = signing_secret_encrypted
        updates.pop("signing_secret", None)

    workspace = await repo.update(workspace.id, **updates)

    return success_response(
        data=SlackConfigResponse.model_validate(workspace).model_dump(),
        message="Slack configuration updated",
    )


@router.delete("/config")
async def delete_slack_config(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Disconnect Slack integration."""
    repo = SlackWorkspaceRepository(session)
    workspace = await repo.get_by_tenant(auth.tenant_id)

    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slack not configured")

    await repo.delete(workspace.id)

    return success_response(message="Slack integration disconnected")


@router.get("/channels")
async def list_slack_channels(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """List Slack channels accessible to the bot."""
    repo = SlackWorkspaceRepository(session)
    workspace = await repo.get_by_tenant(auth.tenant_id)

    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slack not configured")

    if not workspace.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slack integration is inactive")

    bot_token = await CryptoService.decrypt_config(workspace.bot_token_encrypted, session)
    if not bot_token or "bot_token" not in bot_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve Slack credentials",
        )

    slack_client = SlackService(bot_token["bot_token"])

    try:
        channels = await slack_client.list_channels(limit=200)
        return success_response(data=channels, message=f"Retrieved {len(channels)} channels")
    except Exception as e:
        logger.error(f"Failed to list Slack channels: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list Slack channels",
        )


@router.post("/test-channel/{channel_id}")
async def test_slack_channel(
    channel_id: str,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """Send a test message to verify the bot can post to the specified channel."""
    from slack_sdk.errors import SlackApiError

    repo = SlackWorkspaceRepository(session)
    workspace = await repo.get_by_tenant(auth.tenant_id)

    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slack not configured")

    if not workspace.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slack integration is inactive")

    bot_token = await CryptoService.decrypt_config(workspace.bot_token_encrypted, session)
    if not bot_token or "bot_token" not in bot_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve Slack credentials",
        )

    slack_client = SlackService(bot_token["bot_token"])

    try:
        await slack_client.post_message(
            channel=channel_id,
            text="This is a test message from Byaan to verify the bot can post to this channel.",
        )
        return success_response(
            data={"channel_id": channel_id},
            message="Test message sent successfully",
        )
    except SlackApiError as e:
        error_code = e.response.get("error", "unknown_error")
        error_messages = {
            "not_in_channel": "Bot is not a member of this channel. Please add @Byaan to the channel.",
            "channel_not_found": "Channel not found. Please refresh and try again.",
            "token_expired": "Slack authorization expired. Please reconnect Slack in Settings.",
            "invalid_auth": "Slack authorization is invalid. Please reconnect Slack in Settings.",
            "account_inactive": "Slack workspace is inactive or token has been revoked.",
        }
        user_message = error_messages.get(error_code, f"Unable to send to this channel. Error: {error_code}")
        logger.warning(f"Slack test message failed for channel {channel_id}: {error_code}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=user_message)
    except Exception as e:
        logger.error(f"Failed to send test message to channel {channel_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send test message",
        )
