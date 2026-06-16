#!/bin/bash

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Container config
IMAGE="byaan/self-hosted:stable"
CONTAINER_NAME="byaan"

# Minimum free disk space required (in GB) before starting/updating
MIN_FREE_DISK_GB="${MIN_FREE_DISK_GB:-3}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_FILE_RUNTIME=""

# State tracking for blue-green deployment
STATE_FILE="$SCRIPT_DIR/.byaan-state"
MANIFEST_URL="https://downloads.byaan.ai/docker/docker-manifest.json"
BACKUP_DIR="$SCRIPT_DIR/backups"

print_info() { echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"; }
print_success() { echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"; }
print_warning() { echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"; }
print_error() { echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] $1${NC}"; }

# Blue-green deployment helpers
get_active_color() {
    if [ -f "$STATE_FILE" ]; then
        grep "active=" "$STATE_FILE" | cut -d= -f2
    else
        echo "blue"
    fi
}

get_inactive_color() {
    local active=$(get_active_color)
    if [ "$active" = "blue" ]; then
        echo "green"
    else
        echo "blue"
    fi
}

save_state() {
    cat > "$STATE_FILE" <<EOF
active=$1
version=$2
updated=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ -f /etc/os-release ]]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|raspbian) echo "debian" ;;
            centos|rhel|fedora|rocky|almalinux|amzn) echo "redhat" ;;
            arch|manjaro) echo "arch" ;;
            alpine) echo "alpine" ;;
            *) echo "linux" ;;
        esac
    elif [[ -f /etc/debian_version ]]; then
        echo "debian"
    elif [[ -f /etc/redhat-release ]]; then
        echo "redhat"
    else
        echo "linux"
    fi
}

# Install Docker
install_docker() {
    local os=$(detect_os)

    print_info "Installing Docker..."
    echo ""

    case $os in
        macos)
            print_error "Please install Docker Desktop manually:"
            echo "  brew install --cask docker"
            echo "  Or download from: https://docs.docker.com/desktop/install/mac-install/"
            exit 1
            ;;
        debian|redhat|linux)
            print_info "Running official Docker install script..."
            curl -fsSL https://get.docker.com | sh

            # Start Docker
            if command -v systemctl &> /dev/null; then
                sudo systemctl start docker
                sudo systemctl enable docker
            fi

            # Add current user to docker group
            if [[ -n "$SUDO_USER" ]]; then
                sudo usermod -aG docker "$SUDO_USER"
                print_warning "Added $SUDO_USER to docker group"
            elif [[ "$EUID" -ne 0 ]]; then
                sudo usermod -aG docker "$USER"
                print_warning "Added $USER to docker group"
            fi

            print_success "Docker installed successfully!"
            echo ""

            # Check if we need to use sudo for docker commands
            if ! docker info &> /dev/null; then
                if sudo docker info &> /dev/null; then
                    print_warning "Docker requires sudo. Either:"
                    echo "  1. Log out and back in (to apply group membership)"
                    echo "  2. Run: newgrp docker"
                    echo "  3. Run this script with sudo"
                    exit 1
                fi
            fi
            ;;
        arch)
            print_info "Installing Docker via pacman..."
            sudo pacman -S --noconfirm docker
            sudo systemctl start docker
            sudo systemctl enable docker
            sudo usermod -aG docker "$USER"
            print_success "Docker installed!"
            ;;
        alpine)
            print_info "Installing Docker via apk..."
            sudo apk add --no-cache docker
            sudo rc-service docker start
            sudo rc-update add docker
            print_success "Docker installed!"
            ;;
        *)
            print_error "Unsupported OS. Please install Docker manually:"
            echo "  https://docs.docker.com/engine/install/"
            exit 1
            ;;
    esac
}

# Check if Docker is installed, install if not
ensure_docker_installed() {
    if ! command -v docker &> /dev/null; then
        print_warning "Docker is not installed."
        echo ""
        read -p "Would you like to install Docker now? [Y/n] " -n 1 -r
        echo ""

        if [[ $REPLY =~ ^[Nn]$ ]]; then
            print_error "Docker is required to run Byaan."
            echo ""
            echo "Install Docker manually:"
            echo "  curl -fsSL https://get.docker.com | sh"
            exit 1
        fi

        install_docker
    fi
}

# Check if Docker daemon is running
ensure_docker_running() {
    if ! docker info > /dev/null 2>&1; then
        # Try with sudo
        if sudo docker info > /dev/null 2>&1; then
            print_warning "Docker requires elevated permissions."
            print_info "Tip: Add your user to the docker group and re-login:"
            echo "  sudo usermod -aG docker \$USER"
            echo ""
            print_info "For now, running with sudo..."
            DOCKER_CMD="sudo docker"
            return
        fi

        local os=$(detect_os)
        case $os in
            macos)
                print_error "Docker Desktop is not running."
                echo "Please start Docker Desktop and try again."
                ;;
            *)
                print_warning "Docker daemon is not running. Starting it..."
                if command -v systemctl &> /dev/null; then
                    sudo systemctl start docker
                    sleep 2
                    if docker info > /dev/null 2>&1; then
                        print_success "Docker started!"
                        return
                    fi
                fi
                print_error "Could not start Docker."
                echo "Try: sudo systemctl start docker"
                ;;
        esac
        exit 1
    fi
}

# Docker command (may be prefixed with sudo)
DOCKER_CMD="docker"

# Check Docker prerequisites
check_docker() {
    ensure_docker_installed
    ensure_docker_running
}

# Run docker command (handles sudo if needed)
run_docker() {
    $DOCKER_CMD "$@"
}

snapshot_is_valid() {
    local snapshot_dir="$1"
    local archive="$snapshot_dir/data.tar.gz"

    [ -s "$archive" ] || return 1
    tar tzf "$archive" >/dev/null 2>&1 || return 1
    # Self-hosted PostgreSQL is initialized at /data/postgres in docker/self-hosted/entrypoint.sh.
    tar tzf "$archive" | grep -Eq '(^|/)postgres/PG_VERSION$'
}

restart_container_if_needed() {
    local container_name="$1"
    local was_running="$2"

    if [ -z "$container_name" ]; then
        return 0
    fi

    if [ "$was_running" = "true" ]; then
        print_info "Restarting previous container..."
        run_docker start "$container_name" >/dev/null || true
    else
        print_info "Previous container was stopped before update; leaving it stopped."
    fi
}

abort_update_after_backup_failure() {
    local active_container="$1"
    local active_container_was_running="$2"
    local snapshot_dir="$3"

    if [ -n "$snapshot_dir" ] && [ -d "$snapshot_dir" ]; then
        print_info "Removing failed backup snapshot: $(basename "$snapshot_dir")"
        rm -rf "$snapshot_dir"
    fi

    restart_container_if_needed "$active_container" "$active_container_was_running"
    return 0
}

# Check free disk space on the partition holding Docker's storage.
# Returns 0 if there is at least MIN_FREE_DISK_GB free, 1 otherwise.
check_disk_space() {
    local required_gb="${1:-$MIN_FREE_DISK_GB}"
    local docker_root
    docker_root=$(run_docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "/var/lib/docker")
    [ -d "$docker_root" ] || docker_root="/"

    local avail_kb avail_gb
    avail_kb=$(df -P "$docker_root" 2>/dev/null | awk 'NR==2 {print $4}')
    [ -n "$avail_kb" ] || return 0
    avail_gb=$(( avail_kb / 1024 / 1024 ))

    if [ "$avail_gb" -lt "$required_gb" ]; then
        print_error "Insufficient disk space on $docker_root: ${avail_gb}G free, need at least ${required_gb}G."
        echo ""
        echo "Free space, then retry. Common cleanups:"
        echo "  ./start.sh prune        # remove unused Docker images and old snapshots"
        echo "  df -h                   # see what's full"
        echo "  docker system df        # see Docker disk usage"
        return 1
    fi

    if [ "$avail_gb" -lt $(( required_gb * 2 )) ]; then
        print_warning "Low disk space on $docker_root: ${avail_gb}G free. Consider './start.sh prune'."
    fi

    return 0
}

# Remove all byaan/self-hosted images except the one currently in use
# by a running container. Safe to run any time; only touches our images.
prune_old_images() {
    local in_use
    in_use=$(run_docker ps --filter "ancestor=byaan/self-hosted" --format '{{.Image}}' 2>/dev/null | sort -u)

    local image_id current_id keep_ids=""
    for img in $in_use; do
        current_id=$(run_docker image inspect --format '{{.Id}}' "$img" 2>/dev/null || true)
        [ -n "$current_id" ] && keep_ids="$keep_ids $current_id"
    done

    while IFS= read -r line; do
        [ -n "$line" ] || continue
        image_id="${line%% *}"
        case " $keep_ids " in
            *" $image_id "*) continue ;;
        esac
        run_docker rmi "$image_id" >/dev/null 2>&1 || true
    done < <(run_docker images byaan/self-hosted --format '{{.ID}} {{.Repository}}:{{.Tag}}' 2>/dev/null)
}

prune_snapshots() {
    local max_valid_snapshots="${1:-2}"
    local valid_count=0
    local snapshot

    while IFS= read -r snapshot; do
        [ -n "$snapshot" ] || continue

        if snapshot_is_valid "$snapshot"; then
            valid_count=$((valid_count + 1))
            if [ "$valid_count" -gt "$max_valid_snapshots" ]; then
                print_info "Removing old backup: $(basename "$snapshot")"
                rm -rf "$snapshot"
            fi
        else
            print_warning "Removing invalid backup snapshot: $(basename "$snapshot")"
            rm -rf "$snapshot"
        fi
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type d -name "snapshot_*" -print 2>/dev/null | sort -r)
}

find_latest_valid_snapshot() {
    local snapshot

    while IFS= read -r snapshot; do
        [ -n "$snapshot" ] || continue

        if snapshot_is_valid "$snapshot"; then
            printf "%s\n" "$snapshot"
            return 0
        fi

        print_warning "Skipping invalid snapshot: $(basename "$snapshot")" >&2
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type d -name "snapshot_*" -print 2>/dev/null | sort -r)

    return 1
}

# Load .env file
load_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        print_error "Error: .env file not found"
        echo ""
        print_info "Download the example .env file:"
        echo "  curl -fsSL https://downloads.byaan.ai/docker/env.example -o .env"
        echo ""
        print_info "Or create one manually:"
        echo ""
        echo "cat > .env << 'EOF'"
        echo "APP_SECRET=$(openssl rand -hex 32)"
        echo "MASTER_USER_EMAIL=admin@example.com"
        echo "MASTER_USER_PASSWORD=changeme123"
        echo "ORG_NAME=MyCompany"
        echo "EOF"
        exit 1
    fi

    set -a
    source "$ENV_FILE"
    set +a
}

# Build runtime env file for container
build_runtime_env_file() {
    ENV_FILE_RUNTIME="$(mktemp)"
    cp "$ENV_FILE" "$ENV_FILE_RUNTIME"
}

# Validate required env vars
validate_env() {
    local missing=()
    [[ -z "${APP_SECRET:-}" ]] && missing+=("APP_SECRET")
    [[ -z "${MASTER_USER_EMAIL:-}" ]] && missing+=("MASTER_USER_EMAIL")
    [[ -z "${MASTER_USER_PASSWORD:-}" ]] && missing+=("MASTER_USER_PASSWORD")
    [[ -z "${ORG_NAME:-}" ]] && missing+=("ORG_NAME")

    if [[ ${#missing[@]} -gt 0 ]]; then
        print_error "Missing required environment variables in .env:"
        for var in "${missing[@]}"; do
            echo "  - $var"
        done
        echo ""
        print_info "Edit your .env file and add these values."
        exit 1
    fi
}

# Resolve CONTAINER_NAME to the active blue-green container
resolve_container_name() {
    CONTAINER_NAME="byaan-$(get_active_color)"
}

# Check if container exists
container_exists() {
    run_docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

# Check if container is running
container_running() {
    run_docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

# Start application
start() {
    check_docker
    load_env
    validate_env

    ACTIVE=$(get_active_color)
    resolve_container_name

    # Migrate legacy container naming: rename "byaan" to "byaan-blue"
    if run_docker ps -a --format '{{.Names}}' | grep -q "^byaan$"; then
        if ! run_docker ps -a --format '{{.Names}}' | grep -q "^byaan-blue$"; then
            print_info "Migrating legacy container name 'byaan' -> 'byaan-blue'..."
            run_docker rename byaan byaan-blue
            CONTAINER_NAME="byaan-blue"
        fi
    fi

    if container_running; then
        print_warning "Byaan is already running"
        echo ""
        if [[ -n "${DOMAIN:-}" ]]; then
            echo "Access at: https://$DOMAIN"
        else
            echo "Access at: http://localhost"
        fi
        echo ""
        echo "Run './start.sh logs' to view logs"
        echo "Run './start.sh stop' to stop"
        return
    fi

    if container_exists; then
        check_disk_space || exit 1
        print_info "Starting existing container..."
        run_docker start "$CONTAINER_NAME"
    else
        check_disk_space || exit 1
        print_info "Pulling latest image..."
        run_docker pull "$IMAGE"

        print_info "Starting Byaan ($ACTIVE)..."

        build_runtime_env_file
        local docker_args=(
            -d
            --name "$CONTAINER_NAME"
            --restart unless-stopped
            -p 80:80
            -v byaan_data:/data
            --env-file "$ENV_FILE_RUNTIME"
        )

        if [[ -n "${DOMAIN:-}" ]]; then
            docker_args+=(-p 443:443)
            docker_args+=(-v byaan_caddy:/root/.local/share/caddy)
        fi

        run_docker run "${docker_args[@]}" "$IMAGE"
        rm -f "$ENV_FILE_RUNTIME"

        RESOLVED_VERSION="${IMAGE#*:}"
        if [ "$RESOLVED_VERSION" = "stable" ]; then
            MANIFEST_JSON=$(curl -sf "$MANIFEST_URL" 2>/dev/null)
            RESOLVED_VERSION=$(echo "$MANIFEST_JSON" | jq -r '.stable_version // .version // "stable"' 2>/dev/null)
        fi

        save_state "$ACTIVE" "$RESOLVED_VERSION"
    fi

    # Confirm the container is actually alive before claiming success.
    sleep 3
    if ! container_running; then
        print_error "Byaan container exited shortly after start. Recent logs:"
        echo ""
        run_docker logs --tail 50 "$CONTAINER_NAME" || true
        echo ""
        print_info "Run './start.sh logs' for the full output and fix the configuration in .env."
        exit 1
    fi

    echo ""
    print_success "Byaan is running!"
    echo ""
    if [[ -n "${DOMAIN:-}" ]]; then
        echo "Access at: https://$DOMAIN"
    else
        echo "Access at: http://localhost"
    fi
    echo ""
    echo "Login with: $MASTER_USER_EMAIL"
    echo ""
}

# Stop application
stop() {
    check_docker
    resolve_container_name

    # Also check legacy container name
    if ! container_exists; then
        CONTAINER_NAME="byaan"
        if ! container_exists; then
            print_warning "Byaan is not running"
            return
        fi
    fi

    print_info "Stopping Byaan..."
    run_docker stop "$CONTAINER_NAME" > /dev/null
    print_success "Stopped"
}

# Update to latest version (stop-migrate-start approach)
# Blue-green is not possible with embedded Postgres sharing a single data volume,
# so we stop the current container, back up, start the new version, and let the
# container entrypoint handle migrations.
update() {
    check_docker
    load_env
    validate_env

    ACTIVE=$(get_active_color)

    CURRENT_VERSION=$(grep "version=" "$STATE_FILE" 2>/dev/null | cut -d= -f2 || echo "unknown")

    print_info "Fetching manifest..."
    MANIFEST_JSON=$(curl -sf "$MANIFEST_URL" 2>/dev/null)

    if [ -z "$MANIFEST_JSON" ]; then
        print_info "No update manifest available"
        return 0
    fi

    LATEST_STABLE_VERSION=$(echo "$MANIFEST_JSON" | jq -r '.stable_version // .version' 2>/dev/null || echo "unknown")

    if [ "$CURRENT_VERSION" = "$LATEST_STABLE_VERSION" ]; then
        print_success "Already on latest version: $CURRENT_VERSION"
        return 0
    fi

    print_info "Update available: $CURRENT_VERSION -> $LATEST_STABLE_VERSION"

    # Updates pull a new image, create a backup snapshot, and keep the old image
    # around briefly for rollback. Require ~5G headroom (image + snapshot + slack).
    check_disk_space 5 || return 1

    NEW_IMAGE="byaan/self-hosted:$LATEST_STABLE_VERSION"

    # Find the active container, including stopped containers, so failures can preserve its prior state.
    ACTIVE_CONTAINER=""
    ACTIVE_CONTAINER_WAS_RUNNING=false
    for name in "byaan-$ACTIVE" "byaan"; do
        if run_docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            ACTIVE_CONTAINER="$name"
            if run_docker ps --format '{{.Names}}' | grep -q "^${name}$"; then
                ACTIVE_CONTAINER_WAS_RUNNING=true
            fi
            break
        fi
    done

    # Step 1: Pull new image before stopping anything
    print_info "Step 1/5: Pulling new image..."
    run_docker pull "$NEW_IMAGE"

    # Step 2: Stop the active container before backing up PostgreSQL files.
    # A raw tar backup of a live PostgreSQL data directory is not safe.
    print_info "Step 2/5: Stopping current container for consistent backup..."
    if [ "$ACTIVE_CONTAINER_WAS_RUNNING" = "true" ]; then
        run_docker stop "$ACTIVE_CONTAINER" >/dev/null
    elif [ -n "$ACTIVE_CONTAINER" ]; then
        print_info "Active container is already stopped; backing up the data volume without starting it"
    else
        print_warning "No running container found; backing up the existing data volume as-is"
    fi

    # Step 3: Create backup (keep last 2 snapshots after a successful update)
    print_info "Step 3/5: Creating backup..."
    mkdir -p "$BACKUP_DIR"

    SNAPSHOT_DIR="$BACKUP_DIR/snapshot_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$SNAPSHOT_DIR"
    echo "$CURRENT_VERSION" > "$SNAPSHOT_DIR/version"

    print_info "Archiving Docker volume byaan_data (this can take a while for large databases/uploads)..."
    BACKUP_LOG="$SNAPSHOT_DIR/backup.log"
    if ! run_docker run --rm \
        --entrypoint tar \
        -v byaan_data:/data:ro \
        -v "$SNAPSHOT_DIR":/backup \
        "$NEW_IMAGE" czf /backup/data.tar.gz -C /data . >"$BACKUP_LOG" 2>&1; then
        print_error "Backup failed. Update aborted before starting the new version."
        print_error "Backup command output:"
        tail -n 20 "$BACKUP_LOG" 2>/dev/null || true
        abort_update_after_backup_failure "$ACTIVE_CONTAINER" "$ACTIVE_CONTAINER_WAS_RUNNING" "$SNAPSHOT_DIR"
        return 1
    fi

    if [ ! -s "$SNAPSHOT_DIR/data.tar.gz" ]; then
        print_error "Backup archive is missing or empty. Update aborted."
        abort_update_after_backup_failure "$ACTIVE_CONTAINER" "$ACTIVE_CONTAINER_WAS_RUNNING" "$SNAPSHOT_DIR"
        return 1
    fi

    print_info "Validating backup archive..."
    if ! snapshot_is_valid "$SNAPSHOT_DIR"; then
        print_error "Backup archive validation failed. Update aborted."
        print_error "The archive must be readable and contain /data/postgres/PG_VERSION."
        abort_update_after_backup_failure "$ACTIVE_CONTAINER" "$ACTIVE_CONTAINER_WAS_RUNNING" "$SNAPSHOT_DIR"
        return 1
    else
        rm -f "$BACKUP_LOG"
        print_success "Backup saved to $SNAPSHOT_DIR"
    fi

    # Remove old container after backup succeeds
    print_info "Removing old container..."
    if [ -n "$ACTIVE_CONTAINER" ]; then
        run_docker rm "$ACTIVE_CONTAINER" >/dev/null 2>&1 || true
    fi

    # Also clean up any leftover containers from failed updates
    for name in "byaan-blue" "byaan-green" "byaan"; do
        if run_docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            run_docker stop "$name" >/dev/null 2>&1 || true
            run_docker rm "$name" >/dev/null 2>&1 || true
        fi
    done

    # Step 4: Start new container (entrypoint handles Postgres + migrations)
    print_info "Step 4/5: Starting new container..."
    build_runtime_env_file

    local docker_args=(
        -d
        --name "byaan-$ACTIVE"
        --restart unless-stopped
        -p 80:80
        -v byaan_data:/data
        --env-file "$ENV_FILE_RUNTIME"
    )

    if [[ -n "${DOMAIN:-}" ]]; then
        docker_args+=(-p 443:443)
        docker_args+=(-v byaan_caddy:/root/.local/share/caddy)
    fi

    run_docker run "${docker_args[@]}" "$NEW_IMAGE"
    rm -f "$ENV_FILE_RUNTIME"

    # Step 5: Health check
    print_info "Step 5/5: Running health checks (waiting up to 3 minutes)..."

    HEALTH_TIMEOUT=${HEALTH_CHECK_TIMEOUT:-180}
    HEALTH_OK=false

    for i in $(seq 1 $HEALTH_TIMEOUT); do
        if curl -sf "http://localhost:80/health" >/dev/null 2>&1; then
            HEALTH_OK=true
            print_success "Container ready after ${i} seconds"
            break
        fi
        sleep 1
    done

    if [ "$HEALTH_OK" = false ]; then
        print_error "Health check failed after update"
        print_info "Check logs with: ./start.sh logs"
        print_info "If needed, rollback with: ./start.sh rollback"
        return 1
    fi

    save_state "$ACTIVE" "$LATEST_STABLE_VERSION"

    prune_snapshots 2
    print_info "Pruning old byaan images..."
    prune_old_images

    echo ""
    print_success "Update complete! Now running $LATEST_STABLE_VERSION"
    echo ""
    if [[ -n "${DOMAIN:-}" ]]; then
        echo "Access at: https://$DOMAIN"
    else
        echo "Access at: http://localhost"
    fi
    echo ""
}

# Rollback to previous version with database restoration
rollback() {
    check_docker
    load_env

    ACTIVE=$(get_active_color)

    print_info "Finding latest snapshot..."

    LATEST_SNAPSHOT=$(find_latest_valid_snapshot || true)

    if [ -z "$LATEST_SNAPSHOT" ]; then
        print_error "No valid snapshots found to rollback to."
        echo ""
        echo "Snapshots are created automatically during updates."
        echo "You need at least one successful, validated update backup before you can rollback."
        exit 1
    fi

    SNAPSHOT_VERSION=$(cat "$LATEST_SNAPSHOT/version" 2>/dev/null || echo "unknown")
    SNAPSHOT_DATE=$(stat -c %y "$LATEST_SNAPSHOT" 2>/dev/null || stat -f %Sm -t "%Y-%m-%d %H:%M:%S" "$LATEST_SNAPSHOT" 2>/dev/null || echo "unknown")
    print_success "Selected valid snapshot: $(basename "$LATEST_SNAPSHOT")"

    echo ""
    print_warning "WARNING: This will restore your database!"
    echo ""
    echo "Rollback details:"
    echo "  Snapshot: $(basename "$LATEST_SNAPSHOT")"
    echo "  Version:  $SNAPSHOT_VERSION"
    echo "  Created:  $SNAPSHOT_DATE"
    echo ""
    echo "This will:"
    echo "  1. Stop the current container"
    echo "  2. Restore database from snapshot"
    echo "  3. Start version $SNAPSHOT_VERSION"
    echo ""
    print_warning "All data changes since this snapshot will be LOST!"
    echo ""

    read -p "Continue with rollback? [y/N] " -n 1 -r
    echo

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Rollback cancelled"
        exit 0
    fi

    ROLLBACK_IMAGE="byaan/self-hosted:$SNAPSHOT_VERSION"

    print_info "Pulling rollback image..."
    if ! run_docker pull "$ROLLBACK_IMAGE" 2>/dev/null; then
        print_error "Could not pull rollback image: $ROLLBACK_IMAGE"
        print_error "Rollback aborted before changing data. The exact snapshot version must be available to avoid schema mismatch."
        exit 1
    fi

    print_info "Starting rollback..."

    # Stop all possible containers
    for name in "byaan-blue" "byaan-green" "byaan"; do
        if run_docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            print_info "Stopping container $name..."
            run_docker stop "$name" >/dev/null 2>&1 || true
            run_docker rm "$name" >/dev/null 2>&1 || true
        fi
    done

    print_info "Restoring database from snapshot..."
    if ! run_docker run --rm \
        --entrypoint sh \
        -v byaan_data:/data \
        -v "$LATEST_SNAPSHOT":/backup:ro \
        "$ROLLBACK_IMAGE" -c '
            set -e
            rollback_previous="/data/.rollback-previous-$(date +%Y%m%d_%H%M%S)"
            rm -rf /data/.restore-tmp
            mkdir -p /data/.restore-tmp
            tar xzf /backup/data.tar.gz -C /data/.restore-tmp

            mkdir -p "$rollback_previous"
            cd /data
            for item in .[!.]* ..?* *; do
                [ -e "$item" ] || continue
                [ "$item" = ".restore-tmp" ] && continue
                [ "$item" = ".rollback-previous" ] && continue
                case "$item" in
                    .rollback-previous-*) continue ;;
                esac
                mv "$item" "$rollback_previous"/
            done

            cd /data/.restore-tmp
            for item in .[!.]* ..?* *; do
                [ -e "$item" ] || continue
                mv "$item" /data/
            done

            rmdir /data/.restore-tmp
            rm -rf "$rollback_previous"
        '; then
        print_error "Failed to restore database snapshot!"
        echo ""
        echo "Rollback did not complete. Previous data may still be available inside the byaan_data volume at /data/.rollback-previous-*."
        echo "The snapshot may be corrupted or the Docker volume may be out of space: $LATEST_SNAPSHOT"
        exit 1
    fi

    print_success "Database restored successfully"

    print_info "Starting container with version $SNAPSHOT_VERSION..."

    build_runtime_env_file
    local docker_args=(
        -d
        --name "byaan-$ACTIVE"
        --restart unless-stopped
        -p 80:80
        -v byaan_data:/data
        --env-file "$ENV_FILE_RUNTIME"
    )

    if [[ -n "${DOMAIN:-}" ]]; then
        docker_args+=(-p 443:443)
        docker_args+=(-v byaan_caddy:/root/.local/share/caddy)
    fi

    run_docker run "${docker_args[@]}" "$ROLLBACK_IMAGE"
    rm -f "$ENV_FILE_RUNTIME"

    save_state "$ACTIVE" "$SNAPSHOT_VERSION"

    echo ""
    print_success "Rollback complete!"
    echo ""
    echo "Restored to:"
    echo "  Version:  $SNAPSHOT_VERSION"
    echo "  Snapshot: $(basename "$LATEST_SNAPSHOT")"
    echo ""
    if [[ -n "${DOMAIN:-}" ]]; then
        echo "Access at: https://$DOMAIN"
    else
        echo "Access at: http://localhost"
    fi
    echo ""
}

# Show logs
logs() {
    check_docker
    resolve_container_name

    # Also check legacy container name
    if ! container_exists; then
        CONTAINER_NAME="byaan"
        if ! container_exists; then
            print_error "Byaan is not running"
            exit 1
        fi
    fi

    local log_type="${1:-all}"

    case "$log_type" in
        backend)
            run_docker exec "$CONTAINER_NAME" tail -f /data/logs/backend.log
            ;;
        caddy)
            run_docker exec "$CONTAINER_NAME" tail -f /data/logs/caddy.log
            ;;
        postgres)
            run_docker exec "$CONTAINER_NAME" tail -f /data/logs/postgres.log
            ;;
        all)
            run_docker exec "$CONTAINER_NAME" tail -f /data/logs/backend.log /data/logs/caddy.log /data/logs/postgres.log
            ;;
        *)
            print_error "Unknown log type: $log_type"
            echo "Usage: ./start.sh logs [all|backend|caddy|postgres]"
            exit 1
            ;;
    esac
}

# Show status
status() {
    check_docker
    resolve_container_name

    # Also check legacy container name
    if ! container_exists && ! container_running; then
        CONTAINER_NAME="byaan"
    fi

    if container_running; then
        print_success "Byaan is running"
        echo ""
        run_docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Status}}\t{{.Ports}}"
    elif container_exists; then
        print_warning "Byaan is stopped"
        echo ""
        echo "Run './start.sh' to start"
    else
        print_info "Byaan is not installed"
        echo ""
        echo "Run './start.sh' to install and start"
    fi
}

# Remove container and optionally data
remove() {
    check_docker

    # Remove all possible container names (active, inactive, legacy)
    for name in "byaan-blue" "byaan-green" "byaan"; do
        if run_docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
            print_info "Stopping and removing $name..."
            run_docker stop "$name" >/dev/null 2>&1 || true
            run_docker rm "$name" >/dev/null 2>&1 || true
        fi
    done

    if [[ "${1:-}" == "--data" ]]; then
        print_warning "Removing data volumes..."
        run_docker volume rm byaan_data byaan_caddy 2>/dev/null || true
        print_success "Removed container and all data"
    else
        print_success "Removed container (data preserved in byaan_data volume)"
    fi
}

# Reclaim disk: remove unused byaan images and old snapshots beyond the
# 2 most recent valid ones. Does NOT touch the byaan_data volume.
prune() {
    check_docker

    print_info "Disk usage before:"
    df -h / 2>/dev/null | awk 'NR==1 || NR==2'

    print_info "Pruning old byaan images..."
    prune_old_images

    print_info "Pruning old snapshots (keeping 2 most recent valid)..."
    prune_snapshots 2

    print_info "Pruning dangling Docker layers..."
    run_docker image prune -f >/dev/null 2>&1 || true

    print_success "Prune complete"
    echo ""
    print_info "Disk usage after:"
    df -h / 2>/dev/null | awk 'NR==1 || NR==2'
}

# Sync start.sh from remote
sync_script() {
    SCRIPT_URL="https://downloads.byaan.ai/docker/start.sh"
    SCRIPT_PATH="$SCRIPT_DIR/start.sh"

    print_info "Fetching latest start.sh..."
    TEMP_SCRIPT="$(mktemp)"

    if ! curl -fsSL "$SCRIPT_URL" -o "$TEMP_SCRIPT" 2>/dev/null; then
        print_error "Failed to download latest start.sh"
        rm -f "$TEMP_SCRIPT"
        return 1
    fi

    if diff -q "$SCRIPT_PATH" "$TEMP_SCRIPT" >/dev/null 2>&1; then
        print_success "start.sh is already up to date"
        rm -f "$TEMP_SCRIPT"
        return 0
    fi

    cp "$TEMP_SCRIPT" "$SCRIPT_PATH"
    chmod +x "$SCRIPT_PATH"
    rm -f "$TEMP_SCRIPT"

    print_success "start.sh updated successfully"
}

# Show help
show_help() {
    echo "Byaan Self-Hosted Management"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  (none)         Start the application"
    echo "  stop           Stop the application"
    echo "  update         Pull latest image and restart"
    echo "  rollback       Rollback to previous version (after failed update)"
    echo "  logs           Show all logs (backend, caddy, postgres)"
    echo "  logs backend   Show backend logs only"
    echo "  logs caddy     Show caddy/web server logs only"
    echo "  logs postgres  Show database logs only"
    echo "  status         Show container status"
    echo "  sync           Update this script to the latest version"
    echo "  prune          Free disk: remove unused byaan images and old snapshots"
    echo "  remove         Remove container (keeps data)"
    echo "  remove --data  Remove container and all data"
    echo "  help           Show this help message"
    echo ""
    echo "Documentation: https://docs.byaan.ai/self-hosted"
}

# Main
case "${1:-}" in
    stop)
        stop
        ;;
    update)
        update
        ;;
    rollback)
        rollback
        ;;
    logs)
        logs "${2:-all}"
        ;;
    status)
        status
        ;;
    sync)
        sync_script
        ;;
    prune)
        prune
        ;;
    remove)
        remove "${2:-}"
        ;;
    help|--help|-h)
        show_help
        ;;
    "")
        start
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
