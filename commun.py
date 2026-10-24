"""Socle partagé par tous les écrans : accès aux données, cache, droits, formatage."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

import metier
from referentiel import MANIFESTATION_GENERALE, MANIFESTATIONS_DEFAUT, ROLES_ADMIN, ROLES_SAISIE
from stockage import creer_backend, lire_config



# ====================================================================== socle
@st.cache_resource
def backend():
    return creer_backend(lire_config())


@st.cache_data(ttl=120, show_spinner=False)
def charger(table: str) -> pd.DataFrame:
    return backend().lire(table)


def rafraichir():
    charger.clear()


def eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def parametre(cle: str, defaut: str = "") -> str:
    p = charger("parametres")
    v = p.loc[p["cle"] == cle, "valeur"]
    return str(v.iloc[0]) if len(v) and str(v.iloc[0]) != "" else defaut


def ecrire_parametre(cle: str, valeur) -> None:
    p = charger("parametres").copy()
    if (p["cle"] == cle).any():
        p.loc[p["cle"] == cle, "valeur"] = str(valeur)
    else:
        p = pd.concat([p, pd.DataFrame([{"cle": cle, "valeur": str(valeur)}])], ignore_index=True)
    backend().remplacer("parametres", p)
    rafraichir()


def journaliser(action: str, detail: str = "") -> None:
    backend().ajouter("journal", [{"horodatage": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                   "utilisateur": st.session_state.get("email", "?"),
                                   "action": action, "detail": detail}])


def initialiser_si_vide() -> None:
    if charger("manifestations").empty:
        backend().ajouter("manifestations", [{"nom": n, "mois": m, "actif": "oui", "description": d}
                                             for n, m, d in MANIFESTATIONS_DEFAUT])
        rafraichir()


def operations() -> pd.DataFrame:
    return metier.typer_operations(charger("operations"))


def manifestations(actives_seulement: bool = False) -> list[str]:
    m = charger("manifestations")
    if actives_seulement:
        m = m[m["actif"].astype(str).str.lower() != "non"]
    noms = m["nom"].tolist()
    if MANIFESTATION_GENERALE in noms:
        noms.remove(MANIFESTATION_GENERALE)
        noms.insert(0, MANIFESTATION_GENERALE)
    return noms


def mois_debut() -> int:
    try:
        return int(parametre("mois_debut_exercice", "1"))
    except ValueError:
        return 1


def peut_saisir() -> bool:
    return st.session_state.get("role") in ROLES_SAISIE


def est_admin() -> bool:
    return st.session_state.get("role") in ROLES_ADMIN




def flash(message: str) -> None:
    """Message de confirmation conservé jusqu'à l'affichage suivant (survit à st.rerun)."""
    st.session_state["flash"] = message


def afficher_flash() -> None:
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
