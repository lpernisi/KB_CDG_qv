# CDG_QV — Datawarehouse di Controllo di Gestione

Datawarehouse semplice su SQL Server che estrae i dati da **Mago 4**, li pulisce e
calcola il conto economico di riga, per poi alimentare **QlikSense** e altri strumenti.
Qlik fa solo da visualizzatore: la logica di calcolo vive qui.

Questo primo scaffold copre **estrazione + calcolo** del costo del venduto (WAP)
fino al **Margine di Contribuzione I**. La riconciliazione contro la vecchia tabella
e gli altri componenti di costo arrivano nelle fasi successive.

## Struttura

```
sql/ddl/      creazione di CDG_QV: database, schemi (src/core/cfg/pres), tabelle
sql/extract/  procedure di estrazione da Mago (cross-DB, stessa istanza)
sql/build/    calcolo (core.fatto_riga) e vista di presentazione per Qlik
src/          orchestratore Python + verifica "a secco" della formula
run_pipeline.py   esegue tutta la pipeline per il periodo in config
```

Strati del datawarehouse: `src` (landing da Mago) → `core` (fatto_riga, il "contratto")
→ `pres` (viste per i consumatori). `cfg` tiene lo stato di avanzamento.

## Avvio

1. `pip install -r requirements.txt`
2. Copia `.env.example` in `.env` e (se non usi l'autenticazione Windows) metti utente/password.
3. In `config/settings.yaml` imposta `server`, il `driver` ODBC e conferma i nomi dei database.
4. **Prima di lanciare sul DB**: in `sql/extract/10_usp_load_da_mago.sql` adatta i nomi di
   tabelle/colonne sorgente marcati con `-- ADATTA` allo schema reale di `KODICEBAGNO_4`.
5. `python run_pipeline.py`

Per provare solo la formula di calcolo senza database: `python src/demo_calcolo.py`.

## Consumo da Qlik (e altri strumenti)

Leggi sempre dalla vista `pres.conto_economico_riga`, mai dalle tabelle interne:
così, se cambiamo la struttura di `core`, i consumatori non si rompono.
