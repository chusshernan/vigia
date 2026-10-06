"""Piezas compartidas del Vigía: ajustes, credenciales, HTTP educado y el «hallazgo»."""
import json
import logging
import os
import platform
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ES_WINDOWS = platform.system() == "Windows"
AGENTE = "Vigia/1.0 (monitor propio)"

log = logging.getLogger("vigia")


def cargar_config(ruta=None):
    ruta = Path(ruta) if ruta else AQUI / "vigia.json"
    with open(ruta, encoding="utf-8") as f:
        cfg = json.load(f)
    datos = Path(os.path.expandvars(os.path.expanduser(cfg["datos"])))
    if not datos.is_absolute():
        datos = AQUI / datos
    datos.mkdir(parents=True, exist_ok=True)
    cfg["_datos"] = datos
    return cfg


def preparar_log(cfg, verbose=False):
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fichero = RotatingFileHandler(cfg["_datos"] / "vigia.log", maxBytes=2_000_000,
                                  backupCount=3, encoding="utf-8")
    fichero.setFormatter(fmt)
    fichero.setLevel(logging.INFO)
    log.handlers[:] = [fichero]
    if sys.stderr:  # con pythonw (sin ventana) no hay consola
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        pantalla = logging.StreamHandler()
        pantalla.setFormatter(fmt)
        pantalla.setLevel(logging.DEBUG if verbose else logging.INFO)
        log.handlers.append(pantalla)


def cargar_credenciales(cfg):
    """Lee el .env (CLAVE=valor) sin tocar el entorno. Lo que falte, queda vacío."""
    clave = "windows" if ES_WINDOWS else "linux"
    ruta = Path(os.path.expandvars(os.path.expanduser(cfg["credenciales"][clave])))
    cred = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            cred[k.strip().removeprefix("export ").strip()] = v
    else:
        log.warning("No encuentro el fichero de credenciales: %s", ruta)
    return cred


# --- HTTP ---------------------------------------------------------------------
# Regla del servidor compartido: una petición cada vez y con pausa. El plugin de
# seguridad de la cuenta bloquea la IP si ve ráfagas.
_ultima_peticion = {}
PAUSA_MISMO_HOST = 2.0


def _esperar_turno(host):
    espera = PAUSA_MISMO_HOST - (time.monotonic() - _ultima_peticion.get(host, 0))
    if espera > 0:
        time.sleep(espera)
    _ultima_peticion[host] = time.monotonic()


@dataclass
class Respuesta:
    ok: bool
    estado: int = 0
    cuerpo: bytes = b""
    segundos: float = 0.0
    error: str = ""
    cabeceras: dict = field(default_factory=dict)


def pedir(url, timeout=20, max_bytes=3_000_000, cabeceras=None, datos=None):
    host = urllib.parse.urlsplit(url).hostname or ""
    _esperar_turno(host)
    req = urllib.request.Request(url, data=datos, headers={"User-Agent": AGENTE, **(cabeceras or {})})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            cuerpo = r.read(max_bytes)
            return Respuesta(True, r.status, cuerpo, time.monotonic() - t0,
                             cabeceras={k.lower(): v for k, v in r.headers.items()})
    except urllib.error.HTTPError as e:
        return Respuesta(False, e.code, b"", time.monotonic() - t0, f"HTTP {e.code}")
    except Exception as e:  # timeouts, DNS, TLS…
        return Respuesta(False, 0, b"", time.monotonic() - t0, f"{type(e).__name__}: {e}")


def hay_internet():
    """¿Tiene salida a internet este equipo? Se mira contra sitios que no son nuestros."""
    import socket
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            continue
    return False


# --- Hallazgos ----------------------------------------------------------------
SEVERIDADES = ["info", "baja", "media", "alta", "critica"]


@dataclass
class Hallazgo:
    """Un problema detectado en una pasada.

    clave: identifica la incidencia (misma clave = misma incidencia, no se repite el aviso).
    tipo:  entrada del catálogo de soluciones (soluciones.py).
    datos: valores que se meten en el texto de la solución ({ip}, {fichero}…).
    """
    clave: str
    tipo: str
    severidad: str
    titulo: str
    detalle: str = ""
    datos: dict = field(default_factory=dict)


@dataclass
class Resultado:
    """Lo que devuelve un módulo: hallazgos, qué áreas ha revisado de verdad y métricas."""
    area: str
    hallazgos: list = field(default_factory=list)
    revisado: bool = True          # False si no se pudo comprobar (no se cierran incidencias)
    metricas: dict = field(default_factory=dict)
    resumen: str = ""

