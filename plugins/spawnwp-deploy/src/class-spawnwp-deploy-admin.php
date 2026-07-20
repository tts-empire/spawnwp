<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_Admin {
	public static function init(): void {
		add_action( 'admin_menu', array( __CLASS__, 'menu' ) );
		add_action( 'admin_init', array( __CLASS__, 'actions' ) );
		add_action( 'admin_enqueue_scripts', array( __CLASS__, 'enqueue_assets' ) );
		add_action( 'wp_ajax_spawnwp_deploy_step', array( __CLASS__, 'ajax_step' ) );
	}

	public static function menu(): void {
		add_management_page( 'SpawnWP Deploy', 'SpawnWP Deploy', 'manage_options', 'spawnwp-deploy', array( __CLASS__, 'page' ) );
	}

	public static function enqueue_assets( string $hook_suffix ): void {
		if ( 'tools_page_spawnwp-deploy' !== $hook_suffix ) {
			return;
		}
		wp_enqueue_style( 'spawnwp-deploy-admin', SPAWNWP_DEPLOY_URL . 'assets/admin.css', array(), SPAWNWP_DEPLOY_VERSION );
		wp_enqueue_script( 'spawnwp-deploy-admin', SPAWNWP_DEPLOY_URL . 'assets/admin.js', array(), SPAWNWP_DEPLOY_VERSION, true );
		wp_localize_script(
			'spawnwp-deploy-admin',
			'spawnwpDeployAdmin',
			array(
				'ajaxUrl'        => admin_url( 'admin-ajax.php' ),
				'deployNonce'    => wp_create_nonce( 'spawnwp_deploy_ajax' ),
				'blueprintNonce' => wp_create_nonce( 'spawnwp_blueprint_ajax' ),
			)
		);
	}

	public static function actions(): void {
		if ( ! current_user_can( 'manage_options' ) || empty( $_POST['spawnwp_deploy_action'] ) ) {
			return;
		}
		check_admin_referer( 'spawnwp_deploy_admin' );
		$action = sanitize_key( wp_unslash( $_POST['spawnwp_deploy_action'] ) );
		try {
			if ( 'generate_pairing' === $action ) {
				self::generate_pairing();
			} elseif ( 'connect' === $action ) {
				self::connect( sanitize_textarea_field( wp_unslash( $_POST['pairing_bundle'] ?? '' ) ) );
			} elseif ( 'revoke_local' === $action ) {
				self::revoke_local( sanitize_text_field( wp_unslash( $_POST['connection_id'] ?? '' ) ) );
			} elseif ( 'revoke_target' === $action ) {
				self::revoke_target( sanitize_text_field( wp_unslash( $_POST['connection_id'] ?? '' ) ) );
			} elseif ( 'connect_server' === $action ) {
				SpawnWP_Deploy_Blueprint::connect( sanitize_textarea_field( wp_unslash( $_POST['pairing_bundle'] ?? '' ) ) );
			} elseif ( 'revoke_server' === $action ) {
				SpawnWP_Deploy_Blueprint::revoke( sanitize_text_field( wp_unslash( $_POST['connection_id'] ?? '' ) ) );
			}
			self::redirect( 'success' );
		} catch ( Throwable $error ) {
			self::redirect( rawurlencode( $error->getMessage() ) );
		}
	}

	public static function page(): void {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		global $wpdb;
		SpawnWP_Deploy_Database::maintain_connections();
		// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- Read-only status message set by this plugin's redirect.
		$message     = isset( $_GET['spawnwp_message'] ) ? sanitize_text_field( wp_unslash( $_GET['spawnwp_message'] ) ) : '';
		$table       = SpawnWP_Deploy_Database::table( 'connections' );
		$connections = $wpdb->get_results( $wpdb->prepare( "SELECT * FROM %i WHERE role='source' AND status IN ('active','consumed') ORDER BY created_at DESC", $table ), ARRAY_A );
		$pending     = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM %i WHERE role='target' AND status='pending' ORDER BY created_at DESC LIMIT 1", $table ), ARRAY_A );
		$receivers   = $wpdb->get_results( $wpdb->prepare( "SELECT * FROM %i WHERE role='target' AND status='active' ORDER BY created_at DESC", $table ), ARRAY_A );
		$history     = $wpdb->get_results( $wpdb->prepare( "SELECT id,label,role,remote_url,status,updated_at FROM %i WHERE status IN ('expired','revoked') ORDER BY updated_at DESC LIMIT 10", $table ), ARRAY_A );
		$guard       = SpawnWP_Deploy_Guard::target_report();
		?>
		<div class="wrap spawnwp-deploy-wrap">
			<header class="spawnwp-brandbar">
				<span class="spawnwp-logo" aria-hidden="true"><span class="grid"><i></i><i></i><i class="ring"></i><i></i><i class="on"></i><i></i><i></i><i></i><i></i></span></span>
				<span class="spawnwp-word">Spawn<strong>WP</strong></span>
				<span class="spawnwp-role">Self-hosted</span>
			</header>
			<?php if ( $message ) : ?>
				<div class="notice <?php echo 'success' === $message ? 'notice-success' : 'notice-error'; ?> is-dismissible"><p><?php echo esc_html( 'success' === $message ? 'Action completed.' : rawurldecode( $message ) ); ?></p></div>
			<?php endif; ?>

			<?php SpawnWP_Deploy_Blueprint::render_panel(); ?>

			<details class="spawnwp-deploy-adv"<?php echo ( $connections || $receivers || $pending ) ? ' open' : ''; ?>>
				<summary><span class="spawnwp-adv-title">Publish a finished site elsewhere</span><span class="spawnwp-badge">Advanced</span></summary>
				<p class="spawnwp-muted">A one-time transfer of a finished site to a separate, empty WordPress install — not staging or synchronization, and separate from blueprints above.</p>

					<div class="spawnwp-subpanel">
						<h3>Send this site to an empty destination</h3>
						<p class="spawnwp-muted">Paste the short-lived connection key generated by the empty destination site.</p>
						<form method="post">
							<?php wp_nonce_field( 'spawnwp_deploy_admin' ); ?>
							<input type="hidden" name="spawnwp_deploy_action" value="connect">
							<textarea name="pairing_bundle" rows="4" class="large-text code" required placeholder="spawndeploy1:..."></textarea>
							<?php submit_button( 'Connect destination', 'primary', 'submit', false ); ?>
						</form>
					</div>

					<div class="spawnwp-subpanel">
						<h3>Connected destinations</h3>
						<?php
						if ( ! $connections ) :
							?>
							<p class="spawnwp-muted">No destination connected yet.</p><?php endif; ?>
						<?php foreach ( $connections as $connection ) : ?>
							<div class="spawnwp-connection">
								<div><strong><?php echo esc_html( ! empty( $connection['label'] ) ? $connection['label'] : $connection['remote_url'] ); ?></strong><br><code><?php echo esc_html( $connection['remote_url'] ); ?></code><br><small>Status: <?php echo esc_html( $connection['status'] ); ?></small></div>
								<div class="spawnwp-actions">
									<button class="button button-primary spawnwp-start" data-connection="<?php echo esc_attr( $connection['id'] ); ?>" <?php disabled( 'active' !== $connection['status'] ); ?>>Deploy to empty site</button>
									<?php $last_job = get_option( 'spawnwp_deploy_last_job_' . $connection['id'] ); if ( 'consumed' === $connection['status'] && $last_job ) : ?>
										<button class="button spawnwp-rollback" data-connection="<?php echo esc_attr( $connection['id'] ); ?>" data-job="<?php echo esc_attr( $last_job ); ?>">Rollback</button>
									<?php endif; ?>
									<form method="post">
										<?php wp_nonce_field( 'spawnwp_deploy_admin' ); ?>
										<input type="hidden" name="spawnwp_deploy_action" value="revoke_local"><input type="hidden" name="connection_id" value="<?php echo esc_attr( $connection['id'] ); ?>">
										<button class="button" type="submit">Remove</button>
									</form>
								</div>
							</div>
						<?php endforeach; ?>
						<pre id="spawnwp-log" hidden></pre>
					</div>
					<div class="spawnwp-subpanel">
						<h3>Receive a published site</h3>
						<?php if ( $guard['ok'] ) : ?>
							<p class="spawnwp-ok">This site is empty and ready to receive.</p>
							<form method="post">
								<?php wp_nonce_field( 'spawnwp_deploy_admin' ); ?>
								<input type="hidden" name="spawnwp_deploy_action" value="generate_pairing">
								<?php submit_button( 'Generate connection key', 'secondary', 'submit', false ); ?>
							</form>
							<?php if ( $pending && strtotime( $pending['pair_expires'] . ' UTC' ) > time() ) : ?>
								<textarea rows="4" class="large-text code spawnwp-select-on-click" readonly><?php echo esc_textarea( get_transient( 'spawnwp_deploy_bundle_' . $pending['id'] ) ); ?></textarea>
								<p class="spawnwp-muted">Expires <?php echo esc_html( gmdate( 'Y-m-d H:i:s', strtotime( $pending['pair_expires'] . ' UTC' ) ) ); ?> UTC.</p>
							<?php endif; ?>
							<?php foreach ( $receivers as $receiver ) : ?>
								<div class="spawnwp-receiver">
									<span>Connected source: <code><?php echo esc_html( $receiver['remote_url'] ); ?></code></span>
									<form method="post">
										<?php wp_nonce_field( 'spawnwp_deploy_admin' ); ?>
										<input type="hidden" name="spawnwp_deploy_action" value="revoke_target">
										<input type="hidden" name="connection_id" value="<?php echo esc_attr( $receiver['id'] ); ?>">
										<button class="button" type="submit">Revoke source</button>
									</form>
								</div>
							<?php endforeach; ?>
						<?php else : ?>
							<p class="spawnwp-bad">This site is not eligible as a destination:</p>
							<ul>
							<?php foreach ( $guard['issues'] as $issue ) : ?>
								<li><?php echo esc_html( $issue ); ?></li>
							<?php endforeach; ?>
							</ul>
						<?php endif; ?>
					</div>
				<?php if ( $history ) : ?>
					<div class="spawnwp-subpanel spawnwp-history">
						<h3>Connection history (<?php echo esc_html( count( $history ) ); ?>)</h3>
						<?php foreach ( $history as $item ) : ?>
							<div class="spawnwp-connection">
								<div><strong><?php echo esc_html( ! empty( $item['label'] ) ? $item['label'] : ( ! empty( $item['remote_url'] ) ? $item['remote_url'] : 'Unused pairing key' ) ); ?></strong><br><small><?php echo esc_html( ucfirst( $item['role'] ) . ' · ' . ucfirst( $item['status'] ) . ' · ' . gmdate( 'Y-m-d H:i:s', strtotime( $item['updated_at'] . ' UTC' ) ) . ' UTC' ); ?></small></div>
							</div>
						<?php endforeach; ?>
						<p class="spawnwp-muted">Expired and revoked entries are removed after 30 days when no deployment job references them.</p>
					</div>
				<?php endif; ?>
			</details>
		</div>
		<?php
	}

	private static function generate_pairing(): void {
		global $wpdb;
		$guard = SpawnWP_Deploy_Guard::target_report();
		if ( ! $guard['ok'] ) {
			throw new RuntimeException( implode( ' ', array_map( 'esc_html', $guard['issues'] ) ) );
		}
		$id      = SpawnWP_Deploy_Database::uuid();
		$token   = SpawnWP_Deploy_Crypto::random_token();
		$keys    = SpawnWP_Deploy_Crypto::generate_keypair();
		$expires = gmdate( 'Y-m-d H:i:s', time() + 15 * MINUTE_IN_SECONDS );
		$wpdb->insert(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'id'              => $id,
				'label'           => 'Pending source',
				'role'            => 'target',
				'remote_url'      => '',
				'public_key'      => '',
				'private_key'     => $keys['private'],
				'owner_user_id'   => get_current_user_id(),
				'pair_token_hash' => hash( 'sha256', $token ),
				'pair_expires'    => $expires,
				'status'          => 'pending',
				'created_at'      => current_time( 'mysql', true ),
				'updated_at'      => current_time( 'mysql', true ),
			)
		);
		$bundle_data = array(
			'version'           => 1,
			'target_url'        => untrailingslashit( home_url() ),
			'pairing_id'        => $id,
			'token'             => $token,
			'target_public_key' => $keys['public'],
			'expires'           => gmdate( 'c', strtotime( $expires . ' UTC' ) ),
		);
		$bundle      = 'spawndeploy1:' . rtrim( strtr( base64_encode( wp_json_encode( $bundle_data ) ), '+/', '-_' ), '=' );
		set_transient( 'spawnwp_deploy_bundle_' . $id, $bundle, 15 * MINUTE_IN_SECONDS );
		SpawnWP_Deploy_Database::audit( 'pairing_key_created', array(), $id );
	}

	private static function connect( string $bundle ): void {
		global $wpdb;
		if ( ! str_starts_with( $bundle, 'spawndeploy1:' ) ) {
			throw new RuntimeException( 'Connection key format is invalid.' );
		}
		$encoded  = substr( $bundle, strlen( 'spawndeploy1:' ) );
		$encoded .= str_repeat( '=', ( 4 - strlen( $encoded ) % 4 ) % 4 );
		$data     = json_decode( base64_decode( strtr( $encoded, '-_', '+/' ), true ), true, 512, JSON_THROW_ON_ERROR );
		if ( empty( $data['target_url'] ) || 'https' !== wp_parse_url( $data['target_url'], PHP_URL_SCHEME ) || strtotime( $data['expires'] ) < time() ) {
			throw new RuntimeException( 'Connection key is expired or does not use HTTPS.' );
		}
		$keys       = SpawnWP_Deploy_Crypto::generate_keypair();
		$secret     = SpawnWP_Deploy_Crypto::decrypt( $keys['private'] );
		$proof_data = 'pair|' . $data['pairing_id'] . '|' . $keys['public'] . '|' . untrailingslashit( home_url() );
		$payload    = array(
			'pairing_id'        => $data['pairing_id'],
			'token'             => $data['token'],
			'source_public_key' => $keys['public'],
			'source_url'        => untrailingslashit( home_url() ),
			'proof'             => base64_encode( sodium_crypto_sign_detached( $proof_data, $secret ) ),
		);
		$response   = wp_safe_remote_post(
			trailingslashit( $data['target_url'] ) . 'wp-json/' . SpawnWP_Deploy_REST::NS . '/pair',
			array(
				'timeout'   => 30,
				'sslverify' => true,
				'headers'   => array( 'Content-Type' => 'application/json' ),
				'body'      => wp_json_encode( $payload ),
			)
		);
		$body       = self::decode_response( $response );
		if ( ! hash_equals( $data['target_public_key'], $body['target_public_key'] ?? '' ) ) {
			throw new RuntimeException( 'Target key confirmation failed.' );
		}
		$wpdb->replace(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'id'            => $body['connection_id'],
				'label'         => wp_parse_url( $body['target_url'], PHP_URL_HOST ),
				'role'          => 'source',
				'remote_url'    => $body['target_url'],
				'public_key'    => $body['target_public_key'],
				'private_key'   => $keys['private'],
				'owner_user_id' => get_current_user_id(),
				'status'        => 'active',
				'created_at'    => current_time( 'mysql', true ),
				'updated_at'    => current_time( 'mysql', true ),
			)
		);
		SpawnWP_Deploy_Database::audit( 'destination_connected', array( 'target_url' => $body['target_url'] ), $body['connection_id'] );
	}

	public static function ajax_step(): void {
		check_ajax_referer( 'spawnwp_deploy_ajax', 'nonce' );
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_send_json_error( array( 'message' => 'Insufficient permissions.' ), 403 );
		}
		try {
			$connection = self::source_connection( sanitize_text_field( wp_unslash( $_POST['connection'] ?? '' ) ) );
			$op         = sanitize_key( wp_unslash( $_POST['op'] ?? '' ) );
			if ( 'preflight' === $op ) {
				$preflight             = self::remote( $connection, 'GET', '/preflight' );
				$preflight['warnings'] = SpawnWP_Deploy_Guard::compatibility_warnings( SpawnWP_Deploy_Guard::environment(), $preflight['environment'] ?? array() );
				wp_send_json_success( $preflight );
			}
			if ( 'prepare' === $op ) {
				$preflight = self::remote( $connection, 'GET', '/preflight' );
				if ( empty( $preflight['ok'] ) ) {
					throw new RuntimeException( implode( ' ', $preflight['issues'] ?? array( 'Target preflight failed.' ) ) );
				}
				$local    = SpawnWP_Deploy_Database::uuid();
				$manifest = SpawnWP_Deploy_Package::prepare( $local, $connection['remote_url'], $preflight['environment'] );
				$remote   = self::remote( $connection, 'POST', '/jobs', wp_json_encode( $manifest ) );
				$state    = array(
					'local'    => $local,
					'job'      => $remote['job_id'],
					'manifest' => $manifest,
					'next'     => 0,
				);
				update_option( 'spawnwp_deploy_source_job_' . $remote['job_id'], $state, false );
				update_option( 'spawnwp_deploy_last_job_' . $connection['id'], $remote['job_id'], false );
				wp_send_json_success( $state );
			}
			$job_id = sanitize_text_field( wp_unslash( $_POST['job'] ?? '' ) );
			$state  = get_option( 'spawnwp_deploy_source_job_' . $job_id );
			if ( ! is_array( $state ) || $state['job'] !== $job_id ) {
				throw new RuntimeException( 'Source deployment state was not found.' );
			}
			if ( 'transfer' === $op ) {
				$index = isset( $_POST['next'] ) ? absint( wp_unslash( $_POST['next'] ) ) : (int) $state['next'];
				$chunk = SpawnWP_Deploy_Package::chunk( $state['local'], $index, (int) $state['manifest']['chunk_size'] );
				self::remote(
					$connection,
					'PUT',
					'/jobs/' . rawurlencode( $job_id ) . '/chunks/' . $index,
					$chunk,
					array(
						'X-SpawnWP-Chunk-SHA256' => hash( 'sha256', $chunk ),
						'Content-Type'           => 'application/octet-stream',
					)
				);
				$state['next'] = $index + 1;
				update_option( 'spawnwp_deploy_source_job_' . $job_id, $state, false );
				wp_send_json_success( $state );
			}
			if ( 'stage' === $op ) {
				wp_send_json_success( self::remote( $connection, 'POST', '/jobs/' . rawurlencode( $job_id ) . '/stage', '{}' ) );
			}
			if ( 'activate' === $op ) {
				$result = self::remote( $connection, 'POST', '/jobs/' . rawurlencode( $job_id ) . '/activate', '{}' );
				self::mark_connection( $connection['id'], 'consumed' );
				SpawnWP_Deploy_Package::cleanup( $state['local'] );
				wp_send_json_success( $result );
			}
			if ( 'rollback' === $op ) {
				$result = self::remote( $connection, 'POST', '/jobs/' . rawurlencode( $job_id ) . '/rollback', '{}' );
				self::mark_connection( $connection['id'], 'active' );
				wp_send_json_success( $result );
			}
			throw new RuntimeException( 'Unknown deployment operation.' );
		} catch ( Throwable $error ) {
			wp_send_json_error( array( 'message' => $error->getMessage() ), 409 );
		}
	}

	private static function remote( array $connection, string $method, string $route, string $body = '', array $extra_headers = array() ): array {
		$path      = '/' . SpawnWP_Deploy_REST::NS . $route;
		$timestamp = time();
		$nonce     = SpawnWP_Deploy_Crypto::random_token( 18 );
		$headers   = array_merge(
			array(
				'Content-Type'         => 'application/json',
				'X-SpawnWP-Connection' => $connection['id'],
				'X-SpawnWP-Timestamp'  => (string) $timestamp,
				'X-SpawnWP-Nonce'      => $nonce,
				'X-SpawnWP-Signature'  => SpawnWP_Deploy_Crypto::sign( $connection['private_key'], $method, $path, $timestamp, $nonce, $body ),
			),
			$extra_headers
		);
		$response  = wp_safe_remote_request(
			trailingslashit( $connection['remote_url'] ) . 'wp-json' . $path,
			array(
				'method'      => $method,
				'timeout'     => 120,
				'sslverify'   => true,
				'redirection' => 0,
				'headers'     => $headers,
				'body'        => $body,
				'data_format' => 'body',
			)
		);
		return self::decode_response( $response );
	}

	private static function decode_response( $response ): array {
		if ( is_wp_error( $response ) ) {
			throw new RuntimeException( esc_html( $response->get_error_message() ) );
		}
		$status = wp_remote_retrieve_response_code( $response );
		$data   = json_decode( wp_remote_retrieve_body( $response ), true );
		if ( $status < 200 || $status >= 300 ) {
			$message = isset( $data['message'] ) ? esc_html( (string) $data['message'] ) : 'Remote request failed with HTTP ' . absint( $status );
			throw new RuntimeException( esc_html( $message ) );
		}
		return is_array( $data ) ? $data : array();
	}

	private static function source_connection( string $id ): array {
		global $wpdb;
		$row = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM %i WHERE id=%s AND role='source' AND status IN ('active','consumed')", SpawnWP_Deploy_Database::table( 'connections' ), $id ), ARRAY_A );
		if ( ! $row ) {
			throw new RuntimeException( 'Active destination connection not found.' );
		}
		return $row;
	}

	private static function revoke_local( string $id ): void {
		global $wpdb;
		$connection = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM %i WHERE id=%s AND role='source'", SpawnWP_Deploy_Database::table( 'connections' ), $id ), ARRAY_A );
		if ( ! $connection ) {
			throw new RuntimeException( 'Source connection not found.' );
		}
		if ( in_array( $connection['status'], array( 'active', 'consumed' ), true ) ) {
			try {
				self::remote( $connection, 'DELETE', '/connection' );
			} catch ( Throwable $error ) {
				SpawnWP_Deploy_Database::audit( 'remote_revoke_unreachable', array( 'error' => $error->getMessage() ), $id );
			}
		}
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'status'      => 'revoked',
				'private_key' => '',
				'updated_at'  => current_time( 'mysql', true ),
			),
			array( 'id' => $id )
		);
	}

	private static function revoke_target( string $id ): void {
		global $wpdb;
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'status'      => 'revoked',
				'private_key' => '',
				'updated_at'  => current_time( 'mysql', true ),
			),
			array(
				'id'   => $id,
				'role' => 'target',
			)
		);
		SpawnWP_Deploy_Database::audit( 'connection_revoked_locally', array(), $id );
	}

	private static function mark_connection( string $id, string $status ): void {
		global $wpdb;
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'status'     => $status,
				'updated_at' => current_time( 'mysql', true ),
			),
			array( 'id' => $id )
		);
	}

	private static function redirect( string $message ): void {
		wp_safe_redirect(
			add_query_arg(
				array(
					'page'            => 'spawnwp-deploy',
					'spawnwp_message' => $message,
				),
				admin_url( 'tools.php' )
			)
		);
		exit;
	}
}
