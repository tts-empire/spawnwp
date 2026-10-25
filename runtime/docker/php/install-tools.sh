#!/bin/sh
set -eu
cd /tmp
fetch() {
    key=$1 dest=$2
    url=$(php -r '$j=json_decode(file_get_contents("/usr/local/share/spawnwp/tools-lock.json"),true,512,JSON_THROW_ON_ERROR); foreach(explode(".",$argv[1]) as $k){$j=$j[$k];} echo $j["url"];' "$key")
    sum=$(php -r '$j=json_decode(file_get_contents("/usr/local/share/spawnwp/tools-lock.json"),true,512,JSON_THROW_ON_ERROR); foreach(explode(".",$argv[1]) as $k){$j=$j[$k];} echo $j["sha256"];' "$key")
    curl -fSL "$url" -o "$dest"
    printf '%s  %s\n' "$sum" "$dest" | sha256sum -c -
}
fetch wp_cli /usr/local/bin/wp
fetch composer /usr/local/bin/composer
chmod 755 /usr/local/bin/wp /usr/local/bin/composer
case "$(dpkg --print-architecture)" in amd64) arch=x64 ;; arm64) arch=arm64 ;; *) exit 1 ;; esac
fetch "node.$arch" /tmp/node.tar.xz
tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
rm /tmp/node.tar.xz
cd /usr/local/composer
COMPOSER_ALLOW_SUPERUSER=1 composer install --no-interaction --no-progress --prefer-dist
for tool in phpcs phpcbf phpstan; do ln -s "/usr/local/composer/vendor/bin/$tool" "/usr/local/bin/$tool"; done
phpcs --config-set default_standard WordPress
composer clear-cache
