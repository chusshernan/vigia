# Instalador del CENTINELA del Vigía en el ordenador de la radio (Windows).
# Se lanza con doble clic en instalar.bat (que llama a este fichero).
#
# Qué hace:
#   1. Comprueba que hay Python (si no, lo instala con winget).
#   2. Copia el Vigía a C:\Vigia (los datos y el historial se conservan).
#   3. Crea el fichero de credenciales en %APPDATA%\Vigia si no existe y lo abre.
#   4. Crea una tarea programada que arranca el centinela al iniciar sesión, oculto.
#   5. Abre el puerto 8790 SOLO para la red de casa (para que el centro de operaciones,
#      en Linux, recoja los datos y mande órdenes). Pide permiso de administrador.
#   6. Pone en el escritorio un acceso directo al panel.
# No toca ZaraRadio ni nada más del equipo.

$ErrorActionPreference = "Stop"
$Origen  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Destino = "C:\Vigia"
$Creds   = Join-Path $env:APPDATA "Vigia\credenciales.env"

function Paso($t) { Write-Host "`n== $t" -ForegroundColor Green }

Paso "Python"
$py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "No hay Python. Lo instalo con winget (tarda un par de minutos)..."
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    $py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $py) { throw "Python instalado, pero hay que cerrar esta ventana y volver a lanzar instalar.bat." }
}
Write-Host "OK: $($py.Source)"

Paso "ffmpeg (para detectar silencio en el directo; opcional)"
if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue)) {
    try {
        winget install -e --id Gyan.FFmpeg --scope user --accept-package-agreements --accept-source-agreements
        Write-Host "OK (si el panel dice 'falta ffmpeg', reinicia el equipo una vez)."
    } catch { Write-Host "No se pudo instalar; el Vigía funcionará sin la comprobación de silencio." }
} else { Write-Host "OK" }

Paso "Copiando el Vigía a $Destino"
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
Get-ChildItem $Origen -File -Filter *.py | Copy-Item -Destination $Destino -Force
if (-not (Test-Path "$Origen\vigia.json")) { throw "Falta vigia.json: copia vigia.ejemplo.json a vigia.json y ajústalo antes de instalar." }
if (-not (Test-Path "$Destino\vigia.json")) {
    Copy-Item "$Origen\vigia.json" $Destino
} else {
    Write-Host "Ya había un vigia.json: lo dejo como está (la versión nueva queda en vigia.json.nuevo)."
    Copy-Item "$Origen\vigia.json" "$Destino\vigia.json.nuevo" -Force
}
Write-Host "OK"

Paso "Credenciales"
if (-not (Test-Path $Creds)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Creds) | Out-Null
    Copy-Item "$Origen\windows\credenciales.ejemplo.env" $Creds
    Write-Host "Creado $Creds. Se abre ahora: rellénalo, guarda y cierra el Bloc de notas."
    Start-Process notepad.exe $Creds -Wait
} else { Write-Host "Ya existen: $Creds" }
# Solo este usuario puede leerlas.
icacls (Split-Path $Creds) /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null

Paso "Tarea programada (arranca sola al iniciar sesión)"
$accion  = New-ScheduledTaskAction -Execute $py.Source -Argument "`"$Destino\vigia.py`" --rol centinela" -WorkingDirectory $Destino
$inicio  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
           -Priority 7
Register-ScheduledTask -TaskName "Vigia" -Action $accion -Trigger $inicio -Settings $ajustes `
    -Description "Vigila la web, el directo y ZaraRadio. Avisa por WhatsApp/Telegram." -Force | Out-Null
Write-Host "OK (prioridad baja, para no quitarle recursos a ZaraRadio)"

Paso "Puerto para el centro de operaciones (pide permiso de administrador)"
$regla = 'New-NetFirewallRule -DisplayName "Vigia (centro de operaciones)" -Direction Inbound -Protocol TCP -LocalPort 8790 -Action Allow -Profile Private -RemoteAddress LocalSubnet -ErrorAction SilentlyContinue'
try {
    $codificada = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($regla))
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -EncodedCommand $codificada"
    Write-Host "OK (solo red privada y solo equipos de la misma red)"
} catch { Write-Host "No se abrió el puerto: el centro no podrá recoger datos, pero los avisos al móvil funcionan igual." }

Paso "Acceso directo al panel en el escritorio"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Vigía.lnk"))
$lnk.TargetPath = "$Destino\datos\panel.html"
$lnk.Save()
Write-Host "OK"

Paso "Prueba de avisos"
& $py.Source.Replace("pythonw.exe", "python.exe") "$Destino\vigia.py" --probar-avisos

Paso "Arrancando el Vigía"
Stop-ScheduledTask -TaskName "Vigia" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "Vigia"
Start-Sleep -Seconds 90
if (Test-Path "$Destino\datos\panel.html") { Start-Process "$Destino\datos\panel.html" }
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in 'Dhcp','Manual' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
Write-Host "`nListo. El centinela queda funcionando en segundo plano." -ForegroundColor Green
Write-Host "`nPARA EL CENTRO DE OPERACIONES (Linux): la dirección de este ordenador es" -ForegroundColor Yellow
Write-Host "    http://$($ip):8790" -ForegroundColor Yellow
Write-Host "Ponla en vigia.json → centinela → url, en el centro de operaciones."
Write-Host "Conviene fijar esa IP en el router (reserva DHCP) para que no cambie."
Write-Host "Recuerda: en vigia.json, 'activos' debe estar en true para que los avisos salgan de verdad."
Read-Host "Pulsa Intro para cerrar"
