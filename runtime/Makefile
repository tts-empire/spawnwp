.PHONY: up down restart status logs shell wp composer npm test lint \
        reset snapshot restore xdebug-on xdebug-off php-switch rebuild \
        db-shell mail logs-wp disk bootstrap new-project

COMPOSE = docker compose
PHP     = $(COMPOSE) exec -T php
WP      = $(PHP) wp --allow-root

# ── Lifecycle ──────────────────────────────────────────────────────────────────

up:
	@VER=$$(grep -E '^PHP_VERSION=' .env | cut -d= -f2); \
	if docker image inspect wp-dev-php:$$VER >/dev/null 2>&1; then \
		echo "Image wp-dev-php:$$VER already present — starting without rebuild."; \
	else \
		echo "PHP image $$VER not cached: the first build may take a few minutes..."; \
	fi
	$(COMPOSE) up -d
	@echo "Stack is up. Run 'make bootstrap' if this is the first start."

# Forced rebuild of the php image (use it after editing the Dockerfile)
rebuild:
	$(COMPOSE) build --no-cache php
	$(COMPOSE) up -d php
	@echo "php image rebuilt and restarted."

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

status:
	$(COMPOSE) ps
	@echo ""
	@docker compose top

logs:
	$(COMPOSE) logs -f --tail=50

bootstrap:
	@bash scripts/bootstrap.sh

# ── PHP version switching ──────────────────────────────────────────────────────

php-switch:
ifndef VER
	$(error VER is required. Usage: make php-switch VER=8.2)
endif
	@sed -i "s/^PHP_VERSION=.*/PHP_VERSION=$(VER)/" .env
	@if [ "$(VER)" = "7.4" ]; then SERIES=6; else SERIES=7; fi; \
	if grep -q '^WORDPRESS_SERIES=' .env; then \
		sed -i "s/^WORDPRESS_SERIES=.*/WORDPRESS_SERIES=$$SERIES/" .env; \
	else \
		echo "WORDPRESS_SERIES=$$SERIES" >> .env; \
	fi
	@echo "Switched PHP_VERSION to $(VER)."
	@if docker image inspect wp-dev-php:$(VER) >/dev/null 2>&1; then \
		echo "Image wp-dev-php:$(VER) already present — skipping the build (instant switch)."; \
	else \
		echo "First build for PHP $(VER): base pull + extension compilation, may take a few minutes..."; \
		$(COMPOSE) build php; \
	fi
	$(COMPOSE) up -d php
	@echo "Done. PHP version is now $(VER)."

# ── Development shell & tools ─────────────────────────────────────────────────

shell:
	$(COMPOSE) exec php bash

db-shell:
	@source .env && $(COMPOSE) exec db mariadb -u"$$DB_USER" -p"$$DB_PASS" "$$DB_NAME"

wp:
ifndef CMD
	$(error CMD is required. Usage: make wp CMD="plugin list")
endif
	$(WP) $(CMD)

composer:
ifndef CMD
	$(error CMD is required. Usage: make composer CMD="install")
endif
	$(PHP) composer $(CMD)

npm:
ifndef CMD
	$(error CMD is required. Usage: make npm CMD="test")
endif
	$(PHP) npm $(CMD)

# ── Testing & linting ─────────────────────────────────────────────────────────

test:
	@echo "==> Running plugin tests..."
	@for dir in projects/primary/wp-content/plugins/*/; do \
	  if [ -f "$$dir/composer.json" ] && grep -q '"test"' "$$dir/composer.json"; then \
	    echo "  -> $$dir"; \
	    $(PHP) bash -c "cd /var/www/html/wp-content/plugins/$$(basename $$dir) && composer test"; \
	  elif [ -f "$$dir/package.json" ] && grep -q '"test"' "$$dir/package.json"; then \
	    echo "  -> $$dir (npm)"; \
	    $(PHP) bash -c "cd /var/www/html/wp-content/plugins/$$(basename $$dir) && npm test"; \
	  else \
	    echo "  -> $$(basename $$dir): no test suite configured"; \
	  fi; \
	done

lint:
	@echo "==> Running PHPCS on plugins..."
	@for dir in projects/primary/wp-content/plugins/*/; do \
	  if [ -f "$$dir/phpcs.xml" ] || [ -f "$$dir/phpcs.xml.dist" ]; then \
	    echo "  -> $$dir"; \
	    $(PHP) bash -c "cd /var/www/html/wp-content/plugins/$$(basename $$dir) && composer lint 2>/dev/null || vendor/bin/phpcs"; \
	  fi; \
	done

# ── Xdebug ────────────────────────────────────────────────────────────────────

xdebug-on:
	@sed -i 's/^;zend_extension=xdebug/zend_extension=xdebug/' docker/php/xdebug.ini
	@sed -i 's/^;xdebug\./xdebug./' docker/php/xdebug.ini
	$(COMPOSE) restart php
	@echo "Xdebug enabled (port 9003)."

xdebug-off:
	@sed -i 's/^zend_extension=xdebug/;zend_extension=xdebug/' docker/php/xdebug.ini
	@sed -i 's/^xdebug\./;xdebug./' docker/php/xdebug.ini
	$(COMPOSE) restart php
	@echo "Xdebug disabled."

# ── Observability ─────────────────────────────────────────────────────────────

mail:
	@echo "Mailpit web UI: http://127.0.0.1:8025"
	@echo "SSH forward:    ssh -L 8025:127.0.0.1:8025 root@devel.presenzaweb.net"

logs-wp:
	$(PHP) tail -f /var/www/html/wp-content/debug.log

disk:
	@echo "=== Host disk ==="
	@df -h /
	@echo ""
	@echo "=== Docker volumes ==="
	@docker system df -v 2>/dev/null | grep -A 50 "Local Volumes"
	@echo ""
	@echo "=== Backups ==="
	@du -sh backups/db backups/files 2>/dev/null || echo "No backups yet."

# ── Backup & restore ──────────────────────────────────────────────────────────

snapshot:
	@bash scripts/snapshot.sh

restore:
ifndef SNAPSHOT
	$(error SNAPSHOT is required. Usage: make restore SNAPSHOT=20260101-120000)
endif
	@bash scripts/restore.sh $(SNAPSHOT)

reset:
	@bash scripts/reset.sh --force

# ── New project (subpath site) ────────────────────────────────────────────────

new-project:
ifndef NAME
	$(error NAME is required. Usage: make new-project NAME=demo-site)
endif
	@bash scripts/new-project.sh "$(NAME)" "$(or $(BLUEPRINT),development)" "$(PHP)"
