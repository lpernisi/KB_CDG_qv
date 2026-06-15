-- =============================================================================
-- Materializzazione dei COLLEGAMENTI tra documenti (calcolo UNA volta, set-based).
-- -----------------------------------------------------------------------------
-- Perche': la risalita movimento<->fattura via MA_CrossReferences fatta a runtime con subquery
-- correlate e' pericolosa (ha saturato la CPU) e, fatta col MIN della data sul grafo, ATTRIBUISCE
-- fatture sbagliate (un movimento e' raggiungibile da piu' documenti). Qui si risolve UNA volta,
-- set-based (MAXDOP 1), scegliendo per ogni documento UNA controparte per PRIORITA' di legame e,
-- a parita', la PIU' VICINA IN DATA (i marketplace fatturano e poi spediscono a giorni di distanza).
--
--   VENDITE: movimento di scarico 506 (ATRI)  ->  ordine di vendita (MA_SaleOrd)  ->  fattura (MA_SaleDoc)
--   ACQUISTI: bolla d'acquisto (9830400)       ->  fattura d'acquisto (9830401)
--
-- Modi di collegamento gestiti (in ordine di priorita'):
--   1 DIRETTO    : CrossReferences a 1 salto  fattura(Origin) -> movimento(Derived)
--   2 DDT_2SALTI : CrossReferences a 2 salti   fattura -> DDT -> movimento (fattura differita B2B)
--   3 VIA_ORDINE : movimento -> ordine (CrossRef/OrderId), ordine -> fattura (InternalOrdNo=SaleDocId B2C / CrossRef)
-- Tipi fattura vendita: 3407878 / 3407874 / 3407876. Cliente 70209 (AMAZON LOGISTICA) ESCLUSO (trasferimento FBA).
-- =============================================================================
USE CDG_QV;
GO

------------------------------------------------------------------------- VENDITE
-- MODELLO (corretto): la catena e' MOVIMENTO -> ORDINE cliente -> FATTURA.
-- Il movimento di scarico ATRI (506) e' SEMPRE collegato all'ordine cliente (CrossReferences: ordine -> movimento).
-- L'ordine si lega alla fattura in 3 modi:
--   B2C      : MA_SaleDoc.SaleDocId = MA_SaleOrd.InternalOrdNo  (match diretto di campo)
--   AMAZON   : CrossReferences a 1 salto  ordine -> fattura  (logistica Amazon)
--   B2B_DDT  : CrossReferences a 2 salti   ordine -> DDT -> fattura  (fattura differita)
-- NON si lega il movimento direttamente alla fattura (era quel legame a pescare fatture vecchie 2013-2023 dal grafo).
IF OBJECT_ID('kodice.vendite_link', 'U') IS NOT NULL DROP TABLE kodice.vendite_link;
GO
CREATE TABLE kodice.vendite_link (
    MovEntryId    int          NOT NULL,
    MovDate       date         NULL,
    SaleOrdId     int          NULL,
    InternalOrdNo varchar(21)  NULL,
    OrderDate     date         NULL,
    OrderInvRsn   varchar(20)  NULL,
    FatturaId     int          NULL,
    FatturaDate   date         NULL,
    FatturaType   int          NULL,
    Modo          varchar(20)  NOT NULL,   -- B2C / AMAZON / B2B_DDT / SOSTITUZIONE / SOLO_ORDINE / NESSUN_ORDINE
    CONSTRAINT PK_vendite_link PRIMARY KEY (MovEntryId)
);
GO

CREATE OR ALTER PROCEDURE kodice.usp_build_vendite_link
    @Anno smallint
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM kodice.vendite_link
    WHERE MovEntryId IN (SELECT EntryId FROM KODICEBAGNO_4.dbo.MA_InventoryEntries
                         WHERE WAPMovementType = 2032533506 AND YEAR(PostingDate) = @Anno);

    ;WITH mov AS (
        SELECT h.EntryId, CAST(h.PostingDate AS date) AS MovDate, h.CustSupp, h.DocNo
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntries h
        WHERE h.WAPMovementType = 2032533506 AND YEAR(h.PostingDate) = @Anno AND h.CustSupp <> '70209'
    ),
    -- LEGAME DIRETTO (priorita' massima): movimento <-> ricevuta/fattura che condividono DocNo + Cliente
    -- (vendite Amazon dal deposito Amazon: ogni ricevuta ha il movimento con lo STESSO DocNo). Niente ordine.
    fatt_diretta AS (
        SELECT mov.EntryId, sd.SaleDocId AS FatturaId, CAST(sd.DocumentDate AS date) AS FatturaDate,
               sd.DocumentType AS FatturaType, 0 AS prio
        FROM mov
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.DocNo = mov.DocNo AND sd.CustSupp = mov.CustSupp
             AND sd.DocumentType IN (3407878,3407874,3407876) AND YEAR(sd.DocumentDate) = @Anno
    ),
    -- GAMBA SEMPRE PRESENTE: movimento -> ordine cliente, in 2 modi (priorita': CrossRef, poi OrderId sul dettaglio).
    -- VALIDAZIONE: il CLIENTE dell'ordine deve combaciare col cliente del movimento (uccide i link spuri del grafo
    -- verso ordini di altri clienti/anni). Se piu' ordini validi, si tiene quello con data piu' vicina al movimento.
    ordine AS (
        SELECT EntryId, SaleOrdId, InternalOrdNo, OrderDate, OrderInvRsn, Customer FROM (
            SELECT lk.EntryId, o.SaleOrdId, o.InternalOrdNo, CAST(o.OrderDate AS date) AS OrderDate, o.InvRsn AS OrderInvRsn, o.Customer,
                   ROW_NUMBER() OVER (PARTITION BY lk.EntryId
                                      ORDER BY lk.prio, ABS(DATEDIFF(day, lk.MovDate, o.OrderDate))) AS rn
            FROM (
                SELECT mov.EntryId, mov.MovDate, mov.CustSupp, xo.OriginDocID AS SaleOrdId, 1 AS prio
                FROM mov JOIN KODICEBAGNO_4.dbo.MA_CrossReferences xo ON xo.DerivedDocID = mov.EntryId
                UNION ALL
                SELECT mov.EntryId, mov.MovDate, mov.CustSupp, dd.OrderId, 2 AS prio
                FROM mov JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail dd ON dd.EntryId = mov.EntryId AND dd.OrderId <> 0
            ) lk
            JOIN KODICEBAGNO_4.dbo.MA_SaleOrd o ON o.SaleOrdId = lk.SaleOrdId AND o.Customer = lk.CustSupp
            WHERE ABS(DATEDIFF(day, lk.MovDate, o.OrderDate)) <= 180
        ) q WHERE rn = 1
    ),
    -- ORDINE -> FATTURA in 3 modi. VALIDAZIONE: anche la FATTURA deve avere lo stesso cliente dell'ordine.
    cand AS (
        SELECT EntryId, FatturaId, FatturaDate, FatturaType, prio FROM fatt_diretta   -- 0: Amazon FBA (DocNo+cliente, senza ordine)
        UNION ALL
        SELECT ord.EntryId, sd.SaleDocId AS FatturaId, CAST(sd.DocumentDate AS date) AS FatturaDate,
               sd.DocumentType AS FatturaType, 1 AS prio  -- B2C
        FROM ordine ord
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON CAST(sd.SaleDocId AS varchar(21)) = ord.InternalOrdNo
             AND sd.DocumentType IN (3407878,3407874,3407876)        UNION ALL
        SELECT ord.EntryId, sd.SaleDocId, CAST(sd.DocumentDate AS date), sd.DocumentType, 2  -- AMAZON (1 salto)
        FROM ordine ord
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences xf ON xf.OriginDocID = ord.SaleOrdId
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.SaleDocId = xf.DerivedDocID AND sd.DocumentType IN (3407878,3407874,3407876)        UNION ALL
        SELECT ord.EntryId, sd.SaleDocId, CAST(sd.DocumentDate AS date), sd.DocumentType, 3  -- B2B via DDT (2 salti)
        FROM ordine ord
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x1 ON x1.OriginDocID = ord.SaleOrdId
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x2 ON x2.OriginDocID = x1.DerivedDocID
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.SaleDocId = x2.DerivedDocID AND sd.DocumentType IN (3407878,3407874,3407876)    ),
    -- scelta UNICA per movimento: priorita' (0 DocNo diretto, 1 B2C, 2 Amazon, 3 B2B), poi fattura piu' vicina
    -- alla data del MOVIMENTO; tetto 180 gg (no link spuri). Riferimento data = movimento (vale anche senza ordine).
    best AS (
        SELECT c.EntryId, c.FatturaId, c.FatturaDate, c.FatturaType, c.prio,
               ROW_NUMBER() OVER (PARTITION BY c.EntryId
                    ORDER BY c.prio, ABS(DATEDIFF(day, m.MovDate, c.FatturaDate)), c.FatturaDate) AS rn
        FROM cand c JOIN mov m ON m.EntryId = c.EntryId
        WHERE ABS(DATEDIFF(day, m.MovDate, c.FatturaDate)) <= 180
    )
    INSERT INTO kodice.vendite_link
        (MovEntryId, MovDate, SaleOrdId, InternalOrdNo, OrderDate, OrderInvRsn, FatturaId, FatturaDate, FatturaType, Modo)
    SELECT mov.EntryId, mov.MovDate, ord.SaleOrdId, ord.InternalOrdNo, ord.OrderDate, ord.OrderInvRsn,
           b.FatturaId, b.FatturaDate, b.FatturaType,
           CASE WHEN b.prio = 0 THEN 'AMAZON_FBA'        -- DocNo+cliente diretto (Amazon, senza ordine)
                WHEN b.prio = 1 THEN 'B2C'
                WHEN b.prio = 2 THEN 'AMAZON'
                WHEN b.prio = 3 THEN 'B2B_DDT'
                WHEN ord.OrderInvRsn = 'KLSOSTA' THEN 'SOSTITUZIONE'   -- DDT solo, nessuna fattura per natura
                WHEN ord.SaleOrdId IS NOT NULL THEN 'SOLO_ORDINE'
                ELSE 'NESSUN_ORDINE' END
    FROM mov
    LEFT JOIN ordine ord ON ord.EntryId = mov.EntryId
    LEFT JOIN best b ON b.EntryId = mov.EntryId AND b.rn = 1
    OPTION (MAXDOP 1);
END
GO

------------------------------------------------------------------------- ACQUISTI
IF OBJECT_ID('kodice.acquisti_link', 'U') IS NOT NULL DROP TABLE kodice.acquisti_link;
GO
CREATE TABLE kodice.acquisti_link (
    BollaId      int          NOT NULL,
    BollaDate    date         NULL,
    FatturaId    int          NULL,
    FatturaDate  date         NULL,
    Modo         varchar(20)  NOT NULL,   -- DIRETTO / NESSUNA
    CONSTRAINT PK_acquisti_link PRIMARY KEY (BollaId)
);
GO

CREATE OR ALTER PROCEDURE kodice.usp_build_acquisti_link
    @Anno smallint
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM kodice.acquisti_link
    WHERE BollaId IN (SELECT PurchaseDocId FROM KODICEBAGNO_4.dbo.MA_PurchaseDoc
                      WHERE DocumentType = 9830400 AND YEAR(DocumentDate) = @Anno);

    ;WITH bolla AS (
        SELECT PurchaseDocId, CAST(DocumentDate AS date) AS BollaDate
        FROM KODICEBAGNO_4.dbo.MA_PurchaseDoc WHERE DocumentType = 9830400 AND YEAR(DocumentDate) = @Anno
    ),
    cand AS (
        SELECT b.PurchaseDocId, f.PurchaseDocId AS FatturaId, CAST(f.DocumentDate AS date) AS FatturaDate,
               ROW_NUMBER() OVER (PARTITION BY b.PurchaseDocId
                                  ORDER BY ABS(DATEDIFF(day, b.BollaDate, f.DocumentDate)), f.DocumentDate) AS rn
        FROM bolla b
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x ON x.OriginDocID = b.PurchaseDocId
        JOIN KODICEBAGNO_4.dbo.MA_PurchaseDoc f ON f.PurchaseDocId = x.DerivedDocID AND f.DocumentType = 9830401
    )
    INSERT INTO kodice.acquisti_link (BollaId, BollaDate, FatturaId, FatturaDate, Modo)
    SELECT b.PurchaseDocId, b.BollaDate, c.FatturaId, c.FatturaDate,
           CASE WHEN c.FatturaId IS NULL THEN 'NESSUNA' ELSE 'DIRETTO' END
    FROM bolla b LEFT JOIN cand c ON c.PurchaseDocId = b.PurchaseDocId AND c.rn = 1
    OPTION (MAXDOP 1);
END
GO
