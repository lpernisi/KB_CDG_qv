-- =============================================================================
-- wap_ricalc.sql   (oggetti in CDG_QV, schema kodice — eseguire in SSMS o via pipeline)
-- -----------------------------------------------------------------------------
-- RICALCOLO PARALLELO del WAP (NON tocca MA_ItemsWAP di Mago), con split del costo in
-- ACQUISTO PURO + ONERI ACCESSORI, partendo dalla giacenza REALE ricostruita.
--
-- Per ogni (Item, Mese) dell'anno @Anno, a rotazione da Gennaio:
--   Giacenza iniziale Gen = SUM(KLProgUbicazioni.QtaIniziale Eserc.@Anno)              [deposito ATRI, fisico]
--                         + SUM(MA_ItemsBalances.InitialBookInv FY @Anno, Storage<>'ATRI')
--   Valore iniziale Gen   = QtaIniz * WAPCost(ultima riga MA_ItemsWAP < Gen @Anno)      [risalita Dic anno prec.]
--   Split iniziale (opz. a): ValPuroIniz = valore;  ValOneriIniz = 0  (oneri si accumulano dai carichi)
--   Carichi/oneri dai MOVIMENTI (per WAPMovementType):
--     ACQUISTI (2032533505): +Qty, +valore (puro = LineAmount non AGGDAZI/IMPORT; oneri = LineAmount AGGDAZI/IMPORT)
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

CREATE OR ALTER PROCEDURE kodice.usp_ricalc_wap
    @Anno    smallint,
    @MeseMax tinyint = 12
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM kodice.wap_ricalc WHERE Anno = @Anno;

    -- ---- SEED: giacenza iniziale reale + WAPCost di risalita (Dic anno precedente) ----
    IF OBJECT_ID('tempdb..#seed') IS NOT NULL DROP TABLE #seed;
    ;WITH ubi AS (
        SELECT LTRIM(RTRIM(Articolo)) AS Item, SUM(QtaIniziale) AS q
        FROM KODICEBAGNO_4.dbo.KLProgUbicazioni WHERE Esercizio = @Anno GROUP BY LTRIM(RTRIM(Articolo))
    ),
    baln AS (
        SELECT LTRIM(RTRIM(Item)) AS Item, SUM(InitialBookInv) AS q
        FROM KODICEBAGNO_4.dbo.MA_ItemsBalances WHERE FiscalYear = @Anno AND Storage <> 'ATRI' GROUP BY LTRIM(RTRIM(Item))
    ),
    seedw AS (
        SELECT Item, WAPCost FROM (
            SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
                   ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
            FROM KODICEBAGNO_4.dbo.MA_ItemsWAP WHERE Storage = '' AND EndPeriodDate < DATEFROMPARTS(@Anno,1,1)
        ) t WHERE rn = 1
    ),
    mov AS (
        SELECT DISTINCT LTRIM(RTRIM(d.Item)) AS Item
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
        WHERE YEAR(h.PostingDate) = @Anno
    ),
    univ AS (SELECT Item FROM ubi UNION SELECT Item FROM baln UNION SELECT Item FROM mov UNION SELECT Item FROM seedw)
    SELECT u.Item,
           ISNULL(ubi.q,0) + ISNULL(baln.q,0) AS qty,
           ISNULL(sw.WAPCost,0)               AS wcost
    INTO #seed
    FROM univ u
    LEFT JOIN ubi  ON ubi.Item  = u.Item
    LEFT JOIN baln ON baln.Item = u.Item
    LEFT JOIN seedw sw ON sw.Item = u.Item;

    DECLARE @m tinyint = 1;
    WHILE @m <= @MeseMax
    BEGIN
        ;WITH prev AS (
            SELECT Item, QtaFin, ValPuroFin, ValOneriFin
            FROM kodice.wap_ricalc WHERE Anno = @Anno AND Mese = @m - 1
        ),
        iniz AS (
            SELECT s.Item,
                   CASE WHEN @m = 1 THEN s.qty           ELSE ISNULL(p.QtaFin,0)      END AS QtaIniz,
                   CASE WHEN @m = 1 THEN s.qty * s.wcost ELSE ISNULL(p.ValPuroFin,0)  END AS ValPuroIniz,
                   CASE WHEN @m = 1 THEN 0.0             ELSE ISNULL(p.ValOneriFin,0) END AS ValOneriIniz
            FROM #seed s LEFT JOIN prev p ON p.Item = s.Item
        ),
        mv AS (
            SELECT LTRIM(RTRIM(d.Item)) AS Item,
                   SUM(CASE WHEN h.WAPMovementType = 2032533505 THEN d.Qty ELSE 0 END) AS QtaAcq,
                   SUM(CASE WHEN h.InvRsn IN ('AGGDAZI','IMPORT') THEN d.LineAmount ELSE 0 END) AS ValAcqOneri,
                   SUM(CASE WHEN h.WAPMovementType = 2032533505 AND h.InvRsn NOT IN ('AGGDAZI','IMPORT') THEN d.LineAmount ELSE 0 END) AS ValAcqPuro,
                   SUM(CASE WHEN h.WAPMovementType = 2032533506 THEN d.Qty ELSE 0 END) AS QtaVend,
                   SUM(CASE WHEN h.WAPMovementType = 2032533509 THEN d.Qty ELSE 0 END) AS QtaResi
            FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
            WHERE YEAR(h.PostingDate) = @Anno AND MONTH(h.PostingDate) = @m
            GROUP BY LTRIM(RTRIM(d.Item))
        ),
        calc AS (
            SELECT i.Item, i.QtaIniz, i.ValPuroIniz, i.ValOneriIniz,
                   ISNULL(mv.QtaAcq,0) AS QtaAcq, ISNULL(mv.ValAcqPuro,0) AS ValAcqPuro, ISNULL(mv.ValAcqOneri,0) AS ValAcqOneri,
                   ISNULL(mv.QtaVend,0) AS QtaVend, ISNULL(mv.QtaResi,0) AS QtaResi
            FROM iniz i LEFT JOIN mv ON mv.Item = i.Item
        )
        INSERT INTO kodice.wap_ricalc
            (Item,Anno,Mese,QtaIniz,ValPuroIniz,ValOneriIniz,QtaAcq,ValAcqPuro,ValAcqOneri,QtaVend,QtaResi,
             QtaFin,ValPuroFin,ValOneriFin,PuroUnit,OneriUnit,WAPCost_ricalc,WAPCost_Mago,Delta)
        SELECT c.Item, @Anno, @m, c.QtaIniz, c.ValPuroIniz, c.ValOneriIniz, c.QtaAcq, c.ValAcqPuro, c.ValAcqOneri, c.QtaVend, c.QtaResi,
               u.qtafin,
               u.puro_unit * u.qtafin, u.oneri_unit * u.qtafin,
               u.puro_unit, u.oneri_unit, (u.puro_unit + u.oneri_unit),
               wm.WAPCost,
               (u.puro_unit + u.oneri_unit) - wm.WAPCost
        FROM calc c
        CROSS APPLY (SELECT
                CASE WHEN (c.QtaIniz + c.QtaAcq) <> 0 THEN (c.ValPuroIniz  + c.ValAcqPuro)  / (c.QtaIniz + c.QtaAcq) ELSE 0 END AS puro_unit,
                CASE WHEN (c.QtaIniz + c.QtaAcq) <> 0 THEN (c.ValOneriIniz + c.ValAcqOneri) / (c.QtaIniz + c.QtaAcq) ELSE 0 END AS oneri_unit,
                (c.QtaIniz + c.QtaAcq - c.QtaVend + c.QtaResi) AS qtafin) u
        LEFT JOIN (
            SELECT LTRIM(RTRIM(Item)) AS Item, MAX(WAPCost) AS WAPCost
            FROM KODICEBAGNO_4.dbo.MA_ItemsWAP
            WHERE Storage = '' AND YEAR(EndPeriodDate) = @Anno AND MONTH(EndPeriodDate) = @m
            GROUP BY LTRIM(RTRIM(Item))
        ) wm ON wm.Item = c.Item
        WHERE c.QtaIniz <> 0 OR c.QtaAcq <> 0 OR c.QtaVend <> 0 OR c.QtaResi <> 0;

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
