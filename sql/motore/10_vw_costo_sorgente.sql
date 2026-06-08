-- =============================================================================
-- kodice.vw_costo_sorgente  —  MOTORE COSTI (snapshot dal DB, NON parte dello scaffold originale)
-- Estratto da CDG_QV per documentazione/versionamento. La fonte di verita' resta
-- l'oggetto nel database; la dashboard mostra la definizione LIVE.
-- =============================================================================

/* =====================================================================
   CDG Engine - ADATTATORE KODICE
   Unico pezzo azienda-specifico. Normalizza la sorgente di costo nel
   contratto atteso dal motore comune:
     - vw_costo_sorgente (Item, Anno, Mese, Costo)   <- kodice.wap_ricalc (NOSTRO ricalcolo)
     - vw_distinta       (BOM, Component, Qty)        <- MA_BillOfMaterialsComp
   Per una nuova azienda: si scrivono due viste analoghe sulla SUA sorgente.
   Idempotente.
   ===================================================================== */

/* ---------------------------------------------------------------------
   FONTE DEL COSTO = NOSTRO RICALCOLO PARALLELO (kodice.wap_ricalc), non piu' MA_ItemsWAP.
   Decisione (giu-2026): il costo del venduto del Conto Economico si basa sul nostro costo
   calcolato (FX risolto, split puro/oneri, rettifiche, apertura bonificata), non sul WAP di
   Mago. MA_ItemsWAP resta solo come RAFFRONTO nella dashboard (sezione dedicata).

   Contratto invariato (Item, Anno, Mese, Costo): il motore (core.usp_prepara_costi) continua a
   fare risalita-mese + esplosione kit + certificazione esattamente come prima. Si filtra
   WAPCost_ricalc > 0 cosi' la risalita del motore prende sempre l'ultimo costo valido
   ("il costo sopravvive a giacenza 0"). Per tornare al WAP di Mago: ripristinare la versione
   precedente di questa vista (FROM KODICEBAGNO_4.dbo.MA_ItemsWAP, Storage='').
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW kodice.vw_costo_sorgente
AS
SELECT
    r.Item                                  AS Item,
    r.Anno                                  AS Anno,
    r.Mese                                  AS Mese,
    CAST(r.WAPCost_ricalc AS DECIMAL(18,4)) AS Costo
FROM kodice.wap_ricalc AS r
WHERE r.WAPCost_ricalc > 0;
GO
