#!/usr/bin/env bash
# El Vigía en Linux (centro de operaciones) y su mu-plugin de bloqueos en la web.
#
#   ./instalar-linux.sh bloqueos         sube el mu-plugin de bloqueos y comprueba la web
#   ./instalar-linux.sh quitar-bloqueos  lo quita (la web vuelve a estar como antes)
#   ./instalar-linux.sh central          arranca el centro de operaciones (y al iniciar sesión)
#   ./instalar-linux.sh parar-central    lo para y lo quita del arranque
#
# Lee la web y la carpeta de la cuenta de vigia.json, y el FTP del fichero de credenciales.
set -euo pipefail
cd "$(dirname "$0")"

SERVICIO="vigia.service"
PLUGIN="wp/aa-vigia-bloqueos.php"

[[ -f vigia.json ]] || { echo "Falta vigia.json: cp vigia.ejemplo.json vigia.json y ajústalo."; exit 1; }
ajuste() { python3 -c "import json,sys; c=json.load(open('vigia.json')); print(eval(sys.argv[1]))" "$1"; }
WEB=$(ajuste 'c["web"]["url"].rstrip("/")')
REMOTO="$(ajuste 'c["ficheros"]["raiz"]')/wp-content/mu-plugins/$(basename "$PLUGIN")"
CRED=$(ajuste 'c["credenciales"]["linux"]'); CRED="${CRED/#\~/$HOME}"

estado_http() { curl -s -o /dev/null -m 30 -w '%{http_code}' -A "Vigia/1.0 (instalación)" "$1"; }

ftp() {  # ftp put|del: FTPS con verificación del certificado (ver ftps.py)
  [[ -f "$CRED" ]] || { echo "No encuentro las credenciales: $CRED"; exit 1; }
  # shellcheck disable=SC1090
  source <(grep -E '^FTP_(HOST|TLS_NOMBRE|USER|PASS)=' "$CRED")
  local nombre="${FTP_TLS_NOMBRE:-$FTP_HOST}" ip
  ip=$(getent ahostsv4 "$FTP_HOST" | awk 'NR==1{print $1}')
  local base=(curl -sS --ssl-reqd --user "$FTP_USER:$FTP_PASS" --resolve "$nombre:21:$ip")
  case "$1" in
    put) "${base[@]}" -T "$PLUGIN" "ftp://$nombre/$REMOTO" ;;
    del) "${base[@]}" -Q "DELE $REMOTO" "ftp://$nombre/" -o /dev/null ;;
  esac
}

case "${1:-}" in
  bloqueos)
    php -l "$PLUGIN"
    grep -q "203.0.113.10" "$PLUGIN" && { echo "Antes ajusta VIGIA_IP_SERVIDOR y VIGIA_CARPETA_APACHE en $PLUGIN."; exit 1; }
    echo "Subiendo el mu-plugin de bloqueos..."
    ftp put
    sleep 2
    portada=$(estado_http "$WEB/")
    api=$(estado_http "$WEB/wp-json/vigia/v1/bloqueos")
    echo "Portada: HTTP $portada · API del Vigía sin contraseña: HTTP $api (debe ser 401)"
    if [[ "$portada" != "200" ]]; then
      echo "¡La portada no responde bien! Quito el plugin ahora mismo."
      ftp del
      echo "Quitado. Portada ahora: HTTP $(estado_http "$WEB/")"
      exit 1
    fi
    [[ "$api" == "401" ]] && echo "Listo: el plugin está activo y protegido." \
                          || echo "Aviso: la API no contesta 401. Revísalo antes de usar /bloquear."
    echo
    echo "Falta el usuario del Vigía en WordPress (una vez):"
    echo "  Usuarios → Añadir nuevo → nombre «vigia», rol «Suscriptor», contraseña larga cualquiera."
    echo "  Luego, en su perfil → Contraseñas de aplicación → nombre «Vigía» → Añadir."
    echo "  Esa contraseña va en las credenciales como VIGIA_WP_APP_PASSWORD (y VIGIA_WP_USER=vigia)."
    ;;
  quitar-bloqueos)
    ftp del
    echo "Quitado. Las IPs bloqueadas dejan de estarlo."
    ;;
  central)
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/$SERVICIO" <<EOF
[Unit]
Description=Vigía — centro de operaciones
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $PWD/vigia.py --rol central
Restart=on-failure
RestartSec=30
Nice=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICIO"
    sleep 3
    systemctl --user status "$SERVICIO" --no-pager | head -5
    echo
    echo "Panel: $PWD/datos/panel.html"
    ;;
  parar-central)
    systemctl --user disable --now "$SERVICIO" || true
    echo "Centro de operaciones parado."
    ;;
  *)
    sed -n '2,8p' "$0"; exit 1 ;;
esac
