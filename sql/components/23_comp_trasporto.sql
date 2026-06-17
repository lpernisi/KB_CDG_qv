-- =============================================================================
-- 23_comp_trasporto.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: TRASPORTO (livello 3) — costo di spedizione, nel Margine di Contribuzione III.
--
-- Il controllo di gestione non puo' aspettare la fattura del vettore (fine mese) per dare
-- una marginalita'. Quindi il costo di trasporto di una vendita ha PIU' LIVELLI di stima e
-- si usa il PIU' ATTENDIBILE DISPONIBILE (cascata):
--   3) FATTURA del vettore (reale)              -- src.fattura_vettore_riga -> kodice.ordine_documento
--   2) STIMA da listino corriere (alla spedizione) -- KB_OrdiniPrelevati.StimaCostoTrasporto (nostro algoritmo)
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
    -- documenti del periodo con peso (kit esplosi) / paese / macro (ITALIA-ESTERO) / canale
    doc AS (
        SELECT sale_doc_id, peso_doc, paese, macro, canale, data_doc
        FROM kodice.vw_doc_trasporto
        WHERE anno = @anno AND mese = @mese
    ),
    -- LIVELLO 1: stima per fascia di peso = riga di config PIU' SPECIFICA e VALIDA alla data
    -- del documento. Geografia a CASCATA: Paese specifico -> ESTERO generico -> '*' (qualsiasi);
    -- analogamente il canale esatto batte il jolly '*'. A parita', vince la valido_dal piu' recente
    -- (cosi' aggiornare la tariffa con una nuova data NON tocca il pregresso).
    liv1 AS (
        SELECT d.sale_doc_id, t.costo_eur AS costo
        FROM doc AS d
        CROSS APPLY (
            SELECT TOP 1 c.costo_eur
            FROM cfg.trasporto_stima_peso AS c
            WHERE (c.canale = d.canale OR c.canale = N'*')
              AND (c.area = d.paese OR c.area = d.macro OR c.area = N'*')
              AND d.peso_doc >= c.peso_da_kg AND d.peso_doc < c.peso_a_kg
              AND c.valido_dal <= d.data_doc
            ORDER BY CASE WHEN c.canale = N'*' THEN 1 ELSE 0 END,
                     CASE WHEN c.area = d.paese THEN 0 WHEN c.area = d.macro THEN 1 ELSE 2 END,
                     c.valido_dal DESC
        ) AS t
    ),
    -- LIVELLO 2: stima da listino corriere (nostro algoritmo) = KB_OrdiniPrelevati.StimaCostoTrasporto
    -- per ordine, riportata sul documento via il numero ordine. MAX per ordine (collassa i rari
    -- doppioni di riga), poi somma per documento (un documento puo' avere piu' ordini/spedizioni).
    liv2 AS (
        SELECT od.FatturaId AS sale_doc_id, SUM(op.stima) AS costo
        FROM (
            SELECT LTRIM(RTRIM(NrOrdine)) AS InternalOrdNo, MAX(StimaCostoTrasporto) AS stima
            FROM KODICEBAGNO_4.dbo.KB_OrdiniPrelevati
            WHERE StimaCostoTrasporto > 0
            GROUP BY LTRIM(RTRIM(NrOrdine))
        ) AS op
        JOIN kodice.ordine_documento AS od
             ON od.InternalOrdNo = op.InternalOrdNo AND od.FatturaId IS NOT NULL
        GROUP BY od.FatturaId
    ),
    -- CASCATA: il piu' attendibile disponibile -> fattura, poi stima listino, poi stima per fascia.
    costo AS (
        SELECT d.sale_doc_id,
               COALESCE(l3.costo, l2.costo, l1.costo) AS costo,
               CASE WHEN l3.costo IS NOT NULL THEN N'Fattura vettore (costo reale)'
                    WHEN l2.costo IS NOT NULL THEN N'Stima da listino corriere'
                    WHEN l1.costo IS NOT NULL THEN N'Stima per fascia di peso (a preventivo)'
                    ELSE NULL END AS origine
        FROM doc AS d
        LEFT JOIN liv3 AS l3 ON l3.sale_doc_id = d.sale_doc_id
        LEFT JOIN liv2 AS l2 ON l2.sale_doc_id = d.sale_doc_id
        LEFT JOIN liv1 AS l1 ON l1.sale_doc_id = d.sale_doc_id
    ),
    -- righe del periodo con il PESO di riga (kit esploso, come kodice.vw_doc_trasporto):
    -- serve come base di riparto alternativa quando il documento non ha ricavo (es. sostituzioni).
    righe AS (
        SELECT r.sale_doc_id, r.line, r.ricavo_netto,
               ABS(r.quantita) * COALESCE(k.peso_kit, g.GrossWeight, 0) AS peso_riga
        FROM src.righe_vendita AS r
        LEFT JOIN KODICEBAGNO_4.dbo.MA_ItemsGoodsData AS g ON LTRIM(RTRIM(g.Item)) = r.codice_articolo
        OUTER APPLY (   -- se kit: peso = somma dei componenti esplosi (un livello)
            SELECT SUM(dd.Qty * ISNULL(gc.GrossWeight, 0)) AS peso_kit
            FROM kodice.vw_distinta AS dd
            LEFT JOIN KODICEBAGNO_4.dbo.MA_ItemsGoodsData AS gc ON LTRIM(RTRIM(gc.Item)) = LTRIM(RTRIM(dd.Component))
            WHERE LTRIM(RTRIM(dd.BOM)) = r.codice_articolo
        ) AS k
        WHERE r.anno = @anno AND r.mese = @mese
    ),
    -- basi di riparto per documento: valore (ricavo), peso, numero di righe
    base AS (
        SELECT sale_doc_id,
               SUM(ricavo_netto) AS ricavo_doc,
               SUM(peso_riga)    AS peso_doc,
               COUNT(*)          AS n_righe
        FROM righe
        GROUP BY sale_doc_id
    )
    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        @anno, @mese, r.sale_doc_id, r.line,
        N'TRASPORTO',
        -- RIPARTIZIONE A CASCATA: per VALORE (ricavo riga / ricavo doc); se il documento non
        -- ha ricavo (sostituzioni, merce gratis) per PESO; se manca anche il peso, equa per N. RIGHE.
        CAST(co.costo *
             CASE WHEN b.ricavo_doc <> 0 THEN r.ricavo_netto / b.ricavo_doc
                  WHEN b.peso_doc   <> 0 THEN r.peso_riga    / b.peso_doc
                  ELSE 1.0 / b.n_righe END
             AS DECIMAL(18,2)) AS importo,
        co.origine
    FROM righe AS r
    JOIN costo AS co ON co.sale_doc_id = r.sale_doc_id AND co.costo IS NOT NULL
    JOIN base  AS b  ON b.sale_doc_id  = r.sale_doc_id;
END
GO
