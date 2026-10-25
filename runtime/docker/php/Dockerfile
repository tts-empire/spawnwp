ARG PHP_VERSION=8.3
# Key the base image on PHP only (a tag that is always published). The exact
# WordPress core is fetched on the fly below, so the base image's bundled WP
# version is irrelevant — this decouples the captured WP version from image tags.
ARG PHP_BASE_IMAGE=wordpress:php${PHP_VERSION}-fpm
FROM ${PHP_BASE_IMAGE}

ARG PHP_VERSION
ARG PHP_BASE_IMAGE
RUN . /etc/os-release && test "$ID:$VERSION_ID" = debian:13

ARG WORDPRESS_VERSION=latest
ARG SPAWNWP_CONTEXT_HASH=dev

# Reject floating PHP base tags that have not yet picked up the php2shell fixes.
# PHP 7.4 remains available for explicitly acknowledged LAB use.
RUN if [ "$PHP_VERSION" != "7.4" ]; then \
      php -r '$v=PHP_VERSION; $min=["8.2"=>"8.2.35","8.3"=>"8.3.36","8.4"=>"8.4.27","8.5"=>"8.5.12"][$argv[1]]??null; if (!$min || version_compare($v,$min,"<")) { fwrite(STDERR,"SpawnWP security check failed: PHP ".$v." is below the fixed release floor for ".$argv[1].". Refresh the base image and rebuild.\\n"); exit(1); }' "$PHP_VERSION"; \
    fi

# FTP/FTPS: WordPress reported "ftp and ftps are not available" because ext/ftp was
# never installed. FTPS additionally needs OpenSSL *headers* (libssl-dev — the base
# image only ships the runtime) AND an explicit opt-in: the implicit "FTPS comes with
# openssl" path only applies when PHP itself is configured --with-openssl, which is
# not the case here because docker-php-ext-install builds each extension standalone
# via phpize.
#
# The opt-in flag was RENAMED in PHP 8.4, and passing the wrong one aborts configure
# with "unrecognized options" (verified on every version we build):
#   PHP <= 8.3  ->  --with-openssl-dir
#   PHP >= 8.4  ->  --with-ftp-ssl
#
# Pre-configuring ftp here leaves a Makefile behind, and docker-php-ext-install skips
# re-configuring when one exists ([ -e Makefile ] || docker-php-ext-configure "$ext"),
# so the batch install below honours the SSL-enabled config — the same trick already
# used for gd. Note: imap cannot be added this way on 8.4 (dropped from core).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libssl-dev \
        libxml2-dev \
        libgmp-dev \
        unzip \
        less \
        mariadb-client \
        libfcgi-bin \
        util-linux \
    && if [ "$(printf '%s\n8.4\n' "$PHP_VERSION" | sort -V | head -1)" = "8.4" ]; then \
           docker-php-ext-configure ftp --with-ftp-ssl; \
       else \
           docker-php-ext-configure ftp --with-openssl-dir; \
       fi \
    && docker-php-ext-install \
        ftp \
        soap \
        sockets \
        pcntl \
        gmp \
        pdo_mysql \
        calendar \
    && php -r 'exit(version_compare(phpversion("imagick"),"3.8.1",">=")?0:1);' \
    && pecl install redis-6.3.0 \
    && docker-php-ext-enable redis \
    # Xdebug: xdebug.ini (copied below) ships it commented out and `make xdebug-on`
    # uncomments `zend_extension=xdebug` — but the module was never actually installed,
    # so the toggle only produced "Failed loading Zend extension 'xdebug'". Install it
    # here, and leave it DISABLED (no docker-php-ext-enable): xdebug.ini is the switch.
    # Current Xdebug requires PHP >= 8.0, so 7.4 must pin the last version that supports it.
    && if [ "$(printf '%s\n8.0\n' "$PHP_VERSION" | sort -V | head -1)" = "8.0" ]; then \
           pecl install xdebug-3.5.3; \
       else \
           pecl install xdebug-3.1.6; \
       fi \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/local/share/spawnwp \
    && php -r 'file_put_contents("/usr/local/share/spawnwp/php-version", PHP_VERSION."\n");'

# Versioned tool downloads and Composer dependencies, shared with the legacy build.
ENV COMPOSER_HOME=/usr/local/composer
COPY tools-lock.json runtime-lock.json UPSTREAM-NOTICES.md /usr/local/share/spawnwp/
COPY tooling-composer.json /usr/local/composer/composer.json
COPY tooling-composer.lock /usr/local/composer/composer.lock
COPY install-tools.sh seed-wordpress.sh /usr/local/share/spawnwp/
RUN sh /usr/local/share/spawnwp/install-tools.sh
# Preserve the official long-path-safe ZIP behavior for pinned cores.
RUN if [ "$WORDPRESS_VERSION" != "latest" ]; then \
        sh /usr/local/share/spawnwp/seed-wordpress.sh "$WORDPRESS_VERSION"; \
    else \
        php -r 'include "/usr/src/wordpress/wp-includes/version.php"; file_put_contents("/usr/local/share/spawnwp/wordpress-version",$wp_version."\n");'; \
    fi
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY fpm-healthcheck /usr/local/bin/fpm-healthcheck
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh /usr/local/bin/fpm-healthcheck \
    && printf '[www]\nping.path = /fpm-ping\n' > /usr/local/etc/php-fpm.d/zz-health.conf

COPY phpstan-wp.neon /usr/local/etc/phpstan-wp.neon

COPY php.ini /usr/local/etc/php/conf.d/custom.ini
COPY opcache.ini /usr/local/etc/php/conf.d/opcache.ini
COPY xdebug-default.ini /usr/local/etc/php/conf.d/xdebug.ini

# Build-context fingerprint: new-project.sh compares this label against the hash of
# the current docker/php context (+ WP build args) to skip rebuilds when nothing
# changed. Declared last so changing the hash never invalidates the layers above.
ARG SPAWNWP_CONTEXT_HASH=dev
LABEL org.spawnwp.context-hash=$SPAWNWP_CONTEXT_HASH \
      org.spawnwp.php-series=$PHP_VERSION \
      org.spawnwp.php-version-file=/usr/local/share/spawnwp/php-version \
      org.spawnwp.base-image=$PHP_BASE_IMAGE \
      org.spawnwp.runtime-provider=official \
      org.spawnwp.php-security-policy=trixie-2026-10-24
