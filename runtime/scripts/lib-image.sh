#!/usr/bin/env bash
# Identity of the shared PHP image: its tag and its build-context hash.
#
# One definition, used by every build path (new-project.sh, refresh-image.sh,
# php-switch-progress.py). It used to be copy-pasted into two of those three and
# simply MISSING from the third, which is how php-switch ended up building the
# image with SPAWNWP_CONTEXT_HASH unset — compose then stamped the label "dev"
# and every later deploy on that PHP version rebuilt from scratch.
#
# Usable two ways:
#   source scripts/lib-image.sh                 # bash callers
#   bash scripts/lib-image.sh hash|tag|suffix   # python / CLI callers
# Both expect the CWD to be a project root (the directory holding docker/php).

PHP_IMAGE_REPO=wp-dev-php

# Exactly the files the Dockerfile consumes: its own text plus every COPY source.
# NOT `find . -type f`, which is what the two inline copies used to do — any stray
# file (an editor backup, .DS_Store, a copied-in artifact) then changed the hash,
# invalidated the cache for that project, and — because new-project.sh copies
# docker/ wholesale — propagated into every site created afterwards.
# zz-site.ini is deliberately absent: it is a per-site runtime mount, not a build
# input, so a custom PHP setting must not force an image rebuild.
# installer/tests/static.sh asserts this list covers every COPY in the Dockerfile.
SPAWNWP_IMAGE_INPUTS=(Dockerfile php.ini opcache.ini xdebug.ini phpstan-wp.neon)

# "" for latest/unset, "-wp<version>" for a pinned WordPress. Anything that is not
# a plain dotted version is treated as latest rather than smuggled into a tag.
# The explicit `return 0` matters: callers run under `set -e`, and a bare failing
# [[ ]] would make `SUFFIX=$(spawnwp_wp_suffix latest)` abort the whole script —
# the very trap that made System → Refresh die with "Exited with code 2".
spawnwp_wp_suffix() {
  local wp="${1:-latest}"
  [[ "$wp" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]] && printf -- '-wp%s' "$wp"
  return 0
}

# The image tag MUST carry the WordPress version, because the image CONTENT does:
# the Dockerfile bakes WORDPRESS_VERSION into /usr/src/wordpress. Keyed on PHP
# alone, a `latest` site and a site pinning WP 7.0.1 shared one mutable tag: each
# deploy re-tagged it, the other's context hash stopped matching, and they
# rebuilt each other forever — and a site recreated with Down/Up could come back
# up on another site's WordPress core.
spawnwp_image_tag() {
  printf '%s:%s%s' "$PHP_IMAGE_REPO" "$1" "$(spawnwp_wp_suffix "${2:-latest}")"
}

# Stamped onto the image as org.spawnwp.context-hash and compared before reusing
# it, so a changed Dockerfile (a SpawnWP update) rebuilds while an unchanged one
# is reused. The WP version is folded in too: same reason as the tag.
spawnwp_context_hash() {
  local wp="${1:-latest}"
  {
    local f
    for f in "${SPAWNWP_IMAGE_INPUTS[@]}"; do
      sha256sum "docker/php/$f"
    done
    echo "wp=${wp}"
  } | sha256sum | cut -c1-12
}

# Drop everything from a project's docker/php that is not a build input, so junk
# sitting in the template can never ride along into a new site and de-sync its
# cache from every other site's.
spawnwp_prune_image_context() {
  local dir="$1/docker/php" keep name
  [ -d "$dir" ] || return 0
  # zz-site.ini is not a build input but IS needed at runtime (it is bind-mounted).
  keep=" ${SPAWNWP_IMAGE_INPUTS[*]} zz-site.ini "
  while IFS= read -r -d '' entry; do
    name=${entry##*/}
    [[ "$keep" == *" $name "* ]] || rm -rf "$entry"
  done < <(find "$dir" -mindepth 1 -maxdepth 1 -print0)
}

# CLI form, for callers that are not bash.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  case "${1:-}" in
    hash)   spawnwp_context_hash "${2:-latest}" ;;
    tag)    spawnwp_image_tag "${2:?php version}" "${3:-latest}" ;;
    suffix) spawnwp_wp_suffix "${2:-latest}" ;;
    *) echo "Usage: $0 hash <wp> | tag <php> <wp> | suffix <wp>" >&2; exit 1 ;;
  esac
fi
