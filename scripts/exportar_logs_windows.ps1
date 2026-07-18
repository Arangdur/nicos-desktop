# Junta en una carpeta con fecha lo necesario para diagnosticar un problema
# durante la prueba física, del lado de la PC de Marianela (Operativa).
#
# Deliberadamente NUNCA copia (ni cifrados): el token del dispositivo, códigos
# de pairing (esos ni siquiera existen de este lado -- viven en nicos.db, en
# la Mac), ni el contenido del outbox local (nicos-outbox.json tiene raw_text
# de tareas en cola -- puede tener datos operativos). Para diagnóstico alcanza
# con indicar qué campos EXISTEN, su estado, y versiones -- no los valores.
#
# Uso (PowerShell, parado en la carpeta "NicOS Desktop"):
#   powershell -ExecutionPolicy Bypass -File scripts\exportar_logs_windows.ps1

$outDir = "$env:USERPROFILE\Desktop\nicos-logs-windows-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Write-Host "Exportando a: $outDir"

# --- Tailscale ---
$tailscaleOut = @()
$tailscaleOut += "=== tailscale status ==="
try { $tailscaleOut += (tailscale status 2>&1 | Out-String) } catch { $tailscaleOut += "(tailscale no disponible o no logueado)" }
$tailscaleOut += "=== tailscale ip -4 ==="
try { $tailscaleOut += (tailscale ip -4 2>&1 | Out-String) } catch { $tailscaleOut += "(sin IP asignada)" }
$tailscaleOut | Out-File "$outDir\tailscale.txt"

# --- Versión de la app ---
$pkgPath = Join-Path (Split-Path $PSScriptRoot -Parent) "package.json"
if (Test-Path $pkgPath) {
    $pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
    "version: $($pkg.version)" | Out-File "$outDir\version.txt"
}

# --- Configuración local de Operativa: solo QUÉ CAMPOS EXISTEN y su estado,
#     nunca valores sensibles. El token NO se exporta ni cifrado (mejor no
#     copiarlo de ningún modo, aunque settings-store.js ya lo cifra con
#     DPAPI). ---
$settingsPath = "$env:APPDATA\nicos-desktop\nicos-settings.json"
if (Test-Path $settingsPath) {
    $raw = Get-Content $settingsPath -Raw | ConvertFrom-Json
    $safe = [ordered]@{
        role = $raw.role
        MAC_LAN_HOST_configurado = [bool]$raw.MAC_LAN_HOST
        MAC_LAN_PORT = $raw.MAC_LAN_PORT   # el puerto no es sensible, ayuda a diagnosticar
        PAIRED_DEVICE_NAME = $raw.PAIRED_DEVICE_NAME  # nombre del dispositivo, no un secreto
        PAIRED_DEVICE_ID_configurado = [bool]$raw.PAIRED_DEVICE_ID
        token_configurado = [bool]$raw.PAIRED_DEVICE_TOKEN
    }
    $safe | ConvertTo-Json | Out-File "$outDir\config_operativa.json"
} else {
    "(no se encontró $settingsPath)" | Out-File "$outDir\config_no_encontrada.txt"
}

# --- Outbox: solo el CONTEO de tareas pendientes, nunca su contenido
#     (nicos-outbox.json tiene raw_text real de lo que se escribió). ---
$outboxPath = "$env:APPDATA\nicos-desktop\nicos-outbox.json"
if (Test-Path $outboxPath) {
    try {
        $outboxItems = Get-Content $outboxPath -Raw | ConvertFrom-Json
        "tareas en outbox: $($outboxItems.Count)" | Out-File "$outDir\outbox_estado.txt"
    } catch {
        "(no se pudo leer el outbox -- posible JSON corrupto)" | Out-File "$outDir\outbox_estado.txt"
    }
} else {
    "tareas en outbox: 0 (archivo no existe)" | Out-File "$outDir\outbox_estado.txt"
}

# --- Log de npm start, si se guardó en el Desktop como sugiere la guía ---
$latestLog = Get-ChildItem "$env:USERPROFILE\Desktop\nicos-windows-*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latestLog) {
    Copy-Item $latestLog.FullName "$outDir\npm_start.log"
}

Write-Host "Listo. Carpeta: $outDir"
Get-ChildItem $outDir
