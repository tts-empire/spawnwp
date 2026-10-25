#!/bin/sh
set -eu
. /etc/os-release
[ "$ID:$VERSION_ID" = debian:13 ]
export DEBIAN_FRONTEND=noninteractive
echo "b486fd5488185c4c46467960fa69c53d5085fec492cf76b9eaf3db33561c9d7c  /usr/share/keyrings/sury-archive-keyring.gpg" | sha256sum -c -
# Packages must not start daemons during the image build.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    bash unzip less util-linux mariadb-client ghostscript libfcgi-bin patch gcc g++ make \
    systemd-standalone-tmpfiles
printf 'deb [signed-by=/usr/share/keyrings/sury-archive-keyring.gpg] https://packages.sury.org/php/ trixie main\n' > /etc/apt/sources.list.d/sury.list
apt-get update
# Each item is a fixed package=version, with APT verifying signed repository hashes.
mkdir /tmp/sury-debs
while read -r checksum filename; do
    destination="/tmp/sury-debs/${filename##*/}"
    package=${filename##*/}; package=${package%%_*}
    apt-cache show "$package" | grep -Fx "SHA256: $checksum" >/dev/null
    curl -fSL "https://packages.sury.org/php/$filename" -o "$destination"
    printf '%s  %s\n' "$checksum" "$destination" | sha256sum -c -
done < "/usr/local/share/spawnwp/sury-downloads-$(dpkg --print-architecture).txt"
apt-get install -y --no-install-recommends /tmp/sury-debs/*.deb
rm -rf /tmp/sury-debs
apt-get install -y --no-install-recommends php-pear
mkdir -p /usr/local/etc/php/conf.d /usr/local/etc/php/fpm-conf.d /usr/local/share/spawnwp /var/www/html /usr/src/wordpress
ln -sf /usr/bin/php7.4 /usr/local/bin/php
ln -sf /usr/bin/phpize7.4 /usr/local/bin/phpize
ln -sf /usr/bin/php-config7.4 /usr/local/bin/php-config
# Use the exact same extension INIs for both SAPIs. Xdebug is enabled per site.
for ini in /etc/php/7.4/cli/conf.d/*.ini; do
    case "$ini" in *xdebug*) continue ;; esac
    ln -s "$(readlink -f "$ini")" /usr/local/etc/php/conf.d/"$(basename "$ini")"
done
# The distro ini disables PCNTL and relies on a host cron for session collection.
# Our container owns its ini, and uses PHP's GC instead.
printf 'session.save_path=/tmp\nsession.gc_probability=1\nsession.gc_divisor=1000\ndisable_functions=\n' > /usr/local/etc/php/php.ini
chown -R 33:33 /var/www/html /usr/src/wordpress
dpkg-query -W -f='${Package}\t${Version}\n' > /usr/local/share/spawnwp/packages.tsv
rm -rf /var/lib/apt/lists/* /tmp/pear
