"""Pruebas sin red: detección en logs, lectura incremental con rotación, órdenes y catálogo.

    python3 -m unittest discover -s pruebas -v
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mod_logs  # noqa: E402
import ordenes  # noqa: E402
import servidor_centinela  # noqa: E402
import soluciones  # noqa: E402

CARPETA = "logs"


class FuenteFalsa:
    """Hace de FTPS / LogsPorWeb con ficheros en memoria."""

    def __init__(self, ficheros):
        self.f = ficheros

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def tamano(self, ruta):
        return len(self.f.get(ruta.rsplit("/", 1)[-1], b""))

    def listar(self, carpeta):
        return {n: {"tipo": "fichero", "tamano": len(d), "fecha": str(len(d))} for n, d in self.f.items()}

    def leer(self, ruta, desde=0):
        return self.f[ruta.rsplit("/", 1)[-1]][desde:]


def linea(ip, ruta, codigo=404, metodo="GET", hace_min=1, tam=150, ua="Mozilla/5.0"):
    ts = (datetime.now().astimezone() - timedelta(minutes=hace_min)).strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'{ip} - - [{ts}] "{metodo} {ruta} HTTP/1.1" {codigo} {tam} "-" "{ua}"'


def config():
    return {
        "web": {"dominio": "example.com"},
        "logs": {
            "carpeta": CARPETA, "acceso": "access_log", "acceso_rotado": "access_log.1",
            "errores": "error_log", "errores_rotado": "error_log.1",
            "sensor": {"acceso": "access_log", "acceso_rotado": "access_log.1",
                       "errores": "error_log", "errores_rotado": "error_log.1"},
            "ips_de_confianza": ["198.51.100.1"], "ventana_minutos": 60,
            "umbrales": {"login_por_ip": 10, "login_total": 40, "sensibles_por_ip": 3, "inyeccion_por_ip": 2,
                         "peticiones_por_ip": 600, "errores_5xx": 15, "errores_php_por_hora": 100},
        },
    }


class Deteccion(unittest.TestCase):
    def setUp(self):
        self._pais, self._fuente = mod_logs.pais_de, mod_logs.LogsPorWeb
        mod_logs.pais_de = lambda ip, estado: "XX"

    def tearDown(self):
        mod_logs.pais_de, mod_logs.LogsPorWeb = self._pais, self._fuente

    def analizar(self, lineas):
        f = {"access_log": ("\n".join(lineas) + "\n").encode(), "error_log": b""}
        mod_logs.LogsPorWeb = lambda cfg, cred, rotados: FuenteFalsa(f)
        return {h.tipo: h for h in mod_logs.comprobar(config(), {}, {}).hallazgos}

    def test_fuerza_bruta(self):
        h = self.analizar([linea("203.0.113.5", "/wp-login.php", 200, "POST") for _ in range(12)])
        self.assertIn("ataque_fuerza_bruta", h)
        self.assertEqual(h["ataque_fuerza_bruta"].datos["ip"], "203.0.113.5")

    def test_webshell_ejecutado(self):
        h = self.analizar([linea("203.0.113.6", "/wp-content/uploads/2026/10/x.php?c=id", 200)])
        self.assertEqual(h["ataque_webshell"].severidad, "critica")

    def test_escaner_y_fuga(self):
        h = self.analizar([linea("203.0.113.7", r) for r in ("/.git/config", "/phpmyadmin/", "/backup.zip")]
                          + [linea("203.0.113.7", "/.env", 200, tam=900)])
        self.assertIn("ataque_escaneo", h)
        self.assertIn("ataque_expuesto", h)

    def test_inyeccion_sql(self):
        h = self.analizar([linea("203.0.113.8", "/?id=1%20UNION%20SELECT%20password"),
                           linea("203.0.113.8", "/?q=1'%20or%20'1'='1")])
        self.assertIn("ataque_inyeccion", h)

    def test_ip_de_confianza_no_alerta(self):
        h = self.analizar([linea("198.51.100.1", "/wp-login.php", 200, "POST") for _ in range(30)])
        self.assertNotIn("ataque_fuerza_bruta", h)

    def test_trafico_normal_sin_alertas(self):
        h = self.analizar([linea("203.0.113.9", "/", 200), linea("203.0.113.9", "/noticias/", 200)])
        self.assertEqual(h, {})


class LecturaIncremental(unittest.TestCase):
    def test_solo_lo_nuevo_y_rotacion(self):
        a, b, c = (linea("203.0.113.1", f"/{n}", 200, hace_min=m) for n, m in (("a", 30), ("b", 20), ("c", 10)))
        f = {"access_log": f"{a}\n{b}\n".encode()}
        st = {}
        primera = mod_logs.leer_nuevo(FuenteFalsa(f), CARPETA, "access_log", "access_log.1", st,
                                      mod_logs._extraer_ts_acceso)
        self.assertEqual(len(primera), 2)
        # El servidor rota: lo de antes pasa a .1 (con una línea más) y el log vuelve a empezar.
        f.update({"access_log.1": f"{a}\n{b}\n{c}\n".encode(), "access_log": b""})
        segunda = mod_logs.leer_nuevo(FuenteFalsa(f), CARPETA, "access_log", "access_log.1", st,
                                      mod_logs._extraer_ts_acceso)
        self.assertEqual([l for _, l in segunda], [c])


class Ordenes(unittest.TestCase):
    def test_no_se_bloquean_ips_privadas(self):
        for ip in ("192.168.1.10", "10.0.0.1", "127.0.0.1", "no-es-una-ip"):
            self.assertIsNone(ordenes._ip_valida(ip), ip)
        self.assertEqual(ordenes._ip_valida("8.8.8.8"), "8.8.8.8")

    def test_orden_desconocida(self):
        o = ordenes.Ordenes(vigia=None)
        self.assertIn("No conozco", o.ejecutar("/rm -rf /"))

    def test_centinela_sin_token_no_abre_puerto(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = {"roles": {"centinela": {"puerto": 0}}, "centinela": {"token": ""}, "_datos": Path(d)}
            self.assertIsNone(servidor_centinela.arrancar(cfg))


class Catalogo(unittest.TestCase):
    def test_cada_entrada_tiene_modo_y_solucion(self):
        for tipo, e in soluciones.CATALOGO.items():
            self.assertIn(e["modo"], ("estado", "evento"), tipo)
            self.assertTrue(e["solucion"], tipo)

    def test_variables_que_faltan_no_rompen(self):
        que, pasos = soluciones.explicar("ataque_fuerza_bruta", {"ip": "203.0.113.5"})
        self.assertIn("203.0.113.5", que)
        self.assertIn("?", que)  # {pais} y {n} sin datos


if __name__ == "__main__":
    unittest.main()
