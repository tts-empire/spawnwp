<?php
/**
 * Plugin Name: SpawnWP Deploy Recovery Loader
 * Description: Keeps the SpawnWP Deploy receiver available during activation recovery.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

$spawnwp_deploy_main = WP_PLUGIN_DIR . '/spawnwp-deploy/spawnwp-deploy.php';
if ( file_exists( $spawnwp_deploy_main ) && ! defined( 'SPAWNWP_DEPLOY_FILE' ) ) {
	require_once $spawnwp_deploy_main;
}
