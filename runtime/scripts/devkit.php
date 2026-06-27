<?php
/**
 * Plugin Name: SpawnWP Development Toolkit
 * Description: Development links and local email routing for SpawnWP environments.
 * Version: 1.0.0
 */
if ( ! defined( 'ABSPATH' ) ) { exit; }
add_action( 'phpmailer_init', static function ( $mailer ) {
	$mailer->isSMTP();
	$mailer->Host = defined( 'SMTP_HOST' ) ? SMTP_HOST : 'mailpit';
	$mailer->Port = defined( 'SMTP_PORT' ) ? SMTP_PORT : 1025;
	$mailer->SMTPAuth = false;
	$mailer->SMTPAutoTLS = false;
} );
add_action( 'wp_dashboard_setup', static function () {
	if ( ! current_user_can( 'manage_options' ) ) { return; }
	wp_add_dashboard_widget( 'spawnwp_devkit', 'SpawnWP Dev toolkit', static function () {
		$links = array(
			'Plugin Check' => admin_url( 'tools.php?page=plugin-check' ),
			'Theme Check' => admin_url( 'themes.php?page=themecheck' ),
			'WP Crontrol' => admin_url( 'tools.php?page=crontrol_admin_manage_page' ),
			'Users' => admin_url( 'users.php' ),
		);
		echo '<ul>';
		foreach ( $links as $label => $url ) {
			printf( '<li><a href="%s">%s</a></li>', esc_url( $url ), esc_html( $label ) );
		}
		echo '</ul><p><code>phpcs &lt;plugin-path&gt;</code><br><code>phpstan analyse -c /usr/local/etc/phpstan-wp.neon &lt;plugin-path&gt;</code></p>';
		printf( '<p>WordPress %s | PHP %s | WP_DEBUG %s</p>', esc_html( get_bloginfo( 'version' ) ), esc_html( phpversion() ), ( defined( 'WP_DEBUG' ) && WP_DEBUG ) ? 'on' : 'off' );
	} );
}, 20 );
