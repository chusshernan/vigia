<?php
/**
 * Vigía — lista de IPs bloqueadas que maneja el Vigía, y lectura de los logs.
 *
 * Se instala como mu-plugin (con «aa-» delante para que cargue el primero):
 *   wp-content/mu-plugins/aa-vigia-bloqueos.php
 *
 * QUÉ HACE
 * 1. A cada visita que pasa por WordPress, si la IP está en la lista y su bloqueo no
 *    ha caducado, responde 403 y no sigue. Es lo primero que se ejecuta: el ataque no
 *    llega ni a cargar los plugins.
 * 2. Abre en la API tres órdenes para el Vigía (pulsar «Bloquear» en Telegram):
 *      GET    /wp-json/vigia/v1/bloqueos             la lista
 *      POST   /wp-json/vigia/v1/bloqueos  {ip, dias, motivo}
 *      DELETE /wp-json/vigia/v1/bloqueos/<ip>        (o POST con X-HTTP-Method-Override)
 * 3. SENSOR: apunta cada visita que pasa por WordPress en un registro propio, con el
 *    mismo formato que los logs de Apache, y los errores graves de PHP en otro:
 *      <cuenta>/private/vigia/sensor-acceso.log   y   sensor-errores.log
 *    (fuera de la web: no se pueden abrir desde internet). Hace falta porque en muchos
 *    hostings compartidos el PHP no puede leer los logs de Apache (open_basedir) y no
 *    hay cuentas FTP de solo lectura. Cada fichero rota al pasar de 3 MB (queda uno .1).
 *    Si no puede escribir ahí, no hace nada.
 * 4. Deja al Vigía leer esos registros (y los de Apache, si algún día se puede), a trozos:
 *      GET    /wp-json/vigia/v1/log?fichero=sensor-acceso.log&desde=<byte>
 *    Solo los ficheros de VIGIA_LOGS; no acepta rutas.
 *
 * SEGURIDAD
 * - Solo los usuarios de VIGIA_USUARIOS pueden usar la API. El del Vigía es un
 *   SUSCRIPTOR: con su contraseña de aplicación no se puede hacer nada más en la web.
 * - Nunca bloquea la IP de quien da la orden (si no, el Vigía se cerraría la puerta),
 *   ni la del propio servidor, ni direcciones privadas.
 * - Las llamadas a la propia API del Vigía no se bloquean nunca, para poder deshacer
 *   un bloqueo equivocado desde cualquier sitio.
 * - Todo bloqueo caduca (máximo 90 días). Como mucho, 2000 IPs.
 * - La lista vive en una opción de WordPress (vigia_bloqueos); no escribe ficheros.
 *
 * Solo frena lo que pasa por WordPress (páginas, login, xmlrpc, API). Los ficheros
 * estáticos (imágenes) los sirve Apache directamente.
 *
 * PARA REVERTIR: borrar este fichero. La opción vigia_bloqueos se puede dejar.
 */
if ( ! defined( 'ABSPATH' ) ) { return; }

define( 'VIGIA_USUARIOS', array( 'vigia' ) );
define( 'VIGIA_OPCION', 'vigia_bloqueos' );
// AJUSTAR: la IP pública del propio servidor (nunca se bloquea).
define( 'VIGIA_IP_SERVIDOR', '203.0.113.10' );
// AJUSTAR: carpeta de los logs de Apache, relativa a la raíz de la cuenta (en Plesk, logs/<dominio>).
define( 'VIGIA_CARPETA_APACHE', 'logs/example.com' );
define( 'VIGIA_MAX_DIAS', 90 );
define( 'VIGIA_MAX_IPS', 2000 );
// Fichero => carpeta donde está. No se acepta ningún otro nombre.
define( 'VIGIA_LOGS', array(
	'sensor-acceso.log'        => 'sensor',
	'sensor-acceso.log.1'      => 'sensor',
	'sensor-errores.log'       => 'sensor',
	'sensor-errores.log.1'     => 'sensor',
	'access_ssl_log'           => 'apache',
	'access_ssl_log.processed' => 'apache',
	'error_log'                => 'apache',
	'error_log.1.gz'           => 'apache',
) );
define( 'VIGIA_SENSOR_MAX', 3 * 1024 * 1024 );
define( 'VIGIA_LOG_TROZO', 2 * 1024 * 1024 );

/** Raíz de la cuenta: la web está en <cuenta>/<dominio>/ */
function vigia_raiz_cuenta() {
	return dirname( untrailingslashit( ABSPATH ) );
}

function vigia_carpeta( $tipo ) {
	return 'sensor' === $tipo ? vigia_raiz_cuenta() . '/private/vigia' : vigia_raiz_cuenta() . '/' . VIGIA_CARPETA_APACHE;
}

function vigia_bloqueos_lista() {
	$lista = get_option( VIGIA_OPCION, array() );
	return is_array( $lista ) ? $lista : array();
}

function vigia_es_su_api() {
	$uri = isset( $_SERVER['REQUEST_URI'] ) ? $_SERVER['REQUEST_URI'] : '';
	return false !== strpos( $uri, '/wp-json/vigia/' ) || false !== strpos( $uri, 'rest_route=/vigia/' )
		|| false !== strpos( $uri, 'rest_route=%2Fvigia%2F' );
}

// --- 1. El portero -----------------------------------------------------------
( function () {
	$ip = isset( $_SERVER['REMOTE_ADDR'] ) ? $_SERVER['REMOTE_ADDR'] : '';
	if ( '' === $ip || vigia_es_su_api() ) { return; }
	$lista = vigia_bloqueos_lista();
	if ( empty( $lista[ $ip ] ) ) { return; }
	if ( (int) $lista[ $ip ]['hasta'] < time() ) { return; }  // caducado: se limpia al guardar
	if ( ! headers_sent() ) {
		header( 'HTTP/1.1 403 Forbidden', true, 403 );
		header( 'Content-Type: text/plain; charset=utf-8' );
		header( 'Cache-Control: no-store' );
	}
	echo 'Acceso bloqueado.';
	exit;
} )();

// --- 3. El sensor ------------------------------------------------------------
function vigia_sensor_escribir( $fichero, $linea ) {
	$dir = vigia_carpeta( 'sensor' );
	if ( ! @is_dir( $dir ) ) {
		if ( ! @mkdir( $dir, 0700, true ) ) { return; }
		@file_put_contents( $dir . '/.htaccess', "Require all denied\n" );
	}
	$ruta = $dir . '/' . $fichero;
	clearstatcache( true, $ruta );
	if ( @filesize( $ruta ) > VIGIA_SENSOR_MAX ) {
		@rename( $ruta, $ruta . '.1' );
	}
	@file_put_contents( $ruta, $linea . "\n", FILE_APPEND | LOCK_EX );
}

function vigia_sensor_hora( $formato ) {
	try {
		// La zona horaria de WordPress (Ajustes → Generales), como la de los logs de Apache.
		$d = new DateTime( 'now', function_exists( 'wp_timezone' ) ? wp_timezone() : new DateTimeZone( 'UTC' ) );
	} catch ( Exception $e ) {
		return gmdate( $formato );
	}
	return $d->format( $formato );
}

function vigia_sensor_limpio( $texto, $largo ) {
	return substr( str_replace( array( '"', "\n", "\r" ), array( '%22', ' ', ' ' ), (string) $texto ), 0, $largo );
}

register_shutdown_function( function () {
	try {
		$ip = isset( $_SERVER['REMOTE_ADDR'] ) ? $_SERVER['REMOTE_ADDR'] : '-';
		// Errores graves de PHP, con el formato del error_log de Apache
		$e = error_get_last();
		if ( $e && in_array( $e['type'], array( E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR ), true ) ) {
			$archivo = str_replace( vigia_raiz_cuenta(), '', (string) $e['file'] );
			vigia_sensor_escribir( 'sensor-errores.log', sprintf( '[%s.000000 %s] [php:error] [pid %d] [client %s:0] PHP Fatal error:  %s in %s:%d',
				vigia_sensor_hora( 'D M d H:i:s' ), vigia_sensor_hora( 'Y' ), getmypid(), $ip,
				vigia_sensor_limpio( $e['message'], 400 ), $archivo, $e['line'] ) );
		}
		// La visita, con el formato del access_log de Apache (sin tamaño: no se conoce aquí)
		$codigo = http_response_code();
		vigia_sensor_escribir( 'sensor-acceso.log', sprintf( '%s - - [%s] "%s %s HTTP/1.1" %d - "%s" "%s"',
			$ip, vigia_sensor_hora( 'd/M/Y:H:i:s O' ),
			vigia_sensor_limpio( isset( $_SERVER['REQUEST_METHOD'] ) ? $_SERVER['REQUEST_METHOD'] : '-', 10 ),
			vigia_sensor_limpio( isset( $_SERVER['REQUEST_URI'] ) ? $_SERVER['REQUEST_URI'] : '-', 500 ),
			$codigo ? $codigo : 200,
			vigia_sensor_limpio( isset( $_SERVER['HTTP_REFERER'] ) ? $_SERVER['HTTP_REFERER'] : '-', 300 ),
			vigia_sensor_limpio( isset( $_SERVER['HTTP_USER_AGENT'] ) ? $_SERVER['HTTP_USER_AGENT'] : '-', 300 ) ) );
	} catch ( Throwable $t ) {
		// El sensor nunca debe romper la web.
	}
} );

// --- 4. La API para el Vigía -------------------------------------------------
function vigia_permiso() {
	$u = wp_get_current_user();
	return $u && $u->exists() && in_array( $u->user_login, VIGIA_USUARIOS, true );
}

function vigia_limpiar( $lista ) {
	$ahora = time();
	foreach ( $lista as $ip => $b ) {
		if ( (int) $b['hasta'] < $ahora ) { unset( $lista[ $ip ] ); }
	}
	return $lista;
}

function vigia_respuesta_lista() {
	return array( 'bloqueos' => vigia_limpiar( vigia_bloqueos_lista() ) );
}

function vigia_bloquear( WP_REST_Request $req ) {
	$ip     = trim( (string) $req->get_param( 'ip' ) );
	$dias   = (int) $req->get_param( 'dias' );
	$motivo = sanitize_text_field( (string) $req->get_param( 'motivo' ) );
	if ( ! filter_var( $ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE ) ) {
		return new WP_Error( 'vigia_ip', 'IP no válida (o privada).', array( 'status' => 400 ) );
	}
	$quien = isset( $_SERVER['REMOTE_ADDR'] ) ? $_SERVER['REMOTE_ADDR'] : '';
	if ( $ip === $quien ) {
		return new WP_Error( 'vigia_propia', 'Esa es la IP desde la que se da la orden: no se bloquea.', array( 'status' => 409 ) );
	}
	if ( VIGIA_IP_SERVIDOR === $ip ) {
		return new WP_Error( 'vigia_servidor', 'Esa es la IP del propio servidor.', array( 'status' => 409 ) );
	}
	$dias  = max( 1, min( VIGIA_MAX_DIAS, $dias ? $dias : 7 ) );
	$lista = vigia_limpiar( vigia_bloqueos_lista() );
	if ( count( $lista ) >= VIGIA_MAX_IPS && empty( $lista[ $ip ] ) ) {
		return new WP_Error( 'vigia_lleno', 'La lista está llena.', array( 'status' => 409 ) );
	}
	$lista[ $ip ] = array(
		'desde'  => time(),
		'hasta'  => time() + $dias * DAY_IN_SECONDS,
		'motivo' => substr( $motivo, 0, 200 ),
	);
	update_option( VIGIA_OPCION, $lista, true );
	return vigia_respuesta_lista();
}

function vigia_desbloquear( WP_REST_Request $req ) {
	$ip    = trim( (string) $req->get_param( 'ip' ) );
	$lista = vigia_bloqueos_lista();
	unset( $lista[ $ip ] );
	update_option( VIGIA_OPCION, vigia_limpiar( $lista ), true );
	return vigia_respuesta_lista();
}

function vigia_log( WP_REST_Request $req ) {
	$fichero = (string) $req->get_param( 'fichero' );
	if ( ! isset( VIGIA_LOGS[ $fichero ] ) ) {
		return new WP_Error( 'vigia_log', 'Ese fichero no está permitido.', array( 'status' => 400 ) );
	}
	$ruta = vigia_carpeta( VIGIA_LOGS[ $fichero ] ) . '/' . $fichero;
	clearstatcache();
	if ( ! @file_exists( $ruta ) ) {
		if ( ! @is_readable( dirname( $ruta ) ) ) {
			return new WP_Error( 'vigia_log_ilegible', 'El PHP de la web no puede leer la carpeta '
				. VIGIA_LOGS[ $fichero ] . ( ini_get( 'open_basedir' ) ? ' (open_basedir)' : '' ) . '.', array( 'status' => 503 ) );
		}
		return array( 'fichero' => $fichero, 'existe' => false, 'tamano' => 0, 'fecha' => 0, 'desde' => 0, 'datos' => '' );
	}
	if ( ! @is_readable( $ruta ) ) {
		return new WP_Error( 'vigia_log_ilegible', 'El PHP de la web no puede leer ' . $fichero . '.', array( 'status' => 503 ) );
	}
	$tamano = (int) filesize( $ruta );
	$desde  = max( 0, (int) $req->get_param( 'desde' ) );
	$max    = $req->get_param( 'max' );
	$max    = null === $max ? VIGIA_LOG_TROZO : max( 0, min( VIGIA_LOG_TROZO, (int) $max ) );
	$datos  = '';
	if ( $max > 0 && $desde < $tamano ) {
		$h = @fopen( $ruta, 'rb' );
		if ( $h ) {
			fseek( $h, $desde );
			$datos = (string) fread( $h, $max );
			fclose( $h );
		}
	}
	return array(
		'fichero' => $fichero,
		'existe'  => true,
		'tamano'  => $tamano,
		'fecha'   => (int) filemtime( $ruta ),
		'desde'   => $desde,
		'datos'   => base64_encode( $datos ),
	);
}

add_action( 'rest_api_init', function () {
	register_rest_route( 'vigia/v1', '/bloqueos', array(
		array( 'methods' => 'GET', 'callback' => 'vigia_respuesta_lista', 'permission_callback' => 'vigia_permiso' ),
		array( 'methods' => 'POST', 'callback' => 'vigia_bloquear', 'permission_callback' => 'vigia_permiso' ),
	) );
	register_rest_route( 'vigia/v1', '/bloqueos/(?P<ip>[0-9a-fA-F:.]+)', array(
		array( 'methods' => 'DELETE', 'callback' => 'vigia_desbloquear', 'permission_callback' => 'vigia_permiso' ),
	) );
	register_rest_route( 'vigia/v1', '/log', array(
		array( 'methods' => 'GET', 'callback' => 'vigia_log', 'permission_callback' => 'vigia_permiso' ),
	) );
} );
