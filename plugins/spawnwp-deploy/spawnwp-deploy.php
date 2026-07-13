<?php
/**
 * Plugin Name: SpawnWP Deploy
 * Description: Capture a configured site as a reusable SpawnWP blueprint, and optionally publish a finished site once to a separate, empty WordPress install.
 * Version: 0.3.4-dev
 * Requires at least: 6.8
 * Requires PHP: 7.4
 * Author: SpawnWP
 * License: GPL-2.0-or-later
 * Text Domain: spawnwp-deploy
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'SPAWNWP_DEPLOY_VERSION', '0.3.4-dev' );
define( 'SPAWNWP_DEPLOY_FILE', __FILE__ );
define( 'SPAWNWP_DEPLOY_DIR', plugin_dir_path( __FILE__ ) );

// PHP 7.4 support: WordPress core (>= 5.9) already polyfills these PHP 8.0 string
// helpers, and the plugin requires WP 6.8. These guarded fallbacks make the plugin
// self-sufficient on PHP 7.4 even outside a full WP bootstrap; function_exists()
// guards mean whichever definition loads first wins (no redeclare fatal).
if ( ! function_exists( 'str_starts_with' ) ) {
	function str_starts_with( $haystack, $needle ) {
		return 0 === strncmp( $haystack, $needle, strlen( $needle ) );
	}
}
if ( ! function_exists( 'str_contains' ) ) {
	function str_contains( $haystack, $needle ) {
		return '' === $needle || false !== strpos( $haystack, $needle );
	}
}

require_once SPAWNWP_DEPLOY_DIR . 'src/class-database.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-crypto.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-guard.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-package.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-receiver.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-rest.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-blueprint.php';
require_once SPAWNWP_DEPLOY_DIR . 'src/class-admin.php';

register_activation_hook( __FILE__, array( 'SpawnWP_Deploy_Database', 'activate' ) );
register_deactivation_hook( __FILE__, array( 'SpawnWP_Deploy_Database', 'deactivate' ) );
add_action( 'spawnwp_deploy_cleanup', array( 'SpawnWP_Deploy_Receiver', 'cleanup_expired' ) );

add_action(
	'plugins_loaded',
	static function () {
		if ( ! extension_loaded( 'sodium' ) || ! class_exists( 'ZipArchive' ) ) {
			add_action(
				'admin_notices',
				static function () {
					echo '<div class="notice notice-error"><p>' . esc_html__( 'SpawnWP Deploy requires the PHP sodium and zip extensions.', 'spawnwp-deploy' ) . '</p></div>';
				}
			);
			return;
		}

		SpawnWP_Deploy_REST::init();
		SpawnWP_Deploy_Admin::init();
		SpawnWP_Deploy_Blueprint::init();
	}
);
