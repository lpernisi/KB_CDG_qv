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
        SELECT EntryId, SaleOrdId, InternalOrdNo, OrderDate, OrderInvRsn, Customer, InvoicingCustomer FROM (
            SELECT lk.EntryId, o.SaleOrdId, o.InternalOrdNo, CAST(o.OrderDate AS date) AS OrderDate, o.InvRsn AS OrderInvRsn, o.Customer,
                   ISNULL(NULLIF(co.InvoicingCustomer,''), o.Customer) AS InvoicingCustomer,   -- cliente di FATTURAZIONE (anagrafica)
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
            LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions co ON co.Customer = o.Customer
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
             AND sd.DocumentType IN (3407878,3407874,3407876)
             AND (sd.CustSupp = ord.Customer OR sd.CustSupp = ord.InvoicingCustomer)        UNION ALL
        SELECT ord.EntryId, sd.SaleDocId, CAST(sd.DocumentDate AS date), sd.DocumentType, 2  -- AMAZON (1 salto)
        FROM ordine ord
        -- SOLO derivazioni VERE ordine->fattura: 27066387 Fattura Immediata, 27066391 Ricevuta Fiscale, 27066389 Nota Credito.
        -- (escluso 27066370 "Movimento Magazzino" generico e altri tipi contabili: pescavano fatture sbagliate/vecchie.)
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences xf ON xf.OriginDocID = ord.SaleOrdId
             AND xf.DerivedDocType IN (27066387,27066391,27066389)
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.SaleDocId = xf.DerivedDocID AND sd.DocumentType IN (3407878,3407874,3407876)
             AND (sd.CustSupp = ord.Customer OR sd.CustSupp = ord.InvoicingCustomer)        UNION ALL
        SELECT ord.EntryId, sd.SaleDocId, CAST(sd.DocumentDate AS date), sd.DocumentType, 3  -- B2B via DDT (2 salti)
        FROM ordine ord
        -- 1deg salto ordine->DDT (27066383 Documento di Trasporto), 2deg salto DDT->fattura (tipi fattura veri).
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x1 ON x1.OriginDocID = ord.SaleOrdId AND x1.DerivedDocType = 27066383
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x2 ON x2.OriginDocID = x1.DerivedDocID
             AND x2.DerivedDocType IN (27066387,27066391,27066389)
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.SaleDocId = x2.DerivedDocID AND sd.DocumentType IN (3407878,3407874,3407876)
             AND (sd.CustSupp = ord.Customer OR sd.CustSupp = ord.InvoicingCustomer)    ),
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

-- =============================================================================
-- kodice.carico_fattura  — materializza il legame CARICO MERCE -> BOLLA -> FATTURA
-- con la DATA DI COMPETENZA della fattura (AccrualDate). Evita di rifare il grafo
-- CrossReferences (lento) a ogni caricamento della riconciliazione.
--   * carico = movimenti 505 KLACQ-OA, SOLO PRODOTTI (ItemType<>997), non annullati;
--   * bolla  = CrossRef (bolla 9830400 = Origin del movimento);
--   * fattura= CrossRef DerivedDocType 27066402 (derivazione VERA bolla->fattura acquisto);
--   * AccrualDate della fattura presa dal GLDetail (conto merce) risalendo all'header per PK;
--   * fattura scelta = la PIU' ANTICA entro +/-1 anno dal carico.
-- =============================================================================
IF OBJECT_ID('kodice.carico_fattura', 'U') IS NOT NULL DROP TABLE kodice.carico_fattura;
GO
CREATE TABLE kodice.carico_fattura (
    Anno         smallint NOT NULL,
    MovEntryId   int      NOT NULL,
    MovDate      date     NULL,
    ValPuro      float    NULL,        -- valore carico merce PRODOTTI (KLACQ-OA, EUR)
    FattId       int      NULL,
    FattAccrual  date     NULL,        -- competenza contabile della fattura collegata (NULL = nessuna fattura)
    CONSTRAINT PK_carico_fattura PRIMARY KEY (MovEntryId)
);
GO
CREATE OR ALTER PROCEDURE kodice.usp_build_carico_fattura
    @Anno smallint
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM kodice.carico_fattura WHERE Anno = @Anno;

    ;WITH carico AS (
        SELECT h.EntryId, MIN(CAST(h.PostingDate AS date)) MovDate,
               SUM(d.LineAmount*CASE WHEN h.Currency NOT IN ('','EUR') AND h.Fixing>0 THEN h.Fixing ELSE 1 END) val
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntries h
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId=h.EntryId
        LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
        WHERE h.WAPMovementType=2032533505 AND h.InvRsn='KLACQ-OA' AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
          AND h.CancelPhase1='0' AND h.CancelPhase2='0' AND YEAR(h.PostingDate)=@Anno
        GROUP BY h.EntryId),
    m2b AS (SELECT DISTINCT c.EntryId, c.MovDate, x.OriginDocID AS BollaId
            FROM carico c JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x ON x.DerivedDocID=c.EntryId
            JOIN KODICEBAGNO_4.dbo.MA_PurchaseDoc b ON b.PurchaseDocId=x.OriginDocID AND b.DocumentType=9830400),
    fattacc AS (SELECT je.CRRefID AS FattId, MIN(CAST(g.AccrualDate AS date)) Accrual
            FROM KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g
            JOIN kodice.conti_quadratura q ON q.Account=g.Account AND q.Componente='MATERIALE' AND q.Ruolo='ACQUISTO'
            JOIN KODICEBAGNO_4.dbo.MA_JournalEntries je ON je.JournalEntryId=g.JournalEntryId AND je.CRRefType=27066402
            WHERE g.AccrualDate >= DATEFROMPARTS(@Anno-1,1,1)
            GROUP BY je.CRRefID),
    b2f AS (SELECT DISTINCT m.EntryId, m.MovDate, f.PurchaseDocId AS FattId, fa.Accrual
            FROM m2b m JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x2 ON x2.OriginDocID=m.BollaId AND x2.DerivedDocType=27066402
            JOIN KODICEBAGNO_4.dbo.MA_PurchaseDoc f ON f.PurchaseDocId=x2.DerivedDocID AND f.DocumentType=9830401
            JOIN fattacc fa ON fa.FattId=f.PurchaseDocId
            WHERE ABS(DATEDIFF(day, m.MovDate, fa.Accrual)) <= 365),
    fr AS (SELECT EntryId, FattId, Accrual, ROW_NUMBER() OVER (PARTITION BY EntryId ORDER BY Accrual) rn FROM b2f)
    INSERT INTO kodice.carico_fattura (Anno, MovEntryId, MovDate, ValPuro, FattId, FattAccrual)
    SELECT @Anno, c.EntryId, c.MovDate, c.val, fr.FattId, fr.Accrual
    FROM carico c LEFT JOIN fr ON fr.EntryId=c.EntryId AND fr.rn=1
    OPTION (MAXDOP 1);
END
GO

-- =============================================================================
-- kodice.raccordo_proposto — RICERCA CONTRARIA: spedizioni senza fattura (Insieme A) ri-agganciate
-- a fatture ORFANE senza aggancio a movimento (Insieme B), per cliente + articolo in comune +
-- |data spedizione - data fattura| <= 90 gg. Finestra fatture = anno +/- 90 gg.
-- Serve per le fatture fatte MANUALMENTE senza riferimento all'ordine (il link in avanti non le trova).
-- Stato: PROPOSTO (default) -> CONFERMATO / RIFIUTATO da frontend. NumCandidati>1 = scelta multipla.
-- =============================================================================
IF OBJECT_ID('kodice.raccordo_proposto', 'U') IS NOT NULL DROP TABLE kodice.raccordo_proposto;
GO
CREATE TABLE kodice.raccordo_proposto (
    Anno          smallint NOT NULL,
    MovEntryId    int      NOT NULL,
    FatturaId     int      NOT NULL,
    CustSupp      varchar(20) NULL,
    MovDate       date     NULL,
    FatturaDate   date     NULL,
    GgDiff        int      NULL,
    ArtComuni     int      NULL,        -- nr articoli in comune (forza del match)
    NumCandidati  int      NULL,        -- quante fatture candidate ha questa spedizione
    Stato         varchar(12) NOT NULL CONSTRAINT DF_racc_stato DEFAULT 'PROPOSTO',
    CONSTRAINT PK_raccordo_proposto PRIMARY KEY (MovEntryId, FatturaId)
);
GO
CREATE OR ALTER PROCEDURE kodice.usp_build_raccordo_proposto
    @Anno smallint
AS
BEGIN
    SET NOCOUNT ON;

    -- REBUILD CONSERVATIVO: salva gli stati gia' lavorati (CONFERMATO/RIFIUTATO) e li riapplica
    -- a fine ricostruzione, cosi' un rebuild non cancella il lavoro manuale fatto da frontend.
    DECLARE @stati TABLE (MovEntryId int, FatturaId int, Stato varchar(12), PRIMARY KEY (MovEntryId, FatturaId));
    INSERT INTO @stati (MovEntryId, FatturaId, Stato)
        SELECT MovEntryId, FatturaId, Stato FROM kodice.raccordo_proposto
        WHERE Anno = @Anno AND Stato <> 'PROPOSTO';

    DELETE FROM kodice.raccordo_proposto WHERE Anno = @Anno;

    DECLARE @binf date = DATEADD(day, -90, DATEFROMPARTS(@Anno,1,1));
    DECLARE @bsup date = DATEADD(day,  91, DATEFROMPARTS(@Anno,12,31));

    ;WITH A AS (   -- spedizioni dell'anno SENZA fattura agganciata (prodotti, no sostituzioni 1300*)
        SELECT vl.MovEntryId, h.CustSupp, vl.MovDate
        FROM kodice.vendite_link vl
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = vl.MovEntryId
        WHERE vl.Modo IN ('SOLO_ORDINE','NESSUN_ORDINE') AND vl.InternalOrdNo NOT LIKE '1300%'
          AND YEAR(vl.MovDate) = @Anno),
    B AS (         -- fatture ORFANE (nessun movimento le aggancia) nella finestra anno +/- 90 gg
        SELECT sd.SaleDocId, sd.CustSupp, sd.DocumentDate
        FROM KODICEBAGNO_4.dbo.MA_SaleDoc sd
        LEFT JOIN (SELECT DISTINCT FatturaId FROM kodice.vendite_link WHERE FatturaId IS NOT NULL) v
               ON v.FatturaId = sd.SaleDocId
        WHERE sd.DocumentType IN ('3407874','3407878','3407876') AND v.FatturaId IS NULL
          AND sd.DocumentDate >= @binf AND sd.DocumentDate < @bsup
          AND sd.CustSupp NOT IN ('KODICEFR','KODICEDE','KODICEES')),
    ov AS (        -- coppie FORTI: stesso cliente, |data|<=90, >=1 articolo in comune.
                   -- La fattura puo' avere un codice-KIT mentre la spedizione scarica i COMPONENTI: per
                   -- agganciarli si ESPLODE il kit fatturato (vw_distinta) e si confronta sui componenti.
        SELECT a.MovEntryId, a.CustSupp, a.MovDate, b.SaleDocId, b.DocumentDate,
               ABS(DATEDIFF(day, a.MovDate, b.DocumentDate)) AS GgDiff,
               COUNT(DISTINCT LTRIM(RTRIM(ad.Item))) AS ArtComuni
        FROM A a
        JOIN B b ON b.CustSupp = a.CustSupp AND ABS(DATEDIFF(day, a.MovDate, b.DocumentDate)) <= 90
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail ad ON ad.EntryId = a.MovEntryId
        JOIN KODICEBAGNO_4.dbo.MA_SaleDocDetail bd ON bd.SaleDocId = b.SaleDocId
        CROSS APPLY (   -- articoli fatturati ESPLOSI: componenti se kit, altrimenti l'articolo stesso
            SELECT LTRIM(RTRIM(dd.Component)) AS Item FROM kodice.vw_distinta dd
                 WHERE LTRIM(RTRIM(dd.BOM)) = LTRIM(RTRIM(bd.Item))
            UNION ALL
            SELECT LTRIM(RTRIM(bd.Item)) WHERE NOT EXISTS
                 (SELECT 1 FROM kodice.vw_distinta dd WHERE LTRIM(RTRIM(dd.BOM)) = LTRIM(RTRIM(bd.Item)))
        ) bx
        WHERE bx.Item = LTRIM(RTRIM(ad.Item))
        GROUP BY a.MovEntryId, a.CustSupp, a.MovDate, b.SaleDocId, b.DocumentDate,
                 ABS(DATEDIFF(day, a.MovDate, b.DocumentDate))),
    fb AS (        -- FALLBACK: spedizioni SENZA alcun candidato con articolo in comune (sostituzione TOTALE:
                   -- ordinato un prodotto, spedito altro) -> aggancio su cliente + vicinanza data, ArtComuni=0.
        SELECT a.MovEntryId, a.CustSupp, a.MovDate, b.SaleDocId, b.DocumentDate,
               ABS(DATEDIFF(day, a.MovDate, b.DocumentDate)) AS GgDiff, 0 AS ArtComuni
        FROM A a
        JOIN B b ON b.CustSupp = a.CustSupp AND ABS(DATEDIFF(day, a.MovDate, b.DocumentDate)) <= 90
        WHERE NOT EXISTS (SELECT 1 FROM ov o WHERE o.MovEntryId = a.MovEntryId)),
    allc AS (SELECT * FROM ov UNION ALL SELECT * FROM fb)
    INSERT INTO kodice.raccordo_proposto (Anno, MovEntryId, FatturaId, CustSupp, MovDate, FatturaDate, GgDiff, ArtComuni, NumCandidati)
    SELECT @Anno, m.MovEntryId, m.SaleDocId, m.CustSupp, m.MovDate, m.DocumentDate, m.GgDiff, m.ArtComuni,
           COUNT(*) OVER (PARTITION BY m.MovEntryId)
    FROM allc m
    OPTION (MAXDOP 1);

    -- riapplica gli stati lavorati salvati (solo dove la coppia esiste ancora)
    UPDATE rp SET Stato = s.Stato
    FROM kodice.raccordo_proposto rp
    JOIN @stati s ON s.MovEntryId = rp.MovEntryId AND s.FatturaId = rp.FatturaId
    WHERE rp.Anno = @Anno;
END
GO
