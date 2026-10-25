The WordPress entrypoint and wp-config-docker.php are derived from docker-library/wordpress,
commit c8b214cc10dae78c4a7c9e1d98ee104e0f027441:
https://github.com/docker-library/wordpress/tree/c8b214cc10dae78c4a7c9e1d98ee104e0f027441

SpawnWP changes the root entrypoint's extraction step to run tar as the destination
owner, using existing SETUID/SETGID capabilities instead of adding FOWNER.

PHP 7.4 packages and source patches are supplied by DEB.SURY.ORG. Their exact
versions, upstream filenames and SHA-256 values are recorded in runtime-lock.json.
The downloaded binary packages must also appear in the authenticated APT index.
Source archives used for the additional PCNTL module are checked in build-pcntl.sh.
The signing key fingerprint is 15058500A0235D97F5D10063B188E2B695BD4743.
Package copyright/license files remain distributed in the image.

Node.js, Composer, WP-CLI and Composer dependencies retain their upstream licenses.
The explicit download checksums are in tools-lock.json; dependency versions and
licenses are recorded in tooling-composer.lock.
