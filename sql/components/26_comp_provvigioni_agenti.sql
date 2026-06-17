-- =============================================================================
-- 26_comp_provvigioni_agenti.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: PROVVIGIONI_AGENTI (livello 2, gruppo "Costi commerciali") —
--   provvigioni riconosciute agli AGENTI di vendita (tipicamente sul canale B2B).
--   Stessa logica validata dell'oracolo (Split_Costi_Variabili, campo
--   ImpProvvigioniAgenti) — vedi docs/riferimento-split-costi-variabili.md.
--
-- LOGICA: a differenza delle commissioni marketplace NON e' una %: e' un IMPORTO gia'
--   calcolato da Mago per riga (MA_SaleDocDetail.SalespersonComm). Lo prendiamo cosi'
--   com'e', col segno del documento (fatture +, note di credito -). Nessun azzeramento
--   sulle note di credito (la provvigione agente segue il documento).
--
-- CONTRATTO: DELETE delle proprie righe del periodo + INSERT. Solo righe con provvigione <> 0.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_PROVVIGIONI_AGENTI
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'PROVVIGIONI_AGENTI';

    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'PROVVIGIONI_AGENTI',
        CAST( det.SalespersonComm
              * CASE WHEN doc.DocumentType = N'3407876' THEN -1 ELSE 1 END
              AS DECIMAL(18,2)) AS importo,
        N'Provvigione agente di vendita' AS origine
    FROM src.righe_vendita AS r
    JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS doc
         ON doc.SaleDocId = r.sale_doc_id
    JOIN KODICEBAGNO_4.dbo.MA_SaleDocDetail AS det
         ON det.SaleDocId = r.sale_doc_id AND det.Line = r.line
    WHERE r.anno = @anno AND r.mese = @mese
      AND det.SalespersonComm <> 0;
END
GO
