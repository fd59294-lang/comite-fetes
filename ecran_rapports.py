"""Écran « Rapports d'AG » : génération du dossier PDF d'assemblée générale."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

import metier
import rapports
from commun import (backend, charger, ecrire_parametre, journaliser, mois_debut, parametre, peut_saisir)
from referentiel import ROLES_LIBELLES


def _nombre_ou_none(texte: str) -> float | None:
    texte = (texte or "").strip()
    if not texte:
        return None
    v = metier.nombres(pd.Series([texte])).iloc[0]
    return None if v != v else float(v)  # NaN -> None


def _signataire_par_defaut(role: str) -> str:
    u = charger("utilisateurs")
    x = u[u["role"] == role]
    if x.empty:
        return ""
    r = x.iloc[0]
    return str(r["nom"] or r["email"])


def page_rapports():
    st.header("Rapports d'assemblée générale")
    st.caption("Génère le dossier PDF à présenter en AG. Il est aussi rangé automatiquement dans le dossier "
               "« Rapports » du Drive.")
    ops = metier.typer_operations(charger("operations"))
    exercices = sorted(ops["exercice"].unique(), reverse=True) if not ops.empty else []
    if not exercices:
        st.info("Aucune opération enregistrée : le rapport serait vide. Saisissez ou importez d'abord les opérations.")
        return
    md = mois_debut()
    ex = st.selectbox("Exercice", exercices, format_func=lambda e: rapports.libelle_exercice(int(e), md))
    ex = int(ex)
    debut, fin = rapports.bornes_exercice(ex, md)

    st.subheader("Contenu")
    colonnes = st.columns(3)
    choisies = [cle for i, (cle, titre) in enumerate(rapports.SECTIONS.items())
                if colonnes[i % 3].checkbox(titre, value=True, key=f"section_{cle}")]

    st.subheader("Trésorerie d'ouverture")
    st.caption(f"Soldes au {debut:%d/%m/%Y}, tels qu'ils figurent sur le relevé bancaire et le comptage de caisse. "
               "Laissez vide pour ne pas présenter la trésorerie.")
    c1, c2 = st.columns(2)
    banque = c1.text_input("Solde banque (€)", value=parametre(f"solde_debut_banque_{ex}"), placeholder="ex. 3 250,40")
    caisse = c2.text_input("Solde caisse (€)", value=parametre(f"solde_debut_caisse_{ex}"), placeholder="ex. 150")

    st.subheader("Rapport d'activité")
    faits = st.text_area("Faits marquants de l'année (un paragraphe par ligne)", value=parametre(f"faits_{ex}"),
                         height=120, placeholder="Brocante record avec 120 exposants.\nAchat d'un nouveau barnum.")

    st.subheader("Signataires")
    c1, c2, c3 = st.columns(3)
    president = c1.text_input(ROLES_LIBELLES["president"],
                              value=parametre("signataire_president", _signataire_par_defaut("president")))
    tresorier = c2.text_input(ROLES_LIBELLES["tresorier"],
                              value=parametre("signataire_tresorier", _signataire_par_defaut("tresorier")))
    secretaire = c3.text_input(ROLES_LIBELLES["secretaire"] + " (facultatif)",
                               value=parametre("signataire_secretaire", ""))

    erreurs = [nom for nom, v in (("banque", banque), ("caisse", caisse)) if v.strip() and _nombre_ou_none(v) is None]
    if erreurs:
        st.error("Montant illisible pour : " + ", ".join(erreurs) + ". Utilisez des chiffres, ex. 3250,40.")
    if not choisies:
        st.warning("Cochez au moins une partie du rapport.")
    if not st.button("Générer le rapport PDF", type="primary", disabled=bool(erreurs) or not choisies):
        return

    signataires = {}
    for role, nom in (("Le président", president), ("Le trésorier", tresorier), ("Le secrétaire", secretaire)):
        if nom.strip():
            signataires[role] = nom.strip()
    donnees = rapports.DonneesRapport(
        nom_association=parametre("nom_association", "Comité des Fêtes"), exercice=ex, mois_debut=md,
        operations=charger("operations"), mouvements=charger("mouvements_stock"), articles=charger("articles"),
        materiel=charger("materiel"), sorties=charger("sorties_materiel"), previsionnel=charger("previsionnel"),
        manifestations=charger("manifestations"), solde_debut_banque=_nombre_ou_none(banque),
        solde_debut_caisse=_nombre_ou_none(caisse), faits_marquants=faits, signataires=signataires,
        sections=choisies)
    with st.spinner("Préparation du rapport…"):
        pdf = rapports.generer_pdf(donnees)
    nom_fichier = f"rapport_AG_{rapports.libelle_exercice(ex, md)}_{datetime.now():%Y-%m-%d_%Hh%M}.pdf"

    if peut_saisir():
        for cle, valeur in ((f"solde_debut_banque_{ex}", banque.strip()), (f"solde_debut_caisse_{ex}", caisse.strip()),
                            (f"faits_{ex}", faits), ("signataire_president", president.strip()),
                            ("signataire_tresorier", tresorier.strip()), ("signataire_secretaire", secretaire.strip())):
            if parametre(cle) != valeur:
                ecrire_parametre(cle, valeur)
        try:
            lieu = backend().enregistrer_fichier("Rapports", nom_fichier, pdf, "application/pdf")
            journaliser("rapport", nom_fichier)
            st.success(f"Rapport enregistré dans le dossier « Rapports » du Drive : {lieu}")
        except Exception as e:
            st.warning(f"Le rapport n'a pas pu être rangé dans le Drive ({e}). Téléchargez-le ci-dessous.")
    st.download_button("Télécharger le rapport PDF", pdf, file_name=nom_fichier, mime="application/pdf",
                       type="primary")
