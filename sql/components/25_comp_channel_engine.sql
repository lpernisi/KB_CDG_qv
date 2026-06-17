-- =============================================================================
-- 25_comp_channel_engine.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: CHANNEL_ENGINE (livello 2, gruppo "Costi commerciali") —
--   costo della PIATTAFORMA DI INTEGRAZIONE MARKETPLACE (ChannelEngine), una % del
--   venduto sui canali marketplace. Stessa logica validata dell'oracolo
--   (Split_Costi_Variabili, campo CostoChannelEngine) — vedi
--   docs/riferimento-split-costi-variabili.md.
--
-- LOGICA: per ogni riga di MERCE, costo = aliquota % del CANALE x ricavo di riga.
--   - Aliquota in kb_TabProvvigioniVendita.CostiChannelEngine (tipicamente 1,2% sui
--     marketplace, 0 su B2B/canali diretti).
--   - Stessa base (ricavo_netto), canale, segno e regole-NC del componente PROVVIGIONI.
--   - Parte dal 2022-04-01 (prima il servizio non esisteva): righe precedenti = nessun costo.
--
-- CONTRATTO: DELETE delle proprie righe del periodo + INSERT. Solo righe con importo <> 0.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_CHANNEL_ENGINE
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'CHANNEL_ENGINE';

    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'CHANNEL_ENGINE',
        CAST( provv.CostiChannelEngine / 100.0 * r.ricavo_netto AS DECIMAL(18,2)) AS importo,
        N'Piattaforma marketplace (ChannelEngine) '
          + REPLACE(CONVERT(NVARCHAR(20), CAST(provv.CostiChannelEngine AS DECIMAL(9,2))), N'.', N',')
          + N'% — canale ' + provv.Descrizione AS origine
    FROM src.righe_vendita AS r
    JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS doc
         ON doc.SaleDocId = r.sale_doc_id
    LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions AS opt
         ON opt.Customer = doc.CustSupp AND opt.CustSuppType = 3211264
    JOIN KODICEBAGNO_4.dbo.kb_TabProvvigioniVendita AS provv
         ON provv.CategoriaCliente = CASE WHEN doc.CustSupp = N'B2B0415' THEN doc.CustSupp ELSE opt.Category END
    WHERE r.anno = @anno AND r.mese = @mese
      AND r.tipo_articolo <> N'SERVIZIO'
      AND provv.CostiChannelEngine <> 0
      AND r.ricavo_netto <> 0
      AND doc.DocumentDate >= '20220401'            -- ChannelEngine attivo dal 2022-04
      AND NOT (doc.DocumentType = N'3407876' AND doc.InvoicingAccGroup NOT IN ('A','I','C'));
END
GO
