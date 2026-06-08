# =============================================================================
# Registra la dashboard CDG_QV come SERVIZIO che parte all'avvio del server.
# ESEGUIRE COME AMMINISTRATORE (PowerShell admin).
# Avvia waitress su 127.0.0.1:8765 (solo locale: e' IIS a esporla in rete via reverse-proxy).
# =============================================================================
$python  = "C:\Users\mago.admin\AppData\Local\Programs\Python\Python313\python.exe"
$appDir  = "C:\ApplicazioniLP\KODICEBAGNO\SORGENTI\KB_ControlloDiGestioneQV\cdg-qv"
$task    = "CDG_QV Dashboard"

# NB: gira come l'utente che POSSIEDE Python (mago.admin), non SYSTEM, altrimenti non vede
# Python in C:\Users\mago.admin\AppData. S4U = "esegui anche se l'utente non e' connesso", senza password.
$utente  = "$env:USERDOMAIN\$env:USERNAME"   # esegui lo script come admin: di solito VM2MAGO\mago.admin
$action  = New-ScheduledTaskAction -Execute $python -Argument "src\dashboard_app.py" -WorkingDirectory $appDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$princ   = New-ScheduledTaskPrincipal -UserId $utente -LogonType S4U -RunLevel Highest
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

# CDG_HOST=127.0.0.1 e' il default nel codice: il servizio resta locale, IIS fa il proxy.
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $princ -Settings $set -Force
Start-ScheduledTask -TaskName $task
Write-Host "Servizio '$task' registrato e avviato. Backend su http://127.0.0.1:8765" -ForegroundColor Green
