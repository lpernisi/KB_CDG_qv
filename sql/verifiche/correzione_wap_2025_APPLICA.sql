-- =============================================================================
-- correzione_wap_2025_APPLICA.sql   (eseguire su KODICEBAGNO_4Test, in SSMS)
-- -----------------------------------------------------------------------------
-- *** SCRIVE su MA_ItemsWAP (tabella di sistema Mago). OPERAZIONE DELICATA. ***
--
-- Scopo: sanare la QUANTITA' di fine @AnnoWap di MA_ItemsWAP (riga Dic, Storage='')
-- portandola alla giacenza REALE, cosi' il roll-forward 2026 non va in negativo e
-- il motore continua a calcolare il WAPCost. IL COSTO NON SI TOCCA.
--
--   QUANTITA' (FinalQty) := SUM(MA_ItemsBalances.BookInv) FY @AnnoWap   [giacenza reale,
--                           concorda con KLProgUbicazioni; NON usare InitialBookInv]
--   COSTO (WAPCost)      := INVARIATO  (UPDATE: resta quello della riga; INSERT: costo di
--                           risalita = ultima riga WAP <= Dic). MAI ricalcolato/sostituito.
--   VALORE (FinalValue)  := FinalQty * WAPCost.
--
-- Scrive il Dic-@AnnoWap per TUTTI gli articoli gestiti a WAP (UPDATE se esiste, INSERT
-- altrimenti) -> ripristino pulito dal backup.
--
-- SICUREZZE: @Applica=0 -> DRY-RUN (rollback + conteggi). @Applica=1 -> COMMIT.
--            STEP 0 backup OBBLIGATORIO prima di @Applica=1.
-- =============================================================================

USE KODICEBAGNO_4Test;   -- <-- DB di test
SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

DECLARE @AnnoEs   SMALLINT = 2026;
DECLARE @AnnoWap  SMALLINT = 2025;
DECLARE @Applica  BIT      = 0;     -- 0 = dry-run (rollback) | 1 = applica (commit)
DECLARE @Start DATE = DATEFROMPARTS(@AnnoWap, 12, 1);
DECLARE @End   DATE = EOMONTH(DATEFROMPARTS(@AnnoWap, 12, 1));

-- ---- STEP 0 — BACKUP (decommenta ed esegui UNA volta prima di @Applica=1) ----
-- IF OBJECT_ID('dbo.MA_ItemsWAP_BAK_2025','U') IS NULL
--     SELECT * INTO dbo.MA_ItemsWAP_BAK_2025 FROM dbo.MA_ItemsWAP;
-- GO

-- ---- Valori da scrivere: qty reale (BookInv) + costo di risalita (per gli INSERT) ----
IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;
;WITH stock AS (   -- giacenza REALE fine @AnnoWap
    SELECT LTRIM(RTRIM(Item)) AS Item, SUM(BookInv) AS q
    FROM dbo.MA_ItemsBalances WHERE FiscalYear = @AnnoWap
    GROUP BY LTRIM(RTRIM(Item))
),
wlast AS (         -- ultima riga WAP <= Dic-@AnnoWap (per il costo di risalita degli INSERT)
    SELECT Item, WAPCost FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
               ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
        FROM dbo.MA_ItemsWAP WHERE Storage='' AND EndPeriodDate < DATEFROMPARTS(@AnnoEs,1,1)
    ) t WHERE rn = 1
)
SELECT s.Item, CAST(s.q AS float) AS qty, CAST(w.WAPCost AS float) AS costo_risalita
INTO #c
FROM stock s
JOIN wlast w ON w.Item = s.Item;     -- solo articoli gestiti a WAP (hanno un costo)

DECLARE @tot INT = (SELECT COUNT(*) FROM #c);

BEGIN TRAN;

-- ---- STEP 1 — UPDATE righe Dic-@AnnoWap esistenti: solo QUANTITA' e VALORE, COSTO INVARIATO
UPDATE w
   SET w.FinalQty = c.qty,
       w.FinalValue = c.qty * w.WAPCost,      -- usa il costo PROPRIO della riga (non lo cambia)
       w.TBModified = SYSDATETIME()
FROM dbo.MA_ItemsWAP w
JOIN #c c ON LTRIM(RTRIM(w.Item)) = c.Item
WHERE w.Storage='' AND YEAR(w.EndPeriodDate)=@AnnoWap AND MONTH(w.EndPeriodDate)=12;
DECLARE @n_upd INT = @@ROWCOUNT;

-- ---- STEP 2 — INSERT righe Dic-@AnnoWap mancanti (seme: Initial=Final; costo = risalita)
DECLARE @Company INT = (SELECT TOP 1 TBCompanyID  FROM dbo.MA_ItemsWAP WHERE Storage='' AND YEAR(EndPeriodDate)=@AnnoWap AND MONTH(EndPeriodDate)=12);
DECLARE @Uid     INT = (SELECT TOP 1 TBModifiedID FROM dbo.MA_ItemsWAP WHERE Storage='' AND YEAR(EndPeriodDate)=@AnnoWap AND MONTH(EndPeriodDate)=12);

INSERT INTO dbo.MA_ItemsWAP
    (StartingPeriodDate, Item, Storage, EndPeriodDate, InitialQty, InitialValue,
     FinalQty, FinalValue, WAPCost, TBCreated, TBModified, TBCreatedID, TBModifiedID, TBCompanyID)
SELECT @Start, c.Item, '', @End, c.qty, c.qty * c.costo_risalita, c.qty, c.qty * c.costo_risalita, c.costo_risalita,
       SYSDATETIME(), SYSDATETIME(), @Uid, @Uid, @Company
FROM #c c
WHERE c.costo_risalita IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dbo.MA_ItemsWAP w
                  WHERE LTRIM(RTRIM(w.Item))=c.Item AND w.Storage=''
                    AND YEAR(w.EndPeriodDate)=@AnnoWap AND MONTH(w.EndPeriodDate)=12);
DECLARE @n_ins INT = @@ROWCOUNT;

PRINT CONCAT('Articoli WAP elaborati: ', @tot, ' | UPDATE: ', @n_upd, ' | INSERT: ', @n_ins,
             ' | modalita: ', CASE WHEN @Applica=1 THEN 'APPLICA (COMMIT)' ELSE 'DRY-RUN (ROLLBACK)' END);

IF @Applica = 1 BEGIN COMMIT TRAN END ELSE BEGIN ROLLBACK TRAN END;
DROP TABLE #c;
GO

-- =============================================================================
-- STEP 3 (manuale): consolidamento Mago dei mesi @AnnoEs (globale).
-- STEP 4 — RIPRISTINO Dic-@AnnoWap dal backup (riporta il 2025 ufficiale), DOPO il
--          consolidamento. Decommenta:
-- BEGIN TRAN;
--   DELETE w FROM dbo.MA_ItemsWAP w WHERE w.Storage='' AND YEAR(w.EndPeriodDate)=2025 AND MONTH(w.EndPeriodDate)=12;
--   INSERT INTO dbo.MA_ItemsWAP SELECT * FROM dbo.MA_ItemsWAP_BAK_2025 b WHERE b.Storage='' AND YEAR(b.EndPeriodDate)=2025 AND MONTH(b.EndPeriodDate)=12;
-- COMMIT TRAN;
-- =============================================================================
