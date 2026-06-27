<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_Receiver {
	public static function root( string $job_id ): string {
		return WP_CONTENT_DIR . '/.spawnwp-deploy/receiver/' . sanitize_file_name( $job_id );
	}

	public static function create_job( string $connection_id, array $manifest ): string {
		global $wpdb;
		$job_id = SpawnWP_Deploy_Database::uuid();
		$root   = self::root( $job_id );
		if ( ! wp_mkdir_p( $root . '/chunks' ) ) {
			throw new RuntimeException( 'Unable to create receiver workspace.' );
		}
		$wpdb->insert(
			SpawnWP_Deploy_Database::table( 'jobs' ),
			array(
				'id'             => $job_id,
				'connection_id'  => $connection_id,
				'state'          => 'transferring',
				'manifest'       => wp_json_encode( $manifest ),
				'total_bytes'    => (int) $manifest['archive_bytes'],
				'received_bytes' => 0,
				'created_at'     => current_time( 'mysql', true ),
				'updated_at'     => current_time( 'mysql', true ),
			),
			array( '%s', '%s', '%s', '%s', '%d', '%d', '%s', '%s' )
		);
		SpawnWP_Deploy_Database::audit( 'job_created', array( 'bytes' => (int) $manifest['archive_bytes'] ), $connection_id, $job_id );
		return $job_id;
	}

	public static function receive_chunk( array $job, int $index, string $bytes, string $expected_sha ): array {
		$manifest = json_decode( $job['manifest'], true );
		if ( 'transferring' !== $job['state'] || $index < 0 || $index >= (int) $manifest['chunk_count'] ) {
			throw new RuntimeException( 'Chunk is not valid for the current job state.' );
		}
		if ( ! hash_equals( strtolower( $expected_sha ), hash( 'sha256', $bytes ) ) ) {
			throw new RuntimeException( 'Chunk checksum mismatch.' );
		}
		$file = self::root( $job['id'] ) . '/chunks/' . $index . '.part';
		if ( false === file_put_contents( $file, $bytes, LOCK_EX ) ) {
			throw new RuntimeException( 'Unable to store chunk.' );
		}
		return self::chunk_status( $job['id'], $manifest );
	}

	public static function chunk_status( string $job_id, array $manifest ): array {
		global $wpdb;
		$missing  = array();
		$received = 0;
		for ( $i = 0; $i < (int) $manifest['chunk_count']; ++$i ) {
			$file = self::root( $job_id ) . '/chunks/' . $i . '.part';
			if ( is_file( $file ) ) {
				$received += filesize( $file );
			} else {
				$missing[] = $i;
			}
		}
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'jobs' ),
			array(
				'received_bytes' => $received,
				'updated_at'     => current_time( 'mysql', true ),
			),
			array( 'id' => $job_id ),
			array( '%d', '%s' ),
			array( '%s' )
		);
		return array(
			'received_bytes' => $received,
			'missing'        => $missing,
		);
	}

	public static function stage( array $job ): array {
		global $wpdb;
		$manifest = json_decode( $job['manifest'], true );
		$status   = self::chunk_status( $job['id'], $manifest );
		if ( $status['missing'] ) {
			throw new RuntimeException( 'Cannot stage an incomplete upload.' );
		}
		$root    = self::root( $job['id'] );
		$archive = $root . '/payload.zip';
		$out     = fopen( $archive, 'wb' );
		for ( $i = 0; $i < (int) $manifest['chunk_count']; ++$i ) {
			$part = fopen( $root . '/chunks/' . $i . '.part', 'rb' );
			stream_copy_to_stream( $part, $out );
			fclose( $part );
		}
		fclose( $out );
		if ( ! hash_equals( $manifest['archive_sha256'], hash_file( 'sha256', $archive ) ) ) {
			throw new RuntimeException( 'Payload checksum mismatch.' );
		}
		if ( disk_free_space( WP_CONTENT_DIR ) < (int) $manifest['archive_bytes'] * 2 ) {
			throw new RuntimeException( 'Insufficient free space to stage and activate payload.' );
		}

		$stage = $root . '/stage';
		wp_mkdir_p( $stage );
		$zip = new ZipArchive();
		if ( true !== $zip->open( $archive ) ) {
			throw new RuntimeException( 'Unable to open payload archive.' );
		}
		self::validate_archive( $zip, (int) $manifest['archive_bytes'] * 5 );
		if ( ! $zip->extractTo( $stage ) ) {
			$zip->close();
			throw new RuntimeException( 'Unable to extract payload.' );
		}
		$zip->close();
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'jobs' ),
			array(
				'state'      => 'staged',
				'updated_at' => current_time( 'mysql', true ),
			),
			array( 'id' => $job['id'] )
		);
		SpawnWP_Deploy_Database::audit( 'job_staged', array(), $job['connection_id'], $job['id'] );
		return array( 'state' => 'staged' );
	}

	public static function activate( array $job, int $owner_user_id ): array {
		global $wpdb;
		if ( 'staged' !== $job['state'] ) {
			throw new RuntimeException( 'Job is not staged.' );
		}
		$guard = SpawnWP_Deploy_Guard::target_report();
		if ( ! $guard['ok'] ) {
			throw new RuntimeException( 'Target is no longer empty: ' . implode( ' ', $guard['issues'] ) );
		}
		$manifest = json_decode( $job['manifest'], true );
		$compatibility_warnings = SpawnWP_Deploy_Guard::compatibility_warnings( $manifest, $guard['environment'] );
		if ( $compatibility_warnings ) {
			SpawnWP_Deploy_Database::audit( 'compatibility_warning_accepted', array( 'warnings' => $compatibility_warnings ), $job['connection_id'], $job['id'] );
		}

		$activation = array(
			'tables'     => array(),
			'files'      => array(),
			'started_at' => gmdate( 'c' ),
		);
		try {
			self::maintenance( true );
			$activation['tables']   = self::import_and_swap_database( $job, $manifest, $owner_user_id );
			$manifest['activation'] = $activation;
			self::persist_manifest( $job['id'], $manifest, 'activating' );
			self::swap_files( $job, $manifest );
			$activation = $manifest['activation'];
			$wpdb->update(
				SpawnWP_Deploy_Database::table( 'jobs' ),
				array(
					'state'      => 'verifying',
					'manifest'   => wp_json_encode( $manifest ),
					'updated_at' => current_time( 'mysql', true ),
				),
				array( 'id' => $job['id'] )
			);
			wp_cache_flush();
			self::maintenance( false );
			$healthcheck_url = defined( 'SPAWNWP_DEPLOY_HEALTHCHECK_URL' ) ? SPAWNWP_DEPLOY_HEALTHCHECK_URL : home_url( '/' );
			$check           = wp_remote_get(
				$healthcheck_url,
				array(
					'timeout'     => 20,
					'redirection' => 3,
					'sslverify'   => true,
				)
			);
			$status_code = wp_remote_retrieve_response_code( $check );
			if ( is_wp_error( $check ) || $status_code < 200 || $status_code >= 400 ) {
				throw new RuntimeException( 'Post-activation health check failed.' );
			}
			update_option( 'spawnwp_deploy_completed_at', gmdate( 'c' ), false );
			update_option( 'spawnwp_deploy_rollback_expires', time() + 7 * DAY_IN_SECONDS, false );
			$wpdb->update(
				SpawnWP_Deploy_Database::table( 'jobs' ),
				array(
					'state'        => 'complete',
					'completed_at' => current_time( 'mysql', true ),
					'updated_at'   => current_time( 'mysql', true ),
				),
				array( 'id' => $job['id'] )
			);
			SpawnWP_Deploy_Database::audit( 'deployment_complete', array(), $job['connection_id'], $job['id'] );
			return array(
				'state'            => 'complete',
				'rollback_expires' => get_option( 'spawnwp_deploy_rollback_expires' ),
			);
		} catch ( Throwable $error ) {
			self::maintenance( false );
			$latest_manifest = $wpdb->get_var( $wpdb->prepare( 'SELECT manifest FROM ' . SpawnWP_Deploy_Database::table( 'jobs' ) . ' WHERE id=%s', $job['id'] ) );
			$latest_manifest = $latest_manifest ? json_decode( $latest_manifest, true ) : array();
			if ( ! empty( $latest_manifest['activation'] ) ) {
				$activation = $latest_manifest['activation'];
			}
			if ( $activation['tables'] || $activation['files'] ) {
				try {
					self::rollback_maps( $activation );
				} catch ( Throwable $rollback_error ) {
					SpawnWP_Deploy_Database::audit( 'rollback_failed', array( 'error' => $rollback_error->getMessage() ), $job['connection_id'], $job['id'] );
				}
			}
			$wpdb->update(
				SpawnWP_Deploy_Database::table( 'jobs' ),
				array(
					'state'      => 'failed',
					'error_text' => $error->getMessage(),
					'updated_at' => current_time( 'mysql', true ),
				),
				array( 'id' => $job['id'] )
			);
			throw $error;
		}
	}

	public static function rollback( array $job ): array {
		$manifest = json_decode( $job['manifest'], true );
		if ( empty( $manifest['activation'] ) || time() > (int) get_option( 'spawnwp_deploy_rollback_expires', 0 ) ) {
			throw new RuntimeException( 'Rollback is not available.' );
		}
		self::maintenance( true );
		self::rollback_maps( $manifest['activation'] );
		delete_option( 'spawnwp_deploy_completed_at' );
		delete_option( 'spawnwp_deploy_rollback_expires' );
		self::maintenance( false );
		SpawnWP_Deploy_Database::audit( 'rollback_complete', array(), $job['connection_id'], $job['id'] );
		return array( 'state' => 'rollback' );
	}

	public static function cleanup_expired(): void {
		global $wpdb;
		SpawnWP_Deploy_Database::maintain_connections();
		$expires = (int) get_option( 'spawnwp_deploy_rollback_expires', 0 );
		if ( ! $expires || time() <= $expires ) {
			return;
		}
		$jobs = $wpdb->get_results( 'SELECT * FROM ' . SpawnWP_Deploy_Database::table( 'jobs' ) . " WHERE state='complete'", ARRAY_A );
		foreach ( $jobs as $job ) {
			$manifest = json_decode( $job['manifest'], true );
			foreach ( $manifest['activation']['tables'] ?? array() as $entry ) {
				$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $entry['backup'] ) . '`' );
			}
			self::remove_tree( self::root( $job['id'] ) . '/backup-content' );
		}
		delete_option( 'spawnwp_deploy_rollback_expires' );
		SpawnWP_Deploy_Database::audit( 'rollback_expired_cleanup' );
	}

	private static function import_and_swap_database( array $job, array $manifest, int $owner_user_id ): array {
		global $wpdb;
		$file          = self::root( $job['id'] ) . '/stage/database.jsonl';
		$handle        = fopen( $file, 'rb' );
		$short         = substr( str_replace( '-', '', $job['id'] ), 0, 6 );
		$map           = array();
		$current_table = '';
		while ( ( $line = fgets( $handle ) ) !== false ) {
			$record = json_decode( $line, true, 512, JSON_THROW_ON_ERROR );
			if ( 'table' === $record['type'] ) {
				$suffix = str_starts_with( $record['name'], $manifest['source_prefix'] ) ? substr( $record['name'], strlen( $manifest['source_prefix'] ) ) : $record['name'];
				$live   = $wpdb->prefix . $suffix;
				$temp   = substr( $wpdb->prefix . 'sd' . $short . '_' . $suffix, 0, 64 );
				$backup = substr( $wpdb->prefix . 'sb' . $short . '_' . $suffix, 0, 64 );
				$ddl    = preg_replace( '/^CREATE TABLE\s+`[^`]+`/i', 'CREATE TABLE `' . esc_sql( $temp ) . '`', $record['create'] );
				$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $temp ) . '`' );
				if ( false === $wpdb->query( $ddl ) ) {
					throw new RuntimeException( 'Unable to create temporary table for ' . $live );
				}
				$map[ $record['name'] ] = array(
					'live'   => $live,
					'temp'   => $temp,
					'backup' => $backup,
				);
				$current_table          = $record['name'];
			} elseif ( 'row' === $record['type'] ) {
				$current_table = $record['table'];
				$data          = array();
				foreach ( $record['data'] as $column => $value ) {
					$data[ $column ] = null === $value ? null : base64_decode( $value, true );
				}
				if ( false === $wpdb->insert( $map[ $current_table ]['temp'], $data ) ) {
					throw new RuntimeException( 'Unable to import a row into ' . $map[ $current_table ]['live'] );
				}
			}
		}
		fclose( $handle );

		$options_live   = $wpdb->options;
		$options_source = $manifest['source_prefix'] . 'options';
		if ( isset( $map[ $options_source ] ) ) {
			$temp_options = $map[ $options_source ]['temp'];
			foreach ( array( 'home', 'siteurl', 'admin_email' ) as $name ) {
				$value = $wpdb->get_var( $wpdb->prepare( "SELECT option_value FROM {$options_live} WHERE option_name=%s", $name ) );
				$wpdb->query( $wpdb->prepare( "UPDATE `{$temp_options}` SET option_value=%s WHERE option_name=%s", $value, $name ) );
			}
		}
		$posts_source = $manifest['source_prefix'] . 'posts';
		if ( isset( $map[ $posts_source ] ) ) {
			$wpdb->query( $wpdb->prepare( 'UPDATE `' . esc_sql( $map[ $posts_source ]['temp'] ) . '` SET post_author=%d', $owner_user_id ) );
		}
		$comments_source = $manifest['source_prefix'] . 'comments';
		if ( isset( $map[ $comments_source ] ) ) {
			$wpdb->query( 'UPDATE `' . esc_sql( $map[ $comments_source ]['temp'] ) . '` SET user_id=0' );
		}

		$renames = array();
		foreach ( $map as $entry ) {
			$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $entry['backup'] ) . '`' );
			if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $entry['live'] ) ) ) {
				$renames[] = '`' . esc_sql( $entry['live'] ) . '` TO `' . esc_sql( $entry['backup'] ) . '`';
			}
			$renames[] = '`' . esc_sql( $entry['temp'] ) . '` TO `' . esc_sql( $entry['live'] ) . '`';
		}
		if ( false === $wpdb->query( 'RENAME TABLE ' . implode( ', ', $renames ) ) ) {
			throw new RuntimeException( 'Atomic database table swap failed.' );
		}
		return array_values( $map );
	}

	private static function swap_files( array $job, array &$manifest ): void {
		$stage_content = self::root( $job['id'] ) . '/stage/content';
		$backup_root   = self::root( $job['id'] ) . '/backup-content';
		wp_mkdir_p( $backup_root );
		foreach ( array( 'plugins', 'themes', 'uploads' ) as $kind ) {
			$staged = $stage_content . '/' . $kind;
			$live   = 'plugins' === $kind ? WP_PLUGIN_DIR : ( 'themes' === $kind ? get_theme_root() : wp_get_upload_dir()['basedir'] );
			$backup = $backup_root . '/' . $kind;
			if ( 'plugins' === $kind ) {
				$manifest['activation']['files'][] = array(
					'mode'     => 'children',
					'staged'   => $staged,
					'live'     => $live,
					'backup'   => $backup,
					'preserve' => array( 'spawnwp-deploy' ),
				);
				self::persist_manifest( $job['id'], $manifest, 'activating' );
				self::swap_directory_children( $staged, $live, $backup, array( 'spawnwp-deploy' ) );
			} else {
				$manifest['activation']['files'][] = array(
					'mode'   => 'directory',
					'live'   => $live,
					'backup' => $backup,
				);
				self::persist_manifest( $job['id'], $manifest, 'activating' );
				if ( is_dir( $live ) && ! rename( $live, $backup ) ) {
					throw new RuntimeException( 'Unable to back up ' . $kind );
				}
				if ( is_dir( $staged ) && ! rename( $staged, $live ) ) {
					throw new RuntimeException( 'Unable to activate ' . $kind );
				}
			}
		}
	}

	private static function persist_manifest( string $job_id, array $manifest, string $state ): void {
		global $wpdb;
		$wpdb->update(
			SpawnWP_Deploy_Database::table( 'jobs' ),
			array(
				'state'      => $state,
				'manifest'   => wp_json_encode( $manifest ),
				'updated_at' => current_time( 'mysql', true ),
			),
			array( 'id' => $job_id )
		);
	}

	private static function swap_directory_children( string $staged, string $live, string $backup, array $preserve ): void {
		wp_mkdir_p( $backup );
		foreach ( glob( $live . '/*' ) ?: array() as $path ) {
			if ( in_array( basename( $path ), $preserve, true ) ) {
				continue;
			}
			rename( $path, $backup . '/' . basename( $path ) );
		}
		foreach ( glob( $staged . '/*' ) ?: array() as $path ) {
			if ( in_array( basename( $path ), $preserve, true ) ) {
				continue;
			}
			rename( $path, $live . '/' . basename( $path ) );
		}
	}

	private static function rollback_maps( array $activation ): void {
		global $wpdb;
		if ( ! empty( $activation['tables'] ) ) {
			$renames = array();
			foreach ( $activation['tables'] as $entry ) {
				if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $entry['backup'] ) ) ) {
					$failed = substr( $entry['temp'] . '_failed', 0, 64 );
					$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $failed ) . '`' );
					$renames[] = '`' . esc_sql( $entry['live'] ) . '` TO `' . esc_sql( $failed ) . '`';
					$renames[] = '`' . esc_sql( $entry['backup'] ) . '` TO `' . esc_sql( $entry['live'] ) . '`';
				}
			}
			if ( $renames ) {
				$wpdb->query( 'RENAME TABLE ' . implode( ', ', $renames ) );
			}
		}
		foreach ( array_reverse( $activation['files'] ?? array() ) as $entry ) {
			if ( 'directory' === $entry['mode'] ) {
				$failed = $entry['live'] . '.spawnwp-failed-' . time();
				if ( is_dir( $entry['live'] ) ) {
					rename( $entry['live'], $failed );
				}
				if ( is_dir( $entry['backup'] ) ) {
					rename( $entry['backup'], $entry['live'] );
				}
			} else {
				self::swap_directory_children( $entry['backup'], $entry['live'], dirname( $entry['backup'] ) . '/failed-plugins', $entry['preserve'] );
			}
		}
		wp_cache_flush();
	}

	private static function validate_archive( ZipArchive $zip, int $max_uncompressed ): void {
		$total = 0;
		for ( $i = 0; $i < $zip->numFiles; ++$i ) {
			$stat = $zip->statIndex( $i );
			$name = str_replace( '\\', '/', $stat['name'] );
			$parts = explode( '/', $name );
			if ( str_starts_with( $name, '/' ) || preg_match( '/^[A-Za-z]:/', $name ) || in_array( '..', $parts, true ) || str_contains( $name, "\0" ) ) {
				throw new RuntimeException( 'Unsafe archive path.' );
			}
			$opsys      = 0;
			$attributes = 0;
			if ( $zip->getExternalAttributesIndex( $i, $opsys, $attributes ) && 0120000 === ( ( $attributes >> 16 ) & 0170000 ) ) {
				throw new RuntimeException( 'Archive symlinks are not supported.' );
			}
			$total += (int) $stat['size'];
			if ( $total > $max_uncompressed ) {
				throw new RuntimeException( 'Archive expansion exceeds safety limit.' );
			}
		}
	}

	private static function maintenance( bool $enable ): void {
		$file = ABSPATH . '.maintenance';
		if ( $enable ) {
			file_put_contents( $file, '<?php $upgrading = ' . time() . ';' );
		} else {
			@unlink( $file );
		}
	}

	private static function remove_tree( string $path ): void {
		if ( ! is_dir( $path ) ) {
			return;
		}
		$iterator = new RecursiveIteratorIterator( new RecursiveDirectoryIterator( $path, FilesystemIterator::SKIP_DOTS ), RecursiveIteratorIterator::CHILD_FIRST );
		foreach ( $iterator as $item ) {
			$item->isDir() ? @rmdir( $item->getPathname() ) : @unlink( $item->getPathname() );
		}
		@rmdir( $path );
	}
}
