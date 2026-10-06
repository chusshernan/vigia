"""Las órdenes que se le pueden dar al Vigía, desde Telegram o desde el centro de operaciones.

Lista cerrada: no hay forma de ejecutar nada que no esté aquí.

  /estado                    incidencias abiertas
  /bloquear <ip> [días]      bloquea la IP en la web (7 días si no se dice)
  /desbloquear <ip>
  /bloqueados                lista de IPs bloqueadas
  /visto <n>                 marcar una incidencia como vista (no más recordatorios)
  /silenciar <2h|30m|no>     sin avisos durante un rato (salvo los críticos)
  /aceptar_ficheros          los cambios actuales en el servidor son legítimos
  /aceptar_web               los scripts actuales de la portada son legítimos
  /ayuda
"""
import ipaddress
import re
import time
from datetime import datetime

import wp_api
from comun import log
from historial import Historial, ahora, iso

TOCAN_ESTADO = {"silenciar", "aceptar_ficheros", "aceptar_web"}
# La lista de órdenes es el tercer párrafo de la cabecera de este fichero.
AYUDA = "Órdenes del Vigía:\n\n" + "\n".join(l.strip() for l in __doc__.split("\n\n")[2].strip().splitlines())


class Ordenes:
    def __init__(self, vigia):
        self.v = vigia  # acceso al estado, la configuración y el cerrojo

    def _wp(self, metodo, ruta="", datos=None):
        return wp_api.llamar(self.v.cfg, self.v.cred, metodo, "/bloqueos" + ruta, datos)

    # --- ejecutar -------------------------------------------------------------
    def ejecutar(self, texto, quien="?"):
        partes = texto.strip().split()
        if not partes:
            return "Orden vacía. /ayuda"
        orden = partes[0].lower().lstrip("/").split("@")[0]
        args = partes[1:]
        log.info("ORDEN de %s: %s", quien, texto.strip())
        metodo = getattr(self, f"o_{orden}", None)
        if not metodo:
            return f"No conozco «{orden}».\n\n{AYUDA}"
        try:
            if orden in TOCAN_ESTADO:  # esperan a que acabe la comprobación en curso
                with self.v.cerrojo:
                    respuesta = metodo(args)
            else:
                respuesta = metodo(args)
            self.v.h_registrar_orden(quien, texto.strip(), respuesta)
            return respuesta
        except Exception as e:
            log.exception("Fallo al ejecutar %s", texto)
            return f"No se pudo: {type(e).__name__}: {e}"

    def o_ayuda(self, args):
        return AYUDA

    o_start = o_ayuda

    def o_estado(self, args):
        h = Historial(self.v.ruta_db)
        try:
            abiertas = [i for i in h.abiertas() if i["severidad"] != "baja"]
        finally:
            h.db.close()
        silencio = self.v.estado.get("silencio_hasta", 0)
        linea_sil = (f"\n🔕 Avisos silenciados hasta las {datetime.fromtimestamp(silencio):%H:%M}"
                     if silencio > time.time() else "")
        if not abiertas:
            return "✅ Todo funciona. No hay incidencias abiertas." + linea_sil
        lineas = [f"{len(abiertas)} incidencias abiertas:"]
        for i in abiertas[:15]:
            visto = " 👁" if i.get("vista") else ""
            lineas.append(f"#{i['id']} [{i['severidad']}] {i['titulo']}{visto}")
        return "\n".join(lineas) + linea_sil

    def o_bloquear(self, args):
        if not args:
            return "Uso: /bloquear 1.2.3.4 [días]"
        ip = _ip_valida(args[0])
        if not ip:
            return f"«{args[0]}» no es una IP válida."
        if ip in self.v.cfg["logs"].get("ips_de_confianza", []):
            return f"{ip} está en las IPs de confianza: no la bloqueo."
        dias = int(args[1]) if len(args) > 1 and args[1].isdigit() else self.v.cfg.get("bloqueo", {}).get("dias", 7)
        datos, error = self._wp("POST", datos={"ip": ip, "dias": dias, "motivo": f"Vigía {iso(ahora())}"})
        if error:
            return f"❌ No se pudo bloquear {ip}: {error}"
        return f"🚫 {ip} bloqueada durante {dias} días. En total hay {len(datos['bloqueos'])} IPs bloqueadas."

    def o_desbloquear(self, args):
        if not args or not _ip_valida(args[0]):
            return "Uso: /desbloquear 1.2.3.4"
        datos, error = self._wp("DELETE", "/" + _ip_valida(args[0]))
        if error:
            return f"❌ No se pudo: {error}"
        return f"✅ {args[0]} desbloqueada. Quedan {len(datos['bloqueos'])} IPs bloqueadas."

    def o_bloqueados(self, args):
        datos, error = self._wp("GET")
        if error:
            return f"❌ {error}"
        b = datos["bloqueos"]
        if not b:
            return "No hay ninguna IP bloqueada."
        lineas = [f"{len(b)} IPs bloqueadas:"]
        for ip, x in sorted(b.items(), key=lambda kv: kv[1]["hasta"])[:30]:
            lineas.append(f"{ip} · hasta {datetime.fromtimestamp(x['hasta']):%d/%m}")
        return "\n".join(lineas)

    def o_visto(self, args):
        if not args or not args[0].lstrip("#").isdigit():
            return "Uso: /visto 12 (el número sale en /estado)"
        n = int(args[0].lstrip("#"))
        h = Historial(self.v.ruta_db)
        try:
            cambiadas = h.marcar_vista(n)
        finally:
            h.db.close()
        return f"👁 Incidencia #{n} marcada como vista: no habrá más recordatorios." if cambiadas else \
            f"No hay ninguna incidencia abierta con el número {n}."

    def o_silenciar(self, args):
        if not args:
            return "Uso: /silenciar 2h · /silenciar 30m · /silenciar no"
        if args[0].lower() in ("no", "0", "off"):
            self.v.estado["silencio_hasta"] = 0
            self.v.guardar_estado()
            return "🔔 Avisos activados de nuevo."
        m = re.fullmatch(r"(\d+)\s*([hm]?)", args[0].lower())
        if not m:
            return "Uso: /silenciar 2h · /silenciar 30m · /silenciar no"
        seg = int(m.group(1)) * (60 if m.group(2) == "m" else 3600)
        seg = min(seg, 24 * 3600)
        self.v.estado["silencio_hasta"] = time.time() + seg
        self.v.guardar_estado()
        return (f"🔕 Sin avisos hasta las {datetime.fromtimestamp(time.time() + seg):%H:%M}. "
                "Los CRÍTICOS llegan igual. Todo se sigue apuntando en el historial.")

    def o_aceptar_ficheros(self, args):
        import mod_ficheros
        mod_ficheros.aceptar(self.v.cfg, self.v.estado, self.v.cred)
        self.v.guardar_estado()
        return "✅ Los ficheros actuales del servidor quedan como referencia."

    def o_aceptar_web(self, args):
        self.v.estado["web_scripts_conocidos"] = self.v.estado.get("web_scripts_actuales", [])
        self.v.guardar_estado()
        return "✅ Scripts aceptados: " + (", ".join(self.v.estado["web_scripts_conocidos"]) or "(ninguno)")


def _ip_valida(texto):
    try:
        ip = ipaddress.ip_address(texto.strip())
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_reserved:
        return None
    return str(ip)
