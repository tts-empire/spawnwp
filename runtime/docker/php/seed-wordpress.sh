#!/bin/sh
set -eu
version=${1:-latest}
mkdir -p /usr/src/wordpress
find /usr/src/wordpress -mindepth 1 -maxdepth 1 ! -name wp-config-docker.php -exec rm -rf -- {} +
if [ "$version" = latest ]; then url=https://wordpress.org/latest.zip
else url="https://wordpress.org/wordpress-${version}.zip"; fi
php -d memory_limit=-1 /usr/local/bin/wp core download "$url" --allow-root --force --path=/usr/src/wordpress
php -r 'include "/usr/src/wordpress/wp-includes/version.php"; if(version_compare(PHP_VERSION,$required_php_version,"<")){fwrite(STDERR,"Requested WordPress requires PHP ".$required_php_version."\n");exit(1);} file_put_contents("/usr/local/share/spawnwp/wordpress-version",$wp_version."\n");'
chown -R www-data:www-data /usr/src/wordpress
