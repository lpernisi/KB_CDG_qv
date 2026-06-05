-- =============================================================================
-- correzione_wap_negativi.sql   (eseguire su [kodicebagno_4-test], in SSMS)
-- -----------------------------------------------------------------------------
-- *** SCRIVE su MA_ItemsWAP. Intervento CHIRURGICO e MINIMO. ***
--
-- Bersaglio: SOLO gli articoli il cui WAP e' andato in NEGATIVO (ultima riga
-- Storage='' con FinalQty < 0) -> sono quelli su cui il motore si e' fermato.
-- Per QUESTI sola la riga Dic-@AnnoWap viene portata alla giacenza REALE positiva,
-- cosi' il roll-forward 2026 riparte. IL COSTO NON SI TOCCA MAI.
--
--   QUANTITA' (FinalQty) := SUM(MA_ItemsBalances.BookInv) FY @AnnoWap  [per i negativi
--                           concorda con KLProgUbicazioni -> affidabile]
--   COSTO (WAPCost)      := INVARIATO (UPDATE: resta quello della riga; INSERT: risalita)
--   VALORE (FinalValue)  := FinalQty * WAPCost
--
-- NON tocca gli articoli sani (WAP positivo), es. 02030110430021.
-- SICUREZZE: @Applica=0 -> DRY-RUN (rollback + elenco). @Applica=1 -> COMMIT.
--            STEP 0 backup OBBLIGATORIO prima di @Applica=1.
-- =============================================================================

USE [kodicebagno_4-test];
SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

DECLARE @AnnoEs   SMALLINT = 2026;
DECLARE @AnnoWap  SMALLINT = 2025;
DECLARE @Applica  BIT      = 0;     -- 0 = dry-run | 1 = applica
DECLARE @Start DATE = DATEFROMPARTS(@AnnoWap,12,1);
DECLARE @End   DATE = EOMONTH(DATEFROMPARTS(@AnnoWap,12,1));

-- ---- STEP 0 — BACKUP (decommenta ed esegui UNA volta prima di @Applica=1) ----
-- IF OBJECT_ID('dbo.MA_ItemsWAP_BAK_2025','U') IS NULL
--     SELECT * INTO dbo.MA_ItemsWAP_BAK_2025 FROM dbo.MA_ItemsWAP;
-- GO

IF OBJECT_ID('tempdb..#t') IS NOT NULL DROP TABLE #t;
;WITH ult AS (   -- ultima riga WAP per articolo
    SELECT Item, FinalQty FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, FinalQty,
               ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
        FROM dbo.MA_ItemsWAP WHERE Storage=''
    ) t WHERE rn=1
),
stock AS (       -- giacenza reale fine @AnnoWap
    SELECT LTRIM(RTRIM(Item)) AS Item, SUM(BookInv) AS q
    FROM dbo.MA_ItemsBalances WHERE FiscalYear=@AnnoWap GROUP BY LTRIM(RTRIM(Item))
),
risal AS (       -- costo di risalita (per gli INSERT)
    SELECT Item, WAPCost FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
               ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
        FROM dbo.MA_ItemsWAP WHERE Storage='' AND EndPeriodDate < DATEFROMPARTS(@AnnoEs,1,1)
    ) t WHERE rn=1
)
SELECT u.Item,
       CAST(ISNULL(s.q,0) AS float) AS qty,          -- giacenza reale (>=0)
       CAST(r.WAPCost AS float)     AS costo_risalita,
       CAST(u.FinalQty AS float)    AS wap_ultimo_neg
INTO #t
FROM ult u
LEFT JOIN stock s ON s.Item = u.Item
LEFT JOIN risal r ON r.Item = u.Item
WHERE u.FinalQty < 0            -- SOLO i negativi
  AND ISNULL(s.q,0) >= 0;       -- giacenza reale non negativa da ripristinare

DECLARE @tot INT = (SELECT COUNT(*) FROM #t);

-- elenco di cosa verrebbe fatto (sempre visibile)
SELECT t.Item, it.Description, t.wap_ultimo_neg AS wap_attuale_neg, t.qty AS qty_reale_bookinv,
       CAST(t.costo_risalita AS DECIMAL(18,4)) AS costo_invariato
FROM #t t LEFT JOIN dbo.MA_Items it ON LTRIM(RTRIM(it.Item))=t.Item
ORDER BY t.wap_ultimo_neg;

BEGIN TRAN;

-- STEP 1 — UPDATE righe Dic-@AnnoWap esistenti: SOLO quantita' e valore, COSTO INVARIATO
UPDATE w
   SET w.FinalQty = c.qty,
       w.FinalValue = c.qty * w.WAPCost,   -- costo PROPRIO della riga (non cambiato)
       w.TBModified = SYSDATETIME()
FROM dbo.MA_ItemsWAP w
JOIN #t c ON LTRIM(RTRIM(w.Item)) = c.Item
WHERE w.Storage='' AND YEAR(w.EndPeriodDate)=@AnnoWap AND MONTH(w.EndPeriodDate)=12;
DECLARE @n_upd INT = @@ROWCOUNT;

-- STEP 2 — INSERT righe Dic-@AnnoWap mancanti (costo = risalita)
DECLARE @Company INT = (SELECT TOP 1 TBCompanyID  FROM dbo.MA_ItemsWAP WHERE Storage='' AND YEAR(EndPeriodDate)=@AnnoWap AND MONTH(EndPeriodDate)=12);
DECLARE @Uid     INT = (SELECT TOP 1 TBModifiedID FROM dbo.MA_ItemsWAP WHERE Storage='' AND YEAR(EndPeriodDate)=@AnnoWap AND MONTH(EndPeriodDate)=12);

INSERT INTO dbo.MA_ItemsWAP
    (StartingPeriodDate, Item, Storage, EndPeriodDate, InitialQty, InitialValue,
     FinalQty, FinalValue, WAPCost, TBCreated, TBModified, TBCreatedID, TBModifiedID, TBCompanyID)
SELECT @Start, c.Item, '', @End, c.qty, c.qty*c.costo_risalita, c.qty, c.qty*c.costo_risalita, c.costo_risalita,
       SYSDATETIME(), SYSDATETIME(), @Uid, @Uid, @Company
FROM #t c
WHERE c.costo_risalita IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dbo.MA_ItemsWAP w
                  WHERE LTRIM(RTRIM(w.Item))=c.Item AND w.Storage=''
                    AND YEAR(w.EndPeriodDate)=@AnnoWap AND MONTH(w.EndPeriodDate)=12);
DECLARE @n_ins INT = @@ROWCOUNT;

PRINT CONCAT('Articoli NEGATIVI elaborati: ', @tot, ' | UPDATE: ', @n_upd, ' | INSERT: ', @n_ins,
             ' | modalita: ', CASE WHEN @Applica=1 THEN 'APPLICA (COMMIT)' ELSE 'DRY-RUN (ROLLBACK)' END);

IF @Applica = 1 BEGIN COMMIT TRAN END ELSE BEGIN ROLLBACK TRAN END;
DROP TABLE #t;
GO

-- =============================================================================
-- STEP 3 (manuale): consolidamento Mago dei mesi @AnnoEs.
-- STEP 4 — ripristino Dic-@AnnoWap dal backup (per tornare indietro):
-- BEGIN TRAN;
--   DELETE w FROM dbo.MA_ItemsWAP w WHERE w.Storage='' AND YEAR(w.EndPeriodDate)=2025 AND MONTH(w.EndPeriodDate)=12;
--   INSERT INTO dbo.MA_ItemsWAP SELECT * FROM dbo.MA_ItemsWAP_BAK_2025 b WHERE b.Storage='' AND YEAR(b.EndPeriodDate)=2025 AND MONTH(b.EndPeriodDate)=12;
-- COMMIT TRAN;
-- =============================================================================
