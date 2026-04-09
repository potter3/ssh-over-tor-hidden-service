#!/usr/bin/env bash
set -euo pipefail

APP_NAME="$(basename "$0")"
STATE_FILE="/etc/ssh-over-tor-manager.env"
MANAGED_BEGIN="# BEGIN SSH_OVER_TOR_MANAGED"
MANAGED_END="# END SSH_OVER_TOR_MANAGED"

SSH_PORT_DEFAULT=22
ALLOW_PASSWORD_AUTH_DEFAULT="yes"
MAX_STARTUPS_DEFAULT=10
ENABLE_FAIL2BAN_DEFAULT="yes"
HIDDEN_SERVICE_DIR_DEFAULT="/var/lib/tor/ssh_service"
ONION_HOSTNAME_DEFAULT=""

SSH_PORT="$SSH_PORT_DEFAULT"
ALLOW_PASSWORD_AUTH="$ALLOW_PASSWORD_AUTH_DEFAULT"
MAX_STARTUPS="$MAX_STARTUPS_DEFAULT"
ENABLE_FAIL2BAN="$ENABLE_FAIL2BAN_DEFAULT"
HIDDEN_SERVICE_DIR="$HIDDEN_SERVICE_DIR_DEFAULT"
ONION_HOSTNAME="$ONION_HOSTNAME_DEFAULT"

SUDO=""
APT_UPDATED=0

log() {
  printf '[%s] %s\n' "$APP_NAME" "$*"
}

warn() {
  printf '[%s] WARNING: %s\n' "$APP_NAME" "$*" >&2
}

die() {
  printf '[%s] ERROR: %s\n' "$APP_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $APP_NAME [option]

Interactive Linux app for managing SSH over a Tor hidden service.

Options:
  --show       Show current saved/live configuration and exit
  --apply      Apply saved configuration immediately and exit
  --wizard     Run one guided setup flow and apply it
  --remove     Interactive remove menu (tor/fail2ban/both)
  -h, --help   Show this help
EOF
}

ensure_privilege_helper() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      die "Run as root or install sudo"
    fi
    SUDO="sudo"
  fi
}

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

require_supported_system() {
  command -v apt-get >/dev/null 2>&1 || die "apt-get is required (Debian/Ubuntu/Parrot/Kali)"
  command -v systemctl >/dev/null 2>&1 || die "systemctl is required"
}

is_valid_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] || return 1
  (( port >= 1 && port <= 65535 ))
}

is_valid_positive_int() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  (( value >= 1 ))
}

normalize_yes_no() {
  local input="${1,,}"
  case "$input" in
    y|yes|1|true|on) printf 'yes' ;;
    n|no|0|false|off) printf 'no' ;;
    *) return 1 ;;
  esac
}

validate_settings() {
  is_valid_port "$SSH_PORT" || die "Invalid SSH_PORT: $SSH_PORT"
  is_valid_positive_int "$MAX_STARTUPS" || die "Invalid MAX_STARTUPS: $MAX_STARTUPS"
  [[ "$ALLOW_PASSWORD_AUTH" == "yes" || "$ALLOW_PASSWORD_AUTH" == "no" ]] || die "Invalid ALLOW_PASSWORD_AUTH: $ALLOW_PASSWORD_AUTH"
  [[ "$ENABLE_FAIL2BAN" == "yes" || "$ENABLE_FAIL2BAN" == "no" ]] || die "Invalid ENABLE_FAIL2BAN: $ENABLE_FAIL2BAN"
  [[ -n "$HIDDEN_SERVICE_DIR" ]] || die "Hidden service directory cannot be empty"
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
      return 0
    fi
  done
  return 1
}

managed_block_present() {
  local file="$1"
  if run_as_root test -f "$file" && run_as_root awk -v marker="$MANAGED_BEGIN" '
      $0 == marker { found=1 }
      END { exit(found ? 0 : 1) }
    ' "$file"; then
    printf 'yes'
  else
    printf 'no'
  fi
}

load_state() {
  SSH_PORT="$SSH_PORT_DEFAULT"
  ALLOW_PASSWORD_AUTH="$ALLOW_PASSWORD_AUTH_DEFAULT"
  MAX_STARTUPS="$MAX_STARTUPS_DEFAULT"
  ENABLE_FAIL2BAN="$ENABLE_FAIL2BAN_DEFAULT"
  HIDDEN_SERVICE_DIR="$HIDDEN_SERVICE_DIR_DEFAULT"
  ONION_HOSTNAME="$ONION_HOSTNAME_DEFAULT"

  if ! run_as_root test -f "$STATE_FILE"; then
    return 0
  fi

  while IFS='=' read -r key value; do
    case "$key" in
      SSH_PORT) SSH_PORT="$value" ;;
      ALLOW_PASSWORD_AUTH) ALLOW_PASSWORD_AUTH="$value" ;;
      MAX_STARTUPS) MAX_STARTUPS="$value" ;;
      ENABLE_FAIL2BAN) ENABLE_FAIL2BAN="$value" ;;
      HIDDEN_SERVICE_DIR) HIDDEN_SERVICE_DIR="$value" ;;
      ONION_HOSTNAME) ONION_HOSTNAME="$value" ;;
      *) ;;
    esac
  done < <(
    run_as_root awk -F= '
      /^[[:space:]]*#/ { next }
      NF >= 2 {
        key=$1
        val=substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
        print key "=" val
      }
    ' "$STATE_FILE"
  )

  if ! is_valid_port "$SSH_PORT"; then
    warn "State SSH_PORT invalid; falling back to default $SSH_PORT_DEFAULT"
    SSH_PORT="$SSH_PORT_DEFAULT"
  fi
  if [[ "$ALLOW_PASSWORD_AUTH" != "yes" && "$ALLOW_PASSWORD_AUTH" != "no" ]]; then
    warn "State ALLOW_PASSWORD_AUTH invalid; falling back to default $ALLOW_PASSWORD_AUTH_DEFAULT"
    ALLOW_PASSWORD_AUTH="$ALLOW_PASSWORD_AUTH_DEFAULT"
  fi
  if ! is_valid_positive_int "$MAX_STARTUPS"; then
    warn "State MAX_STARTUPS invalid; falling back to default $MAX_STARTUPS_DEFAULT"
    MAX_STARTUPS="$MAX_STARTUPS_DEFAULT"
  fi
  if [[ "$ENABLE_FAIL2BAN" != "yes" && "$ENABLE_FAIL2BAN" != "no" ]]; then
    warn "State ENABLE_FAIL2BAN invalid; falling back to default $ENABLE_FAIL2BAN_DEFAULT"
    ENABLE_FAIL2BAN="$ENABLE_FAIL2BAN_DEFAULT"
  fi
  if [[ -z "$HIDDEN_SERVICE_DIR" ]]; then
    warn "State HIDDEN_SERVICE_DIR empty; falling back to default $HIDDEN_SERVICE_DIR_DEFAULT"
    HIDDEN_SERVICE_DIR="$HIDDEN_SERVICE_DIR_DEFAULT"
  fi
}

save_state() {
  local tmp
  tmp="$(mktemp)"
  cat > "$tmp" <<EOF
# Managed by $APP_NAME
SSH_PORT=$SSH_PORT
ALLOW_PASSWORD_AUTH=$ALLOW_PASSWORD_AUTH
MAX_STARTUPS=$MAX_STARTUPS
ENABLE_FAIL2BAN=$ENABLE_FAIL2BAN
HIDDEN_SERVICE_DIR=$HIDDEN_SERVICE_DIR
ONION_HOSTNAME=$ONION_HOSTNAME
LAST_UPDATED_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  run_as_root install -m 0600 "$tmp" "$STATE_FILE"
  rm -f "$tmp"
}

read_onion_hostname() {
  local hostname_path
  hostname_path="${HIDDEN_SERVICE_DIR%/}/hostname"

  if run_as_root test -s "$hostname_path"; then
    ONION_HOSTNAME="$(run_as_root cat "$hostname_path" | tr -d '\r\n')"
  fi
}

install_dependencies() {
  log "Ensuring dependencies are installed..."
  ensure_core_dependencies
  ensure_optional_fail2ban
  install_if_missing "micro" "micro"
  install_if_missing "pv" "pv"
}

install_if_missing() {
  local package="$1"
  local bin_check="${2:-}"
  local dpkg_ok=1
  local need_install=0

  if run_as_root dpkg -s "$package" >/dev/null 2>&1; then
    dpkg_ok=0
  fi

  if [[ "$dpkg_ok" -ne 0 ]]; then
    need_install=1
  fi

  if [[ -n "$bin_check" ]] && ! command -v "$bin_check" >/dev/null 2>&1; then
    need_install=1
  fi

  if [[ "$need_install" -eq 0 ]]; then
    return 0
  fi

  log "Installing missing package: $package"
  apt_update_once
  run_as_root apt-get install -y "$package"
}

ensure_core_dependencies() {
  install_if_missing "openssh-server" "sshd"
  install_if_missing "tor" "tor"
  install_if_missing "torsocks" "torsocks"
}

ensure_optional_fail2ban() {
  if [[ "$ENABLE_FAIL2BAN" == "yes" ]]; then
    install_if_missing "fail2ban" "fail2ban-client"
  fi
}

create_backups() {
  backup_if_missing "/etc/ssh/sshd_config" "/etc/ssh/sshd_config.bak"
  backup_if_missing "/etc/tor/torrc" "/etc/tor/torrc.bak"
  backup_if_missing "/etc/fail2ban/jail.conf" "/etc/fail2ban/jail.conf.bak"
}

prepare_tor_dir() {
  local tor_user
  run_as_root mkdir -p "$HIDDEN_SERVICE_DIR"
  run_as_root chmod 0700 "$HIDDEN_SERVICE_DIR"

  for tor_user in debian-tor toranon tor; do
    if id -u "$tor_user" >/dev/null 2>&1; then
      run_as_root chown -R "$tor_user:$tor_user" "$HIDDEN_SERVICE_DIR"
      return 0
    fi
  done

  warn "Tor user not found (debian-tor/toranon/tor); continuing without chown"
}

apply_configuration() {
  local install_if_needed="${1:-no}"
  local ssh_service tor_service ssh_block tor_block fail2ban_enabled

  validate_settings

  if [[ "$install_if_needed" == "yes" ]]; then
    install_dependencies
  else
    ensure_core_dependencies
    ensure_optional_fail2ban
  fi
  create_backups

  ssh_block=$(
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
MaxStartups $MAX_STARTUPS
MaxSessions $MAX_STARTUPS
EOF
  )
  write_managed_block "/etc/ssh/sshd_config" "$MANAGED_BEGIN" "$MANAGED_END" "$ssh_block"
  run_as_root sshd -t
  ssh_service="$(restart_service_any ssh sshd)" || die "Failed to restart ssh/sshd service"
  enable_service_any "$ssh_service" || true

  prepare_tor_dir
  tor_block=$(
    cat <<EOF
# SSH Hidden Service
HiddenServiceDir ${HIDDEN_SERVICE_DIR%/}/
HiddenServicePort $SSH_PORT 127.0.0.1:$SSH_PORT
EOF
  )
  write_managed_block "/etc/tor/torrc" "$MANAGED_BEGIN" "$MANAGED_END" "$tor_block"
  tor_service="$(restart_service_any tor tor@default)" || die "Failed to restart tor service"
  enable_service_any "$tor_service" || true

  if [[ "$ENABLE_FAIL2BAN" == "yes" ]]; then
    fail2ban_enabled="true"
  else
    fail2ban_enabled="false"
  fi

  write_managed_block \
    "/etc/fail2ban/jail.local" \
    "$MANAGED_BEGIN" \
    "$MANAGED_END" \
    "[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 3

[sshd]
enabled  = $fail2ban_enabled
port     = $SSH_PORT
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 3"

  run_as_root systemctl enable --now fail2ban >/dev/null 2>&1 || true
  run_as_root systemctl restart fail2ban >/dev/null 2>&1 || true

  log "Waiting for onion hostname..."
  ONION_HOSTNAME=""
  for _ in $(seq 1 30); do
    read_onion_hostname
    if [[ -n "$ONION_HOSTNAME" ]]; then
      break
    fi
    sleep 1
  done

  if [[ -z "$ONION_HOSTNAME" ]]; then
    warn "Onion hostname not available yet in ${HIDDEN_SERVICE_DIR%/}/hostname"
  fi

  save_state
}

remove_managed_block() {
  local file="$1"
  local cleaned_tmp final_tmp
  local parent_dir

  parent_dir="$(dirname "$file")"
  if ! run_as_root test -d "$parent_dir"; then
    return 0
  fi

  cleaned_tmp="$(mktemp)"
  final_tmp="$(mktemp)"

  run_as_root touch "$file"
  run_as_root awk -v begin="$MANAGED_BEGIN" -v end="$MANAGED_END" '
    $0 == begin { in_block=1; next }
    $0 == end   { in_block=0; next }
    !in_block   { print }
  ' "$file" > "$cleaned_tmp"

  cat "$cleaned_tmp" > "$final_tmp"
  run_as_root install -m 0644 "$final_tmp" "$file"
  rm -f "$cleaned_tmp" "$final_tmp"
}

remove_fail2ban() {
  log "Removing Fail2Ban package and managed config..."
  run_as_root systemctl stop fail2ban >/dev/null 2>&1 || true
  run_as_root systemctl disable fail2ban >/dev/null 2>&1 || true
  if run_as_root test -d "/etc/fail2ban"; then
    remove_managed_block "/etc/fail2ban/jail.local"
  fi
  run_as_root apt-get remove -y fail2ban >/dev/null 2>&1 || true
  run_as_root apt-get autoremove -y >/dev/null 2>&1 || true
  ENABLE_FAIL2BAN="no"
  log "Fail2Ban removed."
}

remove_tor() {
  log "Removing Tor package and managed config..."
  run_as_root systemctl stop tor >/dev/null 2>&1 || true
  run_as_root systemctl stop tor@default >/dev/null 2>&1 || true
  run_as_root systemctl disable tor >/dev/null 2>&1 || true
  run_as_root systemctl disable tor@default >/dev/null 2>&1 || true
  if run_as_root test -d "/etc/tor"; then
    remove_managed_block "/etc/tor/torrc"
  fi
  run_as_root apt-get remove -y tor torsocks >/dev/null 2>&1 || true
  run_as_root apt-get autoremove -y >/dev/null 2>&1 || true
  ONION_HOSTNAME=""
  log "Tor removed."
}

remove_components_menu() {
  local choice
  load_state
  printf '\n=== Remove Components ===\n'
  printf '1) Remove Fail2Ban only\n'
  printf '2) Remove Tor only\n'
  printf '3) Remove both Fail2Ban and Tor\n'
  printf '4) Cancel\n'
  read -r -p "Choose [1-4]: " choice

  case "$choice" in
    1)
      remove_fail2ban
      ;;
    2)
      remove_tor
      ;;
    3)
      remove_fail2ban
      remove_tor
      ;;
    4)
      log "Remove operation cancelled."
      return 0
      ;;
    *)
      warn "Invalid remove option."
      return 1
      ;;
  esac

  save_state
  print_current_info
}

print_current_info() {
  local ssh_managed tor_managed fail2ban_managed
  load_state
  read_onion_hostname

  ssh_managed="$(managed_block_present /etc/ssh/sshd_config)"
  tor_managed="$(managed_block_present /etc/tor/torrc)"
  fail2ban_managed="$(managed_block_present /etc/fail2ban/jail.local)"

  printf '\n=== SSH over Tor Manager: Current Information ===\n'
  printf 'State file:              %s\n' "$STATE_FILE"
  printf 'SSH custom port:         %s\n' "$SSH_PORT"
  printf 'Password auth:           %s\n' "$ALLOW_PASSWORD_AUTH"
  printf 'Connection limit:        %s (MaxStartups / MaxSessions)\n' "$MAX_STARTUPS"
  printf 'Fail2Ban enabled:        %s\n' "$ENABLE_FAIL2BAN"
  printf 'Hidden service dir:      %s\n' "$HIDDEN_SERVICE_DIR"
  printf 'Managed SSH config:      %s\n' "$ssh_managed"
  printf 'Managed Tor config:      %s\n' "$tor_managed"
  printf 'Managed Fail2Ban config: %s\n' "$fail2ban_managed"

  if [[ -n "$ONION_HOSTNAME" ]]; then
    printf 'Onion hostname:          %s\n' "$ONION_HOSTNAME"
    printf 'Connect command:         torsocks ssh -p %s <username>@%s\n' "$SSH_PORT" "$ONION_HOSTNAME"
  else
    printf 'Onion hostname:          (not available yet)\n'
  fi
  printf '\n'
}

prompt_port() {
  local input
  while true; do
    read -r -p "Custom SSH port [$SSH_PORT]: " input
    input="${input:-$SSH_PORT}"
    if is_valid_port "$input"; then
      SSH_PORT="$input"
      return 0
    fi
    warn "Port must be a number between 1 and 65535."
  done
}

prompt_connection_limit() {
  local input
  while true; do
    read -r -p "Connection limit (MaxStartups/MaxSessions) [$MAX_STARTUPS]: " input
    input="${input:-$MAX_STARTUPS}"
    if is_valid_positive_int "$input"; then
      MAX_STARTUPS="$input"
      return 0
    fi
    warn "Connection limit must be a positive integer."
  done
}

prompt_yes_no_value() {
  local question="$1"
  local current="$2"
  local answer normalized

  while true; do
    read -r -p "$question [$current]: " answer
    answer="${answer:-$current}"
    if normalized="$(normalize_yes_no "$answer")"; then
      printf '%s' "$normalized"
      return 0
    fi
    warn "Please answer yes or no."
  done
}

prompt_hidden_service_dir() {
  local input
  read -r -p "Hidden service directory [$HIDDEN_SERVICE_DIR]: " input
  if [[ -n "$input" ]]; then
    HIDDEN_SERVICE_DIR="$input"
  fi
}

run_wizard() {
  load_state
  printf '\n--- Guided Setup ---\n'
  prompt_port
  ALLOW_PASSWORD_AUTH="$(prompt_yes_no_value "Enable password authentication? (yes/no)" "$ALLOW_PASSWORD_AUTH")"
  prompt_connection_limit
  ENABLE_FAIL2BAN="$(prompt_yes_no_value "Enable Fail2Ban? (yes/no)" "$ENABLE_FAIL2BAN")"
  prompt_hidden_service_dir
  apply_configuration "yes"
  print_current_info
}

change_port_now() {
  load_state
  prompt_port
  apply_configuration "no"
  print_current_info
}

toggle_password_auth_now() {
  load_state
  ALLOW_PASSWORD_AUTH="$(prompt_yes_no_value "Enable password authentication? (yes/no)" "$ALLOW_PASSWORD_AUTH")"
  apply_configuration "no"
  print_current_info
}

change_connection_limit_now() {
  load_state
  prompt_connection_limit
  apply_configuration "no"
  print_current_info
}

toggle_fail2ban_now() {
  load_state
  ENABLE_FAIL2BAN="$(prompt_yes_no_value "Enable Fail2Ban? (yes/no)" "$ENABLE_FAIL2BAN")"
  apply_configuration "no"
  print_current_info
}

reapply_saved_configuration() {
  load_state
  apply_configuration "no"
  print_current_info
}

interactive_menu() {
  while true; do
    printf '\n=== SSH over Tor Manager ===\n'
    printf '1) Guided setup (install + configure)\n'
    printf '2) Change SSH custom port (apply immediately)\n'
    printf '3) Change password-auth setting (apply immediately)\n'
    printf '4) Change connection limit (apply immediately)\n'
    printf '5) Toggle Fail2Ban (apply immediately)\n'
    printf '6) Show current information\n'
    printf '7) Re-apply saved configuration\n'
    printf '8) Remove tor/fail2ban components\n'
    printf '9) Exit\n'

    read -r -p "Choose [1-9]: " choice
    case "$choice" in
      1) run_wizard ;;
      2) change_port_now ;;
      3) toggle_password_auth_now ;;
      4) change_connection_limit_now ;;
      5) toggle_fail2ban_now ;;
      6) print_current_info ;;
      7) reapply_saved_configuration ;;
      8) remove_components_menu ;;
      9) break ;;
      *) warn "Invalid option. Choose a number from 1 to 9." ;;
    esac
  done
}

main() {
  ensure_privilege_helper
  require_supported_system

  case "${1:-}" in
    --show)
      print_current_info
      ;;
    --apply)
      reapply_saved_configuration
      ;;
    --wizard)
      run_wizard
      ;;
    --remove)
      remove_components_menu
      ;;
    -h|--help)
      usage
      ;;
    "")
      interactive_menu
      ;;
    *)
      usage
      die "Unknown option: $1"
      ;;
  esac
}

main "$@"
