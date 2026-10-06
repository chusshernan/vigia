"""Catálogo de incidencias: qué significa cada una y qué hacer.

modo «estado»: algo que está mal mientras dura (la web caída). Se cierra sola cuando la
               comprobación vuelve a salir bien, y entonces se avisa de que se arregló.
modo «evento»: algo que pasó (un ataque). Se cierra sola tras `cerrar_horas` sin repetirse.
confirmar:     cuántas pasadas seguidas tiene que verse antes de avisar (evita falsas alarmas).

Los textos admiten {variables} que rellena cada comprobación.
"""

BLOQUEAR_IP = (
    "Bloquéala: botón «🚫 Bloquear» del aviso en Telegram, o escribe /bloquear {ip} "
    "(7 días; se deshace con /desbloquear {ip})."
)

CATALOGO = {
    # --- WEB -------------------------------------------------------------------
    "web_caida": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "La web no responde o devuelve un error ({detalle}).",
        "solucion": [
            "Ábrela tú desde el móvil con datos (no wifi) para descartar que sea cosa de este equipo.",
            "Si da error 500: suele ser un plugin o un cambio reciente. Revisa la incidencia de «errores de PHP» si la hay.",
            "Si da 403/«IP bloqueada»: el plugin de seguridad de la cuenta ha bloqueado a alguien; se suelta en unos minutos.",
            "Si no carga nada en ningún sitio: mira la página de estado del hosting o abre una incidencia con ellos.",
        ],
    },
    "web_lenta": {
        "modo": "estado", "confirmar": 3,
        "que_pasa": "La portada tarda {segundos} s en cargar (el límite es {limite} s).",
        "solucion": [
            "Si coincide con un ataque o un rastreador (mira la sección Ataques), bloquear esa IP suele bastar.",
            "Si es constante: revisar el plugin de caché y vaciarla desde el escritorio de WordPress.",
            "Si es de todo el servidor (otras webs del mismo hosting también van lentas), es el hosting: esperar o abrir incidencia.",
        ],
    },
    "web_contenido": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "La portada carga pero no tiene el aspecto esperado: {detalle}.",
        "solucion": [
            "Ábrela en el navegador y mira si se ve bien.",
            "Si aparece texto extraño («hacked by…», anuncios, idiomas raros): la web puede estar comprometida. "
            "NO toques nada aún, haz captura y pon en modo mantenimiento; restaura desde la copia de seguridad.",
            "Si sale en blanco: error de PHP (mira la sección Errores) o caché rota.",
        ],
    },
    "web_scripts_nuevos": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "La portada carga scripts de un dominio que no estaba antes: {dominios}.",
        "solucion": [
            "Si has instalado un plugin, un widget o un código de seguimiento nuevo, es eso: márcalo como conocido con "
            "`python vigia.py --aceptar-web`.",
            "Si no reconoces el dominio, puede ser código inyectado (un clásico de las webs hackeadas para meter "
            "publicidad o robar datos). Búscalo en Google y revisa los plugins actualizados recientemente.",
        ],
    },
    "web_certificado": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "El certificado HTTPS caduca en {dias} días ({fecha}).",
        "solucion": [
            "En el panel del hosting (Plesk, cPanel…): comprobar que el certificado de Let's Encrypt está en renovación automática.",
            "Si no se renueva solo, renuévalo a mano o pide al hosting que lo revise.",
        ],
    },
    "web_dns": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "El dominio apunta a {ip} y debería apuntar a {esperada}.",
        "solucion": [
            "Si has cambiado de hosting o de DNS a propósito, actualiza `ip_esperada` en vigia.json.",
            "Si no: puede ser un secuestro del dominio. Entra en el registrador del dominio, cambia la contraseña, "
            "activa la verificación en dos pasos y revisa los registros DNS.",
        ],
    },
    "web_api": {
        "modo": "estado", "confirmar": 3,
        "que_pasa": "La API de WordPress (/wp-json/) no responde ({detalle}). Lo que publique por la API (automatizaciones, apps) dejará de funcionar.",
        "solucion": [
            "Si la web sí carga, suele ser un plugin de seguridad que ha cortado la API.",
            "Revisa en WordPress → Ajustes → Enlaces permanentes y pulsa «Guardar» (regenera las reglas).",
        ],
    },

    # --- STREAM ----------------------------------------------------------------
    "stream_caido": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "El directo no se puede escuchar ({detalle}). La radio está fuera del aire.",
        "solucion": [
            "En el ordenador de la radio: comprueba que el programa de emisión está sonando.",
            "Comprueba el programa codificador (el que manda el audio al servidor de streaming): debe estar «conectado». Si no, dale a conectar.",
            "Si ambos van bien: entra en el panel del servidor de streaming (AzuraCast, Icecast…) y mira si la estación está arrancada.",
            "Si el panel no carga: el problema es del proveedor, abre un ticket con ellos.",
        ],
    },
    "stream_lento": {
        "modo": "estado", "confirmar": 3,
        "que_pasa": "El directo llega a {kbps} kbps y debería ir a {esperado}. Los oyentes oirán cortes.",
        "solucion": [
            "Comprueba la conexión a internet del ordenador de la radio (que nadie esté descargando cosas).",
            "Reinicia el codificador.",
            "Si sigue: puede ser del servidor de streaming; mira su estado o abre ticket.",
        ],
    },
    "stream_silencio": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "El directo emite pero se oye silencio ({segundos} s seguidos sin sonido).",
        "solucion": [
            "En el programa de emisión: mira si la lista se ha acabado, está en pausa o atascada en un tema.",
            "Revisa que la salida de audio del programa de emisión es la que recoge el codificador (a veces Windows cambia el dispositivo).",
            "Mira el volumen/silencio del mezclador de Windows.",
        ],
    },

    # --- ATAQUES (logs) --------------------------------------------------------
    "ataque_fuerza_bruta": {
        "modo": "evento", "cerrar_horas": 6,
        "que_pasa": "La IP {ip} ({pais}) ha intentado entrar en WordPress {n} veces en la última hora.",
        "solucion": [
            "Asegúrate de que todos los usuarios de WordPress tienen contraseñas largas y únicas.",
            BLOQUEAR_IP,
            "A medio plazo: instala un plugin que limite intentos (Limit Login Attempts Reloaded o Wordfence) y "
            "activa la verificación en dos pasos.",
        ],
    },
    "ataque_fuerza_bruta_distribuida": {
        "modo": "evento", "cerrar_horas": 6,
        "que_pasa": "{n} intentos de entrar en WordPress en la última hora desde {ips} IPs distintas (ataque repartido).",
        "solucion": [
            "Bloquear IPs una a una no sirve aquí. Lo eficaz es proteger el login:",
            "Instala Limit Login Attempts Reloaded o Wordfence y activa la verificación en dos pasos.",
            "Si no usáis xmlrpc.php (las apps móviles de WordPress y Jetpack lo usan), bloquéalo en .htaccess:\n"
            "<Files xmlrpc.php>\n  Require all denied\n</Files>",
        ],
    },
    "login_correcto": {
        "modo": "evento", "cerrar_horas": 24,
        "que_pasa": "Alguien ha iniciado sesión en WordPress desde la IP {ip} ({pais}).",
        "solucion": [
            "Si fuiste tú o alguien del equipo: no hay que hacer nada. Puedes añadir tu IP a `ips_de_confianza` en vigia.json.",
            "Si NO lo reconoces: cambia ya la contraseña de todos los administradores, revisa en WordPress → Usuarios "
            "que no haya usuarios nuevos y cierra todas las sesiones (Perfil → «Cerrar sesión en todas partes»).",
        ],
    },
    "ataque_escaneo": {
        "modo": "evento", "cerrar_horas": 6,
        "que_pasa": "La IP {ip} ({pais}) está buscando ficheros sensibles ({n} intentos): {ejemplos}.",
        "solucion": [
            "Es un escáner automático buscando fallos. Si todas las respuestas son 404 (no existe), no ha encontrado nada.",
            BLOQUEAR_IP,
        ],
    },
    "ataque_expuesto": {
        "modo": "evento", "cerrar_horas": 24,
        "que_pasa": "Se ha descargado con éxito un fichero sensible: {ruta} (IP {ip}). Puede haber datos expuestos.",
        "solucion": [
            "Comprueba qué contiene ese fichero (por FTP).",
            "Si tiene contraseñas o datos: bórralo o muévelo fuera de la web y CAMBIA las contraseñas que aparezcan.",
            "Las copias de seguridad (.zip, .sql) nunca deben estar dentro de la carpeta pública.",
        ],
    },
    "ataque_inyeccion": {
        "modo": "evento", "cerrar_horas": 6,
        "que_pasa": "La IP {ip} ({pais}) ha enviado {n} peticiones con intentos de inyección (SQL, rutas, código): {ejemplos}.",
        "solucion": [
            "WordPress actualizado aguanta la mayoría. Lo importante es tener al día WordPress, el tema y los plugins.",
            BLOQUEAR_IP,
        ],
    },
    "ataque_webshell": {
        "modo": "evento", "cerrar_horas": 48,
        "que_pasa": "Se ha EJECUTADO un fichero PHP dentro de uploads: {ruta} (IP {ip}). Es la señal típica de una web hackeada.",
        "solucion": [
            "URGENTE. Entra por FTP en esa ruta, descarga el fichero (como prueba) y bórralo.",
            "Busca más .php en wp-content/uploads: ahí NUNCA debe haber ninguno.",
            "Cambia contraseñas de FTP, WordPress y base de datos.",
            "Para que no vuelva a pasar, crea wp-content/uploads/.htaccess con:\n<FilesMatch \"\\.ph(p|ar|tml)\">\n  Require all denied\n</FilesMatch>",
            "Si hay dudas, restaura la copia de seguridad limpia más reciente.",
        ],
    },
    "ataque_enumeracion": {
        "modo": "evento", "cerrar_horas": 12,
        "que_pasa": "La IP {ip} ha intentado sacar la lista de usuarios de WordPress ({n} veces). Suele ser el paso previo a un ataque al login.",
        "solucion": [
            "No es grave por sí solo, pero facilita adivinar contraseñas: los nombres de usuario no deberían ser públicos.",
            "Los plugins de seguridad (Wordfence…) tienen una opción para ocultar los usuarios.",
        ],
    },
    "rastreador_masivo": {
        "modo": "evento", "cerrar_horas": 6,
        "que_pasa": "La IP {ip} ({pais}) ha hecho {n} peticiones en la última hora. Se identifica como «{agente}».",
        "solucion": [
            "Si es un buscador conocido (Google, Bing) es normal.",
            "Si se hace pasar por un navegador normal, es un robot que copia la web (o un ataque de saturación si la web va lenta).",
            BLOQUEAR_IP,
        ],
    },

    # --- FALLOS (logs) ---------------------------------------------------------
    "errores_5xx": {
        "modo": "evento", "cerrar_horas": 2,
        "que_pasa": "La web ha dado {n} errores de servidor (5xx) en la última hora. Ejemplos: {ejemplos}.",
        "solucion": [
            "Mira la incidencia de errores de PHP si la hay: dice qué fichero falla.",
            "Si empezó tras actualizar un plugin, desactívalo (renombrando su carpeta por FTP si no entras al escritorio).",
        ],
    },
    "errores_php": {
        "modo": "evento", "cerrar_horas": 6,
        "que_pasa": "Error grave de PHP: {mensaje}",
        "solucion": [
            "El mensaje indica el fichero y la línea. Si es de un plugin, actualízalo o desactívalo.",
            "Si es de un mu-plugin propio, revisa el fichero y la línea que da el mensaje.",
        ],
    },
    "errores_php_muchos": {
        "modo": "evento", "cerrar_horas": 3,
        "que_pasa": "El servidor ha registrado {n} errores en la última hora (lo normal es bastante menos). Más frecuente: {mensaje}",
        "solucion": [
            "Los «Broken pipe» de fcgid indican que PHP corta conexiones: suele pasar con picos de visitas o robots.",
            "Si va a más y la web se pone lenta, mira si hay un rastreador masivo y bloquéalo.",
        ],
    },
    "enlaces_rotos": {
        "modo": "evento", "cerrar_horas": 24,
        "que_pasa": "{n} enlaces de la propia web llevan a páginas o imágenes que no existen. Ejemplos: {ejemplos}.",
        "solucion": [
            "No es un ataque. Son imágenes borradas o URLs cambiadas.",
            "En «Detalle técnico» está cada ruta rota y la página que la enlaza («viene de»): corrige o quita el enlace en esa entrada.",
        ],
    },

    # --- FICHEROS (FTP) --------------------------------------------------------
    "fichero_php_en_uploads": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "Hay un fichero PHP en una carpeta de subidas: {ruta}. Ahí nunca debe haber código.",
        "solucion": [
            "URGENTE. Descárgalo por FTP (para revisarlo) y bórralo del servidor.",
            "Cambia las contraseñas de FTP y de WordPress.",
            "Protege la carpeta: crea wp-content/uploads/.htaccess con:\n<FilesMatch \"\\.ph(p|ar|tml)\">\n  Require all denied\n</FilesMatch>",
        ],
    },
    "fichero_nuevo": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "Ha aparecido un fichero nuevo en {carpeta}: {ficheros}.",
        "solucion": [
            "Si lo has subido tú (un plugin, una actualización), márcalo como conocido: "
            "`python vigia.py --aceptar-ficheros`.",
            "Si no sabes de dónde sale, NO lo abras en el navegador. Descárgalo por FTP y revísalo con calma (o pásaselo a quien lleve la web).",
        ],
    },
    "fichero_cambiado": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "Ha cambiado un fichero importante: {ficheros}.",
        "solucion": [
            "Si has actualizado WordPress o tocado la configuración, es normal: `python vigia.py --aceptar-ficheros`.",
            "Si no: compara con la copia de seguridad. Un .htaccess o index.php modificado por sorpresa es señal de intrusión.",
        ],
    },
    "fichero_borrado": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "Ha desaparecido un fichero de {carpeta}: {ficheros}.",
        "solucion": [
            "Si lo borraste tú, `python vigia.py --aceptar-ficheros`.",
            "Si no, restáuralo desde la copia de seguridad.",
        ],
    },

    # --- EQUIPO DE LA RADIO ----------------------------------------------------
    "equipo_proceso_parado": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "{nombre} no está abierto en el ordenador de la radio.",
        "solucion": [
            "Ábrelo y comprueba que vuelve a sonar.",
            "Si se cierra solo a menudo, mira el Visor de eventos de Windows (Registros de Windows → Aplicación) a la hora del cierre.",
        ],
    },
    "equipo_proceso_colgado": {
        "modo": "estado", "confirmar": 3,
        "que_pasa": "{nombre} está abierto pero Windows dice que «no responde».",
        "solucion": [
            "Espera un minuto. Si sigue, ciérralo desde el Administrador de tareas y ábrelo de nuevo.",
            "Si se cuelga a menudo: puede ser falta de memoria o un fichero de audio dañado en la lista.",
        ],
    },
    "equipo_disco": {
        "modo": "estado", "confirmar": 1,
        "que_pasa": "Al disco {unidad} del ordenador de la radio le quedan {libre} GB libres.",
        "solucion": [
            "Vacía la papelera y borra descargas o grabaciones antiguas.",
            "Sin espacio, el programa de emisión y Windows pueden fallar sin avisar.",
        ],
    },
    "equipo_sin_internet": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "El ordenador de la radio se ha quedado sin internet. Si el codificador está en ese equipo, la radio estará fuera del aire.",
        "solucion": [
            "Revisa el router y el cable de red.",
            "Este aviso puede llegar con retraso: se manda en cuanto vuelve la conexión.",
        ],
    },
    "centinela_caido": {
        "modo": "estado", "confirmar": 3,
        "que_pasa": "El centro de operaciones no consigue hablar con el ordenador de la radio ({detalle}). "
                    "Si está apagado o colgado, nadie vigila la web ni el directo a esta hora.",
        "solucion": [
            "Mira si el ordenador de la radio está encendido y con sesión iniciada.",
            "Si lo está: abre el Programador de tareas de Windows y comprueba que la tarea «Vigia» está «En ejecución». Si no, botón derecho → Ejecutar.",
            "Si dice «token incorrecto»: el token de vigia.json tiene que ser el mismo en los dos ordenadores.",
            "Si el ordenador cambió de sitio o de red, actualiza la dirección en vigia.json → centinela → url.",
        ],
    },
    "centinela_parado": {
        "modo": "estado", "confirmar": 2,
        "que_pasa": "El centinela de la radio contesta, pero lleva {minutos} min sin hacer comprobaciones.",
        "solucion": [
            "Reinicia la tarea «Vigia» en el Programador de tareas de Windows (Finalizar y Ejecutar).",
            "Si se repite, revisa el fichero C:\\Vigia\\datos\\vigia.log.",
        ],
    },
    "vigia_fallo": {
        "modo": "estado", "confirmar": 3,
        "que_pasa": "El Vigía no puede hacer una de sus comprobaciones ({modulo}): {detalle}.",
        "solucion": [
            "Si es de FTP: puede que hayan cambiado la contraseña. Actualízala en el fichero de credenciales.",
            "Si persiste, revisa el fichero datos/vigia.log.",
        ],
    },
}


class _Seguro(dict):
    def __missing__(self, k):
        return "?"


def rellenar(texto, datos):
    return texto.format_map(_Seguro(datos))


def explicar(tipo, datos):
    """Devuelve (qué pasa, [pasos]) con las variables rellenadas."""
    e = CATALOGO.get(tipo)
    if not e:
        return datos.get("detalle", tipo), []
    return rellenar(e["que_pasa"], datos), [rellenar(p, datos) for p in e["solucion"]]
