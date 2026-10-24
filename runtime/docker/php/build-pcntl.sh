#!/bin/sh
set -eu
cd /tmp
base=https://packages.sury.org/php/pool/main/p/php7.4
curl -fSL "$base/php7.4_7.4.33.orig.tar.xz" -o php.tar.xz
curl -fSL "$base/php7.4_7.4.33-31+0~20261021.121+debian13~1.gbpe9c428.debian.tar.xz" -o debian.tar.xz
echo '924846abf93bc613815c55dd3f5809377813ac62a9ec4eb3778675b82a27b927  php.tar.xz' | sha256sum -c -
echo '931faf5908545becc2d3089c1e3905befa61cee545459e80a217670e64fcaa56  debian.tar.xz' | sha256sum -c -
mkdir source
tar -xJf php.tar.xz -C source --strip-components=1
tar -xJf debian.tar.xz -C source
cd source
while IFS= read -r patchfile; do
    case "$patchfile" in ''|'#'*) continue ;; esac
    patch -p1 < "debian/patches/$patchfile"
done < debian/patches/series
cd ext/pcntl
phpize7.4
./configure --with-php-config=/usr/bin/php-config7.4 --enable-pcntl
make -j2
mkdir /pcntl
cp modules/pcntl.so /pcntl/
