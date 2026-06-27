server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name @@DOMAIN@@ @@COCKPIT_DOMAIN@@;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 301 https://$host$request_uri; }
}
