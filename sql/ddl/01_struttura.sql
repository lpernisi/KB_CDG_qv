-- =============================================================================
-- 01_struttura.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- Schemi e tabelle del datawarehouse. Tutto idempotente.
--
-- Modello a COMPONENTI ISOLATI:
--   - ogni componente di costo/ricavo ha le SUE righe in core.componente_riga
--     (formato "lungo": una riga per (riga documento, componente));
--   - cfg.componenti e' il REGISTRO: dice livello di margine e segno di ognuno;
--   - core.fatto_riga e' l'assemblaggio finale (ricavo + MdC I/II/III).
-- Correggere un componente tocca SOLO le sue righe: gli altri restano intatti.
-- =============================================================================

-- ---- Schemi ----------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'src')  EXEC('CREATE SCHEMA src');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'core') EXEC('CREATE SCHEMA core');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'cfg')  EXEC('CREATE SCHEMA cfg');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'pres') EXEC('CREATE SCHEMA pres');
GO

-- ---- LANDING (src): righe di vendita estratte da Mago ----------------------
IF OBJECT_ID(N'src.righe_vendita', N'U') IS NULL
BEGIN
    CREATE TABLE src.righe_vendita (
        anno              INT            NOT NULL,
        mese              INT            NOT NULL,
        sale_doc_id       BIGINT         NOT NULL,
        line              INT            NOT NULL,
        codice_articolo   NVARCHAR(50)   NULL,
        tipo_articolo     NVARCHAR(20)   NULL,   -- MERCE | SERVIZIO | ALTRO (da MA_Items.IsGood)
        quantita          DECIMAL(18,4)  NULL,
        ricavo_netto      DECIMAL(18,2)  NULL,   -- imponibile riga + quota trasporto recuperato (spalmato)
        data_caricamento  DATETIME2      NOT NULL CONSTRAINT DF_righe_vendita_dt DEFAULT SYSDATETIME(),
        CONSTRAINT PK_src_righe_vendita PRIMARY KEY (anno, mese, sale_doc_id, line)
    );
END
GO

-- NB: il costo (WAP) NON ha piu' una tabella di landing qui. La preparazione del
-- costo (risalita mese, kit, bonifica) e' del motore core.usp_prepara_costi, che
-- certifica kodice.costi_articolo_mese; il componente COSTO_VENDUTO legge da li'.

-- ---- CFG: REGISTRO dei componenti ------------------------------------------
-- modo_attacco : 'diretto' (attribuibile alla riga) | 'driver' (allocato) | 'struttura'
-- livello      : 1,2,3 = a quale margine di contribuzione entra. NULL = struttura (fuori riga)
-- segno        : -1 per i costi (sottraggono), +1 per ricavi/abbuoni positivi
-- attivo       : 1 = entra nel calcolo. 0 finche' non hai adattato il suo estrattore.
IF OBJECT_ID(N'cfg.componenti', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.componenti (
        codice_componente NVARCHAR(40)  NOT NULL,
        descrizione       NVARCHAR(200) NULL,
        modo_attacco      NVARCHAR(20)  NULL,
        livello           INT           NULL,
        segno             INT           NOT NULL CONSTRAINT DF_componenti_segno DEFAULT (-1),
        attivo            BIT           NOT NULL CONSTRAINT DF_componenti_attivo DEFAULT (0),
        note              NVARCHAR(400) NULL,
        CONSTRAINT PK_cfg_componenti PRIMARY KEY (codice_componente)
    );
END
GO

-- gruppo: la VOCE DI PRIMO LIVELLO del Conto Economico a cui appartiene il componente
-- (es. 'Costi commerciali', 'Costo dei materiali', 'Costi di trasporto'). Serve a
-- raggruppare piu' componenti drill-abili sotto un'unica voce leggibile dal CEO.
IF COL_LENGTH(N'cfg.componenti', N'gruppo') IS NULL
    ALTER TABLE cfg.componenti ADD gruppo NVARCHAR(60) NULL;
GO

-- ---- CORE: dettaglio per riga e per componente (formato "lungo") -----------
IF OBJECT_ID(N'core.componente_riga', N'U') IS NULL
BEGIN
    CREATE TABLE core.componente_riga (
        anno               INT            NOT NULL,
        mese               INT            NOT NULL,
        sale_doc_id        BIGINT         NOT NULL,
        line               INT            NOT NULL,
        codice_componente  NVARCHAR(40)   NOT NULL,
        importo            DECIMAL(18,2)  NULL,
        origine            NVARCHAR(200)  NULL,
        data_calcolo       DATETIME2      NOT NULL CONSTRAINT DF_componente_riga_dt DEFAULT SYSDATETIME(),
        CONSTRAINT PK_core_componente_riga PRIMARY KEY (anno, mese, sale_doc_id, line, codice_componente)
    );
END
GO

-- ---- CORE: assemblaggio finale, una riga per riga documento ----------------
IF OBJECT_ID(N'core.fatto_riga', N'U') IS NULL
BEGIN
    CREATE TABLE core.fatto_riga (
        anno               INT            NOT NULL,
        mese               INT            NOT NULL,
        sale_doc_id        BIGINT         NOT NULL,
        line               INT            NOT NULL,
        codice_articolo    NVARCHAR(50)   NULL,
        tipo_articolo      NVARCHAR(20)   NULL,
        quantita           DECIMAL(18,4)  NULL,
        ricavo_netto       DECIMAL(18,2)  NULL,
        mdc1               DECIMAL(18,2)  NULL,
        mdc2               DECIMAL(18,2)  NULL,
        mdc3               DECIMAL(18,2)  NULL,
        data_calcolo       DATETIME2      NOT NULL CONSTRAINT DF_fatto_riga_dt DEFAULT SYSDATETIME(),
        CONSTRAINT PK_core_fatto_riga PRIMARY KEY (anno, mese, sale_doc_id, line)
    );
END
GO

-- ---- CFG: stato di avanzamento ---------------------------------------------
IF OBJECT_ID(N'cfg.controllo_mesi', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.controllo_mesi (
        anno            INT          NOT NULL,
        mese            INT          NOT NULL,
        righe_caricate  INT          NULL,
        ultimo_run      DATETIME2    NULL,
        CONSTRAINT PK_cfg_controllo_mesi PRIMARY KEY (anno, mese)
    );
END
GO
