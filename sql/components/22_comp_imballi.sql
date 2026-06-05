-- =============================================================================
-- 22_comp_imballi.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: IMBALLI (livello 2) — costi di imballaggio, tipicamente per unita.
-- STUB da adattare. Quando e' pronta, in cfg.componenti metti attivo = 1.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_IMBALLI
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'IMBALLI';

    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'IMBALLI',
        -- ADATTA: costo imballo (es. quantita * tariffa imballo per articolo/categoria).
        CAST(NULL AS DECIMAL(18,2)) AS importo,
        N'-- ADATTA: sorgente imballi' AS origine
    FROM src.righe_vendita AS r
    WHERE r.anno = @anno AND r.mese = @mese;
END
GO
