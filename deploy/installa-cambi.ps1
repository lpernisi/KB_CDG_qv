# =============================================================================
# Registra un task SETTIMANALE che aggiorna i cambi valuta dalla BCE
# (src/aggiorna_cambi.py -> kodice.cambio_valuta). I cambi BCE bastano settimanali.
# ESEGUIRE COME AMMINISTRATORE. Idempotente: si puo' rilanciare.
# =============================================================================
$python = "C:\Users\mago.admin\AppData\Local\Programs\Python\Python313\python.exe"
$appDir = "C:\ApplicazioniLP\KODICEBAGNO\SORGENTI\KB_ControlloDiGestioneQV\cdg-qv"
$task   = "CDG_QV Cambi BCE"
$utente = "$env:USERDOMAIN\$env:USERNAME"

$action  = New-ScheduledTaskAction -Execute $python -Argument "src\aggiorna_cambi.py" -WorkingDirectory $appDir
# Ogni lunedi' alle 06:00. Per mensile: -Weekly -WeeksInterval 4, o un trigger -Daily ridotto.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 6am
$princ   = New-ScheduledTaskPrincipal -UserId $utente -LogonType S4U -RunLevel Highest
$set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $princ -Settings $set -Force | Out-Null
Write-Host "Task '$task' registrato (lunedi 06:00)." -ForegroundColor Green

# Esecuzione subito, per popolare/aggiornare ora
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 6
Write-Host "Lanciato un primo aggiornamento. Verifica: SELECT * FROM kodice.vw_cambio_corrente;"
