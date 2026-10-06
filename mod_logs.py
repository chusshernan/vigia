"""Los logs de Apache de la web: ataques y fallos.

En el centro de operaciones se leen los logs de Apache por FTPS. El centinela no tiene
FTP: lee los registros del SENSOR del mu-plugin (mismo formato, solo lo que pasa por
WordPress) a través de la web, con el usuario «vigia». En un hosting compartido el PHP no
suele poder leer los logs de Apache (open_basedir), por eso existe el sensor.

Cada pasada lee solo lo nuevo (desde el último byte leído). El hosting rota los logs una
vez al día (en Plesk, el log de acceso se vuelca en access_ssl_log.processed y el de
errores pasa a error_log.1.gz; los nombres se ajustan en vigia.json). Si se detecta la rotación, se lee también el rotado, quedándose solo con
las líneas posteriores a la última vista. Así no se pierde nada aunque el equipo haya
estado apagado unas horas (el servidor guarda 7 días).
"""
import gzip
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import unquote

from comun import AGENTE, Hallazgo, Resultado, log, pedir
from ftps import FTPS
from wp_api import LogsPorWeb

LINEA_ACCESO = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<metodo>\S+) (?P<ruta>\S+)[^"]*" '
    r'(?P<estado>\d{3}) (?P<tam>\S+) "(?P<ref>[^"]*)" "(?P<ua>[^"]*)"')
LINEA_ERROR = re.compile(r"^\[(?P<ts>\w{3} \w{3} \d{2} [\d:.]+ \d{4})\] \[(?P<mod>[^\]]+)\] (?P<resto>.*)$")

# Rutas que solo pide quien busca fallos.
SENSIBLE = re.compile(
    r"/\.env|/\.git/|/\.svn|wp-config\.php[.~_-]|wp-config\.(bak|old|txt|save|orig)|/\.htpasswd|/\.htaccess"
    r"|phpmyadmin|/pma/|/adminer|\.sql(\.gz|\.zip)?$|/backup|\.bak$|\.old$|/vendor/phpunit|/\.aws|/\.ssh"
    r"|/server-status|/debug\.log|/\.DS_Store|/cgi-bin/|/(shell|wso|alfa|c99|r57|cmd|up|upload|bypass)\.php"
    r"|/phpinfo\.php|/info\.php|/setup-config\.php|/wp-admin/install\.php|/\.well-known/[^/]+\.php"
    r"|/(admin|config|database|db|dump|site)\.(zip|tar|gz|sql)", re.I)
# De las anteriores, las que si devuelven contenido son una fuga de datos de verdad.
EXPOSICION = re.compile(
    r"/\.env|/\.git/|wp-config\.php[.~_-]|wp-config\.(bak|old|txt|save|orig)|/\.htpasswd|\.sql(\.gz|\.zip)?$"
    r"|\.bak$|/debug\.log|/phpinfo\.php|/info\.php|/(admin|config|database|db|dump|site|backup)\.(zip|tar|gz|sql)", re.I)
INYECCION = re.compile(
    r"union[\s+(]+(all[\s+]+)?select|select.+from.+where|sleep\(\s*\d|benchmark\(|\.\./|/etc/passwd|<script"
    r"|javascript:|base64_decode|eval\(|\$\{jndi|information_schema|concat\(|\bor\s+'?1'?\s*=\s*'?1|%00", re.I)
WEBSHELL = re.compile(r"^/wp-content/uploads/.*\.(php\d?|phtml|phar)(\?|$)", re.I)
ENUMERACION = re.compile(r"[?&]author=\d|/wp-json/wp/v2/users", re.I)
BOT_DECLARADO = re.compile(
    r"googlebot|bingbot|yandex|baiduspider|duckduckbot|applebot|facebookexternalhit|ahrefs|semrush|mj12"
    r"|petalbot|gptbot|claudebot|ccbot|bytespider|amazonbot|dotbot|seznambot|uptimerobot|betteruptime", re.I)


def _ts_acceso(t):
    return datetime.strptime(t, "%d/%b/%Y:%H:%M:%S %z").timestamp()


def _ts_error(t):
    return datetime.strptime(t.split(".")[0] + t[-5:], "%a %b %d %H:%M:%S %Y").timestamp()


def leer_nuevo(ftp, carpeta, nombre, rotado, st, extraer_ts):
    """Devuelve las líneas nuevas de un log y actualiza el estado `st`."""
    ruta = f"{carpeta}/{nombre}"
    tam = ftp.tamano(ruta)
    listado = ftp.listar(carpeta)
    firma_rot = json.dumps(listado.get(rotado, {}), sort_keys=True)
    offset = st.get("offset", 0)
    ultima = st.get("ultima_ts", 0)
    trozos = []

    rotado_ahora = st.get("firma_rotado") not in (None, firma_rot) or tam < offset
    if rotado_ahora:
        log.info("Log %s rotado: se recupera la parte que faltaba", nombre)
        if rotado in listado:
            datos = ftp.leer(f"{carpeta}/{rotado}")
            if rotado.endswith(".gz"):
                datos = gzip.decompress(datos)
            trozos.append(datos)
        offset = 0
    trozos.append(ftp.leer(ruta, desde=offset) if tam > offset else b"")

    lineas = []
    leido_actual = trozos[-1]
    corte = leido_actual.rfind(b"\n") + 1  # la última línea puede estar a medias
    trozos[-1] = leido_actual[:corte]
    nueva_ultima = ultima
    filtrar = rotado_ahora or offset == 0
    for trozo in trozos:
        for linea in trozo.decode("utf-8", "replace").splitlines():
            ts = extraer_ts(linea)
            if ts is None:
                continue
            if filtrar and ts <= ultima:
                continue
            nueva_ultima = max(nueva_ultima, ts)
            lineas.append((ts, linea))
    st.update(offset=offset + corte, ultima_ts=nueva_ultima, firma_rotado=firma_rot)
    return lineas


def _extraer_ts_acceso(linea):
    m = LINEA_ACCESO.match(linea)
    try:
        return _ts_acceso(m.group("ts")) if m else None
    except ValueError:
        return None


def _extraer_ts_error(linea):
    m = LINEA_ERROR.match(linea)
    try:
        return _ts_error(m.group("ts")) if m else None
    except ValueError:
        return None


def pais_de(ip, estado):
    """País y compañía de una IP (ipinfo.io, con caché de 30 días)."""
    cache = estado.setdefault("geo", {})
    c = cache.get(ip)
    if c and time.time() - c["t"] < 30 * 86400:
        return c["txt"]
    txt = "?"
    r = pedir(f"https://ipinfo.io/{ip}/json", timeout=10, max_bytes=5000)
    if r.ok:
        try:
            d = json.loads(r.cuerpo)
            txt = ", ".join(x for x in (d.get("country"), (d.get("org") or "")[:40]) if x) or "?"
        except ValueError:
            pass
    cache[ip] = {"t": time.time(), "txt": txt}
    return txt


def comprobar(cfg, estado, cred):
    L = cfg["logs"]
    U = L["umbrales"]
    res = Resultado("logs")
    st = estado.setdefault("logs", {})
    try:
        if cred.get("FTP_USER") and cred.get("FTP_PASS"):
            fuente = FTPS(cred)
        else:
            L = {**L, **L["sensor"]}  # otros nombres de fichero, misma lógica
            fuente = LogsPorWeb(cfg, cred, [L["acceso_rotado"], L["errores_rotado"]])
        with fuente as ftp:
            acceso = leer_nuevo(ftp, L["carpeta"], L["acceso"], L["acceso_rotado"],
                                st.setdefault("acceso", {}), _extraer_ts_acceso)
            errores = leer_nuevo(ftp, L["carpeta"], L["errores"], L["errores_rotado"],
                                 st.setdefault("errores", {}), _extraer_ts_error)
    except Exception as e:
        res.revisado = False
        res.hallazgos.append(Hallazgo("vigia:logs", "vigia_fallo", "media", "El Vigía no puede leer los logs",
                                      f"{type(e).__name__}: {e}", {"modulo": "logs"}))
        res.resumen = "sin acceso a los logs"
        return res

    confianza = set(L.get("ips_de_confianza", []))
    ahora = time.time()
    ventana = L.get("ventana_minutos", 60) * 60
    v = st.setdefault("ventana", {})
    for k in ("login", "sensible", "inyeccion", "enum", "e5xx", "php", "peticiones"):
        v.setdefault(k, [])
    instantaneos = []
    rotos = st.setdefault("rotos", {})
    dominio = cfg["web"]["dominio"]

    for ts, linea in acceso:
        m = LINEA_ACCESO.match(linea)
        ip, metodo, ruta, codigo, ua, ref = (m.group("ip"), m.group("metodo"), m.group("ruta"),
                                             int(m.group("estado")), m.group("ua"), m.group("ref"))
        if ua.startswith(AGENTE.split("/")[0]):
            continue  # el propio Vigía
        dec = unquote(unquote(ruta))
        tam = int(m.group("tam")) if m.group("tam").isdigit() else 0
        v["peticiones"].append([ts, ip, ua[:120]])
        if codigo >= 500:
            v["e5xx"].append([ts, ip, ruta[:120], codigo])
        if codigo == 404 and dominio in ref:
            rotos[ruta[:200]] = [ts, ref[:200]]
        if ip in confianza:
            continue
        sin_query = ruta.split("?")[0]
        if metodo == "POST" and sin_query in ("/wp-login.php", "/xmlrpc.php"):
            v["login"].append([ts, ip])
            if (sin_query == "/wp-login.php" and codigo == 302
                    and ("action=" not in ruta or "action=login" in ruta)):
                instantaneos.append(Hallazgo(f"logs:login:{ip}", "login_correcto", "media",
                                             f"Inicio de sesión en WordPress desde {ip}", linea[:300], {"ip": ip}))
        if WEBSHELL.search(sin_query) and codigo == 200:
            instantaneos.append(Hallazgo(f"logs:webshell:{sin_query}", "ataque_webshell", "critica",
                                         "Se ha ejecutado un PHP dentro de uploads", linea[:300],
                                         {"ip": ip, "ruta": sin_query}))
        if SENSIBLE.search(sin_query):
            v["sensible"].append([ts, ip, sin_query[:100]])
            if EXPOSICION.search(sin_query) and codigo == 200 and tam > 100:
                instantaneos.append(Hallazgo(f"logs:expuesto:{sin_query}", "ataque_expuesto", "critica",
                                             "Se ha descargado un fichero sensible", linea[:300],
                                             {"ip": ip, "ruta": sin_query}))
        if INYECCION.search(dec):
            v["inyeccion"].append([ts, ip, dec[:100]])
        if ENUMERACION.search(dec):
            v["enum"].append([ts, ip])

    for ts, linea in errores:
        m = LINEA_ERROR.match(linea)
        resto = re.sub(r"\[(pid|client|remote) [^\]]*\] ?", "", m.group("resto"))
        v["php"].append([ts, m.group("mod"), resto[:300]])

    # Puesta al día: si este equipo ha estado apagado, se repasan las horas perdidas una a
    # una (el servidor guarda 7 días). Lo que se encuentre se marca «de madrugada», etc.
    atrasados = []
    if acceso:
        inicio = min(ts for ts, _ in acceso)
        t = inicio
        while t < ahora - ventana:
            tramo = {k: [e for e in lista if t <= e[0] < t + ventana] for k, lista in v.items()}
            for x in _reglas(tramo, U, estado, confianza):
                x.titulo += f" (el {datetime.fromtimestamp(t):%d/%m hacia las %H:%M})"
                atrasados.append(x)
            t += ventana

    # Se quita de la ventana lo que tiene más de una hora.
    for k in v:
        v[k] = [e for e in v[k] if e[0] >= ahora - ventana]
    for ruta in [r for r, (ts, _) in rotos.items() if ts < ahora - 86400]:
        del rotos[ruta]

    res.hallazgos += instantaneos
    actuales = _reglas(v, U, estado, confianza)
    claves_actuales = {x.clave for x in actuales}
    # De lo atrasado, la última vez de cada cosa (y si sigue pasando ahora, manda lo actual).
    ultimo_atrasado = {x.clave: x for x in atrasados if x.clave not in claves_actuales}
    res.hallazgos += list(ultimo_atrasado.values()) + actuales
    if len(rotos) >= 5:
        ej = ", ".join(list(rotos)[:4])
        detalle = "\n".join(f"{r}  ← viene de {ref}" for r, (_, ref) in rotos.items())
        res.hallazgos.append(Hallazgo("logs:enlaces-rotos", "enlaces_rotos", "baja",
                                      f"{len(rotos)} enlaces rotos dentro de la web", detalle,
                                      {"n": len(rotos), "ejemplos": ej}))
    res.metricas["peticiones_hora"] = len(v["peticiones"])
    res.metricas["errores_5xx_hora"] = len(v["e5xx"])
    res.metricas["login_hora"] = len(v["login"])
    res.metricas["errores_servidor_hora"] = len(v["php"])
    st["rotos"] = rotos
    res.resumen = (f"{len(acceso)} líneas nuevas · última hora: {len(v['peticiones'])} peticiones, "
                   f"{len(v['login'])} intentos de login, {len(v['e5xx'])} errores 5xx")
    return res


def _reglas(v, U, estado, confianza=()):
    h = []

    def por_ip(eventos):
        c = Counter(e[1] for e in eventos)
        return c

    # Fuerza bruta contra el login
    logins = por_ip(v["login"])
    for ip, n in logins.items():
        if n >= U["login_por_ip"]:
            sev = "alta" if n >= U["login_por_ip"] * 5 else "media"
            h.append(Hallazgo(f"logs:fuerza-bruta:{ip}", "ataque_fuerza_bruta", sev,
                              f"Ataque al login desde {ip}", f"{n} intentos en una hora",
                              {"ip": ip, "n": n, "pais": pais_de(ip, estado)}))
    total = sum(logins.values())
    if total >= U["login_total"] and len(logins) >= 5:
        sev = "alta" if total >= U["login_total"] * 5 else "media"
        h.append(Hallazgo("logs:fuerza-bruta-distribuida", "ataque_fuerza_bruta_distribuida", sev,
                          "Ataque repartido contra el login", f"{total} intentos desde {len(logins)} IPs",
                          {"n": total, "ips": len(logins)}))

    # Escáneres
    rutas = defaultdict(list)
    for _, ip, ruta in v["sensible"]:
        rutas[ip].append(ruta)
    for ip, lista in rutas.items():
        if len(lista) >= U["sensibles_por_ip"]:
            ej = ", ".join(sorted(set(lista))[:4])
            h.append(Hallazgo(f"logs:escaneo:{ip}", "ataque_escaneo", "media", f"Escáner buscando fallos: {ip}",
                              f"{len(lista)} rutas sensibles",
                              {"ip": ip, "n": len(lista), "ejemplos": ej, "pais": pais_de(ip, estado)}))

    # Inyecciones
    rutas = defaultdict(list)
    for _, ip, ruta in v["inyeccion"]:
        rutas[ip].append(ruta)
    for ip, lista in rutas.items():
        if len(lista) >= U["inyeccion_por_ip"]:
            ej = " | ".join(lista[:2])
            h.append(Hallazgo(f"logs:inyeccion:{ip}", "ataque_inyeccion", "media",
                              f"Intentos de inyección desde {ip}", f"{len(lista)} peticiones",
                              {"ip": ip, "n": len(lista), "ejemplos": ej, "pais": pais_de(ip, estado)}))

    # Enumeración de usuarios
    for ip, n in por_ip(v["enum"]).items():
        if n >= 2:
            h.append(Hallazgo(f"logs:enumeracion:{ip}", "ataque_enumeracion", "baja",
                              f"Buscan los usuarios de WordPress: {ip}", f"{n} peticiones", {"ip": ip, "n": n}))

    # Volumen por IP
    cuenta = Counter(e[1] for e in v["peticiones"])
    agentes = {e[1]: e[2] for e in v["peticiones"]}
    for ip, n in cuenta.items():
        if n >= U["peticiones_por_ip"] and ip not in confianza:
            ua = agentes.get(ip, "")
            declarado = bool(BOT_DECLARADO.search(ua))
            h.append(Hallazgo(f"logs:rastreador:{ip}", "rastreador_masivo", "baja" if declarado else "media",
                              f"{'Robot' if declarado else 'Robot camuflado'} haciendo {n} peticiones/hora: {ip}",
                              ua, {"ip": ip, "n": n, "agente": ua[:80], "pais": pais_de(ip, estado)}))

    # Errores 5xx
    if len(v["e5xx"]) >= U["errores_5xx"]:
        ej = ", ".join(sorted({f"{e[3]} {e[2]}" for e in v["e5xx"]})[:3])
        h.append(Hallazgo("logs:5xx", "errores_5xx", "alta", "La web está dando errores de servidor",
                          f"{len(v['e5xx'])} en una hora", {"n": len(v["e5xx"]), "ejemplos": ej}))

    # Errores del servidor y de PHP
    for _, mod, msg in v["php"]:
        if re.search(r"PHP (Fatal error|Parse error)|Allowed memory size", msg):
            limpio = re.sub(r"\d+", "N", msg)[:160]
            clave = hashlib.sha1(limpio.encode()).hexdigest()[:10]
            h.append(Hallazgo(f"logs:php:{clave}", "errores_php", "alta", "Error grave de PHP en la web", msg,
                              {"mensaje": msg[:250]}))
    if len(v["php"]) >= U["errores_php_por_hora"]:
        frec = Counter(re.sub(r"\d+", "N", e[2])[:100] for e in v["php"]).most_common(1)[0][0]
        h.append(Hallazgo("logs:errores-servidor", "errores_php_muchos", "media",
                          "Muchos errores en el servidor web", f"{len(v['php'])} en una hora",
                          {"n": len(v["php"]), "mensaje": frec}))
    # Una misma clave solo una vez por pasada
    vistos, unicos = set(), []
    for x in h:
        if x.clave not in vistos:
            vistos.add(x.clave)
            unicos.append(x)
    return unicos
