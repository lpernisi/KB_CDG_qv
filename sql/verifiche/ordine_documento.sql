-- =============================================================================
-- ordine_documento.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- PONTE ORDINE -> DOCUMENTO di vendita, materializzato (calcolo UNA volta, set-based).
-- Serve a chi ha un riferimento all'ORDINE (es. le fatture dei vettori: la colonna
-- "N. rif. Cliente" = MA_SaleOrd.InternalOrdNo) e deve risalire al DOCUMENTO contabile
-- (fattura/ricevuta) su cui il controllo di gestione calcola ricavo e margini.
--
-- E' la stessa logica gia' validata in kodice.vendite_link (parte ordine->fattura),
-- ma con chiave l'ORDINE invece del movimento di magazzino: cosi' si aggancia anche
-- quando non esiste un movimento 506 collegato.
--
-- Ordine -> fattura/ricevuta (tipi 3407878 / 3407874) in 3 modi, per priorita':
--   B2C     : MA_SaleDoc.SaleDocId = MA_SaleOrd.InternalOrdNo  (per il web l'ordine "e'" il documento)
--   AMAZON  : MA_CrossReferences a 1 salto   ordine -> fattura
--   B2B_DDT : MA_CrossReferences a 2 salti    ordine -> DDT -> fattura (fattura differita)
-- VALIDAZIONE: la fattura deve avere lo stesso cliente (o cliente di fatturazione) dell'ordine.
-- Le NOTE DI CREDITO (3407876) NON sono mappate qui: i resi/rientri sono trattati a parte.
-- =============================================================================
USE CDG_QV;
GO

IF OBJECT_ID('kodice.ordine_documento', 'U') IS NOT NULL DROP TABLE kodice.ordine_documento;
GO
CREATE TABLE kodice.ordine_documento (
    Anno          smallint     NOT NULL,
    SaleOrdId     int          NOT NULL,
    InternalOrdNo varchar(21)  NULL,        -- riferimento usato dai vettori ("N. rif. Cliente")
    Customer      varchar(20)  NULL,
    OrderDate     date         NULL,
    FatturaId     int          NULL,        -- documento di vendita agganciato (NULL = nessuno)
    FatturaType   int          NULL,        -- 3407878 ricevuta / 3407874 fattura
    FatturaDate   date         NULL,
    Modo          varchar(20)  NOT NULL,    -- B2C / AMAZON / B2B_DDT / SENZA_DOC
    CONSTRAINT PK_ordine_documento PRIMARY KEY (SaleOrdId)
);
GO

CREATE OR ALTER PROCEDURE kodice.usp_build_ordine_documento
    @Anno smallint = NULL   -- mantenuto per compatibilita': il raccordo si materializza COMPLETO
AS
BEGIN
    SET NOCOUNT ON;
    -- REBUILD COMPLETO (non per anno): le fatture dei vettori agganciano ordini anche a
    -- CAVALLO D'ANNO (spedizione di gennaio per un ordine di dicembre). Materializziamo
    -- l'intero raccordo ordine->documento dall'orizzonte dati del progetto (2024), una volta.
    DELETE FROM kodice.ordine_documento;

    ;WITH ord AS (
        SELECT o.SaleOrdId, LTRIM(RTRIM(o.InternalOrdNo)) AS InternalOrdNo, o.Customer,
               ISNULL(NULLIF(co.InvoicingCustomer,''), o.Customer) AS InvoicingCustomer,
               CAST(o.OrderDate AS date) AS OrderDate
        FROM KODICEBAGNO_4.dbo.MA_SaleOrd o
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions co ON co.Customer = o.Customer
        WHERE o.OrderDate >= '20240101'
    ),
    cand AS (
        -- B2C: l'ordine web "e'" il documento (InternalOrdNo numerico = SaleDocId)
        SELECT ord.SaleOrdId, sd.SaleDocId AS FatturaId, sd.DocumentType AS FatturaType,
               CAST(sd.DocumentDate AS date) AS FatturaDate, 1 AS prio
        FROM ord
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON CAST(sd.SaleDocId AS varchar(21)) = ord.InternalOrdNo
             AND sd.DocumentType IN (3407878,3407874)
             AND (sd.CustSupp = ord.Customer OR sd.CustSupp = ord.InvoicingCustomer)
        UNION ALL
        -- AMAZON: derivazione vera ordine->fattura (1 salto)
        SELECT ord.SaleOrdId, sd.SaleDocId, sd.DocumentType, CAST(sd.DocumentDate AS date), 2
        FROM ord
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences xf ON xf.OriginDocID = ord.SaleOrdId
             AND xf.DerivedDocType IN (27066387,27066391,27066389)
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.SaleDocId = xf.DerivedDocID
             AND sd.DocumentType IN (3407878,3407874)
             AND (sd.CustSupp = ord.Customer OR sd.CustSupp = ord.InvoicingCustomer)
        UNION ALL
        -- B2B via DDT (2 salti): ordine -> DDT (27066383) -> fattura
        SELECT ord.SaleOrdId, sd.SaleDocId, sd.DocumentType, CAST(sd.DocumentDate AS date), 3
        FROM ord
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x1 ON x1.OriginDocID = ord.SaleOrdId AND x1.DerivedDocType = 27066383
        JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x2 ON x2.OriginDocID = x1.DerivedDocID
             AND x2.DerivedDocType IN (27066387,27066391,27066389)
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc sd ON sd.SaleDocId = x2.DerivedDocID
             AND sd.DocumentType IN (3407878,3407874)
             AND (sd.CustSupp = ord.Customer OR sd.CustSupp = ord.InvoicingCustomer)
    ),
    best AS (
        SELECT SaleOrdId, FatturaId, FatturaType, FatturaDate,
               ROW_NUMBER() OVER (PARTITION BY SaleOrdId ORDER BY prio, FatturaDate) AS rn
        FROM cand
    )
    INSERT INTO kodice.ordine_documento
        (Anno, SaleOrdId, InternalOrdNo, Customer, OrderDate, FatturaId, FatturaType, FatturaDate, Modo)
    SELECT YEAR(ord.OrderDate), ord.SaleOrdId, ord.InternalOrdNo, ord.Customer, ord.OrderDate,
           b.FatturaId, b.FatturaType, b.FatturaDate,
           CASE WHEN b.FatturaId IS NULL THEN 'SENZA_DOC'
                WHEN ord.InternalOrdNo LIKE '[0-9][0-9]/[0-9]%' THEN 'B2B_DDT'
                WHEN ord.InternalOrdNo NOT LIKE '%[^0-9]%' THEN 'B2C'
                ELSE 'AMAZON' END
    FROM ord
    LEFT JOIN best b ON b.SaleOrdId = ord.SaleOrdId AND b.rn = 1
    OPTION (MAXDOP 1);
END
GO
