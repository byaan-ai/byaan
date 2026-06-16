#!/bin/bash
set -e

echo "=== Byaan Self-Hosted Container Starting ==="

# Fail fast if the data volume is out of space. Without this check, postgres
# panics mid-checkpoint and supervisord restarts it forever, hiding the cause.
MIN_FREE_MB="${MIN_FREE_MB:-200}"
avail_mb=$(df -Pm /data 2>/dev/null | awk 'NR==2 {print $4}')
if [ -n "$avail_mb" ] && [ "$avail_mb" -lt "$MIN_FREE_MB" ]; then
    echo "FATAL: /data has only ${avail_mb}MB free (need >=${MIN_FREE_MB}MB)."
    echo "Free space on the host, then restart this container."
    echo "On the host:  ./start.sh prune   # or   docker system prune -af"
    exit 1
fi

# Create log directory on persistent volume
mkdir -p /data/logs

# Database configuration from environment (with defaults)
DB_USER="${POSTGRES_USER:-${DB_USER:-byaan}}"
DB_PASSWORD="${POSTGRES_PASSWORD:-${DB_PASSWORD:-$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 24)}}"
DB_NAME="${POSTGRES_DB:-${DB_NAME:-byaan}}"
PG_BIN="/usr/lib/postgresql/15/bin"

# Force UTF-8 locale defaults to avoid SQL_ASCII initdb behavior.
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

update_pg_hba() {
    if [ -f "/data/postgres/pg_hba.conf" ]; then
        sed -i -E "s/^(local[[:space:]]+all[[:space:]]+all[[:space:]]+)(trust|peer)$/\\1scram-sha-256/" /data/postgres/pg_hba.conf
        sed -i -E "s/^(host[[:space:]]+all[[:space:]]+all[[:space:]]+127\\.0\\.0\\.1\\/32[[:space:]]+)(trust|peer)$/\\1scram-sha-256/" /data/postgres/pg_hba.conf
        if ! grep -q "^host all all 127.0.0.1/32" /data/postgres/pg_hba.conf; then
            echo "host all all 127.0.0.1/32 scram-sha-256" >> /data/postgres/pg_hba.conf
        fi
    fi
}

set_pg_hba_trust() {
    if [ -f "/data/postgres/pg_hba.conf" ]; then
        sed -i -E "s/^(local[[:space:]]+all[[:space:]]+all[[:space:]]+).*/\\1trust/" /data/postgres/pg_hba.conf
        sed -i -E "s/^(host[[:space:]]+all[[:space:]]+all[[:space:]]+127\\.0\\.0\\.1\\/32[[:space:]]+).*/\\1trust/" /data/postgres/pg_hba.conf
        if ! grep -q "^host all all 127.0.0.1/32" /data/postgres/pg_hba.conf; then
            echo "host all all 127.0.0.1/32 trust" >> /data/postgres/pg_hba.conf
        fi
    fi
}

pg_ctl_start_temp() {
    local log_file="${1:-/tmp/pg_init.log}"
    su - postgres -c "${PG_BIN}/pg_ctl -D /data/postgres -l ${log_file} start"
    local retries=0
    until pg_isready -h localhost -p 5432 > /dev/null 2>&1; do
        retries=$((retries + 1))
        if [ "$retries" -ge 30 ]; then
            echo "ERROR: PostgreSQL did not become ready in time"
            return 1
        fi
        sleep 1
    done
}

pg_ctl_stop_temp() {
    local mode="${1:-fast}"
    su - postgres -c "${PG_BIN}/pg_ctl -D /data/postgres stop -m ${mode}" || true
}

pg_query_value() {
    local sql="$1"
    local db="${2:-postgres}"
    su - postgres -c "psql -d \"${db}\" -tAc \"${sql}\"" | tr -d '[:space:]'
}

init_postgres_cluster() {
    if su - postgres -c "${PG_BIN}/initdb -D /data/postgres --encoding=UTF8 --locale=C.UTF-8"; then
        return 0
    fi

    echo "initdb with C.UTF-8 locale failed, retrying with C locale"
    su - postgres -c "${PG_BIN}/initdb -D /data/postgres --encoding=UTF8 --locale=C"
}

ensure_database_role_and_db() {
    if [ "$(pg_query_value "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")" != "1" ]; then
        su - postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';\""
    else
        su - postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';\""
    fi

    if [ "$(pg_query_value "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" != "1" ]; then
        su - postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};\""
    fi
    su - postgres -c "psql -v ON_ERROR_STOP=1 -c \"GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};\""
}

ensure_cluster_encoding() {
    set_pg_hba_trust
    pg_ctl_start_temp "/tmp/pg_encoding_check.log"

    local server_encoding client_encoding
    server_encoding="$(pg_query_value "SHOW server_encoding;")"
    client_encoding="$(pg_query_value "SHOW client_encoding;")"
    echo "PostgreSQL encoding check: server=${server_encoding}, client=${client_encoding}"

    pg_ctl_stop_temp "fast"
    update_pg_hba

    if [ "${server_encoding}" != "UTF8" ]; then
        echo "ERROR: PostgreSQL cluster encoding is '${server_encoding}' (expected UTF8)."
        echo "Please migrate this cluster to UTF8 before continuing."
        return 1
    fi

    return 0
}

# ============================================
# 1. Initialize PostgreSQL if needed
# ============================================
# Backward compatibility: if old deployments have credentials but no marker, create it
if [ -f "/data/.db_credentials" ] && [ ! -f "/data/.pg_init_complete" ]; then
    touch /data/.pg_init_complete
    echo "Created .pg_init_complete marker for existing deployment"
fi

if [ ! -f "/data/.pg_init_complete" ]; then
    echo "Initializing PostgreSQL database..."
    chown -R postgres:postgres /data/postgres

    if [ ! -f "/data/postgres/PG_VERSION" ]; then
        init_postgres_cluster
    fi

    # Start PostgreSQL temporarily with trust auth to create users
    set_pg_hba_trust
    pg_ctl_start_temp "/tmp/pg_init.log"

    # Create database and user (while using trust auth)
    ensure_database_role_and_db

    # Save credentials for subsequent runs
    echo "${DB_USER}:${DB_PASSWORD}" > /data/.db_credentials
    chmod 600 /data/.db_credentials

    # Stop PostgreSQL to apply pg_hba changes
    pg_ctl_stop_temp "fast"

    # NOW secure the authentication (after users exist)
    update_pg_hba

    # Mark initialization as complete
    touch /data/.pg_init_complete

    echo "PostgreSQL initialized successfully"
else
    echo "PostgreSQL data directory exists, skipping initialization"
    chown -R postgres:postgres /data/postgres
    update_pg_hba

    # Load saved credentials if they exist
    if [ -f "/data/.db_credentials" ]; then
        DB_USER=$(cut -d: -f1 /data/.db_credentials)
        DB_PASSWORD=$(cut -d: -f2 /data/.db_credentials)
    fi
fi

# Ensure PostgreSQL run directory exists
mkdir -p /run/postgresql
chown postgres:postgres /run/postgresql

# Ensure cluster encoding is UTF8
if ! ensure_cluster_encoding; then
    exit 1
fi

# ============================================
# 2. Generate Caddyfile from template
# ============================================
echo "Generating Caddyfile..."

# Determine TLS configuration based on DOMAIN
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
    if [ -n "$ACME_EMAIL" ]; then
        TLS_CONFIG="tls $ACME_EMAIL"
    else
        TLS_CONFIG="tls internal"
    fi
    echo "HTTPS mode enabled for domain: $DOMAIN"
else
    DOMAIN=":80"
    TLS_CONFIG=""
    echo "HTTP mode (port 80 only, no TLS)"
fi

# Generate Caddyfile
export DOMAIN
export TLS_CONFIG
envsubst '${DOMAIN} ${TLS_CONFIG}' < /etc/caddy/Caddyfile.template > /etc/caddy/Caddyfile

echo "Caddyfile generated:"
cat /etc/caddy/Caddyfile

# ============================================
# 3. Generate frontend runtime config
# ============================================
echo "Generating frontend runtime config..."

cat > /app/client/dist/config.js << EOF2
window.__RUNTIME_CONFIG__ = {
  apiUrl: "",
  isHosted: true,
  isSelfHosted: true,
  orgName: "${ORG_NAME:-Byaan}",
  googleClientId: "${VITE_GOOGLE_CLIENT_ID:-}"
};
EOF2

echo "Runtime config generated"

# ============================================
# 4. Create persistent data directories
# ============================================
mkdir -p /data/uploads /data/backups

# Create cache directory for byaan user (backend runs as non-root)
mkdir -p /home/byaan/.cache/uv
chown -R byaan:byaan /home/byaan

# Claude CLI data (sessions, OAuth credentials)
mkdir -p /data/claude

# Application data (datasets, DuckDB, agent sessions)
mkdir -p /data/app_data/datasets

# Set ownership for byaan user (backend runs as non-root)
chown -R byaan:byaan /data/claude /data/app_data /data/uploads /data/backups /data/logs

# ============================================
# 4a. Symlink application paths to persistent volume
# ============================================

# Claude CLI for byaan user: ~/.claude -> /data/claude
if [ ! -L /home/byaan/.claude ]; then
    rm -rf /home/byaan/.claude
    ln -sf /data/claude /home/byaan/.claude
    echo "Symlinked /home/byaan/.claude -> /data/claude"
fi

# Keep root symlink for any root-level scripts (migrations, etc.)
if [ ! -L /root/.claude ]; then
    rm -rf /root/.claude
    ln -sf /data/claude /root/.claude
    echo "Symlinked /root/.claude -> /data/claude"
fi

# Datasets & DuckDB: /app/server/.data -> /data/app_data
if [ ! -L /app/server/.data ]; then
    rm -rf /app/server/.data
    ln -sf /data/app_data /app/server/.data
    echo "Symlinked /app/server/.data -> /data/app_data"
fi

# Agent sessions: /app/.data -> /data/app_data (same location)
if [ ! -L /app/.data ]; then
    rm -rf /app/.data
    ln -sf /data/app_data /app/.data
    echo "Symlinked /app/.data -> /data/app_data"
fi

# ============================================
# 5. Set environment variables for backend
# ============================================
export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}"
export APP_MODE=self-hosted

# Persist datasets to /data volume (backup in case symlink missed)
export DATASETS_STORAGE_DIR="/data/app_data/datasets"

if [ -z "$APP_SECRET" ]; then
    echo "ERROR: APP_SECRET is required for self-hosted mode. Set it in your .env."
    exit 1
fi

# WORKER_URL is optional. When unset, worker-backed features (PDF export,
# public share links, AI dashboard screenshots) stay disabled gracefully.
export WORKER_URL="${WORKER_URL:-}"

# Pass through user-provided env vars
export APP_SECRET
export MASTER_USER_EMAIL="${MASTER_USER_EMAIL:-}"
export MASTER_USER_PASSWORD="${MASTER_USER_PASSWORD:-}"
export ORG_NAME="${ORG_NAME:-Byaan}"

# Frontend URL (derived from DOMAIN for OAuth callbacks, invite emails, etc.)
if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && [ "$DOMAIN" != ":80" ]; then
    export FRONTEND_URL="${FRONTEND_URL:-https://${DOMAIN}}"
else
    export FRONTEND_URL="${FRONTEND_URL:-http://localhost}"
fi

# Google OAuth settings
export GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
export GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-}"

# SMTP settings
export SMTP_HOST="${SMTP_HOST:-}"
export SMTP_PORT="${SMTP_PORT:-587}"
export SMTP_USERNAME="${SMTP_USERNAME:-}"
export SMTP_PASSWORD="${SMTP_PASSWORD:-}"
export SMTP_FROM_EMAIL="${SMTP_FROM_EMAIL:-}"
export SMTP_FROM_NAME="${SMTP_FROM_NAME:-Byaan}"
export SMTP_USE_TLS="${SMTP_USE_TLS:-true}"

# ============================================
# 6. Start supervisord
# ============================================
echo "Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
