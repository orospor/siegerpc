#!/usr/bin/env bash
set -euo pipefail

WP_ROOT=""
MAX_UPLOAD_MB="8"
WINDOW_SECONDS="60"
MAX_REQUESTS="10"
BLOCK_XMLRPC="1"
INSTALL_NGINX="0"
NGINX_SITE=""
DRY_RUN="0"

usage() {
  cat <<'EOF'
Usage: harden-wordpress-forms.sh [options]

Installs defensive controls for WordPress Contact Form 7 upload abuse.

What it can do:
  1. Install a WordPress MU-plugin that:
     - blocks xmlrpc.php
     - rate-limits Contact Form 7 feedback REST requests per client IP
     - rejects Contact Form 7 feedback requests above a configured Content-Length
  2. Optionally install Nginx rules that:
     - block xmlrpc.php before PHP
     - rate-limit Contact Form 7 feedback endpoint before PHP
     - cap request body size before PHP

Options:
  --wp-root DIR             WordPress root containing wp-config.php
  --max-upload-mb N         Max CF7 feedback request body size in MB (default: 8)
  --window-seconds N        Rate-limit window in seconds (default: 60)
  --max-requests N          Requests allowed per client per window (default: 10)
  --no-block-xmlrpc         Do not block xmlrpc.php
  --nginx-site FILE         Patch this Nginx site/server file with the guard include
  --dry-run                 Print intended changes without writing files
  -h, --help                Show this help

Examples:
  sudo bash harden-wordpress-forms.sh --wp-root /var/www/html
  sudo bash harden-wordpress-forms.sh --wp-root /var/www/html --max-upload-mb 5 --max-requests 5
  sudo bash harden-wordpress-forms.sh --wp-root /var/www/html --nginx-site /etc/nginx/sites-available/default
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "run as root, e.g. sudo bash scripts/harden-wordpress-forms.sh --wp-root /var/www/html"
  fi
}

backup_file() {
  local file="$1"
  if [ -f "$file" ]; then
    cp "$file" "$file.siegemax-backup.$(date +%Y%m%d%H%M%S)"
  fi
}

find_wp_root() {
  if [ -n "$WP_ROOT" ]; then
    return
  fi
  for candidate in /var/www/html /var/www/wordpress /var/www/*/public_html /usr/share/nginx/html /srv/www/*/public_html; do
    if [ -f "$candidate/wp-config.php" ]; then
      WP_ROOT="$candidate"
      return
    fi
  done
  die "could not find wp-config.php automatically; pass --wp-root /path/to/wordpress"
}

write_file() {
  local path="$1"
  if [ "$DRY_RUN" = "1" ]; then
    log "would write $path"
    return
  fi
  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

install_mu_plugin() {
  local plugin_dir="$WP_ROOT/wp-content/mu-plugins"
  local plugin_file="$plugin_dir/siegemax-guard.php"
  local max_bytes=$((MAX_UPLOAD_MB * 1024 * 1024))

  log "Installing WordPress MU-plugin guard: $plugin_file"
  if [ "$DRY_RUN" = "0" ]; then
    mkdir -p "$plugin_dir"
    backup_file "$plugin_file"
  fi

  write_file "$plugin_file" <<PHP
<?php
/**
 * Plugin Name: Siegemax Guard
 * Description: Defensive guard for Contact Form 7 feedback upload abuse and XML-RPC exposure.
 */

if (!defined('ABSPATH')) {
    exit;
}

define('SIEGEMAX_GUARD_MAX_BYTES', $max_bytes);
define('SIEGEMAX_GUARD_WINDOW', $WINDOW_SECONDS);
define('SIEGEMAX_GUARD_MAX_REQUESTS', $MAX_REQUESTS);
define('SIEGEMAX_GUARD_BLOCK_XMLRPC', $BLOCK_XMLRPC);

function siegemax_guard_client_ip(): string {
    foreach (array('HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR') as \$key) {
        if (empty(\$_SERVER[\$key])) {
            continue;
        }
        \$raw = trim((string) \$_SERVER[\$key]);
        \$ip = trim(explode(',', \$raw)[0]);
        if (filter_var(\$ip, FILTER_VALIDATE_IP)) {
            return \$ip;
        }
    }
    return 'unknown';
}

function siegemax_guard_is_cf7_feedback_route(\$route): bool {
    return is_string(\$route) && preg_match('#^/contact-form-7/v1/contact-forms/[0-9]+/feedback/?$#', \$route);
}

if (SIEGEMAX_GUARD_BLOCK_XMLRPC) {
    add_filter('xmlrpc_enabled', '__return_false');
    add_action('init', function () {
        \$uri = \$_SERVER['REQUEST_URI'] ?? '';
        if (preg_match('#/xmlrpc\\.php(?:\\?|$)#i', \$uri)) {
            status_header(403);
            header('Content-Type: text/plain; charset=utf-8');
            echo 'XML-RPC disabled';
            exit;
        }
    }, 0);
}

add_filter('rest_pre_dispatch', function (\$result, \$server, \$request) {
    if (!siegemax_guard_is_cf7_feedback_route(\$request->get_route())) {
        return \$result;
    }

    \$content_length = isset(\$_SERVER['CONTENT_LENGTH']) ? (int) \$_SERVER['CONTENT_LENGTH'] : 0;
    if (\$content_length > SIEGEMAX_GUARD_MAX_BYTES) {
        return new WP_Error(
            'siegemax_payload_too_large',
            'Contact form upload is too large.',
            array('status' => 413)
        );
    }

    \$ip = siegemax_guard_client_ip();
    \$key = 'siegemax_cf7_' . md5(\$ip);
    \$count = (int) get_transient(\$key);

    if (\$count >= SIEGEMAX_GUARD_MAX_REQUESTS) {
        return new WP_Error(
            'siegemax_rate_limited',
            'Too many contact form submissions. Please slow down.',
            array('status' => 429)
        );
    }

    set_transient(\$key, \$count + 1, SIEGEMAX_GUARD_WINDOW);
    return \$result;
}, 10, 3);
PHP

  if [ "$DRY_RUN" = "0" ]; then
    chown "$(stat -c '%U:%G' "$WP_ROOT/wp-config.php" 2>/dev/null || echo root:root)" "$plugin_file" 2>/dev/null || true
  fi
}

install_nginx_guard() {
  [ -n "$NGINX_SITE" ] || return
  [ -f "$NGINX_SITE" ] || die "Nginx site file not found: $NGINX_SITE"

  local max_body="${MAX_UPLOAD_MB}m"
  local rate_per_second
  rate_per_second=$(( (MAX_REQUESTS + WINDOW_SECONDS - 1) / WINDOW_SECONDS ))
  [ "$rate_per_second" -lt 1 ] && rate_per_second=1

  log "Installing Nginx guard snippets"
  if [ "$DRY_RUN" = "0" ]; then
    mkdir -p /etc/nginx/snippets /etc/nginx/conf.d
    backup_file /etc/nginx/conf.d/siegemax-limit-zone.conf
    backup_file /etc/nginx/snippets/siegemax-guard.conf
    backup_file "$NGINX_SITE"
  fi

  write_file /etc/nginx/conf.d/siegemax-limit-zone.conf <<NGINX
# Siegemax Guard: shared request-rate zone for Contact Form 7 feedback.
limit_req_zone \$binary_remote_addr zone=siegemax_cf7:10m rate=${rate_per_second}r/s;
NGINX

  write_file /etc/nginx/snippets/siegemax-guard.conf <<NGINX
# Siegemax Guard: block XML-RPC and rate-limit Contact Form 7 feedback uploads.
location = /xmlrpc.php {
    return 403;
}

location ~ ^/wp-json/contact-form-7/v1/contact-forms/[0-9]+/feedback/?$ {
    client_max_body_size $max_body;
    limit_req zone=siegemax_cf7 burst=$MAX_REQUESTS nodelay;
    try_files \$uri \$uri/ /index.php?\$args;
}
NGINX

  if grep -q "siegemax-guard.conf" "$NGINX_SITE"; then
    log "Nginx site already includes siegemax guard: $NGINX_SITE"
  elif [ "$DRY_RUN" = "1" ]; then
    log "would insert include /etc/nginx/snippets/siegemax-guard.conf into first server block of $NGINX_SITE"
  else
    local tmp
    tmp="$(mktemp)"
    awk '
      BEGIN { in_server=0; depth=0; inserted=0; }
      {
        line=$0
        if (inserted == 0 && line ~ /^[[:space:]]*server[[:space:]]*\{/) {
          in_server=1
        }
        if (in_server && inserted == 0 && line ~ /^[[:space:]]*\}/ && depth == 1) {
          print "    include /etc/nginx/snippets/siegemax-guard.conf;"
          inserted=1
          in_server=0
        }
        print line
        if (in_server || line ~ /^[[:space:]]*server[[:space:]]*\{/) {
          opens=gsub(/\{/, "{", line)
          closes=gsub(/\}/, "}", line)
          depth += opens - closes
        }
      }
      END {
        if (inserted == 0) {
          exit 3
        }
      }
    ' "$NGINX_SITE" > "$tmp" || {
      rm -f "$tmp"
      die "could not patch Nginx site automatically; add include /etc/nginx/snippets/siegemax-guard.conf inside your server block"
    }
    mv "$tmp" "$NGINX_SITE"
  fi

  if command -v nginx >/dev/null 2>&1 && [ "$DRY_RUN" = "0" ]; then
    nginx -t
    systemctl reload nginx || service nginx reload
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --wp-root)
      shift
      [ "$#" -gt 0 ] || die "--wp-root requires a directory"
      WP_ROOT="$1"
      ;;
    --max-upload-mb)
      shift
      [ "$#" -gt 0 ] || die "--max-upload-mb requires a value"
      MAX_UPLOAD_MB="$1"
      ;;
    --window-seconds)
      shift
      [ "$#" -gt 0 ] || die "--window-seconds requires a value"
      WINDOW_SECONDS="$1"
      ;;
    --max-requests)
      shift
      [ "$#" -gt 0 ] || die "--max-requests requires a value"
      MAX_REQUESTS="$1"
      ;;
    --no-block-xmlrpc)
      BLOCK_XMLRPC="0"
      ;;
    --nginx-site)
      shift
      [ "$#" -gt 0 ] || die "--nginx-site requires a file"
      INSTALL_NGINX="1"
      NGINX_SITE="$1"
      ;;
    --dry-run)
      DRY_RUN="1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

[[ "$MAX_UPLOAD_MB" =~ ^[0-9]+$ ]] || die "--max-upload-mb must be an integer"
[[ "$WINDOW_SECONDS" =~ ^[0-9]+$ ]] || die "--window-seconds must be an integer"
[[ "$MAX_REQUESTS" =~ ^[0-9]+$ ]] || die "--max-requests must be an integer"
[ "$MAX_UPLOAD_MB" -gt 0 ] || die "--max-upload-mb must be greater than 0"
[ "$WINDOW_SECONDS" -gt 0 ] || die "--window-seconds must be greater than 0"
[ "$MAX_REQUESTS" -gt 0 ] || die "--max-requests must be greater than 0"

if [ "$DRY_RUN" != "1" ]; then
  need_root
fi
find_wp_root
[ -f "$WP_ROOT/wp-config.php" ] || die "wp-config.php not found in $WP_ROOT"

log "WordPress root:       $WP_ROOT"
log "Max upload body:      ${MAX_UPLOAD_MB} MB"
log "Rate limit:           $MAX_REQUESTS requests per $WINDOW_SECONDS seconds per client"
log "Block XML-RPC:        $BLOCK_XMLRPC"

install_mu_plugin
if [ "$INSTALL_NGINX" = "1" ]; then
  install_nginx_guard
fi

log ""
log "Done."
log "Test with:"
log "  curl -i https://yourdomain.com/xmlrpc.php"
log "  siegemax --url https://yourdomain.com/wp-json/contact-form-7/v1/contact-forms/50/feedback --file-size-mb $MAX_UPLOAD_MB --rate 1 --requests 3 --i-own-this-server"
