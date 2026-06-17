-- =============================================================================
-- 21_comp_provvigioni.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: PROVVIGIONI (livello 2, gruppo "Costi commerciali") —
--   COMMISSIONI DI VENDITA del marketplace/canale (la voce piu' grossa dei costi
--   commerciali).
--
-- La tariffa NON e' piu' letta direttamente dalla tabella legacy: viene risolta dalla
-- tabella di attribuzione cfg.commissione_marketplace, a CASCATA (stesso criterio della
-- stima trasporto), per:
--   * canale        = categoria cliente (codice), col caso speciale Bricoman 'B2B0415';
--   * area          = Paese di destinazione -> ESTERO generico -> '*' (da kodice.vw_doc_trasporto);
--   * tipo_articolo = MA_Items.ItemType della riga -> '*'.
-- A parita' di specificita' vince la valido_dal piu' recente (correggere una tariffa con una
-- nuova data NON tocca i periodi gia' chiusi).
--
-- LOGICA: commissione = commissione_pct del CANALE x ricavo_netto della riga (l'imponibile con
--   la quota di trasporto recuperato gia' inclusa: il marketplace applica la commissione sul
--   totale che paga il cliente). I SERVIZI non pagano commissione. Segno gia' in ricavo_netto.
--
-- NOTE DI CREDITO (storno): su una nota di credito il costo commerciale si STORNA in base a
--   recupero_pct della tariffa (quanto il marketplace restituisce su un reso). ECCEZIONE: gli
--   annullamenti/inevadibili/cambio documento (InvoicingAccGroup A/I/C) stornano SEMPRE il 100%
--   (la vendita e' annullata). recupero_pct = 0 (default legacy) => sui resi la commissione NON
--   si recupera: la riga di reso non produce storno.
--
-- CONTRATTO: DELETE delle proprie righe del periodo + INSERT. Importo col segno (il -1 del
--   registro lo trasforma in costo). PREREQUISITI: cfg.commissione_marketplace popolata
--   (seed da 04_struttura_commerciali.sql) e kodice.vw_doc_trasporto disponibile.
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
        CAST( t.commissione_pct / 100.0 * r.ricavo_netto
              * CASE WHEN doc.DocumentType = N'3407876'
                     THEN CASE WHEN doc.InvoicingAccGroup IN ('A','I','C') THEN 1.0
                               ELSE t.recupero_pct / 100.0 END
                     ELSE 1.0 END
              AS DECIMAL(18,2)) AS importo,
        N'Commissione '
          + REPLACE(CONVERT(NVARCHAR(20), CAST(t.commissione_pct AS DECIMAL(9,2))), N'.', N',')
          + N'% — canale ' + ISNULL(NULLIF(t.marketplace, N''), t.canale)
          + CASE WHEN doc.DocumentType = N'3407876' AND doc.InvoicingAccGroup NOT IN ('A','I','C')
                 THEN N' (nota credito: storno '
                      + REPLACE(CONVERT(NVARCHAR(20), CAST(t.recupero_pct AS DECIMAL(9,2))), N'.', N',') + N'%)'
                 ELSE N'' END AS origine
    FROM src.righe_vendita AS r
    JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS doc
         ON doc.SaleDocId = r.sale_doc_id
    LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions AS opt
         ON opt.Customer = doc.CustSupp AND opt.CustSuppType = 3211264
    LEFT JOIN kodice.vw_doc_trasporto AS geo
         ON geo.anno = r.anno AND geo.mese = r.mese AND geo.sale_doc_id = r.sale_doc_id
    LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS it
         ON LTRIM(RTRIM(it.Item)) = r.codice_articolo
    CROSS APPLY (
        SELECT TOP 1 c.commissione_pct, c.recupero_pct, c.marketplace, c.canale
        FROM cfg.commissione_marketplace AS c
        WHERE (c.canale = CASE WHEN doc.CustSupp = N'B2B0415' THEN doc.CustSupp ELSE opt.Category END
               OR c.canale = N'*')
          AND (c.area = geo.paese OR c.area = geo.macro OR c.area = N'*')
          AND (c.tipo_articolo = it.ItemType OR c.tipo_articolo = N'*')
          AND c.valido_dal <= doc.DocumentDate
        ORDER BY CASE WHEN c.canale = N'*' THEN 1 ELSE 0 END,
                 CASE WHEN c.area = geo.paese THEN 0 WHEN c.area = geo.macro THEN 1 ELSE 2 END,
                 CASE WHEN c.tipo_articolo = N'*' THEN 1 ELSE 0 END,
                 c.valido_dal DESC
    ) AS t
    WHERE r.anno = @anno AND r.mese = @mese
      AND r.tipo_articolo <> N'SERVIZIO'        -- i servizi non pagano commissione
      AND r.ricavo_netto <> 0
      AND t.commissione_pct <> 0
      -- niente righe a zero: sui resi senza recupero (recupero_pct=0) non si genera storno
      AND NOT (doc.DocumentType = N'3407876' AND doc.InvoicingAccGroup NOT IN ('A','I','C') AND t.recupero_pct = 0);
END
GO
