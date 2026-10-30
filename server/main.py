import asyncio
import json
import os
import re
import resource
import sys
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

# Validate APP_MODE on startup
VALID_APP_MODES = {"desktop", "community", "self-hosted"}
app_mode = os.getenv("APP_MODE", "desktop").lower()
if app_mode not in VALID_APP_MODES:
    raise ValueError(f"Invalid APP_MODE='{app_mode}'. Must be one of: {', '.join(sorted(VALID_APP_MODES))}")

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.services.database_operations import AsyncDatabaseService

os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")
os.environ.setdefault("LANG", "en_US.UTF-8")
os.environ.setdefault("LC_ALL", "en_US.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target_limit = min(4096, hard) if hard != resource.RLIM_INFINITY else 4096
    resource.setrlimit(resource.RLIMIT_NOFILE, (target_limit, hard))
    print(f"Increased file descriptor limit from {soft} to {target_limit}")
except Exception as e:
    print(f"Warning: Could not increase file descriptor limit: {e}")

import logging

from fastapi.middleware.cors import CORSMiddleware

from server.auth.error_messages import AUTH_ERROR_MESSAGES, get_auth_error_message
from server.auth.tenant_context import TenantContextMiddleware
from server.db.session import ensure_database_encoding, ensure_database_schema
from server.routers import app_config as app_config_router
from server.routers import auth as auth_router
from server.routers import cache as cache_router
from server.routers import claude_oauth as claude_oauth_router
from server.routers import codex_oauth as codex_oauth_router
from server.routers import connections as connections_router
from server.routers import custom_skills as custom_skills_router
from server.routers import databricks_oauth as databricks_oauth_router
from server.routers import datasets as datasets_router  # Dataset management
from server.routers import (
    datasources as datasources_router,
)  # Unified datasources (connections + datasets)
from server.routers import exports as exports_router
from server.routers import (
    file_upload as file_upload_router,
)  # File upload with DB storage
from server.routers import folders as folders_router
from server.routers import github as github_router
from server.routers import imports as imports_router
from server.routers import learnings as learnings_router
from server.routers import llm_connections, unified_agent
from server.routers import local_repos as local_repos_router
from server.routers import mcp_keys as mcp_keys_router
from server.routers import notebooks as notebooks_router
from server.routers import queries as queries_router
from server.routers import raw_query as raw_query_router
from server.routers import schedules as schedules_router
from server.routers import scopes as scopes_router
from server.routers import settings as settings_router
from server.routers import skill_loop as skill_loop_router
from server.routers import skill_suggestions as skill_suggestions_router
from server.routers import skills as skills_router
from server.routers import slack as slack_router
from server.routers import tenant as tenant_router
from server.routers import user_preferences as user_preferences_router
from server.routers import users as users_router
from server.routers import waitlist as waitlist_router
from server.schemas.standard_response import error_response, success_response
from server.services.conversation_evaluation_service import skill_loop_service
from server.services.credit_sync_service import credit_sync_service
from server.services.dashboard_refresh_service import dashboard_refresh_service
from server.services.posthog_service import PostHogService
from server.services.schedule_runner_service import schedule_runner_service
from server.utils.config_loader import get_skill_loop_config, is_community_mode, is_self_hosted
from server.utils.custom_logger import configure_log_redaction, get_logger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Suppress expected errors during client disconnection/abort
logging.getLogger("sqlalchemy.pool.impl.AsyncAdaptedQueuePool").setLevel(logging.CRITICAL)
logging.getLogger("claude_agent_sdk._internal.query").setLevel(logging.CRITICAL)

# Enable sensitive data redaction in logs
configure_log_redaction()

logger = get_logger(__name__)

# Global migration status tracking
migration_status = {"completed": False, "error": None, "message": "Migrations pending"}


async def init_posthog_background():
    """Initialize PostHog in background to avoid blocking startup."""
    try:
        await asyncio.sleep(0)  # Yield control to event loop
        PostHogService.initialize()
        logger.info("✅ PostHog initialized successfully in background")
    except Exception as e:
        logger.error(f"⚠️  PostHog initialization failed (non-fatal): {e}")
        # Non-fatal - app continues without analytics


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    try:
        total_start = time.perf_counter()
        logger.info("🚀 Starting backend initialization...")

        # Start PostHog in background (non-blocking)
        start = time.perf_counter()
        asyncio.create_task(init_posthog_background())
        logger.info(f"📊 PostHog background task started: {time.perf_counter() - start:.3f}s")

        # Run database migrations
        migration_status["message"] = "Running database migrations..."
        start = time.perf_counter()
        logger.info("📦 Running database migrations...")
        await ensure_database_schema()
        migration_status["completed"] = True
        migration_status["message"] = "Migrations completed successfully"
        logger.info(f"✅ Database migrations completed: {time.perf_counter() - start:.3f}s")

        # Prime PostHog opt-out cache from DB so background captures respect the flag
        # even before any HTTP request (which would carry the X-Analytics-Opt-Out header).
        await PostHogService.load_opt_outs_from_db()

        start = time.perf_counter()
        await ensure_database_encoding()
        logger.info(f"🔤 Database encoding check completed: {time.perf_counter() - start:.3f}s")

        # Log team features status
        if is_self_hosted():
            logger.info(f"✅ Team features enabled (APP_MODE={os.getenv('APP_MODE')})")
        else:
            logger.info(f"ℹ️  Running in desktop mode (APP_MODE={os.getenv('APP_MODE')})")

        # Self-hosted setup
        if is_self_hosted():
            from server.services.self_hosted_setup import setup_self_hosted_environment

            start = time.perf_counter()
            logger.info("🏢 Setting up self-hosted environment...")
            await setup_self_hosted_environment()
            logger.info(f"✅ Self-hosted setup completed: {time.perf_counter() - start:.3f}s")

        # Community setup (auto-create workspace, no auth)
        elif is_community_mode():
            from server.services.community_setup import setup_community_environment

            start = time.perf_counter()
            await setup_community_environment()
            logger.info(f"🐳 Community setup completed: {time.perf_counter() - start:.3f}s")

        # NOTE: Encryption key initialization and demo notebook seeding are deferred
        # to user onboarding when a tenant is available (required for multi-tenancy support)

        # Credit sync only runs in desktop mode (not community or self-hosted)
        if not is_self_hosted() and not is_community_mode():
            migration_status["message"] = "Starting credit sync service..."
            start = time.perf_counter()
            await credit_sync_service.start()
            logger.info(f"🔄 Credit sync service started: {time.perf_counter() - start:.3f}s")
        else:
            logger.info("⏭️  Credit sync service skipped (not desktop mode)")

        start = time.perf_counter()
        await dashboard_refresh_service.start()
        logger.info(f"🔄 Dashboard refresh service started: {time.perf_counter() - start:.3f}s")

        start = time.perf_counter()
        await schedule_runner_service.start()
        logger.info(f"⏰ Schedule runner service started: {time.perf_counter() - start:.3f}s")

        if get_skill_loop_config()["enabled"]:
            start = time.perf_counter()
            await skill_loop_service.start()
            logger.info(f"🧠 Skill loop service started: {time.perf_counter() - start:.3f}s")
        else:
            logger.info("⏭️  Skill loop service disabled (SKILL_LOOP_ENABLED=false)")

        migration_status["message"] = "Backend ready"
        logger.info("✅ Backend initialization completed successfully")
        logger.info(f"⏱️  TOTAL STARTUP TIME: {time.perf_counter() - total_start:.3f}s")

    except Exception as e:
        import traceback

        logger.error(
            f"❌ Failed to initialize backend: {e}\nTraceback:\n{traceback.format_exc()}",
            exc_info=True,
            posthog_context={"stage": "startup", "traceback": traceback.format_exc()},
        )

        migration_status["completed"] = False
        migration_status["error"] = str(e)
        migration_status["message"] = f"Initialization failed: {str(e)}"

        # Don't re-raise - keep backend alive to report error
        logger.warning("⚠️  Backend staying alive to report initialization error to frontend")

    yield


from fastmcp.utilities.lifespan import combine_lifespans

from server.mcp.http_server import mcp

mcp_app = mcp.http_app(path="/", stateless_http=False)

app = FastAPI(
    title="Database API",
    description="API for interacting Database",
    version="1.0.0",
    lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan),
)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    try:
        # Stop credit sync service (only if it was started)
        if not is_self_hosted():
            await credit_sync_service.stop()

        # Stop dashboard refresh service
        await dashboard_refresh_service.stop()

        # Stop schedule runner service
        await schedule_runner_service.stop()

        # Stop skill loop service
        await skill_loop_service.stop()

        # Shutdown PostHog
        PostHogService.shutdown()

        # Close all database connections
        await AsyncDatabaseService.close_all_connections()
        logger.info("All database connections closed")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


def get_cors_origins() -> list[str]:
    env_origins = os.environ.get("CORS_ORIGINS", "")
    if env_origins:
        return [o.strip() for o in env_origins.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
        "http://localhost:17434",
        "http://127.0.0.1:17434",
        "tauri://localhost",
        "http://tauri.localhost",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add tenant context middleware for automatic tenant isolation
# Extracts X-Tenant-ID header and makes it available via context
app.add_middleware(TenantContextMiddleware)

# Add ProxyHeadersMiddleware for proper handling of X-Forwarded headers behind reverse proxies
# This is needed for correct client IP detection and HTTPS detection in self-hosted deployments
from server.utils.deployment import get_security_flags

if get_security_flags()["proxy_headers_enabled"]:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

EXCLUDED_PATHS = [
    "/api/unified-agent/stream",
    "/api/mcp.*",
]


def should_standardize_response(path: str) -> bool:
    # Skip non-API endpoints
    if not path.startswith("/api/"):
        return False

    for excluded in EXCLUDED_PATHS:
        pattern = excluded.replace("{notebook_id}", "[^/]+")
        pattern = pattern.replace("{thread_id}", "[^/]+")
        pattern = pattern.replace("{connection_id}", "[^/]+")

        if re.match(f"^{pattern}$", path):
            return False

    return True


# CREDIT SYSTEM DISABLED - Uncomment when ready to enable
# Users provide their own API keys, so no credit checking needed for now
# TODO: Re-enable for free tier in the future
#
# @app.middleware("http")
# async def credit_deduction_middleware(request: Request, call_next):
#     """
#     Middleware to automatically deduct credits after AI requests
#     Runs BEFORE standardize_response_middleware
#     """
#     # Process the request first
#     response = await call_next(request)
#
#     # Only deduct credits for AI-related endpoints
#     ai_endpoints = [
#         "/api/unified-agent/",
#         "/api/queries/",
#         "/api/raw-query/",
#     ]
#
#     is_ai_request = any(request.url.path.startswith(endpoint) for endpoint in ai_endpoints)
#
#     # Only deduct if:
#     # 1. It's an AI request
#     # 2. Request was successful (2xx status)
#     # 3. Not a GET request (only mutations cost credits)
#     if is_ai_request and 200 <= response.status_code < 300 and request.method != "GET":
#         try:
#             # Import here to avoid circular imports
#             from server.services.waitlist_service import waitlist_service
#
#             async with AsyncSessionFactory() as session:
#                 # Get stored credentials
#                 credentials = await waitlist_service.get_stored_credentials(session)
#
#                 if credentials and credentials.get("apiKey"):
#                     # Deduct 10 credits per AI request
#                     await waitlist_service.deduct_credits(
#                         credentials["apiKey"],
#                         amount=10,
#                         session=session
#                     )
#                     logger.debug(f"Deducted 10 credits for {request.url.path}")
#         except Exception as e:
#             # Don't fail the request if credit deduction fails
#             # Just log the error
#             logger.error(f"Failed to deduct credits (non-fatal): {e}")
#
#     return response


@app.middleware("http")
async def analytics_opt_out_middleware(request: Request, call_next):
    """Read X-Analytics-Opt-Out header and propagate to PostHog gate for this request."""
    header = request.headers.get("X-Analytics-Opt-Out", "").strip().lower()
    PostHogService.set_request_opt_out(header in ("1", "true", "yes"))
    return await call_next(request)


@app.middleware("http")
async def standardize_response_middleware(request: Request, call_next):
    # Process the request
    response = await call_next(request)

    if not should_standardize_response(request.url.path):
        return response

    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return response

    if not (200 <= response.status_code < 300) or not response.headers.get("content-type", "").startswith(
        "application/json"
    ):
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    try:
        data = json.loads(body.decode())

        if isinstance(data, dict) and "success" in data and "message" in data:
            new_response = Response(
                content=body,
                status_code=response.status_code,
                media_type=response.headers.get("content-type"),
            )
            for key, value in response.headers.raw:
                if key.lower() != b"content-length":
                    new_response.headers.append(key.decode(), value.decode())
            return new_response

        standardized = success_response(data=data, message="Request processed successfully")

        new_response = JSONResponse(
            content=standardized,
            status_code=response.status_code,
        )
        for key, value in response.headers.raw:
            if key.lower() not in (b"content-length", b"content-type"):
                new_response.headers.append(key.decode(), value.decode())
        return new_response
    except (json.JSONDecodeError, UnicodeDecodeError):
        new_response = Response(
            content=body,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )
        for key, value in response.headers.raw:
            if key.lower() != b"content-length":
                new_response.headers.append(key.decode(), value.decode())
        return new_response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Convert auth error codes to human-readable messages
    detail_str = str(exc.detail)
    is_auth_error = "ErrorCode." in detail_str or detail_str in AUTH_ERROR_MESSAGES
    readable_message = get_auth_error_message(detail_str) if is_auth_error else detail_str

    log_message = f"HTTP {exc.status_code}: {request.method} {request.url.path} - {exc.detail}"
    if exc.status_code >= 500:
        logger.error(
            log_message,
            posthog_context={
                "path": request.url.path,
                "method": request.method,
                "status_code": exc.status_code,
                "user_agent": request.headers.get("user-agent"),
                "detail": detail_str,
            },
        )
    else:
        # 4xx are expected client errors (stale notebooks, missing datasources, auth) — don't report to PostHog
        logger.warning(log_message)

    # Check if this is an HTML endpoint - these should return HTML errors, not JSON
    if request.url.path.endswith("/html"):
        from fastapi.responses import HTMLResponse

        error_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error {exc.status_code}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        .error {{ color: #d32f2f; }}
    </style>
</head>
<body>
    <h1 class="error">Error {exc.status_code}</h1>
    <p>{readable_message}</p>
</body>
</html>"""
        return HTMLResponse(content=error_html, status_code=exc.status_code)

    if request.url.path.startswith("/api/"):
        if isinstance(exc.detail, dict):
            return JSONResponse(
                status_code=exc.status_code,
                content=error_response(
                    message=exc.detail.get("message", exc.detail.get("error", str(exc.detail))),
                    data=exc.detail,
                ),
            )
        else:
            return JSONResponse(
                status_code=exc.status_code,
                content=error_response(message=readable_message),
            )

    return JSONResponse(status_code=exc.status_code, content={"detail": readable_message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # 422 is an expected client error (malformed request) — log as warning, don't report to PostHog
    logger.warning(f"Validation error: {request.method} {request.url.path} - {exc.errors()}")

    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=422,
            content=error_response(message="Validation error", data={"errors": exc.errors()}),
        )

    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler to catch ALL unhandled exceptions.
    This ensures every error is tracked in PostHog, no matter where it occurs.
    """
    import traceback

    tb = traceback.format_exc()

    logger.error(
        f"Unhandled exception in {request.method} {request.url.path}: {exc}",
        exc_info=True,
        posthog_context={
            "path": request.url.path,
            "method": request.method,
            "status_code": 500,
            "user_agent": request.headers.get("user-agent"),
            "traceback": tb,
            "exception_type": type(exc).__name__,
            "exception_module": type(exc).__module__,
            "handler": "global_exception_handler",
        },
    )

    # Return appropriate response
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content=error_response(
                message="An unexpected error occurred. Our team has been notified.",
                data={"error_type": type(exc).__name__, "error_message": str(exc)},
            ),
        )

    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(notebooks_router.router, prefix="/api", tags=["notebooks"])

app.include_router(unified_agent.router, prefix="/api", tags=["unified-agent"])

app.include_router(connections_router.router, prefix="/api", tags=["connections"])

# NEW: Dataset management endpoints
app.include_router(datasets_router.router, prefix="/api", tags=["datasets"])

# NEW: Unified datasources (connections + datasets)
app.include_router(datasources_router.router, prefix="/api", tags=["datasources"])

# File upload with database storage
app.include_router(file_upload_router.router, prefix="/api", tags=["file-upload"])

app.include_router(queries_router.router, prefix="/api", tags=["queries"])

app.include_router(raw_query_router.router, prefix="/api", tags=["raw-query"])

app.include_router(llm_connections.router, prefix="/api", tags=["llm-connections"])

app.include_router(claude_oauth_router.router, tags=["claude-oauth"])

app.include_router(codex_oauth_router.router, tags=["codex-oauth"])

app.include_router(exports_router.router, prefix="/api", tags=["exports"])

app.include_router(imports_router.router, prefix="/api", tags=["imports"])

app.include_router(user_preferences_router.router, prefix="/api", tags=["user-preferences"])
app.include_router(learnings_router.router, prefix="/api", tags=["learnings"])

app.include_router(skills_router.router, prefix="/api", tags=["skills"])

app.include_router(custom_skills_router.router, prefix="/api", tags=["custom-skills"])

app.include_router(skill_suggestions_router.router, prefix="/api", tags=["skill-suggestions"])

app.include_router(skill_loop_router.router, prefix="/api", tags=["skill-loop"])

app.include_router(slack_router.router, prefix="/api", tags=["slack"])

app.include_router(mcp_keys_router.router, prefix="/api", tags=["mcp"])
app.include_router(mcp_keys_router.mcp_router, prefix="/api", tags=["mcp"])


@app.api_route("/api/mcp", methods=["GET", "POST"], include_in_schema=False)
async def redirect_mcp_no_slash():
    return RedirectResponse(url="/api/mcp/", status_code=307)


app.mount("/api/mcp", mcp_app)

if not is_self_hosted():
    app.include_router(waitlist_router.router, prefix="/api", tags=["waitlist"])

app.include_router(settings_router.router, prefix="/api", tags=["settings"])

app.include_router(tenant_router.router, prefix="/api", tags=["tenants"])

app.include_router(folders_router.router, prefix="/api", tags=["folders"])

app.include_router(github_router.router, prefix="/api", tags=["github"])
app.include_router(databricks_oauth_router.router, prefix="/api", tags=["databricks-oauth"])

app.include_router(local_repos_router.router, prefix="/api", tags=["local-repos"])

app.include_router(auth_router.router, prefix="/api", tags=["auth"])

app.include_router(app_config_router.router, prefix="/api", tags=["config"])

app.include_router(users_router.router, prefix="/api", tags=["users"])

app.include_router(scopes_router.router, prefix="/api", tags=["scopes"])

app.include_router(cache_router.router, prefix="/api", tags=["cache"])

app.include_router(schedules_router.router, prefix="/api", tags=["schedules"])


@app.get("/health")
async def health_check():
    """
    Health check endpoint that reports backend status including migration completion.
    Frontend should wait until migrations are completed before considering backend ready.

    Returns:
        - status: "starting" - Backend is still initializing
        - status: "healthy" - Backend is ready
        - status: "error" - Backend failed to initialize
    """
    global migration_status

    # Check if initialization failed
    if not migration_status["completed"] and migration_status.get("error"):
        return {
            "status": "error",
            "migrations": migration_status,
            "message": migration_status["message"],
            "error": migration_status["error"],
        }

    # Check if still starting up
    if not migration_status["completed"]:
        return {
            "status": "starting",
            "migrations": migration_status,
            "message": migration_status["message"],
        }

    # All good - backend is healthy
    return {
        "status": "healthy",
        "migrations": migration_status,
        "message": "Backend is running",
    }


if __name__ == "__main__":
    import argparse
    import socket

    def find_available_port(host: str, preferred: int, range_size: int = 1000) -> int:
        for port in range(preferred, preferred + range_size):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind((host, port))
                    return port
            except OSError:
                continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            return s.getsockname()[1]

    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        module_name = sys.argv[2]
        if module_name == "server.mcp.stdio_server":
            from server.mcp.stdio_server import main as stdio_main

            asyncio.run(stdio_main())
            sys.exit(0)
        else:
            print(f"Error: Unknown module '{module_name}'", file=sys.stderr)
            sys.exit(1)

    parser = argparse.ArgumentParser(description="Run the FastAPI backend server")
    parser.add_argument("--port", type=int, default=8000, help="Preferred port to run the server on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to run the server on")
    args = parser.parse_args()

    actual_port = find_available_port(args.host, args.port)
    print(f"BACKEND_PORT:{actual_port}", flush=True)

    reload_flag = os.getenv("VITE_DEV_MODE", "").lower() == "true"

    os.environ.setdefault(
        "BATCH_QUERY_ENDPOINT",
        f"http://{args.host}:{actual_port}/api/queries/batch",
    )

    if getattr(sys, "frozen", False):
        reload_flag = False

    uvicorn.run(
        app,
        host=args.host,
        port=actual_port,
        reload=reload_flag,
    )
