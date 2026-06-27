<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_Database {
	const SCHEMA_VERSION = 1;

	public static function table( string $name ): string {
		global $wpdb;
		return $wpdb->prefix . 'spawnwp_deploy_' . $name;
	}

	public static function control_tables(): array {
		return array(
			self::table( 'connections' ),
			self::table( 'jobs' ),
			self::table( 'nonces' ),
			self::table( 'audit' ),
		);
	}

	public static function activate(): void {
		global $wpdb;
		require_once ABSPATH . 'wp-admin/includes/upgrade.php';

		$charset     = $wpdb->get_charset_collate();
		$connections = self::table( 'connections' );
		$jobs        = self::table( 'jobs' );
		$nonces      = self::table( 'nonces' );
		$audit       = self::table( 'audit' );

		dbDelta(
			"CREATE TABLE {$connections} (
			id char(36) NOT NULL,
			label varchar(191) NOT NULL DEFAULT '',
			role varchar(16) NOT NULL,
			remote_url text NOT NULL,
			public_key text NOT NULL,
			private_key text NOT NULL,
			owner_user_id bigint unsigned NOT NULL DEFAULT 0,
			pair_token_hash char(64) NOT NULL DEFAULT '',
			pair_expires datetime NULL,
			status varchar(24) NOT NULL DEFAULT 'pending',
			created_at datetime NOT NULL,
			updated_at datetime NOT NULL,
			PRIMARY KEY  (id),
			KEY status (status)
		) {$charset};"
		);

		dbDelta(
			"CREATE TABLE {$jobs} (
			id char(36) NOT NULL,
			connection_id char(36) NOT NULL,
			state varchar(24) NOT NULL,
			manifest longtext NULL,
			total_bytes bigint unsigned NOT NULL DEFAULT 0,
			received_bytes bigint unsigned NOT NULL DEFAULT 0,
			error_text text NULL,
			created_at datetime NOT NULL,
			updated_at datetime NOT NULL,
			completed_at datetime NULL,
			PRIMARY KEY  (id),
			KEY connection_id (connection_id),
			KEY state (state)
		) {$charset};"
		);

		dbDelta(
			"CREATE TABLE {$nonces} (
			connection_id char(36) NOT NULL,
			nonce_hash char(64) NOT NULL,
			created_at datetime NOT NULL,
			PRIMARY KEY  (connection_id, nonce_hash),
			KEY created_at (created_at)
		) {$charset};"
		);

		dbDelta(
			"CREATE TABLE {$audit} (
			id bigint unsigned NOT NULL AUTO_INCREMENT,
			connection_id char(36) NULL,
			job_id char(36) NULL,
			event varchar(64) NOT NULL,
			details text NULL,
			created_at datetime NOT NULL,
			PRIMARY KEY  (id),
			KEY connection_id (connection_id),
			KEY job_id (job_id),
			KEY created_at (created_at)
		) {$charset};"
		);

		update_option( 'spawnwp_deploy_schema_version', self::SCHEMA_VERSION, false );
		self::install_mu_loader();
		if ( ! wp_next_scheduled( 'spawnwp_deploy_cleanup' ) ) {
			wp_schedule_event( time() + HOUR_IN_SECONDS, 'daily', 'spawnwp_deploy_cleanup' );
		}
	}

	public static function deactivate(): void {
		wp_clear_scheduled_hook( 'spawnwp_deploy_cleanup' );
		// Connections and rollback state intentionally survive deactivation.
	}

	public static function install_mu_loader(): bool {
		$dir = defined( 'WPMU_PLUGIN_DIR' ) ? WPMU_PLUGIN_DIR : WP_CONTENT_DIR . '/mu-plugins';
		if ( ! wp_mkdir_p( $dir ) ) {
			return false;
		}
		$source = SPAWNWP_DEPLOY_DIR . 'recovery/spawnwp-deploy-loader.php';
		$target = trailingslashit( $dir ) . 'spawnwp-deploy-loader.php';
		return copy( $source, $target );
	}

	public static function audit( string $event, array $details = array(), ?string $connection_id = null, ?string $job_id = null ): void {
		global $wpdb;
		$wpdb->insert(
			self::table( 'audit' ),
			array(
				'connection_id' => $connection_id,
				'job_id'        => $job_id,
				'event'         => $event,
				'details'       => $details ? wp_json_encode( $details ) : null,
				'created_at'    => current_time( 'mysql', true ),
			),
			array( '%s', '%s', '%s', '%s', '%s' )
		);
	}

	public static function maintain_connections(): void {
		global $wpdb;
		$table   = self::table( 'connections' );
		$now     = current_time( 'mysql', true );
		$expired = $wpdb->get_col( $wpdb->prepare( "SELECT id FROM {$table} WHERE status='pending' AND pair_expires IS NOT NULL AND pair_expires < %s", $now ) );
		foreach ( $expired as $id ) {
			$wpdb->update(
				$table,
				array( 'status' => 'expired', 'private_key' => '', 'pair_token_hash' => '', 'updated_at' => $now ),
				array( 'id' => $id ),
				array( '%s', '%s', '%s', '%s' ),
				array( '%s' )
			);
			delete_transient( 'spawnwp_deploy_bundle_' . $id );
			self::audit( 'pairing_key_expired', array(), $id );
		}

		$jobs   = self::table( 'jobs' );
		$cutoff = gmdate( 'Y-m-d H:i:s', time() - 30 * DAY_IN_SECONDS );
		$wpdb->query(
			$wpdb->prepare(
				"DELETE c FROM {$table} c LEFT JOIN {$jobs} j ON j.connection_id=c.id WHERE c.status IN ('expired','revoked') AND c.updated_at < %s AND j.id IS NULL",
				$cutoff
			)
		);
	}

	public static function uuid(): string {
		return wp_generate_uuid4();
	}
}
