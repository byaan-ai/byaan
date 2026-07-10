# Accessing a locally-running Byaan (dev / testing)

Two ways to run Byaan on a dev box. They are separate stacks with separate ports, databases, and login models.

| | Community (`make dev`) | Hosted (`make hosted`) |
|---|---|---|
| `APP_MODE` | `community` | `self-hosted` |
| Database | SQLite (`server/.data/app.db`) | PostgreSQL (container) |
| App UI port | **17434** | **5173** |
| Backend API port | **17433** | **8000** |
| Postgres | — | 5432 |
| Login | onboarding flow / existing local data | master user seeded from env |
| Closest to | desktop app | production |

The API port serves `/health`, `/docs`, and `/api/mcp` — it is **not** the app. Open the **UI port** in a browser.

## Reaching it from your Mac (remote dev box)

The dev box is headless and ports bind to `0.0.0.0` on the box, not your laptop. Tunnel over SSH:

```bash
# Hosted stack
ssh -L 5173:localhost:5173 -L 8000:localhost:8000 <devbox>
# then open http://localhost:5173

# Community stack
ssh -L 17434:localhost:17434 -L 17433:localhost:17433 <devbox>
# then open http://localhost:17434
```

Or, if the box is on your Tailscale tailnet, browse `http://<tailnet-ip>:5173` directly.

## Hosted stack: master user

Self-hosted mode disables public registration and seeds a single **master user** at startup from these env vars (in `.env` or a compose override):

```
MASTER_USER_EMAIL=admin@byaan.ai
MASTER_USER_PASSWORD=<min 8 chars>
ORG_NAME=Byaan Test Org
```

> Use a **real TLD** for the master email. Reserved TLDs (`.test`, `.local`, `.example`, `.invalid`) pass the bootstrap but the `UserRead` schema's `EmailStr` validator rejects them, so `/users/me` 500s right after login.

If they are missing, startup fails with `ORG_NAME ... is required for self-hosted mode` and the frontend shows an initialization error (it polls the backend status endpoint and blocks until init succeeds). The seeding is idempotent — it is skipped if the master user already exists.

Add these without editing a committed `.env` by layering a compose override:

```bash
# scratch override with just the three env vars under services.server.environment
docker compose -f docker-compose.hosted.yml \
  -f /path/to/hosted.bootstrap.override.yml \
  -p byaan-hosted up -d server
```

## Connecting Claude Code (MCP)

- **Local/community stack** — stdio, no auth:
  ```bash
  claude mcp add-json byaan '{"type":"stdio","command":"uv","args":["--directory","<PROJECT_ROOT>","run","python","-m","server.mcp.stdio_server"]}' --scope user
  ```
- **Hosted stack over the network** — HTTP with a Bearer key (generate in Settings):
  ```bash
  claude mcp add-json byaan '{"type":"http","url":"http://localhost:8000/api/mcp/","headers":{"Authorization":"Bearer <BYAAN_API_KEY>"}}' --scope user
  ```
- **Hosted stack on the server via docker exec** — stdio, identity via `BYAAN_MCP_USER`; see [self-hosted/README.md](self-hosted/README.md#connect-claude-code-via-mcp-stdio-on-the-server).

## Managing the stacks

```bash
make hosted        # start hosted (background)
make hosted-logs   # start hosted with visible logs
make hosted-stop   # stop hosted (keeps Postgres volume)
make hosted-clean  # stop + delete Postgres volume

make dev           # start community (foreground)
make dev-detach    # start community (background)
make stop          # stop community
```
