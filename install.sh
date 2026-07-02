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
  value=${value//$'\r'/}
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
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
validate_domain() {
  local domain=${1%.} label tld
  local -a labels
  [ -n "$domain" ] && [ "${#domain}" -le 253 ] && [[ "$domain" == *.* ]] || return 1
  IFS=. read -r -a labels <<< "$domain"
  [ "${#labels[@]}" -ge 2 ] || return 1
  for label in "${labels[@]}"; do
    [ -n "$label" ] && [ "${#label}" -le 63 ] || return 1
    [[ "$label" != -* && "$label" != *- && "$label" != *[!A-Za-z0-9-]* ]] || return 1
  done
  tld=${labels[${#labels[@]}-1]}
  [ "${#tld}" -ge 2 ] && [[ "$tld" != *[!A-Za-z]* ]]
}
render() {
  sed -e "s|@@DOMAIN@@|$DOMAIN|g" -e "s|@@COCKPIT_DOMAIN@@|$COCKPIT_DOMAIN|g" "$1" > "$2"
}

[ "$(id -u)" = 0 ] || die "Run the installer as root (sudo bash)."
. /etc/os-release
[[ "$ID" =~ ^(ubuntu|debian)$ ]] || die "Supported OS: Ubuntu or Debian"
case "$ID:$VERSION_ID" in ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;; *) die "Unsupported release: $PRETTY_NAME" ;; esac
[[ "$(dpkg --print-architecture)" =~ ^(amd64|arm64)$ ]] || die "Supported architectures: amd64 or arm64"

cleanup_previous_install() {
  if [ -f /srv/wp-dev/compose.yaml ] || [ -d /srv/wp-cockpit ] || [ -d /etc/spawnwp ] || [ -d /var/lib/spawnwp ] || [ -d /opt/spawnwp ]; then
    log "Resetting any previous SpawnWP footprint"
    [ ! -f /srv/wp-dev/.env ] || (cd /srv/wp-dev && docker compose down -v --remove-orphans) || true
    systemctl disable --now wp-cockpit spawnwp-telemetry.timer 2>/dev/null || true
    rm -rf /srv/wp-dev /srv/wp-cockpit /etc/spawnwp /var/lib/spawnwp /opt/spawnwp
  fi
}

cleanup_previous_install

prompt DOMAIN "WordPress sites hostname"
prompt COCKPIT_DOMAIN "Cockpit hostname"
prompt EMAIL "Let's Encrypt email"
validate_domain "$DOMAIN" || die "Invalid DOMAIN: '$DOMAIN'"
validate_domain "$COCKPIT_DOMAIN" || die "Invalid COCKPIT_DOMAIN: '$COCKPIT_DOMAIN'"
[ "$DOMAIN" != "$COCKPIT_DOMAIN" ] || die "DOMAIN and COCKPIT_DOMAIN must differ"
[[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "Invalid EMAIL"
echo "Anonymous telemetry is optional, expires after 90 days, and never includes domains, IPs, email, usernames, site names, content or logs."
echo "Privacy notice: https://spawnwp.com/privacy/telemetry"
confirm ENABLE_TELEMETRY "Share anonymous usage statistics for 90 days?" 0

for host in "$DOMAIN" "$COCKPIT_DOMAIN"; do
  getent ahosts "$host" >/dev/null || die "$host does not resolve yet. Configure DNS before installing."
done

log "Installing host prerequisites"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y ca-certificates curl gnupg jq openssl nginx certbot python3-certbot-nginx \
  python3 python3-venv python3-pip rsync unzip git cron iproute2
ensure_certbot_nginx_defaults() {
  [ -f /etc/letsencrypt/options-ssl-nginx.conf ] || install -D -m 0644 \
    /usr/lib/python3/dist-packages/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
    /etc/letsencrypt/options-ssl-nginx.conf
  [ -f /etc/letsencrypt/ssl-dhparams.pem ] || install -D -m 0644 \
    /usr/lib/python3/dist-packages/certbot/ssl-dhparams.pem \
    /etc/letsencrypt/ssl-dhparams.pem
}
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
ensure_certbot_nginx_defaults
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
install -d -m 0700 /var/lib/spawnwp/docker
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

cat > /etc/spawnwp/config.env <<EOF
DOMAIN=$DOMAIN
COCKPIT_DOMAIN=$COCKPIT_DOMAIN
EMAIL=$EMAIL
ENABLE_TELEMETRY=$ENABLE_TELEMETRY
EOF
chmod 600 /etc/spawnwp/config.env
mkdir -p /srv/wp-dev/.spawnwp
touch /srv/wp-dev/.spawnwp/template-only

log "Configuring TLS and nginx"
install -d -m 0755 /var/www/letsencrypt /etc/nginx/snippets
install -m 0644 "$(src installer spawnwp-proxy.conf)" /etc/nginx/snippets/spawnwp-proxy.conf
render "$(src installer nginx-http.conf.tpl)" /etc/nginx/sites-available/spawnwp
ln -sfn /etc/nginx/sites-available/spawnwp /etc/nginx/sites-enabled/spawnwp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/letsencrypt --non-interactive --agree-tos \
  --email "$EMAIL" --cert-name "$DOMAIN" -d "$DOMAIN" -d "$COCKPIT_DOMAIN"
ensure_certbot_nginx_defaults

render "$(src installer nginx.conf.tpl)" /etc/nginx/sites-available/spawnwp
nginx -t && systemctl reload nginx

log "Initializing cockpit authentication"
FernetKey=$(/srv/wp-cockpit/venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
printf '%s\n' "$FernetKey" > /etc/spawnwp/auth.key
chmod 600 /etc/spawnwp/auth.key
APP_SETUP_CODE=$(cd /srv/wp-cockpit && /srv/wp-cockpit/venv/bin/python -c 'from auth import create_bootstrap; print(create_bootstrap())')
install -m 0644 "$(src installer wp-cockpit.service)" /etc/systemd/system/wp-cockpit.service
install -m 0644 "$(src installer spawnwp-update.service)" /etc/systemd/system/spawnwp-update.service
install -m 0644 "$(src installer docker-prune.service)" "$(src installer docker-prune.timer)" /etc/systemd/system/
install -m 0644 "$(src installer spawnwp-image-gc.service)" "$(src installer spawnwp-image-gc.timer)" /etc/systemd/system/
install -m 0644 "$(src installer spawnwp-site-expiry.service)" "$(src installer spawnwp-site-expiry.timer)" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wp-cockpit docker-prune.timer spawnwp-image-gc.timer spawnwp-site-expiry.timer
echo "Cockpit authentication is ready. The one-time activation procedure and code are shown in the final report below."

printf '{"telemetry":false}\n' > /var/lib/spawnwp/features.json
if [ "$ENABLE_TELEMETRY" = 1 ]; then
  install -d -m 0700 /var/lib/spawnwp/telemetry
  install -m 0755 "$(src installer telemetry.py)" /usr/local/lib/spawnwp/telemetry.py
  install -m 0644 "$(src installer spawnwp-telemetry.service)" "$(src installer spawnwp-telemetry.timer)" /etc/systemd/system/
  /usr/local/lib/spawnwp/telemetry.py enable
  systemctl daemon-reload; systemctl enable --now spawnwp-telemetry.timer
fi

touch "$MARKER"
cat > "$REPORT" <<EOF
SpawnWP $VERSION - installation complete

Sites: https://$DOMAIN/
Cockpit: https://$COCKPIT_DOMAIN/

COCKPIT FIRST-TIME ACTIVATION

1. Open: https://$COCKPIT_DOMAIN/
2. Enter this one-time activation code:

   $APP_SETUP_CODE

   Valid for 24 hours and usable once. This is not your password.

3. Create the administrator username and password.
4. Scan the QR code with a TOTP authenticator app.
5. Create a passkey when prompted by the browser.
6. Save the ten recovery codes shown at the end.

No WordPress environment was created automatically.
After cockpit activation, create the first environment from the dashboard and
choose the blueprint you want.

This root-only report is stored at:
  $REPORT

Read it again with:
  sudo cat $REPORT
EOF
chmod 600 "$REPORT"
cat "$REPORT"
echo "Next: follow the COCKPIT FIRST-TIME ACTIVATION steps above."
