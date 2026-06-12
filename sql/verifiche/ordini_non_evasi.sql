-- =============================================================================
-- Ordini di vendita NON ancora spediti (per la riconciliazione "fatturato non spedito").
-- -----------------------------------------------------------------------------
-- Oracolo autorevole = la vista nativa Mago/KL del controllo ordini, VwKLStatoOrdini, su cui
-- l'azienda fa il report con il filtro CompletamenteConsegnato = 'No'. La incapsuliamo qui per
-- esporre solo le colonne utili e dare nomi stabili al consumatore (dashboard CDG).
--
-- COMPETENZA DI PERIODO (importante): CompletamenteConsegnato e' uno snapshot di OGGI. Un ordine
-- ricevuto entro la chiusura ma spedito DOPO (es. ordine di maggio spedito a giugno) oggi risulta
-- 'Si', ma ALLA CHIUSURA era non spedito. Per la competenza si filtra quindi:
--     DataOrdine <= fine_periodo  AND  ( CompletamenteConsegnato = 'No'  OR  DataSpedizione > fine_periodo )
-- Il filtro vive nella query (parametrico), non nella vista.
-- =============================================================================
USE CDG_QV;
GO

CREATE OR ALTER VIEW kodice.vw_ordini_non_evasi AS
SELECT
    s.DataOrdine,
    s.NrOrdine,
    s.IdOrdine,
    s.Cliente,
    s.RagioneSocialeCliente,
    s.GruppoCliente,
    s.NrRighe,
    s.QtaOrdinata,
    s.QtaConsegnata,
    (s.QtaOrdinata - s.QtaConsegnata) AS QtaResidua,
    s.Stato,
    s.DataSpedizione,
    s.CompletamenteConsegnato
FROM KODICEBAGNO_4.dbo.VwKLStatoOrdini s;
GO
