-- =============================================================================
-- 21_comp_provvigioni.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: PROVVIGIONI (livello 2, gruppo "Costi commerciali") —
--   COMMISSIONI DI VENDITA del marketplace/canale (la voce piu' grossa dei costi
--   commerciali). E' la stessa logica validata della procedura storica
--   Split_Costi_Variabili (campo ImportoProvvigioni) — vedi
--   docs/riferimento-split-costi-variabili.md.
--
-- LOGICA: per ogni riga di MERCE, commissione = aliquota % del CANALE x ricavo di riga.
--   - Il CANALE e' la categoria del cliente (MA_CustSuppCustomerOptions.Category),
--     con il caso speciale del cliente 'B2B0415' (Bricoman) che usa il proprio codice.
--   - L'aliquota % e' in kb_TabProvvigioniVendita.CommissioniVendita per categoria
--     (Amazon 24,36% · Leroy Merlin 14,29% · MANOMANO 19,80% · B2B 4,22% ...).
--   - BASE = ricavo_netto della riga (imponibile + quota di trasporto recuperato gia'
--     spalmata). E' la scelta corretta: il marketplace applica la commissione sul totale
--     che paga il cliente, spedizione inclusa; ed e' coerente con la definizione di ricavo
--     del CDG. L'oracolo legacy applicava la % anche alla riga di trasporto: stessa cosa,
--     qui il trasporto e' gia' dentro il ricavo delle righe prodotto.
--   - I SERVIZI (IsGood=0) non pagano commissione.
--   - Segno: gia' dentro ricavo_netto (fatture +, note di credito -).
--
-- QUALITA' (regola del 19.03.2026 dell'oracolo): sulle NOTE DI CREDITO al cliente (resi)
--   il marketplace NON restituisce la commissione, quindi NON la recuperiamo; il recupero
--   vale solo su annullamenti/inevadibili/cambio documento (InvoicingAccGroup A/I/C).
--
-- CONTRATTO: DELETE delle proprie righe del periodo + INSERT. Importo col segno del
--   documento (il -1 del registro lo trasforma in costo). Solo righe con importo <> 0.
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
        CAST( provv.CommissioniVendita / 100.0 * r.ricavo_netto AS DECIMAL(18,2)) AS importo,
        N'Commissione marketplace '
          + REPLACE(CONVERT(NVARCHAR(20), CAST(provv.CommissioniVendita AS DECIMAL(9,2))), N'.', N',')
          + N'% — canale ' + provv.Descrizione AS origine
    FROM src.righe_vendita AS r
    JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS doc
         ON doc.SaleDocId = r.sale_doc_id
    LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions AS opt
         ON opt.Customer = doc.CustSupp AND opt.CustSuppType = 3211264
    JOIN KODICEBAGNO_4.dbo.kb_TabProvvigioniVendita AS provv
         ON provv.CategoriaCliente = CASE WHEN doc.CustSupp = N'B2B0415' THEN doc.CustSupp ELSE opt.Category END
    WHERE r.anno = @anno AND r.mese = @mese
      AND r.tipo_articolo <> N'SERVIZIO'           -- i servizi (IsGood=0) non pagano commissione
      AND provv.CommissioniVendita <> 0
      AND r.ricavo_netto <> 0                       -- niente righe a base nulla (es. sostituzioni)
      -- QUALITA': sulle note di credito-reso non si recupera la commissione (solo A/I/C)
      AND NOT (doc.DocumentType = N'3407876' AND doc.InvoicingAccGroup NOT IN ('A','I','C'));
END
GO
