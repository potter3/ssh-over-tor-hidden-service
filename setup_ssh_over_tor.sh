#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"

SSH_PORT=22
ENABLE_FAIL2BAN="yes"
ALLOW_PASSWORD_AUTH="yes"
HIDDEN_SERVICE_DIR="/var/lib/tor/ssh_service"
HOSTNAME_OUTPUT_FILE="./ssh.txt"

log() {
  printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

die() {
  printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $SCRIPT_NAME [options]

One-shot setup for SSH over a Tor hidden service on Debian-based systems.

Options:
  --ssh-port <port>             Local SSH daemon port to listen on (default: 22)
  --allow-password-auth <yes|no>
                                Enable or disable SSH password auth (default: yes)
  --enable-fail2ban <yes|no>    Configure/start Fail2Ban (default: yes)
  --hidden-service-dir <path>   Tor HiddenServiceDir (default: /var/lib/tor/ssh_service)
  --hostname-output-file <path> Write generated onion hostname to file (default: ./ssh.txt)
  -h, --help                    Show this help

Example:
  sudo ./$SCRIPT_NAME --ssh-port 22 --enable-fail2ban yes
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-port)
      [[ $# -ge 2 ]] || die "Missing value for --ssh-port"
      SSH_PORT="$2"
      shift 2
      ;;
    --allow-password-auth)
      [[ $# -ge 2 ]] || die "Missing value for --allow-password-auth"
      ALLOW_PASSWORD_AUTH="$2"
      shift 2
      ;;
    --enable-fail2ban)
      [[ $# -ge 2 ]] || die "Missing value for --enable-fail2ban"
      ENABLE_FAIL2BAN="$2"
      shift 2
      ;;
    --hidden-service-dir)
      [[ $# -ge 2 ]] || die "Missing value for --hidden-service-dir"
      HIDDEN_SERVICE_DIR="$2"
      shift 2
      ;;
    --hostname-output-file)
      [[ $# -ge 2 ]] || die "Missing value for --hostname-output-file"
      HOSTNAME_OUTPUT_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1 (use --help)"
      ;;
  esac
done

[[ "$SSH_PORT" =~ ^[0-9]+$ ]] || die "--ssh-port must be numeric"
(( SSH_PORT >= 1 && SSH_PORT <= 65535 )) || die "--ssh-port must be 1-65535"

case "$ALLOW_PASSWORD_AUTH" in
  yes|no) ;;
  *) die "--allow-password-auth must be yes or no" ;;
esac

case "$ENABLE_FAIL2BAN" in
  yes|no) ;;
  *) die "--enable-fail2ban must be yes or no" ;;
esac

if ! command -v apt-get >/dev/null 2>&1; then
  die "This script targets Debian/Ubuntu/Parrot/Kali (apt-get required)"
fi

SUDO=""
APT_UPDATED=0
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    die "Run as root or install sudo"
  fi
  SUDO="sudo"
fi

run_as_root() {
  if [[ -n "$SUDO" ]]; then
    "$SUDO" "$@"
  else
    "$@"
  fi
}

apt_update_once() {
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    log "Running apt-get update..."
    run_as_root apt-get update
    APT_UPDATED=1
  fi
}

package_installed() {
  local package_name="$1"
  local status
  status="$(run_as_root dpkg-query -W -f='${db:Status-Status}' "$package_name" 2>/dev/null || true)"
  [[ "$status" == "installed" ]]
}

ensure_package_installed() {
  local package_name="$1"
  if package_installed "$package_name"; then
    log "Package already installed: $package_name"
    return 0
  fi
  apt_update_once
  log "Installing missing package: $package_name"
  run_as_root apt-get install -y "$package_name"
}

install_optional_package() {
  local package_name="$1"
  if package_installed "$package_name"; then
    return 0
  fi
  apt_update_once
  if run_as_root apt-get install -y "$package_name" >/dev/null 2>&1; then
    log "Installed optional package: $package_name"
  else
    log "Optional package not installed (continuing): $package_name"
  fi
}

backup_if_missing() {
  local src="$1"
  local backup="$2"
  if run_as_root test -f "$src" && ! run_as_root test -f "$backup"; then
    run_as_root cp -a "$src" "$backup"
    log "Backup created: $backup"
  fi
}

write_managed_block() {
  local file="$1"
  local begin_marker="$2"
  local end_marker="$3"
  local block_content="$4"
  local cleaned_tmp final_tmp

  cleaned_tmp="$(mktemp)"
  final_tmp="$(mktemp)"

  run_as_root touch "$file"
  run_as_root awk -v begin="$begin_marker" -v end="$end_marker" '
    $0 == begin { in_block=1; next }
    $0 == end   { in_block=0; next }
    !in_block   { print }
  ' "$file" > "$cleaned_tmp"

  {
    cat "$cleaned_tmp"
    printf '\n%s\n' "$begin_marker"
    printf '%s\n' "$block_content"
    printf '%s\n' "$end_marker"
  } > "$final_tmp"

  run_as_root install -m 0644 "$final_tmp" "$file"
  rm -f "$cleaned_tmp" "$final_tmp"
}

restart_service_any() {
  local service
  for service in "$@"; do
    if run_as_root systemctl restart "$service" >/dev/null 2>&1; then
      log "Restarted service: $service"
      printf '%s' "$service"
      return 0
    fi
  done
  return 1
}

enable_service_any() {
  local service
  for service in "$@"; do
    if run_as_root systemctl enable "$service" >/dev/null 2>&1; then
      log "Enabled service at boot: $service"
      return 0
    fi
  done
  return 1
}

log "Ensuring required packages are installed..."
ensure_package_installed "tor"
ensure_package_installed "openssh-server"
ensure_package_installed "torsocks"
if [[ "$ENABLE_FAIL2BAN" == "yes" ]]; then
  ensure_package_installed "fail2ban"
fi
install_optional_package "micro"
install_optional_package "pv"

log "Creating backups..."
backup_if_missing "/etc/ssh/sshd_config" "/etc/ssh/sshd_config.bak"
backup_if_missing "/etc/tor/torrc" "/etc/tor/torrc.bak"
backup_if_missing "/etc/fail2ban/jail.conf" "/etc/fail2ban/jail.conf.bak"

log "Configuring SSH daemon..."
SSH_BLOCK=$(
  cat <<EOF
Port $SSH_PORT
ListenAddress 127.0.0.1
PermitRootLogin no
PasswordAuthentication $ALLOW_PASSWORD_AUTH
PubkeyAuthentication yes
UseDNS no
GSSAPIAuthentication no
MaxAuthTries 3
LoginGraceTime 30
MaxStartups 3:30:10
EOF
)
write_managed_block \
  "/etc/ssh/sshd_config" \
  "# BEGIN SSH_OVER_TOR_MANAGED" \
  "# END SSH_OVER_TOR_MANAGED" \
  "$SSH_BLOCK"

run_as_root sshd -t
SSH_SERVICE="$(restart_service_any ssh sshd)" || die "Failed to restart ssh/sshd"
enable_service_any "$SSH_SERVICE" || true

log "Configuring Tor hidden service..."
run_as_root mkdir -p "$HIDDEN_SERVICE_DIR"
run_as_root chmod 0700 "$HIDDEN_SERVICE_DIR"

for tor_user in debian-tor toranon tor; do
  if id -u "$tor_user" >/dev/null 2>&1; then
    run_as_root chown -R "$tor_user:$tor_user" "$HIDDEN_SERVICE_DIR"
    log "Using Tor service account: $tor_user"
    break
  fi
done

TOR_BLOCK=$(
  cat <<EOF
# SSH Hidden Service
HiddenServiceDir ${HIDDEN_SERVICE_DIR%/}/
HiddenServicePort 22 127.0.0.1:$SSH_PORT
EOF
)
write_managed_block \
  "/etc/tor/torrc" \
  "# BEGIN SSH_OVER_TOR_MANAGED" \
  "# END SSH_OVER_TOR_MANAGED" \
  "$TOR_BLOCK"

TOR_SERVICE="$(restart_service_any tor tor@default)" || die "Failed to restart tor service"
enable_service_any "$TOR_SERVICE" || true

if [[ "$ENABLE_FAIL2BAN" == "yes" ]]; then
  log "Configuring Fail2Ban..."
  FAIL2BAN_BLOCK=$(
    cat <<EOF
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 3

[sshd]
enabled  = true
port     = $SSH_PORT
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 3
EOF
  )
  write_managed_block \
    "/etc/fail2ban/jail.local" \
    "# BEGIN SSH_OVER_TOR_MANAGED" \
    "# END SSH_OVER_TOR_MANAGED" \
    "$FAIL2BAN_BLOCK"

  run_as_root systemctl enable --now fail2ban >/dev/null 2>&1 || true
  run_as_root systemctl restart fail2ban >/dev/null 2>&1 || true
  log "Fail2Ban configured and restarted."
else
  log "Skipping Fail2Ban by request."
fi

log "Waiting for onion hostname..."
HOSTNAME_PATH="${HIDDEN_SERVICE_DIR%/}/hostname"
for _ in $(seq 1 30); do
  if run_as_root test -s "$HOSTNAME_PATH"; then
    break
  fi
  sleep 1
done

if ! run_as_root test -s "$HOSTNAME_PATH"; then
  die "Tor hostname file not generated: $HOSTNAME_PATH"
fi

ONION_HOSTNAME="$(run_as_root cat "$HOSTNAME_PATH" | tr -d '\r\n')"
printf '%s\n' "$ONION_HOSTNAME" > "$HOSTNAME_OUTPUT_FILE"

if [[ -n "${SUDO_USER:-}" ]]; then
  run_as_root chown "$SUDO_USER:$SUDO_USER" "$HOSTNAME_OUTPUT_FILE" || true
fi

log "Setup complete."
log "Onion hostname: $ONION_HOSTNAME"
log "Saved hostname to: $HOSTNAME_OUTPUT_FILE"
log "Connect with: torsocks ssh -p $SSH_PORT <username>@$ONION_HOSTNAME"
