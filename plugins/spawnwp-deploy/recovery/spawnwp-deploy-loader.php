<?php
/**
 * Plugin Name: SpawnWP Deploy Recovery Loader
 * Description: Keeps the SpawnWP Deploy receiver available during activation recovery.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

$spawnwp_deploy_basename = get_option( 'spawnwp_deploy_plugin_basename', '' );
if ( is_string( $spawnwp_deploy_basename ) && preg_match( '#^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\.php$#', $spawnwp_deploy_basename ) ) {
	$spawnwp_deploy_main = trailingslashit( WP_PLUGIN_DIR ) . $spawnwp_deploy_basename;
	if ( file_exists( $spawnwp_deploy_main ) && ! defined( 'SPAWNWP_DEPLOY_FILE' ) ) {
		require_once $spawnwp_deploy_main;
	}
}
