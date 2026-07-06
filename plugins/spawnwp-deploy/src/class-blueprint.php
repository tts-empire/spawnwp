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
		$connection = $wpdb->get_row( $wpdb->prepare( 'SELECT * FROM ' . SpawnWP_Deploy_Database::table( 'connections' ) . " WHERE id=%s AND role='server'", $id ), ARRAY_A );
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
			array( 'status' => 'revoked', 'private_key' => '', 'updated_at' => current_time( 'mysql', true ) ),
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
		$fields    = self::capture_fields();
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
					'replace'   => ! empty( $_POST['replace'] ),
				)
			)
		);
		$state  = array(
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
		if ( ! in_array( true, $capture, true ) ) {
			throw new RuntimeException( 'Select at least one component to capture.' );
		}
		return compact( 'id', 'name', 'description', 'version', 'php_default', 'php_allowed', 'capture' );
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
		$row = $wpdb->get_row( $wpdb->prepare( 'SELECT * FROM ' . SpawnWP_Deploy_Database::table( 'connections' ) . " WHERE id=%s AND role='server' AND status='active'", $id ), ARRAY_A );
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
			throw new RuntimeException( $response->get_error_message() );
		}
		$status = wp_remote_retrieve_response_code( $response );
		$data   = json_decode( wp_remote_retrieve_body( $response ), true );
		if ( $status < 200 || $status >= 300 ) {
			$message = is_array( $data ) ? ( $data['detail'] ?? $data['message'] ?? null ) : null;
			if ( ! $message && 404 === $status ) {
				$message = 'The server does not support blueprint capture. Update your SpawnWP server to 0.4.0 or later.';
			}
			throw new RuntimeException( is_string( $message ) ? $message : 'Server request failed with HTTP ' . $status );
		}
		return is_array( $data ) ? $data : array();
	}

	public static function render_panel(): void {
		global $wpdb;
		$servers   = $wpdb->get_results( 'SELECT * FROM ' . SpawnWP_Deploy_Database::table( 'connections' ) . " WHERE role='server' AND status='active' ORDER BY created_at DESC", ARRAY_A );
		$inventory = SpawnWP_Deploy_Guard::plugin_inventory();
		$php       = PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION;
		$php_pin   = in_array( $php, self::PHP_CHOICES, true ) ? $php : '8.2';
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
				<form id="spawnwp-blueprint-form" onsubmit="return false">
					<table class="form-table" role="presentation">
						<tr><th><label for="spawnwp-bp-id">Blueprint id</label></th>
							<td><input type="text" id="spawnwp-bp-id" pattern="[a-z0-9][a-z0-9-]{0,30}" maxlength="31" class="regular-text" placeholder="my-agency-base" required>
							<p class="description">Lowercase letters, digits and hyphens. Reusing an id lets you replace the existing blueprint.</p></td></tr>
						<tr><th><label for="spawnwp-bp-name">Name</label></th>
							<td><input type="text" id="spawnwp-bp-name" maxlength="60" class="regular-text" placeholder="Agency base setup" required></td></tr>
						<tr><th><label for="spawnwp-bp-description">Description</label></th>
							<td><input type="text" id="spawnwp-bp-description" maxlength="240" class="large-text" placeholder="Usual plugins, theme and starting content" required></td></tr>
						<tr><th><label for="spawnwp-bp-version">Version</label></th>
							<td><input type="text" id="spawnwp-bp-version" pattern="\d+\.\d+\.\d+" class="small-text" value="1.0.0" required></td></tr>
						<tr><th>Contents</th><td>
							<label><input type="checkbox" id="spawnwp-bp-plugins" checked> Plugin files (including premium/custom)</label><br>
							<label><input type="checkbox" id="spawnwp-bp-themes" checked> Theme files</label><br>
							<label><input type="checkbox" id="spawnwp-bp-uploads" checked> Media uploads</label><br>
							<label><input type="checkbox" id="spawnwp-bp-database" checked> Database (posts, pages, settings &mdash; never users or passwords)</label>
						</td></tr>
						<tr><th>PHP versions</th><td>
							<?php foreach ( self::PHP_CHOICES as $choice ) : ?>
								<label style="margin-right:12px"><input type="checkbox" class="spawnwp-bp-php" value="<?php echo esc_attr( $choice ); ?>" <?php checked( $choice === $php_pin ); ?>> <?php echo esc_html( $choice ); ?></label>
							<?php endforeach; ?>
							<p class="description">Default for new sites:
								<select id="spawnwp-bp-php-default">
									<?php foreach ( self::PHP_CHOICES as $choice ) : ?>
										<option value="<?php echo esc_attr( $choice ); ?>" <?php selected( $choice === $php_pin ); ?>><?php echo esc_html( $choice ); ?></option>
									<?php endforeach; ?>
								</select>
								This site runs PHP <?php echo esc_html( $php ); ?>.</p>
						</td></tr>
					</table>
					<?php foreach ( $servers as $server ) : ?>
						<button class="button button-primary spawnwp-bp-start" data-connection="<?php echo esc_attr( $server['id'] ); ?>">Create blueprint on <?php echo esc_html( $server['label'] ?: $server['remote_url'] ); ?></button>
					<?php endforeach; ?>
				</form>
				<pre id="spawnwp-bp-log" hidden></pre>
			<?php endif; ?>
		</section>
		<?php if ( $servers ) : ?>
		<script>
		(function(){
			const ajaxUrl=<?php echo wp_json_encode( admin_url( 'admin-ajax.php' ) ); ?>;
			const nonce=<?php echo wp_json_encode( wp_create_nonce( 'spawnwp_blueprint_ajax' ) ); ?>;
			const premium=<?php echo wp_json_encode( count( $inventory['premium'] ) ); ?>;
			const log=document.getElementById('spawnwp-bp-log');
			function line(text){log.hidden=false;log.textContent+=text+'\n';log.scrollTop=log.scrollHeight;}
			function fields(){
				const allowed=[...document.querySelectorAll('.spawnwp-bp-php:checked')].map(box=>box.value);
				return {
					blueprint_id:document.getElementById('spawnwp-bp-id').value.trim(),
					blueprint_name:document.getElementById('spawnwp-bp-name').value.trim(),
					blueprint_description:document.getElementById('spawnwp-bp-description').value.trim(),
					blueprint_version:document.getElementById('spawnwp-bp-version').value.trim(),
					php_default:document.getElementById('spawnwp-bp-php-default').value,
					php_allowed:allowed.join(','),
					include_plugins:document.getElementById('spawnwp-bp-plugins').checked?'1':'',
					include_themes:document.getElementById('spawnwp-bp-themes').checked?'1':'',
					include_uploads:document.getElementById('spawnwp-bp-uploads').checked?'1':'',
					include_database:document.getElementById('spawnwp-bp-database').checked?'1':''
				};
			}
			async function step(connection,op,state={}){const body=new URLSearchParams({action:'spawnwp_blueprint_step',nonce,connection,op,...state});const response=await fetch(ajaxUrl,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});const data=await response.json();if(!data.success)throw new Error(data.data&&data.data.message?data.data.message:'Blueprint step failed');return data.data;}
			const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
			async function capture(button){
				const connection=button.dataset.connection;
				const form=fields();
				if(!document.getElementById('spawnwp-blueprint-form').reportValidity())return;
				button.disabled=true;
				try{
					line('Checking SpawnWP server...');
					const pre=await step(connection,'preflight',{blueprint_id:form.blueprint_id});
					line('Server SpawnWP '+pre.spawnwp_version+' ready.');
					if(pre.exists){
						if(!pre.replaceable)throw new Error('Blueprint id "'+form.blueprint_id+'" already exists on the server and cannot be replaced. Pick another id.');
						if(!confirm('A blueprint named "'+form.blueprint_id+'" already exists on the server.\nReplace it with this capture? The old version is kept until the new one is verified.'))throw new Error('Capture cancelled.');
						form.replace='1';
					}
					if(premium>0&&!confirm('This site uses '+premium+' premium/custom plugin(s) (see the warning above).\nSites spawned from this blueprint may require new license keys or re-activation.\n\nContinue?'))throw new Error('Capture cancelled.');
					if(form.include_database&&!confirm('The database capture includes this site\'s real posts, pages and settings: they will appear in every site spawned from this blueprint.\nUsers and passwords are never included.\n\nContinue?'))throw new Error('Capture cancelled.');
					line('Building capture package (this can take a while)...');
					let data=await step(connection,'prepare',form);
					const job=data.job;
					line('Package ready: '+data.chunk_count+' chunks.');
					while(data.next<data.chunk_count){data=await step(connection,'transfer',{job,next:String(data.next)});line('Transferred '+data.next+' / '+data.chunk_count+' chunks');}
					line('Upload complete. Server is verifying and installing...');
					await step(connection,'finalize',{job});
					for(;;){
						await sleep(2000);
						const status=await step(connection,'status',{job});
						if(status.state==='complete'){line('Blueprint installed. It is now available on the Deploy page of your SpawnWP server.');button.textContent='Blueprint created';return;}
						if(status.state==='failed')throw new Error(status.error||'Server-side installation failed.');
						line('Server: '+status.state+'...');
					}
				}catch(error){line('ERROR: '+error.message);button.disabled=false;}
			}
			document.querySelectorAll('.spawnwp-bp-start').forEach(button=>button.addEventListener('click',()=>capture(button)));
		})();
		</script>
		<?php endif; ?>
		<?php
	}
}
