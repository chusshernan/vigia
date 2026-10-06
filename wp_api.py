"""La API del mu-plugin del Vigía en la web (wp/aa-vigia-bloqueos.php).

Con el usuario «vigia» de WordPress (rol Suscriptor): solo sirve para estas rutas.
"""
import base64
import json
from urllib.parse import urlencode

from comun import pedir


def llamar(cfg, cred, metodo, ruta, datos=None, consulta=None, timeout=30):
    """Devuelve (respuesta JSON, "") o (None, motivo del fallo)."""
    u, clave = cred.get("VIGIA_WP_USER", ""), cred.get("VIGIA_WP_APP_PASSWORD", "")
    if not u or not clave:
        return None, "faltan VIGIA_WP_USER / VIGIA_WP_APP_PASSWORD en las credenciales"
    auth = base64.b64encode(f"{u}:{clave}".encode()).decode()
    cab = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    if metodo == "DELETE":  # el servidor corta DELETE: se manda como POST
        cab["X-HTTP-Method-Override"] = "DELETE"
    url = cfg["web"]["url"].rstrip("/") + "/wp-json/vigia/v1" + ruta
    if consulta:
        url += "?" + urlencode(consulta)
    cuerpo = json.dumps(datos).encode() if datos is not None else (b"" if metodo != "GET" else None)
    r = pedir(url, timeout=timeout, max_bytes=8_000_000, datos=cuerpo, cabeceras=cab)
    if not r.ok:
        if r.estado == 404:
            return None, "el mu-plugin del Vigía no está instalado en la web"
        if r.estado in (401, 403):
            return None, f"WordPress rechaza al usuario del Vigía (HTTP {r.estado})"
        if r.estado == 503:
            return None, "el PHP de la web no puede leer los logs"
        if r.estado in (400, 409):
            return None, "WordPress no lo acepta"
        return None, r.error
    return json.loads(r.cuerpo), ""


class LogsPorWeb:
    """Lo mismo que necesita mod_logs de la conexión FTPS (tamano, listar, leer), pero
    pidiendo al mu-plugin los registros de su sensor. Así el centinela no necesita FTP."""

    def __init__(self, cfg, cred, rotados):
        self.cfg, self.cred, self.rotados = cfg, cred, rotados

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _pedir(self, fichero, desde=0, maximo=None):
        consulta = {"fichero": fichero, "desde": desde}
        if maximo is not None:
            consulta["max"] = maximo
        d, error = llamar(self.cfg, self.cred, "GET", "/log", consulta=consulta, timeout=60)
        if error:
            raise RuntimeError(error)
        return d

    def tamano(self, ruta):
        return self._pedir(ruta.rsplit("/", 1)[-1], maximo=0)["tamano"]

    def listar(self, carpeta):
        """Solo interesa la «firma» de los rotados (tamaño y fecha) para notar la rotación."""
        salida = {}
        for nombre in self.rotados:
            d = self._pedir(nombre, maximo=0)
            if d["existe"]:
                salida[nombre] = {"tipo": "fichero", "tamano": d["tamano"], "fecha": d["fecha"]}
        return salida

    def leer(self, ruta, desde=0):
        nombre = ruta.rsplit("/", 1)[-1]
        partes = []
        while True:
            d = self._pedir(nombre, desde=desde)
            trozo = base64.b64decode(d["datos"])
            partes.append(trozo)
            desde += len(trozo)
            if not trozo or desde >= d["tamano"]:
                return b"".join(partes)
