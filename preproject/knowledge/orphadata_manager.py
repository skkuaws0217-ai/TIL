"""Orphadata manager -- download and parse rare disease data from Orphadata.

Downloads two primary Orphadata XML resources:
    - en_product4.xml : Disease-HPO phenotype associations
    - en_product6.xml : Disease-gene associations

Parses XML into Python lookups keyed by OrphaCode, and caches the results
as JSON in the project output directory for fast subsequent access.

Reference Sources:
    - Orphadata: Free access products, http://www.orphadata.org/
    - HPO (Human Phenotype Ontology), https://hpo.jax.org/
    - OMIM (Online Mendelian Inheritance in Man)
    - Orphanet: Portal for rare diseases and orphan drugs

Data URLs:
    - http://www.orphadata.org/data/xml/en_product4.xml  (disease-HPO)
    - http://www.orphadata.org/data/xml/en_product6.xml  (disease-gene)
"""

import os
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# ─── Constants ───
ORPHADATA_PRODUCT4_URL = "http://www.orphadata.org/data/xml/en_product4.xml"
ORPHADATA_PRODUCT6_URL = "http://www.orphadata.org/data/xml/en_product6.xml"

_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
_OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")
_CACHE_DIR = os.path.join(_OUTPUT_DIR, "orphadata_cache")

PRODUCT4_XML_PATH = os.path.join(_CACHE_DIR, "en_product4.xml")
PRODUCT6_XML_PATH = os.path.join(_CACHE_DIR, "en_product6.xml")
PRODUCT4_JSON_PATH = os.path.join(_CACHE_DIR, "disease_hpo.json")
PRODUCT6_JSON_PATH = os.path.join(_CACHE_DIR, "disease_gene.json")
LUNG_DISEASES_JSON_PATH = os.path.join(_CACHE_DIR, "lung_rare_diseases.json")

# ─── Lung / respiratory keywords for filtering rare diseases ───
# Ref: Murray & Nadel's Ch. on rare pulmonary diseases, Orphanet respiratory group
LUNG_KEYWORDS = [
    "lung", "pulmonary", "respiratory", "bronch", "alveol",
    "pleural", "trachea", "pneumo", "fibrosis", "interstitial",
    "airway", "diaphragm", "thoracic", "ciliary", "surfactant",
]


class OrphadataManager:
    """Download, parse, and query Orphadata rare disease resources.

    Usage:
        mgr = OrphadataManager()
        mgr.download_all()        # Downloads XML files if not cached
        mgr.parse_all()           # Parses XML -> JSON cache
        lung_diseases = mgr.get_lung_rare_diseases()
    """

    def __init__(self, cache_dir: str = _CACHE_DIR):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

        # Loaded lookups
        self.disease_hpo: Dict[str, Dict[str, Any]] = {}
        self.disease_gene: Dict[str, Dict[str, Any]] = {}
        self.lung_rare_diseases: Dict[str, Dict[str, Any]] = {}

    # ─── Download ────────────────────────────────────────────────

    def download_file(self, url: str, dest_path: str, force: bool = False) -> bool:
        """Download a file from URL to dest_path.

        Args:
            url: Remote URL.
            dest_path: Local path to save.
            force: Re-download even if file exists.

        Returns:
            True if download succeeded or file already exists.
        """
        if os.path.exists(dest_path) and not force:
            logger.info("File already cached: %s", dest_path)
            return True

        logger.info("Downloading %s -> %s", url, dest_path)
        try:
            urllib.request.urlretrieve(url, dest_path)
            size_mb = os.path.getsize(dest_path) / (1024 * 1024)
            logger.info("Downloaded %.1f MB: %s", size_mb, dest_path)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.error("Download failed for %s: %s", url, exc)
            return False

    def download_all(self, force: bool = False) -> Dict[str, bool]:
        """Download both Orphadata XML files.

        Returns:
            Dict mapping filename to success boolean.
        """
        results = {}
        results["product4"] = self.download_file(
            ORPHADATA_PRODUCT4_URL, PRODUCT4_XML_PATH, force=force
        )
        results["product6"] = self.download_file(
            ORPHADATA_PRODUCT6_URL, PRODUCT6_XML_PATH, force=force
        )
        return results

    # ─── XML Parsing ─────────────────────────────────────────────

    def parse_product4(self, xml_path: str = PRODUCT4_XML_PATH) -> Dict[str, Dict[str, Any]]:
        """Parse en_product4.xml: disease-HPO phenotype associations.

        XML structure (Orphadata format):
            <JDBOR>
              <DisorderList>
                <Disorder id="...">
                  <OrphaCode>...</OrphaCode>
                  <Name lang="en">...</Name>
                  <HPODisorderAssociationList>
                    <HPODisorderAssociation>
                      <HPO>
                        <HPOId>HP:XXXXXXX</HPOId>
                        <HPOTerm>...</HPOTerm>
                      </HPO>
                      <HPOFrequency>
                        <Name lang="en">...</Name>
                      </HPOFrequency>
                    </HPODisorderAssociation>
                  </HPODisorderAssociationList>
                </Disorder>
              </DisorderList>
            </JDBOR>

        Returns:
            Dict[OrphaCode -> {"name": str, "hpo_terms": [{"hpo_id", "term", "frequency"}]}]
        """
        if not os.path.exists(xml_path):
            logger.warning("Product4 XML not found at %s. Run download_all() first.", xml_path)
            return {}

        logger.info("Parsing product4 XML: %s", xml_path)
        result: Dict[str, Dict[str, Any]] = {}

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            logger.error("XML parse error for product4: %s", exc)
            return {}

        # Navigate to disorder list -- handle varying Orphadata XML structures
        disorders = root.iter("Disorder")

        for disorder in disorders:
            orpha_code_el = disorder.find("OrphaCode")
            name_el = disorder.find("Name")

            if orpha_code_el is None:
                continue

            orpha_code = orpha_code_el.text.strip() if orpha_code_el.text else ""
            disease_name = ""
            if name_el is not None and name_el.text:
                disease_name = name_el.text.strip()

            hpo_terms = []
            # Try multiple possible XML paths
            for assoc in disorder.iter("HPODisorderAssociation"):
                hpo_el = assoc.find("HPO")
                if hpo_el is None:
                    continue

                hpo_id_el = hpo_el.find("HPOId")
                hpo_term_el = hpo_el.find("HPOTerm")

                hpo_id = hpo_id_el.text.strip() if (hpo_id_el is not None and hpo_id_el.text) else ""
                hpo_term = hpo_term_el.text.strip() if (hpo_term_el is not None and hpo_term_el.text) else ""

                # Frequency
                freq = ""
                freq_el = assoc.find("HPOFrequency")
                if freq_el is not None:
                    freq_name = freq_el.find("Name")
                    if freq_name is not None and freq_name.text:
                        freq = freq_name.text.strip()

                if hpo_id:
                    hpo_terms.append({
                        "hpo_id": hpo_id,
                        "term": hpo_term,
                        "frequency": freq,
                    })

            if orpha_code:
                result[orpha_code] = {
                    "name": disease_name,
                    "hpo_terms": hpo_terms,
                }

        self.disease_hpo = result
        logger.info("Parsed %d diseases with HPO associations", len(result))
        return result

    def parse_product6(self, xml_path: str = PRODUCT6_XML_PATH) -> Dict[str, Dict[str, Any]]:
        """Parse en_product6.xml: disease-gene associations.

        XML structure (Orphadata format):
            <JDBOR>
              <DisorderList>
                <Disorder id="...">
                  <OrphaCode>...</OrphaCode>
                  <Name lang="en">...</Name>
                  <DisorderGeneAssociationList>
                    <DisorderGeneAssociation>
                      <Gene>
                        <Symbol>...</Symbol>
                        <Name lang="en">...</Name>
                      </Gene>
                      <DisorderGeneAssociationType>
                        <Name lang="en">...</Name>
                      </DisorderGeneAssociationType>
                    </DisorderGeneAssociation>
                  </DisorderGeneAssociationList>
                </Disorder>
              </DisorderList>
            </JDBOR>

        Returns:
            Dict[OrphaCode -> {"name": str, "genes": [{"symbol", "name", "association_type"}]}]
        """
        if not os.path.exists(xml_path):
            logger.warning("Product6 XML not found at %s. Run download_all() first.", xml_path)
            return {}

        logger.info("Parsing product6 XML: %s", xml_path)
        result: Dict[str, Dict[str, Any]] = {}

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            logger.error("XML parse error for product6: %s", exc)
            return {}

        for disorder in root.iter("Disorder"):
            orpha_code_el = disorder.find("OrphaCode")
            name_el = disorder.find("Name")

            if orpha_code_el is None:
                continue

            orpha_code = orpha_code_el.text.strip() if orpha_code_el.text else ""
            disease_name = ""
            if name_el is not None and name_el.text:
                disease_name = name_el.text.strip()

            genes = []
            for assoc in disorder.iter("DisorderGeneAssociation"):
                gene_el = assoc.find("Gene")
                if gene_el is None:
                    continue

                symbol_el = gene_el.find("Symbol")
                gene_name_el = gene_el.find("Name")

                symbol = symbol_el.text.strip() if (symbol_el is not None and symbol_el.text) else ""
                gene_name = gene_name_el.text.strip() if (gene_name_el is not None and gene_name_el.text) else ""

                assoc_type = ""
                assoc_type_el = assoc.find("DisorderGeneAssociationType")
                if assoc_type_el is not None:
                    type_name = assoc_type_el.find("Name")
                    if type_name is not None and type_name.text:
                        assoc_type = type_name.text.strip()

                if symbol:
                    genes.append({
                        "symbol": symbol,
                        "name": gene_name,
                        "association_type": assoc_type,
                    })

            if orpha_code:
                result[orpha_code] = {
                    "name": disease_name,
                    "genes": genes,
                }

        self.disease_gene = result
        logger.info("Parsed %d diseases with gene associations", len(result))
        return result

    def parse_all(self) -> None:
        """Parse both product4 and product6 XMLs and cache as JSON."""
        self.parse_product4()
        self.parse_product6()
        self._filter_lung_diseases()
        self._save_cache()

    # ─── Filtering ───────────────────────────────────────────────

    def _filter_lung_diseases(self) -> None:
        """Filter to lung/respiratory rare diseases based on name keywords.

        Uses LUNG_KEYWORDS list to identify diseases whose name suggests
        pulmonary involvement.  Also includes diseases with lung-relevant
        HPO terms (HP:0002206 Pulmonary fibrosis, HP:0002090 Pneumonia, etc.).
        """
        # Ref: HPO terms indicating pulmonary involvement
        # Source: https://hpo.jax.org/ -- Abnormality of the respiratory system branch
        lung_hpo_prefixes = {
            "HP:0002206",  # Pulmonary fibrosis
            "HP:0002090",  # Pneumonia
            "HP:0002091",  # Pulmonary arteriovenous malformation
            "HP:0002093",  # Respiratory insufficiency
            "HP:0002094",  # Dyspnea
            "HP:0002105",  # Hemoptysis
            "HP:0002110",  # Bronchiectasis
            "HP:0002202",  # Pleural effusion
            "HP:0006530",  # Abnormal pulmonary interstitial morphology
            "HP:0002795",  # Abnormal respiratory system physiology
            "HP:0020163",  # Ground glass opacity on pulmonary HRCT
        }

        self.lung_rare_diseases = {}

        for orpha_code, info in self.disease_hpo.items():
            name_lower = info.get("name", "").lower()

            # Check name keywords
            name_match = any(kw in name_lower for kw in LUNG_KEYWORDS)

            # Check HPO terms
            hpo_match = False
            for hpo in info.get("hpo_terms", []):
                if hpo.get("hpo_id", "") in lung_hpo_prefixes:
                    hpo_match = True
                    break

            if name_match or hpo_match:
                entry = dict(info)
                # Merge gene info if available
                gene_info = self.disease_gene.get(orpha_code, {})
                entry["genes"] = gene_info.get("genes", [])
                self.lung_rare_diseases[orpha_code] = entry

        logger.info(
            "Filtered %d lung/respiratory rare diseases from %d total",
            len(self.lung_rare_diseases), len(self.disease_hpo),
        )

    # ─── Cache I/O ───────────────────────────────────────────────

    def _save_cache(self) -> None:
        """Save parsed data as JSON for fast subsequent loads."""
        os.makedirs(self.cache_dir, exist_ok=True)

        if self.disease_hpo:
            with open(PRODUCT4_JSON_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.disease_hpo, fh, ensure_ascii=False, indent=2)
            logger.info("Cached disease-HPO data: %s", PRODUCT4_JSON_PATH)

        if self.disease_gene:
            with open(PRODUCT6_JSON_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.disease_gene, fh, ensure_ascii=False, indent=2)
            logger.info("Cached disease-gene data: %s", PRODUCT6_JSON_PATH)

        if self.lung_rare_diseases:
            with open(LUNG_DISEASES_JSON_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.lung_rare_diseases, fh, ensure_ascii=False, indent=2)
            logger.info("Cached lung rare diseases: %s", LUNG_DISEASES_JSON_PATH)

    def load_cache(self) -> bool:
        """Load previously parsed JSON cache.

        Returns:
            True if all caches loaded successfully.
        """
        loaded = True

        if os.path.exists(PRODUCT4_JSON_PATH):
            with open(PRODUCT4_JSON_PATH, "r", encoding="utf-8") as fh:
                self.disease_hpo = json.load(fh)
            logger.info("Loaded cached disease-HPO: %d entries", len(self.disease_hpo))
        else:
            loaded = False

        if os.path.exists(PRODUCT6_JSON_PATH):
            with open(PRODUCT6_JSON_PATH, "r", encoding="utf-8") as fh:
                self.disease_gene = json.load(fh)
            logger.info("Loaded cached disease-gene: %d entries", len(self.disease_gene))
        else:
            loaded = False

        if os.path.exists(LUNG_DISEASES_JSON_PATH):
            with open(LUNG_DISEASES_JSON_PATH, "r", encoding="utf-8") as fh:
                self.lung_rare_diseases = json.load(fh)
            logger.info("Loaded cached lung rare diseases: %d entries",
                        len(self.lung_rare_diseases))
        else:
            loaded = False

        return loaded

    # ─── Query API ───────────────────────────────────────────────

    def get_lung_rare_diseases(self) -> Dict[str, Dict[str, Any]]:
        """Return filtered lung/respiratory rare diseases.

        Attempts to load from cache first, then from parsed data.
        """
        if self.lung_rare_diseases:
            return self.lung_rare_diseases
        self.load_cache()
        return self.lung_rare_diseases

    def get_disease_hpo_terms(self, orpha_code: str) -> List[Dict[str, str]]:
        """Get HPO terms for a specific disease by OrphaCode."""
        if not self.disease_hpo:
            self.load_cache()
        info = self.disease_hpo.get(orpha_code, {})
        return info.get("hpo_terms", [])

    def get_disease_genes(self, orpha_code: str) -> List[Dict[str, str]]:
        """Get associated genes for a specific disease by OrphaCode."""
        if not self.disease_gene:
            self.load_cache()
        info = self.disease_gene.get(orpha_code, {})
        return info.get("genes", [])

    def search_by_name(self, query: str) -> Dict[str, Dict[str, Any]]:
        """Search diseases by name substring (case-insensitive)."""
        if not self.disease_hpo:
            self.load_cache()
        query_lower = query.lower()
        return {
            code: info for code, info in self.disease_hpo.items()
            if query_lower in info.get("name", "").lower()
        }
