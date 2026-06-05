"""
config_loader.py
================
Legge la configurazione: config/settings.yaml (non segreto) + .env (segreto).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # cartella del progetto


def carica_config() -> dict:
    """Restituisce la configurazione completa come dizionario."""
    load_dotenv(ROOT / ".env")  # carica le variabili segrete nell'ambiente

    with open(ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    ist = config["istanza"]

    # Server/driver/autenticazione: se presenti in .env, hanno la precedenza sui
    # valori (segnaposto) di settings.yaml. Cosi' i dati di connessione possono
    # stare tutti nel file segreto .env, non committato.
    if os.getenv("DB_SERVER"):
        ist["server"] = os.getenv("DB_SERVER")
    if os.getenv("DB_DRIVER"):
        ist["driver"] = os.getenv("DB_DRIVER")
    trusted = os.getenv("DB_TRUSTED")
    if trusted:
        ist["trusted_connection"] = trusted.strip().lower() in ("1", "true", "yes", "si", "sì")

    # Inietta le credenziali (servono solo se non si usa l'autenticazione Windows).
    ist["user"] = os.getenv("DB_USER", "")
    ist["password"] = os.getenv("DB_PASSWORD", "")
    return config
