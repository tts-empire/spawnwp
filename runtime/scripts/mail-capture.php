<?php
/**
 * Plugin Name: SpawnWP Mail Capture
 * Description: Routes all outgoing site email to the site's own Mailpit instance.
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
