"""Escucha a Telegram: los botones de los avisos y las órdenes escritas (/bloquear, /estado…).

Corre en el centinela, en un hilo aparte (si hubiera dos máquinas escuchando el mismo bot,
Telegram se lo daría a una sola). Solo obedece al chat de TELEGRAM_CHAT_ID: cualquier
otro mensaje se ignora y se apunta en el log.
"""
import json
import ssl
import threading
import time
import urllib.request

from comun import AGENTE, log

MAX_ANTIGUEDAD = 600  # una orden de hace más de 10 min (equipo apagado) no se ejecuta


class BotTelegram:
    def __init__(self, vigia, ordenes):
        self.v = vigia
        self.ordenes = ordenes
        self.token = vigia.cred.get("TELEGRAM_TOKEN", "")
        self.chat = str(vigia.cred.get("TELEGRAM_CHAT_ID", ""))

    def arrancar(self):
        if not self.token or not self.chat:
            log.warning("Telegram sin configurar: no se atenderán órdenes desde el móvil")
            return
        threading.Thread(target=self._bucle, daemon=True, name="telegram").start()
        log.info("Escuchando órdenes de Telegram")

    def _api(self, metodo, datos, timeout=30):
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{metodo}",
                                     data=json.dumps(datos).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": AGENTE})
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return json.loads(r.read())

    def responder(self, texto, botones=None):
        datos = {"chat_id": self.chat, "text": texto[:3500], "disable_web_page_preview": True}
        if botones:
            datos["reply_markup"] = {"inline_keyboard": [botones]}
        try:
            self._api("sendMessage", datos)
        except Exception as e:
            log.warning("No se pudo contestar en Telegram: %s", type(e).__name__)

    def _bucle(self):
        offset = self.v.estado.get("telegram_offset", 0)
        while True:
            try:
                r = self._api("getUpdates", {"offset": offset, "timeout": 50,
                                             "allowed_updates": ["message", "callback_query"]}, timeout=70)
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    self.v.estado["telegram_offset"] = offset
                    self._atender(u)
            except Exception as e:
                log.debug("Telegram: %s", type(e).__name__)
                time.sleep(10)

    def _atender(self, u):
        if "callback_query" in u:
            cb = u["callback_query"]
            chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            try:
                self._api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Hecho" if chat == self.chat else ""})
            except Exception:
                pass
            if chat != self.chat:
                log.warning("Botón pulsado desde un chat no autorizado (%s): ignorado", chat)
                return
            accion, _, valor = cb.get("data", "").partition(":")
            orden = {"b": f"/bloquear {valor}", "d": f"/desbloquear {valor}", "v": f"/visto {valor}"}.get(accion)
            if orden:
                self._ejecutar(orden)
            return
        m = u.get("message") or {}
        chat = str(m.get("chat", {}).get("id", ""))
        texto = m.get("text", "")
        if chat != self.chat:
            log.warning("Mensaje de un chat no autorizado (%s): ignorado", chat)
            return
        if time.time() - m.get("date", 0) > MAX_ANTIGUEDAD:
            self.responder(f"⏱ Esta orden llegó con retraso y no la he ejecutado: «{texto}». Repítela si sigue haciendo falta.")
            return
        if texto.startswith("/"):
            self._ejecutar(texto)
        else:
            self.responder("Escribe /ayuda para ver las órdenes.")

    def _ejecutar(self, orden):
        respuesta = self.ordenes.ejecutar(orden, quien="telegram")
        botones = None
        if orden.startswith("/bloquear ") and respuesta.startswith("🚫"):
            ip = orden.split()[1]
            botones = [{"text": f"↩️ Deshacer (desbloquear {ip})", "callback_data": f"d:{ip}"}]
        self.responder(respuesta, botones)
