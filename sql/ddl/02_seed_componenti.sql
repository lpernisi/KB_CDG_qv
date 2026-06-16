-- =============================================================================
-- 02_seed_componenti.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- Popola il REGISTRO dei componenti. Idempotente (MERGE): aggiorna le righe
-- esistenti, inserisce le nuove. Solo COSTO_VENDUTO e' attivo: gli altri sono
-- gia' censiti ma 'attivo = 0' finche' non adatti il loro estrattore.
--
-- Per attivare un componente: implementa la sua procedura in sql/components/
-- e metti attivo = 1 qui. Il resto del calcolo si adegua da solo.
-- =============================================================================
MERGE cfg.componenti AS t
USING (VALUES
    -- codice, descrizione, modo_attacco, livello, segno, attivo, note
    (N'COSTO_VENDUTO', N'Costo del venduto (WAP)',         N'diretto', 1, -1, 1, N'quantita * WAP con risalita mese'),
    (N'PROVVIGIONI',   N'Provvigioni marketplace',         N'diretto', 2, -1, 0, N'fee per categoria/canale -- ADATTA sorgente'),
    (N'IMBALLI',       N'Costi di imballaggio',            N'diretto', 2, -1, 0, N'per unita -- ADATTA sorgente'),
    (N'TRASPORTO',     N'Trasporto / spedizione',          N'driver',  3, -1, 1, N'costo vettore reale (riepilogativo) per documento, spalmato sulle righe per valore'),
    (N'TRASPORTO_RESI',N'Logistica dei resi (rientri)',    N'struttura', NULL, -1, 0, N'rientri/ritiri dai vettori: voce a parte, non spalmata sulla riga'),
    (N'PUBBLICITA',    N'Pubblicita / advertising',        N'driver',  3, -1, 0, N'allocato per driver -- ADATTA sorgente')
) AS s (codice_componente, descrizione, modo_attacco, livello, segno, attivo, note)
    ON t.codice_componente = s.codice_componente
WHEN MATCHED THEN UPDATE SET
    t.descrizione = s.descrizione, t.modo_attacco = s.modo_attacco,
    t.livello = s.livello, t.segno = s.segno, t.note = s.note
    -- NB: non sovrascrivo 'attivo' sugli aggiornamenti, cosi' non perdi le tue scelte.
WHEN NOT MATCHED THEN
    INSERT (codice_componente, descrizione, modo_attacco, livello, segno, attivo, note)
    VALUES (s.codice_componente, s.descrizione, s.modo_attacco, s.livello, s.segno, s.attivo, s.note);
GO

-- TRASPORTO ora e' implementato (componente reale): lo attivo esplicitamente. E' una
-- decisione di codice, non una preferenza manuale, quindi la forzo qui (idempotente).
UPDATE cfg.componenti SET attivo = 1 WHERE codice_componente = N'TRASPORTO' AND attivo = 0;
GO
