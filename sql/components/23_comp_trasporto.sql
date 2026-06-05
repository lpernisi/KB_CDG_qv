-- =============================================================================
-- 23_comp_trasporto.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: TRASPORTO (livello 3) — spedizione, allocata per DRIVER (peso/volume),
-- non spalmata a fatturato. Sorgente: DB 'trasporti' (fatture vettori).
-- STUB: mostra il pattern dell'allocazione per driver, da completare (-- ADATTA).
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_TRASPORTO
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'TRASPORTO';

    -- Pattern di allocazione per driver (esempio: peso di riga / peso totale documento):
    --   costo_trasporto_riga = costo_trasporto_documento * (peso_riga / peso_doc)
    -- I pesi e il costo vettore vanno presi dalle sorgenti reali (-- ADATTA).
    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'TRASPORTO',
        -- ADATTA: costo vettore del documento * quota driver della riga.
        CAST(NULL AS DECIMAL(18,2)) AS importo,
        N'-- ADATTA: trasporti.dbo.KB_FattureVettori, allocato per driver' AS origine
    FROM src.righe_vendita AS r
    WHERE r.anno = @anno AND r.mese = @mese;
END
GO
