# Junta en una carpeta con fecha lo necesario para diagnosticar un problema
# durante la prueba física, del lado de la PC de Marianela (Operativa) --
# esta PC NUNCA tiene secretos ni nicos.db (el sidecar solo corre en la Mac),
# así que no hay nada sensible que exportar por accidente.
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

# --- Configuración local de Operativa (host/puerto de la Mac, nombre del
#     dispositivo -- el token en sí NO se exporta acá porque settings-store.js
#     lo guarda cifrado con DPAPI, ilegible fuera de esta cuenta de Windows,
#     y de todas formas es mejor no copiarlo ni cifrado). ---
$settingsPath = "$env:APPDATA\nicos-desktop\nicos-settings.json"
if (Test-Path $settingsPath) {
    $raw = Get-Content $settingsPath -Raw | ConvertFrom-Json
    $safe = [ordered]@{
        role = $raw.role
        MAC_LAN_HOST = $raw.MAC_LAN_HOST
        MAC_LAN_PORT = $raw.MAC_LAN_PORT
        PAIRED_DEVICE_NAME = $raw.PAIRED_DEVICE_NAME
        PAIRED_DEVICE_ID = $raw.PAIRED_DEVICE_ID
        token_configurado = [bool]$raw.PAIRED_DEVICE_TOKEN
    }
    $safe | ConvertTo-Json | Out-File "$outDir\config_operativa.json"
} else {
    "(no se encontró $settingsPath)" | Out-File "$outDir\config_no_encontrada.txt"
}

# --- Log de npm start, si se guardó en el Desktop como sugiere la guía ---
$latestLog = Get-ChildItem "$env:USERPROFILE\Desktop\nicos-windows-*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latestLog) {
    Copy-Item $latestLog.FullName "$outDir\npm_start.log"
}

Write-Host "Listo. Carpeta: $outDir"
Get-ChildItem $outDir
