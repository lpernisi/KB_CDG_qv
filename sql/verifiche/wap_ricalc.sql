-- =============================================================================
-- wap_ricalc.sql   (oggetti in CDG_QV, schema kodice — eseguire in SSMS o via pipeline)
-- -----------------------------------------------------------------------------
-- RICALCOLO PARALLELO del WAP (NON tocca MA_ItemsWAP di Mago), con split del costo in
-- ACQUISTO PURO + ONERI ACCESSORI, partendo dalla giacenza REALE ricostruita.
--
-- Per ogni (Item, Mese) dell'anno @Anno, a rotazione da Gennaio:
--   Giacenza iniziale Gen = SUM(KLProgUbicazioni.QtaIniziale Eserc.@Anno)              [deposito ATRI, fisico]
--                         + SUM(MA_ItemsBalances.InitialBookInv FY @Anno, Storage<>'ATRI')
--   Costo d'apertura Gen  = OVERRIDE manuale (kodice.wap_apertura_override) se presente [BONIFICA dati di partenza],
--                           altrimenti ultimo WAPCost>0 di MA_ItemsWAP < Gen @Anno (risalita robusta: non azzera se
--                           a fine anno prec. Mago era a 0/negativo). Valore iniziale = QtaIniz * costo d'apertura.
--   Split iniziale: ValPuroIniz = qty*puro_unit; ValOneriIniz = qty*oneri_unit (di norma oneri apertura = 0).
--   Carichi/oneri dai MOVIMENTI (per WAPMovementType). VALORI CONVERTITI IN EUR: LineAmount * Fixing per
--   i movimenti in valuta estera (Fixing = cambio del movimento; EUR -> Fixing 0, nessuna conversione):
--     ACQUISTI (2032533505): +Qty; valore spaccato per QUANTITA' (come MA_ItemsWAP): movimento CON qty = acquisto
--                            (puro); SENZA qty = oneri/cambi spalmati (dazi/import + differenze cambio ACQ-VALD).
--     VENDITE  (2032533506): -Qty
--     RESI     (2032533509): +Qty (rientro al WAPCost di periodo)
--     TRASFERIMENTI (507) / IGNORA (508) / NULL: esclusi
--   Media ponderata di periodo:
--     puro_unit  = (ValPuroIniz  + ValAcqPuro)  / (QtaIniz + QtaAcq)
--     oneri_unit = (ValOneriIniz + ValAcqOneri) / (QtaIniz + QtaAcq)
--     WAPCost_ricalc = puro_unit + oneri_unit          (somma SEMPRE = costo; split garantito)
--     QtaFin = QtaIniz + QtaAcq - QtaVend + QtaResi
--     ValPuroFin = puro_unit*QtaFin ; ValOneriFin = oneri_unit*QtaFin
--   Il mese successivo parte da *Fin. Colonna di CONTROLLO: WAPCost_Mago (stesso mese) e Delta.
-- =============================================================================

USE CDG_QV;
GO

IF OBJECT_ID('kodice.wap_ricalc', 'U') IS NULL
CREATE TABLE kodice.wap_ricalc (
    Item          varchar(21)  NOT NULL,
    Anno          smallint     NOT NULL,
    Mese          tinyint      NOT NULL,
    QtaIniz       float        NULL,
    ValPuroIniz   float        NULL,
    ValOneriIniz  float        NULL,
    QtaAcq        float        NULL,
    ValAcqPuro    float        NULL,
    ValAcqOneri   float        NULL,
    QtaVend       float        NULL,
    QtaResi       float        NULL,
    QtaRettTrasf  float        NULL,   -- rettifiche/trasferimenti con segno (CAR-AMA +, KLRI/RI ±, KLR-FORA −), costo-neutro
    QtaFin        float        NULL,
    ValPuroFin    float        NULL,
    ValOneriFin   float        NULL,
    PuroUnit      float        NULL,
    OneriUnit     float        NULL,
    WAPCost_ricalc float       NULL,
    WAPCost_Mago  float        NULL,
    Delta         float        NULL,
    CONSTRAINT PK_wap_ricalc PRIMARY KEY (Item, Anno, Mese)
);
GO

-- aggiunta colonna se la tabella esisteva gia' (idempotente)
IF COL_LENGTH('kodice.wap_ricalc', 'QtaRettTrasf') IS NULL
    ALTER TABLE kodice.wap_ricalc ADD QtaRettTrasf float NULL;
GO

-- =============================================================================
-- wap_apertura_override — BONIFICA dei DATI DI PARTENZA (apertura d'esercizio).
-- -----------------------------------------------------------------------------
-- L'algoritmo di ricalcolo e' affidabile: l'unico punto debole sono i dati di apertura
-- (giacenza/costo iniziale). Per gli articoli con apertura sbagliata — tipicamente WAPCost di
-- risalita = 0 perche' a fine anno precedente Mago era andato in quantita' NEGATIVA, oppure
-- quantita' d'apertura errata — qui si FORZA il valore corretto. Il seed di usp_ricalc_wap usa
-- questi valori al posto del calcolo automatico (COALESCE: override -> automatico).
IF OBJECT_ID('kodice.wap_apertura_override', 'U') IS NULL
CREATE TABLE kodice.wap_apertura_override (
    Item            varchar(21)  NOT NULL,
    Anno            smallint     NOT NULL,
    QtaIniz         float        NULL,   -- forza la QUANTITA' d'apertura (NULL = usa quella calcolata)
    CostoPuroUnit   float        NULL,   -- forza il costo PURO unitario d'apertura (NULL = risalita automatica su WAPCost>0)
    CostoOneriUnit  float        NULL,   -- forza gli ONERI unitari d'apertura (NULL = 0)
    Fonte           varchar(30)  NULL,   -- LASTCOST / RISALITA_POS / MAGO / MANUALE ...
    Nota            varchar(500) NULL,
    Utente          varchar(100) NULL,
    DataStato       datetime     NULL,
    CONSTRAINT PK_wap_apertura_override PRIMARY KEY (Item, Anno)
);
GO

-- =============================================================================
-- articoli_esclusi_costo — codici di SERVIZIO/non-prodotto da NON valorizzare.
-- -----------------------------------------------------------------------------
-- Alcuni "articoli" di Mago non sono merce di magazzino ma voci di servizio (es.
-- SPESEDITRASPORTO = "Spese di trasporto"): hanno movimenti che generano quantita'
-- negative e NON devono entrare nel costo del prodotto (il trasporto e' una voce a se'
-- del Conto Economico). Vengono esclusi dal ricalcolo WAP -> quindi anche da
-- vw_costo_eff, vw_qualita_costo e dal report inventario. Per escluderne altri basta
-- inserire una riga qui (nessuna modifica al codice).
IF OBJECT_ID('kodice.articoli_esclusi_costo', 'U') IS NULL
CREATE TABLE kodice.articoli_esclusi_costo (
    Item  varchar(21)  NOT NULL CONSTRAINT PK_articoli_esclusi_costo PRIMARY KEY,
    Nota  varchar(200) NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM kodice.articoli_esclusi_costo WHERE Item = 'SPESEDITRASPORTO')
    INSERT INTO kodice.articoli_esclusi_costo (Item, Nota)
    VALUES ('SPESEDITRASPORTO', 'Voce di servizio (Spese di trasporto): non e'' un prodotto, escluso dal costo materiali.');
GO

CREATE OR ALTER PROCEDURE kodice.usp_ricalc_wap
    @Anno    smallint,
    @MeseMax tinyint = 12
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM kodice.wap_ricalc WHERE Anno = @Anno;

    -- ---- SEED: giacenza iniziale reale + costo d'apertura ----
    -- Costo d'apertura: 1) OVERRIDE manuale (kodice.wap_apertura_override) se presente -> bonifica dati;
    --                   2) altrimenti RISALITA su ultimo WAPCost>0 (NON l'ultima riga: se Mago era a 0/negativo
    --                      a fine anno prec. il costo non deve azzerarsi -> regola "il costo sopravvive a giacenza 0").
    IF OBJECT_ID('tempdb..#seed') IS NOT NULL DROP TABLE #seed;
    ;WITH ubi AS (
        SELECT LTRIM(RTRIM(Articolo)) AS Item, SUM(QtaIniziale) AS q
        FROM KODICEBAGNO_4.dbo.KLProgUbicazioni WHERE Esercizio = @Anno GROUP BY LTRIM(RTRIM(Articolo))
    ),
    baln AS (
        SELECT LTRIM(RTRIM(Item)) AS Item, SUM(InitialBookInv) AS q
        FROM KODICEBAGNO_4.dbo.MA_ItemsBalances WHERE FiscalYear = @Anno AND Storage <> 'ATRI' GROUP BY LTRIM(RTRIM(Item))
    ),
    baln_atri AS (   -- apertura ATRI presa da MA_ItemsBalances SOLO per articoli NON in KLProgUbicazioni
        -- (es. pool imballi/EPAL: stanno in ATRI a bilancio ma non sono ubicazioni fisiche). Cosi'
        -- entrano nel magazzino senza fare doppio conteggio degli articoli ATRI gia' presi da 'ubi'.
        SELECT LTRIM(RTRIM(Item)) AS Item, SUM(InitialBookInv) AS q
        FROM KODICEBAGNO_4.dbo.MA_ItemsBalances
        WHERE FiscalYear = @Anno AND Storage = 'ATRI'
          AND LTRIM(RTRIM(Item)) NOT IN (   -- escludi solo i normali ATRI gia' contati da 'ubi' (qta KLProg > 0)
              SELECT LTRIM(RTRIM(Articolo)) FROM KODICEBAGNO_4.dbo.KLProgUbicazioni
              WHERE Esercizio = @Anno GROUP BY LTRIM(RTRIM(Articolo)) HAVING SUM(QtaIniziale) > 0)
        GROUP BY LTRIM(RTRIM(Item))
    ),
    seedw AS (   -- ultimo WAPCost>0 prima dell'anno (risalita robusta; NON azzera se l'ultima riga e' 0)
        SELECT Item, WAPCost FROM (
            SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
                   ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
            FROM KODICEBAGNO_4.dbo.MA_ItemsWAP WHERE Storage = '' AND WAPCost > 0 AND EndPeriodDate < DATEFROMPARTS(@Anno,1,1)
        ) t WHERE rn = 1
    ),
    py AS (   -- CHIUSURA del ricalcolo dell'ANNO PRECEDENTE (ultimo mese): conserva lo SPLIT puro/oneri
              -- all'apertura, altrimenti il totale di risalita finirebbe tutto in 'puro' (oneri=0 -> falso Q3).
        SELECT Item, PuroUnit, OneriUnit FROM (
            SELECT Item, PuroUnit, OneriUnit,
                   ROW_NUMBER() OVER (PARTITION BY Item ORDER BY Mese DESC) rn
            FROM kodice.wap_ricalc WHERE Anno = @Anno - 1
        ) t WHERE rn = 1
    ),
    ovr AS (
        SELECT LTRIM(RTRIM(Item)) AS Item, QtaIniz, CostoPuroUnit, CostoOneriUnit
        FROM kodice.wap_apertura_override WHERE Anno = @Anno
    ),
    mov AS (
        SELECT DISTINCT LTRIM(RTRIM(d.Item)) AS Item
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
        WHERE YEAR(h.PostingDate) = @Anno
    ),
    univ AS (SELECT Item FROM ubi UNION SELECT Item FROM baln UNION SELECT Item FROM baln_atri UNION SELECT Item FROM mov UNION SELECT Item FROM seedw UNION SELECT Item FROM ovr UNION SELECT Item FROM py)
    -- Il TOTALE d'apertura resta invariato (override oppure risalita WAP): NON tocca il costo/COGS.
    -- Lo SPLIT puro/oneri pero' viene preso dalla chiusura dell'anno precedente (py): senza, il totale
    -- di risalita finirebbe tutto in 'puro' (oneri=0) generando un falso Q3 "oneri spariti" a inizio anno.
    SELECT u.Item,
           COALESCE(ov.QtaIniz, ISNULL(ubi.q,0) + ISNULL(baln.q,0) + ISNULL(ba.q,0)) AS qty,
           CASE WHEN ov.CostoPuroUnit IS NOT NULL THEN ov.CostoPuroUnit
                WHEN py.Item IS NOT NULL AND (py.PuroUnit + py.OneriUnit) > 0
                     THEN ISNULL(sw.WAPCost,0) * py.PuroUnit / (py.PuroUnit + py.OneriUnit)
                ELSE ISNULL(sw.WAPCost,0) END                       AS puro_unit,
           CASE WHEN ov.CostoPuroUnit IS NOT NULL THEN ISNULL(ov.CostoOneriUnit, 0)
                WHEN py.Item IS NOT NULL AND (py.PuroUnit + py.OneriUnit) > 0
                     THEN ISNULL(sw.WAPCost,0) * py.OneriUnit / (py.PuroUnit + py.OneriUnit)
                ELSE 0 END                                          AS oneri_unit
    INTO #seed
    FROM univ u
    LEFT JOIN ubi  ON ubi.Item  = u.Item
    LEFT JOIN baln ON baln.Item = u.Item
    LEFT JOIN baln_atri ba ON ba.Item = u.Item
    LEFT JOIN seedw sw ON sw.Item = u.Item
    LEFT JOIN ovr ov ON ov.Item = u.Item
    LEFT JOIN py ON py.Item = u.Item
    WHERE NOT EXISTS (SELECT 1 FROM kodice.articoli_esclusi_costo x WHERE x.Item = u.Item);  -- voci di servizio (es. SPESEDITRASPORTO)

    DECLARE @m tinyint = 1;
    WHILE @m <= @MeseMax
    BEGIN
        ;WITH prev AS (
            SELECT Item, QtaFin, ValPuroFin, ValOneriFin
            FROM kodice.wap_ricalc WHERE Anno = @Anno AND Mese = @m - 1
        ),
        iniz AS (
            SELECT s.Item,
                   CASE WHEN @m = 1 THEN s.qty                ELSE ISNULL(p.QtaFin,0)      END AS QtaIniz,
                   CASE WHEN @m = 1 THEN s.qty * s.puro_unit   ELSE ISNULL(p.ValPuroFin,0)  END AS ValPuroIniz,
                   CASE WHEN @m = 1 THEN s.qty * s.oneri_unit  ELSE ISNULL(p.ValOneriFin,0) END AS ValOneriIniz
            FROM #seed s LEFT JOIN prev p ON p.Item = s.Item
        ),
        mv AS (
            -- I valori sono convertiti in EUR: LineAmount * Fixing per i movimenti in valuta estera
            -- (h.Fixing = cambio del movimento su MA_InventoryEntries; per EUR Fixing=0 -> nessuna conversione).
            -- Split (come MA_ItemsWAP): movimento acquisto CON quantita' = costo d'acquisto (PURO).
            -- SENZA quantita' = valori spalmati: ONERI accessori (dazi/import: IMPORT, AGGDAZI) MA la
            -- differenza CAMBIO sull'acquisto (causale ACQ-VALD) e' parte del costo d'acquisto REALE in EUR
            -- -> va nel PURO, non negli oneri. Riconciliato al centesimo col WAP di Mago.
            SELECT LTRIM(RTRIM(d.Item)) AS Item,
                   SUM(CASE WHEN h.WAPMovementType = 2032533505 THEN d.Qty ELSE 0 END) AS QtaAcq,
                   SUM(CASE WHEN h.WAPMovementType = 2032533505 AND d.Qty = 0 AND h.InvRsn <> 'ACQ-VALD'
                            THEN d.LineAmount * CASE WHEN h.Currency NOT IN ('','EUR') AND h.Fixing > 0 THEN h.Fixing ELSE 1 END
                            ELSE 0 END) AS ValAcqOneri,
                   SUM(CASE WHEN h.WAPMovementType = 2032533505 AND (d.Qty <> 0 OR h.InvRsn = 'ACQ-VALD')
                            THEN d.LineAmount * CASE WHEN h.Currency NOT IN ('','EUR') AND h.Fixing > 0 THEN h.Fixing ELSE 1 END
                            ELSE 0 END) AS ValAcqPuro,
                   SUM(CASE WHEN h.WAPMovementType = 2032533506 THEN d.Qty ELSE 0 END) AS QtaVend,
                   SUM(CASE WHEN h.WAPMovementType = 2032533509 THEN d.Qty ELSE 0 END) AS QtaResi,
                   -- RETTIFICHE/TRASFERIMENTI con SEGNO (a costo-neutro: entrano/escono al WAP del periodo).
                   -- +: CAR-AMA (carico Amazon FBA), KLRI-P-A / RI-POS (rettifiche inventario positive).
                   -- -: KLRI-N-A / RI-NEG (rettifiche negative), KLR-FORA (reso a fornitore, merce che esce).
                   -- IGNORATI: KL-TRASF (spostamento ubicazione dentro ATRI), MOV-DEP (mov. tra depositi),
                   --           KRETASS+/- (rettifiche di assegnazione). KLVEN-OA resta vendita (-, tipo 506).
                   SUM(CASE WHEN h.InvRsn IN ('CAR-AMA','KLRI-P-A','RI-POS')  THEN d.Qty
                            WHEN h.InvRsn IN ('KLRI-N-A','RI-NEG','KLR-FORA') THEN -d.Qty
                            ELSE 0 END) AS QtaRettTrasf
            FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
            WHERE YEAR(h.PostingDate) = @Anno AND MONTH(h.PostingDate) = @m
            GROUP BY LTRIM(RTRIM(d.Item))
        ),
        calc AS (
            SELECT i.Item, i.QtaIniz, i.ValPuroIniz, i.ValOneriIniz,
                   ISNULL(mv.QtaAcq,0) AS QtaAcq, ISNULL(mv.ValAcqPuro,0) AS ValAcqPuro, ISNULL(mv.ValAcqOneri,0) AS ValAcqOneri,
                   ISNULL(mv.QtaVend,0) AS QtaVend, ISNULL(mv.QtaResi,0) AS QtaResi, ISNULL(mv.QtaRettTrasf,0) AS QtaRettTrasf
            FROM iniz i LEFT JOIN mv ON mv.Item = i.Item
        )
        INSERT INTO kodice.wap_ricalc
            (Item,Anno,Mese,QtaIniz,ValPuroIniz,ValOneriIniz,QtaAcq,ValAcqPuro,ValAcqOneri,QtaVend,QtaResi,QtaRettTrasf,
             QtaFin,ValPuroFin,ValOneriFin,PuroUnit,OneriUnit,WAPCost_ricalc,WAPCost_Mago,Delta)
        SELECT c.Item, @Anno, @m, c.QtaIniz, c.ValPuroIniz, c.ValOneriIniz, c.QtaAcq, c.ValAcqPuro, c.ValAcqOneri, c.QtaVend, c.QtaResi, c.QtaRettTrasf,
               u.qtafin,
               u.puro_unit * u.qtafin, u.oneri_unit * u.qtafin,
               u.puro_unit, u.oneri_unit, (u.puro_unit + u.oneri_unit),
               wm.WAPCost,
               (u.puro_unit + u.oneri_unit) - wm.WAPCost
        FROM calc c
        CROSS APPLY (SELECT
                CASE WHEN (c.QtaIniz + c.QtaAcq) <> 0 THEN (c.ValPuroIniz  + c.ValAcqPuro)  / (c.QtaIniz + c.QtaAcq) ELSE 0 END AS puro_unit,
                CASE WHEN (c.QtaIniz + c.QtaAcq) <> 0 THEN (c.ValOneriIniz + c.ValAcqOneri) / (c.QtaIniz + c.QtaAcq) ELSE 0 END AS oneri_unit,
                (c.QtaIniz + c.QtaAcq - c.QtaVend + c.QtaResi + c.QtaRettTrasf) AS qtafin) u
        LEFT JOIN (
            SELECT LTRIM(RTRIM(Item)) AS Item, MAX(WAPCost) AS WAPCost
            FROM KODICEBAGNO_4.dbo.MA_ItemsWAP
            WHERE Storage = '' AND YEAR(EndPeriodDate) = @Anno AND MONTH(EndPeriodDate) = @m
            GROUP BY LTRIM(RTRIM(Item))
        ) wm ON wm.Item = c.Item
        WHERE c.QtaIniz <> 0 OR c.QtaAcq <> 0 OR c.QtaVend <> 0 OR c.QtaResi <> 0 OR c.QtaRettTrasf <> 0;

        SET @m += 1;
    END
END
GO

-- Esecuzione:  EXEC kodice.usp_ricalc_wap @Anno = 2026;
-- Controllo:   SELECT * FROM kodice.wap_ricalc WHERE Anno=2026 ORDER BY Item, Mese;
GO

-- =============================================================================
-- vw_costo_eff  — COSTO UNITARIO EFFICACE per articolo, con metodo per TIPO VALORIZZAZIONE.
-- -----------------------------------------------------------------------------
-- Valorizza OGNI articolo con il metodo che Mago usa per il suo ValuationType, piu' i ripieghi
-- per i casi che il ricalcolo dai movimenti non copre (rientri senza acquisto: trasferimenti,
-- rettifiche, produzione, imballaggi riutilizzati). Taglio valorizzazione = APRILE 2026
-- (a maggio mancano ancora gli oneri accessori da caricare).
--
-- ValuationType (MA_ItemsFiscalYearData): 11272206 = MPP (media ponderata di periodo / WAP),
--                                         11272194 = MEDIO (media annuale).
--
-- METODO PER TIPO:
--  * MEDIO (11272194): MEDIO_NOSTRO = media annuale "alla Mago" = (apertura valorizzata + acquisti
--      PURI del periodo) / (qta apertura + qta acquisti), su 2026 fino ad Aprile, SENZA oneri.
--      Ripiego: MEDIO_LASTCOST = MA_ItemsBalances.LastCost. (Il medio NON e' salvato in Mago: si
--      ricalcola; per gli articoli senza apertura/acquisti il valore d'apertura storico non e' in
--      SQL -> il report di inventario usa come ultimo ripiego il prezzo del report Mago = MEDIO_REPORT.)
--  * MPP / default: RICALCOLO_APR (ultimo costo wap_ricalc 2026 Mese<=4 con costo>0, split puro/oneri)
--      -> RISALITA_WAP (ultimo WAPCost>0 storico di MA_ItemsWAP, anche di anni fa: stessa risalita del
--      report Mago) -> MEDIO_NOSTRO -> MEDIO_LASTCOST.
-- NB: regola "costo che sopravvive a giacenza 0" — risalita/ripiego cercano sempre l'ultimo costo>0.
CREATE OR ALTER VIEW kodice.vw_costo_eff AS
WITH ric AS (   -- WAP: ultimo costo mensile del ricalcolo (puro+oneri)
    SELECT Item, PuroUnit, OneriUnit, (PuroUnit+OneriUnit) AS costo
    FROM (SELECT Item, PuroUnit, OneriUnit,
                 ROW_NUMBER() OVER (PARTITION BY Item ORDER BY Mese DESC) rn
          FROM kodice.wap_ricalc
          WHERE Anno=2026 AND Mese<=4 AND (PuroUnit+OneriUnit) > 0) t
    WHERE rn=1
),
med AS (        -- MEDIO annuale alla Mago: (apertura + acquisti PURI) / (qta tot), no oneri, 2026 Mese<=4
    SELECT Item, (vini + vacq) / NULLIF(qini + qacq, 0) AS medio
    FROM (SELECT Item,
                 MAX(CASE WHEN Mese = 1 THEN QtaIniz      END) AS qini,
                 MAX(CASE WHEN Mese = 1 THEN ValPuroIniz  END) AS vini,
                 SUM(CASE WHEN Mese <= 4 THEN QtaAcq      ELSE 0 END) AS qacq,
                 SUM(CASE WHEN Mese <= 4 THEN ValAcqPuro  ELSE 0 END) AS vacq
          FROM kodice.wap_ricalc WHERE Anno = 2026 GROUP BY Item) a
),
ris AS (        -- risalita: ultimo WAPCost>0 storico
    SELECT Item, WAPCost FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
               ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
        FROM KODICEBAGNO_4.dbo.MA_ItemsWAP
        WHERE Storage='' AND WAPCost > 0) t
    WHERE rn=1
),
lc AS (         -- ripiego: ultimo costo d'acquisto registrato per deposito
    SELECT LTRIM(RTRIM(Item)) AS Item, MAX(NULLIF(LastCost,0)) AS lastc
    FROM KODICEBAGNO_4.dbo.MA_ItemsBalances WHERE FiscalYear IN (2025,2026) GROUP BY LTRIM(RTRIM(Item))
),
vt AS (
    SELECT LTRIM(RTRIM(Item)) AS Item, MAX(ValuationType) AS vtype
    FROM KODICEBAGNO_4.dbo.MA_ItemsFiscalYearData WHERE FiscalYear = 2026 GROUP BY LTRIM(RTRIM(Item))
),
univ AS (SELECT Item FROM ric UNION SELECT Item FROM med UNION SELECT Item FROM ris UNION SELECT Item FROM lc),
calc AS (
    SELECT u.Item, v.vtype AS ValuationType,
           CASE
             WHEN v.vtype = 11272194 AND md.medio > 0  THEN 'MEDIO_NOSTRO'
             WHEN v.vtype = 11272194 AND lc.lastc > 0   THEN 'MEDIO_LASTCOST'
             WHEN r.costo  > 0                          THEN 'RICALCOLO_APR'
             WHEN rs.WAPCost > 0                        THEN 'RISALITA_WAP'
             WHEN md.medio > 0                          THEN 'MEDIO_NOSTRO'
             WHEN lc.lastc > 0                          THEN 'MEDIO_LASTCOST'
           END AS Fonte,
           r.costo AS ric_costo, r.OneriUnit AS ric_oneri, md.medio AS medio, rs.WAPCost AS risalita, lc.lastc AS lastc
    FROM univ u
    LEFT JOIN ric r  ON r.Item  = u.Item
    LEFT JOIN med md ON md.Item = u.Item
    LEFT JOIN ris rs ON rs.Item = u.Item
    LEFT JOIN lc     ON lc.Item = u.Item
    LEFT JOIN vt v   ON v.Item  = u.Item
)
SELECT Item, ValuationType, Fonte,
       CAST(CASE Fonte
              WHEN 'RICALCOLO_APR'  THEN ric_costo
              WHEN 'RISALITA_WAP'   THEN risalita
              WHEN 'MEDIO_NOSTRO'   THEN medio
              WHEN 'MEDIO_LASTCOST' THEN lastc
            END AS float) AS CostoEff,
       CAST(CASE WHEN Fonte = 'RICALCOLO_APR' THEN ric_oneri ELSE 0 END AS float) AS OneriUnit,
       CAST(CASE Fonte
              WHEN 'RICALCOLO_APR'  THEN ric_costo - ric_oneri
              WHEN 'RISALITA_WAP'   THEN risalita
              WHEN 'MEDIO_NOSTRO'   THEN medio
              WHEN 'MEDIO_LASTCOST' THEN lastc
            END AS float) AS PuroUnit
FROM calc
WHERE Fonte IS NOT NULL;
GO

-- =============================================================================
-- vw_bonifica_apertura — CANDIDATI alla bonifica dell'apertura 2026.
-- -----------------------------------------------------------------------------
-- PRINCIPIO (anti-fragilita'): l'universo e' TUTTO il magazzino di Mago, preso dal
-- dato di giacenza LIVE e DETERMINISTICO (MA_ItemsBalances.BookInv, OGNI deposito;
-- + fallback KLProgUbicazioni per gli articoli solo-ATRI). NON parte da un report,
-- da nomi o dagli input del ricalcolo: cosi' OGNI articolo con giacenza emerge per
-- forza, anche se assente dal nostro wap_ricalc o senza movimenti (es. imballi/EPAL).
-- Colonne: NostroDic2025 = WAPCost del nostro roll 2025-Dic (= apertura 2026); CostoSeed
-- = ultimo WAPCost Mago < 2026; MagoDic2025 = WAPCost Mago Dic-2025; LastCost = MA_ItemsBalances.
--   Categoria A = giacenza SENZA valore nostro (assente dal ricalcolo o NostroDic2025=0) -> priorita'.
--   Categoria B = NostroDic2025 diverge > 15% dal CostoSeed -> costo da certificare.
--   InRicalcolo = 0 se l'articolo NON e' nel nostro wap_ricalc (MANCANTE) -> caso piu' grave.
-- Override suggerito = nostro Dic-2025 (se >0) -> LastCost -> Mago -> 0 (=DA DEFINIRE a mano).
-- La presenza di una riga in kodice.wap_apertura_override significa "gia' certificato".
CREATE OR ALTER VIEW kodice.vw_bonifica_apertura AS
WITH ap AS (   -- UNIVERSO = giacenza d'APERTURA 1/1/2026 di TUTTO il magazzino (ogni deposito).
               -- InitialBookInv (NON BookInv: quello e' la giacenza ATTUALE, include acquisti 2026).
               -- Cosi' entrano solo gli articoli con stock all'apertura (es. imballi/EPAL in ATRI) ed
               -- escono quelli con apertura 0 ma comprati nel 2026 (che prendono il costo dei loro acquisti).
    SELECT Item, SUM(q) AS qty FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, SUM(InitialBookInv) AS q
        FROM KODICEBAGNO_4.dbo.MA_ItemsBalances WHERE FiscalYear = 2026 GROUP BY LTRIM(RTRIM(Item))
        UNION ALL  -- articoli SOLO in ubicazioni ATRI, non presenti in MA_ItemsBalances
        SELECT LTRIM(RTRIM(Articolo)), SUM(QtaIniziale)
        FROM KODICEBAGNO_4.dbo.KLProgUbicazioni
        WHERE Esercizio = 2026 AND LTRIM(RTRIM(Articolo)) NOT IN
              (SELECT LTRIM(RTRIM(Item)) FROM KODICEBAGNO_4.dbo.MA_ItemsBalances WHERE FiscalYear = 2026)
        GROUP BY LTRIM(RTRIM(Articolo))
    ) t GROUP BY Item HAVING SUM(q) > 0
),
seed AS (
    SELECT Item, WAPCost FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
               ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
        FROM KODICEBAGNO_4.dbo.MA_ItemsWAP WHERE Storage='' AND WAPCost>0 AND EndPeriodDate < '2026-01-01') t WHERE rn=1
),
-- NostroDic2025 = ULTIMO costo 2025 noto (NON forzatamente Dicembre): se la giacenza si azzera a
-- meta' anno il roll non ha righe dopo, ma il costo "sopravvive a giacenza 0" -> prendi l'ultimo mese con costo>0.
n25 AS (
    SELECT Item, WAPCost_ricalc, QtaFin FROM (
        SELECT Item, WAPCost_ricalc, QtaFin,
               ROW_NUMBER() OVER (PARTITION BY Item ORDER BY Mese DESC) rn
        FROM kodice.wap_ricalc WHERE Anno=2025 AND WAPCost_ricalc > 0
    ) t WHERE rn=1
),
magoD AS (SELECT LTRIM(RTRIM(Item)) AS Item, MAX(WAPCost) AS WAPCost
          FROM KODICEBAGNO_4.dbo.MA_ItemsWAP WHERE Storage='' AND YEAR(EndPeriodDate)=2025 AND MONTH(EndPeriodDate)=12 GROUP BY LTRIM(RTRIM(Item))),
lc AS (SELECT LTRIM(RTRIM(Item)) AS Item, MAX(NULLIF(LastCost,0)) AS lastc
       FROM KODICEBAGNO_4.dbo.MA_ItemsBalances WHERE FiscalYear IN (2025,2026) GROUP BY LTRIM(RTRIM(Item))),
calc AS (
    SELECT ap.Item, i.Description AS Descrizione, ap.qty AS Giacenza,
           CASE WHEN n.Item IS NULL THEN 0 ELSE 1 END AS InRicalcolo,
           ISNULL(s.WAPCost,0) AS CostoSeed, ISNULL(n.WAPCost_ricalc,0) AS NostroDic2025,
           ISNULL(md.WAPCost,0) AS MagoDic2025, ISNULL(lc.lastc,0) AS LastCost,
           CASE WHEN ISNULL(n.WAPCost_ricalc,0) <= 0 THEN 'A'   -- giacenza senza valore nostro (assente o costo 0)
                WHEN s.WAPCost > 0 AND ABS(n.WAPCost_ricalc - s.WAPCost) > 0.15 * s.WAPCost THEN 'B'
                ELSE NULL END AS Categoria,
           CASE WHEN ISNULL(n.WAPCost_ricalc,0) > 0 THEN n.WAPCost_ricalc
                WHEN ISNULL(lc.lastc,0) > 0 THEN lc.lastc
                ELSE ISNULL(md.WAPCost,0) END AS OverrideSuggerito,
           ov.CostoPuroUnit AS OverrideAttuale, ov.Fonte AS OverrideFonte, ov.Nota AS OverrideNota
    FROM ap
    LEFT JOIN seed s  ON s.Item  = ap.Item
    LEFT JOIN n25 n   ON n.Item  = ap.Item
    LEFT JOIN magoD md ON md.Item = ap.Item
    LEFT JOIN lc      ON lc.Item = ap.Item
    LEFT JOIN kodice.wap_apertura_override ov ON ov.Item = ap.Item AND ov.Anno = 2026
    LEFT JOIN KODICEBAGNO_4.dbo.MA_Items i ON LTRIM(RTRIM(i.Item)) = ap.Item
)
SELECT * FROM calc WHERE Categoria IS NOT NULL OR OverrideAttuale IS NOT NULL;
GO
