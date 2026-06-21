"""
Section 2 — Taxonomia przemysłowa

Hierarchiczna klasyfikacja węzłów grafu.
Każda gałąź taksonomii mapuje się na węzeł lub atrybut węzła.

Struktura:
  MaterialTaxonomy  — drzewo materiałów (klasa → podklasa → gatunek)
  ProcessTaxonomy   — drzewo procesów (rodzina → typ → wariant)
  ProductTaxonomy   — drzewo wyrobów (kategoria → rodzina → SKU)
  StandardTaxonomy  — drzewo norm (organ → seria → numer)
  SupplierTaxonomy  — klasyfikacja dostawców (tier, region, ryzyko)

eCl@ss 13.0 — europejski standard klasyfikacji produktów przemysłowych.
UNSPSC       — United Nations Standard Products and Services Code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Węzeł taksonomii (drzewo)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TaxonomyNode:
    code:        str
    label:       str
    label_pl:    str
    parent_code: str | None = None
    eclasscc:    str | None = None   # eCl@ss code
    unspsc:      str | None = None   # UNSPSC code
    description: str = ""
    children:    list["TaxonomyNode"] = field(default_factory=list)
    attributes:  dict[str, Any] = field(default_factory=dict)

    def all_codes(self) -> list[str]:
        """Returns this code + all descendant codes (depth-first)."""
        result = [self.code]
        for child in self.children:
            result.extend(child.all_codes())
        return result

    def find(self, code: str) -> "TaxonomyNode | None":
        if self.code == code:
            return self
        for child in self.children:
            found = child.find(code)
            if found:
                return found
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomia materiałów
# ─────────────────────────────────────────────────────────────────────────────

MATERIAL_TAXONOMY = TaxonomyNode(
    code="MAT", label="Materials", label_pl="Materiały",
    children=[
        TaxonomyNode(
            code="MAT.MET", label="Metals", label_pl="Metale",
            eclasscc="42", unspsc="11100000",
            children=[
                TaxonomyNode(
                    code="MAT.MET.FE", label="Ferrous Metals", label_pl="Metale żelazne",
                    eclasscc="42-01",
                    children=[
                        TaxonomyNode(code="MAT.MET.FE.CS",  label="Carbon Steel",      label_pl="Stal węglowa",
                                     eclasscc="42-01-01-01", unspsc="11101500",
                                     attributes={"grades": ["S235JR","S355J2","C45","C60","16MnCr5"]}),
                        TaxonomyNode(code="MAT.MET.FE.SS",  label="Stainless Steel",   label_pl="Stal nierdzewna",
                                     eclasscc="42-01-01-02", unspsc="11101600",
                                     attributes={"grades": ["1.4301 (304)","1.4404 (316L)","1.4571 (316Ti)"]}),
                        TaxonomyNode(code="MAT.MET.FE.HS",  label="High-Strength Steel", label_pl="Stal wysokowytrzymała",
                                     eclasscc="42-01-01-03",
                                     attributes={"grades": ["S700MC","DP600","TRIP800"]}),
                        TaxonomyNode(code="MAT.MET.FE.CI",  label="Cast Iron",         label_pl="Żeliwo",
                                     eclasscc="42-01-02-01",
                                     attributes={"grades": ["GJL-250","GJS-400-15","GJV-400"]}),
                        TaxonomyNode(code="MAT.MET.FE.TOOL",label="Tool Steel",        label_pl="Stal narzędziowa",
                                     attributes={"grades": ["1.2379 (D2)","1.2344 (H13)","HSS M2"]}),
                    ],
                ),
                TaxonomyNode(
                    code="MAT.MET.NF", label="Non-Ferrous Metals", label_pl="Metale nieżelazne",
                    eclasscc="42-02",
                    children=[
                        TaxonomyNode(code="MAT.MET.NF.AL",  label="Aluminium Alloys",  label_pl="Stopy aluminium",
                                     eclasscc="42-02-01-01", unspsc="11101700",
                                     attributes={"grades": ["EN AW-1050","EN AW-6082 T6","EN AW-7075","A380"]}),
                        TaxonomyNode(code="MAT.MET.NF.CU",  label="Copper Alloys",     label_pl="Stopy miedzi",
                                     eclasscc="42-02-02-01",
                                     attributes={"grades": ["Cu-ETP","CuZn37","CuSn8","CuBe2"]}),
                        TaxonomyNode(code="MAT.MET.NF.ZN",  label="Zinc Alloys",       label_pl="Stopy cynku",
                                     attributes={"grades": ["Zamak 3","Zamak 5","Z410"]}),
                        TaxonomyNode(code="MAT.MET.NF.TI",  label="Titanium Alloys",   label_pl="Stopy tytanu",
                                     attributes={"grades": ["Grade 2","Ti-6Al-4V","Ti-3Al-2.5V"]}),
                        TaxonomyNode(code="MAT.MET.NF.MG",  label="Magnesium Alloys",  label_pl="Stopy magnezu",
                                     attributes={"grades": ["AZ31B","AZ91D"]}),
                    ],
                ),
            ],
        ),
        TaxonomyNode(
            code="MAT.POL", label="Polymers", label_pl="Tworzywa sztuczne",
            eclasscc="61", unspsc="11110000",
            children=[
                TaxonomyNode(
                    code="MAT.POL.TP", label="Thermoplastics", label_pl="Termoplasty",
                    children=[
                        TaxonomyNode(code="MAT.POL.TP.PA",  label="Polyamide (Nylon)", label_pl="Poliamid",
                                     attributes={"grades": ["PA6","PA66","PA12","PA6-GF30"]}),
                        TaxonomyNode(code="MAT.POL.TP.PP",  label="Polypropylene",     label_pl="Polipropylen",
                                     attributes={"grades": ["PP-H","PP-C","PP-GF30"]}),
                        TaxonomyNode(code="MAT.POL.TP.PE",  label="Polyethylene",      label_pl="Polietylen",
                                     attributes={"grades": ["HDPE","LDPE","UHMWPE"]}),
                        TaxonomyNode(code="MAT.POL.TP.ABS", label="ABS",               label_pl="ABS",
                                     attributes={"grades": ["ABS standard","ABS-GF","ABS+PC"]}),
                        TaxonomyNode(code="MAT.POL.TP.POM", label="POM (Acetal)",      label_pl="Polioksymetylen",
                                     attributes={"grades": ["POM-C","POM-H"]}),
                        TaxonomyNode(code="MAT.POL.TP.PET", label="PET/PBT",           label_pl="Poliester",
                                     attributes={"grades": ["PET-GF30","PBT-GF30"]}),
                        TaxonomyNode(code="MAT.POL.TP.PEEK",label="PEEK",              label_pl="Polieteroetylen"),
                    ],
                ),
                TaxonomyNode(
                    code="MAT.POL.TS", label="Thermosets", label_pl="Duroplasty",
                    children=[
                        TaxonomyNode(code="MAT.POL.TS.EP",  label="Epoxy",             label_pl="Epoksyd"),
                        TaxonomyNode(code="MAT.POL.TS.PUR", label="Polyurethane",      label_pl="Poliuretan"),
                        TaxonomyNode(code="MAT.POL.TS.UP",  label="Unsaturated Polyester", label_pl="Polister nienasycony"),
                    ],
                ),
                TaxonomyNode(
                    code="MAT.POL.EL", label="Elastomers", label_pl="Elastomery",
                    children=[
                        TaxonomyNode(code="MAT.POL.EL.NBR", label="NBR Rubber",        label_pl="Kauczuk NBR"),
                        TaxonomyNode(code="MAT.POL.EL.EPDM",label="EPDM",              label_pl="EPDM"),
                        TaxonomyNode(code="MAT.POL.EL.SI",  label="Silicone",          label_pl="Silikon"),
                        TaxonomyNode(code="MAT.POL.EL.FKM", label="FKM (Viton)",       label_pl="Fluoroelastomer"),
                    ],
                ),
            ],
        ),
        TaxonomyNode(
            code="MAT.WOOD", label="Wood & Forest Products", label_pl="Drewno i produkty leśne",
            eclasscc="36", unspsc="11120000",
            children=[
                TaxonomyNode(code="MAT.WOOD.SW",  label="Softwood Lumber",   label_pl="Drewno iglaste",
                             attributes={"species": ["pine","spruce","fir","larch"]}),
                TaxonomyNode(code="MAT.WOOD.HW",  label="Hardwood",          label_pl="Drewno liściaste",
                             attributes={"species": ["oak","beech","ash","maple"]}),
                TaxonomyNode(code="MAT.WOOD.PLY", label="Plywood",           label_pl="Sklejka"),
                TaxonomyNode(code="MAT.WOOD.OSB", label="OSB",               label_pl="Płyta OSB"),
                TaxonomyNode(code="MAT.WOOD.MDF", label="MDF/HDF",           label_pl="Płyta MDF/HDF"),
                TaxonomyNode(code="MAT.WOOD.LVL", label="LVL Beams",         label_pl="Belki LVL"),
            ],
        ),
        TaxonomyNode(
            code="MAT.PKG", label="Packaging Materials", label_pl="Materiały opakowaniowe",
            eclasscc="36-02",
            children=[
                TaxonomyNode(code="MAT.PKG.CB",  label="Corrugated Board",   label_pl="Tektura falista"),
                TaxonomyNode(code="MAT.PKG.OCC", label="OCC (Recovered)",    label_pl="Makulatura OCC"),
                TaxonomyNode(code="MAT.PKG.LB",  label="Linerboard",         label_pl="Linerboard"),
                TaxonomyNode(code="MAT.PKG.FILM",label="Stretch/Shrink Film",label_pl="Folia stretch/termokurczliwa"),
                TaxonomyNode(code="MAT.PKG.FOAM",label="Foam Packaging",     label_pl="Pianka opakowaniowa"),
            ],
        ),
        TaxonomyNode(
            code="MAT.CHEM", label="Chemicals & Consumables", label_pl="Chemikalia i materiały eksploatacyjne",
            eclasscc="32",
            children=[
                TaxonomyNode(code="MAT.CHEM.CUT",  label="Cutting Fluids",    label_pl="Ciecze obróbkowe"),
                TaxonomyNode(code="MAT.CHEM.LUB",  label="Lubricants",        label_pl="Smary i oleje"),
                TaxonomyNode(code="MAT.CHEM.ADH",  label="Adhesives",         label_pl="Kleje"),
                TaxonomyNode(code="MAT.CHEM.COAT", label="Surface Coatings",  label_pl="Powłoki"),
                TaxonomyNode(code="MAT.CHEM.WELD", label="Welding Consumables",label_pl="Materiały spawalnicze"),
                TaxonomyNode(code="MAT.CHEM.ABR",  label="Abrasives",         label_pl="Materiały ścierne"),
            ],
        ),
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomia procesów
# ─────────────────────────────────────────────────────────────────────────────

PROCESS_TAXONOMY = TaxonomyNode(
    code="PROC", label="Manufacturing Processes", label_pl="Procesy wytwórcze",
    children=[
        TaxonomyNode(
            code="PROC.CUT", label="Cutting / Machining", label_pl="Obróbka skrawaniem",
            eclasscc="23-17",
            children=[
                TaxonomyNode(code="PROC.CUT.TURN",  label="Turning (CNC/Manual)", label_pl="Toczenie",
                             attributes={"achievable_tolerance_mm": 0.005, "surface_ra_um": 0.8}),
                TaxonomyNode(code="PROC.CUT.MILL",  label="Milling",              label_pl="Frezowanie",
                             attributes={"achievable_tolerance_mm": 0.01}),
                TaxonomyNode(code="PROC.CUT.DRILL", label="Drilling",             label_pl="Wiercenie"),
                TaxonomyNode(code="PROC.CUT.GRIND", label="Grinding",             label_pl="Szlifowanie",
                             attributes={"achievable_tolerance_mm": 0.001, "surface_ra_um": 0.2}),
                TaxonomyNode(code="PROC.CUT.EDM",   label="EDM / Wire EDM",       label_pl="Obróbka elektroerozyjna"),
                TaxonomyNode(code="PROC.CUT.LASER", label="Laser Cutting",        label_pl="Cięcie laserowe"),
                TaxonomyNode(code="PROC.CUT.WATER", label="Waterjet Cutting",     label_pl="Cięcie strumieniem wody"),
                TaxonomyNode(code="PROC.CUT.PLASMA",label="Plasma Cutting",       label_pl="Cięcie plazmowe"),
            ],
        ),
        TaxonomyNode(
            code="PROC.FORM", label="Metal Forming", label_pl="Obróbka plastyczna",
            children=[
                TaxonomyNode(code="PROC.FORM.STAMP", label="Stamping / Punching", label_pl="Tłoczenie"),
                TaxonomyNode(code="PROC.FORM.DEEP",  label="Deep Drawing",        label_pl="Głębokie tłoczenie"),
                TaxonomyNode(code="PROC.FORM.BEND",  label="Bending / Folding",   label_pl="Gięcie"),
                TaxonomyNode(code="PROC.FORM.ROLL",  label="Roll Forming",        label_pl="Profilowanie"),
                TaxonomyNode(code="PROC.FORM.FORGE", label="Forging",             label_pl="Kucie"),
                TaxonomyNode(code="PROC.FORM.EXTR",  label="Extrusion",           label_pl="Wytłaczanie"),
            ],
        ),
        TaxonomyNode(
            code="PROC.JOIN", label="Joining", label_pl="Łączenie",
            children=[
                TaxonomyNode(code="PROC.JOIN.WELD",  label="Welding (MIG/MAG/TIG/Laser)", label_pl="Spawanie"),
                TaxonomyNode(code="PROC.JOIN.BRAZ",  label="Brazing / Soldering",         label_pl="Lutowanie twarde/miękkie"),
                TaxonomyNode(code="PROC.JOIN.ADH",   label="Adhesive Bonding",            label_pl="Klejenie"),
                TaxonomyNode(code="PROC.JOIN.RIVET", label="Riveting",                    label_pl="Nitowanie"),
                TaxonomyNode(code="PROC.JOIN.BOLT",  label="Bolted Connections",          label_pl="Połączenia śrubowe"),
                TaxonomyNode(code="PROC.JOIN.PRESS", label="Press Fit",                   label_pl="Połączenia wciskowe"),
            ],
        ),
        TaxonomyNode(
            code="PROC.HEAT", label="Heat Treatment", label_pl="Obróbka cieplna",
            children=[
                TaxonomyNode(code="PROC.HEAT.HARD",   label="Through Hardening",  label_pl="Hartowanie"),
                TaxonomyNode(code="PROC.HEAT.CASE",   label="Case Hardening",     label_pl="Nawęglanie/Azotowanie"),
                TaxonomyNode(code="PROC.HEAT.ANN",    label="Annealing",          label_pl="Wyżarzanie"),
                TaxonomyNode(code="PROC.HEAT.TEMP",   label="Tempering",          label_pl="Odpuszczanie"),
                TaxonomyNode(code="PROC.HEAT.IND",    label="Induction Hardening",label_pl="Hartowanie indukcyjne"),
            ],
        ),
        TaxonomyNode(
            code="PROC.COAT", label="Surface Treatment", label_pl="Obróbka powierzchniowa",
            children=[
                TaxonomyNode(code="PROC.COAT.GALV",  label="Hot-Dip Galvanizing", label_pl="Cynkowanie ogniowe"),
                TaxonomyNode(code="PROC.COAT.EPOX",  label="Epoxy Powder Coat",   label_pl="Malowanie proszkowe"),
                TaxonomyNode(code="PROC.COAT.ANOD",  label="Anodizing",           label_pl="Anodowanie"),
                TaxonomyNode(code="PROC.COAT.CHROM", label="Chrome Plating",      label_pl="Chromowanie"),
                TaxonomyNode(code="PROC.COAT.NIKEL", label="Nickel Plating",      label_pl="Niklowanie"),
                TaxonomyNode(code="PROC.COAT.PAINT", label="Wet Paint",           label_pl="Lakierowanie"),
                TaxonomyNode(code="PROC.COAT.PVD",   label="PVD/CVD Coating",     label_pl="Powłoki PVD/CVD"),
            ],
        ),
        TaxonomyNode(
            code="PROC.CAST", label="Casting & Moulding", label_pl="Odlewanie i formowanie",
            children=[
                TaxonomyNode(code="PROC.CAST.SAND",  label="Sand Casting",       label_pl="Odlewanie w piasku"),
                TaxonomyNode(code="PROC.CAST.DIE",   label="Die Casting",        label_pl="Odlewanie ciśnieniowe"),
                TaxonomyNode(code="PROC.CAST.INV",   label="Investment Casting", label_pl="Odlewanie metodą traconego wosku"),
                TaxonomyNode(code="PROC.CAST.INJ",   label="Injection Moulding", label_pl="Wtryskiwanie"),
                TaxonomyNode(code="PROC.CAST.BLOW",  label="Blow Moulding",      label_pl="Formowanie z rozdmuchiwaniem"),
            ],
        ),
        TaxonomyNode(
            code="PROC.ADD", label="Additive Manufacturing", label_pl="Wytwarzanie addytywne",
            children=[
                TaxonomyNode(code="PROC.ADD.FDM",  label="FDM / FFF",  label_pl="FDM"),
                TaxonomyNode(code="PROC.ADD.SLA",  label="SLA / DLP",  label_pl="Stereolitografia"),
                TaxonomyNode(code="PROC.ADD.SLS",  label="SLS / SLM",  label_pl="Spiekanie laserowe"),
                TaxonomyNode(code="PROC.ADD.DMLS", label="DMLS / EBM", label_pl="Druk metali"),
            ],
        ),
        TaxonomyNode(
            code="PROC.ASM", label="Assembly & Inspection", label_pl="Montaż i kontrola",
            children=[
                TaxonomyNode(code="PROC.ASM.MAN",  label="Manual Assembly",    label_pl="Montaż ręczny"),
                TaxonomyNode(code="PROC.ASM.ROB",  label="Robotic Assembly",   label_pl="Montaż robotyczny"),
                TaxonomyNode(code="PROC.ASM.CMM",  label="CMM Inspection",     label_pl="Pomiar CMM"),
                TaxonomyNode(code="PROC.ASM.VIS",  label="Vision Inspection",  label_pl="Inspekcja wizualna"),
                TaxonomyNode(code="PROC.ASM.NDT",  label="NDT (UT/RT/MT/PT)", label_pl="Badania nieniszczące"),
                TaxonomyNode(code="PROC.ASM.PACK", label="Packaging",          label_pl="Pakowanie"),
            ],
        ),
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomia norm
# ─────────────────────────────────────────────────────────────────────────────

STANDARD_TAXONOMY = TaxonomyNode(
    code="STD", label="Standards", label_pl="Normy",
    children=[
        TaxonomyNode(
            code="STD.QUALITY", label="Quality Management", label_pl="Zarządzanie jakością",
            children=[
                TaxonomyNode(code="STD.QUALITY.ISO9001",    label="ISO 9001:2015",  label_pl="ISO 9001:2015"),
                TaxonomyNode(code="STD.QUALITY.IATF16949",  label="IATF 16949",     label_pl="IATF 16949 (automotive)"),
                TaxonomyNode(code="STD.QUALITY.AS9100",     label="AS 9100D",       label_pl="AS 9100D (aerospace)"),
            ],
        ),
        TaxonomyNode(
            code="STD.MAT", label="Material Standards", label_pl="Normy materiałowe",
            children=[
                TaxonomyNode(code="STD.MAT.DINEN10025", label="DIN EN 10025", label_pl="Stale konstrukcyjne"),
                TaxonomyNode(code="STD.MAT.DINEN573",   label="DIN EN 573",   label_pl="Aluminium — skład chemiczny"),
                TaxonomyNode(code="STD.MAT.ASTMA36",    label="ASTM A36",     label_pl="Stal konstrukcyjna USA"),
            ],
        ),
        TaxonomyNode(
            code="STD.ENV", label="Environmental", label_pl="Środowiskowe",
            children=[
                TaxonomyNode(code="STD.ENV.ISO14001", label="ISO 14001:2015", label_pl="Zarządzanie środowiskowe"),
                TaxonomyNode(code="STD.ENV.REACH",    label="REACH",          label_pl="REACH (substancje chemiczne)"),
                TaxonomyNode(code="STD.ENV.ROHS",     label="RoHS 3",         label_pl="RoHS 3 (substancje niebezpieczne)"),
                TaxonomyNode(code="STD.ENV.WEEE",     label="WEEE",           label_pl="Dyrektywa o odpadach elektronicznych"),
            ],
        ),
        TaxonomyNode(
            code="STD.SAFETY", label="Safety", label_pl="Bezpieczeństwo",
            children=[
                TaxonomyNode(code="STD.SAFETY.PNED",   label="PED 2014/68/EU", label_pl="Dyrektywa ciśnieniowa"),
                TaxonomyNode(code="STD.SAFETY.MACHED", label="MD 2006/42/EC",  label_pl="Dyrektywa maszynowa"),
                TaxonomyNode(code="STD.SAFETY.ATEX",   label="ATEX 2014/34/EU",label_pl="Dyrektywa ATEX"),
            ],
        ),
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomia dostawców (tier + ryzyko geograficzne)
# ─────────────────────────────────────────────────────────────────────────────

SUPPLIER_TAXONOMY = TaxonomyNode(
    code="SUPP", label="Suppliers", label_pl="Dostawcy",
    children=[
        TaxonomyNode(
            code="SUPP.T1", label="Tier 1 — Direct", label_pl="Tier 1 — bezpośredni",
            description="Dostawcy dostarczający bezpośrednio do zakładu",
        ),
        TaxonomyNode(
            code="SUPP.T2", label="Tier 2 — Sub-tier", label_pl="Tier 2 — poddostawcy",
        ),
        TaxonomyNode(
            code="SUPP.T3", label="Tier 3 — Raw Material", label_pl="Tier 3 — surowce",
        ),
        TaxonomyNode(
            code="SUPP.DIST", label="Distributors", label_pl="Dystrybutorzy",
        ),
        TaxonomyNode(
            code="SUPP.GEO", label="By Geography", label_pl="Według geografii",
            children=[
                TaxonomyNode(code="SUPP.GEO.PL",   label="Poland",         label_pl="Polska",      attributes={"risk": "LOW"}),
                TaxonomyNode(code="SUPP.GEO.EU",    label="EU (ex-PL)",     label_pl="UE",          attributes={"risk": "LOW"}),
                TaxonomyNode(code="SUPP.GEO.CN",    label="China",          label_pl="Chiny",       attributes={"risk": "MEDIUM"}),
                TaxonomyNode(code="SUPP.GEO.IN",    label="India",          label_pl="Indie",       attributes={"risk": "MEDIUM"}),
                TaxonomyNode(code="SUPP.GEO.TR",    label="Turkey",         label_pl="Turcja",      attributes={"risk": "MEDIUM"}),
                TaxonomyNode(code="SUPP.GEO.US",    label="North America",  label_pl="Ameryka Płn.",attributes={"risk": "LOW"}),
                TaxonomyNode(code="SUPP.GEO.HIGH",  label="High-Risk Zones",label_pl="Strefy wysokiego ryzyka", attributes={"risk": "HIGH"}),
            ],
        ),
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Pomocnicze funkcje taksonomii
# ─────────────────────────────────────────────────────────────────────────────

def get_taxonomy_path(code: str, root: TaxonomyNode) -> list[str]:
    """Returns list of codes from root to the given code."""
    def _search(node: TaxonomyNode, target: str, path: list[str]) -> list[str] | None:
        path = path + [node.code]
        if node.code == target:
            return path
        for child in node.children:
            result = _search(child, target, path)
            if result:
                return result
        return None
    return _search(root, code, []) or []


def get_all_leaf_codes(root: TaxonomyNode) -> list[str]:
    """Returns all leaf node codes (no children)."""
    if not root.children:
        return [root.code]
    result = []
    for child in root.children:
        result.extend(get_all_leaf_codes(child))
    return result


def get_siblings(code: str, root: TaxonomyNode) -> list[str]:
    """Returns sibling codes (same parent)."""
    node = root.find(code)
    if node is None:
        return []
    path = get_taxonomy_path(code, root)
    if len(path) < 2:
        return []
    parent_code   = path[-2]
    parent_node   = root.find(parent_code)
    if parent_node is None:
        return []
    return [c.code for c in parent_node.children if c.code != code]


def taxonomy_to_dict(node: TaxonomyNode) -> dict:
    return {
        "code":       node.code,
        "label":      node.label,
        "label_pl":   node.label_pl,
        "eclasscc":   node.eclasscc,
        "unspsc":     node.unspsc,
        "attributes": node.attributes,
        "children":   [taxonomy_to_dict(c) for c in node.children],
    }


# ─────────────────────────────────────────────────────────────────────────────
# eCl@ss mapping — material_class → eCl@ss root code
# ─────────────────────────────────────────────────────────────────────────────

ECLASSCC_MAP: dict[str, str] = {
    "MAT.MET.FE.CS":   "42-01-01-01",
    "MAT.MET.FE.SS":   "42-01-01-02",
    "MAT.MET.NF.AL":   "42-02-01-01",
    "MAT.MET.NF.CU":   "42-02-02-01",
    "MAT.POL.TP.PA":   "61-01-03-01",
    "MAT.POL.TP.PP":   "61-01-07-01",
    "MAT.WOOD.SW":     "36-01-01-01",
    "MAT.PKG.CB":      "36-02-03-01",
}
