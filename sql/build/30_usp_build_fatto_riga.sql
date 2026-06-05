-- =============================================================================
-- 30_usp_build_fatto_riga.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- ASSEMBLAGGIO dei margini di contribuzione, in modo DICHIARATIVO.
-- Somma i componenti di core.componente_riga raggruppandoli per LIVELLO,
-- usando livello/segno/attivo dal registro cfg.componenti. I margini sono cumulativi:
--   MdC I   = ricavo + somma(componenti livello 1, con segno)
--   MdC II  = MdC I  + somma(livello 2)
--   MdC III = MdC II + somma(livello 3)
-- Aggiungere un componente NON richiede di toccare questa procedura.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_build_fatto_riga
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.fatto_riga WHERE anno = @anno AND mese = @mese;

    -- Somma dei componenti per riga e per livello (solo quelli attivi nel registro).
    ;WITH comp AS (
        SELECT
            cr.sale_doc_id, cr.line,
            SUM(CASE WHEN c.livello = 1 THEN cr.importo * c.segno ELSE 0 END) AS liv1,
            SUM(CASE WHEN c.livello = 2 THEN cr.importo * c.segno ELSE 0 END) AS liv2,
            SUM(CASE WHEN c.livello = 3 THEN cr.importo * c.segno ELSE 0 END) AS liv3
        FROM core.componente_riga AS cr
        JOIN cfg.componenti AS c
             ON c.codice_componente = cr.codice_componente AND c.attivo = 1
        WHERE cr.anno = @anno AND cr.mese = @mese
        GROUP BY cr.sale_doc_id, cr.line
    )
    INSERT INTO core.fatto_riga
        (anno, mese, sale_doc_id, line, codice_articolo, tipo_articolo, quantita, ricavo_netto, mdc1, mdc2, mdc3)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line, r.codice_articolo, r.tipo_articolo, r.quantita, r.ricavo_netto,
        CAST(r.ricavo_netto + ISNULL(comp.liv1, 0) AS DECIMAL(18,2))                                  AS mdc1,
        CAST(r.ricavo_netto + ISNULL(comp.liv1, 0) + ISNULL(comp.liv2, 0) AS DECIMAL(18,2))           AS mdc2,
        CAST(r.ricavo_netto + ISNULL(comp.liv1, 0) + ISNULL(comp.liv2, 0) + ISNULL(comp.liv3, 0) AS DECIMAL(18,2)) AS mdc3
    FROM src.righe_vendita AS r
    LEFT JOIN comp ON comp.sale_doc_id = r.sale_doc_id AND comp.line = r.line
    WHERE r.anno = @anno AND r.mese = @mese;

    -- Stato di avanzamento del mese.
    MERGE cfg.controllo_mesi AS t
    USING (SELECT @anno AS anno, @mese AS mese) AS s
        ON t.anno = s.anno AND t.mese = s.mese
    WHEN MATCHED THEN
        UPDATE SET righe_caricate = (SELECT COUNT(*) FROM core.fatto_riga WHERE anno=@anno AND mese=@mese),
                   ultimo_run = SYSDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (anno, mese, righe_caricate, ultimo_run)
        VALUES (@anno, @mese, (SELECT COUNT(*) FROM core.fatto_riga WHERE anno=@anno AND mese=@mese), SYSDATETIME());
END
GO
