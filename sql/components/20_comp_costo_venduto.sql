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
    WHERE r.anno = @anno AND r.mese = @mese;
END
GO
