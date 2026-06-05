-- =============================================================================
-- core.usp_prepara_costi  —  MOTORE COSTI (snapshot dal DB, NON parte dello scaffold originale)
-- Estratto da CDG_QV per documentazione/versionamento. La fonte di verita' resta
-- l'oggetto nel database; la dashboard mostra la definizione LIVE.
-- =============================================================================

/* =====================================================================
   CDG Engine - core.usp_prepara_costi
   Fase di preparazione/bonifica costi per articolo/mese.
   Copia UNICA, parametrica sullo schema azienda (SQL dinamico).
   Sostituisce: Split_calcoloCostofineTrimestre, GetCostoMedioUltimo...,
                Split_Costi_MaterialeMensileDistinte, KB_View_CostoMedioMensile.
   Idempotente sul mese: rieseguibile, ricalcola e aggiorna lo stato.

   Esecuzione:
     EXEC core.usp_prepara_costi @schema_azienda='kodice', @anno=2026, @mese=5;
     EXEC core.usp_prepara_costi @schema_azienda='kodice';  -- => mese precedente
   ===================================================================== */
CREATE   PROCEDURE core.usp_prepara_costi
    @schema_azienda SYSNAME,
    @anno           SMALLINT = NULL,
    @mese           TINYINT  = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    /* --- competenza: default = mese precedente (dinamico, mai hardcoded) --- */
    IF @anno IS NULL OR @mese IS NULL
    BEGIN
        DECLARE @prev DATE = DATEADD(MONTH, -1, CAST(GETDATE() AS DATE));
        SET @anno = YEAR(@prev);
        SET @mese = MONTH(@prev);
    END;

    /* --- guardia anti-injection sullo schema --- */
    IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = @schema_azienda)
    BEGIN
        RAISERROR('Schema azienda inesistente: %s', 16, 1, @schema_azienda);
        RETURN;
    END;

    DECLARE @S NVARCHAR(258) = QUOTENAME(@schema_azienda);
    DECLARE @sql NVARCHAR(MAX);

    SET @sql = N'
    DECLARE @periodo INT = @anno*12 + (@mese-1);   -- chiave periodo confrontabile

    /* === 1. costo risolto per articolo "as-of" il mese di competenza =====
       ultimo mese PRESENTE <= competenza. Nessun filtro sul segno: lo zero
       viene valutato dopo, non saltato (a differenza del vecchio <>0). */
    ;WITH costo_asof AS (
        SELECT Item, Costo, Anno, Mese,
               ROW_NUMBER() OVER (PARTITION BY Item
                                  ORDER BY (Anno*12+(Mese-1)) DESC) AS rn
        FROM @@S@@.vw_costo_sorgente
        WHERE (Anno*12+(Mese-1)) <= @periodo
    )
    SELECT Item, Costo, DATEFROMPARTS(Anno,Mese,1) AS MeseCosto
    INTO #costo
    FROM costo_asof WHERE rn = 1;

    /* === 2. insieme dei KIT (articoli con distinta) ===================== */
    SELECT DISTINCT BOM AS Item INTO #kitset FROM @@S@@.vw_distinta;

    /* === 3. esplosione RICORSIVA delle distinte fino alle FOGLIE ========
       moltiplica i coefficienti lungo la catena; somma i contributi se una
       foglia e'' raggiunta per piu'' percorsi. Si costano solo le foglie
       (componenti che NON sono a loro volta distinta): niente doppio conteggio
       degli assemblati intermedi, niente drop silenzioso di sub-assiemi. */
    ;WITH esploso AS (
        SELECT d.BOM AS TopBom, d.Component, d.Qty
        FROM @@S@@.vw_distinta d
        UNION ALL
        SELECT e.TopBom, d.Component, CAST(e.Qty * d.Qty AS DECIMAL(18,6))
        FROM esploso e
        JOIN @@S@@.vw_distinta d ON d.BOM = e.Component
    )
    SELECT TopBom, Component, SUM(Qty) AS Qty
    INTO #foglie
    FROM esploso
    WHERE Component NOT IN (SELECT Item FROM #kitset)   -- solo foglie
    GROUP BY TopBom, Component
    OPTION (MAXRECURSION 100);

    /* === 4. costo e completezza dei KIT ================================= */
    SELECT
        f.TopBom AS Item,
        SUM(CASE WHEN c.Costo > 0 THEN f.Qty * c.Costo ELSE 0 END) AS Costo,
        COUNT(*) AS NComponentiTotali,
        SUM(CASE WHEN c.Costo > 0 THEN 1 ELSE 0 END) AS NComponentiValidi
    INTO #kit
    FROM #foglie f
    LEFT JOIN #costo c ON c.Item = f.Component
    GROUP BY f.TopBom;

    /* === 5. svuoto output del mese (idempotenza) ======================= */
    DELETE FROM @@S@@.costi_articolo_mese WHERE Anno=@anno AND Mese=@mese;

    /* === 6. certifico i MAGAZZINO validi (costo > 0, non kit) =========== */
    INSERT INTO @@S@@.costi_articolo_mese
        (Item,Anno,Mese,TipoArticolo,Costo,Completo,NComponentiTotali,NComponentiValidi,MeseCostoUsato)
    SELECT c.Item, @anno, @mese, ''MAGAZZINO'', c.Costo, 1, NULL, NULL, c.MeseCosto
    FROM #costo c
    WHERE c.Costo > 0
      AND c.Item NOT IN (SELECT Item FROM #kitset);

    /* === 7. certifico i KIT COMPLETI =================================== */
    INSERT INTO @@S@@.costi_articolo_mese
        (Item,Anno,Mese,TipoArticolo,Costo,Completo,NComponentiTotali,NComponentiValidi,MeseCostoUsato)
    SELECT k.Item, @anno, @mese, ''KIT'', k.Costo, 1, k.NComponentiTotali, k.NComponentiValidi, NULL
    FROM #kit k
    WHERE k.NComponentiValidi = k.NComponentiTotali
      AND k.NComponentiTotali > 0;

    /* === 8. ECCEZIONI ==================================================
       8a. articoli a magazzino con costo presente ma <= 0
       8b. foglie di KIT mancanti o <= 0 (KIT incompleto), una per colpevole */
    SELECT Item, ''COSTO_NON_VALIDO'' AS TipoEccezione, CAST('''' AS VARCHAR(40)) AS ComponenteColpevole,
           CONCAT(''Costo '', CONVERT(VARCHAR(30), Costo)) AS Dettaglio
    INTO #ecc
    FROM #costo
    WHERE Costo <= 0
      AND Item NOT IN (SELECT Item FROM #kitset)

    UNION ALL
    SELECT f.TopBom,
           CASE WHEN c.Item IS NULL THEN ''COSTO_MANCANTE'' ELSE ''COSTO_NON_VALIDO'' END,
           f.Component,
           CASE WHEN c.Item IS NULL THEN ''Foglia senza costo <= competenza''
                ELSE CONCAT(''Foglia con costo '', CONVERT(VARCHAR(30), c.Costo)) END
    FROM #foglie f
    LEFT JOIN #costo c ON c.Item = f.Component
    WHERE c.Item IS NULL OR c.Costo <= 0;

    /* aggiungo la riga riassuntiva KIT_INCOMPLETO per i kit con foglie mancanti/non valide */
    INSERT INTO #ecc (Item, TipoEccezione, ComponenteColpevole, Dettaglio)
    SELECT k.Item, ''KIT_INCOMPLETO'', '''',
           CONCAT(k.NComponentiValidi, ''/'', k.NComponentiTotali, '' componenti validi'')
    FROM #kit k
    WHERE k.NComponentiValidi < k.NComponentiTotali;

    /* 8c. upsert eccezioni con ciclo di vita ============================
       - presenti ora  -> APERTA (riaperta se era risolta)
       - non piu'' presenti per il mese -> RISOLTA */
    MERGE @@S@@.costi_eccezioni AS t
    USING (
        SELECT Item, @anno AS Anno, @mese AS Mese, TipoEccezione, ComponenteColpevole, MAX(Dettaglio) AS Dettaglio
        FROM #ecc GROUP BY Item, TipoEccezione, ComponenteColpevole
    ) AS s
    ON  t.Item=s.Item AND t.Anno=s.Anno AND t.Mese=s.Mese
    AND t.TipoEccezione=s.TipoEccezione AND t.ComponenteColpevole=s.ComponenteColpevole
    WHEN MATCHED THEN UPDATE SET
        Stato=''APERTA'', Dettaglio=s.Dettaglio, DataRisoluzione=NULL,
        DataRilevazione=CASE WHEN t.Stato=''APERTA'' THEN t.DataRilevazione ELSE SYSUTCDATETIME() END
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (Item,Anno,Mese,TipoEccezione,ComponenteColpevole,Dettaglio,Stato)
        VALUES (s.Item,s.Anno,s.Mese,s.TipoEccezione,s.ComponenteColpevole,s.Dettaglio,''APERTA'')
    WHEN NOT MATCHED BY SOURCE AND t.Anno=@anno AND t.Mese=@mese AND t.Stato=''APERTA'' THEN
        UPDATE SET Stato=''RISOLTA'', DataRisoluzione=SYSUTCDATETIME();

    /* === 9. controllo mese ============================================ */
    DECLARE @nArt INT  = (SELECT COUNT(*) FROM @@S@@.costi_articolo_mese WHERE Anno=@anno AND Mese=@mese AND TipoArticolo=''MAGAZZINO'');
    DECLARE @nKit INT  = (SELECT COUNT(*) FROM @@S@@.costi_articolo_mese WHERE Anno=@anno AND Mese=@mese AND TipoArticolo=''KIT'');
    DECLARE @nEcc INT  = (SELECT COUNT(*) FROM @@S@@.costi_eccezioni WHERE Anno=@anno AND Mese=@mese AND Stato=''APERTA'');

    MERGE @@S@@.prep_controllo_mesi AS t
    USING (SELECT @anno AS Anno, @mese AS Mese) AS s
    ON t.Anno=s.Anno AND t.Mese=s.Mese
    WHEN MATCHED THEN UPDATE SET
        DataEsecuzione=SYSUTCDATETIME(), NArticoli=@nArt, NKit=@nKit,
        NEccezioniAperte=@nEcc, Stato=CASE WHEN @nEcc=0 THEN ''PRONTO'' ELSE ''CON_ANOMALIE'' END
    WHEN NOT MATCHED THEN
        INSERT (Anno,Mese,DataEsecuzione,NArticoli,NKit,NEccezioniAperte,Stato)
        VALUES (@anno,@mese,SYSUTCDATETIME(),@nArt,@nKit,@nEcc,CASE WHEN @nEcc=0 THEN ''PRONTO'' ELSE ''CON_ANOMALIE'' END);

    SELECT @anno AS Anno, @mese AS Mese, @nArt AS Articoli, @nKit AS Kit, @nEcc AS EccezioniAperte;
    ';

    SET @sql = REPLACE(@sql, '@@S@@', @S);

    EXEC sys.sp_executesql @sql,
         N'@anno SMALLINT, @mese TINYINT',
         @anno=@anno, @mese=@mese;
END
GO
