<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_REST {
	const NS = 'spawnwp-deploy/v1';

	public static function init(): void {
		add_action( 'rest_api_init', array( __CLASS__, 'routes' ) );
	}

	public static function routes(): void {
		register_rest_route(
			self::NS,
			'/pair',
			array(
				'methods'             => 'POST',
				'callback'            => array( __CLASS__, 'pair' ),
				'permission_callback' => '__return_true',
			)
		);
		register_rest_route(
			self::NS,
			'/preflight',
			array(
				'methods'             => 'GET',
				'callback'            => array( __CLASS__, 'preflight' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/jobs',
			array(
				'methods'             => 'POST',
				'callback'            => array( __CLASS__, 'create_job' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/jobs/(?P<id>[a-f0-9-]+)/chunks/(?P<index>\d+)',
			array(
				'methods'             => 'PUT',
				'callback'            => array( __CLASS__, 'chunk' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/jobs/(?P<id>[a-f0-9-]+)/stage',
			array(
				'methods'             => 'POST',
				'callback'            => array( __CLASS__, 'stage' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/jobs/(?P<id>[a-f0-9-]+)/activate',
			array(
				'methods'             => 'POST',
				'callback'            => array( __CLASS__, 'activate' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/jobs/(?P<id>[a-f0-9-]+)',
			array(
				'methods'             => 'GET',
				'callback'            => array( __CLASS__, 'status' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/jobs/(?P<id>[a-f0-9-]+)/rollback',
			array(
				'methods'             => 'POST',
				'callback'            => array( __CLASS__, 'rollback' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
		register_rest_route(
			self::NS,
			'/connection',
			array(
				'methods'             => 'DELETE',
				'callback'            => array( __CLASS__, 'revoke' ),
				'permission_callback' => array( __CLASS__, 'authorize' ),
			)
		);
	}

	public static function pair( WP_REST_Request $request ) {
		global $wpdb;
		$data = $request->get_json_params();
		foreach ( array( 'pairing_id', 'token', 'source_public_key', 'source_url', 'proof' ) as $required ) {
			if ( empty( $data[ $required ] ) ) {
				return new WP_Error( 'spawnwp_pair_invalid', 'Invalid pairing payload.', array( 'status' => 400 ) );
			}
		}
		$table = SpawnWP_Deploy_Database::table( 'connections' );
		$row   = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM {$table} WHERE id=%s AND role='target'", sanitize_text_field( $data['pairing_id'] ) ), ARRAY_A );
		if ( ! $row || 'pending' !== $row['status'] || strtotime( $row['pair_expires'] . ' UTC' ) < time() ) {
			return new WP_Error( 'spawnwp_pair_expired', 'Pairing key is invalid or expired.', array( 'status' => 403 ) );
		}
		if ( ! hash_equals( $row['pair_token_hash'], hash( 'sha256', (string) $data['token'] ) ) ) {
			return new WP_Error( 'spawnwp_pair_denied', 'Pairing key is invalid or expired.', array( 'status' => 403 ) );
		}
		$proof_data = 'pair|' . $row['id'] . '|' . $data['source_public_key'] . '|' . untrailingslashit( $data['source_url'] );
		$public     = base64_decode( $data['source_public_key'], true );
		$proof      = base64_decode( $data['proof'], true );
		if ( false === $public || false === $proof || ! sodium_crypto_sign_verify_detached( $proof, $proof_data, $public ) ) {
			return new WP_Error( 'spawnwp_pair_proof', 'Pairing proof is invalid.', array( 'status' => 403 ) );
		}
		$guard = SpawnWP_Deploy_Guard::target_report();
		if ( ! $guard['ok'] ) {
			return new WP_Error( 'spawnwp_target_not_empty', implode( ' ', $guard['issues'] ), array( 'status' => 409 ) );
		}
		$wpdb->update(
			$table,
			array(
				'remote_url'      => esc_url_raw( untrailingslashit( $data['source_url'] ) ),
				'public_key'      => sanitize_text_field( $data['source_public_key'] ),
				'pair_token_hash' => '',
				'pair_expires'    => null,
				'status'          => 'active',
				'updated_at'      => current_time( 'mysql', true ),
			),
			array( 'id' => $row['id'] )
		);
		$response = array(
			'connection_id'     => $row['id'],
			'target_url'        => untrailingslashit( home_url() ),
			'target_public_key' => self::public_from_private( $row['private_key'] ),
			'environment'       => $guard['environment'],
		);
		SpawnWP_Deploy_Database::audit( 'connection_paired', array( 'source_url' => $data['source_url'] ), $row['id'] );
		return rest_ensure_response( $response );
	}

	public static function authorize( WP_REST_Request $request ) {
		global $wpdb;
		$id        = sanitize_text_field( (string) $request->get_header( 'x-spawnwp-connection' ) );
		$timestamp = (int) $request->get_header( 'x-spawnwp-timestamp' );
		$nonce     = sanitize_text_field( (string) $request->get_header( 'x-spawnwp-nonce' ) );
		$signature = (string) $request->get_header( 'x-spawnwp-signature' );
		if ( ! $id || ! $timestamp || strlen( $nonce ) < 16 || ! $signature ) {
			return new WP_Error( 'spawnwp_auth_missing', 'Signed connection headers are required.', array( 'status' => 401 ) );
		}
		$table    = SpawnWP_Deploy_Database::table( 'connections' );
		$row      = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM {$table} WHERE id=%s AND role='target' AND status='active'", $id ), ARRAY_A );
		$raw_body = (string) $request->get_body();
		if ( ! $row || ! SpawnWP_Deploy_Crypto::verify( $row['public_key'], $signature, $request->get_method(), $request->get_route(), $timestamp, $nonce, $raw_body ) ) {
			return new WP_Error( 'spawnwp_auth_invalid', 'Request signature is invalid.', array( 'status' => 401 ) );
		}
		$nonces = SpawnWP_Deploy_Database::table( 'nonces' );
		$wpdb->query( $wpdb->prepare( "DELETE FROM {$nonces} WHERE created_at < %s", gmdate( 'Y-m-d H:i:s', time() - 600 ) ) );
		$inserted = $wpdb->query(
			$wpdb->prepare(
				"INSERT IGNORE INTO {$nonces} (connection_id,nonce_hash,created_at) VALUES (%s,%s,%s)",
				$id,
				hash( 'sha256', $nonce ),
				current_time( 'mysql', true )
			)
		);
		if ( 1 !== $inserted ) {
			return new WP_Error( 'spawnwp_auth_replay', 'Request nonce has already been used.', array( 'status' => 409 ) );
		}
		$request->set_param( '_spawnwp_connection', $row );
		return true;
	}

	public static function preflight( WP_REST_Request $request ): WP_REST_Response {
		return rest_ensure_response( SpawnWP_Deploy_Guard::target_report() );
	}

	public static function create_job( WP_REST_Request $request ) {
		$connection = $request->get_param( '_spawnwp_connection' );
		$manifest   = $request->get_json_params();
		try {
			self::validate_manifest( $manifest );
			$guard = SpawnWP_Deploy_Guard::target_report();
			if ( ! $guard['ok'] ) {
				throw new RuntimeException( implode( ' ', $guard['issues'] ) );
			}
			return rest_ensure_response( array( 'job_id' => SpawnWP_Deploy_Receiver::create_job( $connection['id'], $manifest ) ) );
		} catch ( Throwable $error ) {
			return new WP_Error( 'spawnwp_job_rejected', $error->getMessage(), array( 'status' => 409 ) );
		}
	}

	public static function chunk( WP_REST_Request $request ) {
		try {
			$job = self::job( $request );
			return rest_ensure_response( SpawnWP_Deploy_Receiver::receive_chunk( $job, (int) $request['index'], $request->get_body(), (string) $request->get_header( 'x-spawnwp-chunk-sha256' ) ) );
		} catch ( Throwable $error ) {
			return new WP_Error( 'spawnwp_chunk_rejected', $error->getMessage(), array( 'status' => 409 ) );
		}
	}

	public static function stage( WP_REST_Request $request ) {
		try {
			return rest_ensure_response( SpawnWP_Deploy_Receiver::stage( self::job( $request ) ) );
		} catch ( Throwable $error ) {
			return new WP_Error( 'spawnwp_stage_failed', $error->getMessage(), array( 'status' => 409 ) );
		}
	}

	public static function activate( WP_REST_Request $request ) {
		try {
			$connection = $request->get_param( '_spawnwp_connection' );
			return rest_ensure_response( SpawnWP_Deploy_Receiver::activate( self::job( $request ), (int) $connection['owner_user_id'] ) );
		} catch ( Throwable $error ) {
			return new WP_Error( 'spawnwp_activation_failed', $error->getMessage(), array( 'status' => 500 ) );
		}
	}

	public static function status( WP_REST_Request $request ) {
		$job = self::job( $request );
		unset( $job['manifest'] );
		return rest_ensure_response( $job );
	}

	public static function rollback( WP_REST_Request $request ) {
		try {
			return rest_ensure_response( SpawnWP_Deploy_Receiver::rollback( self::job( $request ) ) );
		} catch ( Throwable $error ) {
			return new WP_Error( 'spawnwp_rollback_failed', $error->getMessage(), array( 'status' => 409 ) );
		}
	}

	public static function revoke( WP_REST_Request $request ): WP_REST_Response {
		global $wpdb;
		$connection = $request->get_param( '_spawnwp_connection' );
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'status'      => 'revoked',
				'private_key' => '',
				'updated_at'  => current_time( 'mysql', true ),
			),
			array( 'id' => $connection['id'] )
		);
		SpawnWP_Deploy_Database::audit( 'connection_revoked', array(), $connection['id'] );
		return rest_ensure_response( array( 'status' => 'revoked' ) );
	}

	private static function job( WP_REST_Request $request ): array {
		global $wpdb;
		$connection = $request->get_param( '_spawnwp_connection' );
		$job        = $wpdb->get_row(
			$wpdb->prepare( 'SELECT * FROM ' . SpawnWP_Deploy_Database::table( 'jobs' ) . ' WHERE id=%s AND connection_id=%s', sanitize_text_field( $request['id'] ), $connection['id'] ),
			ARRAY_A
		);
		if ( ! $job ) {
			throw new RuntimeException( 'Deployment job not found.' );
		}
		return $job;
	}

	private static function validate_manifest( array $manifest ): void {
		foreach ( array( 'format', 'source_url', 'target_url', 'wordpress', 'php', 'archive_bytes', 'archive_sha256', 'chunk_size', 'chunk_count', 'source_prefix' ) as $field ) {
			if ( ! isset( $manifest[ $field ] ) ) {
				throw new RuntimeException( 'Manifest is missing ' . $field );
			}
		}
		if ( 1 !== (int) $manifest['format'] || (int) $manifest['archive_bytes'] > SpawnWP_Deploy_Package::MAX_BYTES || ! hash_equals( untrailingslashit( home_url() ), untrailingslashit( $manifest['target_url'] ) ) ) {
			throw new RuntimeException( 'Manifest target, version, or size is invalid.' );
		}
	}

	private static function public_from_private( string $encrypted ): string {
		$secret = SpawnWP_Deploy_Crypto::decrypt( $encrypted );
		return base64_encode( sodium_crypto_sign_publickey_from_secretkey( $secret ) );
	}
}
