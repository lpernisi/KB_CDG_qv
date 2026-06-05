-- =============================================================================
-- 21_comp_provvigioni.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: PROVVIGIONI (livello 2) — provvigioni/fee del marketplace.
-- STUB: stessa struttura del costo del venduto, ma la SELECT sorgente e' tutta
-- da adattare (-- ADATTA). Quando e' pronta, in cfg.componenti metti attivo = 1.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_PROVVIGIONI
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'PROVVIGIONI';

    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'PROVVIGIONI',
        -- ADATTA: la fee marketplace, tipicamente per categoria/canale.
        CAST(NULL AS DECIMAL(18,2)) AS importo,
        N'-- ADATTA: sorgente provvigioni' AS origine
    FROM src.righe_vendita AS r
    WHERE r.anno = @anno AND r.mese = @mese;
END
GO
