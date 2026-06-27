#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPOSITORY="tts-empire/spawnwp"
MARKER="/var/lib/spawnwp/installed"
REPORT="/root/spawnwp-credentials.txt"
FORCE=0
NON_INTERACTIVE=${NON_INTERACTIVE:-0}
for arg in "$@"; do
  case "$arg" in --force) FORCE=1 ;; --non-interactive) NON_INTERACTIVE=1 ;; *) echo "Unknown option: $arg" >&2; exit 2 ;; esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
random_secret() { openssl rand -base64 36 | tr -d '/+=' | head -c "${1:-32}"; }
prompt() {
  local variable=$1 label=$2 default=${3:-} value
  value=$(printenv "$variable" 2>/dev/null || true)
  if [ -z "$value" ] && [ "$NON_INTERACTIVE" != 1 ]; then
    [ -r /dev/tty ] || die "$variable is required in non-interactive mode"
    read -r -p "$label${default:+ [$default]}: " value </dev/tty
  fi
  value=${value:-$default}
  printf -v "$variable" '%s' "$value"
}
confirm() {
  local variable=$1 label=$2 default=$3 value
  value=$(printenv "$variable" 2>/dev/null || true)
  if [ -z "$value" ] && [ "$NON_INTERACTIVE" != 1 ]; then
    local suffix='[y/N]'; [ "$default" = 1 ] && suffix='[Y/n]'
    read -r -p "$label $suffix " value </dev/tty
    if [ "$default" = 1 ]; then [[ ! "$value" =~ ^[Nn]$ ]] && value=1 || value=0
    else [[ "$value" =~ ^[Yy]$ ]] && value=1 || value=0; fi
  fi
  value=${value:-$default}
  [[ "$value" =~ ^[01]$ ]] || die "$variable must be 1 or 0"
  printf -v "$variable" '%s' "$value"
}
validate_domain() { [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }
render() {
  sed -e "s|@@DOMAIN@@|$DOMAIN|g" -e "s|@@COCKPIT_DOMAIN@@|$COCKPIT_DOMAIN|g" "$1" > "$2"
}

[ "$(id -u)" = 0 ] || die "Run the installer as root (sudo bash)."
[ ! -e "$MARKER" ] || [ "$FORCE" = 1 ] || die "SpawnWP is already installed. Use --force only for a destructive reinstall."
. /etc/os-release
[[ "$ID" =~ ^(ubuntu|debian)$ ]] || die "Supported OS: Ubuntu or Debian"
case "$ID:$VERSION_ID" in ubuntu:22.04|ubuntu:24.04|debian:12|debian:13) ;; *) die "Unsupported release: $PRETTY_NAME" ;; esac
[[ "$(dpkg --print-architecture)" =~ ^(amd64|arm64)$ ]] || die "Supported architectures: amd64 or arm64"

prompt DOMAIN "WordPress sites hostname"
prompt COCKPIT_DOMAIN "Cockpit hostname"
prompt EMAIL "Let's Encrypt email"
prompt BASIC_AUTH_USER "HTTP Basic Auth username" "admin"
validate_domain "$DOMAIN" || die "Invalid DOMAIN"
validate_domain "$COCKPIT_DOMAIN" || die "Invalid COCKPIT_DOMAIN"
[ "$DOMAIN" != "$COCKPIT_DOMAIN" ] || die "DOMAIN and COCKPIT_DOMAIN must differ"
[[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "Invalid EMAIL"
[[ "$BASIC_AUTH_USER" =~ ^[A-Za-z0-9_.-]{1,32}$ ]] || die "Invalid BASIC_AUTH_USER"
confirm ENABLE_PORT_KNOCKING "Enable port-knocking? Strongly recommended." 1
echo "Anonymous telemetry is optional, expires after 90 days, and never includes domains, IPs, email, usernames, site names, content or logs."
echo "Privacy notice: https://spawnwp.com/privacy/telemetry"
confirm ENABLE_TELEMETRY "Share anonymous usage statistics for 90 days?" 0

for host in "$DOMAIN" "$COCKPIT_DOMAIN"; do
  getent ahosts "$host" >/dev/null || die "$host does not resolve yet. Configure DNS before installing."
done

if [ "$FORCE" = 1 ]; then
  log "Removing the previous SpawnWP control plane"
  [ ! -f /srv/wp-dev/compose.yaml ] || (cd /srv/wp-dev && docker compose down -v --remove-orphans) || true
  systemctl disable --now wp-cockpit cockpit-reaper.timer spawnwp-telemetry.timer 2>/dev/null || true
  rm -rf /srv/wp-dev /srv/wp-cockpit /etc/spawnwp /var/lib/spawnwp /opt/spawnwp
fi

log "Installing host prerequisites"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y ca-certificates curl gnupg jq openssl nginx certbot python3-certbot-nginx apache2-utils \
  python3 python3-venv python3-pip rsync unzip git cron iproute2
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker nginx

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
if [ -n "${SPAWNWP_SOURCE_DIR:-}" ]; then
  SOURCE=$(realpath "$SPAWNWP_SOURCE_DIR")
  [ -f "$SOURCE/VERSION" ] || die "Invalid SPAWNWP_SOURCE_DIR"
  VERSION=$(<"$SOURCE/VERSION")
  mode=source
else
  log "Downloading the latest signed SpawnWP release"
  auth=(); [ -z "${GH_TOKEN:-}" ] || auth=(-H "Authorization: Bearer $GH_TOKEN")
  release=$(curl -fsSL "${auth[@]}" -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPOSITORY/releases/latest")
  VERSION=$(jq -r '.tag_name' <<<"$release" | sed 's/^v//')
  prefix="spawnwp-$VERSION"
  for suffix in tar.gz manifest.json manifest.sig; do
    url=$(jq -r --arg name "$prefix.$suffix" '.assets[] | select(.name==$name) | .url' <<<"$release")
    [ -n "$url" ] || die "Release asset missing: $prefix.$suffix"
    curl -fsSL "${auth[@]}" -H 'Accept: application/octet-stream' "$url" -o "$WORK/$prefix.$suffix"
  done
  cat > "$WORK/release-public.pem" <<'KEY'
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEACJc49JSWzK2xu9v0L81n5wyeKAAs9vBZJZmBtbgJrrE=
-----END PUBLIC KEY-----
KEY
  openssl pkeyutl -verify -pubin -inkey "$WORK/release-public.pem" -rawin \
    -in "$WORK/$prefix.manifest.json" -sigfile "$WORK/$prefix.manifest.sig" >/dev/null || die "Release signature invalid"
  expected=$(jq -r .archive_sha256 "$WORK/$prefix.manifest.json")
  [ "$(sha256sum "$WORK/$prefix.tar.gz" | cut -d' ' -f1)" = "$expected" ] || die "Release checksum invalid"
  tar xzf "$WORK/$prefix.tar.gz" -C "$WORK"
  SOURCE="$WORK/$prefix"
  mode=package
fi

src() {
  local group=$1 path=$2
  if [ "$mode" = source ]; then
    case "$group" in cockpit) printf '%s/runtime/%s' "$SOURCE" "$path" ;; runtime) printf '%s/runtime/%s' "$SOURCE" "$path" ;; installer) printf '%s/installer/%s' "$SOURCE" "$path" ;; bin) printf '%s/updater/%s' "$SOURCE" "$path" ;; esac
  else
    case "$group" in cockpit) printf '%s/payload/cockpit/%s' "$SOURCE" "$path" ;; runtime) printf '%s/payload/runtime/%s' "$SOURCE" "$path" ;; installer) printf '%s/payload/lib/installer/%s' "$SOURCE" "$path" ;; bin) printf '%s/payload/%s' "$SOURCE" "$path" ;; esac
  fi
}

log "Installing SpawnWP $VERSION"
install -d -m 0755 /srv/wp-dev /srv/wp-cockpit/static/assets /usr/local/lib/spawnwp/installer /etc/spawnwp /var/lib/spawnwp /opt/spawnwp/releases
if [ "$mode" = source ]; then
  rsync -a --exclude primary.env "$(src runtime .)/" /srv/wp-dev/
  install -m 0644 "$(src cockpit app.py)" "$(src cockpit auth.py)" "$(src cockpit requirements.txt)" /srv/wp-cockpit/
  install -m 0644 "$SOURCE/runtime/manage.html" "$SOURCE/runtime/deploy.html" "$SOURCE/runtime/updates.html" /srv/wp-cockpit/static/
  install -m 0644 "$SOURCE/runtime/assets/"* /srv/wp-cockpit/static/assets/
  install -m 0755 "$SOURCE/updater/spawnwp" /usr/local/bin/spawnwp
  install -m 0644 "$SOURCE/updater/release-public.pem" /usr/local/lib/spawnwp/release-public.pem
else
  rsync -a "$SOURCE/payload/runtime/" /srv/wp-dev/
  rsync -a "$SOURCE/payload/cockpit/" /srv/wp-cockpit/
  install -m 0755 "$SOURCE/payload/bin/spawnwp" /usr/local/bin/spawnwp
  install -m 0644 "$SOURCE/payload/lib/release-public.pem" /usr/local/lib/spawnwp/release-public.pem
fi
rsync -a "$(src installer .)/" /usr/local/lib/spawnwp/installer/
if [ "$mode" = package ]; then
  rm -rf "/opt/spawnwp/releases/$VERSION"
  cp -a "$SOURCE" "/opt/spawnwp/releases/$VERSION"
  ln -sfn "/opt/spawnwp/releases/$VERSION" /opt/spawnwp/current
fi
echo "$VERSION" > /var/lib/spawnwp/VERSION

python3 -m venv /srv/wp-cockpit/venv
/srv/wp-cockpit/venv/bin/pip install --disable-pip-version-check -q -r /srv/wp-cockpit/requirements.txt

BASIC_AUTH_PASSWORD=${BASIC_AUTH_PASSWORD:-$(random_secret 28)}
DB_PASS=$(random_secret 32); DB_ROOT_PASS=$(random_secret 32); WP_ADMIN_PASS=$(random_secret 32); REDIS_PASSWORD=$(random_secret 32)
WP_ADMIN_USER="spawnwp-$(openssl rand -hex 3)"
cat > /etc/spawnwp/config.env <<EOF
DOMAIN=$DOMAIN
COCKPIT_DOMAIN=$COCKPIT_DOMAIN
EMAIL=$EMAIL
ENABLE_PORT_KNOCKING=$ENABLE_PORT_KNOCKING
ENABLE_TELEMETRY=$ENABLE_TELEMETRY
EOF
chmod 600 /etc/spawnwp/config.env
cat > /srv/wp-dev/.env <<EOF
COMPOSE_PROJECT_NAME=wp-dev
DOMAIN=$DOMAIN
PHP_VERSION=8.3
WP_VERSION=latest
WORDPRESS_SERIES=7
WP_DEBUG=true
SPAWNWP_BLUEPRINT=development
SPAWNWP_BLUEPRINT_VERSION=1.0.0
DB_NAME=wordpress
DB_USER=wpuser
DB_PASS=$DB_PASS
DB_ROOT_PASS=$DB_ROOT_PASS
DB_TABLE_PREFIX=wp_
WP_ADMIN_USER=$WP_ADMIN_USER
WP_ADMIN_EMAIL=$EMAIL
WP_ADMIN_PASS=$WP_ADMIN_PASS
WP_HOME=https://$DOMAIN
WP_SITEURL=https://$DOMAIN
WEB_PORT=8080
ADMINER_PORT=9001
MAILPIT_PORT=8025
MAILPIT_WEBROOT=/wp-dev-mail
REDIS_PASSWORD=$REDIS_PASSWORD
EOF
chmod 600 /srv/wp-dev/.env
mkdir -p /srv/wp-dev/projects/primary/wp-content/plugins /srv/wp-dev/backups/{db,files} /srv/wp-dev/.spawnwp
python3 /srv/wp-dev/scripts/blueprint.py resolve development --output /srv/wp-dev/.spawnwp/blueprint.json >/dev/null
chown -R 33:33 /srv/wp-dev/projects/primary/wp-content

log "Configuring TLS and nginx"
install -d -m 0755 /var/www/letsencrypt /etc/nginx/snippets
install -m 0644 "$(src installer spawnwp-proxy.conf)" /etc/nginx/snippets/spawnwp-proxy.conf
htpasswd -bBc /etc/nginx/.spawnwp-htpasswd "$BASIC_AUTH_USER" "$BASIC_AUTH_PASSWORD" >/dev/null
render "$(src installer nginx-http.conf.tpl)" /etc/nginx/sites-available/spawnwp
ln -sfn /etc/nginx/sites-available/spawnwp /etc/nginx/sites-enabled/spawnwp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/letsencrypt --non-interactive --agree-tos \
  --email "$EMAIL" --cert-name "$DOMAIN" -d "$DOMAIN" -d "$COCKPIT_DOMAIN"

KNOCK_OPEN=""
if [ "$ENABLE_PORT_KNOCKING" = 1 ]; then
  apt-get install -y knockd
  mapfile -t KNOCK_PORTS < <(shuf -i 20000-60000 -n 3)
  KNOCK_OPEN="${KNOCK_PORTS[*]}"
  install -m 0755 "$(src installer knock-session)" /usr/local/lib/spawnwp/knock-session
  cat > /etc/knockd.conf <<EOF
[options]
    UseSyslog
[openSpawnWP]
    sequence = ${KNOCK_PORTS[0]},${KNOCK_PORTS[1]},${KNOCK_PORTS[2]}
    seq_timeout = 10
    command = /usr/local/lib/spawnwp/knock-session open %IP%
    tcpflags = syn
[closeSpawnWP]
    sequence = ${KNOCK_PORTS[2]},${KNOCK_PORTS[1]},${KNOCK_PORTS[0]}
    seq_timeout = 10
    command = /usr/local/lib/spawnwp/knock-session close %IP%
    tcpflags = syn
EOF
  sed -i 's/^START_KNOCKD=.*/START_KNOCKD=1/' /etc/default/knockd 2>/dev/null || echo 'START_KNOCKD=1' > /etc/default/knockd
  interface=$(ip route show default | awk 'NR==1 {print $5}')
  printf 'START_KNOCKD=1\nKNOCKD_OPTS="-i %s"\n' "$interface" > /etc/default/knockd
  echo 'deny all;' > /etc/nginx/cockpit-allowed.conf
  install -m 0644 "$(src installer cockpit-reaper.service)" "$(src installer cockpit-reaper.timer)" /etc/systemd/system/
else
  echo 'allow all;' > /etc/nginx/cockpit-allowed.conf
fi
render "$(src installer nginx.conf.tpl)" /etc/nginx/sites-available/spawnwp
nginx -t && systemctl reload nginx

log "Initializing cockpit authentication"
FernetKey=$(/srv/wp-cockpit/venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
printf '%s\n' "$FernetKey" > /etc/spawnwp/auth.key
chmod 600 /etc/spawnwp/auth.key
APP_SETUP_CODE=$(cd /srv/wp-cockpit && /srv/wp-cockpit/venv/bin/python -c 'from auth import create_bootstrap; print(create_bootstrap())')
install -m 0644 "$(src installer wp-cockpit.service)" /etc/systemd/system/wp-cockpit.service
install -m 0644 "$(src installer docker-prune.service)" "$(src installer docker-prune.timer)" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wp-cockpit docker-prune.timer
[ "$ENABLE_PORT_KNOCKING" != 1 ] || systemctl enable --now knockd cockpit-reaper.timer

log "Starting the primary WordPress development environment"
(cd /srv/wp-dev && docker compose build --pull php && docker compose up -d php)
for _ in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' wp-dev-php-1 2>/dev/null || true)" = healthy ] && break
  sleep 3
done
(cd /srv/wp-dev && docker compose up -d && make bootstrap && bash scripts/apply-blueprint.sh .spawnwp/blueprint.json)

if [ "$ENABLE_TELEMETRY" = 1 ]; then
  install -d -m 0700 /var/lib/spawnwp/telemetry
  install -m 0755 "$(src installer telemetry.py)" /usr/local/lib/spawnwp/telemetry.py
  install -m 0644 "$(src installer spawnwp-telemetry.service)" "$(src installer spawnwp-telemetry.timer)" /etc/systemd/system/
  uuid=$(cat /proc/sys/kernel/random/uuid); echo "$uuid" > /var/lib/spawnwp/telemetry/installation-id
  now=$(date +%s); printf '{"enabled":true,"notice_version":"1","consented_at":%s,"expires_at":%s}\n' "$now" "$((now+7776000))" > /var/lib/spawnwp/telemetry/consent.json
  printf '{"port_knocking":%s,"telemetry":true}\n' "$([ "$ENABLE_PORT_KNOCKING" = 1 ] && echo true || echo false)" > /var/lib/spawnwp/features.json
  systemctl daemon-reload; systemctl enable --now spawnwp-telemetry.timer
  /usr/local/lib/spawnwp/telemetry.py send installation || true
else
  printf '{"port_knocking":%s,"telemetry":false}\n' "$([ "$ENABLE_PORT_KNOCKING" = 1 ] && echo true || echo false)" > /var/lib/spawnwp/features.json
fi

touch "$MARKER"
cat > "$REPORT" <<EOF
SpawnWP $VERSION - installation complete

Sites: https://$DOMAIN/
Cockpit: https://$COCKPIT_DOMAIN/

HTTP Basic Auth
  user: $BASIC_AUTH_USER
  pass: $BASIC_AUTH_PASSWORD

Application setup code (expires in 24h): $APP_SETUP_CODE

WordPress admin (primary environment)
  URL: https://$DOMAIN/wp-admin/
  user: $WP_ADMIN_USER
  pass: $WP_ADMIN_PASS

Port-knocking: $([ "$ENABLE_PORT_KNOCKING" = 1 ] && echo enabled || echo disabled)
EOF
if [ "$ENABLE_PORT_KNOCKING" = 1 ]; then
  cat >> "$REPORT" <<EOF
  open sequence: $KNOCK_OPEN
  command: ./clients/knock.sh $COCKPIT_DOMAIN $KNOCK_OPEN
EOF
else
  cat >> "$REPORT" <<EOF
  WARNING: the Basic Auth endpoint is publicly reachable. Application login remains mandatory.
EOF
fi
chmod 600 "$REPORT"
cat "$REPORT"
echo "Next: knock if enabled, open the cockpit, pass Basic Auth, then complete passkey + TOTP setup."
