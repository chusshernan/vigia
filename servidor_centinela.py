"""El centinela (ordenador de la radio) habla con el centro de operaciones.

Servidor HTTP mínimo, siempre con la cabecera X-Token:
  GET  /estado?desde=<fecha ISO>   lo que ha visto desde esa fecha
  POST /orden {"orden": "..."}     una orden de la lista cerrada de ordenes.py
"""
import hmac
import json
import socket
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from comun import log
from historial import Historial, ahora, iso


def arrancar(cfg, ordenes=None):
    puerto = cfg["roles"]["centinela"].get("puerto", 8790)
    token = cfg["centinela"].get("token", "")
    if len(token) < 16:
        # Con el token vacío, cualquiera de la red podría leer el historial y dar órdenes.
        log.error("Falta el token del centinela en vigia.json (mínimo 16 caracteres): no abro el puerto.")
        return None
    ruta_db = cfg["_datos"] / "vigia.db"

    class Manejador(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlsplit(self.path)
            if u.path != "/estado":
                return self._responder(404, {"error": "no existe"})
            if not hmac.compare_digest(self.headers.get("X-Token", ""), token):
                return self._responder(403, {"error": "token incorrecto"})
            desde = parse_qs(u.query).get("desde", [iso(ahora() - timedelta(hours=24))])[0]
            h = Historial(ruta_db)  # conexión propia: sqlite no se comparte entre hilos
            try:
                paquete = h.exportar(desde)
            finally:
                h.db.close()
            paquete.update(equipo=socket.gethostname(), ahora=iso(ahora()))
            self._responder(200, paquete)

        def do_POST(self):
            # Órdenes del centro de operaciones (las mismas que desde Telegram).
            if urlsplit(self.path).path != "/orden" or ordenes is None:
                return self._responder(404, {"error": "no existe"})
            if not hmac.compare_digest(self.headers.get("X-Token", ""), token):
                return self._responder(403, {"error": "token incorrecto"})
            try:
                largo = min(int(self.headers.get("Content-Length", 0)), 10000)
                orden = json.loads(self.rfile.read(largo)).get("orden", "")
            except (ValueError, AttributeError):
                return self._responder(400, {"error": "petición mal formada"})
            self._responder(200, {"respuesta": ordenes.ejecutar(orden, quien="centro")})

        def _responder(self, codigo, datos):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", puerto), Manejador)
    except OSError as e:
        # Lo primero son los avisos al móvil: sin servidor, el centinela sigue vigilando igual.
        log.error("No se puede abrir el puerto %s (%s). El centro no recibirá datos, pero los avisos siguen.",
                  puerto, e)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="centinela-http").start()
    log.info("Centinela escuchando en el puerto %s para el centro de operaciones", puerto)
    return srv
