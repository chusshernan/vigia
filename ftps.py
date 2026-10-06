"""FTPS al hosting, solo lectura.

En muchos hostings compartidos el certificado del FTP está emitido para el nombre del
servidor (p. ej. srv123.hosting.net), no para el dominio. Por eso se conecta a FTP_HOST y
se verifica el certificado contra FTP_TLS_NOMBRE: la verificación TLS es completa sin
desactivarla. Si el certificado ya es del propio FTP_HOST, FTP_TLS_NOMBRE sobra.
"""
import ftplib
import io
import re
import ssl

class FTPS:
    def __init__(self, cred, timeout=60):
        # Si el hosting no da cuentas FTP de solo lectura, el FTP no debería salir del centro
        # de operaciones. El centinela lee los logs por la web (wp_api.LogsPorWeb).
        self.host = cred.get("FTP_HOST", "")
        self.nombre_tls = cred.get("FTP_TLS_NOMBRE", "") or self.host
        self.usuario, self.clave = cred.get("FTP_USER", ""), cred.get("FTP_PASS", "")
        self.timeout = timeout
        self.ftp = None

    def __enter__(self):
        if not self.host or not self.usuario or not self.clave:
            raise RuntimeError("Faltan las credenciales de FTP (FTP_HOST / FTP_USER / FTP_PASS)")
        ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=self.timeout)
        ftp.connect(self.host, 21)
        ftp.host = self.nombre_tls  # el nombre contra el que se valida el certificado
        ftp.login(self.usuario, self.clave)
        ftp.prot_p()
        self.ftp = ftp
        return self

    def __exit__(self, *exc):
        try:
            self.ftp.quit()
        except Exception:
            pass

    def tamano(self, ruta):
        self.ftp.voidcmd("TYPE I")
        return self.ftp.size(ruta)

    def leer(self, ruta, desde=0):
        """Baja el fichero (o solo lo que hay a partir del byte `desde`)."""
        buf = io.BytesIO()
        self.ftp.retrbinary(f"RETR {ruta}", buf.write, rest=desde or None)
        return buf.getvalue()

    def listar(self, ruta):
        """{nombre: {tipo, tamano, fecha}} a partir del LIST de estilo Unix."""
        lineas = []
        self.ftp.retrlines(f"LIST {ruta}", lineas.append)
        salida = {}
        for linea in lineas:
            m = re.match(r"^([dl\-])\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w{3}\s+\d+\s+[\d:]+)\s+(.+)$", linea)
            if not m:
                continue
            tipo, tamano, fecha, nombre = m.groups()
            if nombre in (".", ".."):
                continue
            nombre = nombre.split(" -> ")[0]
            salida[nombre] = {"tipo": "dir" if tipo == "d" else "fichero",
                              "tamano": int(tamano), "fecha": fecha}
        return salida
