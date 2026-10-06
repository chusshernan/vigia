"""Centro de operaciones: recoge lo que ha visto el centinela del ordenador de la radio.

Copia sus incidencias, métricas y avisos en el historial de aquí (sin volver a avisar: eso
ya lo hizo él) y abre una incidencia si el centinela no contesta o lleva rato sin trabajar.
"""
import json
from urllib.parse import quote
from datetime import datetime, timedelta

from comun import Hallazgo, Resultado, pedir
from historial import ahora, iso

# Las dos máquinas comprueban su propia conexión; la del estudio se guarda aparte.
RENOMBRAR = {"conexion": "conexion_radio"}


def comprobar(cfg, estado, cred, historial):
    c = cfg["centinela"]
    res = Resultado("centinela")
    if not c.get("url"):
        res.revisado = False
        res.resumen = "falta la dirección del centinela en vigia.json"
        return res
    st = estado.setdefault("centinela", {})
    desde = st.get("desde") or iso(ahora() - timedelta(hours=24))
    r = pedir(f"{c['url'].rstrip('/')}/estado?desde={quote(desde)}", timeout=20, max_bytes=20_000_000,
              cabeceras={"X-Token": c["token"]})
    if not r.ok:
        detalle = r.error or f"HTTP {r.estado}"
        if r.estado == 403:
            detalle = "el token no coincide con el del centinela"
        res.hallazgos.append(Hallazgo("centinela:sin-respuesta", "centinela_caido", "alta",
                                      "El ordenador de la radio no responde", detalle, {"detalle": detalle}))
        res.resumen = f"sin respuesta ({detalle})"
        return res
    paquete = json.loads(r.cuerpo)
    historial.importar(paquete, "radio", RENOMBRAR)
    st["desde"] = paquete["ahora"]

    # ¿Sigue trabajando? Su última pasada no debería tener más de 10 minutos.
    ultimas = [p["ts"] for p in paquete.get("pasadas", [])]
    if ultimas:
        hace = (datetime.fromisoformat(paquete["ahora"]) - datetime.fromisoformat(max(ultimas))).total_seconds()
        if hace > 600:
            res.hallazgos.append(Hallazgo("centinela:parado", "centinela_parado", "alta",
                                          "El centinela de la radio está parado",
                                          f"última comprobación hace {int(hace // 60)} min",
                                          {"minutos": int(hace // 60)}))
    abiertas = sum(1 for i in paquete.get("incidencias", []) if i["abierta"])
    res.resumen = f"{paquete.get('equipo', '?')} responde · {abiertas} incidencias abiertas allí"
    return res
