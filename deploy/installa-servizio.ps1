# =============================================================================
# Registra/aggiorna la dashboard CDG_QV come task che parte all'avvio del server e
# resta attivo, in ascolto su 0.0.0.0:8765 (raggiungibile in VPN; firewall lo restringe).
# ESEGUIRE COME AMMINISTRATORE (PowerShell "Esegui come amministratore").
# Idempotente: si puo' rilanciare per aggiornare/riavviare pulito.
# =============================================================================
$python = "C:\Users\mago.admin\AppData\Local\Programs\Python\Python313\python.exe"
$appDir = "C:\ApplicazioniLP\KODICEBAGNO\SORGENTI\KB_ControlloDiGestioneQV\cdg-qv"
$task   = "CDG_QV Dashboard"
$utente = "$env:USERDOMAIN\$env:USERNAME"   # eseguito come admin = di solito VM2MAGO\mago.admin (proprietario di Python)

# 1) Ferma il task e UCCIDI in modo forzato l'eventuale processo che tiene la 8765 (anche orfani).
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
$p = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue).OwningProcess
if ($p) { Start-Process taskkill -ArgumentList "/F /PID $p" -Wait -NoNewWindow; Write-Host "Terminato processo $p sulla 8765" }
Start-Sleep -Seconds 1

# 2) CDG_HOST a livello MACCHINA: il backend ascolta sulla VPN. Niente wrapper cmd (evita processi orfani):
#    il task esegue python DIRETTAMENTE, cosi' lo stop del task lo termina pulito.
[Environment]::SetEnvironmentVariable("CDG_HOST", "0.0.0.0", "Machine")
$env:CDG_HOST = "0.0.0.0"

$action  = New-ScheduledTaskAction -Execute $python -Argument "src\dashboard_app.py" -WorkingDirectory $appDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$princ   = New-ScheduledTaskPrincipal -UserId $utente -LogonType S4U -RunLevel Highest
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $princ -Settings $set -Force | Out-Null

# 3) Avvia e verifica
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 4
try {
    $html = (New-Object System.Net.WebClient).DownloadString("http://127.0.0.1:8765/")
    if ($html.Contains("const API = location.pathname")) { Write-Host "OK: backend attivo su 8765 col codice aggiornato." -ForegroundColor Green }
    else { Write-Host "Backend attivo ma codice NON aggiornato: rilancia questo script." -ForegroundColor Yellow }
} catch { Write-Host "Backend non risponde ancora: $($_.Exception.Message)" -ForegroundColor Red }
