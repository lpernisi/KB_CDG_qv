-- =============================================================================
-- Controllo trasferimenti FBA (Amazon Logistica) — sincronizzazione delle due gambe.
-- -----------------------------------------------------------------------------
-- L'invio merce alla logistica Amazon (FBA) e' un TRASFERIMENTO TRA DEPOSITI fatto con due documenti,
-- netto ~zero sul magazzino totale (non e' una vendita, non ha fattura):
--   - gamba USCITA da ATRI : ordine di vendita al cliente 70209 (AMAZON LOGISTICA S.r.l.) evaso dal
--                            magazzino -> movimento 506 causale KLVEN-OA;
--   - gamba CARICO su Amazon: DDT causale CAR-AMA -> movimento 507 (carico deposito Amazon).
-- Le due gambe DEVONO bilanciarsi per articolo. Se non lo fanno, lo sbilancio (trasferimenti
-- incompleti / sfasati nel tempo) e' una differenza da EVIDENZIARE nella riconciliazione, non da nascondere.
-- Grana: movimento per articolo/mese (con segno gia' applicato), cosi' la dashboard filtra per periodo.
-- =============================================================================
USE CDG_QV;
GO

CREATE OR ALTER VIEW kodice.vw_fba_movimenti AS
SELECT 'USCITA_ATRI'   AS Gamba, YEAR(h.PostingDate) AS Anno, MONTH(h.PostingDate) AS Mese,
       LTRIM(RTRIM(d.Item)) AS Item, d.Qty AS Qta, h.PostingDate
FROM KODICEBAGNO_4.dbo.MA_InventoryEntries h
JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId = h.EntryId
WHERE h.WAPMovementType = 2032533506 AND h.CustSupp = '70209'
UNION ALL
SELECT 'CARICO_AMAZON', YEAR(h.PostingDate), MONTH(h.PostingDate),
       LTRIM(RTRIM(d.Item)), d.Qty, h.PostingDate
FROM KODICEBAGNO_4.dbo.MA_InventoryEntries h
JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId = h.EntryId
WHERE h.WAPMovementType = 2032533507 AND h.InvRsn = 'CAR-AMA';
GO
