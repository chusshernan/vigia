"""Historial en SQLite: incidencias, métricas y avisos enviados."""
import json
import sqlite3
from datetime import datetime, timedelta

from comun import SEVERIDADES, log
from soluciones import CATALOGO, explicar

ESQUEMA = """
CREATE TABLE IF NOT EXISTS incidencias (
    id INTEGER PRIMARY KEY,
    clave TEXT NOT NULL,
    area TEXT NOT NULL,
    tipo TEXT NOT NULL,
    severidad TEXT NOT NULL,
    titulo TEXT NOT NULL,
    detalle TEXT,
    datos TEXT,
    abierta INTEGER NOT NULL DEFAULT 1,
    inicio TEXT NOT NULL,
    ultima_vez TEXT NOT NULL,
    fin TEXT,
    veces INTEGER NOT NULL DEFAULT 1,
    avisada_en TEXT
);
CREATE INDEX IF NOT EXISTS inc_abiertas ON incidencias(abierta, clave);
CREATE TABLE IF NOT EXISTS metricas (ts TEXT NOT NULL, nombre TEXT NOT NULL, valor REAL);
CREATE INDEX IF NOT EXISTS met_nombre ON metricas(nombre, ts);
CREATE TABLE IF NOT EXISTS avisos (
    ts TEXT NOT NULL, canal TEXT, ok INTEGER, texto TEXT, error TEXT, incidencia INTEGER
);
CREATE TABLE IF NOT EXISTS ordenes (ts TEXT NOT NULL, quien TEXT, orden TEXT, respuesta TEXT);
CREATE TABLE IF NOT EXISTS pasadas (ts TEXT NOT NULL, area TEXT NOT NULL, revisado INTEGER, resumen TEXT);
"""


def ahora():
    return datetime.now().astimezone().replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


class Historial:
    def __init__(self, ruta):
        self.db = sqlite3.connect(ruta)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(ESQUEMA)
        # Incidencias que llegan del centinela (ordenador de la radio): origen «radio».
        columnas = {r[1] for r in self.db.execute("PRAGMA table_info(incidencias)")}
        if "origen" not in columnas:
            self.db.execute("ALTER TABLE incidencias ADD COLUMN origen TEXT NOT NULL DEFAULT 'local'")
            self.db.execute("ALTER TABLE incidencias ADD COLUMN id_remoto INTEGER")
            self.db.commit()
        if "vista" not in columnas:
            # «Visto» desde Telegram o el centro: no más recordatorios de esa incidencia.
            self.db.execute("ALTER TABLE incidencias ADD COLUMN vista TEXT")
            self.db.commit()

    # --- lectura ---------------------------------------------------------------
    def abiertas(self, area=None, solo_locales=False):
        sql = "SELECT * FROM incidencias WHERE abierta=1"
        args = ()
        if area:
            sql += " AND area=?"
            args = (area,)
        if solo_locales:
            sql += " AND origen='local'"
        return [dict(r) for r in self.db.execute(sql + " ORDER BY inicio DESC", args)]

    def cerradas(self, limite=100):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM incidencias WHERE abierta=0 ORDER BY fin DESC LIMIT ?", (limite,))]

    def metricas(self, nombre, horas=24):
        desde = iso(ahora() - timedelta(hours=horas))
        return [(r["ts"], r["valor"]) for r in self.db.execute(
            "SELECT ts, valor FROM metricas WHERE nombre=? AND ts>=? ORDER BY ts", (nombre, desde))]

    def ultimas_pasadas(self):
        return {r["area"]: dict(r) for r in self.db.execute(
            "SELECT area, MAX(ts) AS ts, revisado, resumen FROM pasadas GROUP BY area")}

    def ultimos_avisos(self, limite=30):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM avisos ORDER BY ts DESC LIMIT ?", (limite,))]

    def ultimas_ordenes(self, limite=20):
        return [dict(r) for r in self.db.execute("SELECT * FROM ordenes ORDER BY ts DESC LIMIT ?", (limite,))]

    def avisos_ultima_hora(self):
        desde = iso(ahora() - timedelta(hours=1))
        return self.db.execute("SELECT COUNT(*) FROM avisos WHERE ts>=? AND ok=1 AND canal NOT LIKE 'radio:%'", (desde,)).fetchone()[0]

    # --- escritura -------------------------------------------------------------
    def guardar_metricas(self, metricas, ts=None):
        ts = ts or iso(ahora())
        self.db.executemany("INSERT INTO metricas VALUES (?,?,?)",
                            [(ts, k, v) for k, v in metricas.items() if v is not None])

    def guardar_pasada(self, resultado, ts=None):
        self.db.execute("INSERT INTO pasadas VALUES (?,?,?,?)",
                        (ts or iso(ahora()), resultado.area, int(resultado.revisado), resultado.resumen))

    def guardar_aviso(self, canal, ok, texto, error="", incidencia=None):
        self.db.execute("INSERT INTO avisos VALUES (?,?,?,?,?,?)",
                        (iso(ahora()), canal, int(ok), texto, error, incidencia))

    def guardar_orden(self, quien, orden, respuesta):
        self.db.execute("INSERT INTO ordenes VALUES (?,?,?,?)", (iso(ahora()), quien, orden, respuesta))
        self.db.commit()

    def marcar_vista(self, inc_id):
        cur = self.db.execute("UPDATE incidencias SET vista=? WHERE id=? AND abierta=1 AND origen='local'",
                              (iso(ahora()), inc_id))
        self.db.commit()
        return cur.rowcount

    def purgar(self, dias=90):
        limite = iso(ahora() - timedelta(days=dias))
        self.db.execute("DELETE FROM metricas WHERE ts<?", (limite,))
        self.db.execute("DELETE FROM pasadas WHERE ts<?", (iso(ahora() - timedelta(days=7)),))
        self.db.execute("DELETE FROM incidencias WHERE abierta=0 AND fin<?", (limite,))
        self.db.commit()

    def commit(self):
        self.db.commit()

    # --- el motor --------------------------------------------------------------
    def procesar(self, resultado, avisador):
        """Abre, actualiza y cierra incidencias según lo que ha visto un módulo."""
        t = ahora()
        vistas = set()
        for h in resultado.hallazgos:
            vistas.add(h.clave)
            fila = self.db.execute("SELECT * FROM incidencias WHERE abierta=1 AND origen='local' AND clave=?",
                                   (h.clave,)).fetchone()
            datos = json.dumps({**h.datos, "detalle": h.detalle}, ensure_ascii=False)
            if fila:
                sev = h.severidad
                if SEVERIDADES.index(fila["severidad"]) > SEVERIDADES.index(sev):
                    sev = fila["severidad"]  # nunca baja de gravedad mientras está abierta
                self.db.execute(
                    "UPDATE incidencias SET ultima_vez=?, veces=veces+1, detalle=?, datos=?, severidad=?, titulo=? WHERE id=?",
                    (iso(t), h.detalle, datos, sev, h.titulo, fila["id"]))
                inc_id = fila["id"]
            else:
                cur = self.db.execute(
                    "INSERT INTO incidencias (clave, area, tipo, severidad, titulo, detalle, datos, inicio, ultima_vez) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (h.clave, resultado.area, h.tipo, h.severidad, h.titulo, h.detalle, datos, iso(t), iso(t)))
                inc_id = cur.lastrowid
                log.info("NUEVA [%s] %s", h.severidad, h.titulo)
            self._quizas_avisar(inc_id, avisador)

        # Cierres
        for inc in self.abiertas(resultado.area, solo_locales=True):
            cat = CATALOGO.get(inc["tipo"], {})
            modo = cat.get("modo", "estado")
            if modo == "estado":
                # Solo se cierra si el módulo ha revisado de verdad y ya no lo ve.
                if resultado.revisado and inc["clave"] not in vistas:
                    self._cerrar(inc, avisador)
            else:
                horas = cat.get("cerrar_horas", 6)
                if datetime.fromisoformat(inc["ultima_vez"]) < t - timedelta(hours=horas):
                    self._cerrar(inc, avisador, avisar=False)
        self.db.commit()

    def _quizas_avisar(self, inc_id, avisador):
        inc = dict(self.db.execute("SELECT * FROM incidencias WHERE id=?", (inc_id,)).fetchone())
        cat = CATALOGO.get(inc["tipo"], {})
        if inc["veces"] < cat.get("confirmar", 1):
            return
        if not avisador.merece_aviso(inc["severidad"]):
            return
        if inc["origen"] == "local" and not inc["avisada_en"]:
            ya = self.db.execute(
                "SELECT avisada_en FROM incidencias WHERE origen='radio' AND clave=? AND avisada_en IS NOT NULL "
                "AND (abierta=1 OR fin>=?) ORDER BY id DESC LIMIT 1",
                (inc["clave"], iso(ahora() - timedelta(hours=24)))).fetchone()
            if ya:  # el centinela ya avisó de esto mismo: aquí solo se apunta
                self.db.execute("UPDATE incidencias SET avisada_en=? WHERE id=?", (ya["avisada_en"], inc_id))
                return
        recordatorio = False
        if inc["avisada_en"] and inc.get("vista"):
            return
        if inc["avisada_en"]:
            horas = avisador.recordar_cada_horas
            if cat.get("modo") == "evento" or not horas:
                return
            if datetime.fromisoformat(inc["avisada_en"]) > ahora() - timedelta(hours=horas):
                return
            recordatorio = True
        datos = json.loads(inc["datos"] or "{}")
        que_pasa, pasos = explicar(inc["tipo"], datos)
        if avisador.avisar(inc, que_pasa, pasos, recordatorio=recordatorio):
            self.db.execute("UPDATE incidencias SET avisada_en=? WHERE id=?", (iso(ahora()), inc_id))

    def _cerrar(self, inc, avisador, avisar=True):
        self.db.execute("UPDATE incidencias SET abierta=0, fin=? WHERE id=?", (iso(ahora()), inc["id"]))
        log.info("CERRADA %s", inc["titulo"])
        if avisar and inc["avisada_en"]:
            avisador.avisar_resuelta(inc)
        elif inc["tipo"] == "equipo_sin_internet" and inc["veces"] >= 2:
            # Mientras no había internet no se pudo avisar: se cuenta ahora.
            avisador.avisar_resuelta(inc, "Mientras duró no se pudo avisar. Revisa si el directo se cortó.")

    # --- intercambio con el centinela ------------------------------------------
    def exportar(self, desde):
        """Lo que el centinela entrega al centro: todo lo ocurrido desde `desde` (ISO)."""
        q = lambda sql, *a: [dict(r) for r in self.db.execute(sql, a)]
        return {
            "incidencias": q("SELECT * FROM incidencias WHERE abierta=1 OR fin>=? OR ultima_vez>=?", desde, desde),
            "metricas": q("SELECT * FROM metricas WHERE ts>? ORDER BY ts LIMIT 20000", desde),
            "pasadas": list(self.ultimas_pasadas().values()),
            "avisos": q("SELECT * FROM avisos WHERE ts>? ORDER BY ts", desde),
            "ordenes": q("SELECT * FROM ordenes WHERE ts>? ORDER BY ts", desde),
        }

    def importar(self, paquete, origen="radio", renombrar_areas=None):
        """Copia en este historial lo que manda el centinela. No avisa de nada: eso ya lo hizo él."""
        renombrar_areas = renombrar_areas or {}
        for inc in paquete.get("incidencias", []):
            area = renombrar_areas.get(inc["area"], inc["area"])
            fila = self.db.execute("SELECT id FROM incidencias WHERE origen=? AND id_remoto=?",
                                   (origen, inc["id"])).fetchone()
            valores = (inc["clave"], area, inc["tipo"], inc["severidad"], inc["titulo"], inc["detalle"],
                       inc["datos"], inc["abierta"], inc["inicio"], inc["ultima_vez"], inc["fin"], inc["veces"],
                       inc["avisada_en"], inc.get("vista"))
            if fila:
                self.db.execute("UPDATE incidencias SET clave=?, area=?, tipo=?, severidad=?, titulo=?, detalle=?, "
                                "datos=?, abierta=?, inicio=?, ultima_vez=?, fin=?, veces=?, avisada_en=?, vista=? "
                                "WHERE id=?", (*valores, fila["id"]))
            else:
                self.db.execute("INSERT INTO incidencias (clave, area, tipo, severidad, titulo, detalle, datos, "
                                "abierta, inicio, ultima_vez, fin, veces, avisada_en, vista, origen, id_remoto) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*valores, origen, inc["id"]))
        self.db.executemany("INSERT INTO metricas VALUES (?,?,?)",
                            [(m["ts"], m["nombre"], m["valor"]) for m in paquete.get("metricas", [])])
        for p in paquete.get("pasadas", []):
            area = renombrar_areas.get(p["area"], p["area"])
            self.db.execute("INSERT INTO pasadas VALUES (?,?,?,?)", (p["ts"], area, p["revisado"], p["resumen"]))
        self.db.executemany("INSERT INTO avisos VALUES (?,?,?,?,?,?)",
                            [(a["ts"], f"{origen}:{a['canal']}", a["ok"], a["texto"], a["error"], None)
                             for a in paquete.get("avisos", [])])
        self.db.executemany("INSERT INTO ordenes VALUES (?,?,?,?)",
                            [(o["ts"], f"{origen}:{o['quien']}", o["orden"], o["respuesta"])
                             for o in paquete.get("ordenes", [])])
        self.db.commit()
