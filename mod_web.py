"""La web: disponibilidad, velocidad, contenido, scripts de terceros, certificado, DNS y API."""
import re
import socket
import ssl
from datetime import datetime, timezone

from comun import Hallazgo, Resultado, pedir

PALABRAS_HACKEO = ["hacked by", "pwned by", "defaced", "owned by", "h4ck", "free bitcoin",
                   "casino online", "viagra"]


def dominios_de_scripts(html):
    dominios = set()
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)", html, re.I):
        m = re.match(r"(?:https?:)?//([^/:]+)", src)
        if m:
            dominios.add(m.group(1).lower())
    return dominios


def comprobar(cfg, estado):
    w = cfg["web"]
    res = Resultado("web")
    dominio = w["dominio"]

    # DNS
    try:
        ip = socket.gethostbyname(dominio)
        if w.get("ip_esperada") and ip != w["ip_esperada"]:
            res.hallazgos.append(Hallazgo("web:dns", "web_dns", "alta", "El dominio apunta a otro servidor",
                                          f"{dominio} → {ip}", {"ip": ip, "esperada": w["ip_esperada"]}))
    except OSError as e:
        res.hallazgos.append(Hallazgo("web:caida", "web_caida", "critica", "La web está caída",
                                      f"no se resuelve el dominio ({e})"))
        res.resumen = "DNS no resuelve"
        return res

    # Portada (un reintento antes de darla por caída)
    r = pedir(w["url"], timeout=30)
    if not r.ok or r.estado != 200:
        r = pedir(w["url"], timeout=30)
    if not r.ok or r.estado != 200:
        detalle = r.error or f"HTTP {r.estado}"
        res.hallazgos.append(Hallazgo("web:caida", "web_caida", "critica", "La web está caída", detalle))
        res.resumen = f"caída ({detalle})"
        return res

    res.metricas["web_segundos"] = round(r.segundos, 2)
    html = r.cuerpo.decode("utf-8", "replace")

    if r.segundos > w.get("lenta_segundos", 5):
        res.hallazgos.append(Hallazgo("web:lenta", "web_lenta", "media", "La web va lenta",
                                      f"{r.segundos:.1f} s", {"segundos": f"{r.segundos:.1f}",
                                                              "limite": w.get("lenta_segundos", 5)}))

    problemas = [f"falta «{m}»" for m in w.get("marcas_obligatorias", []) if m not in html]
    if len(r.cuerpo) < w.get("tamano_minimo_bytes", 0):
        problemas.append(f"ocupa solo {len(r.cuerpo) // 1024} KB")
    bajo = html.lower()
    raras = [p for p in PALABRAS_HACKEO if p in bajo]
    if raras:
        problemas.append("aparece: " + ", ".join(raras))
    if problemas:
        sev = "critica" if raras else "alta"
        res.hallazgos.append(Hallazgo("web:contenido", "web_contenido", sev, "La portada no se ve como debería",
                                      "; ".join(problemas)))

    # Scripts de dominios externos: se compara con la foto aceptada.
    actuales = dominios_de_scripts(html)
    conocidos = set(estado.get("web_scripts_conocidos") or [])
    if not conocidos:
        estado["web_scripts_conocidos"] = sorted(actuales)  # primera vez: se toma como referencia
    else:
        nuevos = sorted(actuales - conocidos)
        if nuevos:
            res.hallazgos.append(Hallazgo("web:scripts", "web_scripts_nuevos", "alta",
                                          "Scripts nuevos de terceros en la portada", ", ".join(nuevos),
                                          {"dominios": ", ".join(nuevos)}))
    estado["web_scripts_actuales"] = sorted(actuales)

    # Certificado
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((dominio, 443), timeout=15) as s:
            with ctx.wrap_socket(s, server_hostname=dominio) as t:
                caduca = datetime.strptime(t.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z") \
                    .replace(tzinfo=timezone.utc)
        dias = (caduca - datetime.now(timezone.utc)).days
        res.metricas["web_cert_dias"] = dias
        if dias <= w.get("certificado_aviso_dias", 14):
            sev = "critica" if dias <= 2 else "alta" if dias <= 7 else "media"
            res.hallazgos.append(Hallazgo("web:certificado", "web_certificado", sev,
                                          "El certificado HTTPS está a punto de caducar", f"{dias} días",
                                          {"dias": dias, "fecha": caduca.strftime("%d/%m/%Y")}))
    except ssl.SSLCertVerificationError as e:
        res.hallazgos.append(Hallazgo("web:certificado", "web_certificado", "critica",
                                      "El certificado HTTPS no es válido", str(e), {"dias": 0, "fecha": "ya"}))
    except OSError:
        pass  # si la portada ha cargado, un fallo puntual aquí no es noticia

    # API de WordPress
    if w.get("api"):
        a = pedir(w["api"], timeout=30, max_bytes=200_000)
        if not a.ok or not a.cuerpo.lstrip().startswith(b"{"):
            res.hallazgos.append(Hallazgo("web:api", "web_api", "media", "La API de WordPress no responde",
                                          a.error or "no devuelve JSON"))

    res.resumen = f"OK en {r.segundos:.1f} s"
    return res
