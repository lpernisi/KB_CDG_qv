# Riconciliazione con la contabilità — METODO DI RIFERIMENTO

> **A cosa serve.** Questo è il documento UNICO di riferimento per **riconciliare una sezione del
> Conto Economico CDG contro la contabilità generale di Mago**. La sezione **Costo dei materiali** è
> già fatta e validata: è il modello. Per ogni nuova sezione (Commerciali, Trasporto, Imballi,
> Finanziari…) si segue questa ricetta e si **punta a questo documento** per le istruzioni, invece di
> reinventare la logica.

## Principio (vale per tutte le sezioni)

La contabilità è il **secondo oracolo** (oltre al controllo riga-per-riga): a livello di **periodo** un
totale di costo del CDG deve **quadrare** con quanto registrato sui conti di bilancio. Due livelli:

1. **Quadratura top-down** (la fa `api_quadratura_materiale`): un'identità contabile che parte dai conti
   e arriva al consumo, da confrontare col nostro totale.
   - Materiale: `Consumo = Acquisti(GL) + Oneri(GL) + Rimanenze iniziali − Rimanenze finali`.
   - **Costi diretti senza magazzino** (Commerciali, Trasporto, Pubblicità…): NON hanno rimanenze →
     l'identità si semplifica a `Costo contabile del periodo (GL) ≈ Nostro componente del periodo`.
2. **Riconciliazione analitica** (la fa `api_riconciliazione_cogs`): il **ponte** voce per voce dal
   nostro numero al numero di bilancio, dove **ogni differenza ha una causa scritta** (sfasamenti
   tempo, righe senza codice, prima nota diretta…) e drill fino al documento. La **differenza non
   giustificata** che resta è il vero residuo da indagare.

> **Regola d'oro (vedi CLAUDE.md, principio N.1):** ogni riga di riconciliazione è in lingua da
> controllo di gestione, ha una **causa in parole**, è **drill-abile fino al documento** e i **totali
> tornano**. Niente numeri "per differenza".

## Le fonti contabili in Mago (nomi reali, CONFERMATI)

| Tabella `KODICEBAGNO_4.dbo` | Cos'è | Campi chiave |
|---|---|---|
| **MA_JournalEntriesGLDetail** | Righe del libro giornale (registrazioni) | `Account` (conto), `Amount`, `DebitCreditSign` (**4980736 = DARE, 4980737 = AVERE**), `AccrualDate` (competenza), `JournalEntryId` |
| **MA_JournalEntries** | Testata registrazione | `JournalEntryId`, `DocumentDate`, `AccrualDate`, `CRRefID`/`CRRefType` (documento collegato; **27066402 = fattura**) |
| **MA_ChartOfAccountsBalances** | Saldi di **bilancio** per conto/mese (per le RIMANENZE) | `Account`, `FiscalYear`, `BalanceType` (**3145728 = apertura, 3145730 = progressivo del mese**), `BalanceMonth`, `Debit`, `Credit` |
| **MA_PurchaseDoc** | Fatture d'acquisto (per legare il conto al fornitore) | `PurchaseDocId`, `DocNo`, `Supplier`, `DocumentDate` |
| **MA_CrossReferences** | Legami tra documenti (origine→derivato) | `OriginDocID`/`OriginDocType`, `DerivedDocID`/`DerivedDocType` (27066402 = fattura, 27066370 = movimento magazzino) |

**Saldo di un conto di costo nel periodo** (la formula da riusare sempre):
```sql
SUM(CASE WHEN g.DebitCreditSign = 4980736 THEN g.Amount ELSE -g.Amount END)   -- DARE − AVERE
-- filtro competenza: YEAR(g.AccrualDate)=@anno AND MONTH(g.AccrualDate) BETWEEN @mese_da AND @mese_a
```
Le **rimanenze** (stato patrimoniale) NON si leggono dal GL cumulato ma dai **saldi di bilancio**
(`MA_ChartOfAccountsBalances`): apertura = `BalanceType 3145728`; saldo a fine mese = apertura +
progressivi (`3145730`) fino al mese. Vedi `src/dashboard_app.py:667-675`.

## La mappatura dei conti — `kodice.conti_quadratura`

Cuore riusabile del metodo: una tabella che dice **quali conti contabili appartengono a quale
componente del CE e con che ruolo**. Definizione e seed in **`sql/verifiche/quadratura_contabile.sql`**.

```sql
kodice.conti_quadratura (
    Componente varchar(30),   -- MATERIALE | COMMERCIALI | TRASPORTO | ...
    Account    varchar(30),   -- conto di MA_ChartOfAccounts (es. '06011000')
    Ruolo      varchar(30),   -- ACQUISTO | ONERE_ACQUISTO | COSTO | RIMANENZE | ...
    Nota       varchar(200),  -- descrizione leggibile del conto
    PRIMARY KEY (Componente, Account)
)
```
Seed con `MERGE … WHEN NOT MATCHED` (idempotente, non sovrascrive). Già presenti:
- **MATERIALE**: `06011000/06011002` (ACQUISTO), `06013000`/`06014*`/`06015*` (ONERE_ACQUISTO),
  `00041000..00041300` (RIMANENZE). Riconciliato gen–mag 2026 a **+2,0%** del nostro COGS.
- **TRASPORTO**: `06021600..06021613` (COSTO) — spese di spedizione sulle vendite.

**Ruoli** = natura del conto, guida COME si riconcilia: `ACQUISTO` (vs carico magazzino, per
fornitore/documento), `ONERE_ACQUISTO` (vs carico, per mese — c'è sfasamento), `COSTO` (costo diretto:
totale GL del periodo vs nostro componente), `RIMANENZE` (saldo di bilancio, non GL).

## Gli endpoint del materiale (i modelli da copiare)

| Endpoint (`src/dashboard_app.py`) | Riga | Cosa fa |
|---|---|---|
| `api_quadratura_materiale` | 645 | Quadratura top-down del periodo (identità Consumo). **Modello per la quadratura di una sezione.** |
| `api_riconciliazione_cogs` | 768 | Ponte analitico (≈16 voci) con causa e drill. **Modello per il ponte di una sezione.** |
| `api_riconciliazione_acquisti` / `_forn` | 1515 / 1588 | Validazione per fornitore e drill **documento per documento**. |
| `api_riconciliazione_imb_forn` | 1386 | Drill per fornitore su un singolo conto (06021505), Fattura vs carico vs prima nota. |
| `api_riconciliazione_drill` | 996 | Drill generico di una voce del ponte. |

Tabelle/viste di supporto: `kodice.wap_ricalc` (valorizzazione magazzino), `kodice.vw_classe_articolo`
(PRODOTTO/IMBALLAGGIO/SERVIZIO), `kodice.carico_fattura` e `kodice.vendite_link` (legami
carico→bolla→fattura materializzati). Il nostro costo per la sezione si legge da `core.fatto_riga`
(`ricavo_netto − mdc1` = materiale) o da `core.componente_riga` per il singolo componente.

## La pagina dashboard (pattern visivo)

Sezione `#sec-materiali` con **sottoviste** (tab): `riconc` (ponte) e `valacq` (validazione acquisti)
sono le due di riconciliazione. Struttura: header con pulsante "Elabora/Ricalcola", tab bar, container
dinamico con tabella del ponte (voce · importo · causa · drill) e drill per fornitore/documento.
Gli strumenti di solo debug vanno marcati `.solo-validazione` (spariscono nella vista CEO).

## RICETTA — aggiungere la riconciliazione di una nuova sezione

Esempio: **Commerciali** (commissioni, channel engine, provvigioni agenti, pubblicità, canoni).

1. **Trova i conti contabili** della sezione in Mago (query esplorativa su `MA_JournalEntriesGLDetail`
   per `AccrualDate` del periodo, raggruppando per `Account` con la descrizione da `MA_ChartOfAccounts`,
   per capire dove sono registrati commissioni/pubblicità/provvigioni/canoni). **Questo passo è
   specifico per sezione e va sempre fatto sui dati reali, non indovinato.**
2. **Seeda `kodice.conti_quadratura`** con `Componente='COMMERCIALI'` e i conti trovati, scegliendo il
   `Ruolo` (per i costi diretti tipicamente `COSTO`). Aggiungi un blocco `MERGE` in
   `sql/verifiche/quadratura_contabile.sql` (stesso stile di MATERIALE/TRASPORTO).
3. **Scegli l'identità**: per i costi diretti senza magazzino →
   `Costo contabile del periodo (somma GL dei conti COMMERCIALI) ≈ Σ nostri componenti commerciali`
   (`core.componente_riga` per PROVVIGIONI + CHANNEL_ENGINE + PROVVIGIONI_AGENTI). NIENTE rimanenze.
4. **Crea gli endpoint** ricalcando i modelli: `api_quadratura_commerciali` (totale GL per conto vs
   nostro totale) e, se serve il dettaglio, un ponte/drill con le **cause** delle differenze. La
   differenza tipica qui = **costi presenti in contabilità ma NON ancora attribuiti per riga**
   (pubblicità, canoni, retail media): è il residuo che dice "quanto manca da modellare/caricare".
5. **Aggiungi la sottovista** nella sezione dashboard "Costi commerciali" (tab `riconc`), con il ponte
   e il drill, in lingua da controller.
6. **Valida e itera**: ogni euro di scarto deve avere una causa scritta; ciò che resta indica i costi
   commerciali ancora da caricare/attribuire.

## Riferimenti

- Codice contabile/mappatura: `sql/verifiche/quadratura_contabile.sql`.
- Endpoint modello: `src/dashboard_app.py` (righe in tabella sopra).
- Costi commerciali (stato/decisioni): vedi memoria `[[costi-commerciali]]` e
  `docs/riferimento-split-costi-variabili.md`.
- Quadratura materiale (stato/risultati): memoria `[[quadratura-contabile-magazzino]]`.
