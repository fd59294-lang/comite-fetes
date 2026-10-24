"""Couche de stockage : Google Sheets + Google Drive en production, fichier Excel local pour les essais.

Toutes les tables sont des onglets d'un même classeur. Les fichiers produits (sauvegardes,
rapports, imports archivés) vont dans les sous-dossiers Drive de l'association.
"""
from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import pandas as pd

from referentiel import TABLES, DOSSIERS_DRIVE

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


# ------------------------------------------------------------------ configuration
def lire_config() -> dict:
    """Lit la configuration dans st.secrets (section [comite]) puis dans les variables d'environnement."""
    conf = {}
    try:
        import streamlit as st
        if "comite" in st.secrets:
            conf.update({k: st.secrets["comite"][k] for k in st.secrets["comite"]})
    except Exception:
        pass
    for cle in ("backend", "spreadsheet_id", "dossiers", "google_credentials", "admins", "dossier_local"):
        env = os.environ.get("COMITE_" + cle.upper())
        if env and cle not in conf:
            conf[cle] = env
    conf.setdefault("backend", "local")
    for cle in ("dossiers", "google_credentials"):
        if isinstance(conf.get(cle), str) and conf[cle].strip().startswith("{"):
            conf[cle] = json.loads(conf[cle])
    if isinstance(conf.get("admins"), str):
        conf["admins"] = [a.strip().lower() for a in conf["admins"].split(",") if a.strip()]
    return conf


def creer_backend(conf: dict) -> "Backend":
    if conf.get("backend") == "google":
        return BackendGoogle(conf)
    return BackendLocal(conf.get("dossier_local", "donnees_locales"))


def credentials_google(info: dict):
    """Jeton du compte Google de l'association (recommandé) ou compte de service."""
    if info.get("type") == "service_account":
        from google.oauth2.service_account import Credentials
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(info, scopes=SCOPES)


# ------------------------------------------------------------------ interface commune
class Backend:
    nom = ""

    def lire(self, table: str) -> pd.DataFrame: ...
    def ajouter(self, table: str, lignes: list[dict]) -> None: ...
    def remplacer(self, table: str, df: pd.DataFrame) -> None: ...
    def supprimer_ids(self, table: str, ids: list[str]) -> int: ...
    def enregistrer_fichier(self, dossier: str, nom: str, contenu: bytes, mime: str) -> str: ...

    @staticmethod
    def _vide(table: str) -> pd.DataFrame:
        return pd.DataFrame(columns=TABLES[table])

    @staticmethod
    def _ligne(table: str, d: dict) -> list:
        valeurs = []
        for col in TABLES[table]:
            v = d.get(col, "")
            if v is None or (isinstance(v, float) and pd.isna(v)):
                v = ""
            if hasattr(v, "item"):  # types numpy -> python
                v = v.item()
            valeurs.append(v)
        return valeurs


# ------------------------------------------------------------------ mode local (essais)
class BackendLocal(Backend):
    nom = "Fichier local (mode essai)"
    _verrou = threading.Lock()

    def __init__(self, dossier: str):
        self.racine = Path(dossier)
        self.racine.mkdir(parents=True, exist_ok=True)
        for d in DOSSIERS_DRIVE:
            (self.racine / d).mkdir(exist_ok=True)
        self.fichier = self.racine / "base_locale.xlsx"
        if not self.fichier.exists():
            with pd.ExcelWriter(self.fichier, engine="openpyxl") as w:
                for t in TABLES:
                    self._vide(t).to_excel(w, sheet_name=t, index=False)

    def _tout(self) -> dict[str, pd.DataFrame]:
        feuilles = pd.read_excel(self.fichier, sheet_name=None, dtype=str, keep_default_na=False)
        return {t: feuilles.get(t, self._vide(t)) for t in TABLES}

    def _ecrire(self, tout: dict[str, pd.DataFrame]) -> None:
        tmp = self.fichier.with_suffix(".tmp.xlsx")
        with pd.ExcelWriter(tmp, engine="openpyxl") as w:
            for t, df in tout.items():
                df.reindex(columns=TABLES[t]).to_excel(w, sheet_name=t, index=False)
        tmp.replace(self.fichier)

    def lire(self, table):
        with self._verrou:
            return self._tout()[table].reindex(columns=TABLES[table]).fillna("").astype(str)

    def ajouter(self, table, lignes):
        if not lignes:
            return
        with self._verrou:
            tout = self._tout()
            neuf = pd.DataFrame([self._ligne(table, l) for l in lignes], columns=TABLES[table])
            tout[table] = pd.concat([tout[table], neuf.astype(str)], ignore_index=True)
            self._ecrire(tout)

    def remplacer(self, table, df):
        with self._verrou:
            tout = self._tout()
            tout[table] = df.reindex(columns=TABLES[table]).fillna("").astype(str)
            self._ecrire(tout)

    def supprimer_ids(self, table, ids):
        with self._verrou:
            tout = self._tout()
            avant = len(tout[table])
            tout[table] = tout[table][~tout[table]["id"].isin(ids)]
            self._ecrire(tout)
            return avant - len(tout[table])

    def enregistrer_fichier(self, dossier, nom, contenu, mime):
        chemin = self.racine / dossier / nom
        chemin.write_bytes(contenu)
        return str(chemin)


# ------------------------------------------------------------------ mode Google
class BackendGoogle(Backend):
    nom = "Google Drive de l'association"

    def __init__(self, conf: dict):
        import gspread
        from googleapiclient.discovery import build

        info = conf["google_credentials"]
        if isinstance(info, str):
            info = json.loads(info) if info.strip().startswith("{") else json.loads(Path(info).read_text())
        self.creds = credentials_google(dict(info))
        self.gc = gspread.authorize(self.creds)
        self.classeur = self.gc.open_by_key(conf["spreadsheet_id"])
        self.dossiers = dict(conf.get("dossiers") or {})
        self.drive = build("drive", "v3", credentials=self.creds, cache_discovery=False)
        self._onglets = {}

    def _onglet(self, table: str):
        if table not in self._onglets:
            import gspread
            try:
                ws = self.classeur.worksheet(table)
            except gspread.WorksheetNotFound:
                ws = self.classeur.add_worksheet(table, rows=1000, cols=len(TABLES[table]))
                ws.update([TABLES[table]], "A1")
            self._onglets[table] = ws
        return self._onglets[table]

    def lire(self, table):
        valeurs = self._onglet(table).get(value_render_option="UNFORMATTED_VALUE")
        if not valeurs:
            return self._vide(table)
        entete, lignes = valeurs[0], valeurs[1:]
        lignes = [l + [""] * (len(entete) - len(l)) for l in lignes]
        df = pd.DataFrame(lignes, columns=entete)
        return df.reindex(columns=TABLES[table]).fillna("").astype(str)

    def ajouter(self, table, lignes):
        if lignes:
            self._onglet(table).append_rows([self._ligne(table, l) for l in lignes],
                                            value_input_option="RAW", insert_data_option="INSERT_ROWS")

    def remplacer(self, table, df):
        ws = self._onglet(table)
        df = df.reindex(columns=TABLES[table]).fillna("")
        ws.clear()
        ws.update([TABLES[table]] + [self._ligne(table, r) for r in df.to_dict("records")],
                  "A1", value_input_option="RAW")

    def supprimer_ids(self, table, ids):
        ws = self._onglet(table)
        col_id = ws.col_values(TABLES[table].index("id") + 1)
        a_supprimer = [i + 1 for i, v in enumerate(col_id) if i > 0 and str(v) in set(ids)]
        for numero in sorted(a_supprimer, reverse=True):
            ws.delete_rows(numero)
        return len(a_supprimer)

    def enregistrer_fichier(self, dossier, nom, contenu, mime):
        from googleapiclient.http import MediaIoBaseUpload
        parent = self.dossiers.get(dossier)
        meta = {"name": nom, **({"parents": [parent]} if parent else {})}
        media = MediaIoBaseUpload(io.BytesIO(contenu), mimetype=mime, resumable=False)
        f = self.drive.files().create(body=meta, media_body=media, fields="id,webViewLink").execute()
        return f.get("webViewLink", f["id"])
