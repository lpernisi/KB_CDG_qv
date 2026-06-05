-- =============================================================================
-- verifica_giacenza_iniziale.sql   (eseguire su KODICEBAGNO_4, in SSMS)
-- -----------------------------------------------------------------------------
-- SCOPO: trovare gli articoli per cui MA_ItemsWAP (aggiornata da una procedura
-- Mago non modificabile) ha quantita'/costi NON affidabili, confrontando la
-- giacenza iniziale dell'esercizio su TRE fonti che dovrebbero coincidere:
--
--   A = MA_ItemsBalances.InitialBookInv  (FiscalYear = @AnnoEs)        -> somma per articolo (tutti i depositi/varianti)
--   B = KLProgUbicazioni.QtaIniziale     (Esercizio   = @AnnoEs)        -> somma per articolo (tutte le ubicazioni)
--   C = MA_ItemsWAP.FinalQty             (periodo che chiude a Dic @AnnoEs-1, riga totale Storage = '')
--
-- A, B, C dovrebbero essere uguali (giacenza fine anno precedente = giacenza
-- inizio anno corrente). Dove C si scosta da A e B, il dato WAP e' sospetto.
--
-- NOTE / assunzioni (verificate sullo schema reale):
--   - MA_ItemsWAP ha SOLO la riga totale Storage = '' (nessun dettaglio deposito);
--   - MA_ItemsBalances NON ha riga totale: si somma su tutti i depositi/varianti;
--   - i codici articolo possono avere spazi: si applica LTRIM/RTRIM per il join.
-- SOLA LETTURA: nessuna modifica ai dati.
-- =============================================================================

USE KODICEBAGNO_4;
GO

DECLARE @AnnoEs       SMALLINT = 2026;   -- esercizio da verificare
DECLARE @AnnoWap      SMALLINT = @AnnoEs - 1;  -- anno della giacenza finale WAP (Dic)
DECLARE @MeseWap      TINYINT  = 12;
DECLARE @Tolleranza   FLOAT    = 0.001;  -- soglia per ignorare differenze di arrotondamento

;WITH bal AS (
    SELECT LTRIM(RTRIM(Item)) AS Item, SUM(InitialBookInv) AS A_balances
    FROM dbo.MA_ItemsBalances
    WHERE FiscalYear = @AnnoEs
    GROUP BY LTRIM(RTRIM(Item))
),
ubi AS (
    SELECT LTRIM(RTRIM(Articolo)) AS Item, SUM(QtaIniziale) AS B_ubicazioni
    FROM dbo.KLProgUbicazioni
    WHERE Esercizio = @AnnoEs
    GROUP BY LTRIM(RTRIM(Articolo))
),
wap AS (
    SELECT LTRIM(RTRIM(Item)) AS Item, SUM(FinalQty) AS C_wap
    FROM dbo.MA_ItemsWAP
    WHERE Storage = ''
      AND YEAR(EndPeriodDate) = @AnnoWap AND MONTH(EndPeriodDate) = @MeseWap
    GROUP BY LTRIM(RTRIM(Item))
),
tutti AS (
    SELECT Item FROM bal
    UNION SELECT Item FROM ubi
    UNION SELECT Item FROM wap
),
j AS (
    SELECT t.Item,
           ISNULL(b.A_balances, 0)   AS A_balances,
           ISNULL(u.B_ubicazioni, 0) AS B_ubicazioni,
           ISNULL(w.C_wap, 0)        AS C_wap
    FROM tutti t
    LEFT JOIN bal b ON b.Item = t.Item
    LEFT JOIN ubi u ON u.Item = t.Item
    LEFT JOIN wap w ON w.Item = t.Item
)
-- ---- DETTAGLIO: solo gli articoli con almeno uno scostamento -----------------
SELECT
    j.Item,
    it.Description,
    CAST(j.A_balances   AS DECIMAL(18,3)) AS A_balances_init,
    CAST(j.B_ubicazioni AS DECIMAL(18,3)) AS B_ubicazioni_init,
    CAST(j.C_wap        AS DECIMAL(18,3)) AS C_wap_finale_dic_prec,
    CAST(j.A_balances - j.C_wap        AS DECIMAL(18,3)) AS delta_A_meno_C,
    CAST(j.B_ubicazioni - j.C_wap      AS DECIMAL(18,3)) AS delta_B_meno_C,
    CAST(j.A_balances - j.B_ubicazioni AS DECIMAL(18,3)) AS delta_A_meno_B,
    CASE WHEN j.C_wap = 0 AND (j.A_balances <> 0 OR j.B_ubicazioni <> 0) THEN 'WAP A ZERO (manca giacenza finale)'
         WHEN ABS(j.A_balances - j.B_ubicazioni) <= @Tolleranza AND ABS(j.A_balances - j.C_wap) > @Tolleranza THEN 'WAP DISCORDE (Balances=Ubicazioni)'
         WHEN ABS(j.A_balances - j.B_ubicazioni) > @Tolleranza THEN 'Balances <> Ubicazioni (verificare anche fonti reali)'
         ELSE 'altro' END AS diagnosi
FROM j
LEFT JOIN dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = j.Item
WHERE ABS(j.A_balances - j.C_wap)        > @Tolleranza
   OR ABS(j.B_ubicazioni - j.C_wap)      > @Tolleranza
   OR ABS(j.A_balances - j.B_ubicazioni) > @Tolleranza
ORDER BY ABS(j.A_balances - j.C_wap) DESC, ABS(j.B_ubicazioni - j.C_wap) DESC;
GO
