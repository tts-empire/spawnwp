ARG PHP_VERSION=8.3
# Key the base image on PHP only (a tag that is always published). The exact
# WordPress core is fetched on the fly below, so the base image's bundled WP
# version is irrelevant — this decouples the captured WP version from image tags.
FROM wordpress:php${PHP_VERSION}-fpm

ARG PHP_VERSION
ARG WORDPRESS_VERSION=latest

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
        libicu-dev \
        libmagickwand-dev \
        libwebp-dev \
        libzip-dev \
        libexif-dev \
        libssl-dev \
        libxml2-dev \
        libgmp-dev \
        unzip \
        less \
        mariadb-client \
    && if [ "$(printf '%s\n8.4\n' "$PHP_VERSION" | sort -V | head -1)" = "8.4" ]; then \
           docker-php-ext-configure ftp --with-ftp-ssl; \
       else \
           docker-php-ext-configure ftp --with-openssl-dir; \
       fi \
    && docker-php-ext-install \
        intl \
        zip \
        exif \
        bcmath \
        ftp \
        soap \
        sockets \
        pcntl \
        gmp \
        pdo_mysql \
        calendar \
    && docker-php-ext-configure gd --with-jpeg --with-webp \
    && docker-php-ext-install gd \
    && pecl install imagick redis \
    && docker-php-ext-enable imagick redis \
    # Xdebug: xdebug.ini (copied below) ships it commented out and `make xdebug-on`
    # uncomments `zend_extension=xdebug` — but the module was never actually installed,
    # so the toggle only produced "Failed loading Zend extension 'xdebug'". Install it
    # here, and leave it DISABLED (no docker-php-ext-enable): xdebug.ini is the switch.
    # Current Xdebug requires PHP >= 8.0, so 7.4 must pin the last version that supports it.
    && if [ "$(printf '%s\n8.0\n' "$PHP_VERSION" | sort -V | head -1)" = "8.0" ]; then \
           pecl install xdebug; \
       else \
           pecl install xdebug-3.1.6; \
       fi \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# WP-CLI
RUN curl -sS https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp \
    && chmod +x /usr/local/bin/wp

# Seed the exact requested WordPress core into the image so spawned sites mirror
# the captured origin. Runs when a specific version is pinned (any PHP), and also
# for PHP 7.4 whose base image ships a frozen, obsolete core that must be refreshed
# to the current release when "latest" is requested.
RUN if [ "$WORDPRESS_VERSION" != "latest" ] || [ "$PHP_VERSION" = "7.4" ]; then \
        php -d memory_limit=-1 /usr/local/bin/wp core download --allow-root --force --path=/usr/src/wordpress --version="$WORDPRESS_VERSION"; \
    fi

# Composer 2
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer --2

# Node.js LTS
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# QA / WordPress.org review toolchain (image-wide CLI tools):
#   phpcs --standard=WordPress   → WordPress Coding Standards (WPCS) + PHPCompatibilityWP
#   phpstan -c /usr/local/etc/phpstan-wp.neon <path>  → static analysis with WordPress stubs
# Global Composer packages; binaries linked into /usr/local/bin. The composer-installer
# automatically registers the WordPress/PHPCompatibility standards in phpcs.
ENV COMPOSER_HOME=/usr/local/composer
RUN composer global config --no-plugins allow-plugins.dealerdirect/phpcodesniffer-composer-installer true \
    && composer global require --no-interaction --no-progress \
        squizlabs/php_codesniffer \
        wp-coding-standards/wpcs \
        phpcompatibility/phpcompatibility-wp \
        dealerdirect/phpcodesniffer-composer-installer \
        phpstan/phpstan \
        szepeviktor/phpstan-wordpress \
    && ln -sf "$COMPOSER_HOME/vendor/bin/phpcs"   /usr/local/bin/phpcs \
    && ln -sf "$COMPOSER_HOME/vendor/bin/phpcbf"  /usr/local/bin/phpcbf \
    && ln -sf "$COMPOSER_HOME/vendor/bin/phpstan" /usr/local/bin/phpstan \
    && phpcs --config-set default_standard WordPress \
    && composer clear-cache
COPY phpstan-wp.neon /usr/local/etc/phpstan-wp.neon

COPY php.ini /usr/local/etc/php/conf.d/custom.ini
COPY opcache.ini /usr/local/etc/php/conf.d/opcache.ini
COPY xdebug.ini /usr/local/etc/php/conf.d/xdebug.ini

# Build-context fingerprint: new-project.sh compares this label against the hash of
# the current docker/php context (+ WP build args) to skip rebuilds when nothing
# changed. Declared last so changing the hash never invalidates the layers above.
ARG SPAWNWP_CONTEXT_HASH=dev
LABEL org.spawnwp.context-hash=$SPAWNWP_CONTEXT_HASH
