<?php
/**
 * Plugin Name: SpawnWP Auto-login
 * Description: Consumes single-use tokens minted by the SpawnWP cockpit so one click opens wp-admin already signed in.
 * Version: 1.0.0
 * Author: SpawnWP
 *
 * This is an authentication bypass by design, so it is deliberately narrow:
 *
 *  - it is opt-in per site — the cockpit writes this file only when asked, and
 *    deletes it when the feature is turned off. No file, no attack surface.
 *  - the raw token is never stored. The cockpit stores a transient keyed by the
 *    token's SHA-256; only someone holding the token can name that transient.
 *  - the transient is deleted BEFORE the user is authenticated, so two
 *    concurrent requests with the same token cannot both succeed.
 *  - tokens expire in minutes (TTL is set by the cockpit when minting).
 */

if (!defined('ABSPATH')) {
    exit;
}

const SPAWNWP_AUTOLOGIN_PREFIX = 'spawnwp_autologin_';

add_action('init', 'spawnwp_autologin_maybe_consume', 1);

function spawnwp_autologin_maybe_consume()
{
    if (empty($_GET['spawnwp_autologin'])) {
        return;
    }

    $token = $_GET['spawnwp_autologin'];
    if (!is_string($token) || !preg_match('/\A[A-Za-z0-9_-]{16,128}\z/', $token)) {
        spawnwp_autologin_fail();
    }

    // Look the token up by its hash: the plaintext exists only in the URL.
    $key = SPAWNWP_AUTOLOGIN_PREFIX . hash('sha256', $token);
    $user_id = get_transient($key);

    // Burn the token first. If anything below fails, the token is still spent —
    // that is the safe direction to fail in.
    delete_transient($key);

    if ($user_id === false) {
        spawnwp_autologin_fail();
    }

    $user_id = (int) $user_id;
    $user = get_user_by('id', $user_id);
    if (!$user) {
        spawnwp_autologin_fail();
    }

    wp_set_current_user($user_id, $user->user_login);
    wp_set_auth_cookie($user_id, false);
    do_action('wp_login', $user->user_login, $user);

    // Never leave the token sitting in the address bar or in the referrer of
    // whatever the admin loads next.
    wp_safe_redirect(admin_url());
    exit;
}

function spawnwp_autologin_fail()
{
    wp_die(
        esc_html__('This sign-in link is no longer valid. Generate a new one from the SpawnWP cockpit.', 'spawnwp'),
        esc_html__('Link expired', 'spawnwp'),
        array('response' => 403)
    );
}
