"""El ordenador de la radio: ZaraRadio (y lo que se añada) abierto y respondiendo, y disco.

Solo tiene sentido en Windows. En otro sistema no revisa nada.
"""
import csv
import io
import shutil
import string
import subprocess

from comun import ES_WINDOWS, Hallazgo, Resultado


def _tasklist(*filtro):
    salida = subprocess.run(["tasklist", "/fo", "csv", "/nh", *filtro], capture_output=True,
                            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    texto = salida.stdout.decode("cp850", "replace")
    return {fila[0].lower() for fila in csv.reader(io.StringIO(texto)) if fila}


def comprobar(cfg, estado):
    res = Resultado("equipo")
    if not ES_WINDOWS:
        res.revisado = False
        res.resumen = "solo en el ordenador de la radio (Windows)"
        return res
    e = cfg["equipo"]
    abiertos = _tasklist()
    colgados = _tasklist("/fi", "STATUS eq NOT RESPONDING")
    estados = []
    for p in e.get("procesos", []):
        exe, nombre = p["proceso"].lower(), p["nombre"]
        if exe not in abiertos:
            res.hallazgos.append(Hallazgo(f"equipo:parado:{exe}", "equipo_proceso_parado", "critica",
                                          f"{nombre} está cerrado", "", {"nombre": nombre}))
            estados.append(f"{nombre} cerrado")
        elif exe in colgados:
            res.hallazgos.append(Hallazgo(f"equipo:colgado:{exe}", "equipo_proceso_colgado", "alta",
                                          f"{nombre} no responde", "", {"nombre": nombre}))
            estados.append(f"{nombre} colgado")
        else:
            estados.append(f"{nombre} OK")
    for letra in string.ascii_uppercase:
        unidad = f"{letra}:\\"
        try:
            uso = shutil.disk_usage(unidad)
        except OSError:
            continue
        libre = uso.free / 1e9
        res.metricas[f"disco_{letra}_gb"] = round(libre, 1)
        if libre < e.get("disco_minimo_gb", 5) and uso.total > 20e9:
            res.hallazgos.append(Hallazgo(f"equipo:disco:{letra}", "equipo_disco", "alta",
                                          f"Disco {letra}: casi lleno", f"{libre:.1f} GB libres",
                                          {"unidad": f"{letra}:", "libre": f"{libre:.1f}"}))
    res.resumen = ", ".join(estados) or "sin procesos configurados"
    return res
