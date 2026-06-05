-- =============================================================================
-- kodice.vw_costo_sorgente  —  MOTORE COSTI (snapshot dal DB, NON parte dello scaffold originale)
-- Estratto da CDG_QV per documentazione/versionamento. La fonte di verita' resta
-- l'oggetto nel database; la dashboard mostra la definizione LIVE.
-- =============================================================================

/* =====================================================================
   CDG Engine - ADATTATORE KODICE
   Unico pezzo azienda-specifico. Normalizza il gestionale Mago di Kodice
   nel contratto atteso dal motore comune:
     - vw_costo_sorgente (Item, Anno, Mese, Costo)   <- MA_ItemsWAP
     - vw_distinta       (BOM, Component, Qty)       <- MA_BillOfMaterialsComp
   Per una nuova azienda: si scrivono due viste analoghe sulla SUA sorgente.
   Idempotente.
   ===================================================================== */

/* ---------------------------------------------------------------------
   MA_ItemsWAP (colonne reali):
     EndPeriodDate -> fine periodo (mensile)  => Anno/Mese
     WAPCost       -> WAP dell'articolo (TOTALE, non per storage)
     Storage       -> vuoto ('') sulla riga totale
     TBCompanyID   -> company Mago

   Il costo e' TOTALE per articolo/mese: lettura diretta di WAPCost.
   Filtro Storage = '' per prendere la sola riga totale ed evitare ogni
   rischio di doppio conteggio se comparissero righe per singolo storage.

   TODO DA CONFERMARE:
   - Storage: esistono SOLO righe totali (Storage='') o anche righe per
     singolo deposito? Se solo totali, il filtro e' comunque innocuo.
   --------------------------------------------------------------------- */
CREATE   VIEW kodice.vw_costo_sorgente
AS
SELECT
    w.Item                              AS Item,
    YEAR(w.EndPeriodDate)               AS Anno,
    MONTH(w.EndPeriodDate)              AS Mese,
    CAST(w.WAPCost AS DECIMAL(18,4))    AS Costo
FROM KODICEBAGNO_4.dbo.MA_ItemsWAP AS w
WHERE w.Storage = '';       -- sola riga totale
GO
