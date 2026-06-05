-- =============================================================================
-- verifica_post_consolidamento.sql   (eseguire su KODICEBAGNO_4Test, in SSMS)
-- -----------------------------------------------------------------------------
-- Verifica l'effetto della correzione: segue mese per mese, nel @AnnoEs, la
-- FinalQty e il WAPCost degli articoli che OGGI sono "rotti" (qualche riga WAP a
-- FinalQty negativa). Lanciare DUE volte:
--   (A) PRIMA della correzione  -> baseline: si vedono qty negative / WAPCost fermo
--   (B) DOPO correzione + consolidamento -> qty positive, WAPCost che si aggiorna
-- SOLA LETTURA.
-- =============================================================================

USE KODICEBAGNO_4Test;   -- <-- DB di test
GO

DECLARE @AnnoEs SMALLINT = 2026;

-- watchlist: articoli con almeno una FinalQty negativa nel WAP (qualsiasi periodo)
;WITH rotti AS (
    SELECT DISTINCT LTRIM(RTRIM(Item)) AS Item
    FROM dbo.MA_ItemsWAP
    WHERE Storage = '' AND FinalQty < 0
)
SELECT
    LTRIM(RTRIM(w.Item)) AS Item,
    it.Description,
    CONVERT(date, w.EndPeriodDate)       AS fine_periodo,
    CAST(w.InitialQty  AS DECIMAL(18,2))  AS qta_iniz,
    CAST(w.FinalQty    AS DECIMAL(18,2))  AS qta_fin,
    CAST(w.WAPCost     AS DECIMAL(18,4))  AS wap_cost,
    CASE WHEN w.FinalQty < 0 THEN '<<< NEGATIVA' ELSE '' END AS flag
FROM dbo.MA_ItemsWAP w
JOIN rotti r ON r.Item = LTRIM(RTRIM(w.Item))
LEFT JOIN dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = LTRIM(RTRIM(w.Item))
WHERE w.Storage = '' AND YEAR(w.EndPeriodDate) = @AnnoEs
ORDER BY w.Item, w.EndPeriodDate;
GO

-- Riepilogo sintetico (confronto rapido baseline vs post):
SELECT
    COUNT(DISTINCT CASE WHEN FinalQty < 0 THEN LTRIM(RTRIM(Item)) END) AS articoli_con_qta_negativa_AnnoEs,
    SUM(CASE WHEN FinalQty < 0 THEN 1 ELSE 0 END)                       AS righe_negative_AnnoEs
FROM dbo.MA_ItemsWAP
WHERE Storage = '' AND YEAR(EndPeriodDate) = @AnnoEs;
GO
