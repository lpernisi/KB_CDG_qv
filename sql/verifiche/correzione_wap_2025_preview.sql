-- =============================================================================
-- correzione_wap_2025_preview.sql   (eseguire su KODICEBAGNO_4, in SSMS)
-- -----------------------------------------------------------------------------
-- ANTEPRIMA (SOLA LETTURA) della correzione dei saldi finali a fine @AnnoWap di
-- MA_ItemsWAP, propedeutica al ricalcolo dei costi @AnnoEs.
--
-- PRINCIPIO: si interviene SOLO sugli articoli la cui giacenza WAP "di risalita"
-- (ultima riga WAP <= fine @AnnoWap) NON coincide con la giacenza reale. Dove
-- coincide gia' (la maggior parte) NON si tocca nulla: Mago ha gia' riportato bene.
--
-- Per gli articoli da correggere:
--   QUANTITA' := giacenza reale (MA_ItemsBalances.InitialBookInv FY @AnnoEs)
--   COSTO     := WAPCost dell'ultima riga WAP (risalita) -> e' il costo Mago, lo si RIPORTA
--                senza modificarlo; SOLO se e' outlier (>@SogliaScost vs costo movimenti) o
--                mancante si usa il costo da movimenti d'acquisto, poi LastCost.
--   VALORE    := quantita' * costo.
--   AZIONE    := UPDATE se esiste la riga Dic-@AnnoWap, altrimenti INSERT (riga "seme").
--
-- COSTO da MOVIMENTI = SUM(MA_InventoryEntriesDetail.LineAmount)/SUM(Qty) sui movimenti
-- MA_InventoryEntries con WAPMovementType=2032533505 (acquisti; dazi/import gia' inclusi).
-- =============================================================================

USE KODICEBAGNO_4;
GO

DECLARE @AnnoEs      SMALLINT = 2026;
DECLARE @AnnoWap     SMALLINT = @AnnoEs - 1;
DECLARE @SogliaScost FLOAT    = 0.20;   -- oltre questa differenza il WAPCost e' "outlier" vs costo movimenti
DECLARE @TolQta      FLOAT    = 0.001;  -- differenza di quantita' sotto cui consideriamo "gia' corretto"

;WITH bal AS (   -- giacenza reale apertura @AnnoEs (= fine @AnnoWap) + LastCost di ripiego
    SELECT LTRIM(RTRIM(Item)) AS Item, SUM(InitialBookInv) AS q_real, MAX(LastCost) AS last_cost
    FROM dbo.MA_ItemsBalances WHERE FiscalYear = @AnnoEs GROUP BY LTRIM(RTRIM(Item))
),
mov AS (         -- costo da MOVIMENTI d'acquisto @AnnoWap (riferimento per gli outlier)
    SELECT LTRIM(RTRIM(d.Item)) AS Item,
           CASE WHEN SUM(d.Qty) <> 0 THEN SUM(d.LineAmount) / SUM(d.Qty) END AS costo_mov
    FROM dbo.MA_InventoryEntriesDetail d
    JOIN dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
    WHERE YEAR(h.PostingDate) = @AnnoWap AND h.WAPMovementType = 2032533505
    GROUP BY LTRIM(RTRIM(d.Item))
),
wlast AS (       -- RISALITA: ultima riga WAP totale con periodo <= fine @AnnoWap
    SELECT Item, EndPeriodDate, FinalQty, FinalValue, WAPCost FROM (
        SELECT LTRIM(RTRIM(Item)) AS Item, EndPeriodDate, FinalQty, FinalValue, WAPCost,
               ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
        FROM dbo.MA_ItemsWAP
        WHERE Storage = '' AND EndPeriodDate < DATEFROMPARTS(@AnnoEs, 1, 1)
    ) t WHERE rn = 1
),
dic AS (         -- esiste gia' la riga di Dic-@AnnoWap? (per decidere UPDATE vs INSERT)
    SELECT DISTINCT LTRIM(RTRIM(Item)) AS Item FROM dbo.MA_ItemsWAP
    WHERE Storage = '' AND YEAR(EndPeriodDate) = @AnnoWap AND MONTH(EndPeriodDate) = 12
),
calc AS (
    SELECT b.Item, b.q_real, b.last_cost, m.costo_mov,
           w.EndPeriodDate AS wap_periodo, w.FinalQty AS wap_qty, w.WAPCost AS wap_costo,
           w.FinalValue AS wap_valore,
           CAST(CASE
               WHEN w.WAPCost > 0 AND NOT (m.costo_mov > 0 AND ABS(w.WAPCost - m.costo_mov)/m.costo_mov > @SogliaScost)
                    THEN w.WAPCost                       -- costo Mago riportato (non modificato)
               WHEN m.costo_mov > 0 THEN m.costo_mov     -- WAPCost outlier/mancante -> costo movimenti
               WHEN w.WAPCost > 0   THEN w.WAPCost
               WHEN b.last_cost > 0 THEN b.last_cost
               ELSE NULL END AS DECIMAL(18,4)) AS costo_corretto,
           CASE
               WHEN w.WAPCost > 0 AND NOT (m.costo_mov > 0 AND ABS(w.WAPCost - m.costo_mov)/m.costo_mov > @SogliaScost)
                    THEN 'WAPCost risalita (riportato)'
               WHEN m.costo_mov > 0 THEN 'costo movimenti (WAPCost outlier/mancante)'
               WHEN w.WAPCost > 0   THEN 'WAPCost risalita'
               WHEN b.last_cost > 0 THEN 'LastCost (ripiego)'
               ELSE 'COSTO MANCANTE' END AS fonte_costo
    FROM bal b
    LEFT JOIN mov   m ON m.Item = b.Item
    LEFT JOIN wlast w ON w.Item = b.Item
    WHERE ABS(b.q_real) > 0.001
      AND (w.Item IS NULL OR ABS(b.q_real - w.FinalQty) > @TolQta)   -- SOLO discrepanti o senza WAP
)
SELECT
    c.Item,
    it.Description,
    CONVERT(date, c.wap_periodo) AS wap_ultimo_periodo,
    CAST(c.wap_qty AS DECIMAL(18,3))   AS wap_qty_risalita,
    CAST(c.wap_costo AS DECIMAL(18,4)) AS wap_costo_risalita,
    CAST(c.costo_mov AS DECIMAL(18,4)) AS costo_movimenti_rif,
    CAST(c.q_real AS DECIMAL(18,3))    AS qty_corretta,
    CAST(c.q_real - ISNULL(c.wap_qty,0) AS DECIMAL(18,3)) AS delta_qty,
    c.costo_corretto,
    CAST(ISNULL(c.wap_valore,0) AS DECIMAL(18,2))      AS valore_magazzino_attuale,   -- valore WAP riportato oggi
    CAST(c.q_real * c.costo_corretto AS DECIMAL(18,2)) AS valore_corretto,
    -- variazione teorica al valore di magazzino (somma questa colonna per il totale d'impatto):
    CAST(c.q_real * c.costo_corretto - ISNULL(c.wap_valore,0) AS DECIMAL(18,2)) AS delta_valore_magazzino,
    c.fonte_costo,
    CASE WHEN d.Item IS NOT NULL THEN 'UPDATE Dic-' + CAST(@AnnoWap AS varchar)
         WHEN c.wap_periodo IS NOT NULL THEN 'INSERT Dic-' + CAST(@AnnoWap AS varchar) + ' (risalita)'
         ELSE 'INSERT Dic-' + CAST(@AnnoWap AS varchar) + ' (no WAP, costo calcolato)' END AS azione
FROM calc c
LEFT JOIN dic d ON d.Item = c.Item
LEFT JOIN dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = c.Item
ORDER BY ABS(c.q_real - ISNULL(c.wap_qty,0)) DESC;
GO
