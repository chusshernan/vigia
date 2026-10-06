"""El directo: que llegue audio a la velocidad debida, que no sea silencio y cuántos escuchan.

Ojo: si la estación de AzuraCast no tiene AutoDJ (backend «none»), solo retransmite lo que
le manda el codificador del estudio, y en ese modo marca siempre is_online=false. Por eso
ese campo NO se usa. Lo fiable es el propio audio y los oyentes del montaje.
"""
import json
import re
import shutil
import ssl
import subprocess
import time
import urllib.request

from comun import AGENTE, Hallazgo, Resultado, pedir


def muestra_audio(url, segundos):
    """Escucha `segundos` del directo. Devuelve (estado HTTP, tipo, bytes, segundos reales, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Icy-MetaData": "0"})
    t0 = time.monotonic()
    leidos = 0
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            tipo = r.headers.get("Content-Type", "")
            while time.monotonic() - t0 < segundos:
                trozo = r.read(16384)
                if not trozo:
                    break
                leidos += len(trozo)
            return r.status, tipo, leidos, time.monotonic() - t0, ""
    except Exception as e:
        return 0, "", leidos, time.monotonic() - t0, f"{type(e).__name__}: {e}"


def oyentes(cfg, res):
    """Oyentes del montaje según AzuraCast. Se pregunta antes de escuchar nosotros."""
    url = cfg["stream"].get("api_azuracast")
    if not url:
        return
    r = pedir(url, timeout=20, max_bytes=500_000)
    if not r.ok:
        return
    try:
        d = json.loads(r.cuerpo)
        montajes = d.get("station", {}).get("mounts", [])
        res.metricas["oyentes"] = sum(m.get("listeners", {}).get("current", 0) for m in montajes)
    except (ValueError, AttributeError):
        pass


def comprobar(cfg, estado):
    s = cfg["stream"]
    res = Resultado("stream")
    oyentes(cfg, res)
    estado_http, tipo, leidos, dur, error = muestra_audio(s["url"], s.get("segundos_muestra", 5))

    if error or estado_http not in (200, 206) or leidos < 4096:
        detalle = error or f"HTTP {estado_http}, {leidos} bytes"
        res.hallazgos.append(Hallazgo("stream:caido", "stream_caido", "critica", "El directo no se oye", detalle))
        res.resumen = f"caído ({detalle})"
        res.metricas["stream_ok"] = 0
        return res
    if not tipo.startswith("audio/"):
        res.hallazgos.append(Hallazgo("stream:caido", "stream_caido", "critica", "El directo no se oye",
                                      f"devuelve «{tipo}» en vez de audio"))
        res.metricas["stream_ok"] = 0
        return res

    kbps = leidos * 8 / 1000 / max(dur, 0.1)
    res.metricas["stream_ok"] = 1
    res.metricas["stream_kbps"] = round(kbps)
    esperado = s.get("kbps", 128)
    # Icecast manda un colchón al principio, así que solo preocupa si va claramente por debajo.
    if kbps < esperado * 0.6:
        res.hallazgos.append(Hallazgo("stream:lento", "stream_lento", "media", "El directo llega entrecortado",
                                      f"{kbps:.0f} kbps", {"kbps": f"{kbps:.0f}", "esperado": esperado}))

    res.resumen = f"OK, {kbps:.0f} kbps"
    if "oyentes" in res.metricas:
        res.resumen += f", {res.metricas['oyentes']} oyentes"
    return res


def comprobar_silencio(cfg, estado):
    """Escucha un rato con ffmpeg y mide el silencio más largo."""
    s = cfg["stream"]
    sil = s.get("silencio", {})
    res = Resultado("silencio")
    ffmpeg = shutil.which(sil.get("ffmpeg", "ffmpeg"))
    if not sil.get("activo") or not ffmpeg:
        res.revisado = False
        res.resumen = "desactivado" if not sil.get("activo") else "falta ffmpeg"
        return res
    seg = sil.get("segundos_escucha", 25)
    # ffmpeg no siempre lee bien https directo; se le pasa el audio por la entrada estándar.
    req = urllib.request.Request(s["url"], headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            datos = b""
            t0 = time.monotonic()
            while time.monotonic() - t0 < seg:
                trozo = r.read(32768)
                if not trozo:
                    break
                datos += trozo
    except Exception as e:
        res.revisado = False
        res.resumen = f"no se pudo escuchar ({type(e).__name__})"
        return res
    p = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", "pipe:0", "-af",
                        f"silencedetect=n={sil.get('umbral_db', -50)}dB:d=2", "-f", "null", "-"],
                       input=datos, capture_output=True, timeout=120)
    salida = p.stderr.decode("utf-8", "replace")
    duraciones = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", salida)]
    # Un silencio que empieza y no termina en la muestra no trae duración: se mide hasta el final.
    abiertos = re.findall(r"silence_start: ([\d.]+)", salida)
    if len(abiertos) > len(duraciones):
        m = re.findall(r"time=(\d+):(\d+):([\d.]+)", salida)
        total = seg
        if m:
            h, mi, se = m[-1]
            total = int(h) * 3600 + int(mi) * 60 + float(se)
        duraciones.append(total - float(abiertos[-1]))
    mayor = max(duraciones, default=0)
    res.metricas["silencio_max_s"] = round(mayor, 1)
    if mayor >= sil.get("segundos_silencio", 15):
        res.hallazgos.append(Hallazgo("stream:silencio", "stream_silencio", "alta", "El directo está en silencio",
                                      f"{mayor:.0f} s sin sonido", {"segundos": f"{mayor:.0f}"}))
    res.resumen = f"silencio máximo {mayor:.0f} s en {seg} s"
    return res
