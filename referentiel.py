"""Référentiels du logiciel : plan comptable simplifié, manifestations par défaut, structure des tables.

Les catégories suivent le plan comptable associatif (règlement ANC 2018-06), simplifié
pour un comité des fêtes : le code permet de produire un compte de résultat présentable en AG.
"""

APP_NOM = "Comité des Fêtes — Gestion"

# ---------------------------------------------------------------- catégories
CATEGORIES_RECETTES = [
    ("706", "Entrées, inscriptions, repas"),
    ("707", "Buvette et restauration"),
    ("708", "Emplacements et exposants"),
    ("7083", "Locations de matériel"),
    ("7588", "Loto, tombola, jeux"),
    ("74", "Subventions"),
    ("756", "Cotisations"),
    ("754", "Dons et mécénat"),
    ("768", "Produits financiers"),
    ("758", "Autres recettes"),
    ("R99", "À classer (recette)"),
]

CATEGORIES_DEPENSES = [
    ("607", "Achats buvette et alimentation"),
    ("606", "Petit matériel et fournitures"),
    ("6234", "Lots et cadeaux"),
    ("613", "Locations (matériel, salle, chapiteau)"),
    ("616", "Assurances"),
    ("622", "Prestataires et animations"),
    ("623", "Publicité, affiches, impression"),
    ("625", "Déplacements et réceptions"),
    ("626", "Frais postaux et téléphone"),
    ("627", "Frais bancaires"),
    ("651", "SACEM et droits d'auteur"),
    ("637", "Taxes et autorisations"),
    ("658", "Autres dépenses"),
    ("D99", "À classer (dépense)"),
]

SENS = ["Recette", "Dépense"]
MODES_PAIEMENT = ["Espèces", "Chèque", "Virement", "Carte bancaire", "Autre"]


def libelle_categorie(code: str, nom: str) -> str:
    return f"{code} — {nom}"


def categories(sens: str) -> list[str]:
    source = CATEGORIES_RECETTES if sens == "Recette" else CATEGORIES_DEPENSES
    return [libelle_categorie(c, n) for c, n in source]


def categorie_a_classer(sens: str) -> str:
    return categories(sens)[-1]


def index_categories() -> dict[str, tuple[str, str]]:
    """Clé de recherche (code ou nom, en minuscules) -> (sens, libellé complet)."""
    index = {}
    for sens, source in (("Recette", CATEGORIES_RECETTES), ("Dépense", CATEGORIES_DEPENSES)):
        for code, nom in source:
            complet = libelle_categorie(code, nom)
            for cle in (code, nom, complet):
                index[cle.strip().lower()] = (sens, complet)
    return index


# ---------------------------------------------------------- manifestations
MANIFESTATION_GENERALE = "Fonctionnement général"

MANIFESTATIONS_DEFAUT = [
    # nom, mois habituel (1-12, 0 = toute l'année), description
    (MANIFESTATION_GENERALE, 0, "Assurance annuelle, frais bancaires, cotisations, subventions de fonctionnement"),
    ("Brocante", 5, "Emplacements, buvette, restauration"),
    ("Scène ouverte", 6, "Animations, buvette"),
    ("Fête du 14 juillet", 7, "Festivités, feu d'artifice, bal"),
    ("Centre de loisirs", 7, "Accueil des enfants l'été"),
    ("Loto", 10, "Cartons, lots, buvette"),
    ("Marché de Noël", 12, "Exposants, buvette, animations"),
]

# ------------------------------------------------------------------ tables
TABLES = {
    "operations": ["id", "date", "exercice", "manifestation", "sens", "categorie", "libelle",
                   "montant", "mode_paiement", "tiers", "piece", "saisi_par", "saisi_le"],
    "manifestations": ["nom", "mois", "actif", "description"],
    "utilisateurs": ["email", "nom", "role"],
    "previsionnel": ["exercice", "manifestation", "recettes", "depenses", "commentaire", "maj_le"],
    "parametres": ["cle", "valeur"],
    "journal": ["horodatage", "utilisateur", "action", "detail"],
    # ---- stock de consommables (boissons, alimentation)
    "articles": ["id", "nom", "famille", "unite", "prix_vente", "seuil_alerte", "actif"],
    "mouvements_stock": ["id", "date", "article_id", "type", "quantite", "cout_unitaire", "manifestation",
                         "operation_id", "dlc", "commentaire", "saisi_par", "saisi_le"],
    # ---- matériel durable
    "materiel": ["id", "nom", "categorie", "quantite", "etat", "emplacement", "valeur_unitaire",
                 "date_achat", "commentaire"],
    "sorties_materiel": ["id", "materiel_id", "quantite", "destination", "manifestation", "emprunteur",
                         "contact", "caution", "caution_rendue", "montant_location", "date_sortie",
                         "date_retour_prevue", "date_retour", "quantite_retour", "etat_retour",
                         "commentaire", "saisi_par"],
}

# ------------------------------------------------------------------ stock
FAMILLES_ARTICLES = ["Boissons", "Alimentation", "Consommables divers"]
UNITES = ["bouteille", "canette", "fût", "pack", "carton", "kg", "litre", "pièce", "sachet"]
# type de mouvement -> (sens du stock, libellé)
TYPES_MOUVEMENT = {
    "achat": (+1, "Achat / réception"),
    "vente": (-1, "Vente"),
    "offert": (-1, "Offert (bénévoles, invités)"),
    "perte": (-1, "Perte, casse, périmé"),
    "inventaire": (0, "Écart d'inventaire"),   # quantité signée : compté − théorique
    "don_recu": (+1, "Don reçu en nature"),
}
JOURS_ALERTE_DLC = 30

# ------------------------------------------------------------------ matériel
CATEGORIES_MATERIEL = ["Mobilier", "Barnum et abri", "Cuisine et buvette", "Son et lumière",
                       "Décoration", "Signalétique", "Autre"]
ETATS_MATERIEL = ["Bon", "Usé", "À réparer", "Hors service"]
DESTINATIONS = ["Manifestation", "Prêt / location"]

ROLES = ["president", "tresorier", "secretaire", "lecteur"]
ROLES_LIBELLES = {"president": "Président(e)", "tresorier": "Trésorier(e)",
                  "secretaire": "Secrétaire", "lecteur": "Lecture seule"}
ROLES_SAISIE = {"president", "tresorier", "secretaire"}
ROLES_ADMIN = {"president", "tresorier"}

# --------------------------------------------------------- modèle d'import
COLONNES_IMPORT = ["Date", "Manifestation", "Sens", "Catégorie", "Libellé", "Montant",
                   "Mode de paiement", "Tiers", "N° pièce"]

MOIS = ["Toute l'année", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
        "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

DOSSIERS_DRIVE = ["Sauvegardes", "Rapports", "Imports", "Archives"]
