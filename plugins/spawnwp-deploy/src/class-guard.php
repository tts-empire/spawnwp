<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_Guard {
	public static function compatibility_warnings( array $source, array $target ): array {
		$warnings = array();
		if ( ( $source['wordpress'] ?? '' ) !== ( $target['wordpress'] ?? '' ) ) {
			$warnings[] = sprintf( 'WordPress versions differ: source %s, destination %s.', $source['wordpress'] ?? 'unknown', $target['wordpress'] ?? 'unknown' );
		}
		if ( ( $source['php'] ?? '' ) !== ( $target['php'] ?? '' ) ) {
			$warnings[] = sprintf( 'PHP versions differ: source %s, destination %s.', $source['php'] ?? 'unknown', $target['php'] ?? 'unknown' );
		}
		return $warnings;
	}

	public static function target_report(): array {
		$issues = array();
		if ( is_multisite() ) {
			$issues[] = 'WordPress Multisite is not supported.';
		}
		if ( get_option( 'spawnwp_deploy_completed_at' ) ) {
			$issues[] = 'This target has already received a SpawnWP deployment.';
		}

		$content = self::content_counts();
		if ( $content['non_default_posts'] > 0 ) {
			$issues[] = 'The target contains non-default posts, pages, or custom content.';
		}
		if ( $content['uploads'] > 0 ) {
			$issues[] = 'The target uploads directory is not empty.';
		}
		if ( $content['non_default_comments'] > 0 ) {
			$issues[] = 'The target contains comments or form-like interaction data.';
		}
		if ( $content['extra_users'] > 0 ) {
			$issues[] = 'The target contains additional non-administrator users.';
		}
		if ( $content['application_plugins'] ) {
			$issues[] = 'Deactivate/remove application plugins before pairing: ' . implode( ', ', $content['application_plugins'] );
		}

		$writable = wp_is_writable( WP_CONTENT_DIR ) && wp_is_writable( WP_PLUGIN_DIR ) && wp_is_writable( get_theme_root() );
		if ( ! $writable ) {
			$issues[] = 'Direct write access to wp-content, plugins, and themes is required.';
		}

		return array(
			'ok'          => empty( $issues ),
			'issues'      => $issues,
			'counts'      => $content,
			'environment' => self::environment(),
		);
	}

	public static function environment(): array {
		global $wpdb, $wp_version;
		return array(
			'home_url'       => home_url( '/' ),
			'wordpress'      => $wp_version,
			'php'            => PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION,
			'db'             => $wpdb->db_version(),
			'table_prefix'   => $wpdb->prefix,
			'content_dir'    => WP_CONTENT_DIR,
			'uploads'        => wp_get_upload_dir()['basedir'],
			'free_bytes'     => @disk_free_space( WP_CONTENT_DIR ) ?: 0,
			'sodium'         => extension_loaded( 'sodium' ),
			'zip'            => class_exists( 'ZipArchive' ),
			'multisite'      => is_multisite(),
			'max_body_bytes' => wp_convert_hr_to_bytes( ini_get( 'post_max_size' ) ?: '8M' ),
		);
	}

	private static function content_counts(): array {
		global $wpdb;
		$sql = "SELECT COUNT(*) FROM {$wpdb->posts}
			WHERE post_status NOT IN ('auto-draft','trash')
			AND post_type NOT IN ('revision','nav_menu_item','customize_changeset','wp_global_styles','wp_navigation')
			AND NOT (
				(ID = 1 AND post_type = 'post')
				OR (ID IN (2,3) AND post_type = 'page')
			)";
		$non_default_posts = (int) $wpdb->get_var( $sql );

		$uploads_dir  = wp_get_upload_dir()['basedir'];
		$upload_count = 0;
		if ( is_dir( $uploads_dir ) ) {
			$iterator = new RecursiveIteratorIterator( new RecursiveDirectoryIterator( $uploads_dir, FilesystemIterator::SKIP_DOTS ) );
			foreach ( $iterator as $file ) {
				if ( $file->isFile() && 'index.php' !== $file->getFilename() ) {
					++$upload_count;
					break;
				}
			}
		}

		$extra_users = 0;
		foreach ( get_users() as $user ) {
			if ( ! user_can( $user, 'manage_options' ) ) {
				++$extra_users;
			}
		}

		require_once ABSPATH . 'wp-admin/includes/plugin.php';
		$allowed             = array( plugin_basename( SPAWNWP_DEPLOY_FILE ), 'akismet/akismet.php', 'hello.php' );
		$application_plugins = array_values( array_diff( get_option( 'active_plugins', array() ), $allowed ) );
		$comment_count       = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->comments} WHERE NOT (comment_ID = 1 AND comment_post_ID = 1)" );

		return array(
			'non_default_posts'    => $non_default_posts,
			'non_default_comments' => $comment_count,
			'uploads'              => $upload_count,
			'extra_users'          => $extra_users,
			'application_plugins'  => $application_plugins,
		);
	}
}
