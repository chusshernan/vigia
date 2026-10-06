# Vigía

**Monitorización y detección de intrusiones para una web WordPress y una emisora de radio online**, pensado para un equipo pequeño sin SOC ni presupuesto: un "SOC de bolsillo" en Python que vigila la web, el directo y el equipo de emisión, avisa al móvil en tiempo real y deja responder desde Telegram.

Lo hice para una emisora de radio pequeña, con su web en un hosting compartido, y está en producción. Este repositorio es la versión pública: los dominios, las IP y las credenciales son de ejemplo.

- **Sin dependencias:** solo la biblioteca estándar de Python 3.10+. ffmpeg es opcional.
- **Ligero:** unos 0,3 s de CPU por pasada y 35 MB de RAM, con prioridad baja para no molestar al software de emisión.
- **Cada aviso explica qué pasa y qué hacer**, en lenguaje llano, para que lo entienda alguien que no sea técnico.

## Arquitectura

```
 Ordenador de la radio (Windows)              Centro de operaciones (Linux)
 CENTINELA · siempre encendido                ─────────────────────────────
 ───────────────────────────────              recoge todo cada minuto
 vigila web, directo, silencio,   ──datos──▶  logs completos de Apache (FTPS)
 ataques (sensor), equipo         ◀─órdenes─  integridad de ficheros
 avisa al móvil al momento                    panel e historial completos
 atiende los botones de Telegram              avisa si el centinela se cae
        │              │                                │
    WhatsApp       Telegram ◀── el operador         HTTP con token (red local)
                                                        │
             Web WordPress ── mu-plugin: sensor + bloqueo de IPs + API REST
```

- El **centinela** corre en el equipo que nunca se apaga, así los ataques avisan en tiempo real aunque el centro esté apagado.
- El **centro de operaciones** junta lo que ve el centinela con lo que solo él puede ver (logs completos y ficheros por FTPS), y abre una incidencia si el centinela deja de responder.
- En un hosting compartido el PHP no puede leer los logs de Apache (`open_basedir`) y no hay cuentas FTP de solo lectura. Para eso el **mu-plugin** lleva un **sensor** que escribe cada petición en formato de log de Apache, fuera de la carpeta pública. El centinela lo lee por la API REST con un usuario de permisos mínimos y no necesita ninguna credencial de FTP.

## Qué detecta

| Área | Detección |
|---|---|
| **Ataques (logs)** | Fuerza bruta contra `wp-login.php`/`xmlrpc.php`, por IP y repartida entre muchas IP · escáneres de rutas sensibles (`.env`, `.git`, copias de seguridad, phpMyAdmin…) · **descarga con éxito** de un fichero sensible · inyección SQL, XSS, path traversal y Log4Shell · ejecución de **webshells** en `uploads` · enumeración de usuarios · robots masivos que se hacen pasar por navegadores · inicios de sesión correctos desde IP desconocidas |
| **Integridad** | Ficheros nuevos, cambiados o borrados en la raíz, `mu-plugins` y `uploads`, comparados con una foto de referencia (FIM) · cualquier `.php` en `uploads` es una alerta crítica |
| **Web** | Caída, lentitud, **defacement** (la portada no contiene lo que debe o contiene "hacked by"…) · **scripts nuevos de dominios externos** (inyección tipo Magecart) · caducidad del certificado TLS · **secuestro de DNS** (el dominio apunta a otra IP) · API de WordPress caída |
| **Directo** | El stream entrega audio de verdad y con su calidad · silencio prolongado (ffmpeg `silencedetect`) · oyentes |
| **Equipo** | El software de emisión está abierto y no colgado · espacio en disco · conexión a internet |
| **Fallos** | Errores 5xx en ráfaga · errores fatales de PHP · enlaces rotos internos |

Los logs se leen de forma **incremental** (solo los bytes nuevos) y **se detecta la rotación**. Si el equipo estuvo apagado, repasa por tramos las horas que se perdió, porque el servidor guarda 7 días.

## Avisos y respuesta

- **Motor de incidencias** en SQLite. Una incidencia de *estado* (la web caída) se cierra sola cuando se arregla y avisa de que se resolvió. Una de *evento* (un ataque) se cierra sola al dejar de repetirse.
- **Sin falsas alarmas:** cada tipo de incidencia tiene que verse en varias pasadas seguidas antes de avisar. La gravedad nunca baja mientras la incidencia sigue abierta. Si sigue abierta, recuerda cada 6 h.
- **Freno anti-avalancha:** como mucho 15 avisos por hora; por encima, solo pasan los críticos.
- **Canales:** Telegram (detalles y botones) y WhatsApp vía CallMeBot. Como CallMeBot es un tercero que ve el texto, por WhatsApp solo sale un timbre sin detalles.
- **Respuesta desde Telegram:** `/bloquear <ip>` (o el botón del aviso), `/desbloquear`, `/estado`, `/visto`, `/silenciar 2h`, `/aceptar_ficheros`…
- **Panel HTML** estático con el estado por área, las incidencias abiertas con sus pasos para arreglarlas, gráficas de 24 h, el historial y las órdenes dadas.

## Decisiones de seguridad

El propio Vigía tiene poder (bloquea IPs en producción), así que está diseñado para no convertirse en una puerta de entrada:

- **Órdenes en lista cerrada.** Cada orden es un método concreto y no hay forma de ejecutar nada más. Las IP se validan con `ipaddress`.
- **El bot solo obedece a un chat** (`TELEGRAM_CHAT_ID`). Los mensajes de otros chats se ignoran y quedan en el log. Una orden que llega con más de 10 min de retraso no se ejecuta.
- **Mínimo privilegio en WordPress.** El Vigía usa un usuario de rol *Suscriptor* con contraseña de aplicación. El mu-plugin solo le abre sus rutas (`/wp-json/vigia/v1/…`) y solo los ficheros de log de una lista blanca, sin aceptar rutas.
- **Anti auto-bloqueo.** El plugin nunca bloquea la IP desde la que se da la orden, la del propio servidor ni las privadas. Todo bloqueo caduca (máx. 90 días, 2000 IP) y la API propia nunca se bloquea, para poder deshacer un error.
- **Centro ↔ centinela** con token comparado en tiempo constante (`hmac.compare_digest`). El servidor no arranca si el token falta o es corto, y el puerto se abre en el firewall solo para la red local.
- **TLS verificado siempre,** también en FTPS cuando el certificado está emitido para el nombre del servidor y no para el dominio. No se desactiva la verificación: se valida contra el nombre correcto.
- **Credenciales fuera del código y del repositorio,** en un `.env` legible solo por el usuario (en Windows, con `icacls`).
- **Buen vecino:** como mucho una petición cada 2 s al mismo host, para no disparar el WAF del hosting.
- **El sensor nunca rompe la web:** todo va en `try/catch` y, si no puede escribir, no hace nada.

## Puesta en marcha

1. `cp vigia.ejemplo.json vigia.json` y ajusta la web, el stream, los umbrales y los procesos. Genera el token con `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`.
2. **Credenciales:** copia `windows/credenciales.ejemplo.env` a la ruta de `vigia.json → credenciales` y rellénalo:
   - **Telegram:** crea un bot con @BotFather. El `chat_id` sale de `https://api.telegram.org/bot<TOKEN>/getUpdates`.
   - **WhatsApp (opcional):** sigue las instrucciones de [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/).
3. **mu-plugin:** ajusta `VIGIA_IP_SERVIDOR` y `VIGIA_CARPETA_APACHE` en `wp/aa-vigia-bloqueos.php` y súbelo con `./instalar-linux.sh bloqueos`. El script comprueba la portada y, si no responde bien, quita el plugin solo. Después crea en WordPress el usuario `vigia` (Suscriptor) con una contraseña de aplicación.
4. **Centinela (Windows):** doble clic en `windows\instalar.bat`. Instala Python si falta, crea la tarea programada y la regla de firewall solo para la red local, y manda un mensaje de prueba.
5. **Centro (Linux):** pon la dirección del centinela en `vigia.json → centinela → url` y ejecuta `./instalar-linux.sh central` (servicio de systemd de usuario).
6. Cuando lleguen los mensajes de prueba, pon `"activos": true` en `vigia.json`. Hasta entonces los avisos solo se simulan y se apuntan en el historial.

```sh
python3 vigia.py --rol centinela --una-vez web   # probar un módulo a mano
python3 vigia.py --probar-avisos                 # mensaje de prueba por cada canal
python3 vigia.py --orden "bloquear 203.0.113.5"  # cualquier orden de Telegram
python3 -m unittest discover -s pruebas -v       # pruebas (sin red)
```

## Estructura

| | |
|---|---|
| `vigia.py` | Programa principal: roles, bucle, órdenes por línea de comandos |
| `mod_*.py` | Un módulo por comprobación (`web`, `stream`, `logs`, `ficheros`, `equipo`, `centinela`) |
| `soluciones.py` | Catálogo de incidencias: qué significa cada una y los pasos para arreglarla |
| `historial.py` | Motor de incidencias en SQLite (abrir, confirmar, recordar, cerrar) |
| `avisos.py` · `telegram_bot.py` · `ordenes.py` | Avisos, bot de Telegram y órdenes en lista cerrada |
| `servidor_centinela.py` | API HTTP mínima entre el centinela y el centro |
| `panel.py` | Panel HTML |
| `wp/aa-vigia-bloqueos.php` | mu-plugin: portero de IPs, sensor de peticiones y API REST |
| `windows/` · `instalar-linux.sh` | Instaladores |
| `pruebas/` | Pruebas unitarias de la detección y de las salvaguardas |

## Limitaciones conocidas

- El sensor solo ve lo que pasa por WordPress. Lo que sirve Apache directamente (imágenes o un PHP malicioso ejecutado fuera de WordPress) solo lo ve el centro de operaciones en los logs completos, y por eso no es en tiempo real si el centro está apagado.
- El bloqueo de IPs actúa en WordPress, no en el firewall del servidor (en un hosting compartido no hay acceso a él).
- La detección se basa en firmas y umbrales: un atacante lento y cuidadoso puede quedar por debajo.
- Si se va la luz o internet en el estudio, nadie puede avisar desde dentro. Para eso conviene un monitor externo gratuito (UptimeRobot o similar) sobre la web y el stream.

## Licencia

[MIT](LICENSE) © 2026 Jesús Álvarez Hernández
