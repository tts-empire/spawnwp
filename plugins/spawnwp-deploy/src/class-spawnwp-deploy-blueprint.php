<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Capture this site as a SpawnWP content blueprint and push it to the
 * owner's SpawnWP server (/api/ingest/*), signed with the same Ed25519
 * request format used by site-to-site deploys.
 */
final class SpawnWP_Deploy_Blueprint {
	const SUPPORTED_INGEST_FORMAT = 1;
	const PLACEHOLDER_URL         = 'https://blueprint.spawnwp.invalid';
	const PHP_CHOICES             = array( '7.4', '8.2', '8.3', '8.4' );

	public static function init(): void {
		add_action( 'wp_ajax_spawnwp_blueprint_step', array( __CLASS__, 'ajax_step' ) );
	}

	public static function connect( string $bundle ): void {
		global $wpdb;
		if ( ! str_starts_with( $bundle, 'spawnbp1:' ) ) {
			throw new RuntimeException( 'Server pairing code format is invalid.' );
		}
		$encoded  = substr( $bundle, strlen( 'spawnbp1:' ) );
		$encoded .= str_repeat( '=', ( 4 - strlen( $encoded ) % 4 ) % 4 );
		$data     = json_decode( base64_decode( strtr( $encoded, '-_', '+/' ), true ), true, 512, JSON_THROW_ON_ERROR );
		if ( empty( $data['server_url'] ) || 'https' !== wp_parse_url( $data['server_url'], PHP_URL_SCHEME ) || (int) ( $data['expires'] ?? 0 ) < time() ) {
			throw new RuntimeException( 'Server pairing code is expired or does not use HTTPS.' );
		}
		$keys        = SpawnWP_Deploy_Crypto::generate_keypair();
		$secret      = SpawnWP_Deploy_Crypto::decrypt( $keys['private'] );
		$source_host = untrailingslashit( home_url() );
		$proof_data  = 'pair|' . $data['pairing_id'] . '|' . $keys['public'] . '|' . $source_host;
		$payload     = array(
			'pairing_id'        => $data['pairing_id'],
			'token'             => $data['token'],
			'source_public_key' => $keys['public'],
			'source_host'       => $source_host,
			'proof'             => base64_encode( sodium_crypto_sign_detached( $proof_data, $secret ) ),
			'label'             => (string) wp_parse_url( $source_host, PHP_URL_HOST ),
		);
		$response    = wp_safe_remote_post(
			untrailingslashit( $data['server_url'] ) . '/api/ingest/pair',
			array(
				'timeout'   => 30,
				'sslverify' => true,
				'headers'   => array( 'Content-Type' => 'application/json' ),
				'body'      => wp_json_encode( $payload ),
			)
		);
		$body        = self::decode_response( $response );
		if ( ! hash_equals( $data['server_public_key'], $body['server_public_key'] ?? '' ) ) {
			throw new RuntimeException( 'Server key confirmation failed.' );
		}
		if ( (int) ( $body['ingest_format'] ?? 0 ) > self::SUPPORTED_INGEST_FORMAT ) {
			throw new RuntimeException( 'The SpawnWP server uses a newer blueprint format; update this plugin.' );
		}
		$wpdb->replace(
			SpawnWP_Deploy_Database::table( 'connections' ),
			array(
				'id'            => $body['connection_id'],
				'label'         => wp_parse_url( $data['server_url'], PHP_URL_HOST ),
				'role'          => 'server',
				'remote_url'    => untrailingslashit( $data['server_url'] ),
				'public_key'    => $data['server_public_key'],
				'private_key'   => $keys['private'],
				'owner_user_id' => get_current_user_id(),
				'status'        => 'active',
				'created_at'    => current_time( 'mysql', true ),
				'updated_at'    => current_time( 'mysql', true ),
			)
		);
		SpawnWP_Deploy_Database::audit( 'server_connected', array( 'server_url' => $data['server_url'] ), $body['connection_id'] );
	}

	public static function revoke( string $id ): void {
		global $wpdb;
		$connection = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM %i WHERE id=%s AND role='server'", SpawnWP_Deploy_Database::table( 'connections' ), $id ), ARRAY_A );
		if ( ! $connection ) {
			throw new RuntimeException( 'Server connection not found.' );
		}
		if ( 'active' === $connection['status'] ) {
			try {
				self::remote( $connection, 'DELETE', '/api/ingest/connection' );
			} catch ( Throwable $error ) {
				SpawnWP_Deploy_Database::audit( 'server_revoke_unreachable', array( 'error' => $error->getMessage() ), $id );
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
		SpawnWP_Deploy_Database::audit( 'server_connection_revoked', array(), $id );
	}

	public static function ajax_step(): void {
		check_ajax_referer( 'spawnwp_blueprint_ajax', 'nonce' );
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_send_json_error( array( 'message' => 'Insufficient permissions.' ), 403 );
		}
		try {
			$connection = self::server_connection( sanitize_text_field( wp_unslash( $_POST['connection'] ?? '' ) ) );
			$op         = sanitize_key( wp_unslash( $_POST['op'] ?? '' ) );
			if ( 'preflight' === $op ) {
				wp_send_json_success( self::step_preflight( $connection ) );
			}
			if ( 'prepare' === $op ) {
				wp_send_json_success( self::step_prepare( $connection ) );
			}
			$job_id = sanitize_text_field( wp_unslash( $_POST['job'] ?? '' ) );
			$state  = get_option( 'spawnwp_blueprint_job_' . $job_id );
			if ( ! is_array( $state ) || ( $state['job'] ?? '' ) !== $job_id ) {
				throw new RuntimeException( 'Blueprint capture state was not found.' );
			}
			if ( 'transfer' === $op ) {
				$index = isset( $_POST['next'] ) ? absint( wp_unslash( $_POST['next'] ) ) : (int) $state['next'];
				$chunk = SpawnWP_Deploy_Package::chunk( $state['local'], $index, (int) $state['chunk_size'] );
				self::remote(
					$connection,
					'PUT',
					'/api/ingest/jobs/' . rawurlencode( $job_id ) . '/chunks/' . $index,
					$chunk,
					array(
						'X-SpawnWP-Chunk-SHA256' => hash( 'sha256', $chunk ),
						'Content-Type'           => 'application/octet-stream',
					)
				);
				$state['next'] = $index + 1;
				update_option( 'spawnwp_blueprint_job_' . $job_id, $state, false );
				wp_send_json_success( $state );
			}
			if ( 'finalize' === $op ) {
				wp_send_json_success( self::remote( $connection, 'POST', '/api/ingest/jobs/' . rawurlencode( $job_id ) . '/finalize', '{}' ) );
			}
			if ( 'status' === $op ) {
				$status = self::remote( $connection, 'GET', '/api/ingest/jobs/' . rawurlencode( $job_id ) );
				if ( in_array( $status['state'] ?? '', array( 'complete', 'failed' ), true ) ) {
					SpawnWP_Deploy_Package::cleanup( $state['local'] );
					delete_option( 'spawnwp_blueprint_job_' . $job_id );
				}
				wp_send_json_success( $status );
			}
			throw new RuntimeException( 'Unknown blueprint operation.' );
		} catch ( Throwable $error ) {
			wp_send_json_error( array( 'message' => $error->getMessage() ), 409 );
		}
	}

	private static function step_preflight( array $connection ): array {
		$preflight = self::remote( $connection, 'GET', '/api/ingest/preflight' );
		if ( (int) ( $preflight['ingest_format'] ?? 0 ) > self::SUPPORTED_INGEST_FORMAT ) {
			throw new RuntimeException( 'The SpawnWP server uses a newer blueprint format; update this plugin.' );
		}
		// phpcs:ignore WordPress.Security.NonceVerification.Missing -- ajax_step() verifies the request nonce before calling this helper.
		$blueprint_id = sanitize_key( wp_unslash( $_POST['blueprint_id'] ?? '' ) );
		$existing     = $preflight['existing_blueprint_ids'] ?? array();
		$inventory    = SpawnWP_Deploy_Guard::plugin_inventory();
		return array(
			'spawnwp_version' => $preflight['spawnwp_version'] ?? 'unknown',
			'free_bytes'      => $preflight['free_bytes'] ?? 0,
			'exists'          => array_key_exists( $blueprint_id, $existing ),
			'replaceable'     => 'custom' === ( $existing[ $blueprint_id ] ?? '' ),
			'premium_plugins' => $inventory['premium'],
		);
	}

	private static function step_prepare( array $connection ): array {
		$fields = self::capture_fields();
		// Remember the fields here — after validation, but BEFORE the package build,
		// which can legitimately fail (the 2 GiB payload limit is a real case). A
		// failed capture is exactly when the operator must not have to retype
		// everything, so the memory is written even when the capture then throws.
		self::remember_fields( (string) $connection['id'], $fields );
		$blueprint = self::build_manifest( $fields );
		$local     = SpawnWP_Deploy_Database::uuid();
		try {
			$manifest = SpawnWP_Deploy_Package::prepare(
				$local,
				self::PLACEHOLDER_URL,
				array( 'max_body_bytes' => 8 * MB_IN_BYTES ),
				array(
					'include_plugins'    => $fields['capture']['plugins'],
					'include_themes'     => $fields['capture']['themes'],
					'include_uploads'    => $fields['capture']['uploads'],
					'include_database'   => $fields['capture']['database'],
					'keep_deploy_plugin' => false,
				)
			);
		} catch ( Throwable $error ) {
			SpawnWP_Deploy_Package::cleanup( $local );
			if ( str_contains( $error->getMessage(), '2 GiB' ) ) {
				throw new RuntimeException( 'The capture exceeds the 2 GiB payload limit. Try excluding uploads and re-run the capture.' );
			}
			throw $error;
		}
		$remote = self::remote(
			$connection,
			'POST',
			'/api/ingest/jobs',
			wp_json_encode(
				array(
					'blueprint' => $blueprint,
					'archive'   => array(
						'bytes'       => $manifest['archive_bytes'],
						'sha256'      => $manifest['archive_sha256'],
						'chunk_size'  => $manifest['chunk_size'],
						'chunk_count' => $manifest['chunk_count'],
					),
					// phpcs:ignore WordPress.Security.NonceVerification.Missing -- ajax_step() verifies the request nonce before calling this helper.
					'replace'   => ! empty( $_POST['replace'] ),
				)
			)
		);
		$state = array(
			'local'       => $local,
			'job'         => $remote['job_id'],
			'chunk_size'  => (int) $manifest['chunk_size'],
			'chunk_count' => (int) $manifest['chunk_count'],
			'next'        => 0,
		);
		update_option( 'spawnwp_blueprint_job_' . $remote['job_id'], $state, false );
		return $state;
	}

	private static function capture_fields(): array {
		// ajax_step() verifies the request nonce before this helper reads the capture form.
		// phpcs:disable WordPress.Security.NonceVerification.Missing
		$id          = sanitize_key( wp_unslash( $_POST['blueprint_id'] ?? '' ) );
		$name        = sanitize_text_field( wp_unslash( $_POST['blueprint_name'] ?? '' ) );
		$description = sanitize_text_field( wp_unslash( $_POST['blueprint_description'] ?? '' ) );
		$version     = sanitize_text_field( wp_unslash( $_POST['blueprint_version'] ?? '1.0.0' ) );
		$php_default = sanitize_text_field( wp_unslash( $_POST['php_default'] ?? '' ) );
		$php_allowed = array_values( array_intersect( self::PHP_CHOICES, array_map( 'trim', explode( ',', sanitize_text_field( wp_unslash( $_POST['php_allowed'] ?? '' ) ) ) ) ) );
		if ( ! preg_match( '/^[a-z0-9][a-z0-9-]{0,30}$/', $id ) ) {
			throw new RuntimeException( 'Blueprint id must use lowercase letters, digits and hyphens (max 31 characters).' );
		}
		if ( '' === $name || strlen( $name ) > 60 ) {
			throw new RuntimeException( 'Blueprint name must contain 1-60 characters.' );
		}
		if ( '' === $description || strlen( $description ) > 240 ) {
			throw new RuntimeException( 'Blueprint description must contain 1-240 characters.' );
		}
		if ( ! preg_match( '/^\d+\.\d+\.\d+$/', $version ) ) {
			throw new RuntimeException( 'Blueprint version must use MAJOR.MINOR.PATCH.' );
		}
		if ( ! $php_allowed || ! in_array( $php_default, $php_allowed, true ) ) {
			throw new RuntimeException( 'Select at least one PHP version; the default must be among them.' );
		}
		$capture = array(
			'plugins'  => ! empty( $_POST['include_plugins'] ),
			'themes'   => ! empty( $_POST['include_themes'] ),
			'uploads'  => ! empty( $_POST['include_uploads'] ),
			'database' => ! empty( $_POST['include_database'] ),
		);
		// phpcs:enable WordPress.Security.NonceVerification.Missing
		if ( ! in_array( true, $capture, true ) ) {
			throw new RuntimeException( 'Select at least one component to capture.' );
		}
		return compact( 'id', 'name', 'description', 'version', 'php_default', 'php_allowed', 'capture' );
	}

	private static function remember_fields( string $connection_id, array $fields ): void {
		// Keyed by connection, like the existing spawnwp_deploy_last_job_<connection_id>:
		// two SpawnWP servers hold different blueprint sets, and pre-filling one from the
		// other's ids invites an accidental fork. The pointer records which connection was
		// captured last, because the panel renders a single shared form.
		// $fields is capture_fields()' validated output (id, name, description, version,
		// php_default, php_allowed, capture) — no secrets, so autoload stays off.
		update_option( 'spawnwp_deploy_last_blueprint_' . $connection_id, $fields, false );
		update_option( 'spawnwp_deploy_last_blueprint_conn', $connection_id, false );
	}

	private static function last_fields( string $php_pin ): array {
		// The previous capture, sanitised into something the form can always render.
		// Every field is re-checked rather than trusted, because the option can go STALE:
		// if a future release drops a version from PHP_CHOICES, a blueprint captured
		// against it would render the PHP row with nothing ticked, and capture_fields()
		// would then reject the form ("Select at least one PHP version") for a user who
		// never touched it. Same for an empty capture set. Returns array() when nothing is
		// remembered, so a first-ever capture keeps the original static defaults.
		$connection = (string) get_option( 'spawnwp_deploy_last_blueprint_conn', '' );
		if ( '' === $connection ) {
			return array();
		}
		$last = get_option( 'spawnwp_deploy_last_blueprint_' . $connection );
		if ( ! is_array( $last ) || empty( $last['id'] ) ) {
			return array();
		}

		$allowed = array_values( array_intersect( self::PHP_CHOICES, (array) ( $last['php_allowed'] ?? array() ) ) );
		if ( ! $allowed ) {
			$allowed = array( $php_pin );
		}
		$default = (string) ( $last['php_default'] ?? '' );
		if ( ! in_array( $default, $allowed, true ) ) {
			$default = in_array( $php_pin, $allowed, true ) ? $php_pin : $allowed[0];
		}
		$capture = array(
			'plugins'  => ! empty( $last['capture']['plugins'] ),
			'themes'   => ! empty( $last['capture']['themes'] ),
			'uploads'  => ! empty( $last['capture']['uploads'] ),
			'database' => ! empty( $last['capture']['database'] ),
		);
		if ( ! in_array( true, $capture, true ) ) {
			$capture = array_fill_keys( array_keys( $capture ), true );
		}

		return array(
			'id'          => (string) $last['id'],
			'name'        => (string) ( $last['name'] ?? '' ),
			'description' => (string) ( $last['description'] ?? '' ),
			'version'     => self::next_version( (string) ( $last['version'] ?? '' ) ),
			'php_default' => $default,
			'php_allowed' => $allowed,
			'capture'     => $capture,
		);
	}

	private static function next_version( string $version ): string {
		// Bump the patch level: the documented workflow is to update the source site and
		// re-push to REPLACE the same blueprint, so the next capture is almost always a
		// new version of the same thing. Anything unparseable resets to 1.0.0.
		if ( ! preg_match( '/^(\d+)\.(\d+)\.(\d+)$/', $version, $matches ) ) {
			return '1.0.0';
		}
		return $matches[1] . '.' . $matches[2] . '.' . ( (int) $matches[3] + 1 );
	}

	private static function build_manifest( array $fields ): array {
		$inventory = SpawnWP_Deploy_Guard::plugin_inventory();
		$theme     = get_stylesheet();
		// Pin the source site's exact WordPress version so sites spawned from this
		// blueprint mirror the origin. Keep only the numeric MAJOR.MINOR[.PATCH]
		// core (drop -beta/-RC suffixes the server would reject); fall back to
		// 'latest' if the version is unreadable. The spawn can still override it.
		$wordpress = 'latest';
		if ( preg_match( '/^\d+\.\d+(?:\.\d+)?/', (string) get_bloginfo( 'version' ), $matches ) ) {
			$wordpress = $matches[0];
		}
		return array(
			'schema_version'  => 2,
			'id'              => $fields['id'],
			'name'            => $fields['name'],
			'version'         => $fields['version'],
			'description'     => $fields['description'],
			'php'             => array(
				'default' => $fields['php_default'],
				'allowed' => $fields['php_allowed'],
			),
			'wordpress'       => $wordpress,
			'created_at'      => gmdate( 'Y-m-d\TH:i:s\Z' ),
			'capture'         => $fields['capture'],
			'wporg_plugins'   => array_slice( $inventory['wporg'], 0, 64 ),
			'premium_plugins' => array_slice( $inventory['premium'], 0, 64 ),
			'theme'           => preg_match( '/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/', $theme ) ? $theme : null,
		);
	}

	public static function server_connection( string $id ): array {
		global $wpdb;
		$row = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM %i WHERE id=%s AND role='server' AND status='active'", SpawnWP_Deploy_Database::table( 'connections' ), $id ), ARRAY_A );
		if ( ! $row ) {
			throw new RuntimeException( 'Active server connection not found.' );
		}
		return $row;
	}

	private static function remote( array $connection, string $method, string $path, string $body = '', array $extra_headers = array() ): array {
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
			$connection['remote_url'] . $path,
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
			$message = is_array( $data ) ? ( $data['detail'] ?? $data['message'] ?? null ) : null;
			if ( ! $message && 404 === $status ) {
				$message = 'The server does not support blueprint capture. Update your SpawnWP server to 0.4.0 or later.';
			}
			throw new RuntimeException( is_string( $message ) ? esc_html( $message ) : 'Server request failed with HTTP ' . absint( $status ) );
		}
		return is_array( $data ) ? $data : array();
	}

	public static function render_panel(): void {
		global $wpdb;
		$servers   = $wpdb->get_results( $wpdb->prepare( "SELECT * FROM %i WHERE role='server' AND status='active' ORDER BY created_at DESC", SpawnWP_Deploy_Database::table( 'connections' ) ), ARRAY_A );
		$inventory = SpawnWP_Deploy_Guard::plugin_inventory();
		$php       = PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION;
		$php_pin   = in_array( $php, self::PHP_CHOICES, true ) ? $php : '8.2';
		// Pre-fill from the previous capture; empty on a first-ever capture, where the
		// static defaults below apply unchanged.
		$last         = self::last_fields( $php_pin );
		$last_capture = $last['capture'] ?? array(
			'plugins'  => true,
			'themes'   => true,
			'uploads'  => true,
			'database' => true,
		);
		$last_allowed = $last['php_allowed'] ?? array( $php_pin );
		$last_default = $last['php_default'] ?? $php_pin;
		$last_version = $last['version'] ?? '1.0.0';
		?>
		<section class="spawnwp-panel spawnwp-blueprint">
			<h2>Create a SpawnWP blueprint from this site</h2>
			<p class="description">Capture this configured site as a reusable template on your own SpawnWP server. New sites spawned from it start with these plugins, theme, uploads and content.</p>
			<?php if ( ! $servers ) : ?>
				<p>Paste the pairing code generated on your SpawnWP server (System page &rarr; Template connections).</p>
				<form method="post">
					<?php wp_nonce_field( 'spawnwp_deploy_admin' ); ?>
					<input type="hidden" name="spawnwp_deploy_action" value="connect_server">
					<textarea name="pairing_bundle" rows="5" class="large-text code" required placeholder="spawnbp1:..."></textarea>
					<?php submit_button( 'Connect SpawnWP server', 'primary', 'submit', false ); ?>
				</form>
			<?php else : ?>
				<?php foreach ( $servers as $server ) : ?>
					<div class="spawnwp-receiver">
						<span>Connected server: <code><?php echo esc_html( $server['remote_url'] ); ?></code></span>
						<form method="post">
							<?php wp_nonce_field( 'spawnwp_deploy_admin' ); ?>
							<input type="hidden" name="spawnwp_deploy_action" value="revoke_server">
							<input type="hidden" name="connection_id" value="<?php echo esc_attr( $server['id'] ); ?>">
							<button class="button" type="submit">Disconnect</button>
						</form>
					</div>
				<?php endforeach; ?>
				<?php if ( $inventory['premium'] ) : ?>
					<div class="spawnwp-warning">
						<strong>Licensed plugins detected.</strong> The following plugins are not from WordPress.org and may require new license keys or re-activation on every site spawned from this blueprint:
						<ul>
							<?php foreach ( $inventory['premium'] as $plugin ) : ?>
								<li><?php echo esc_html( $plugin['name'] . ' ' . $plugin['version'] ); ?></li>
							<?php endforeach; ?>
						</ul>
					</div>
				<?php endif; ?>
				<?php if ( $last ) : ?>
					<p class="description" id="spawnwp-bp-prefilled">
						Pre-filled from your last capture (<code><?php echo esc_html( $last['id'] ); ?></code>, now version
						<code><?php echo esc_html( $last_version ); ?></code>). Re-capturing with the same id <strong>replaces</strong>
						that blueprint. To create a different one, use <em>Start a new blueprint</em>.
					</p>
				<?php endif; ?>
				<form id="spawnwp-blueprint-form" data-premium-count="<?php echo esc_attr( count( $inventory['premium'] ) ); ?>" data-php-pin="<?php echo esc_attr( $php_pin ); ?>">
					<table class="form-table" role="presentation">
						<tr><th><label for="spawnwp-bp-id">Blueprint id</label></th>
							<td><input type="text" id="spawnwp-bp-id" pattern="[a-z0-9][a-z0-9-]{0,30}" maxlength="31" class="regular-text" placeholder="my-agency-base" value="<?php echo esc_attr( $last['id'] ?? '' ); ?>" required>
							<p class="description">Lowercase letters, digits and hyphens. Reusing an id lets you replace the existing blueprint.</p></td></tr>
						<tr><th><label for="spawnwp-bp-name">Name</label></th>
							<td><input type="text" id="spawnwp-bp-name" maxlength="60" class="regular-text" placeholder="Agency base setup" value="<?php echo esc_attr( $last['name'] ?? '' ); ?>" required></td></tr>
						<tr><th><label for="spawnwp-bp-description">Description</label></th>
							<td><input type="text" id="spawnwp-bp-description" maxlength="240" class="large-text" placeholder="Usual plugins, theme and starting content" value="<?php echo esc_attr( $last['description'] ?? '' ); ?>" required></td></tr>
						<tr><th><label for="spawnwp-bp-version">Version</label></th>
							<td><input type="text" id="spawnwp-bp-version" pattern="\d+\.\d+\.\d+" class="small-text" value="<?php echo esc_attr( $last_version ); ?>" required></td></tr>
						<tr><th>Contents</th><td>
							<label><input type="checkbox" id="spawnwp-bp-plugins" <?php checked( ! empty( $last_capture['plugins'] ) ); ?>> Plugin files (including premium/custom)</label><br>
							<label><input type="checkbox" id="spawnwp-bp-themes" <?php checked( ! empty( $last_capture['themes'] ) ); ?>> Theme files</label><br>
							<label><input type="checkbox" id="spawnwp-bp-uploads" <?php checked( ! empty( $last_capture['uploads'] ) ); ?>> Media uploads</label><br>
							<label><input type="checkbox" id="spawnwp-bp-database" <?php checked( ! empty( $last_capture['database'] ) ); ?>> Database (posts, pages, settings &mdash; never users or passwords)</label>
						</td></tr>
						<tr><th>PHP versions</th><td>
							<?php foreach ( self::PHP_CHOICES as $choice ) : ?>
							<label class="spawnwp-bp-php-choice"><input type="checkbox" class="spawnwp-bp-php" value="<?php echo esc_attr( $choice ); ?>" <?php checked( in_array( $choice, $last_allowed, true ) ); ?>> <?php echo esc_html( $choice ); ?></label>
							<?php endforeach; ?>
							<p class="description">Default for new sites:
								<select id="spawnwp-bp-php-default">
									<?php foreach ( self::PHP_CHOICES as $choice ) : ?>
										<option value="<?php echo esc_attr( $choice ); ?>" <?php selected( $choice === $last_default ); ?>><?php echo esc_html( $choice ); ?></option>
									<?php endforeach; ?>
								</select>
								This site runs PHP <?php echo esc_html( $php ); ?>.</p>
						</td></tr>
					</table>
					<?php foreach ( $servers as $server ) : ?>
						<button class="button button-primary spawnwp-bp-start" data-connection="<?php echo esc_attr( $server['id'] ); ?>">Create blueprint on <?php echo esc_html( ! empty( $server['label'] ) ? $server['label'] : $server['remote_url'] ); ?></button>
					<?php endforeach; ?>
					<?php if ( $last ) : ?>
						<button type="button" class="button" id="spawnwp-bp-reset">Start a new blueprint</button>
					<?php endif; ?>
				</form>
				<pre id="spawnwp-bp-log" hidden></pre>
			<?php endif; ?>
		</section>
		<?php
	}
}
