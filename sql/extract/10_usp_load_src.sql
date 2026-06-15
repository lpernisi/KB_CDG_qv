-- =============================================================================
-- 10_usp_load_src.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- Estrazione della LANDING da Mago (cross-DB, stessa istanza): righe di vendita
-- del periodo. Parametrica, idempotente.
--
-- Nomi sorgente Mago CONFERMATI sullo schema reale (oracolo: estrattore Qlik):
--   MA_SaleDoc (testata, DocumentDate), MA_SaleDocDetail (dettaglio: Item, Qty,
--   TaxableAmount).
--
-- NB: il COSTO (WAP) NON si estrae piu' qui. La sua preparazione (risalita mese,
--     esplosione kit, bonifica/eccezioni) e' del motore core.usp_prepara_costi,
--     che certifica kodice.costi_articolo_mese; il componente COSTO_VENDUTO legge
--     direttamente da li'. Vedi sql/components/20_comp_costo_venduto.sql.
-- =============================================================================

-- ---- Righe di vendita del periodo ------------------------------------------
CREATE OR ALTER PROCEDURE dbo.usp_load_righe_vendita
    @anno INT, @mese INT
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM src.righe_vendita WHERE anno = @anno AND mese = @mese;

    -- Set dei documenti di vendita VALIDI con segno (replica i filtri dell'estrattore
    -- Qlik): fatture (+1), note credito (-1). Esclusi: sostituzioni 3407873, resi 3407877,
    -- tipo 3407898, intercompany (KODICEFR/DE/ES), ProjectCode 3/4/5, taxjournal VENAUTO.
    ;WITH doc AS (
        SELECT SaleDocId, CAST(1 AS INT) AS segno
        FROM KODICEBAGNO_4.dbo.MA_SaleDoc
        WHERE DocumentType IN ('3407874','3407875','3407878')   -- Fatture
          AND ProjectCode NOT IN ('3','4','5')
          AND CustSupp NOT IN ('KODICEFR','KODICEDE','KODICEES')
          AND taxjournal <> 'VENAUTO'
        UNION ALL
        SELECT SaleDocId, -1
        FROM KODICEBAGNO_4.dbo.MA_SaleDoc
        WHERE DocumentType IN ('3407876')                       -- Note credito (sottraggono)
          AND CustSupp NOT IN ('KODICEFR','KODICEDE','KODICEES')
          AND taxjournal <> 'VENAUTO'
    ),
    -- Righe del periodo, classificate per tipo. LTRIM/RTRIM sul codice (in Mago ci sono
    -- codici con spazi che romperebbero i join a kodice/MA_Items). Scarto le righe nota
    -- (item vuoto). Classe 'T' = riga di trasporto (item 'SPESEDITRASPORTO'), da SPALMARE;
    -- altrimenti tipo da MA_Items.IsGood (MERCE/SERVIZIO) o ALTRO se non in anagrafica.
    -- NB: i corrispettivi (taxjournal 'CORS') con sconto 100% hanno imponibile 0: e' corretto.
    righe AS (
        SELECT d.SaleDocId, d.Line, doc.segno,
               LTRIM(RTRIM(d.Item))                   AS item,
               CAST(d.Qty AS DECIMAL(18,4))           AS qty,
               CAST(d.TaxableAmount AS DECIMAL(18,2)) AS imponibile,
               CASE WHEN LTRIM(RTRIM(d.Item)) = 'SPESEDITRASPORTO' THEN 'T'
                    WHEN i.IsGood = 1 THEN 'MERCE'
                    WHEN i.IsGood = 0 THEN 'SERVIZIO'
                    ELSE 'ALTRO' END                   AS tipo
        FROM KODICEBAGNO_4.dbo.MA_SaleDocDetail AS d            -- dettaglio righe (singolare!)
        JOIN doc                          ON doc.SaleDocId = d.SaleDocId
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS t ON t.SaleDocId = d.SaleDocId
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS i ON i.Item = LTRIM(RTRIM(d.Item))
        WHERE YEAR(t.DocumentDate) = @anno AND MONTH(t.DocumentDate) = @mese
          AND LTRIM(RTRIM(d.Item)) <> ''
    ),
    -- Per documento: TRASPORTO da spalmare = righe SPESEDITRASPORTO + ShippingCharges di
    -- testata; BASE di riparto = imponibile delle righe NON di trasporto (articoli+servizi).
    agg AS (
        SELECT r.SaleDocId,
               SUM(CASE WHEN r.tipo = 'T' THEN r.imponibile ELSE 0 END)
                   + MAX(CAST(ISNULL(sh.ShippingCharges, 0) AS DECIMAL(18,4)))  AS trasporto,
               SUM(CASE WHEN r.tipo <> 'T' THEN r.imponibile ELSE 0 END)         AS base
        FROM righe AS r
        LEFT JOIN KODICEBAGNO_4.dbo.MA_SaleDocSummary AS sh ON sh.SaleDocId = r.SaleDocId
        GROUP BY r.SaleDocId
    )
    -- Grana RIGA/articolo. Inserisco solo le righe articolo (NON quelle di trasporto, che
    -- vengono spalmate sui prodotti per confrontabilita' tra marketplace col trasporto a
    -- parte vs inglobato). Ricavo = (imponibile + quota trasporto) * segno, quota = trasporto
    -- * imponibile/base. Segno anche sulla quantita' (nota credito => negativi). base=0 => quota 0.
    INSERT INTO src.righe_vendita (anno, mese, sale_doc_id, line, codice_articolo, tipo_articolo, quantita, ricavo_netto)
    SELECT
        @anno, @mese,
        r.SaleDocId,
        r.Line,
        r.item,
        r.tipo,
        r.qty * r.segno,                                            -- quantita (con segno)
        CAST(
            ( r.imponibile
              + CASE WHEN a.base <> 0 THEN a.trasporto * (r.imponibile / a.base) ELSE 0 END
            ) * r.segno
        AS DECIMAL(18,2))                                           -- ricavo netto = imponibile + quota trasporto recuperato
    FROM righe AS r
    JOIN agg AS a ON a.SaleDocId = r.SaleDocId
    WHERE r.tipo <> 'T';                                            -- righe trasporto: spalmate, non inserite

    -- ---- SOSTITUZIONI GRATUITE -------------------------------------------------
    -- Merce spedita gratis al cliente (garanzia/sostituzione): documenti di SOSTITUZIONE
    -- (MA_SaleDoc DocumentType 3407873). NON hanno fattura -> 0 ricavo, ma il COSTO del
    -- materiale e' una PERDITA e DEVE stare nel COGS: le carico come righe fatto con
    -- ricavo_netto = 0; il componente COSTO_VENDUTO le valorizza (qta x costo certificato).
    -- SELEZIONE = regola dell'estrattore QLIK (oracolo, vedi docs/estrattore-qlik.md, 2deg blocco):
    --   (cliente NON B2B  AND ProjectCode NOT IN 3/4/5)  OR  (cliente B2B AND InvRsn LIKE 'KLSOST%').
    --   ProjectCode 3/4/5 = suddivisione con RICARICO / cambio articolo / spostamenti -> NON sono regali.
    --   B2B = categoria cliente 'BTOB' (MA_CustSuppCustomerOptions.Category = 'BTOB', "BTOB Tradizionale").
    --   Esclusi intercompany + 149449/24209.
    INSERT INTO src.righe_vendita (anno, mese, sale_doc_id, line, codice_articolo, tipo_articolo, quantita, ricavo_netto)
    SELECT @anno, @mese, d.SaleDocId, d.Line, LTRIM(RTRIM(d.Item)),
           CASE WHEN i.IsGood = 1 THEN 'MERCE' WHEN i.IsGood = 0 THEN 'SERVIZIO' ELSE 'ALTRO' END,
           CAST(d.Qty AS DECIMAL(18,4)),                            -- quantita uscita (positiva, come una vendita)
           0                                                        -- ricavo 0: sostituzione gratuita
    FROM KODICEBAGNO_4.dbo.MA_SaleDocDetail AS d
    JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS t ON t.SaleDocId = d.SaleDocId
    LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions AS opt
           ON opt.Customer = t.CustSupp AND opt.CustSuppType = 3211264
    LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS i ON i.Item = LTRIM(RTRIM(d.Item))
    WHERE t.DocumentType = '3407873'
      AND ( (ISNULL(opt.Category,'') <> 'BTOB' AND t.ProjectCode NOT IN ('3','4','5'))
            OR (opt.Category = 'BTOB' AND t.InvRsn LIKE 'KLSOST%') )
      AND t.CustSupp NOT IN ('KODICEFR','KODICEDE','KODICEES','149449','24209')
      AND YEAR(t.DocumentDate) = @anno AND MONTH(t.DocumentDate) = @mese
      AND LTRIM(RTRIM(d.Item)) <> '';
END
GO
