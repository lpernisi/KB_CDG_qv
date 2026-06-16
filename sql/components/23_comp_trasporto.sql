-- =============================================================================
-- 23_comp_trasporto.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: TRASPORTO (livello 3) — costo di spedizione, nel Margine di Contribuzione III.
--
-- Il controllo di gestione non puo' aspettare la fattura del vettore (fine mese) per dare
-- una marginalita'. Quindi il costo di trasporto di una vendita ha PIU' LIVELLI di stima e
-- si usa il PIU' ATTENDIBILE DISPONIBILE (cascata):
--   3) FATTURA del vettore (reale)              -- src.fattura_vettore_riga -> kodice.ordine_documento
--   2) STIMA da listino corriere (alla spedizione) -- FUTURO (slot lasciato nella cascata)
--   1) STIMA per fascia di peso (a preventivo)  -- cfg.trasporto_stima_peso, sempre disponibile
-- Per ogni DOCUMENTO si prende il costo del livello piu' alto presente; il campo 'origine'
-- registra QUALE livello e' stato usato (tracciabilita').
--
-- Il costo e' per DOCUMENTO/spedizione e si RIPARTISCE sulle righe in proporzione al valore:
--   costo_riga = costo_documento * (ricavo_riga / ricavo_documento).
-- I RIENTRI (resi/ritiri) sono una voce a parte (logistica resi), non entrano qui.
--
-- CONTRATTO: DELETE delle proprie righe del periodo + INSERT. Importo POSITIVO (il segno -1
-- del registro lo fa sottrarre). PREREQUISITI: per il livello 3, src.fattura_vettore_riga
-- popolata + kodice.ordine_documento costruita (usp_build_ordine_documento @Anno);
-- per il livello 1, cfg.trasporto_stima_peso valorizzata + vista kodice.vw_doc_trasporto.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_TRASPORTO
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'TRASPORTO';

    ;WITH
    -- LIVELLO 3: costo REALE per documento = somma "Totale" delle spedizioni in uscita
    -- agganciate (via numero ordine) a quel documento.
    liv3 AS (
        SELECT od.FatturaId AS sale_doc_id, SUM(fvr.totale) AS costo
        FROM src.fattura_vettore_riga AS fvr
        JOIN kodice.ordine_documento  AS od ON od.InternalOrdNo = fvr.rif_ordine
        WHERE fvr.tipo_spedizione = N'SPEDIZIONE' AND od.FatturaId IS NOT NULL
        GROUP BY od.FatturaId
    ),
    -- documenti del periodo con peso (kit esplosi) / area / canale
    doc AS (
        SELECT sale_doc_id, peso_doc, area, canale, data_doc
        FROM kodice.vw_doc_trasporto
        WHERE anno = @anno AND mese = @mese
    ),
    -- LIVELLO 1: stima per fascia di peso = riga di config PIU' SPECIFICA e VALIDA alla data
    -- del documento (canale/area esatti battono il jolly '*'; a parita', la valido_dal piu' recente).
    liv1 AS (
        SELECT d.sale_doc_id, t.costo_eur AS costo
        FROM doc AS d
        CROSS APPLY (
            SELECT TOP 1 c.costo_eur
            FROM cfg.trasporto_stima_peso AS c
            WHERE (c.canale = d.canale OR c.canale = N'*')
              AND (c.area   = d.area   OR c.area   = N'*')
              AND d.peso_doc >= c.peso_da_kg AND d.peso_doc < c.peso_a_kg
              AND c.valido_dal <= d.data_doc
            ORDER BY CASE WHEN c.canale = N'*' THEN 1 ELSE 0 END,
                     CASE WHEN c.area   = N'*' THEN 1 ELSE 0 END,
                     c.valido_dal DESC
        ) AS t
    ),
    -- CASCATA: fattura se presente, altrimenti stima per fascia. (Livello 2 listino: futuro.)
    costo AS (
        SELECT d.sale_doc_id,
               COALESCE(l3.costo, l1.costo) AS costo,
               CASE WHEN l3.costo IS NOT NULL THEN N'Fattura vettore (costo reale)'
                    WHEN l1.costo IS NOT NULL THEN N'Stima per fascia di peso (a preventivo)'
                    ELSE NULL END AS origine
        FROM doc AS d
        LEFT JOIN liv3 AS l3 ON l3.sale_doc_id = d.sale_doc_id
        LEFT JOIN liv1 AS l1 ON l1.sale_doc_id = d.sale_doc_id
    ),
    -- base di riparto = ricavo del documento nel periodo
    base AS (
        SELECT r.sale_doc_id, SUM(r.ricavo_netto) AS ricavo_doc
        FROM src.righe_vendita AS r
        WHERE r.anno = @anno AND r.mese = @mese
        GROUP BY r.sale_doc_id
    )
    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'TRASPORTO',
        CAST(co.costo * (r.ricavo_netto / NULLIF(b.ricavo_doc, 0)) AS DECIMAL(18,2)) AS importo,
        co.origine
    FROM src.righe_vendita AS r
    JOIN costo AS co ON co.sale_doc_id = r.sale_doc_id AND co.costo IS NOT NULL
    JOIN base  AS b  ON b.sale_doc_id  = r.sale_doc_id
    WHERE r.anno = @anno AND r.mese = @mese
      AND b.ricavo_doc <> 0;
END
GO
