-- =============================================================================
-- ricognizione_mago.sql   (eseguire su KODICEBAGNO_4, in SSMS)
-- -----------------------------------------------------------------------------
-- SOLA LETTURA. Serve a confermare i nomi reali di tabelle/colonne sorgente di
-- Mago prima di sostituire i segnaposto "-- ADATTA" in 10_usp_load_src.sql.
-- Nessun INSERT/UPDATE/DDL: solo SELECT su INFORMATION_SCHEMA e TOP 5.
--
-- Come usarlo: aprire in SSMS, selezionare il database KODICEBAGNO_4 in alto,
-- eseguire (F5) e incollare i risultati a Claude per la mappatura.
-- =============================================================================

USE KODICEBAGNO_4;   -- assicurati di essere sul database Mago
GO

-- -----------------------------------------------------------------------------
-- [1] Tabelle il cui nome contiene 'Sale' o 'Doc'
--     (candidate per testata + dettaglio delle righe di vendita)
-- -----------------------------------------------------------------------------
PRINT '=== [1] Tabelle candidate (nome contiene Sale o Doc) ===';
SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
  AND (TABLE_NAME LIKE '%Sale%' OR TABLE_NAME LIKE '%Doc%')
ORDER BY TABLE_NAME;
GO

-- -----------------------------------------------------------------------------
-- Colonne e tipi di TUTTE le tabelle candidate (un solo result set, ordinato)
-- -----------------------------------------------------------------------------
PRINT '=== Colonne delle tabelle candidate ===';
SELECT
    c.TABLE_NAME,
    c.ORDINAL_POSITION,
    c.COLUMN_NAME,
    c.DATA_TYPE,
    c.CHARACTER_MAXIMUM_LENGTH,
    c.NUMERIC_PRECISION,
    c.NUMERIC_SCALE,
    c.IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS AS c
JOIN INFORMATION_SCHEMA.TABLES  AS t
     ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND t.TABLE_NAME   = c.TABLE_NAME
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND (c.TABLE_NAME LIKE '%Sale%' OR c.TABLE_NAME LIKE '%Doc%')
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
GO

-- -----------------------------------------------------------------------------
-- [2] Struttura completa di MA_ItemsWAP (colonne e tipi)
-- -----------------------------------------------------------------------------
PRINT '=== [2] Struttura di MA_ItemsWAP ===';
SELECT
    ORDINAL_POSITION,
    COLUMN_NAME,
    DATA_TYPE,
    CHARACTER_MAXIMUM_LENGTH,
    NUMERIC_PRECISION,
    NUMERIC_SCALE,
    IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'MA_ItemsWAP'
ORDER BY ORDINAL_POSITION;
GO

-- -----------------------------------------------------------------------------
-- [3] Prime 5 righe (TOP 5) per capire i dati.
--     NOTA: i SELECT TOP 5 qui sotto usano nomi di tabella PROBABILI. Se una
--     tabella non esiste con questo nome, SSMS dara' errore solo su quella riga:
--     correggi il nome usando l'elenco prodotto al punto [1] e riesegui.
-- -----------------------------------------------------------------------------
PRINT '=== [3] TOP 5 — MA_ItemsWAP ===';
SELECT TOP 5 * FROM dbo.MA_ItemsWAP;
GO

-- Candidate per la TESTATA e il DETTAGLIO delle righe di vendita:
-- decommenta/adatta i nomi reali ricavati dal punto [1].
PRINT '=== [3] TOP 5 — (testata righe vendita: ADATTA il nome dal punto [1]) ===';
-- SELECT TOP 5 * FROM dbo.MA_SaleDoc;
PRINT '=== [3] TOP 5 — (dettaglio righe vendita: ADATTA il nome dal punto [1]) ===';
-- SELECT TOP 5 * FROM dbo.MA_SaleDocDetails;
GO
