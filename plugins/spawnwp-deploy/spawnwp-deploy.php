<?php
/**
 * Plugin Name: SpawnWP Deploy
 * Description: Capture a configured site as a reusable SpawnWP blueprint, and optionally publish a finished site once to a separate, empty WordPress install.
 * Version: 0.3.0-dev
 * Requires at least: 6.8
 * Requires PHP: 8.1
 * Author: SpawnWP
 * License: GPL-2.0-or-later
 * Text Domain: spawnwp-deploy
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'SPAWNWP_DEPLOY_VERSION', '0.3.0-dev' );
define( 'SPAWNWP_DEPLOY_FILE', __FILE__ );
define( 'SPAWNWP_DEPLOY_DIR', plugin_dir_path( __FILE__ ) );

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
