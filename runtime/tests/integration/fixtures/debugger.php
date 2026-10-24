<?php
/** Minimal DBGp client, reachable only on the isolated Compose network. */
function frame($connection) {
    $length = '';
    while (true) {
        $char = fread($connection, 1);
        if ($char === false || $char === '') { throw new RuntimeException('Debugger disconnected'); }
        if ($char === "\0") { break; }
        $length .= $char;
        if (strlen($length) > 10) { throw new RuntimeException('Invalid frame length'); }
    }
    if (!ctype_digit($length) || (int) $length > 1048576) { throw new RuntimeException('Invalid frame'); }
    $body = '';
    while (strlen($body) < (int) $length) {
        $chunk = fread($connection, (int) $length - strlen($body));
        if ($chunk === false || $chunk === '') { throw new RuntimeException('Truncated frame'); }
        $body .= $chunk;
    }
    if (fread($connection, 1) !== "\0") { throw new RuntimeException('Missing frame terminator'); }
    return $body;
}
$server = stream_socket_server('tcp://0.0.0.0:9003', $number, $message);
if (!$server) { throw new RuntimeException($message); }
echo "ready\n";
fflush(STDOUT);
$connection = stream_socket_accept($server, 20);
if (!$connection) { throw new RuntimeException('Xdebug did not connect'); }
stream_set_timeout($connection, 20);
if (strpos(frame($connection), '<init') === false) { throw new RuntimeException('Missing init'); }
fwrite($connection, "breakpoint_set -i 1 -t line -f file:///var/www/html/wp-content/spawnwp-xdebug-test.php -n 3\0");
frame($connection);
fwrite($connection, "run -i 2\0");
if (strpos(frame($connection), 'status="break"') === false) { throw new RuntimeException('Breakpoint not reached'); }
fwrite($connection, "run -i 3\0");
frame($connection);
fclose($connection);
fclose($server);
echo "breakpoint reached\n";
