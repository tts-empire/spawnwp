ARG PHP_VERSION=8.3
ARG WORDPRESS_SERIES=7
FROM wordpress:${WORDPRESS_SERIES}-php${PHP_VERSION}-fpm

ARG PHP_VERSION
ARG WORDPRESS_VERSION=latest

RUN apt-get update && apt-get install -y --no-install-recommends \
        libicu-dev \
        libmagickwand-dev \
        libwebp-dev \
        libzip-dev \
        libexif-dev \
        unzip \
        less \
        mariadb-client \
    && docker-php-ext-install \
        intl \
        zip \
        exif \
        bcmath \
    && docker-php-ext-configure gd --with-jpeg --with-webp \
    && docker-php-ext-install gd \
    && pecl install imagick redis \
    && docker-php-ext-enable imagick redis \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# WP-CLI
RUN curl -sS https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp \
    && chmod +x /usr/local/bin/wp

# The official PHP 7.4 image is frozen with an obsolete WordPress core. Keep its
# EOL runtime only, then seed new volumes with the requested current core.
RUN if [ "$PHP_VERSION" = "7.4" ]; then \
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
