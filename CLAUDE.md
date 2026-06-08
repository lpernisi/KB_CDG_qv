# CDG_QV — Contesto per Claude Code

> Letto automaticamente da Claude Code. È la "memoria" condivisa del progetto:
> tienilo aggiornato, perché è ciò che mi (ci) tiene allineati al disegno reale.

## Obiettivo

Costruire **CDG_QV**, un datawarehouse semplice su SQL Server in cui far atterrare
i dati estratti dal gestionale **Mago 4**, ripuliti, calcolati e verificati. Da
CDG_QV un estrattore alimenta l'app **QlikSense** (e altri strumenti): Qlik fa solo
da visualizzatore, la logica di calcolo del CDG vive qui, nel datawarehouse.

Strategia di lavoro: prima **estrazione + calcolo**, poi **validazione** (riconciliazione
contro la vecchia tabella custom usata come oracolo), infine esposizione su Qlik.
Questo primo scaffold copre estrazione + calcolo del **costo del venduto (WAP)** fino
al **Margine di Contribuzione I**.

## Topologia (tutto sulla STESSA istanza SQL Server)

- `KODICEBAGNO_4` (Mago 4) — sorgente principale. Tabelle `MA_*` (es. `MA_ItemsWAP`).
- `trasporti`     — fatture vettori (`KB_FattureVettori...`), per i costi di trasporto (fase successiva).
- `CDG_QV`        — il datawarehouse che creiamo qui (destinazione).
- vecchia tabella custom — oracolo di riconciliazione (fase successiva).

Join cross-DB consentiti in LETTURA: l'ETL legge da `KODICEBAGNO_4`/`trasporti` e
scrive SOLO in `CDG_QV`. La dipendenza dal gestionale è confinata negli script di `extract/`.

## Architettura di CDG_QV (a strati, via schemi)

- `src`  — landing: i dati grezzi normalizzati estratti da Mago (`src.righe_vendita`).
- `kodice` — **motore di costo articolo/mese** (già presente in CDG_QV, NON parte dello scaffold).
    Lo alimenta `core.usp_prepara_costi` (parametrico per schema azienda): legge `kodice.vw_costo_sorgente`
    (adattatore su `MA_ItemsWAP`, filtro `Storage=''`) + `kodice.vw_distinta` (su `MA_BillOfMaterialsComp`),
    risolve risalita mese ed esplosione kit, e CERTIFICA `kodice.costi_articolo_mese` (solo costi validi)
    mandando il resto in `kodice.costi_eccezioni` (con stato APERTA/RISOLTA). Il componente `COSTO_VENDUTO`
    legge da `kodice.costi_articolo_mese`. Lo stato del mese vive in `kodice.prep_controllo_mesi`.
- `core` — il cuore:
    - `core.componente_riga` (formato "lungo"): una riga per (riga documento, componente).
      Ogni importo sa da dove arriva (`origine`). È la sorgente tracciabile e atomica.
    - `core.fatto_riga`: l'assemblaggio finale, una riga per riga documento, con ricavo e MdC I/II/III.
- `cfg`  — configurazione e stato: `cfg.componenti` (il REGISTRO), `cfg.controllo_mesi` (avanzamento).
- `pres` — viste per i consumatori: `pres.conto_economico_riga` (Qlik), `pres.componente_riga`
  (dettaglio per componente), `pres.controllo_componenti` (sintesi per validare ogni voce).

## Modello a componenti isolati (principio chiave)

Ogni componente di costo/ricavo è **indipendente**:
- ha una sola procedura, `dbo.usp_comp_<CODICE>` in `sql/components/`, che scrive SOLO le
  proprie righe in `core.componente_riga` (DELETE delle proprie + INSERT). Correggere un
  componente non tocca gli altri.
- è descritto in `cfg.componenti` (livello di margine, segno, attivo). L'assemblaggio
  (`usp_build_fatto_riga`) somma per livello leggendo il registro: è **dichiarativo**.
- **Aggiungere un componente** = scrivere la sua procedura + una riga attiva nel registro.
  La pipeline scopre da sola i componenti `attivo = 1` e non va modificata.

Stato attuale: attivo solo `COSTO_VENDUTO`. Provvigioni, imballi, trasporto, pubblicità sono
già censiti nel registro (attivo = 0) con la procedura-stub pronta da adattare.

## Convenzioni (importanti)

- **Calcolo in T-SQL** dentro CDG_QV (stored procedure), **Python solo orchestratore**.
- **Script idempotenti**: `CREATE OR ALTER` per viste/procedure, `IF NOT EXISTS` per
  schemi/tabelle. Lo stesso file gira su DB vuoto o già esistente.
- **Niente date hardcoded**: la competenza è sempre parametro (`@anno`, `@mese`).
  Lo stato di avanzamento vive in tabella (`cfg.controllo_mesi`), non nel codice.
- **Un file per oggetto/gruppo logico**, numerato per ordine di esecuzione.
- **Grana del fatto**: una riga per riga di dettaglio del documento (`sale_doc_id`, `line`).
- Codice e commenti in **italiano** (l'autore viene da C#).
- Credenziali solo in `.env` (vedi `.env.example`), mai committate.

## Definizioni di calcolo

- **Ricavo netto riga**: imponibile di riga (`MA_SaleDocDetail.TaxableAmount`) **+ la quota di
  spese di trasporto recuperate** (`MA_SaleDocSummary.ShippingCharges`, dato di documento)
  spalmata in proporzione all'imponibile — per confrontabilita' tra marketplace che fatturano il
  trasporto a parte e quelli che lo inglobano. Solo documenti di vendita validi, con segno
  (fatture +, note credito −; segno anche sulla quantita'). Filtri/segno: vedi `usp_load_righe_vendita`.
- **Costo del venduto** [componente `COSTO_VENDUTO`, livello 1]: quantità × costo certificato
  dell'articolo per il mese di competenza, letto da `kodice.costi_articolo_mese`. Risalita mese
  ed esplosione kit sono **già risolte** dal motore `core.usp_prepara_costi` (base WAP da `MA_ItemsWAP`).
  Le righe il cui articolo non ha costo certificato non producono costo (sono ricavo senza costo;
  se anomale, sono in `kodice.costi_eccezioni`). Prerequisito: il periodo dev'essere preparato.
- **Margini di contribuzione** (cumulativi, dal registro `cfg.componenti`):
    - **MdC I**   = Ricavo + Σ(componenti livello 1, con segno) — qui: ricavo − costo del venduto.
    - **MdC II**  = MdC I  + Σ(livello 2) — costi variabili di vendita attribuibili (provvigioni, imballi…).
    - **MdC III** = MdC II + Σ(livello 3) — costi allocabili per driver (trasporto, pubblicità…).
- I costi di **struttura** restano fuori dalla riga (livello NULL, modo_attacco 'struttura'):
  vanno gestiti a un livello aggregato, non forzati sul dettaglio.

Tassonomia di attacco (colonna `modo_attacco` nel registro): `diretto` (attribuibile alla riga),
`driver` (allocato con un driver reale: peso, volume, tasso di reso…), `struttura` (overhead).

## Valorizzazione di magazzino e ricalcolo del WAP (`sql/verifiche/wap_ricalc.sql`)

`MA_ItemsWAP` di Mago è aggiornata da una procedura non visibile e su molti articoli ha **quantità**
sbagliate (va negativa → il costo crolla a 0 e il motore costi si ferma). Il **costo unitario** (`WAPCost`)
è invece per lo più affidabile. Per non dipendere da quella tabella abbiamo un **ricalcolo parallelo che NON
tocca Mago**:

- **`kodice.wap_ricalc`** (+ `kodice.usp_ricalc_wap @Anno`): ricostruisce il WAP **mese per mese**, media
  ponderata di periodo con roll-forward. Apertura di Gennaio = `KLProgUbicazioni.QtaIniziale` (ATRI/fisico) +
  `MA_ItemsBalances.InitialBookInv` (depositi ≠ ATRI), valorizzata al WAPCost di risalita (Dic anno prec.).
  Movimenti per `WAPMovementType` (acquisti 2032533505, vendite …506, resi …509; trasferimenti/ignora esclusi).
  Tiene **due bucket** `ValPuro`/`ValOneri` (oneri = causali `AGGDAZI`/`IMPORT`) così lo **split costo
  d'acquisto vs oneri accessori somma sempre al WAPCost**. Verificato: ~91% entro 5% dal WAP di Mago, e dove
  Mago crolla a 0 il ricalcolo tiene il costo.
- **`kodice.vw_costo_eff`** — costo unitario **EFFICACE** per articolo, scelto **per metodo Mago in base al
  `ValuationType`** (`MA_ItemsFiscalYearData`: 11272206 = **MPP/WAP**, 11272194 = **MEDIO**), con ripieghi
  (regola "il costo sopravvive a giacenza 0": si cerca sempre l'ultimo costo > 0):
    - **MPP** → `RICALCOLO_APR` (ultimo costo `wap_ricalc` 2026 ≤ Aprile, con split) → `RISALITA_WAP`
      (ultimo `WAPCost>0` storico di `MA_ItemsWAP`, anche di anni fa: la stessa risalita del report Mago).
    - **MEDIO** → `MEDIO_NOSTRO` = media **annuale** "alla Mago" = (apertura valorizzata + acquisti **PURI**
      del periodo) / (qta apertura + qta acquisti), **senza oneri** (il medio NON è salvato in Mago: si
      ricalcola) → ripiego `MEDIO_LASTCOST` (`MA_ItemsBalances.LastCost`).
  Taglio valorizzazione = **Aprile 2026** (a maggio mancano ancora gli oneri accessori da caricare).
- **Report inventario di bilancio** (`src/genera_report_inventario.py`): confronta **riga per riga** il
  *Valore Bilancio* del report Mago "Magazzino a valori" con il nostro = **giacenza del report × costo
  efficace**. I **kit** sono valorizzati con la **distinta esplosa** (`kodice.vw_distinta` ricorsiva) sui costi
  efficaci dei componenti. Per i pochi articoli senza alcun costo ricostruibile in SQL (imballaggi interni tipo
  EPAL, senza WAP né acquisti) si usa come ultimo ripiego il **prezzo del report Mago** (`MEDIO_REPORT`), così
  tutto resta valorizzato e nulla è "mancante". Dove abbiamo un costo **indipendente** molto diverso dal prezzo
  Mago (report < ½ o > 2× del nostro) la riga è segnalata come **prezzo report sospetto** (foglio dedicato).
- **Articoli esclusi dal costo** (`kodice.articoli_esclusi_costo`): codici di SERVIZIO/non-prodotto (es.
  `SPESEDITRASPORTO` = "Spese di trasporto") con movimenti a quantità negativa, che non sono merce. Filtrati nel seed
  di `usp_ricalc_wap` → niente righe `wap_ricalc`, quindi esclusi anche da `vw_costo_eff`, `vw_qualita_costo` e dal
  report inventario. Per escluderne altri basta inserire una riga (nessuna modifica al codice).

## Nomi sorgente Mago (CONFERMATI)

Confermati sullo schema reale di `KODICEBAGNO_4` (oracolo: estrattore Qlik + adattatore `kodice`):
- righe di vendita: testata `MA_SaleDoc` (chiave `SaleDocId`, data `DocumentDate`), dettaglio
  `MA_SaleDocDetail` (`Line`, articolo `Item`, quantità `Qty`, netto `TaxableAmount`);
- costo: `MA_ItemsWAP` (`Item`, `EndPeriodDate` → anno/mese, `WAPCost`, riga totale `Storage=''`);
- distinta kit: `MA_BillOfMaterialsComp` (`BOM`, `Component`, `Qty`).

## Come lanciare

```
pip install -r requirements.txt
python run_pipeline.py            # DDL, load da Mago, componenti attivi, build per il periodo
python src/demo_calcolo.py        # verifica "a secco" della logica (no DB)
python src/genera_dashboard.py    # genera dashboard/index.html (no DB)
python src/esegui_caso_test.py    # esegue il caso di test sulle procedure reali (richiede DB)
```

## Dashboard delle estrazioni (per i non tecnici)

`dashboard/index.html` mostra, per ogni componente, la spiegazione ("da dove prende i dati",
"con che logica") ACCANTO al testo SQL reale della procedura, più un pannello "caso di test".
È **generata** da `src/genera_dashboard.py` a partire da:
- `dashboard/manifest.yaml` — le spiegazioni (fonte unica, da tenere aggiornata);
- i file `sql/components/*.sql` — il codice reale (mostrato verbatim, non può divergere);
- il seed del registro — livello/segno/attivo di ogni componente.

Il generatore **verifica l'allineamento**: segnala se un componente è nel registro ma senza
spiegazione (o senza file SQL), così la dashboard non scivola dal codice. Regola operativa:
quando aggiungi o modifichi un componente, aggiorna anche la sua voce nel manifest e rigenera.

Il pannello "caso di test" (`dashboard/caso_test.json`, editabile) ha due livelli:
simulazione immediata nel browser (logica dichiarata nel manifest) e, per la verifica
autorevole, `src/esegui_caso_test.py` che fa girare il documento sulle **procedure reali**.
