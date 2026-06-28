map $http_upgrade $connection_upgrade { default upgrade; '' close; }
limit_req_zone $binary_remote_addr zone=spawnwp_auth:10m rate=30r/m;

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
    include /etc/nginx/snippets/spawnwp-proxy.conf;

    location /wp-json/spawnwp-deploy/v1/ {
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
    location = /_spawnwp_auth {
        internal;
        proxy_pass http://127.0.0.1:9393/api/auth/check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header X-Real-IP $remote_addr;
    }
    location @spawnwp_login { return 303 /login; }
    location /wp-dev-db/ {
        auth_request /_spawnwp_auth;
        error_page 401 = @spawnwp_login;
        proxy_pass http://127.0.0.1:9001/;
        add_header Cache-Control "no-store" always;
    }
    location /wp-dev-mail/ {
        auth_request /_spawnwp_auth;
        error_page 401 = @spawnwp_login;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:8025;
        add_header Cache-Control "no-store" always;
    }
    # __COCKPIT_PER_SITE__
    location /assets/ {
        proxy_pass http://127.0.0.1:9393/assets/;
        proxy_buffering on;
        add_header Cache-Control "public, max-age=604800, immutable" always;
    }
    location ~ ^/api/auth/(setup/(start|finish)|passkey/(start|finish)|fallback)$ {
        limit_req zone=spawnwp_auth burst=10 nodelay;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:9393;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
    location / {
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:9393/;
        proxy_read_timeout 300s;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
}
