-- =============================================================================
-- Cambi valuta dalla BCE (indipendenti da Mago, il cui fixing e' spesso fermo).
-- Sorgente: feed BCE eurofxref-daily.xml. Convenzione: CambioPerEur = quante unita'
-- di valuta vale 1 EUR (es. USD 1.1537). Per convertire un importo in valuta -> EUR:
--   EUR = importo / CambioPerEur   (per EUR, /1).
-- Popolata da src/aggiorna_cambi.py (schedulabile settimanale/mensile).
-- =============================================================================
USE CDG_QV;
GO

IF OBJECT_ID('kodice.cambio_valuta', 'U') IS NULL
CREATE TABLE kodice.cambio_valuta (
    Data         date         NOT NULL,
    Valuta       varchar(3)   NOT NULL,
    CambioPerEur float        NOT NULL,   -- 1 EUR = CambioPerEur unita' di Valuta
    Fonte        varchar(30)  NULL,
    Caricato     datetime     NULL,
    CONSTRAINT PK_cambio_valuta PRIMARY KEY (Data, Valuta)
);
GO

-- Ultimo cambio disponibile per ogni valuta (quello "corrente").
CREATE OR ALTER VIEW kodice.vw_cambio_corrente AS
SELECT Valuta, CambioPerEur, Data
FROM (
    SELECT Valuta, CambioPerEur, Data,
           ROW_NUMBER() OVER (PARTITION BY Valuta ORDER BY Data DESC) AS rn
    FROM kodice.cambio_valuta
) t
WHERE rn = 1;
GO
