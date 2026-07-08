<?php

if ( ! defined( 'ABSPATH' ) ) {
	fwrite( STDERR, "Run with wp eval-file.\n" );
	exit( 1 );
}

$failures = array();
$assert   = static function ( bool $condition, string $message ) use ( &$failures ): void {
	if ( $condition ) {
		echo "PASS: {$message}\n";
	} else {
		echo "FAIL: {$message}\n";
		$failures[] = $message;
	}
};

$keys      = SpawnWP_Deploy_Crypto::generate_keypair();
$timestamp = time();
$nonce     = SpawnWP_Deploy_Crypto::random_token( 18 );
$path      = '/spawnwp-deploy/v1/preflight';
$signature = SpawnWP_Deploy_Crypto::sign( $keys['private'], 'GET', $path, $timestamp, $nonce, '' );
$assert( SpawnWP_Deploy_Crypto::verify( $keys['public'], $signature, 'GET', $path, $timestamp, $nonce, '' ), 'Ed25519 sign/verify round trip' );
$assert( ! SpawnWP_Deploy_Crypto::verify( $keys['public'], $signature, 'GET', $path, $timestamp, $nonce, 'changed' ), 'Body tampering is rejected' );
$assert( ! SpawnWP_Deploy_Crypto::verify( $keys['public'], $signature, 'GET', $path, $timestamp - 301, $nonce, '' ), 'Expired request timestamps are rejected' );

$routes = rest_get_server()->get_routes();
$assert( isset( $routes['/spawnwp-deploy/v1/pair'] ), 'Pairing route registered' );
$assert( isset( $routes['/spawnwp-deploy/v1/jobs/(?P<id>[a-f0-9-]+)/activate'] ), 'Activation route registered' );

global $wpdb;
foreach ( SpawnWP_Deploy_Database::control_tables() as $table ) {
	$assert( (bool) $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) ), "Control table exists: {$table}" );
}

$environment = SpawnWP_Deploy_Guard::environment();
$assert( ! empty( $environment['wordpress'] ), 'WordPress version detected' );
$assert( ! empty( $environment['free_bytes'] ), 'Filesystem free space detected' );
$assert( true === $environment['sodium'] && true === $environment['zip'], 'Required PHP extensions detected' );
$warnings = SpawnWP_Deploy_Guard::compatibility_warnings(
	array( 'wordpress' => '7.0', 'php' => '8.4' ),
	array( 'wordpress' => '6.9', 'php' => '8.3' )
);
$assert( 2 === count( $warnings ), 'WordPress and PHP mismatches produce soft warnings' );
$assert( file_exists( WPMU_PLUGIN_DIR . '/spawnwp-deploy-loader.php' ), 'Recovery MU loader installed' );

$assert( class_exists( 'SpawnWP_Deploy_Blueprint' ), 'Blueprint capture class loaded' );
$assert( false !== has_action( 'wp_ajax_spawnwp_blueprint_step' ), 'Blueprint capture ajax handler registered' );
$inventory = SpawnWP_Deploy_Guard::plugin_inventory();
$assert( isset( $inventory['wporg'], $inventory['premium'] ) && is_array( $inventory['wporg'] ) && is_array( $inventory['premium'] ), 'Plugin inventory classifies wp.org and premium plugins' );

$assert( is_bool( SpawnWP_Deploy_Guard::is_cockpit() ), 'Environment detection returns a boolean' );
$assert( defined( 'SPAWNWP_DEPLOY_HEALTHCHECK_URL' ) === SpawnWP_Deploy_Guard::is_cockpit(), 'Cockpit detection tracks the injected constant' );

ob_start();
SpawnWP_Deploy_Blueprint::render_panel();
$panel = ob_get_clean();
$assert( str_contains( $panel, 'Create a SpawnWP blueprint from this site' ), 'Blueprint hero is the primary panel' );

$secret_plaintext = SpawnWP_Deploy_Crypto::random_token( 32 );
$encrypted        = SpawnWP_Deploy_Crypto::encrypt( $secret_plaintext );
$assert( $encrypted !== $secret_plaintext && SpawnWP_Deploy_Crypto::decrypt( $encrypted ) === $secret_plaintext, 'Key storage encrypt/decrypt round trip' );

foreach ( array( 'class-blueprint.php', 'class-admin.php' ) as $source_file ) {
	$src = (string) file_get_contents( SPAWNWP_DEPLOY_DIR . 'src/' . $source_file );
	$assert( ! str_contains( $src, 'wp_remote_post(' ) && ! str_contains( $src, 'wp_remote_request(' ), "Outbound requests use the SSRF-guarded API in {$source_file}" );
}

// Package exclusions must be kind-aware: plugin source directories named
// Upgrade/Cache/Backup are legitimate code and must survive, while user-content
// temp/cache dirs are still stripped from uploads. Regression for the MetaBox
// AIO deploy fatal (MBB\Upgrade\Manager dropped from meta-box-builder).
$excluded_path = new ReflectionMethod( 'SpawnWP_Deploy_Package', 'excluded_path' );
$excluded_path->setAccessible( true );
$excluded = static function ( string $path, string $kind ) use ( $excluded_path ): bool {
	return (bool) $excluded_path->invoke( null, $path, $kind );
};
$assert( false === $excluded( 'meta-box-aio/vendor/meta-box/meta-box-builder/src/Upgrade/Manager.php', 'plugins' ), 'Plugin source dir "Upgrade/" is kept (MetaBox AIO regression)' );
$assert( false === $excluded( 'acme/src/Cache/Store.php', 'plugins' ), 'Plugin source dir "Cache/" is kept' );
$assert( false === $excluded( 'acme/inc/Backup/Job.php', 'plugins' ), 'Plugin source dir "Backup/" is kept' );
$assert( true === $excluded( '2026/07/cache/thumb.jpg', 'uploads' ), 'Uploads cache/ is still excluded' );
$assert( true === $excluded( 'backups/dump.zip', 'uploads' ), 'Uploads backups/ is still excluded' );
$assert( true === $excluded( 'site/upgrade/tmp.txt', 'uploads' ), 'Uploads upgrade/ is still excluded' );
$assert( true === $excluded( 'acme/.git/config', 'plugins' ), 'Dev artefact .git is excluded everywhere' );
$assert( true === $excluded( 'acme/node_modules/x.js', 'plugins' ), 'Dev artefact node_modules is excluded everywhere' );
$assert( true === $excluded( 'acme/debug.log', 'plugins' ), 'debug.log is excluded everywhere' );

if ( $failures ) {
	throw new RuntimeException( count( $failures ) . ' SpawnWP Deploy smoke test(s) failed.' );
}

echo "All SpawnWP Deploy smoke tests passed.\n";
