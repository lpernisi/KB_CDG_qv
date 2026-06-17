-- =============================================================================
-- 03_struttura_trasporti.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- LANDING delle fatture dei vettori: una riga per SPEDIZIONE del riepilogativo
-- (il CSV/Excel che l'amministrazione scarica da Google Sheet e carica dalla
-- dashboard). E' la fonte del costo di TRASPORTO REALE (fatturato dai vettori),
-- distinto dal trasporto STIMATO usato altrove.
--
-- Aggancio al venduto: la colonna "N. rif. Cliente" del riepilogativo e' il
-- NUMERO ORDINE di vendita (MA_SaleOrd.InternalOrdNo). Il controllo di gestione
-- ragiona sui DOCUMENTI (fatture/ricevute/note credito): il ponte ordine->documento
-- e' in kodice.ordine_documento (vedi sql/verifiche/ordine_documento.sql).
--
-- Idempotente: la tabella si (ri)crea solo se manca; il ricarico di un file
-- sostituisce le righe del/i periodo/i presenti nel file (lo fa l'importatore).
-- =============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'src') EXEC('CREATE SCHEMA src');
GO

IF OBJECT_ID(N'src.fattura_vettore_riga', N'U') IS NULL
BEGIN
    CREATE TABLE src.fattura_vettore_riga (
        id                BIGINT IDENTITY(1,1) NOT NULL,
        anno              INT            NOT NULL,   -- anno della SPEDIZIONE (dal file)
        mese              INT            NOT NULL,   -- mese della SPEDIZIONE (dal file)
        vettore           NVARCHAR(40)   NULL,       -- Trasportatore (BRT, GLS, SUSA, ARCO...)
        destino           NVARCHAR(40)   NULL,       -- NAZIONALE / ESTERO
        data_spedizione   DATE           NULL,
        tipo_spedizione   NVARCHAR(20)   NULL,       -- SPEDIZIONE (uscita) | RIENTRO (reso/ritiro)
        categoria_cliente NVARCHAR(20)   NULL,       -- B2B | B2C
        rif_ordine        NVARCHAR(40)   NULL,       -- "N. rif. Cliente" = MA_SaleOrd.InternalOrdNo
        destinatario      NVARCHAR(160)  NULL,
        prov_destinatario NVARCHAR(20)   NULL,
        regione_dest      NVARCHAR(60)   NULL,
        nazione           NVARCHAR(60)   NULL,
        n_colli           INT            NULL,
        peso              DECIMAL(18,3)  NULL,
        volume            DECIMAL(18,3)  NULL,
        nolo              DECIMAL(18,2)  NULL,        -- Nolo
        spese_accessorie  DECIMAL(18,2)  NULL,        -- Spese Accessorie
        totale            DECIMAL(18,2)  NULL,        -- Totale (= Nolo + Spese Accessorie)
        data_caricamento  DATETIME2      NOT NULL CONSTRAINT DF_fvr_dt DEFAULT SYSDATETIME(),
        CONSTRAINT PK_src_fattura_vettore_riga PRIMARY KEY (id)
    );
    CREATE INDEX IX_fvr_rif     ON src.fattura_vettore_riga (rif_ordine);
    CREATE INDEX IX_fvr_periodo ON src.fattura_vettore_riga (anno, mese);
END
GO

-- ---- CFG: tabella di STIMA del trasporto per fascia di peso (livello 1) -----
-- E' la stima "a preventivo" del costo di spedizione, disponibile gia' al momento
-- della fattura (quando la fattura del vettore non c'e' ancora). Si imposta DA UI.
--   costo_eur = costo di UNA spedizione per (canale, area, fascia di peso),
--   poi spalmato sulle righe del documento in proporzione al valore.
-- canale/area accettano il jolly '*' (= qualsiasi): cosi' si parte grezzi (solo per
-- area+peso) e si raffina dove serve. La regola usata e' la PIU' SPECIFICA che combacia.
-- valido_dal: data di inizio validita'. Correggendo i valori nel tempo NON si toccano i
-- periodi gia' chiusi (per un documento si usa la riga valida a quella data).
IF OBJECT_ID(N'cfg.trasporto_stima_peso', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.trasporto_stima_peso (
        id           INT IDENTITY(1,1) NOT NULL,
        canale       NVARCHAR(40)  NOT NULL CONSTRAINT DF_tsp_canale DEFAULT N'*',  -- es. 'Amazon','Leroy Merlin','BTOB tradizionale' o '*'
        area         NVARCHAR(20)  NOT NULL CONSTRAINT DF_tsp_area   DEFAULT N'*',  -- 'ITALIA' | nome Paese (es. 'FRANCIA') | 'ESTERO' generico | '*' (qualsiasi)
        peso_da_kg   DECIMAL(10,3) NOT NULL CONSTRAINT DF_tsp_pda DEFAULT 0,        -- estremo inferiore incluso
        peso_a_kg    DECIMAL(10,3) NOT NULL,                                        -- estremo superiore ESCLUSO
        costo_eur    DECIMAL(18,2) NOT NULL,
        valido_dal   DATE          NOT NULL CONSTRAINT DF_tsp_dal DEFAULT '20260101',
        note         NVARCHAR(200) NULL,
        CONSTRAINT PK_cfg_trasporto_stima_peso PRIMARY KEY (id)
    );
END
GO
