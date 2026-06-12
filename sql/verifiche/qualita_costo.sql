-- =============================================================================
-- qualita_costo.sql   (oggetti in CDG_QV, schema kodice)
-- -----------------------------------------------------------------------------
-- INDICI DI QUALITA' del costo + CERTIFICAZIONE del dato.
--   kodice.qualita_certificazione : tabella di STATO (l'unica scrivibile) - il revisore
--       marca (Item,Anno,Mese) come CERTIFICATO / ACCETTATO_CON_NOTA / DA_CORREGGERE_ALGORITMO / IGNORATO.
--   kodice.vw_qualita_costo : vista LIVE che, per ogni (Item,Anno,Mese) di kodice.wap_ricalc,
--       calcola i flag di qualita', il LIVELLO (ROSSO/GIALLO/VERDE) e lo stato di certificazione.
-- Soglie v1 (tarabili): Q1 5%/20% vs Mago, Q5/Q6 15%, oneri spariti su 3 mesi.
-- =============================================================================

USE CDG_QV;
GO

IF OBJECT_ID('kodice.qualita_certificazione', 'U') IS NULL
CREATE TABLE kodice.qualita_certificazione (
    Item       varchar(21)  NOT NULL,
    Anno       smallint     NOT NULL,
    Mese       tinyint      NOT NULL,
    Stato      varchar(30)  NOT NULL,   -- CERTIFICATO / ACCETTATO_CON_NOTA / DA_CORREGGERE_ALGORITMO / IGNORATO
    Nota       varchar(500) NULL,
    Utente     varchar(100) NULL,
    DataStato  datetime     NULL,
    CONSTRAINT PK_qualita_cert PRIMARY KEY (Item, Anno, Mese)
);
GO

CREATE OR ALTER VIEW kodice.vw_qualita_costo AS
WITH base AS (
    SELECT Item, Anno, Mese, WAPCost_ricalc, PuroUnit, OneriUnit, QtaFin, QtaAcq,
           ValAcqPuro, ValAcqOneri, WAPCost_Mago,
           LAG(WAPCost_ricalc) OVER (PARTITION BY Item ORDER BY Anno, Mese) AS CostoMesePrec,
           LAG(OneriUnit, 1)   OVER (PARTITION BY Item ORDER BY Anno, Mese) AS Oneri1,
           LAG(OneriUnit, 2)   OVER (PARTITION BY Item ORDER BY Anno, Mese) AS Oneri2,
           LAG(OneriUnit, 3)   OVER (PARTITION BY Item ORDER BY Anno, Mese) AS Oneri3
    FROM kodice.wap_ricalc
),
movflag AS (   -- flag per (Item, Anno, Mese) dai MOVIMENTI: valuta non convertita / causale non mappata
    SELECT LTRIM(RTRIM(d.Item)) AS Item, YEAR(h.PostingDate) AS Anno, MONTH(h.PostingDate) AS Mese,
           MAX(CASE WHEN h.WAPMovementType = 2032533505 AND h.Currency NOT IN ('','EUR') AND ISNULL(h.Fixing,0) = 0
                    THEN 1 ELSE 0 END) AS ValutaNonConv,
           MAX(CASE WHEN h.WAPMovementType IN (2032533507, 2032533508)
                     AND h.InvRsn NOT IN ('CAR-AMA','KLRI-P-A','RI-POS','KLRI-N-A','RI-NEG','KLR-FORA',
                                          'KL-TRASF','MOV-DEP','KRETASS+','KRETASS-')
                    THEN 1 ELSE 0 END) AS CausaleNonMappata
    FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
    JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
    GROUP BY LTRIM(RTRIM(d.Item)), YEAR(h.PostingDate), MONTH(h.PostingDate)
),
riacq AS (   -- costo di RIACQUISTO per Item: StandardPrice del fornitore preferenziale, NETTO sconti,
             -- convertito in EUR col cambio BCE. NB: il listino fornitore e' solo costo d'acquisto PURO
             -- (niente oneri accessori) -> si confronta con PuroUnit, non col costo totale.
    SELECT Item, MAX(RiacquistoEur) AS RiacquistoEur
    FROM (
        SELECT LTRIM(RTRIM(g.Item)) AS Item,
               CASE WHEN isup.Currency IS NULL OR LTRIM(RTRIM(isup.Currency)) IN ('','EUR')
                     THEN isup.StandardPrice*(1-ISNULL(isup.Discount1,0)/100.0)*(1-ISNULL(isup.Discount2,0)/100.0)
                    WHEN cv.CambioPerEur > 0
                     THEN isup.StandardPrice*(1-ISNULL(isup.Discount1,0)/100.0)*(1-ISNULL(isup.Discount2,0)/100.0)/cv.CambioPerEur
                    ELSE NULL END AS RiacquistoEur
        FROM KODICEBAGNO_4.dbo.MA_ItemsGoodsData g
        JOIN KODICEBAGNO_4.dbo.MA_ItemSuppliers isup
              ON LTRIM(RTRIM(isup.Item)) = LTRIM(RTRIM(g.Item)) AND LTRIM(RTRIM(isup.Supplier)) = LTRIM(RTRIM(g.Supplier))
        LEFT JOIN kodice.vw_cambio_corrente cv ON cv.Valuta = LTRIM(RTRIM(isup.Currency))
        WHERE g.Supplier IS NOT NULL AND g.Supplier <> '' AND isup.StandardPrice > 0
    ) x
    GROUP BY Item
),
flags AS (
    SELECT b.*,
           ISNULL(m.ValutaNonConv, 0)     AS ValutaNonConv,
           ISNULL(m.CausaleNonMappata, 0) AS CausaleNonMappata,
           r.RiacquistoEur,
           -- Q12 scostamento del COSTO PURO dal costo di riacquisto (listino fornitore, EUR): >25%
           CASE WHEN r.RiacquistoEur > 0 AND b.PuroUnit > 0
                 AND ABS(b.PuroUnit - r.RiacquistoEur) > 0.25 * r.RiacquistoEur THEN 1 ELSE 0 END AS Q12_scost_riacq,
           -- Q1 scostamento vs WAP Mago: 0 nessuno, 1 warn (>5%), 2 alto (>20%)
           CASE WHEN b.WAPCost_Mago > 0 AND ABS(b.WAPCost_ricalc - b.WAPCost_Mago) > 0.20 * b.WAPCost_Mago THEN 2
                WHEN b.WAPCost_Mago > 0 AND ABS(b.WAPCost_ricalc - b.WAPCost_Mago) > 0.05 * b.WAPCost_Mago THEN 1
                ELSE 0 END AS Q1_scost_mago,
           -- Q2 WAP Mago rotto (0/null) con nostro costo e giacenza
           CASE WHEN ISNULL(b.WAPCost_Mago,0) = 0 AND b.WAPCost_ricalc > 0 AND b.QtaFin > 0 THEN 1 ELSE 0 END AS Q2_mago_rotto,
           -- Q3 oneri spariti: oneri=0 ora ma >0 in almeno uno dei 3 mesi precedenti
           CASE WHEN ISNULL(b.OneriUnit,0) = 0 AND (b.Oneri1 > 0 OR b.Oneri2 > 0 OR b.Oneri3 > 0) THEN 1 ELSE 0 END AS Q3_oneri_spariti,
           -- Q5 salto di costo MoM > 15%
           CASE WHEN b.CostoMesePrec > 0 AND ABS(b.WAPCost_ricalc - b.CostoMesePrec) > 0.15 * b.CostoMesePrec THEN 1 ELSE 0 END AS Q5_salto_mom,
           -- Q6 acquisto a prezzo molto diverso dal costo precedente (>15%)
           CASE WHEN b.QtaAcq > 0 AND b.CostoMesePrec > 0
                 AND ABS((b.ValAcqPuro + b.ValAcqOneri)/NULLIF(b.QtaAcq,0) - b.CostoMesePrec) > 0.15 * b.CostoMesePrec THEN 1 ELSE 0 END AS Q6_acq_diverso,
           -- Q9 quantita' ricalcolo negativa
           CASE WHEN b.QtaFin < 0 THEN 1 ELSE 0 END AS Q9_qty_neg
    FROM base b
    LEFT JOIN movflag m ON m.Item = b.Item AND m.Anno = b.Anno AND m.Mese = b.Mese
    LEFT JOIN riacq r   ON r.Item = b.Item
)
SELECT f.Item, f.Anno, f.Mese, f.WAPCost_ricalc, f.PuroUnit, f.OneriUnit, f.QtaFin, f.WAPCost_Mago,
       f.Q1_scost_mago, f.Q2_mago_rotto, f.Q3_oneri_spariti, f.Q5_salto_mom, f.Q6_acq_diverso,
       f.Q9_qty_neg, f.ValutaNonConv AS Q4_valuta, f.CausaleNonMappata AS Q11_causale,
       f.Q12_scost_riacq, f.RiacquistoEur,
       -- LIVELLO: ROSSO (bloccante) / GIALLO (da rivedere) / VERDE
       -- Q2 (WAP Mago azzerato) e' INFORMATIVO (Mago rotto, il NOSTRO costo e' quello buono): NON incide sul livello.
       CASE WHEN f.ValutaNonConv = 1 OR f.Q9_qty_neg = 1 THEN 'ROSSO'
            WHEN f.Q1_scost_mago >= 1 OR f.Q3_oneri_spariti = 1
                 OR f.Q5_salto_mom = 1 OR f.Q6_acq_diverso = 1 OR f.CausaleNonMappata = 1
                 OR f.Q12_scost_riacq = 1 THEN 'GIALLO'
            ELSE 'VERDE' END AS Livello,
       -- elenco compatto dei flag attivi
       STUFF(
            CASE WHEN f.ValutaNonConv = 1     THEN ',Q4 valuta non convertita' ELSE '' END +
            CASE WHEN f.Q9_qty_neg = 1        THEN ',Q9 quantita negativa' ELSE '' END +
            CASE WHEN f.Q1_scost_mago = 2     THEN ',Q1 scost. Mago >20%' WHEN f.Q1_scost_mago = 1 THEN ',Q1 scost. Mago >5%' ELSE '' END +
            -- Q2 (WAP Mago azzerato) e' INFORMATIVO: NON va nella riga di sintesi (rumore). Resta nella
            -- colonna WAP Mago (=azzerato) e nei numeri della scheda; la colonna Q2_mago_rotto resta per query.
            CASE WHEN f.Q3_oneri_spariti = 1  THEN ',Q3 oneri spariti' ELSE '' END +
            CASE WHEN f.Q5_salto_mom = 1      THEN ',Q5 salto costo MoM' ELSE '' END +
            CASE WHEN f.Q6_acq_diverso = 1    THEN ',Q6 acquisto a prezzo anomalo' ELSE '' END +
            CASE WHEN f.CausaleNonMappata = 1 THEN ',Q11 causale non mappata' ELSE '' END +
            CASE WHEN f.Q12_scost_riacq = 1   THEN ',Q12 scost. dal riacquisto >25%' ELSE '' END,
            1, 1, '') AS Flags,
       c.Stato, c.Nota, c.Utente, c.DataStato
FROM flags f
LEFT JOIN kodice.qualita_certificazione c ON c.Item = f.Item AND c.Anno = f.Anno AND c.Mese = f.Mese;
GO

-- Esempi:
--   SELECT Livello, COUNT(*) FROM kodice.vw_qualita_costo WHERE Anno=2026 AND Mese=4 GROUP BY Livello;
--   SELECT * FROM kodice.vw_qualita_costo WHERE Anno=2026 AND Mese=4 AND Livello<>'VERDE' AND Stato IS NULL;

-- =============================================================================
-- costo_mese_stato — CONSOLIDAMENTO MENSILE del costo (certifica prezzi del mese).
-- -----------------------------------------------------------------------------
-- L'amministrazione, quando ha caricato TUTTI i documenti del mese, CONSOLIDA i prezzi:
-- da quel momento il costo del mese e' la base solida per le vendite del mese successivo.
-- Finche' non c'e' la riga CONSOLIDATO, il mese e' "in formazione" = stima incompleta.
IF OBJECT_ID('kodice.costo_mese_stato', 'U') IS NULL
CREATE TABLE kodice.costo_mese_stato (
    Anno       smallint     NOT NULL,
    Mese       tinyint      NOT NULL,
    Stato      varchar(20)  NOT NULL,   -- CONSOLIDATO (assenza riga = IN_FORMAZIONE)
    Utente     varchar(100) NULL,
    DataStato  datetime     NULL,
    Nota       varchar(500) NULL,
    CONSTRAINT PK_costo_mese_stato PRIMARY KEY (Anno, Mese)
);
GO
