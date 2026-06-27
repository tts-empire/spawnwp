<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_Package {
	const MAX_BYTES   = 2147483648;
	const DEV_PLUGINS = array(
		'plugin-check',
		'query-monitor',
		'theme-check',
		'user-switching',
		'wp-crontrol',
		'spawnwp-deploy',
	);

	public static function workspace( string $job_id ): string {
		return WP_CONTENT_DIR . '/.spawnwp-deploy/jobs/' . sanitize_file_name( $job_id );
	}

	public static function prepare( string $job_id, string $target_url, array $target_env ): array {
		global $wpdb, $wp_version;
		$workspace = self::workspace( $job_id );
		if ( ! wp_mkdir_p( $workspace ) ) {
			throw new RuntimeException( 'Unable to create package workspace.' );
		}

		$db_file = $workspace . '/database.jsonl';
		self::export_database( $db_file, untrailingslashit( home_url() ), untrailingslashit( $target_url ) );

		$archive = $workspace . '/payload.zip';
		$zip     = new ZipArchive();
		if ( true !== $zip->open( $archive, ZipArchive::CREATE | ZipArchive::OVERWRITE ) ) {
			throw new RuntimeException( 'Unable to create payload ZIP.' );
		}
		$zip->addFile( $db_file, 'database.jsonl' );
		self::add_tree( $zip, WP_PLUGIN_DIR, 'content/plugins', 'plugins' );
		self::add_tree( $zip, get_theme_root(), 'content/themes', 'themes' );
		$uploads = wp_get_upload_dir()['basedir'];
		if ( is_dir( $uploads ) ) {
			self::add_tree( $zip, $uploads, 'content/uploads', 'uploads' );
		}
		$zip->close();

		$size = filesize( $archive );
		if ( false === $size || $size > self::MAX_BYTES ) {
			@unlink( $archive );
			throw new RuntimeException( 'Payload exceeds the 2 GiB v1 limit.' );
		}

		$chunk_size = min( 4 * MB_IN_BYTES, max( 256 * KB_IN_BYTES, (int) ( ( $target_env['max_body_bytes'] ?? 8 * MB_IN_BYTES ) / 2 ) ) );
		$manifest   = array(
			'format'         => 1,
			'job_id'         => $job_id,
			'created_at'     => gmdate( 'c' ),
			'source_url'     => untrailingslashit( home_url() ),
			'target_url'     => untrailingslashit( $target_url ),
			'wordpress'      => $wp_version,
			'php'            => PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION,
			'source_prefix'  => $wpdb->prefix,
			'archive_bytes'  => $size,
			'archive_sha256' => hash_file( 'sha256', $archive ),
			'chunk_size'     => $chunk_size,
			'chunk_count'    => (int) ceil( $size / $chunk_size ),
			'exclusions'     => array( 'core', 'wp-config.php', 'users', 'usermeta', 'mu-plugins', 'drop-ins', 'cache', 'logs', 'backups', 'spawnwp-dev-tools' ),
		);
		file_put_contents( $workspace . '/manifest.json', wp_json_encode( $manifest, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES ) );
		return $manifest;
	}

	public static function chunk( string $job_id, int $index, int $chunk_size ): string {
		$file   = self::workspace( $job_id ) . '/payload.zip';
		$handle = fopen( $file, 'rb' );
		if ( ! $handle ) {
			throw new RuntimeException( 'Package archive not found.' );
		}
		fseek( $handle, $index * $chunk_size );
		$data = fread( $handle, $chunk_size );
		fclose( $handle );
		if ( false === $data ) {
			throw new RuntimeException( 'Unable to read package chunk.' );
		}
		return $data;
	}

	public static function cleanup( string $job_id ): void {
		self::remove_tree( self::workspace( $job_id ) );
	}

	private static function export_database( string $file, string $source_url, string $target_url ): void {
		global $wpdb;
		$handle = fopen( $file, 'wb' );
		if ( ! $handle ) {
			throw new RuntimeException( 'Unable to create database export.' );
		}

		$excluded = array_merge( SpawnWP_Deploy_Database::control_tables(), array( $wpdb->users, $wpdb->usermeta ) );
		$tables   = $wpdb->get_results( 'SHOW FULL TABLES WHERE Table_type = "BASE TABLE"', ARRAY_N );
		foreach ( $tables as $row ) {
			$table = $row[0];
			if ( in_array( $table, $excluded, true ) ) {
				continue;
			}
			$foreign_keys = (int) $wpdb->get_var(
				$wpdb->prepare(
					'SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL',
					$table
				)
			);
			if ( $foreign_keys ) {
				fclose( $handle );
				throw new RuntimeException( "Foreign keys are not supported in v1 ({$table})." );
			}
			$create_row = $wpdb->get_row( 'SHOW CREATE TABLE `' . esc_sql( $table ) . '`', ARRAY_N );
			self::write_jsonl(
				$handle,
				array(
					'type'   => 'table',
					'name'   => $table,
					'create' => $create_row[1],
				)
			);

			$offset = 0;
			do {
				$rows = $wpdb->get_results( 'SELECT * FROM `' . esc_sql( $table ) . '` LIMIT 500 OFFSET ' . (int) $offset, ARRAY_A );
				foreach ( $rows as $db_row ) {
					if ( $table === $wpdb->options && isset( $db_row['option_name'] ) ) {
						if ( str_starts_with( $db_row['option_name'], 'spawnwp_deploy_' ) ) {
							continue;
						}
						if ( 'active_plugins' === $db_row['option_name'] ) {
							$db_row['option_value'] = self::filter_active_plugins( $db_row['option_value'] );
						}
					}
					$encoded = array();
					foreach ( $db_row as $column => $value ) {
						if ( null === $value ) {
							$encoded[ $column ] = null;
							continue;
						}
						$value              = self::replace_value( (string) $value, $source_url, $target_url );
						$encoded[ $column ] = base64_encode( $value );
					}
					self::write_jsonl(
						$handle,
						array(
							'type'  => 'row',
							'table' => $table,
							'data'  => $encoded,
						)
					);
				}
				$offset += count( $rows );
			} while ( count( $rows ) === 500 );
		}
		fclose( $handle );
	}

	private static function filter_active_plugins( string $serialized ): string {
		$plugins = maybe_unserialize( $serialized );
		if ( ! is_array( $plugins ) ) {
			return $serialized;
		}
		$plugins   = array_values(
			array_filter(
				$plugins,
				static function ( $plugin ) {
					$slug = explode( '/', (string) $plugin )[0];
					return ! in_array( $slug, self::DEV_PLUGINS, true );
				}
			)
		);
		$plugins[] = 'spawnwp-deploy/spawnwp-deploy.php';
		return maybe_serialize( array_values( array_unique( $plugins ) ) );
	}

	private static function replace_value( string $value, string $from, string $to ): string {
		if ( ! str_contains( $value, $from ) ) {
			return $value;
		}
		if ( is_serialized( $value ) ) {
			$data = @unserialize( $value, array( 'allowed_classes' => false ) );
			if ( false === $data && 'b:0;' !== $value ) {
				throw new RuntimeException( 'Unable to safely decode serialized database data.' );
			}
			if ( self::contains_object( $data ) ) {
				throw new RuntimeException( 'Serialized objects containing the source URL are not supported in v1.' );
			}
			return serialize( self::replace_recursive( $data, $from, $to ) );
		}
		return str_replace( $from, $to, $value );
	}

	private static function replace_recursive( $value, string $from, string $to ) {
		if ( is_string( $value ) ) {
			return str_replace( $from, $to, $value );
		}
		if ( is_array( $value ) ) {
			foreach ( $value as $key => $item ) {
				$value[ $key ] = self::replace_recursive( $item, $from, $to );
			}
		}
		return $value;
	}

	private static function contains_object( $value ): bool {
		if ( is_object( $value ) ) {
			return true;
		}
		if ( is_array( $value ) ) {
			foreach ( $value as $item ) {
				if ( self::contains_object( $item ) ) {
					return true;
				}
			}
		}
		return false;
	}

	private static function write_jsonl( $handle, array $record ): void {
		$line = wp_json_encode( $record, JSON_UNESCAPED_SLASHES );
		if ( false === $line || false === fwrite( $handle, $line . "\n" ) ) {
			throw new RuntimeException( 'Unable to write database package.' );
		}
	}

	private static function add_tree( ZipArchive $zip, string $root, string $archive_root, string $kind ): void {
		if ( ! is_dir( $root ) ) {
			return;
		}
		$root     = rtrim( $root, '/\\' );
		$iterator = new RecursiveIteratorIterator( new RecursiveDirectoryIterator( $root, FilesystemIterator::SKIP_DOTS ) );
		foreach ( $iterator as $file ) {
			if ( $file->isLink() ) {
				throw new RuntimeException( 'Symlinks are not supported: ' . $file->getPathname() );
			}
			if ( ! $file->isFile() ) {
				continue;
			}
			$relative = ltrim( str_replace( '\\', '/', substr( $file->getPathname(), strlen( $root ) ) ), '/' );
			$top      = explode( '/', $relative )[0];
			if ( 'plugins' === $kind && in_array( $top, self::DEV_PLUGINS, true ) ) {
				continue;
			}
			if ( self::excluded_path( $relative ) ) {
				continue;
			}
			$zip->addFile( $file->getPathname(), $archive_root . '/' . $relative );
		}
	}

	private static function excluded_path( string $path ): bool {
		$parts = explode( '/', strtolower( $path ) );
		foreach ( $parts as $part ) {
			if ( in_array( $part, array( 'cache', 'backups', 'backup', 'upgrade', 'upgrade-temp-backup', '.git', 'node_modules' ), true ) ) {
				return true;
			}
		}
		return (bool) preg_match( '/(?:debug|error)\.log$/i', $path );
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
