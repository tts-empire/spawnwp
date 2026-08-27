<?php
/**
 * Plugin Name: SpawnWP Restricted Administrator
 * Description: Removes host-affecting capabilities from temporary evaluation users.
 * Version: 1.0.0
 * Author: SpawnWP
 */

if (!defined('ABSPATH')) {
    exit;
}
if (!defined('DISALLOW_FILE_EDIT')) {
    define('DISALLOW_FILE_EDIT', true);
}
if (!defined('DISALLOW_FILE_MODS')) {
    define('DISALLOW_FILE_MODS', true);
}

const SPAWNWP_RESTRICTED_CAPABILITIES = array(
    'activate_plugins', 'create_users', 'delete_plugins', 'delete_themes',
    'delete_users', 'edit_files', 'edit_plugins', 'edit_themes', 'edit_users',
    'export', 'import', 'install_plugins', 'install_themes', 'list_users',
    'promote_users', 'remove_users', 'switch_themes', 'update_core',
    'update_plugins', 'update_themes',
);

add_filter('user_has_cap', 'spawnwp_restrict_evaluation_capabilities', PHP_INT_MAX, 4);

function spawnwp_restrict_evaluation_capabilities($allcaps, $caps, $args, $user)
{
    foreach (SPAWNWP_RESTRICTED_CAPABILITIES as $capability) {
        $allcaps[$capability] = false;
    }
    return $allcaps;
}
