"""Ficheros del servidor: se compara cada carpeta vigilada con la última foto aceptada.

Solo lista carpetas (no baja ni abre ficheros). La primera vez, la foto se toma como
referencia; después, cualquier fichero nuevo, borrado o cambiado es una incidencia hasta
que se acepta con `python vigia.py --aceptar-ficheros`.
"""
import re
from datetime import datetime

from comun import Hallazgo, Resultado
from ftps import FTPS

PHP = re.compile(r"\.(php\d?|phtml|phar)$", re.I)


def carpetas(cfg):
    f = cfg["ficheros"]
    hoy = datetime.now()
    raiz = f["raiz"].rstrip("/")
    for c in f["carpetas"]:
        c = c.format(anio=hoy.strftime("%Y"), mes=hoy.strftime("%m"))
        yield c, f"{raiz}/{c}".rstrip("/")


def foto(cfg, cred):
    instantanea = {}
    with FTPS(cred) as ftp:
        for nombre, ruta in carpetas(cfg):
            try:
                instantanea[nombre] = {n: i for n, i in ftp.listar(ruta).items() if i["tipo"] == "fichero"}
            except Exception:
                instantanea[nombre] = None  # la carpeta del mes puede no existir aún
    return instantanea


def comprobar(cfg, estado, cred):
    res = Resultado("ficheros")
    try:
        actual = foto(cfg, cred)
    except Exception as e:
        res.revisado = False
        res.hallazgos.append(Hallazgo("vigia:ficheros", "vigia_fallo", "media",
                                      "El Vigía no puede revisar los ficheros", f"{type(e).__name__}: {e}",
                                      {"modulo": "ficheros"}))
        return res
    ref = estado.setdefault("ficheros_referencia", {})
    criticos = set(cfg["ficheros"].get("criticos", []))
    total = 0
    for carpeta, ficheros in actual.items():
        if ficheros is None:
            continue
        total += len(ficheros)
        etiqueta = carpeta or "la raíz de la web"
        if "uploads" in carpeta:
            for n in ficheros:
                if PHP.search(n):
                    ruta = f"{carpeta}/{n}"
                    res.hallazgos.append(Hallazgo(f"ficheros:php-uploads:{ruta}", "fichero_php_en_uploads",
                                                  "critica", "Fichero PHP en la carpeta de subidas", ruta,
                                                  {"ruta": ruta}))
        if carpeta not in ref or ref[carpeta] is None:
            ref[carpeta] = ficheros  # primera vez que se ve esta carpeta
            continue
        antes = ref[carpeta]
        nuevos = sorted(set(ficheros) - set(antes))
        borrados = sorted(set(antes) - set(ficheros))
        cambiados = sorted(n for n in set(ficheros) & set(antes)
                           if ficheros[n]["tamano"] != antes[n]["tamano"] or ficheros[n]["fecha"] != antes[n]["fecha"])
        if nuevos:
            hay_php = any(PHP.search(n) for n in nuevos)
            # En uploads se suben imágenes a diario: solo preocupa el código.
            if "uploads" in carpeta and not hay_php:
                pass
            else:
                res.hallazgos.append(Hallazgo(f"ficheros:nuevo:{carpeta}", "fichero_nuevo",
                                              "alta" if hay_php else "media",
                                              f"Ficheros nuevos en {etiqueta}", ", ".join(nuevos[:8]),
                                              {"carpeta": etiqueta, "ficheros": ", ".join(nuevos[:8])}))
        importantes = [n for n in cambiados if n in criticos or PHP.search(n)]
        if "uploads" not in carpeta and importantes:
            sev = "alta" if any(n in criticos for n in importantes) else "media"
            res.hallazgos.append(Hallazgo(f"ficheros:cambiado:{carpeta}", "fichero_cambiado", sev,
                                          f"Ficheros modificados en {etiqueta}", ", ".join(importantes[:8]),
                                          {"ficheros": ", ".join(f"{etiqueta}/{n}" for n in importantes[:8])}))
        borrados_imp = [n for n in borrados if "uploads" not in carpeta]
        if borrados_imp:
            res.hallazgos.append(Hallazgo(f"ficheros:borrado:{carpeta}", "fichero_borrado", "media",
                                          f"Ficheros borrados en {etiqueta}", ", ".join(borrados_imp[:8]),
                                          {"carpeta": etiqueta, "ficheros": ", ".join(borrados_imp[:8])}))
    res.resumen = f"{total} ficheros en {sum(1 for x in actual.values() if x is not None)} carpetas"
    return res


def aceptar(cfg, estado, cred):
    estado["ficheros_referencia"] = foto(cfg, cred)
