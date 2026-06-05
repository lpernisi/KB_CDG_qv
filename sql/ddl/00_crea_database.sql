-- =============================================================================
-- 00_crea_database.sql
-- -----------------------------------------------------------------------------
-- Crea il database CDG_QV se non esiste. Va eseguito sul database "master".
-- Idempotente: se CDG_QV esiste gia', non fa nulla.
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'CDG_QV')
BEGIN
    CREATE DATABASE [CDG_QV];
END
GO
