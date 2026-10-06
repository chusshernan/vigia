"""Mandar los avisos: WhatsApp (CallMeBot) y Telegram.

modo «todos»:   sale por todos los canales (WhatsApp para enterarse, Telegram para actuar:
                sus avisos llevan botones como «Bloquear esta IP»).
modo «primero»: por el primer canal que funcione, en el orden de vigia.json.

WhatsApp va por un servicio de terceros (CallMeBot), que ve el texto. Por eso por ahí solo
sale un «timbre» corto, sin detalles ni el nombre completo de la emisora; los detalles y
los botones, por Telegram.
Todo queda en el historial.
"""
import json
import time
import urllib.parse
from datetime import datetime

from comun import SEVERIDADES, log, pedir

ICONO = {"critica": "🔴", "alta": "🟠", "media": "🟡", "baja": "🔵", "info": "⚪"}
NOMBRE_SEV = {"critica": "CRÍTICO", "alta": "ALTO", "media": "MEDIO", "baja": "BAJO", "info": "INFO"}
NIVEL_ALERTA = {"critica": "CRÍTICA", "alta": "ALTA", "media": "MEDIA", "baja": "BAJA", "info": "INFO"}
MAX_CARACTERES = 1500


class Avisador:
    def __init__(self, cfg, cred, historial, forzar_envio=False, botones=False, estado=None):
        a = cfg["avisos"]
        self.nombre = cfg.get("nombre", "Mi Radio")
        self.activos = a.get("activos", False) or forzar_envio
        self.canales = a.get("canales", ["whatsapp", "telegram"])
        self.minima = a.get("severidad_minima", "media")
        self.recordar_cada_horas = a.get("recordar_cada_horas", 6)
        self.max_por_hora = a.get("max_por_hora", 15)
        self.modo = a.get("modo", "todos")
        self.botones = botones  # solo el centinela atiende los botones de Telegram
        self.estado = estado if estado is not None else {}
        self.nombre_timbre = a.get("whatsapp_nombre", "Radio")
        self.cred = cred
        self.h = historial
        self._silenciados = 0

    def merece_aviso(self, severidad):
        return SEVERIDADES.index(severidad) >= SEVERIDADES.index(self.minima)

    # --- textos ----------------------------------------------------------------
    def avisar(self, inc, que_pasa, pasos, recordatorio=False):
        cab = f"{ICONO.get(inc['severidad'], '')} *{self.nombre} · {NOMBRE_SEV.get(inc['severidad'], '')}*"
        if recordatorio:
            desde = datetime.fromisoformat(inc["inicio"]).strftime("%d/%m %H:%M")
            cab += f"\nSigue sin resolverse (desde {desde})"
        lineas = [cab, "", f"*{inc['titulo']}*", que_pasa]
        datos = json.loads(inc.get("datos") or "{}")
        ip = datos.get("ip")
        if pasos:
            lineas += ["", "*Qué hacer:*"]
            # En el móvil, los tres primeros pasos; el resto está en el panel.
            for i, p in enumerate(pasos[:3], 1):
                lineas.append(f"{i}. {p}")
            if len(pasos) > 3:
                resto = len(pasos) - 3
                lineas.append(f"(+{resto} {'paso' if resto == 1 else 'pasos'} más en el panel)")
        botones = []
        if self.botones:
            lineas += ["", f"Incidencia #{inc['id']}"]
            if ip:
                lineas.append(f"Bloquear: /bloquear {ip}")
                botones.append({"text": f"🚫 Bloquear {ip}", "callback_data": f"b:{ip}"})
            botones.append({"text": "👁 Visto", "callback_data": f"v:{inc['id']}"})
        timbre = (f"{ICONO.get(inc['severidad'], '')} {self.nombre_timbre}: alerta "
                  f"{NIVEL_ALERTA.get(inc['severidad'], '')}. Mira Telegram.")
        return self._mandar("\n".join(lineas), inc["id"], inc["severidad"], botones, timbre)

    def avisar_resuelta(self, inc, nota=""):
        ini = datetime.fromisoformat(inc["inicio"])
        dur = datetime.now().astimezone() - ini
        mins = int(dur.total_seconds() // 60)
        dur_txt = f"{mins} min" if mins < 120 else f"{mins // 60} h {mins % 60} min"
        texto = f"✅ *{self.nombre} · RESUELTO*\n\n{inc['titulo']}\nDuró {dur_txt} (desde las {ini:%H:%M} del {ini:%d/%m})."
        if nota:
            texto += f"\n{nota}"
        return self._mandar(texto, inc["id"], "alta", timbre=f"✅ {self.nombre_timbre}: resuelto. Mira Telegram.")

    def mandar_texto(self, texto):
        """Para pruebas y resúmenes."""
        return self._mandar(texto, None, "critica")

    # --- envío -----------------------------------------------------------------
    def _mandar(self, texto, inc_id, severidad, botones=None, timbre=None):
        texto = texto[:MAX_CARACTERES]
        if severidad != "critica" and self.estado.get("silencio_hasta", 0) > time.time():
            self.h.guardar_aviso("silenciado", False, texto, "/silenciar activo", inc_id)
            return False  # queda pendiente: se manda al acabar el silencio si sigue abierta
        if not self.activos:
            log.info("[aviso simulado — avisos.activos=false]\n%s", texto)
            self.h.guardar_aviso("simulado", True, texto, "", inc_id)
            return True
        # Freno: si hay una avalancha, solo pasa lo crítico.
        if self.h.avisos_ultima_hora() >= self.max_por_hora and severidad != "critica":
            self._silenciados += 1
            self.h.guardar_aviso("frenado", False, texto, "límite de avisos por hora", inc_id)
            return False
        alguno = False
        for canal in self.canales:
            envio = getattr(self, f"_por_{canal}", None)
            if not envio:
                continue
            if canal == "telegram":
                enviado = texto
                ok, error = envio(texto, botones)
            else:
                enviado = timbre or texto
                ok, error = envio(enviado)
            self.h.guardar_aviso(canal, ok, enviado, error, inc_id)
            if ok:
                log.info("Aviso enviado por %s", canal)
                alguno = True
                if self.modo != "todos":
                    break
            else:
                log.warning("No sale por %s: %s", canal, error)
        return alguno

    def _por_whatsapp(self, texto):
        tel = self.cred.get("CALLMEBOT_PHONE", "")
        clave = self.cred.get("CALLMEBOT_APIKEY", "")
        if not tel or not clave:
            return False, "faltan CALLMEBOT_PHONE / CALLMEBOT_APIKEY"
        url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
            {"phone": tel, "text": texto, "apikey": clave})
        r = pedir(url, timeout=30, max_bytes=20000)
        cuerpo = r.cuerpo.decode("utf-8", "replace")
        if r.ok and "error" not in cuerpo.lower() and "invalid" not in cuerpo.lower():
            return True, ""
        return False, r.error or cuerpo[:200]

    def _por_telegram(self, texto, botones=None):
        token = self.cred.get("TELEGRAM_TOKEN", "")
        chat = self.cred.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return False, "faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID"
        mensaje = {"chat_id": chat, "text": texto.replace("*", ""),
                   "disable_web_page_preview": True}
        if botones:
            mensaje["reply_markup"] = {"inline_keyboard": [botones]}
        cuerpo = json.dumps(mensaje).encode()
        r = pedir(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, datos=cuerpo,
                  cabeceras={"Content-Type": "application/json"})
        if r.ok:
            return True, ""
        return False, r.error  # sin el token en el mensaje
