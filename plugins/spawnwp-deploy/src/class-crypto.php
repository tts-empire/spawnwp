<?php

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SpawnWP_Deploy_Crypto {
	const CLOCK_SKEW = 300;

	public static function generate_keypair(): array {
		$keypair = sodium_crypto_sign_keypair();
		return array(
			'public'  => base64_encode( sodium_crypto_sign_publickey( $keypair ) ),
			'private' => self::encrypt( sodium_crypto_sign_secretkey( $keypair ) ),
		);
	}

	public static function encrypt( string $plaintext ): string {
		$nonce  = random_bytes( SODIUM_CRYPTO_SECRETBOX_NONCEBYTES );
		$cipher = sodium_crypto_secretbox( $plaintext, $nonce, self::storage_key() );
		return base64_encode( $nonce . $cipher );
	}

	public static function decrypt( string $encoded ): string {
		$raw = base64_decode( $encoded, true );
		if ( false === $raw || strlen( $raw ) <= SODIUM_CRYPTO_SECRETBOX_NONCEBYTES ) {
			throw new RuntimeException( 'Invalid encrypted key material.' );
		}
		$nonce = substr( $raw, 0, SODIUM_CRYPTO_SECRETBOX_NONCEBYTES );
		$plain = sodium_crypto_secretbox_open( substr( $raw, SODIUM_CRYPTO_SECRETBOX_NONCEBYTES ), $nonce, self::storage_key() );
		if ( false === $plain ) {
			throw new RuntimeException( 'Unable to decrypt key material.' );
		}
		return $plain;
	}

	private static function storage_key(): string {
		$auth   = defined( 'AUTH_KEY' ) ? (string) AUTH_KEY : '';
		$secure = defined( 'SECURE_AUTH_KEY' ) ? (string) SECURE_AUTH_KEY : '';
		if ( '' === $auth && '' === $secure ) {
			// Degenerate install with no WordPress salts: without this guard the
			// key material would be a constant ('|'). Derive from a random secret
			// generated once and stored (non-autoloaded) instead. Installs that
			// have salts keep the exact previous derivation, so already-encrypted
			// key material still decrypts with no migration.
			$fallback = get_option( 'spawnwp_deploy_storage_key' );
			if ( ! is_string( $fallback ) || '' === $fallback ) {
				add_option( 'spawnwp_deploy_storage_key', base64_encode( random_bytes( 32 ) ), '', false );
				$fallback = get_option( 'spawnwp_deploy_storage_key' );
			}
			if ( ! is_string( $fallback ) || '' === $fallback ) {
				throw new RuntimeException( 'Unable to establish a key-storage secret.' );
			}
			$material = 'fallback|' . $fallback;
		} else {
			$material = $auth . '|' . $secure;
		}
		return hash_hkdf( 'sha256', $material, SODIUM_CRYPTO_SECRETBOX_KEYBYTES, 'spawnwp-deploy-storage-v1' );
	}

	public static function canonical( string $method, string $path, int $timestamp, string $nonce, string $body ): string {
		return strtoupper( $method ) . "\n" . $path . "\n" . $timestamp . "\n" . $nonce . "\n" . hash( 'sha256', $body );
	}

	public static function sign( string $private_encrypted, string $method, string $path, int $timestamp, string $nonce, string $body ): string {
		$secret = self::decrypt( $private_encrypted );
		return base64_encode( sodium_crypto_sign_detached( self::canonical( $method, $path, $timestamp, $nonce, $body ), $secret ) );
	}

	public static function verify( string $public_b64, string $signature_b64, string $method, string $path, int $timestamp, string $nonce, string $body ): bool {
		$public    = base64_decode( $public_b64, true );
		$signature = base64_decode( $signature_b64, true );
		if ( false === $public || false === $signature || abs( time() - $timestamp ) > self::CLOCK_SKEW ) {
			return false;
		}
		return sodium_crypto_sign_verify_detached( $signature, self::canonical( $method, $path, $timestamp, $nonce, $body ), $public );
	}

	public static function random_token( int $bytes = 32 ): string {
		return rtrim( strtr( base64_encode( random_bytes( $bytes ) ), '+/', '-_' ), '=' );
	}
}
