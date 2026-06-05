-- =============================================================================
-- 24_comp_pubblicita.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: PUBBLICITA (livello 3) — costi pubblicitari, allocati per DRIVER.
-- STUB da adattare. Quando e' pronta, in cfg.componenti metti attivo = 1.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_PUBBLICITA
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'PUBBLICITA';

    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'PUBBLICITA',
        -- ADATTA: spesa pubblicitaria allocata alla riga con un driver reale.
        CAST(NULL AS DECIMAL(18,2)) AS importo,
        N'-- ADATTA: sorgente pubblicita, allocata per driver' AS origine
    FROM src.righe_vendita AS r
    WHERE r.anno = @anno AND r.mese = @mese;
END
GO
