# Pubblicare CDG_QV dietro IIS (reverse proxy) — server VM2MAGO

Architettura: il browser dei colleghi → **IIS** (sito dedicato) → reverse-proxy → **backend Flask/waitress**
su `http://localhost:8765`. Il backend resta in **locale** (non esposto direttamente in rete): è IIS che lo
pubblica. Così cresce nel tempo (costi commerciali, trasporti, ecc.) ed è multi-utente.

## 1) Backend sempre attivo (come servizio)
Già pronto nel codice: `python src/dashboard_app.py` ora usa **waitress** (multi-utente) e onora gli header del
proxy. Per farlo partire all'avvio del server, da **PowerShell come amministratore**:

```powershell
cd C:\ApplicazioniLP\KODICEBAGNO\SORGENTI\KB_ControlloDiGestioneQV\cdg-qv
.\deploy\installa-servizio.ps1
```
Crea il task "CDG_QV Dashboard" (utente SYSTEM, avvio all'accensione, riavvio automatico). Verifica:
`Invoke-WebRequest http://localhost:8765/api/periodi -UseBasicParsing` deve rispondere 200.

## 2) Moduli IIS necessari (una tantum)
Servono **URL Rewrite** e **Application Request Routing (ARR)**. Verifica/installa:
- IIS Manager → se nel pannello del server non vedi "URL Rewrite" e "Application Request Routing Cache",
  installali (Web Platform Installer, oppure gli MSI ufficiali Microsoft "rewrite_amd64" e "ARRv3").
- Abilita il proxy ARR: IIS Manager → nodo **server** → *Application Request Routing Cache* → *Server Proxy
  Settings* → spunta **Enable proxy** → Apply.

## 3) Sito IIS dedicato (consigliato)
Mantiene l'app alla radice `/` (nessuna riscrittura degli URL interni — robusto e scalabile).

```powershell
# PowerShell admin
New-Item -ItemType Directory -Force C:\inetpub\cdgqv | Out-Null
Copy-Item .\deploy\web.config C:\inetpub\cdgqv\web.config -Force
Import-Module WebAdministration
# Sito su porta 8080 (oppure usa un host header, es. cdg.azienda.local)
New-Website -Name "CDG_QV" -PhysicalPath "C:\inetpub\cdgqv" -Port 8080
```
I colleghi aprono: **http://VM2MAGO:8080/** (o http://192.168.1.13:8080/). Apri la porta 8080 nel firewall:
```powershell
New-NetFirewallRule -DisplayName "CDG_QV (IIS 8080)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
```
> In alternativa: binding con **host header** (es. `cdg.azienda.local` sulla 80/443) se avete DNS interno + certificato.

## 4) (Alternativa) Sotto-percorso del sito Angular esistente
Se preferisci `https://tuo-sito/cdg/`: aggiungi al sito Angular una regola URL Rewrite che inoltra
`^cdg/(.*)` → `http://localhost:8765/{R:1}`. NB: in questo caso gli URL interni dell'app (`/api/...`) vanno resi
relativi al prefisso — chiedimi la variante "sotto-percorso" e adatto il frontend (oggi è tarato per sito a radice).

## Note
- Scritture (Certifica, Applica bonifica) sono attive per tutti gli utenti del sito: ambiente interno/fidato.
  Se vuoi limitarle, si può aggiungere autenticazione Windows su IIS o una modalità sola-lettura per la LAN.
- Aggiornamenti app: `git pull` + riavvio del task ("CDG_QV Dashboard"). Nessun tocco a IIS.
