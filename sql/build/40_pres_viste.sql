-- =============================================================================
-- 40_pres_viste.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- Viste di presentazione. I consumatori (Qlik, altri strumenti, controlli)
-- leggono SEMPRE da 'pres', mai dalle tabelle interne.
-- =============================================================================

-- ---- Conto economico di riga (per Qlik) ------------------------------------
CREATE OR ALTER VIEW pres.conto_economico_riga
AS
SELECT
    f.anno, f.mese, f.sale_doc_id, f.line, f.codice_articolo, f.tipo_articolo, f.quantita,
    f.ricavo_netto, f.mdc1, f.mdc2, f.mdc3,
    CASE WHEN f.ricavo_netto <> 0
         THEN CAST(f.mdc1 / f.ricavo_netto AS DECIMAL(9,4)) ELSE 0 END AS mdc1_pct
FROM core.fatto_riga AS f;
GO

-- ---- Dettaglio elementare per componente (per ispezione/correzione) --------
-- Ogni riga = (riga documento, componente, importo, da dove arriva).
-- E' la vista per guardare un singolo componente a livello atomico.
CREATE OR ALTER VIEW pres.componente_riga
AS
SELECT
    cr.anno, cr.mese, cr.sale_doc_id, cr.line,
    cr.codice_componente, c.descrizione, c.gruppo, c.livello, c.modo_attacco, c.attivo,
    cr.importo, cr.origine
FROM core.componente_riga AS cr
JOIN cfg.componenti AS c ON c.codice_componente = cr.codice_componente;
GO

-- ---- Controllo per componente (sintesi per validare ogni voce) -------------
-- Per ogni componente: quante righe, totale, range, righe senza importo.
-- E' lo strumento rapido per verificare l'estrazione di una singola voce
-- senza guardare le altre.
CREATE OR ALTER VIEW pres.controllo_componenti
AS
SELECT
    cr.anno, cr.mese,
    cr.codice_componente, c.descrizione, c.gruppo, c.livello, c.attivo,
    COUNT(*)                                              AS n_righe,
    SUM(CASE WHEN cr.importo IS NULL THEN 1 ELSE 0 END)   AS n_senza_importo,
    CAST(SUM(cr.importo) AS DECIMAL(18,2))                AS totale,
    CAST(MIN(cr.importo) AS DECIMAL(18,2))                AS minimo,
    CAST(MAX(cr.importo) AS DECIMAL(18,2))                AS massimo
FROM core.componente_riga AS cr
JOIN cfg.componenti AS c ON c.codice_componente = cr.codice_componente
GROUP BY cr.anno, cr.mese, cr.codice_componente, c.descrizione, c.gruppo, c.livello, c.attivo;
GO
