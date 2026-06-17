-- =============================================================================
-- 20_comp_costo_venduto.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- COMPONENTE: COSTO_VENDUTO (livello 1).
-- Scrive SOLO le righe con codice_componente = 'COSTO_VENDUTO' in core.componente_riga.
-- importo = quantita * costo articolo/mese.
--
-- FONTE DEL COSTO: kodice.costi_articolo_mese (Item, Anno, Mese, Costo), l'OUTPUT
-- CERTIFICATO del motore core.usp_prepara_costi gia' presente in CDG_QV:
--   * risalita mese ed esplosione kit (distinta) sono GIA' risolte la' dentro;
--   * contiene solo costi validi/completi (Completo = 1);
--   * i costi mancanti/non validi NON arrivano qui: sono tracciati, con stato,
--     in kodice.costi_eccezioni.
-- NB: usiamo l'output certificato, NON kodice.vw_costo_sorgente (che e' l'input
--     grezzo da MA_ItemsWAP, prima di risalita/kit/bonifica).
-- PREREQUISITO: il periodo deve essere stato preparato dal motore, es.
--     EXEC core.usp_prepara_costi @schema_azienda = 'kodice', @anno = ..., @mese = ...;
--
-- CONTRATTO comune a tutte le procedure di componente:
--   1) DELETE delle proprie righe del periodo;
--   2) INSERT delle proprie righe, valorizzando 'origine'.
-- Cosi' correggere questo componente NON tocca gli altri.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.usp_comp_COSTO_VENDUTO
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM core.componente_riga
    WHERE anno = @anno AND mese = @mese AND codice_componente = N'COSTO_VENDUTO';

    -- JOIN (non LEFT): le righe il cui articolo non ha un costo certificato per il
    -- mese NON producono riga di costo. Sono visibili come ricavo senza costo e,
    -- se anomale, gia' censite in kodice.costi_eccezioni.
    --
    -- REGOLA DI STORNO SULLE NOTE DI CREDITO (vedi docs/estrattore-qlik.md sez. 3-bis;
    -- fonte gestionale: KODICEBAGNO_4.dbo.Split_Costi_Variabili). Il costo del venduto si
    -- storna SOLO sulle NC (DocumentType 3407876) con causale Annullato/Inevadibile/Cambio
    -- documento (InvoicingAccGroup IN 'A','I','C'): li' la merce di fatto rientra o la vendita
    -- e' annullata. Su OGNI ALTRA NC (reso cliente, danno trasporto, difetto fornitore, danno
    -- logistica, ...) il credito e' di SOLO PREZZO: la merce resta venduta, quindi il costo NON
    -- si storna -> per quelle righe non emetto la riga di costo negativa.
    -- In piu': sostituzioni "fotografo" (3407873, ProjectCode=7) -> costo 0 (niente riga).
    INSERT INTO core.componente_riga (anno, mese, sale_doc_id, line, codice_componente, importo, origine)
    SELECT
        r.anno, r.mese, r.sale_doc_id, r.line,
        N'COSTO_VENDUTO',
        CAST(r.quantita * k.Costo AS DECIMAL(18,2))                       AS importo,
        CONCAT(N'kodice.costi_articolo_mese [', k.TipoArticolo, N']')      AS origine
    FROM src.righe_vendita AS r
    JOIN kodice.costi_articolo_mese AS k
         ON LTRIM(RTRIM(k.Item)) = r.codice_articolo   -- TRIM anche lato kodice: robusto agli spazi
        AND k.Anno = r.anno
        AND k.Mese = r.mese
    JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS doc
         ON doc.SaleDocId = r.sale_doc_id
    WHERE r.anno = @anno AND r.mese = @mese
      -- NC che NON devono stornare il costo: escluse (niente riga di costo)
      AND NOT ( doc.DocumentType = '3407876'
                AND LTRIM(RTRIM(doc.InvoicingAccGroup)) NOT IN ('A','I','C') )
      -- sostituzioni "fotografo": costo azzerato
      AND NOT ( doc.DocumentType = '3407873' AND doc.ProjectCode = '7' );
END
GO
