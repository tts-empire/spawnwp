<?php
/**
 * Plugin Name: SpawnWP blueprint safety notice
 * Description: Reminds the admin to reactivate captured plugins one at a time.
 *
 * Installed by SpawnWP into mu-plugins only when a site is spawned from a
 * captured content blueprint with "start with all plugins deactivated". It reads
 * the spawnwp_deactivated_plugins option and shows a dismissible admin notice;
 * dismissing (or clearing it) deletes the option so it never shows again.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_action(
	'admin_init',
	static function () {
		if ( empty( $_GET['spawnwp_dismiss_deactivated'] ) ) {
			return;
		}
		if ( ! current_user_can( 'manage_options' ) || ! check_admin_referer( 'spawnwp_dismiss_deactivated' ) ) {
			return;
		}
		delete_option( 'spawnwp_deactivated_plugins' );
		wp_safe_redirect( remove_query_arg( array( 'spawnwp_dismiss_deactivated', '_wpnonce' ) ) );
		exit;
	}
);

add_action(
	'admin_notices',
	static function () {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$deactivated = get_option( 'spawnwp_deactivated_plugins', '' );
		if ( '' === $deactivated || false === $deactivated ) {
			return;
		}
		$names = array_filter( array_map( 'trim', explode( ',', (string) $deactivated ) ) );
		$count = count( $names );

		$dismiss = wp_nonce_url(
			add_query_arg( 'spawnwp_dismiss_deactivated', '1' ),
			'spawnwp_dismiss_deactivated'
		);
		?>
		<div class="notice notice-warning">
			<p>
				<strong><?php esc_html_e( 'SpawnWP: plugins from this captured blueprint are deactivated.', 'spawnwp' ); ?></strong>
				<?php
				printf(
					/* translators: %d: number of deactivated plugins. */
					esc_html( _n(
						'%d plugin from the captured site was installed but left inactive for safety. Reactivate them one at a time under Plugins so you can catch a security or login-lockout plugin before it locks you out.',
						'%d plugins from the captured site were installed but left inactive for safety. Reactivate them one at a time under Plugins so you can catch a security or login-lockout plugin before it locks you out.',
						$count,
						'spawnwp'
					) ),
					(int) $count
				);
				?>
			</p>
			<?php if ( $names ) : ?>
				<p><code><?php echo esc_html( implode( ', ', $names ) ); ?></code></p>
			<?php endif; ?>
			<p>
				<a href="<?php echo esc_url( admin_url( 'plugins.php' ) ); ?>" class="button button-primary"><?php esc_html_e( 'Go to Plugins', 'spawnwp' ); ?></a>
				<a href="<?php echo esc_url( $dismiss ); ?>" class="button"><?php esc_html_e( 'Dismiss', 'spawnwp' ); ?></a>
			</p>
		</div>
		<?php
	}
);
