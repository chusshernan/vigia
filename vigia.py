#!/usr/bin/env python3
"""Vigía: vigila la web, el directo, los ataques y el equipo de la radio.

Dos papeles (vigia.json → "roles"):
  central    este ordenador, el centro de operaciones: ataques, ficheros, panel e historial
             completos (recoge también lo del centinela).
  centinela  el ordenador de la radio: lo justo para avisar al móvil a cualquier hora
             (web, directo, silencio, ZaraRadio) y entregar sus datos al centro.

Uso:
  python vigia.py [--rol central|centinela]   vigilar sin parar
  python vigia.py --una-vez          una sola pasada de todo y salir
  python vigia.py --una-vez web logs solo esos módulos
  python vigia.py --probar-avisos    manda un mensaje de prueba por cada canal
  python vigia.py --aceptar-ficheros los cambios actuales en el servidor son legítimos
  python vigia.py --aceptar-web      los scripts de terceros actuales de la portada son legítimos
  python vigia.py --panel            solo regenerar el panel

Módulos: web, stream, silencio, logs, ficheros, equipo, centinela.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mod_centinela  # noqa: E402
import mod_equipo  # noqa: E402
import mod_ficheros  # noqa: E402
import mod_logs  # noqa: E402
import mod_stream  # noqa: E402
import mod_web  # noqa: E402
import panel  # noqa: E402
from avisos import Avisador  # noqa: E402
from comun import Hallazgo, Resultado, cargar_config, cargar_credenciales, hay_internet, log, preparar_log  # noqa: E402
from historial import Historial  # noqa: E402

MODULOS = {
    "web": lambda cfg, est, cred, h: mod_web.comprobar(cfg, est),
    "stream": lambda cfg, est, cred, h: mod_stream.comprobar(cfg, est),
    "silencio": lambda cfg, est, cred, h: mod_stream.comprobar_silencio(cfg, est),
    "logs": lambda cfg, est, cred, h: mod_logs.comprobar(cfg, est, cred),
    "ficheros": lambda cfg, est, cred, h: mod_ficheros.comprobar(cfg, est, cred),
    "equipo": lambda cfg, est, cred, h: mod_equipo.comprobar(cfg, est),
    "centinela": mod_centinela.comprobar,
}
NECESITAN_INTERNET = {"web", "stream", "silencio", "logs", "ficheros"}
PUERTO_CERROJO = 47823


class Vigia:
    def __init__(self, cfg, rol, forzar_envio=False):
        self.cfg = cfg
        self.rol = rol
        self.intervalos = cfg["roles"][rol]["intervalos_minutos"]
        self.cred = cargar_credenciales(cfg)
        self.ruta_estado = cfg["_datos"] / "estado.json"
        self.estado = self._leer_estado()
        self.ruta_db = cfg["_datos"] / "vigia.db"
        self.h = Historial(self.ruta_db)
        # Las órdenes (Telegram, centro) llegan por otros hilos: el cerrojo evita que
        # toquen el estado a la vez que una comprobación.
        self.cerrojo = threading.RLock()
        self.avisador = Avisador(cfg, self.cred, self.h, forzar_envio,
                                 botones=(rol == "centinela"), estado=self.estado)

    def _leer_estado(self):
        try:
            return json.loads(self.ruta_estado.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def guardar_estado(self):
        with self.cerrojo:
            tmp = self.ruta_estado.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.estado, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.ruta_estado)

    def h_registrar_orden(self, quien, orden, respuesta):
        h = Historial(self.ruta_db)  # conexión propia: llega desde otro hilo
        try:
            h.guardar_orden(quien, orden, respuesta)
        finally:
            h.db.close()

    def pasada(self, modulos):
        con_red = None
        for nombre in modulos:
            if nombre in NECESITAN_INTERNET:
                if con_red is None:
                    con_red = hay_internet()
                    self._conexion(con_red)
                if not con_red:
                    continue
            try:
                with self.cerrojo:
                    res = MODULOS[nombre](self.cfg, self.estado, self.cred, self.h)
            except Exception as e:  # un módulo roto no tumba al resto
                log.exception("Fallo en el módulo %s", nombre)
                res = Resultado(nombre, revisado=False, resumen=f"fallo: {type(e).__name__}")
                res.hallazgos.append(Hallazgo(f"vigia:{nombre}", "vigia_fallo", "media",
                                              f"El Vigía falla al revisar «{nombre}»", f"{type(e).__name__}: {e}",
                                              {"modulo": nombre}))
            log.info("%-9s %s", nombre, res.resumen)
            self.h.guardar_pasada(res)
            self.h.guardar_metricas(res.metricas)
            self.h.procesar(res, self.avisador)
        self.reintentar_avisos()
        self.guardar_estado()
        panel.generar(self.cfg, self.h, self.cfg["_datos"] / "panel.html")

    def _conexion(self, ok):
        res = Resultado("conexion", resumen="con internet" if ok else "SIN INTERNET")
        if not ok:
            res.hallazgos.append(Hallazgo("conexion:caida", "equipo_sin_internet", "critica",
                                          "Este equipo no tiene internet"))
        self.h.guardar_pasada(res)
        self.h.procesar(res, self.avisador)

    def reintentar_avisos(self):
        """Lo que no se pudo mandar (sin internet, canal caído) se reintenta en cada pasada."""
        for inc in self.h.abiertas(solo_locales=True):
            if not inc["avisada_en"]:
                self.h._quizas_avisar(inc["id"], self.avisador)
        self.h.commit()

    def bucle(self):
        intervalos = self.intervalos
        ultima = {}
        ultima_purga = 0
        if self.rol == "centinela":
            import servidor_centinela
            from ordenes import Ordenes
            from telegram_bot import BotTelegram
            ordenes = Ordenes(self)
            servidor_centinela.arrancar(self.cfg, ordenes)
            BotTelegram(self, ordenes).arrancar()
        log.info("Vigía en marcha como %s. Avisos %s.", self.rol.upper(),
                 "ACTIVOS" if self.avisador.activos else "simulados")
        while True:
            ahora = time.monotonic()
            tocan = [m for m in MODULOS if intervalos.get(m)
                     and ahora - ultima.get(m, -1e9) >= intervalos[m] * 60]
            if tocan:
                self.pasada(tocan)
                for m in tocan:
                    ultima[m] = time.monotonic()
            if ahora - ultima_purga > 86400:
                self.h.purgar()
                ultima_purga = ahora
            time.sleep(15)


def dar_orden(v, orden):
    """Desde el centro, las órdenes van al centinela (que es quien vigila y está siempre
    encendido). Si no contesta, se ejecuta aquí lo que se pueda."""
    from ordenes import Ordenes
    orden = "/" + orden.lstrip("/")
    c = v.cfg["centinela"]
    if v.rol == "central" and c.get("url"):
        from comun import pedir
        r = pedir(c["url"].rstrip("/") + "/orden", timeout=90, datos=json.dumps({"orden": orden}).encode(),
                  cabeceras={"X-Token": c["token"], "Content-Type": "application/json"})
        if r.ok:
            return json.loads(r.cuerpo)["respuesta"]
        print(f"(El centinela no contesta: {r.error or r.estado}. Lo hago desde aquí.)")
    return Ordenes(v).ejecutar(orden, quien=v.rol)


def cerrojo(rol):
    """Que no corran dos Vigías con el mismo papel a la vez en el mismo equipo."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PUERTO_CERROJO + (rol == "centinela")))
    except OSError:
        log.error("Ya hay un Vigía funcionando como %s en este equipo.", rol)
        sys.exit(1)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--una-vez", nargs="*", metavar="MODULO")
    ap.add_argument("--probar-avisos", action="store_true")
    ap.add_argument("--orden", metavar="ORDEN", help='p. ej. "bloquear 1.2.3.4", "estado", "ayuda"')
    ap.add_argument("--aceptar-ficheros", action="store_true")
    ap.add_argument("--aceptar-web", action="store_true")
    ap.add_argument("--panel", action="store_true")
    ap.add_argument("--avisar", action="store_true", help="mandar avisos de verdad aunque activos=false")
    ap.add_argument("--rol", choices=["central", "centinela"])
    ap.add_argument("--config")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    cfg = cargar_config(a.config)
    preparar_log(cfg, a.verbose)
    rol = a.rol or cfg.get("rol_por_defecto", "central")
    v = Vigia(cfg, rol, forzar_envio=a.avisar or a.probar_avisos)

    if a.probar_avisos:
        for canal in v.avisador.canales:
            texto = (f"✅ {v.avisador.nombre_timbre}: prueba. Si lo lees, este canal funciona." if canal == "whatsapp"
                     else f"✅ Vigía · {cfg['nombre']}\nMensaje de prueba por {canal}. Si lo lees, este canal funciona.")
            ok, error = getattr(v.avisador, f"_por_{canal}")(texto)
            v.h.guardar_aviso(canal, ok, "prueba", error)
            print(f"{canal:10} {'OK' if ok else 'FALLA: ' + error}")
        v.h.commit()
        return
    if a.aceptar_ficheros:
        a.orden = "aceptar_ficheros"
    if a.aceptar_web:
        a.orden = "aceptar_web"
    if a.orden:
        print(dar_orden(v, a.orden))
        return
    if a.panel:
        panel.generar(cfg, v.h, cfg["_datos"] / "panel.html")
        print(cfg["_datos"] / "panel.html")
        return
    if a.una_vez is not None:
        modulos = a.una_vez or [m for m in MODULOS if v.intervalos.get(m)]
        desconocidos = set(modulos) - set(MODULOS)
        if desconocidos:
            ap.error(f"módulos desconocidos: {', '.join(desconocidos)}")
        _c = cerrojo(rol)
        v.pasada(modulos)
        print(f"\nPanel: {cfg['_datos'] / 'panel.html'}")
        return
    _c = cerrojo(rol)
    try:
        v.bucle()
    except KeyboardInterrupt:
        log.info("Vigía parado.")


if __name__ == "__main__":
    main()
