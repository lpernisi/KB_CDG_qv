-- =============================================================================
-- 02_seed_componenti.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- Popola il REGISTRO dei componenti. Idempotente (MERGE): aggiorna le righe
-- esistenti, inserisce le nuove. La colonna 'gruppo' e' la VOCE DI PRIMO LIVELLO
-- del Conto Economico (es. "Costi commerciali") sotto cui il componente compare.
--
-- Per attivare un componente: implementa la sua procedura in sql/components/
-- e metti attivo = 1 qui. Il resto del calcolo si adegua da solo.
-- =============================================================================
MERGE cfg.componenti AS t
USING (VALUES
    -- codice, descrizione, modo_attacco, livello, segno, attivo, note, gruppo
    (N'COSTO_VENDUTO',      N'Costo del venduto (WAP)',              N'diretto',   1,    -1, 1, N'quantita * WAP con risalita mese',                                  N'Costo dei materiali'),
    (N'PROVVIGIONI',        N'Commissioni di vendita marketplace',   N'diretto',   2,    -1, 1, N'% per canale (kb_TabProvvigioniVendita.CommissioniVendita) x imponibile', N'Costi commerciali'),
    (N'CHANNEL_ENGINE',     N'Piattaforma marketplace (ChannelEngine)', N'diretto', 2,  -1, 1, N'% per canale (kb_TabProvvigioniVendita.CostiChannelEngine) dal 2022-04',   N'Costi commerciali'),
    (N'PROVVIGIONI_AGENTI', N'Provvigioni agenti di vendita',        N'diretto',   2,    -1, 1, N'importo per riga (MA_SaleDocDetail.SalespersonComm)',               N'Costi commerciali'),
    (N'IMBALLI',            N'Costi di imballaggio',                 N'diretto',   2,    -1, 0, N'per unita -- ADATTA sorgente',                                     N'Imballi'),
    (N'TRASPORTO',          N'Trasporto / spedizione',               N'driver',    3,    -1, 1, N'costo vettore reale (riepilogativo) per documento, spalmato sulle righe per valore', N'Costi di trasporto'),
    (N'TRASPORTO_RESI',     N'Logistica dei resi (rientri)',         N'struttura', NULL, -1, 0, N'rientri/ritiri dai vettori: voce a parte, non spalmata sulla riga', N'Costi di trasporto'),
    (N'PUBBLICITA',         N'Pubblicita / advertising',             N'driver',    3,    -1, 0, N'allocato per driver -- ADATTA sorgente (spesa aggregata per canale)', N'Costi commerciali')
) AS s (codice_componente, descrizione, modo_attacco, livello, segno, attivo, note, gruppo)
    ON t.codice_componente = s.codice_componente
WHEN MATCHED THEN UPDATE SET
    t.descrizione = s.descrizione, t.modo_attacco = s.modo_attacco,
    t.livello = s.livello, t.segno = s.segno, t.note = s.note, t.gruppo = s.gruppo
    -- NB: non sovrascrivo 'attivo' sugli aggiornamenti, cosi' non perdi le tue scelte.
WHEN NOT MATCHED THEN
    INSERT (codice_componente, descrizione, modo_attacco, livello, segno, attivo, note, gruppo)
    VALUES (s.codice_componente, s.descrizione, s.modo_attacco, s.livello, s.segno, s.attivo, s.note, s.gruppo);
GO

-- Componenti IMPLEMENTATI (decisione di codice, non preferenza manuale): li attivo
-- esplicitamente cosi' lo sono anche su un DB gia' esistente (idempotente).
UPDATE cfg.componenti SET attivo = 1 WHERE codice_componente = N'TRASPORTO'          AND attivo = 0;
UPDATE cfg.componenti SET attivo = 1 WHERE codice_componente = N'PROVVIGIONI'        AND attivo = 0;
UPDATE cfg.componenti SET attivo = 1 WHERE codice_componente = N'CHANNEL_ENGINE'     AND attivo = 0;
UPDATE cfg.componenti SET attivo = 1 WHERE codice_componente = N'PROVVIGIONI_AGENTI' AND attivo = 0;
GO
