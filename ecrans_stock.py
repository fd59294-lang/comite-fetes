"""Écrans « Stock boissons et alimentation » et « Matériel »."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

import metier
import prets
import stock
from commun import (flash, backend, charger, eur, est_admin, journaliser, manifestations, mois_debut, operations,
                    peut_saisir, rafraichir)
from referentiel import (CATEGORIES_MATERIEL, DESTINATIONS, ETATS_MATERIEL, FAMILLES_ARTICLES,
                         MODES_PAIEMENT, TYPES_MOUVEMENT, UNITES, categories)


def _num(x, defaut=0.0) -> float:
    v = pd.to_numeric(str(x).replace(",", "."), errors="coerce")
    return defaut if pd.isna(v) else float(v)


def _articles(actifs: bool = True) -> pd.DataFrame:
    a = charger("articles")
    if actifs:
        a = a[a["actif"].astype(str).str.lower() != "non"]
    return a.sort_values(["famille", "nom"])


def _mvts() -> pd.DataFrame:
    return stock.typer_mouvements(charger("mouvements_stock"))


def _completer_ids(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    manquants = df["id"].isna() | (df["id"].astype(str).str.strip() == "")
    df.loc[manquants, "id"] = [metier.nouvel_id() for _ in range(int(manquants.sum()))]
    return df


# ====================================================================== STOCK
def page_stock():
    st.header("Stock boissons et alimentation")
    if _articles().empty:
        st.info("Commencez par créer vos articles dans l'onglet **Articles** (ex. Bière 25 cl, Coca 33 cl, "
                "saucisses…), puis saisissez le stock de départ dans **Réception**.")
    onglets = st.tabs(["État du stock", "Réception", "Manifestation : ventes et inventaire", "Mouvements", "Articles"])
    with onglets[0]:
        _onglet_etat()
    with onglets[1]:
        _onglet_reception()
    with onglets[2]:
        _onglet_manifestation()
    with onglets[3]:
        _onglet_mouvements()
    with onglets[4]:
        _onglet_articles()


def _onglet_etat():
    etat = stock.etat_stock(_articles(), _mvts())
    if etat.empty:
        st.caption("Aucun article.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Valeur du stock", eur(etat["valeur"].sum()))
    c2.metric("Articles en stock", int((etat["quantite"] > 0).sum()))
    c3.metric("Alertes", int((etat["alerte"] != "").sum()))
    alertes = etat[etat["alerte"] != ""]
    if not alertes.empty:
        st.warning("**À surveiller avant la prochaine manifestation** : " +
                   " · ".join(f"{r.article} ({r.alerte})" for r in alertes.itertuples()))
    familles = ["Toutes"] + FAMILLES_ARTICLES
    f = st.radio("Famille", familles, horizontal=True, label_visibility="collapsed")
    vue = etat if f == "Toutes" else etat[etat["famille"] == f]
    st.dataframe(vue.drop(columns=["id"]), hide_index=True, width="stretch", column_config={
        "article": "Article", "famille": "Famille", "unite": "Unité", "quantite": "Quantité",
        "cmup": st.column_config.NumberColumn("Coût moyen", format="%.2f €"),
        "valeur": st.column_config.NumberColumn("Valeur", format="%.2f €"),
        "prochaine_dlc": st.column_config.DateColumn("Prochaine DLC", format="DD/MM/YYYY"),
        "alerte": "Alerte"})


def _onglet_reception():
    if not peut_saisir():
        st.caption("Lecture seule.")
        return
    articles = _articles()
    if articles.empty:
        return
    st.caption("Enregistre l'entrée en stock et, si besoin, la dépense correspondante en trésorerie.")
    c1, c2, c3 = st.columns(3)
    jour = c1.date_input("Date de réception", value=date.today(), format="DD/MM/YYYY", key="rec_date")
    manif = c2.selectbox("Achat fait pour", manifestations(actives_seulement=True), key="rec_manif",
                         help="Marqueur de trésorerie. Le coût sera imputé à la manifestation où les articles seront consommés.")
    origine = c3.radio("Trésorerie", ["Créer la dépense", "Rattacher à une dépense déjà saisie",
                                      "Stock de départ ou don (sans dépense)"], key="rec_origine")
    op_existante, tiers, piece, mode = "", "", "", MODES_PAIEMENT[0]
    if origine == "Créer la dépense":
        c4, c5, c6 = st.columns(3)
        tiers = c4.text_input("Fournisseur", key="rec_tiers")
        piece = c5.text_input("N° de facture / ticket", key="rec_piece")
        mode = c6.selectbox("Mode de paiement", MODES_PAIEMENT, index=3, key="rec_mode")
    elif origine == "Rattacher à une dépense déjà saisie":
        ops = operations()
        deja = set(charger("mouvements_stock")["operation_id"].astype(str))
        candidates = ops[(ops["sens"] == "Dépense") & ~ops["id"].isin(deja)].sort_values(["date", "saisi_le"], ascending=False, kind="stable").head(200)
        if candidates.empty:
            st.info("Aucune dépense disponible à rattacher.")
            return
        op_existante = st.selectbox(
            "Dépense", candidates["id"].tolist(), key="rec_op",
            format_func=lambda i: (lambda r: f"{r['date']:%d/%m/%Y} · {r['manifestation']} · {eur(r['montant'])} · {r['libelle']} · réf. {r['id'][:6]}")(
                candidates[candidates['id'] == i].iloc[0]))

    noms = articles["nom"].tolist()
    lignes = st.data_editor(
        pd.DataFrame({"article": pd.Series(dtype=str), "quantite": pd.Series(dtype=float),
                      "prix_total": pd.Series(dtype=float), "dlc": pd.Series(dtype="datetime64[ns]")}),
        num_rows="dynamic", width="stretch", key="rec_lignes", column_config={
            "article": st.column_config.SelectboxColumn("Article", options=noms, required=True),
            "quantite": st.column_config.NumberColumn("Quantité", min_value=0.0, step=1.0, required=True),
            "prix_total": st.column_config.NumberColumn("Prix total de la ligne (€)", min_value=0.0, format="%.2f"),
            "dlc": st.column_config.DateColumn("DLC / DLUO", format="DD/MM/YYYY")})
    lignes = lignes.dropna(subset=["article", "quantite"])
    lignes = lignes[lignes["quantite"] > 0]
    total = float(lignes["prix_total"].fillna(0).sum())
    st.metric("Total de la réception", eur(total))
    if op_existante:
        montant_op = float(operations().set_index("id").loc[op_existante, "montant"])
        if abs(montant_op - total) > 0.01:
            st.warning(f"Le total des lignes ({eur(total)}) diffère de la dépense rattachée ({eur(montant_op)}).")
    if not st.button("Enregistrer la réception", type="primary", disabled=lignes.empty):
        return
    ids = articles.set_index("nom")["id"]
    op_id = op_existante
    if origine == "Créer la dépense":
        if total <= 0:
            st.error("Indiquez les prix pour créer la dépense.")
            return
        cat = next(c for c in categories("Dépense") if c.startswith("607"))
        op = metier.nouvelle_operation(jour, manif, "Dépense", cat, f"Achat stock ({len(lignes)} article(s))",
                                       total, mode, tiers, piece, st.session_state["email"], mois_debut())
        backend().ajouter("operations", [op])
        op_id = op["id"]
    type_ = "achat"  # stock de départ et dons sont des entrées valorisées sans dépense rattachée
    commentaire = "stock de départ / don" if origine.startswith("Stock") else ""
    mvts = [stock.mouvement(jour, ids[r["article"]], type_, r["quantite"], manif, st.session_state["email"],
                            cout_unitaire=(r["prix_total"] or 0) / r["quantite"], operation_id=op_id,
                            dlc=r["dlc"].date() if pd.notna(r["dlc"]) else None, commentaire=commentaire)
            for r in lignes.to_dict("records")]
    backend().ajouter("mouvements_stock", mvts)
    journaliser("stock", f"réception de {len(mvts)} ligne(s), {eur(total)}, pour {manif}")
    rafraichir()
    st.success(f"Réception enregistrée ({len(mvts)} article(s), {eur(total)}).")


def _onglet_manifestation():
    articles = _articles()
    if articles.empty:
        return
    mvts = _mvts()
    c1, c2 = st.columns(2)
    manif = c1.selectbox("Manifestation", manifestations(actives_seulement=True), key="mf_manif")
    jour = c2.date_input("Date", value=date.today(), format="DD/MM/YYYY", key="mf_date")
    theo = {a: stock.stock_theorique(mvts, a, jour) for a in articles["id"]}

    if peut_saisir():
        st.subheader("1. Ventes, offerts et pertes")
        st.caption("Saisissez ce qui est connu (tickets, comptage de caisse). Laisser à 0 si inconnu : l'inventaire fera le reste.")
        base = pd.DataFrame({"id": articles["id"], "Article": articles["nom"],
                             "En stock": articles["id"].map(theo), "Vendu": 0.0, "Offert": 0.0, "Perte": 0.0})
        saisie = st.data_editor(base, hide_index=True, width="stretch", key=f"mf_ventes_{manif}_{jour}",
                                disabled=["id", "Article", "En stock"], column_config={"id": None})
        if st.button("Enregistrer ventes, offerts et pertes"):
            nouveaux = []
            for r in saisie.to_dict("records"):
                for col, type_ in (("Vendu", "vente"), ("Offert", "offert"), ("Perte", "perte")):
                    if _num(r[col]) > 0:
                        nouveaux.append(stock.mouvement(jour, r["id"], type_, -_num(r[col]), manif,
                                                        st.session_state["email"]))
            if nouveaux:
                backend().ajouter("mouvements_stock", nouveaux)
                journaliser("stock", f"{len(nouveaux)} sortie(s) — {manif} {jour:%d/%m/%Y}")
                rafraichir()
                flash(f"{len(nouveaux)} mouvement(s) enregistré(s).")
                st.rerun()

        st.subheader("2. Inventaire de fin de manifestation")
        st.caption("Comptez ce qui reste réellement. L'écart avec le stock théorique (casse, oublis de saisie, "
                   "consommation des bénévoles) est imputé à cette manifestation.")
        inv = pd.DataFrame({"id": articles["id"], "Article": articles["nom"],
                            "Théorique": articles["id"].map(theo), "Compté": articles["id"].map(theo)})
        compte = st.data_editor(inv, hide_index=True, width="stretch", key=f"mf_inv_{manif}_{jour}",
                                disabled=["id", "Article", "Théorique"], column_config={"id": None})
        compte["Écart"] = compte["Compté"].map(_num) - compte["Théorique"]
        ecarts = compte[compte["Écart"].abs() > 1e-9]
        if not ecarts.empty:
            st.dataframe(ecarts[["Article", "Théorique", "Compté", "Écart"]], hide_index=True)
        if st.button("Valider l'inventaire", disabled=ecarts.empty):
            nouveaux = [stock.mouvement(jour, r["id"], "inventaire", r["Écart"], manif, st.session_state["email"],
                                        commentaire=f"compté {_num(r['Compté']):g}, théorique {r['Théorique']:g}")
                        for r in ecarts.to_dict("records")]
            backend().ajouter("mouvements_stock", nouveaux)
            journaliser("stock", f"inventaire {manif} {jour:%d/%m/%Y} : {len(nouveaux)} écart(s)")
            rafraichir()
            flash("Inventaire enregistré.")
            st.rerun()

    st.subheader("3. Bilan de l'édition")
    c1, c2 = st.columns(2)
    debut = c1.date_input("Du", value=jour - timedelta(days=7), format="DD/MM/YYYY", key="mf_debut")
    fin = c2.date_input("Au", value=jour + timedelta(days=1), format="DD/MM/YYYY", key="mf_fin")
    bilan = stock.bilan_manifestation(articles, _mvts(), manif, debut, fin)
    if bilan.empty:
        st.caption("Aucun mouvement de stock sur cette période pour cette manifestation.")
        return
    st.dataframe(bilan, hide_index=True, width="stretch",
                 column_config={"coût consommé": st.column_config.NumberColumn("Coût consommé", format="%.2f €")})
    prix = articles.set_index("nom")["prix_vente"].map(_num)
    ca_attendu = float((bilan.set_index("Article")["Vendu"] * prix.reindex(bilan["Article"]).fillna(0).values).sum())
    ops = operations()
    caisse = ops[(ops["manifestation"] == manif) & (ops["sens"] == "Recette")
                 & ops["categorie"].str.startswith("707") & (ops["date"] >= pd.Timestamp(debut))
                 & (ops["date"] <= pd.Timestamp(fin))]["montant"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Coût consommé", eur(bilan["coût consommé"].sum()))
    c2.metric("Ventes attendues (prix × vendu)", eur(ca_attendu))
    c3.metric("Recettes buvette encaissées", eur(caisse))
    c4.metric("Écart de caisse", eur(caisse - ca_attendu),
              help="Recettes 707 saisies en trésorerie moins ventes attendues. Un écart important signale "
                   "une erreur de comptage, des ventes non saisies ou un prix non à jour.")


def _onglet_mouvements():
    mvts = stock.valoriser(_mvts())
    if mvts.empty:
        st.caption("Aucun mouvement.")
        return
    noms = charger("articles").set_index("id")["nom"]
    mvts["article"] = mvts["article_id"].map(noms)
    mvts["type"] = mvts["type"].map(lambda t: TYPES_MOUVEMENT.get(t, (0, t))[1])
    c1, c2 = st.columns(2)
    art = c1.selectbox("Article", ["Tous"] + sorted(mvts["article"].dropna().unique()))
    man = c2.selectbox("Manifestation", ["Toutes"] + sorted(mvts["manifestation"].unique()), key="mv_manif")
    v = mvts
    if art != "Tous":
        v = v[v["article"] == art]
    if man != "Toutes":
        v = v[v["manifestation"] == man]
    v = v.sort_values(["date", "saisi_le"], ascending=False)
    st.dataframe(v[["date", "article", "type", "quantite", "manifestation", "cout_unitaire", "cout_consomme",
                    "stock_qte", "cmup", "commentaire"]], hide_index=True, width="stretch", column_config={
        "date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"), "article": "Article", "type": "Mouvement",
        "quantite": "Quantité", "manifestation": "Manifestation",
        "cout_unitaire": st.column_config.NumberColumn("Prix unitaire achat", format="%.2f €"),
        "cout_consomme": st.column_config.NumberColumn("Coût imputé", format="%.2f €"),
        "stock_qte": "Stock après", "cmup": st.column_config.NumberColumn("Coût moyen", format="%.2f €"),
        "commentaire": "Commentaire"})
    if est_admin():
        with st.expander("Annuler un mouvement saisi par erreur"):
            choix = st.selectbox("Mouvement", v["id"].tolist(), format_func=lambda i: (
                lambda r: f"{r['date']:%d/%m/%Y} · {r['article']} · {r['type']} · {r['quantite']:g} · {r['manifestation']} · réf. {r['id'][:6]}")(
                v[v["id"] == i].iloc[0]))
            if st.button("Supprimer ce mouvement"):
                backend().supprimer_ids("mouvements_stock", [choix])
                journaliser("stock", f"mouvement {choix} supprimé")
                rafraichir()
                flash("Mouvement supprimé.")
                st.rerun()


def _onglet_articles():
    a = charger("articles").copy()
    a["actif"] = a["actif"].astype(str).str.lower() != "non"
    for col in ("prix_vente", "seuil_alerte"):
        a[col] = metier.nombres(a[col])
    edite = st.data_editor(a, num_rows="dynamic", hide_index=True, width="stretch",
                           disabled=not est_admin(), key="articles_editeur", column_config={
        "id": None, "nom": st.column_config.TextColumn("Article", required=True),
        "famille": st.column_config.SelectboxColumn("Famille", options=FAMILLES_ARTICLES, required=True),
        "unite": st.column_config.SelectboxColumn("Unité", options=UNITES, required=True),
        "prix_vente": st.column_config.NumberColumn("Prix de vente (€)", format="%.2f"),
        "seuil_alerte": st.column_config.NumberColumn("Seuil d'alerte"),
        "actif": st.column_config.CheckboxColumn("Actif", default=True)})
    if est_admin() and st.button("Enregistrer les articles", type="primary"):
        edite = _completer_ids(edite.dropna(subset=["nom"]))
        if edite["nom"].str.strip().str.lower().duplicated().any():
            st.error("Deux articles portent le même nom.")
            return
        edite["actif"] = edite["actif"].map(lambda b: "non" if b is False else "oui")
        backend().remplacer("articles", edite)
        journaliser("stock", f"catalogue : {len(edite)} article(s)")
        rafraichir()
        st.success("Articles enregistrés.")


# ====================================================================== MATÉRIEL
def page_materiel():
    st.header("Matériel")
    sorties = stock.typer_sorties(charger("sorties_materiel"))
    pretes = prets.quantites_pretees_maintenant(prets.typer_prets(charger("prets")),
                                                prets.typer_lignes(charger("prets_lignes")))
    dispo = stock.disponibilite(charger("materiel"), sorties, pretes)
    en_retard = stock.retards(sorties)
    if not en_retard.empty:
        noms = dispo.set_index("id")["nom"]
        st.error("**Retours en retard** : " + " · ".join(
            f"{noms.get(r.materiel_id, '?')} ×{r.quantite:g} ({r.emprunteur or r.manifestation}, prévu le "
            f"{r.date_retour_prevue:%d/%m/%Y})" for r in en_retard.itertuples()))
    onglets = st.tabs(["Inventaire", "Sortie pour une manifestation", "Retours", "Historique"])
    with onglets[0]:
        _onglet_inventaire_materiel(dispo)
    with onglets[1]:
        _onglet_sortie(dispo)
    with onglets[2]:
        _onglet_retours(dispo, sorties)
    with onglets[3]:
        _onglet_historique_materiel(dispo, sorties)


def _onglet_inventaire_materiel(dispo: pd.DataFrame):
    c1, c2, c3 = st.columns(3)
    c1.metric("Valeur du matériel", eur(dispo["valeur"].sum()))
    c2.metric("Pièces actuellement sorties", int(dispo["en_sortie"].sum()))
    c3.metric("À réparer / hors service", int(dispo["etat"].isin(["À réparer", "Hors service"]).sum()))
    st.caption("Disponible = quantité possédée − quantité actuellement sortie pour une manifestation ou prêtée "
               "à une association.")
    m = charger("materiel").copy()
    for col in ("quantite", "valeur_unitaire"):
        m[col] = metier.nombres(m[col])
    m["date_achat"] = pd.to_datetime(m["date_achat"], errors="coerce")
    m.insert(4, "disponible", m["id"].map(dispo.set_index("id")["disponible"]) if not m.empty else [])
    edite = st.data_editor(m, num_rows="dynamic", hide_index=True, width="stretch",
                           disabled=["disponible"] if est_admin() else True, key="materiel_editeur", column_config={
        "id": None, "nom": st.column_config.TextColumn("Matériel", required=True),
        "categorie": st.column_config.SelectboxColumn("Catégorie", options=CATEGORIES_MATERIEL, required=True),
        "quantite": st.column_config.NumberColumn("Quantité", min_value=0, step=1, required=True),
        "disponible": st.column_config.NumberColumn("Disponible"),
        "etat": st.column_config.SelectboxColumn("État", options=ETATS_MATERIEL, default="Bon"),
        "emplacement": "Emplacement",
        "valeur_unitaire": st.column_config.NumberColumn("Valeur unitaire (€)", format="%.2f"),
        "date_achat": st.column_config.DateColumn("Date d'achat", format="DD/MM/YYYY"),
        "commentaire": "Commentaire"})
    if est_admin() and st.button("Enregistrer l'inventaire", type="primary"):
        edite = _completer_ids(edite.dropna(subset=["nom"])).drop(columns=["disponible"])
        edite["date_achat"] = edite["date_achat"].map(lambda d: d.date().isoformat() if pd.notna(d) else "")
        backend().remplacer("materiel", edite)
        journaliser("matériel", f"inventaire : {len(edite)} ligne(s)")
        rafraichir()
        st.success("Inventaire enregistré.")


def _onglet_sortie(dispo: pd.DataFrame):
    if not peut_saisir() or dispo.empty:
        st.caption("Aucun matériel." if dispo.empty else "Lecture seule.")
        return
    st.caption("Les prêts aux associations se gèrent dans la page « Prêts aux associations » (bon de prêt, "
               "réservations, suivi).")
    destination = DESTINATIONS[0]
    with st.form("sortie_materiel", clear_on_submit=True):
        mat = st.selectbox("Matériel", dispo["id"].tolist(), format_func=lambda i: (
            lambda r: f"{r['nom']} — disponible : {r['disponible']:g}")(dispo[dispo["id"] == i].iloc[0]))
        c1, c2, c3 = st.columns(3)
        qte = c1.number_input("Quantité", min_value=1, step=1)
        sortie = c2.date_input("Date de sortie", value=date.today(), format="DD/MM/YYYY")
        retour = c3.date_input("Retour prévu le", value=date.today() + timedelta(days=2), format="DD/MM/YYYY")
        manif, emprunteur, contact, caution, location, recette = "", "", "", 0.0, 0.0, False
        if destination == "Manifestation":
            manif = st.selectbox("Manifestation", manifestations(actives_seulement=True))
        else:
            c4, c5 = st.columns(2)
            emprunteur = c4.text_input("Emprunteur (association, mairie, particulier)")
            contact = c5.text_input("Contact (téléphone, mail)")
            c6, c7 = st.columns(2)
            caution = c6.number_input("Caution demandée (€)", min_value=0.0, step=10.0)
            location = c7.number_input("Prix de location (€)", min_value=0.0, step=5.0)
            recette = st.checkbox("Enregistrer le prix de location comme recette", value=True)
        commentaire = st.text_input("Commentaire")
        if not st.form_submit_button("Enregistrer la sortie", type="primary"):
            return
    disponible = float(dispo.set_index("id").loc[mat, "disponible"])
    if qte > disponible:
        st.error(f"Seulement {disponible:g} disponible(s).")
        return
    if destination != "Manifestation" and not emprunteur.strip():
        st.error("Indiquez l'emprunteur.")
        return
    backend().ajouter("sorties_materiel", [{
        "id": metier.nouvel_id(), "materiel_id": mat, "quantite": qte, "destination": destination,
        "manifestation": manif, "emprunteur": emprunteur.strip(), "contact": contact.strip(), "caution": caution,
        "caution_rendue": "", "montant_location": location, "date_sortie": sortie.isoformat(),
        "date_retour_prevue": retour.isoformat(), "date_retour": "", "quantite_retour": "", "etat_retour": "",
        "commentaire": commentaire, "saisi_par": st.session_state["email"]}])
    nom = dispo.set_index("id").loc[mat, "nom"]
    if location > 0 and recette:
        cat = next(c for c in categories("Recette") if c.startswith("7083"))
        backend().ajouter("operations", [metier.nouvelle_operation(
            sortie, "Fonctionnement général", "Recette", cat, f"Location {nom} ×{qte}", location, "Autre",
            emprunteur, "", st.session_state["email"], mois_debut())])
    journaliser("matériel", f"sortie {nom} ×{qte} — {manif or emprunteur}")
    rafraichir()
    st.success(f"Sortie enregistrée : {nom} ×{qte}.")


def _onglet_retours(dispo: pd.DataFrame, sorties: pd.DataFrame):
    en_cours = sorties[sorties["en_cours"]]
    if en_cours.empty:
        st.caption("Tout le matériel est rentré.")
        return
    noms = dispo.set_index("id")["nom"]
    choix = st.selectbox("Sortie à clôturer", en_cours["id"].tolist(), format_func=lambda i: (
        lambda r: f"{noms.get(r['materiel_id'], '?')} ×{r['quantite']:g} — {r['manifestation'] or r['emprunteur']} "
                  f"— retour prévu {r['date_retour_prevue']:%d/%m/%Y} · réf. {r['id'][:6]}")(en_cours[en_cours["id"] == i].iloc[0]))
    s = en_cours[en_cours["id"] == choix].iloc[0]
    if not peut_saisir():
        return
    with st.form("retour_materiel"):
        c1, c2, c3 = st.columns(3)
        jour = c1.date_input("Date de retour", value=date.today(), format="DD/MM/YYYY")
        qte = c2.number_input("Quantité rendue", min_value=0, max_value=int(s["quantite"]), value=int(s["quantite"]))
        etat = c3.selectbox("État au retour", ETATS_MATERIEL)
        caution_rendue = st.checkbox("Caution rendue", value=True) if s["caution"] > 0 else False
        retirer = st.checkbox("Retirer les pièces manquantes de l'inventaire")
        commentaire = st.text_input("Commentaire", value=s["commentaire"])
        if not st.form_submit_button("Enregistrer le retour", type="primary"):
            return
    brut = charger("sorties_materiel").copy()
    maj = {"date_retour": jour.isoformat(), "quantite_retour": qte, "etat_retour": etat,
           "caution_rendue": "oui" if caution_rendue else ("non" if s["caution"] > 0 else ""),
           "commentaire": commentaire}
    for col, val in maj.items():
        brut.loc[brut["id"] == choix, col] = str(val)
    backend().remplacer("sorties_materiel", brut)
    manquants = int(s["quantite"]) - qte
    mat = charger("materiel").copy()
    if manquants and retirer:
        q = metier.nombres(mat.loc[mat["id"] == s["materiel_id"], "quantite"]).fillna(0)
        mat.loc[mat["id"] == s["materiel_id"], "quantite"] = (q - manquants).clip(lower=0).astype(int).astype(str)
    if etat in ("À réparer", "Hors service"):
        mat.loc[mat["id"] == s["materiel_id"], "etat"] = etat
    backend().remplacer("materiel", mat)
    journaliser("matériel", f"retour {noms.get(s['materiel_id'], '?')} ×{qte}/{s['quantite']:g}, état {etat}")
    rafraichir()
    flash("Retour enregistré." + (f" Attention : {manquants} pièce(s) manquante(s)." if manquants else ""))
    st.rerun()


def _onglet_historique_materiel(dispo: pd.DataFrame, sorties: pd.DataFrame):
    if sorties.empty:
        st.caption("Aucune sortie.")
        return
    v = sorties.assign(materiel=sorties["materiel_id"].map(dispo.set_index("id")["nom"]))
    v = v.sort_values("date_sortie", ascending=False)
    st.dataframe(v[["date_sortie", "materiel", "quantite", "destination", "manifestation", "emprunteur",
                    "date_retour_prevue", "date_retour", "quantite_retour", "etat_retour", "caution",
                    "caution_rendue", "montant_location"]], hide_index=True, width="stretch", column_config={
        "date_sortie": st.column_config.DateColumn("Sortie", format="DD/MM/YYYY"), "materiel": "Matériel",
        "quantite": "Qté", "destination": "Destination", "manifestation": "Manifestation",
        "emprunteur": "Emprunteur",
        "date_retour_prevue": st.column_config.DateColumn("Retour prévu", format="DD/MM/YYYY"),
        "date_retour": st.column_config.DateColumn("Rentré le", format="DD/MM/YYYY"),
        "quantite_retour": "Qté rendue", "etat_retour": "État", "caution": st.column_config.NumberColumn(
            "Caution", format="%.2f €"), "caution_rendue": "Caution rendue",
        "montant_location": st.column_config.NumberColumn("Location", format="%.2f €")})
