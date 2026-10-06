"""Panel HTML del Vigía: estado, incidencias abiertas con su solución, gráficas e historial.

Es una página estática que se regenera en cada pasada y se recarga sola cada minuto.
"""
import json
import shutil
from datetime import datetime
from html import escape

from soluciones import explicar

AREAS = [
    ("web", "Web", "la portada"),
    ("stream", "Directo", "el stream"),
    ("silencio", "Sonido", "que no haya silencio"),
    ("logs", "Ataques y errores", "logs del servidor"),
    ("ficheros", "Ficheros", "cambios en el servidor"),
    ("equipo", "Equipo de la radio", "programas y disco"),
    ("conexion_radio", "Internet del estudio", "según el centinela"),
    ("conexion", "Internet de este equipo", "salida a internet"),
    ("centinela", "Centinela de la radio", "que responda y trabaje"),
]
ORDEN = {"critica": 4, "alta": 3, "media": 2, "baja": 1, "info": 0}
TXT_SEV = {"critica": "Crítico", "alta": "Alto", "media": "Medio", "baja": "Bajo", "info": "Info"}

CSS = """
:root{--bg:#f5f6f4;--card:#fff;--txt:#1b1f1a;--sub:#5d665b;--linea:#dfe3dc;--ok:#237a05;--okbg:#e6f4df;
--crit:#b3261e;--critbg:#fbe4e2;--alta:#b35c00;--altabg:#fdebd6;--media:#8a6d00;--mediabg:#fbf3cf;
--baja:#2f5f9e;--bajabg:#e1ebf8;--gris:#6b7280;--grisbg:#eceef0;--acento:#509933}
@media (prefers-color-scheme:dark){:root{--bg:#111411;--card:#1a1e19;--txt:#e8ece6;--sub:#9aa597;--linea:#2c332a;
--ok:#7bd957;--okbg:#1d3315;--crit:#ff8a80;--critbg:#3d1714;--alta:#ffb366;--altabg:#3a2410;--media:#f0cf4a;
--mediabg:#332b0c;--baja:#8ab4f8;--bajabg:#152338;--gris:#9ca3af;--grisbg:#23272b;--acento:#7bd957}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;justify-content:space-between;margin-bottom:18px}
h1{font-size:22px;margin:0}h1 span{color:var(--acento)}h2{font-size:17px;margin:28px 0 10px}
.sub{color:var(--sub);font-size:13px}
.global{padding:6px 14px;border-radius:999px;font-weight:600}
.areas{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.area{background:var(--card);border:1px solid var(--linea);border-left:5px solid var(--gris);border-radius:10px;padding:12px 14px}
.area b{display:block}.area .est{font-size:13px;font-weight:600}.area .res{font-size:13px;color:var(--sub);margin-top:4px;word-break:break-word}
.s-ok{border-left-color:var(--ok)}.s-ok .est{color:var(--ok)}
.s-critica{border-left-color:var(--crit)}.s-critica .est{color:var(--crit)}
.s-alta{border-left-color:var(--alta)}.s-alta .est{color:var(--alta)}
.s-media{border-left-color:var(--media)}.s-media .est{color:var(--media)}
.s-baja{border-left-color:var(--baja)}.s-baja .est{color:var(--baja)}
.chip{display:inline-block;font-size:12px;font-weight:700;padding:1px 9px;border-radius:999px;white-space:nowrap}
.c-critica{background:var(--critbg);color:var(--crit)}.c-alta{background:var(--altabg);color:var(--alta)}
.c-media{background:var(--mediabg);color:var(--media)}.c-baja,.c-info{background:var(--bajabg);color:var(--baja)}
.c-ok{background:var(--okbg);color:var(--ok)}.c-gris{background:var(--grisbg);color:var(--gris)}
.inc{background:var(--card);border:1px solid var(--linea);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.inc h3{font-size:16px;margin:6px 0 4px}.inc ol{margin:6px 0 0;padding-left:22px}.inc li{margin:4px 0;white-space:pre-wrap}
.inc details{margin-top:8px;font-size:13px;color:var(--sub)}.inc pre{white-space:pre-wrap;word-break:break-all;font-size:12px}
.vacio{background:var(--card);border:1px dashed var(--linea);border-radius:10px;padding:16px;color:var(--sub)}
.graficas{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.graf{background:var(--card);border:1px solid var(--linea);border-radius:10px;padding:10px 12px}
.graf .v{font-size:20px;font-weight:700}.graf svg{width:100%;height:44px;display:block;margin-top:4px}
.tabla{overflow-x:auto;background:var(--card);border:1px solid var(--linea);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--linea);vertical-align:top}
th{color:var(--sub);font-weight:600}tr:last-child td{border-bottom:0}
"""


def _fecha(iso, con_dia=True):
    if not iso:
        return "—"
    d = datetime.fromisoformat(iso)
    return d.strftime("%d/%m %H:%M") if con_dia else d.strftime("%H:%M")


def _duracion(a, b):
    s = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    m = int(s // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min" if m < 2880 else f"{m // 1440} días"


def _sparkline(puntos):
    if len(puntos) < 2:
        return '<svg viewBox="0 0 100 30"></svg>'
    vals = [v for _, v in puntos]
    lo, hi = min(vals), max(vals)
    rango = (hi - lo) or 1
    n = len(vals) - 1
    xy = " ".join(f"{i * 100 / n:.1f},{28 - (v - lo) / rango * 26:.1f}" for i, v in enumerate(vals))
    return (f'<svg viewBox="0 0 100 30" preserveAspectRatio="none"><polyline points="{xy}" fill="none" '
            f'stroke="var(--acento)" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>')


def generar(cfg, historial, ruta):
    abiertas = historial.abiertas()
    pasadas = historial.ultimas_pasadas()
    peor_por_area = {}
    for inc in abiertas:
        a = inc["area"]
        if ORDEN[inc["severidad"]] > ORDEN.get(peor_por_area.get(a), -1):
            peor_por_area[a] = inc["severidad"]

    peor = max((ORDEN[i["severidad"]] for i in abiertas if i["severidad"] != "baja"), default=0)
    if peor >= 4:
        glob = ('c-critica', "Hay un problema grave")
    elif peor >= 2:
        glob = ('c-alta', f"{sum(1 for i in abiertas if i['severidad'] != 'baja')} incidencias abiertas")
    else:
        glob = ('c-ok', "Todo funciona")

    h = [f"<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' "
         f"content='width=device-width,initial-scale=1'><meta http-equiv='refresh' content='60'>"
         f"<title>Vigía · {escape(cfg.get('nombre', ''))}</title><style>{CSS}</style></head><body><main>",
         f"<header><div><h1>Vigía · <span>{escape(cfg.get('nombre', ''))}</span></h1>"
         f"<div class='sub'>Actualizado {datetime.now():%d/%m/%Y %H:%M:%S} · se recarga cada minuto</div></div>"
         f"<div class='global chip {glob[0]}'>{glob[1]}</div></header>"]

    # Áreas
    h.append("<div class='areas'>")
    for clave, nombre, desc in AREAS:
        p = pasadas.get(clave)
        sev = peor_por_area.get(clave)
        if sev:
            clase, est = f"s-{sev}", "Aviso" if sev == "baja" else TXT_SEV[sev]
        elif p and p["revisado"]:
            clase, est = "s-ok", "Bien"
        elif p:
            clase, est = "", "Sin revisar"
        else:
            clase, est = "", "Aún no revisado"
        resumen = escape(p["resumen"]) if p and p["resumen"] else escape(desc)
        cuando = f" · {_fecha(p['ts'], False)}" if p else ""
        h.append(f"<div class='area {clase}'><b>{nombre}</b><span class='est'>{est}</span>"
                 f"<span class='sub'>{cuando}</span><div class='res'>{resumen}</div></div>")
    h.append("</div>")

    # Incidencias abiertas
    h.append("<h2>Incidencias abiertas</h2>")
    if not abiertas:
        h.append("<div class='vacio'>No hay ninguna incidencia abierta.</div>")
    for inc in sorted(abiertas, key=lambda i: (-ORDEN[i["severidad"]], i["inicio"])):
        datos = json.loads(inc["datos"] or "{}")
        que, pasos = explicar(inc["tipo"], datos)
        avisada = f"avisada {_fecha(inc['avisada_en'])}" if inc["avisada_en"] else "sin avisar"
        num = inc["id_remoto"] if inc.get("origen") == "radio" else inc["id"]
        visto = " · 👁 vista" if inc.get("vista") else ""
        h.append(f"<div class='inc'><span class='chip c-{inc['severidad']}'>{TXT_SEV[inc['severidad']]}</span> "
                 f"<span class='sub'>#{num}{visto} · </span>"
                 f"<span class='sub'>desde {_fecha(inc['inicio'])} · última {_fecha(inc['ultima_vez'], False)} · "
                 f"vista {inc['veces']} veces · {avisada}"
                 f"{' · <b>en la radio</b>' if inc.get('origen') == 'radio' else ''}</span>"
                 f"<h3>{escape(inc['titulo'])}</h3><div>{escape(que)}</div>")
        if pasos:
            h.append("<ol>" + "".join(f"<li>{escape(p)}</li>" for p in pasos) + "</ol>")
        if inc["detalle"]:
            h.append(f"<details><summary>Detalle técnico</summary><pre>{escape(inc['detalle'])}</pre></details>")
        h.append("</div>")

    # Gráficas
    graficas = [("web_segundos", "Carga de la web", " s"), ("oyentes", "Oyentes del directo", ""),
                ("stream_kbps", "Calidad del directo", " kbps"), ("peticiones_hora", "Visitas a la web / hora", ""),
                ("login_hora", "Intentos de login / hora", ""), ("errores_servidor_hora", "Errores del servidor / hora", "")]
    h.append("<h2>Últimas 24 horas</h2><div class='graficas'>")
    for nombre, titulo, unidad in graficas:
        pts = historial.metricas(nombre, 24)
        actual = f"{pts[-1][1]:g}{unidad}" if pts else "—"
        maximo = f" · máx. {max(v for _, v in pts):g}{unidad}" if pts else ""
        h.append(f"<div class='graf'><div class='sub'>{titulo}{maximo}</div><div class='v'>{actual}</div>"
                 f"{_sparkline(pts)}</div>")
    h.append("</div>")

    # Historial
    h.append("<h2>Historial</h2>")
    cerradas = historial.cerradas(60)
    if not cerradas:
        h.append("<div class='vacio'>Todavía no hay incidencias cerradas.</div>")
    else:
        h.append("<div class='tabla'><table><tr><th>Empezó</th><th>Duró</th><th>Nivel</th><th>Qué pasó</th></tr>")
        for inc in cerradas:
            h.append(f"<tr><td>{_fecha(inc['inicio'])}</td><td>{_duracion(inc['inicio'], inc['fin'])}</td>"
                     f"<td><span class='chip c-{inc['severidad']}'>{TXT_SEV[inc['severidad']]}</span></td>"
                     f"<td>{escape(inc['titulo'])}</td></tr>")
        h.append("</table></div>")

    # Avisos
    avisos = historial.ultimos_avisos(20)
    h.append("<h2>Avisos enviados</h2>")
    if not avisos:
        h.append("<div class='vacio'>Aún no se ha enviado ningún aviso.</div>")
    else:
        h.append("<div class='tabla'><table><tr><th>Cuándo</th><th>Canal</th><th>Resultado</th><th>Mensaje</th></tr>")
        for a in avisos:
            res = ("<span class='chip c-ok'>enviado</span>" if a["ok"] and a["canal"] != "simulado"
                   else "<span class='chip c-gris'>simulado</span>" if a["canal"] == "simulado"
                   else f"<span class='chip c-critica'>falló</span> {escape(a['error'] or '')}")
            primera = escape((a["texto"] or "").replace("*", "").split("\n\n")[1:2][0][:140]
                             if "\n\n" in (a["texto"] or "") else (a["texto"] or "")[:140])
            h.append(f"<tr><td>{_fecha(a['ts'])}</td><td>{escape(a['canal'] or '')}</td><td>{res}</td>"
                     f"<td>{primera}</td></tr>")
        h.append("</table></div>")

    # Órdenes dadas (Telegram o desde aquí)
    ordenes = historial.ultimas_ordenes(20)
    if ordenes:
        h.append("<h2>Órdenes dadas</h2><div class='tabla'><table><tr><th>Cuándo</th><th>Desde</th>"
                 "<th>Orden</th><th>Resultado</th></tr>")
        for o in ordenes:
            h.append(f"<tr><td>{_fecha(o['ts'])}</td><td>{escape(o['quien'] or '')}</td>"
                     f"<td><code>{escape(o['orden'] or '')}</code></td><td>{escape((o['respuesta'] or '')[:160])}</td></tr>")
        h.append("</table></div>")

    h.append("</main></body></html>")
    ruta.write_text("".join(h), encoding="utf-8")
    copia = cfg.get("copiar_panel_a")
    if copia:
        try:
            shutil.copyfile(ruta, copia)
        except OSError:
            pass
