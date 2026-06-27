map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name @@DOMAIN@@ @@COCKPIT_DOMAIN@@;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name @@DOMAIN@@;
    ssl_certificate /etc/letsencrypt/live/@@DOMAIN@@/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/@@DOMAIN@@/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    client_max_body_size 64M;
    auth_basic "SpawnWP sites";
    auth_basic_user_file /etc/nginx/.spawnwp-htpasswd;
    include /etc/nginx/snippets/spawnwp-proxy.conf;

    location /wp-json/spawnwp-deploy/v1/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:8080;
    }
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_intercept_errors on;
        error_page 502 503 504 =502 @wp_down;
    }
    # __SPAWNWP_SITES__
    location @wp_down {
        default_type text/plain;
        return 502 "WordPress environment is not running.\n";
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name @@COCKPIT_DOMAIN@@;
    ssl_certificate /etc/letsencrypt/live/@@DOMAIN@@/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/@@DOMAIN@@/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    client_max_body_size 64M;
    auth_basic "SpawnWP cockpit";
    auth_basic_user_file /etc/nginx/.spawnwp-htpasswd;

    location = /_spawnwp_auth {
        internal;
        proxy_pass http://127.0.0.1:9393/api/auth/check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header X-Real-IP $remote_addr;
    }
    location /wp-dev-db/ {
        include /etc/nginx/cockpit-allowed.conf;
        auth_request /_spawnwp_auth;
        error_page 401 =303 /login;
        proxy_pass http://127.0.0.1:9001/;
        add_header Cache-Control "no-store" always;
    }
    location /wp-dev-mail/ {
        include /etc/nginx/cockpit-allowed.conf;
        auth_request /_spawnwp_auth;
        error_page 401 =303 /login;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:8025;
        add_header Cache-Control "no-store" always;
    }
    # __COCKPIT_PER_SITE__
    location /assets/ {
        include /etc/nginx/cockpit-allowed.conf;
        proxy_pass http://127.0.0.1:9393/assets/;
        proxy_buffering on;
        add_header Cache-Control "public, max-age=604800, immutable" always;
    }
    location / {
        include /etc/nginx/cockpit-allowed.conf;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:9393/;
        proxy_read_timeout 300s;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
}
