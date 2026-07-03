<?php
/**
 * Import a content-blueprint database export into a freshly spawned site.
 *
 * Reads the database.jsonl produced by the spawnwp-deploy exporter (records:
 * {"type":"table","name","create"} followed by {"type":"row","table","data"}
 * with base64-encoded column values), loads every table into temporary tables,
 * preserves this site's home/siteurl/admin_email, reassigns post authorship to
 * the fresh admin, then swaps everything over the live tables in one atomic
 * RENAME. No backup is kept: on failure the new-project.sh trap destroys the
 * whole site.
 *
 * Invoked by apply-content-blueprint.sh:
 *   wp eval-file import-database.php <database.jsonl> <admin-login>
 */

if ( ! defined( 'WP_CLI' ) || ! WP_CLI ) {
	exit( 1 );
}

if ( count( $args ) < 2 ) {
	WP_CLI::error( 'Usage: wp eval-file import-database.php <database.jsonl> <admin-login>' );
}
list( $import_file, $admin_login ) = $args;
if ( ! is_readable( $import_file ) ) {
	WP_CLI::error( "Database export not readable: {$import_file}" );
}
$admin = get_user_by( 'login', $admin_login );
if ( ! $admin ) {
	WP_CLI::error( "Admin user not found: {$admin_login}" );
}

/**
 * The blueprint manifest carries no source table prefix, so derive it from the
 * export itself: the prefix is the name of the options table minus "options",
 * confirmed by the presence of a sibling posts table.
 */
function spawnwp_source_prefix( string $file ): string {
	$handle = fopen( $file, 'rb' );
	if ( ! $handle ) {
		WP_CLI::error( 'Unable to open the database export.' );
	}
	$tables = array();
	while ( ( $line = fgets( $handle ) ) !== false ) {
		if ( 0 !== strncmp( $line, '{"type":"table"', 15 ) ) {
			continue;
		}
		$record = json_decode( $line, true, 512, JSON_THROW_ON_ERROR );
		$tables[ $record['name'] ] = true;
	}
	fclose( $handle );
	$candidates = array();
	foreach ( array_keys( $tables ) as $name ) {
		if ( str_ends_with( $name, 'options' ) ) {
			$prefix = substr( $name, 0, -strlen( 'options' ) );
			if ( isset( $tables[ $prefix . 'posts' ] ) ) {
				$candidates[] = $prefix;
			}
		}
	}
	if ( 1 !== count( $candidates ) ) {
		WP_CLI::error( 'Unable to determine the source table prefix from the export.' );
	}
	return $candidates[0];
}

global $wpdb;
$source_prefix = spawnwp_source_prefix( $import_file );
$short         = substr( md5( uniqid( '', true ) ), 0, 6 );

$handle = fopen( $import_file, 'rb' );
if ( ! $handle ) {
	WP_CLI::error( 'Unable to open the database export.' );
}
$map = array();
while ( ( $line = fgets( $handle ) ) !== false ) {
	$record = json_decode( $line, true, 512, JSON_THROW_ON_ERROR );
	if ( 'table' === $record['type'] ) {
		$suffix = str_starts_with( $record['name'], $source_prefix ) ? substr( $record['name'], strlen( $source_prefix ) ) : $record['name'];
		$live   = $wpdb->prefix . $suffix;
		$temp   = substr( $wpdb->prefix . 'bt' . $short . '_' . $suffix, 0, 64 );
		$old    = substr( $wpdb->prefix . 'bo' . $short . '_' . $suffix, 0, 64 );
		$ddl    = preg_replace( '/^CREATE TABLE\s+`[^`]+`/i', 'CREATE TABLE `' . esc_sql( $temp ) . '`', $record['create'] );
		$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $temp ) . '`' );
		if ( false === $wpdb->query( $ddl ) ) {
			WP_CLI::error( 'Unable to create temporary table for ' . $live );
		}
		$map[ $record['name'] ] = array(
			'live' => $live,
			'temp' => $temp,
			'old'  => $old,
		);
	} elseif ( 'row' === $record['type'] ) {
		$data = array();
		foreach ( $record['data'] as $column => $value ) {
			$data[ $column ] = null === $value ? null : base64_decode( $value, true );
		}
		if ( false === $wpdb->insert( $map[ $record['table'] ]['temp'], $data ) ) {
			WP_CLI::error( 'Unable to import a row into ' . $map[ $record['table'] ]['live'] );
		}
	}
}
fclose( $handle );

$options_live   = $wpdb->options;
$options_source = $source_prefix . 'options';
if ( isset( $map[ $options_source ] ) ) {
	$temp_options = $map[ $options_source ]['temp'];
	foreach ( array( 'home', 'siteurl', 'admin_email' ) as $name ) {
		$value = $wpdb->get_var( $wpdb->prepare( "SELECT option_value FROM {$options_live} WHERE option_name=%s", $name ) );
		$wpdb->query( $wpdb->prepare( "UPDATE `{$temp_options}` SET option_value=%s WHERE option_name=%s", $value, $name ) );
	}
}
$posts_source = $source_prefix . 'posts';
if ( isset( $map[ $posts_source ] ) ) {
	$wpdb->query( $wpdb->prepare( 'UPDATE `' . esc_sql( $map[ $posts_source ]['temp'] ) . '` SET post_author=%d', $admin->ID ) );
}
$comments_source = $source_prefix . 'comments';
if ( isset( $map[ $comments_source ] ) ) {
	$wpdb->query( 'UPDATE `' . esc_sql( $map[ $comments_source ]['temp'] ) . '` SET user_id=0' );
}

$renames = array();
foreach ( $map as $entry ) {
	$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $entry['old'] ) . '`' );
	if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $entry['live'] ) ) ) {
		$renames[] = '`' . esc_sql( $entry['live'] ) . '` TO `' . esc_sql( $entry['old'] ) . '`';
	}
	$renames[] = '`' . esc_sql( $entry['temp'] ) . '` TO `' . esc_sql( $entry['live'] ) . '`';
}
if ( false === $wpdb->query( 'RENAME TABLE ' . implode( ', ', $renames ) ) ) {
	WP_CLI::error( 'Atomic database table swap failed.' );
}
foreach ( $map as $entry ) {
	$wpdb->query( 'DROP TABLE IF EXISTS `' . esc_sql( $entry['old'] ) . '`' );
}

WP_CLI::success( 'Imported ' . count( $map ) . ' database tables.' );
