-- =============================================================================
-- doc_trasporto.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- Vista di supporto al COSTO DI TRASPORTO: per ogni DOCUMENTO di vendita gia' caricato
-- (src.righe_vendita) calcola le grandezze che servono alla STIMA per fascia di peso:
--   * peso_doc : peso totale spedito, KIT ESPLOSI (il codice kit di solito non ha peso:
--                si sommano i pesi dei COMPONENTI da kodice.vw_distinta);
--   * area     : ITALIA / ESTERO (da MA_SaleDoc.CountryOfDestination, ISO2: IT = Italia);
--   * canale   : il canale di vendita (categoria cliente: Amazon, Leroy Merlin, BTOB...);
--   * data_doc : data del documento (per scegliere la riga di config valida a quella data).
-- Il peso usa MA_ItemsGoodsData.GrossWeight (peso lordo). Dove manca, vale 0 (stima per
-- difetto: il livello 1 e' comunque il ripiego, subentra il dato reale appena disponibile).
-- =============================================================================
USE CDG_QV;
GO

CREATE OR ALTER VIEW kodice.vw_doc_trasporto
AS
SELECT
    v.anno, v.mese, v.sale_doc_id,
    CAST(SUM(ABS(v.quantita) * COALESCE(k.peso_kit, g.GrossWeight, 0)) AS DECIMAL(18,3)) AS peso_doc,
    MAX(CASE WHEN ISNULL(LTRIM(RTRIM(sd.CountryOfDestination)),'') IN ('','IT') THEN N'ITALIA' ELSE N'ESTERO' END) AS area,
    MAX(COALESCE(NULLIF(LTRIM(RTRIM(ctg.Notes)),''), opt.Category, N'(n/d)')) AS canale,
    MAX(CAST(sd.DocumentDate AS date)) AS data_doc
FROM src.righe_vendita AS v
JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS sd ON sd.SaleDocId = v.sale_doc_id
LEFT JOIN KODICEBAGNO_4.dbo.MA_ItemsGoodsData AS g ON LTRIM(RTRIM(g.Item)) = v.codice_articolo
LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions AS opt
       ON opt.Customer = sd.CustSupp AND opt.CustSuppType = sd.CustSuppType
LEFT JOIN KODICEBAGNO_4.dbo.MA_CustomerCtg AS ctg ON ctg.Category = opt.Category
OUTER APPLY (   -- se l'articolo e' un kit: peso = somma dei componenti esplosi (un livello)
    SELECT SUM(dd.Qty * ISNULL(gc.GrossWeight, 0)) AS peso_kit
    FROM kodice.vw_distinta AS dd
    LEFT JOIN KODICEBAGNO_4.dbo.MA_ItemsGoodsData AS gc ON LTRIM(RTRIM(gc.Item)) = LTRIM(RTRIM(dd.Component))
    WHERE LTRIM(RTRIM(dd.BOM)) = v.codice_articolo
) AS k
GROUP BY v.anno, v.mese, v.sale_doc_id;
GO
