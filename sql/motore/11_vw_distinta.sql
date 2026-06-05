-- =============================================================================
-- kodice.vw_distinta  —  MOTORE COSTI (snapshot dal DB, NON parte dello scaffold originale)
-- Estratto da CDG_QV per documentazione/versionamento. La fonte di verita' resta
-- l'oggetto nel database; la dashboard mostra la definizione LIVE.
-- =============================================================================

/* MA_BillOfMaterialsComp: colonne BOM, Component, Qty confermate dal
   codice esistente (UDF GetCostoMedioUltimoMeseValidoDistintaBase_ok). */
CREATE   VIEW kodice.vw_distinta
AS
SELECT
    c.BOM       COLLATE DATABASE_DEFAULT  AS BOM,
    c.Component COLLATE DATABASE_DEFAULT  AS Component,
    CAST(c.Qty AS DECIMAL(18,6))          AS Qty
FROM KODICEBAGNO_4.dbo.MA_BillOfMaterialsComp AS c;
GO
