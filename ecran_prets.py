"""Écran « Prêts aux associations » : annuaire, réservations, remise, retour, bon de prêt PDF, suivi."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import bon_pret
import metier
import prets
import stock
from commun import (backend, charger, eur, est_admin, flash, journaliser, mois_debut, parametre, peut_saisir,
                    rafraichir)
from referentiel import ETATS_MATERIEL, STATUTS_PRET, categories

STATUT_ICONE = {"reserve": "🟡 Réservé", "en_cours": "🔵 En cours", "rendu": "🟢 Rendu", "annule": "⚪ Annulé"}


# ------------------------------------------------------------------ chargement
def _donnees():
    return (prets.typer_prets(charger("prets")), prets.typer_lignes(charger("prets_lignes")),
            charger("associations"), charger("materiel"),
            stock.typer_sorties(charger("sorties_materiel")))


def _associations_actives() -> pd.DataFrame:
    a = charger("associations")
    return a[a["actif"].astype(str).str.lower() != "non"].sort_values("nom")


def _libelle_pret(p, noms_asso) -> str:
    asso = noms_asso.get(p["association_id"], "?")
    periode = f"{p['date_debut']:%d/%m} → {p['date_fin_prevue']:%d/%m/%Y}" if pd.notna(p["date_debut"]) else ""
    retard = ""
    if p["statut"] == "en_cours" and pd.notna(p["date_fin_prevue"]) and p["date_fin_prevue"] < pd.Timestamp(date.today()):
        retard = f" · ⚠ retard {(pd.Timestamp(date.today()) - p['date_fin_prevue']).days} j"
    return f"{p['numero']} · {asso} · {periode} · {STATUT_ICONE.get(p['statut'], p['statut'])}{retard}"


def _maj_pret(pret_id: str, valeurs: dict) -> None:
    brut = charger("prets").copy()
    for col, val in valeurs.items():
        brut.loc[brut["id"] == pret_id, col] = "" if val is None else str(val)
    backend().remplacer("prets", brut)


def _bon_pdf(pret_id: str) -> tuple[bytes, str]:
    p_all, l_all, assos, materiel, _ = _donnees()
    p = p_all[p_all["id"] == pret_id].iloc[0]
    a = assos[assos["id"] == p["association_id"]]
    pdf = bon_pret.generer_bon(parametre("nom_association", "Comité des Fêtes"),
                               parametre("contact_pret", "feteshaussy@gmail.com"), p,
                               a.iloc[0] if not a.empty else None, l_all[l_all["pret_id"] == pret_id], materiel)
    return pdf, f"bon_de_pret_{p['numero']}.pdf"


# ====================================================================== page
def page_prets():
    st.header("Prêts de matériel aux associations")
    p, l, assos, materiel, _ = _donnees()
    noms_asso = assos.set_index("id")["nom"] if not assos.empty else pd.Series(dtype=str)
    retards = prets.en_retard(p)
    if not retards.empty:
        st.error("**Matériel non rendu à temps** : " + " · ".join(_libelle_pret(r, noms_asso) for _, r in retards.iterrows()))
    preparer = prets.a_preparer(p)
    if not preparer.empty:
        st.warning("**À préparer dans les 7 jours** : " + " · ".join(_libelle_pret(r, noms_asso) for _, r in preparer.iterrows()))
    if materiel.empty:
        st.info("Commencez par inventorier votre matériel dans la page **Matériel** (onglet Inventaire).")
    onglets = st.tabs(["Prêts en cours", "Nouveau prêt ou réservation", "Suivi et historique", "Associations"])
    with onglets[0]:
        _onglet_en_cours()
    with onglets[1]:
        _onglet_nouveau()
    with onglets[2]:
        _onglet_suivi()
    with onglets[3]:
        _onglet_associations()


# ------------------------------------------------------------------ prêts en cours
def _onglet_en_cours():
    p, l, assos, materiel, sorties = _donnees()
    actifs = p[p["statut"].isin(prets.STATUTS_ACTIFS)].sort_values("date_debut")
    if actifs.empty:
        st.caption("Aucun prêt ni réservation en cours.")
        return
    noms_asso = assos.set_index("id")["nom"] if not assos.empty else pd.Series(dtype=str)
    noms_mat = materiel.set_index("id")["nom"] if not materiel.empty else pd.Series(dtype=str)
    choix = st.selectbox("Prêt", actifs["id"].tolist(), key="pret_choisi",
                         format_func=lambda i: _libelle_pret(actifs[actifs["id"] == i].iloc[0], noms_asso))
    pr = actifs[actifs["id"] == choix].iloc[0]
    lignes = l[l["pret_id"] == choix]
    a = assos[assos["id"] == pr["association_id"]]
    if not a.empty:
        aa = a.iloc[0]
        st.markdown(f"**{aa['nom']}** — {aa['contact']} · {aa['telephone']} · {aa['email']}")
    st.dataframe(pd.DataFrame({"Matériel": lignes["materiel_id"].map(noms_mat), "Quantité": lignes["quantite"],
                               "État au départ": lignes["etat_depart"]}), hide_index=True, width="stretch")
    infos = [f"Du {pr['date_debut']:%d/%m/%Y} au {pr['date_fin_prevue']:%d/%m/%Y}"]
    if pr["caution"] > 0:
        infos.append(f"caution {eur(pr['caution'])}")
    infos.append(f"location {eur(pr['montant_location'])}" if pr["montant_location"] > 0 else "prêt gracieux")
    st.caption(" · ".join(infos) + (f" · {pr['commentaire']}" if pr["commentaire"] else ""))

    if st.button("Préparer le bon de prêt (PDF)", key=f"bon_{choix}"):
        pdf, nom = _bon_pdf(choix)
        st.download_button("Télécharger le bon de prêt", pdf, file_name=nom, mime="application/pdf", type="primary")
    if not peut_saisir():
        return

    if pr["statut"] == "reserve":
        st.subheader("Remise du matériel")
        with st.form(f"remise_{choix}"):
            jour = st.date_input("Date de remise", value=date.today(), format="DD/MM/YYYY")
            ok = st.form_submit_button("Le matériel est parti", type="primary")
        if ok:
            d = prets.disponibilite_periode(materiel, p, l, sorties, jour, pr["date_fin_prevue"].date(),
                                            exclure_pret=choix).set_index("id")
            manque = [f"{noms_mat.get(r.materiel_id, '?')} (dispo {d['disponible'].get(r.materiel_id, 0):g})"
                      for r in lignes.itertuples() if r.quantite > d["disponible"].get(r.materiel_id, 0)]
            if manque:
                st.error("Quantité insuffisante pour : " + ", ".join(manque))
            else:
                _maj_pret(choix, {"statut": "en_cours", "date_depart": jour.isoformat()})
                journaliser("prêt", f"{pr['numero']} remis le {jour:%d/%m/%Y}")
                rafraichir()
                flash(f"Prêt {pr['numero']} : matériel remis.")
                st.rerun()
        with st.expander("Annuler la réservation"):
            if st.checkbox("Je confirme l'annulation", key=f"conf_annul_{choix}") and \
                    st.button("Annuler la réservation", key=f"annul_{choix}"):
                _maj_pret(choix, {"statut": "annule"})
                journaliser("prêt", f"{pr['numero']} annulé")
                rafraichir()
                flash(f"Réservation {pr['numero']} annulée.")
                st.rerun()

    if pr["statut"] == "en_cours":
        st.subheader("Retour du matériel")
        base = pd.DataFrame({"id": lignes["id"], "Matériel": lignes["materiel_id"].map(noms_mat),
                             "Prêté": lignes["quantite"], "Rendu": lignes["quantite"], "etat": "Bon"})
        retour = st.data_editor(base, hide_index=True, width="stretch", key=f"retour_{choix}",
                                disabled=["id", "Matériel", "Prêté"], column_config={
            "id": None, "Rendu": st.column_config.NumberColumn("Rendu", min_value=0, step=1),
            "etat": st.column_config.SelectboxColumn("État au retour", options=ETATS_MATERIEL, required=True)})
        with st.form(f"retour_form_{choix}"):
            c1, c2 = st.columns(2)
            jour = c1.date_input("Date de retour", value=date.today(), format="DD/MM/YYYY")
            caution_rendue = c2.checkbox("Caution rendue", value=True) if pr["caution"] > 0 else False
            retirer = st.checkbox("Retirer de l'inventaire les pièces non rendues")
            ok = st.form_submit_button("Enregistrer le retour", type="primary")
        if ok:
            if (retour["Rendu"] > retour["Prêté"]).any():
                st.error("La quantité rendue ne peut pas dépasser la quantité prêtée.")
                return
            brut = charger("prets_lignes").copy()
            for r in retour.itertuples():
                brut.loc[brut["id"] == r.id, "quantite_rendue"] = str(int(r.Rendu))
                brut.loc[brut["id"] == r.id, "etat_retour"] = r.etat
            backend().remplacer("prets_lignes", brut)
            mat = charger("materiel").copy()
            manquants_total = 0
            for r, lig in zip(retour.itertuples(), lignes.itertuples()):
                manquants = int(r.Prêté - r.Rendu)
                manquants_total += manquants
                if manquants and retirer:
                    q = metier.nombres(mat.loc[mat["id"] == lig.materiel_id, "quantite"]).fillna(0)
                    mat.loc[mat["id"] == lig.materiel_id, "quantite"] = (q - manquants).clip(lower=0).astype(int).astype(str)
                if r.etat in ("À réparer", "Hors service"):
                    mat.loc[mat["id"] == lig.materiel_id, "etat"] = r.etat
            backend().remplacer("materiel", mat)
            _maj_pret(choix, {"statut": "rendu", "date_retour": jour.isoformat(),
                              "caution_rendue": "oui" if caution_rendue else ("non" if pr["caution"] > 0 else "")})
            journaliser("prêt", f"{pr['numero']} rendu le {jour:%d/%m/%Y}, {manquants_total} pièce(s) manquante(s)")
            rafraichir()
            flash(f"Retour du prêt {pr['numero']} enregistré."
                  + (f" Attention : {manquants_total} pièce(s) non rendue(s)." if manquants_total else ""))
            st.rerun()

    with st.expander("Prolonger ou modifier la date de retour"):
        with st.form(f"prolonger_{choix}"):
            nouvelle = st.date_input("Nouvelle date de retour prévue", value=pr["date_fin_prevue"].date(),
                                     format="DD/MM/YYYY")
            ok = st.form_submit_button("Enregistrer")
        if ok:
            d = prets.disponibilite_periode(materiel, p, l, sorties, pr["date_debut"].date(), nouvelle,
                                            exclure_pret=choix).set_index("id")
            manque = [noms_mat.get(r.materiel_id, "?") for r in lignes.itertuples()
                      if r.quantite > d["disponible"].get(r.materiel_id, 0)]
            if nouvelle < pr["date_debut"].date():
                st.error("La date de retour ne peut pas précéder la date de début.")
            elif manque:
                st.error("Prolongation impossible, déjà réservé ailleurs : " + ", ".join(manque))
            else:
                _maj_pret(choix, {"date_fin_prevue": nouvelle.isoformat()})
                journaliser("prêt", f"{pr['numero']} retour reporté au {nouvelle:%d/%m/%Y}")
                rafraichir()
                flash(f"Date de retour du prêt {pr['numero']} modifiée.")
                st.rerun()


# ------------------------------------------------------------------ nouveau prêt
def _onglet_nouveau():
    if not peut_saisir():
        st.caption("Lecture seule.")
        return
    p, l, _, materiel, sorties = _donnees()
    if materiel.empty:
        return
    compteur = st.session_state.setdefault("nouveau_pret_n", 0)
    assos = _associations_actives()
    options = ["__nouvelle__"] + assos["id"].tolist()
    noms = assos.set_index("id")["nom"]
    choix = st.selectbox("Association", options, index=1 if len(options) > 1 else 0, key=f"np_asso_{compteur}",
                         format_func=lambda i: "➕ Nouvelle association…" if i == "__nouvelle__" else noms.get(i, i))
    nouvelle = {}
    if choix == "__nouvelle__":
        c1, c2 = st.columns(2)
        nouvelle = {"nom": c1.text_input("Nom de l'association", key=f"np_nom_{compteur}"),
                    "contact": c2.text_input("Responsable", key=f"np_contact_{compteur}"),
                    "telephone": c1.text_input("Téléphone", key=f"np_tel_{compteur}"),
                    "email": c2.text_input("E-mail", key=f"np_mail_{compteur}")}

    c1, c2 = st.columns(2)
    debut = c1.date_input("Début du prêt", value=date.today(), format="DD/MM/YYYY", key=f"np_debut_{compteur}")
    fin = c2.date_input("Retour prévu le", value=date.today() + timedelta(days=3), format="DD/MM/YYYY",
                        key=f"np_fin_{compteur}")
    if fin < debut:
        st.error("La date de retour doit être après la date de début.")
        return
    dispo = prets.disponibilite_periode(materiel, p, l, sorties, debut, fin)
    fetes = prets.fetes_sur_periode(charger("manifestations"), debut, fin)
    if fetes:
        st.info("Sur cette période, le comité organise habituellement : " + ", ".join(fetes)
                + ". Vérifiez que le matériel ne vous manquera pas.")

    noms_mat = dispo.set_index("id")["nom"]
    doublons = dispo["nom"].duplicated(keep=False)
    libelles = {r.id: (f"{r.nom} ({r.id[:4]})" if doublons.iloc[i] else r.nom) for i, r in enumerate(dispo.itertuples())}
    with st.expander(f"Disponibilité du {debut:%d/%m} au {fin:%d/%m/%Y}", expanded=True):
        st.dataframe(pd.DataFrame({"Matériel": dispo["id"].map(libelles), "Disponible": dispo["disponible"],
                                   "Déjà engagé": dispo["engage"], "Pour": dispo["motifs"]}),
                     hide_index=True, width="stretch")
    st.markdown("**Matériel demandé**")
    lignes = st.data_editor(pd.DataFrame({"materiel": pd.Series(dtype=str), "quantite": pd.Series(dtype=float)}),
                            num_rows="dynamic", width="stretch", key=f"np_lignes_{compteur}", column_config={
        "materiel": st.column_config.SelectboxColumn("Matériel", options=list(libelles.values()), required=True),
        "quantite": st.column_config.NumberColumn("Quantité", min_value=1, step=1, required=True)})
    inverse = {v: k for k, v in libelles.items()}
    lignes = lignes.dropna(subset=["materiel", "quantite"])
    lignes = lignes.assign(materiel_id=lignes["materiel"].map(inverse)).dropna(subset=["materiel_id"])
    demande = lignes.groupby("materiel_id")["quantite"].sum()
    d = dispo.set_index("id")
    problemes = [f"{noms_mat[m]} : demandé {q:g}, disponible {d.loc[m, 'disponible']:g}"
                 + (f" (déjà pris : {d.loc[m, 'motifs']})" if d.loc[m, "motifs"] else "")
                 for m, q in demande.items() if q > d.loc[m, "disponible"]]
    for pb in problemes:
        st.error(pb)

    c1, c2, c3 = st.columns(3)
    caution = c1.number_input("Caution (€)", min_value=0.0, step=10.0, key=f"np_caution_{compteur}")
    location = c2.number_input("Prix de location (€, 0 = gracieux)", min_value=0.0, step=5.0, key=f"np_loc_{compteur}")
    recette = c3.checkbox("Enregistrer la location en recette", value=True, key=f"np_rec_{compteur}") \
        if location > 0 else False
    commentaire = st.text_input("Observations", key=f"np_com_{compteur}")
    if debut <= date.today():
        part_maintenant = st.checkbox("Le matériel part aujourd'hui (sinon : réservation)", value=True,
                                      key=f"np_part_{compteur}")
    else:
        part_maintenant = False
        st.caption(f"Réservation : le matériel sera remis le {debut:%d/%m/%Y} (onglet « Prêts en cours »).")

    pret_ok = not lignes.empty and not problemes and (choix != "__nouvelle__" or nouvelle.get("nom", "").strip())
    if not st.button("Enregistrer le prêt" if part_maintenant else "Enregistrer la réservation", type="primary",
                     disabled=not pret_ok):
        if lignes.empty:
            st.caption("Ajoutez au moins une ligne de matériel.")
        return

    if choix == "__nouvelle__":
        if nouvelle["nom"].strip().lower() in [n.lower() for n in charger("associations")["nom"]]:
            st.error("Cette association existe déjà dans l'annuaire : choisissez-la dans la liste.")
            return
        choix = metier.nouvel_id()
        backend().ajouter("associations", [{"id": choix, **{k: v.strip() for k, v in nouvelle.items()},
                                            "adresse": "", "commentaire": "", "actif": "oui"}])
    numero = prets.numero_suivant(p, debut)
    pret_id = metier.nouvel_id()
    nom_asso = nouvelle.get("nom") or noms.get(choix, "")
    op_id = ""
    if location > 0 and recette:
        cat = next(c for c in categories("Recette") if c.startswith("7083"))
        op = metier.nouvelle_operation(debut, "Fonctionnement général", "Recette", cat,
                                       f"Location matériel — prêt {numero}", location, "Autre", nom_asso, numero,
                                       st.session_state["email"], mois_debut())
        backend().ajouter("operations", [op])
        op_id = op["id"]
    backend().ajouter("prets", [{
        "id": pret_id, "numero": numero, "association_id": choix,
        "statut": "en_cours" if part_maintenant else "reserve", "date_debut": debut.isoformat(),
        "date_fin_prevue": fin.isoformat(), "date_depart": debut.isoformat() if part_maintenant else "",
        "date_retour": "", "caution": caution, "caution_rendue": "", "montant_location": location,
        "operation_id": op_id, "commentaire": commentaire.strip(), "cree_par": st.session_state["email"],
        "cree_le": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}])
    etats = d["etat"]
    backend().ajouter("prets_lignes", [{
        "id": metier.nouvel_id(), "pret_id": pret_id, "materiel_id": m, "quantite": int(q),
        "etat_depart": etats.get(m, "Bon") or "Bon", "quantite_rendue": "", "etat_retour": ""}
        for m, q in demande.items()])
    journaliser("prêt", f"{numero} — {nom_asso} — {debut:%d/%m} au {fin:%d/%m/%Y}")
    rafraichir()
    st.session_state["nouveau_pret_n"] = compteur + 1
    st.success(f"{'Prêt' if part_maintenant else 'Réservation'} {numero} {'enregistré' if part_maintenant else 'enregistrée'} pour {nom_asso}.")
    pdf, nom = _bon_pdf(pret_id)
    st.download_button("Télécharger le bon de prêt à faire signer", pdf, file_name=nom, mime="application/pdf",
                       type="primary")


# ------------------------------------------------------------------ suivi
def _onglet_suivi():
    p, l, assos, materiel, _ = _donnees()
    suivi = prets.tableau_suivi(p, l, assos, materiel)
    if suivi.empty:
        st.caption("Aucun prêt enregistré pour l'instant.")
        return
    c1, c2, c3 = st.columns(3)
    asso = c1.selectbox("Association", ["Toutes"] + sorted(suivi["association"].unique()))
    statut = c2.selectbox("Statut", ["Tous"] + list(STATUTS_PRET.values()))
    annees = sorted(suivi["date_debut"].dropna().dt.year.unique(), reverse=True)
    annee = c3.selectbox("Année", ["Toutes"] + [int(a) for a in annees])
    v = suivi
    if asso != "Toutes":
        v = v[v["association"] == asso]
    if statut != "Tous":
        v = v[v["statut"].map(STATUTS_PRET) == statut]
    if annee != "Toutes":
        v = v[v["date_debut"].dt.year == annee]
    v = v.sort_values("date_debut", ascending=False)
    st.dataframe(v.assign(statut=v["statut"].map(STATUT_ICONE)).drop(columns=["id"]), hide_index=True,
                 width="stretch", column_config={
        "numero": "N°", "association": "Association", "articles": "Matériel", "pieces": "Pièces",
        "date_debut": st.column_config.DateColumn("Début", format="DD/MM/YYYY"),
        "date_fin_prevue": st.column_config.DateColumn("Retour prévu", format="DD/MM/YYYY"),
        "date_retour": st.column_config.DateColumn("Rendu le", format="DD/MM/YYYY"),
        "duree": st.column_config.NumberColumn("Durée (jours)"), "statut": "Statut",
        "retard": st.column_config.NumberColumn("Retard (jours)"), "manquants": "Non rendus"})

    stats = prets.statistiques_associations(v)
    if not stats.empty:
        st.subheader("Par association")
        st.altair_chart(alt.Chart(stats).mark_bar(color="#1F4E79").encode(
            y=alt.Y("association:N", title=None, sort="-x", axis=alt.Axis(labelLimit=220)),
            x=alt.X("prets:Q", title="Nombre de prêts", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("association", title="Association"), alt.Tooltip("prets", title="Prêts"),
                     alt.Tooltip("jours", title="Jours cumulés"), alt.Tooltip("retards", title="Retards")],
        ).properties(height=max(160, 36 * len(stats))), width="stretch")
        st.dataframe(stats, hide_index=True, width="stretch", column_config={
            "association": "Association", "prets": "Prêts", "jours": "Jours cumulés", "pieces": "Pièces prêtées",
            "retards": "Rendus en retard", "manquants": "Pièces non rendues",
            "dernier": st.column_config.DateColumn("Dernier prêt", format="DD/MM/YYYY")})
    top = prets.materiel_le_plus_prete(p[p["id"].isin(v["id"])], l, materiel)
    if not top.empty:
        st.subheader("Matériel le plus prêté")
        st.dataframe(top, hide_index=True, width="stretch",
                     column_config={"materiel": "Matériel", "fois": "Nombre de prêts", "pieces": "Pièces prêtées"})


# ------------------------------------------------------------------ annuaire
def _onglet_associations():
    a = charger("associations").copy()
    a["actif"] = a["actif"].astype(str).str.lower() != "non"
    edite = st.data_editor(a, num_rows="dynamic", hide_index=True, width="stretch", key="annuaire_editeur",
                           disabled=not peut_saisir(), column_config={
        "id": None, "nom": st.column_config.TextColumn("Association", required=True), "contact": "Responsable",
        "telephone": "Téléphone", "email": "E-mail", "adresse": "Adresse", "commentaire": "Commentaire",
        "actif": st.column_config.CheckboxColumn("Active", default=True)})
    if not peut_saisir() or not st.button("Enregistrer l'annuaire", type="primary"):
        return
    edite = edite.dropna(subset=["nom"])
    edite = edite[edite["nom"].astype(str).str.strip() != ""]
    if edite["nom"].str.strip().str.lower().duplicated().any():
        st.error("Deux associations portent le même nom.")
        return
    manquants = edite["id"].isna() | (edite["id"].astype(str).str.strip() == "")
    edite.loc[manquants, "id"] = [metier.nouvel_id() for _ in range(int(manquants.sum()))]
    supprimees = set(charger("associations")["id"]) - set(edite["id"])
    if supprimees & set(charger("prets")["association_id"]) and not est_admin():
        st.error("Une association qui a déjà emprunté ne peut pas être supprimée : décochez « Active » à la place.")
        return
    edite["actif"] = edite["actif"].map(lambda b: "non" if b is False else "oui")
    backend().remplacer("associations", edite)
    journaliser("prêt", f"annuaire : {len(edite)} association(s)")
    rafraichir()
    st.success("Annuaire enregistré.")
