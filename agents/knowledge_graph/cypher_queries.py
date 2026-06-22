"""
Section 5 — Cypher Query Library

Biblioteka gotowych zapytań Cypher dla wszystkich typowych operacji.

Kategorie:
  CRUD          — tworzenie i aktualizacja węzłów/krawędzi
  TRAVERSAL     — przechodzenie grafu (BOM, routing, dostawcy)
  ANALYSIS      — analizy grafowe (PageRank, centrality, community)
  RECOMMENDATION — zapytania dla silnika rekomendacji
  SEARCH        — wyszukiwanie (fulltext, filtrowanie)
  RISK          — analiza ryzyka łańcucha dostaw
  REPORTING     — zapytania raportowe (KPI, spend, coverage)

Parametry:
  Wszystkie zapytania używają parametrów ($param) — nigdy interpolacji stringów.
  Zabezpiecza to przed Cypher injection.
"""
from __future__ import annotations

from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

class CRUDQueries:

    UPSERT_MATERIAL = """
    MERGE (m:Material {node_id: $node_id})
    SET
      m.name           = $name,
      m.name_pl        = $name_pl,
      m.tenant_id      = $tenant_id,
      m.material_class = $material_class,
      m.material_form  = $material_form,
      m.grade          = $grade,
      m.sub_class      = $sub_class,
      m.cas_number     = $cas_number,
      m.hs_code        = $hs_code,
      m.unit           = $unit,
      m.is_critical    = $is_critical,
      m.is_hazmat      = $is_hazmat,
      m.reach_compliant = $reach_compliant,
      m.rohs_compliant  = $rohs_compliant,
      m.tags           = $tags,
      m.updated_at     = datetime()
    ON CREATE SET
      m.created_at = datetime()
    RETURN m.node_id AS node_id
    """

    UPSERT_SUPPLIER = """
    MERGE (s:Supplier {node_id: $node_id})
    SET
      s.name           = $name,
      s.legal_name     = $legal_name,
      s.country        = $country,
      s.city           = $city,
      s.vat_id         = $vat_id,
      s.duns           = $duns,
      s.quality_score  = $quality_score,
      s.delivery_score = $delivery_score,
      s.risk_score     = $risk_score,
      s.iso_9001       = $iso_9001,
      s.iatf_16949     = $iatf_16949,
      s.approved       = $approved,
      s.preferred      = $preferred,
      s.blacklisted    = $blacklisted,
      s.payment_terms  = $payment_terms,
      s.incoterms      = $incoterms,
      s.tags           = $tags,
      s.updated_at     = datetime()
    ON CREATE SET
      s.created_at = datetime()
    RETURN s.node_id AS node_id
    """

    UPSERT_PRODUCT = """
    MERGE (p:Product {node_id: $node_id})
    SET
      p.name           = $name,
      p.sku            = $sku,
      p.ean            = $ean,
      p.description    = $description,
      p.product_family = $product_family,
      p.bom_level      = $bom_level,
      p.standard_cost  = $standard_cost,
      p.currency       = $currency,
      p.active         = $active,
      p.tags           = $tags,
      p.tenant_id      = $tenant_id,
      p.updated_at     = datetime()
    ON CREATE SET
      p.created_at = datetime()
    RETURN p.node_id AS node_id
    """

    UPSERT_PROCESS = """
    MERGE (pr:Process {node_id: $node_id})
    SET
      pr.name            = $name,
      pr.process_type    = $process_type,
      pr.description     = $description,
      pr.cycle_time_s    = $cycle_time_s,
      pr.tolerance_mm    = $tolerance_mm,
      pr.cost_per_hour   = $cost_per_hour,
      pr.co2_kg_per_unit = $co2_kg_per_unit,
      pr.tags            = $tags,
      pr.updated_at      = datetime()
    ON CREATE SET
      pr.created_at = datetime()
    RETURN pr.node_id AS node_id
    """

    MERGE_RELATION = """
    MATCH (a {node_id: $source_id})
    MATCH (b {node_id: $target_id})
    CALL apoc.merge.relationship(a, $rel_type, {}, $properties, b, {})
    YIELD rel
    SET rel.updated_at = datetime()
    RETURN type(rel) AS rel_type, rel.weight AS weight
    """

    MERGE_SIMILAR_TO = """
    MATCH (a:Material {node_id: $source_id})
    MATCH (b:Material {node_id: $target_id})
    MERGE (a)-[r:SIMILAR_TO]-(b)
    SET
      r.weight       = $weight,
      r.method       = $method,
      r.grade_compat = $grade_compat,
      r.updated_at   = datetime()
    RETURN r.weight AS weight
    """

    MERGE_SUPPLIED_BY = """
    MATCH (m {node_id: $material_id})
    MATCH (s:Supplier {node_id: $supplier_id})
    MERGE (m)-[r:SUPPLIED_BY]->(s)
    SET
      r.preferred   = $preferred,
      r.price_eur   = $price_eur,
      r.lead_days   = $lead_days,
      r.contract_id = $contract_id,
      r.since       = $since,
      r.updated_at  = datetime()
    RETURN r.preferred AS preferred
    """

    SET_EMBEDDING = """
    MATCH (n {node_id: $node_id})
    SET n.embedding = $embedding
    RETURN n.node_id AS node_id
    """

    DELETE_NODE = """
    MATCH (n {node_id: $node_id, tenant_id: $tenant_id})
    DETACH DELETE n
    """


# ─────────────────────────────────────────────────────────────────────────────
# TRAVERSAL
# ─────────────────────────────────────────────────────────────────────────────

class TraversalQueries:

    # BOM explosion — wszystkie komponenty produktu (multi-level)
    BOM_EXPLOSION = """
    MATCH (root:Product {node_id: $product_id, tenant_id: $tenant_id})
    CALL apoc.path.subgraphNodes(root, {
      relationshipFilter: 'USED_IN<|MADE_OF<',
      minLevel: 1,
      maxLevel: $max_depth
    }) YIELD node
    MATCH (node)-[r]->(root)
    RETURN
      node.node_id   AS node_id,
      node.name      AS name,
      labels(node)[0] AS node_type,
      r.quantity     AS quantity,
      r.unit         AS unit,
      r.bom_position AS bom_position
    ORDER BY bom_position
    """

    # Routing — łańcuch procesów dla materiału/produktu
    PROCESS_ROUTING = """
    MATCH (m {node_id: $node_id})-[r:PROCESSED_BY]->(p:Process)
    OPTIONAL MATCH (p)-[:REQUIRES]->(mach:Machine)
    RETURN
      p.node_id       AS process_id,
      p.name          AS process_name,
      p.process_type  AS process_type,
      r.sequence      AS sequence,
      r.cycle_time_s  AS cycle_time_s,
      collect(mach.name) AS machines
    ORDER BY r.sequence
    """

    # Ścieżka dostawcy dla materiału (multi-hop: material → offer → supplier)
    SUPPLIER_PATH = """
    MATCH (m:Material {node_id: $material_id})
    OPTIONAL MATCH (m)-[:SUPPLIED_BY]->(s:Supplier)
    OPTIONAL MATCH (o:Offer)-[:PRICED_IN]->(m)
    OPTIONAL MATCH (o)-[:OFFERED_BY]->(os:Supplier)
    WITH m, collect(DISTINCT {
      supplier_id:    s.node_id,
      supplier_name:  s.name,
      country:        s.country,
      preferred:      s.preferred,
      quality_score:  s.quality_score,
      delivery_score: s.delivery_score,
      risk_score:     s.risk_score
    }) AS direct_suppliers,
    collect(DISTINCT {
      offer_id:       o.node_id,
      unit_price:     o.unit_price,
      currency:       o.currency,
      valid_until:    o.valid_until,
      supplier_id:    os.node_id,
      supplier_name:  os.name
    }) AS offers
    RETURN m.node_id AS material_id, direct_suppliers, offers
    """

    # Znajdź substytuty materiału (przez krawędź SIMILAR_TO)
    FIND_SUBSTITUTES = """
    MATCH (m:Material {node_id: $material_id})-[r:SIMILAR_TO]-(sub:Material)
    WHERE r.weight >= $min_similarity
      AND sub.tenant_id = $tenant_id
    OPTIONAL MATCH (sub)-[:SUPPLIED_BY]->(s:Supplier {approved: true})
    RETURN
      sub.node_id       AS node_id,
      sub.name          AS name,
      sub.grade         AS grade,
      sub.material_class AS material_class,
      r.weight          AS similarity,
      r.grade_compat    AS grade_compat,
      r.method          AS method,
      collect(s.name)   AS approved_suppliers
    ORDER BY r.weight DESC
    LIMIT $limit
    """

    # Najkrótsza ścieżka między dwoma węzłami
    SHORTEST_PATH = """
    MATCH (a {node_id: $from_id}), (b {node_id: $to_id})
    CALL apoc.algo.dijkstra(a, b, $rel_types, 'weight') YIELD path, weight
    RETURN
      [node IN nodes(path) | {id: node.node_id, name: node.name, type: labels(node)[0]}] AS nodes,
      [rel  IN relationships(path) | {type: type(rel), weight: rel.weight}] AS rels,
      weight AS total_weight,
      length(path) AS path_length
    LIMIT 1
    """

    # Wszystkie ścieżki dostawca → materiał → produkt
    SUPPLY_CHAIN_PATH = """
    MATCH path = (s:Supplier)-[:SUPPLIED_BY|USED_IN*1..6]-(p:Product)
    WHERE s.node_id = $supplier_id
      AND p.tenant_id = $tenant_id
    RETURN
      [node IN nodes(path) | node.name] AS path_names,
      [node IN nodes(path) | labels(node)[0]] AS path_types,
      length(path) AS depth
    ORDER BY depth
    LIMIT $limit
    """

    # Dostawcy dostarczający do więcej niż jednej kategorii materiałów
    MULTI_CATEGORY_SUPPLIERS = """
    MATCH (s:Supplier)<-[:SUPPLIED_BY]-(m:Material)
    WHERE s.approved = true AND s.tenant_id = $tenant_id
    WITH s, collect(DISTINCT m.material_class) AS classes, count(m) AS mat_count
    WHERE size(classes) > 1
    RETURN
      s.node_id  AS supplier_id,
      s.name     AS name,
      s.country  AS country,
      classes    AS material_classes,
      mat_count  AS materials_supplied
    ORDER BY mat_count DESC
    """

    # Wpływ awarii dostawcy (single point of failure analysis)
    SUPPLIER_FAILURE_IMPACT = """
    MATCH (s:Supplier {node_id: $supplier_id})<-[:SUPPLIED_BY]-(m:Material)
    WHERE NOT EXISTS {
      MATCH (m)-[:SUPPLIED_BY]->(alt:Supplier)
      WHERE alt.node_id <> $supplier_id AND alt.approved = true
    }
    OPTIONAL MATCH (m)<-[:USED_IN]-(p:Product)
    RETURN
      m.node_id       AS material_id,
      m.name          AS material_name,
      m.is_critical   AS is_critical,
      collect(p.name) AS affected_products
    ORDER BY m.is_critical DESC
    """


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

class AnalysisQueries:

    # PageRank materiałów — które są najważniejsze w grafie?
    MATERIAL_PAGERANK = """
    CALL gds.pageRank.stream('material_graph', {
      maxIterations:    20,
      dampingFactor:    0.85,
      relationshipWeightProperty: 'weight'
    })
    YIELD nodeId, score
    MATCH (m:Material) WHERE id(m) = nodeId
    RETURN m.node_id AS node_id, m.name AS name, score
    ORDER BY score DESC
    LIMIT $limit
    """

    # Wykrywanie społeczności materiałów (Louvain)
    MATERIAL_COMMUNITIES = """
    CALL gds.louvain.stream('material_similarity_graph')
    YIELD nodeId, communityId
    MATCH (m:Material) WHERE id(m) = nodeId
    RETURN
      communityId,
      collect(m.name)          AS materials,
      collect(m.material_class) AS classes,
      count(m)                  AS size
    ORDER BY size DESC
    """

    # Centralność pośrednicząca — węzły będące mostami
    BETWEENNESS_CENTRALITY = """
    CALL gds.betweenness.stream('supply_graph')
    YIELD nodeId, score
    WITH nodeId, score
    ORDER BY score DESC
    LIMIT $limit
    MATCH (n) WHERE id(n) = nodeId
    RETURN n.node_id AS node_id, n.name AS name, labels(n)[0] AS type, score
    """

    # Koncentracja spend na dostawcach (Pareto)
    SUPPLIER_SPEND_CONCENTRATION = """
    MATCH (s:Supplier)<-[:OFFERED_BY]-(o:Offer)-[:PRICED_IN]->(m:Material)
    WHERE o.status = 'accepted' AND o.tenant_id = $tenant_id
    WITH s, sum(o.unit_price * coalesce(o.min_qty, 1)) AS spend
    WITH collect({supplier: s.name, spend: spend}) AS rows, sum(spend) AS total
    UNWIND rows AS row
    RETURN
      row.supplier AS supplier_name,
      row.spend    AS spend_eur,
      round(row.spend / total * 100, 2) AS pct_of_total
    ORDER BY spend_eur DESC
    """

    # Materiały bez dostawców (orphan detection)
    ORPHAN_MATERIALS = """
    MATCH (m:Material {tenant_id: $tenant_id})
    WHERE NOT EXISTS { MATCH (m)-[:SUPPLIED_BY]->(:Supplier) }
      AND NOT EXISTS { MATCH (m)<-[:PRICED_IN]-(:Offer) }
    RETURN
      m.node_id      AS node_id,
      m.name         AS name,
      m.material_class AS material_class,
      m.is_critical  AS is_critical
    ORDER BY m.is_critical DESC, m.name
    """

    # Ryzyko koncentracji geograficznej dostawców
    GEO_CONCENTRATION = """
    MATCH (s:Supplier {approved: true})<-[:SUPPLIED_BY]-(m:Material {tenant_id: $tenant_id})
    WITH s.country AS country, count(DISTINCT s) AS supplier_count, count(DISTINCT m) AS material_count
    RETURN
      country,
      supplier_count,
      material_count,
      round(supplier_count * 1.0 / sum(supplier_count) OVER () * 100, 1) AS pct_suppliers
    ORDER BY supplier_count DESC
    """

    # Zgodność ze standardami — które materiały nie mają przypisanych norm?
    COMPLIANCE_GAPS = """
    MATCH (m:Material {tenant_id: $tenant_id})
    WHERE NOT EXISTS { MATCH (m)-[:CONFORMS_TO]->(:Standard) }
    RETURN
      m.node_id      AS node_id,
      m.name         AS name,
      m.material_class AS material_class,
      m.is_critical  AS is_critical
    ORDER BY m.is_critical DESC
    LIMIT $limit
    """

    # Koszt procesu dla produktu (sumowanie przez BOM i routing)
    PRODUCT_PROCESS_COST = """
    MATCH (p:Product {node_id: $product_id})<-[:USED_IN]-(m:Material)
    OPTIONAL MATCH (m)-[r:PROCESSED_BY]->(proc:Process)
    RETURN
      m.node_id          AS material_id,
      m.name             AS material_name,
      proc.name          AS process_name,
      proc.cost_per_hour AS cost_per_hour,
      r.cycle_time_s     AS cycle_time_s,
      round(proc.cost_per_hour * r.cycle_time_s / 3600, 4) AS process_cost_eur
    """


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION
# ─────────────────────────────────────────────────────────────────────────────

class RecommendationQueries:

    # Rekomendacje substytutów przez graph traversal (2-hop)
    SUBSTITUTE_RECOMMENDATIONS = """
    MATCH (seed:Material {node_id: $material_id})
    MATCH (seed)-[r1:SIMILAR_TO]-(hop1:Material)-[r2:SIMILAR_TO]-(cand:Material)
    WHERE cand.node_id <> $material_id
      AND cand.tenant_id = $tenant_id
      AND NOT (seed)-[:SIMILAR_TO]-(cand)
    WITH cand,
         r1.weight * r2.weight AS path_score,
         r1.method             AS method
    RETURN
      cand.node_id       AS node_id,
      cand.name          AS name,
      cand.grade         AS grade,
      max(path_score)    AS score,
      collect(method)[0] AS method
    ORDER BY score DESC
    LIMIT $limit
    """

    # Rekomendacje dostawców dla nowego materiału (na podstawie istniejących dostawców podobnych materiałów)
    SUPPLIER_RECOMMENDATIONS = """
    MATCH (m:Material {node_id: $material_id})-[sim:SIMILAR_TO]-(similar:Material)-[:SUPPLIED_BY]->(s:Supplier)
    WHERE s.approved = true
      AND NOT (m)-[:SUPPLIED_BY]->(s)
      AND s.blacklisted = false
    WITH s, avg(sim.weight) AS avg_similarity, count(similar) AS coverage
    RETURN
      s.node_id        AS node_id,
      s.name           AS name,
      s.country        AS country,
      s.quality_score  AS quality_score,
      s.risk_score     AS risk_score,
      avg_similarity   AS similarity_score,
      coverage         AS covered_materials,
      avg_similarity * (1 - coalesce(s.risk_score, 0.5)) AS composite_score
    ORDER BY composite_score DESC
    LIMIT $limit
    """

    # Rekomendacje norm dla materiału (na podstawie norm stosowanych dla podobnych materiałów)
    STANDARD_RECOMMENDATIONS = """
    MATCH (m:Material {node_id: $material_id})-[:SIMILAR_TO]-(sim:Material)-[:CONFORMS_TO]->(std:Standard)
    WHERE NOT (m)-[:CONFORMS_TO]->(std)
    WITH std, count(sim) AS support
    RETURN
      std.node_id  AS node_id,
      std.number   AS number,
      std.title    AS title,
      std.body     AS body,
      support      AS support_count
    ORDER BY support DESC
    LIMIT $limit
    """

    # Materiały często używane razem z danym materiałem (collaborative filtering)
    CO_OCCURRENCE = """
    MATCH (m:Material {node_id: $material_id})<-[:USED_IN]-(p:Product)-[:USED_IN]->(co:Material)
    WHERE co.node_id <> $material_id
    WITH co, count(p) AS co_count
    RETURN
      co.node_id       AS node_id,
      co.name          AS name,
      co.material_class AS material_class,
      co_count         AS co_occurrence_count
    ORDER BY co_count DESC
    LIMIT $limit
    """

    # ANN vector similarity (Neo4j 5.11+ vector index)
    VECTOR_SIMILARITY = """
    CALL db.index.vector.queryNodes($index_name, $k, $embedding)
    YIELD node, score
    WHERE node.tenant_id = $tenant_id
      AND node.node_id <> $exclude_id
    RETURN
      node.node_id  AS node_id,
      node.name     AS name,
      labels(node)[0] AS node_type,
      score         AS similarity
    ORDER BY similarity DESC
    """


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────────────────────────────────────────

class SearchQueries:

    # Fulltext search across node types
    FULLTEXT_SEARCH = """
    CALL db.index.fulltext.queryNodes($index_name, $query_string)
    YIELD node, score
    WHERE node.tenant_id = $tenant_id
    RETURN
      node.node_id   AS node_id,
      node.name      AS name,
      labels(node)[0] AS node_type,
      score          AS relevance
    ORDER BY score DESC
    LIMIT $limit
    """

    # Filtrowane wyszukiwanie materiałów
    FILTER_MATERIALS = """
    MATCH (m:Material {tenant_id: $tenant_id})
    WHERE ($material_class IS NULL OR m.material_class = $material_class)
      AND ($grade        IS NULL OR m.grade CONTAINS $grade)
      AND ($is_critical  IS NULL OR m.is_critical = $is_critical)
      AND ($is_hazmat    IS NULL OR m.is_hazmat   = $is_hazmat)
    OPTIONAL MATCH (m)-[:SUPPLIED_BY]->(s:Supplier {approved: true})
    RETURN
      m.node_id      AS node_id,
      m.name         AS name,
      m.grade        AS grade,
      m.material_class AS material_class,
      m.is_critical  AS is_critical,
      count(s)       AS approved_supplier_count
    ORDER BY m.name
    SKIP  $skip
    LIMIT $limit
    """

    # Wyszukiwanie dostawców po certyfikatach + regionie
    FILTER_SUPPLIERS = """
    MATCH (s:Supplier {tenant_id: $tenant_id, approved: true})
    WHERE ($country    IS NULL OR s.country    = $country)
      AND ($iso_9001   IS NULL OR s.iso_9001   = $iso_9001)
      AND ($iatf_16949 IS NULL OR s.iatf_16949 = $iatf_16949)
      AND ($max_risk   IS NULL OR s.risk_score <= $max_risk)
      AND s.blacklisted = false
    RETURN
      s.node_id       AS node_id,
      s.name          AS name,
      s.country       AS country,
      s.quality_score AS quality_score,
      s.risk_score    AS risk_score,
      s.iso_9001      AS iso_9001,
      s.iatf_16949    AS iatf_16949
    ORDER BY s.quality_score DESC
    SKIP  $skip
    LIMIT $limit
    """

    # Szukaj ofert w przedziale cenowym
    FILTER_OFFERS = """
    MATCH (o:Offer {tenant_id: $tenant_id, status: 'active'})-[:PRICED_IN]->(m:Material)
    MATCH (o)-[:OFFERED_BY]->(s:Supplier)
    WHERE ($material_id IS NULL OR m.node_id = $material_id)
      AND ($max_price   IS NULL OR o.unit_price <= $max_price)
      AND ($min_price   IS NULL OR o.unit_price >= $min_price)
      AND (o.valid_until IS NULL OR o.valid_until >= date())
    RETURN
      o.node_id       AS offer_id,
      o.offer_number  AS offer_number,
      o.unit_price    AS unit_price,
      o.currency      AS currency,
      o.valid_until   AS valid_until,
      o.lead_time_days AS lead_time_days,
      m.name          AS material_name,
      s.name          AS supplier_name,
      s.country       AS supplier_country
    ORDER BY o.unit_price ASC
    LIMIT $limit
    """


# ─────────────────────────────────────────────────────────────────────────────
# RISK
# ─────────────────────────────────────────────────────────────────────────────

class RiskQueries:

    # Materiały krytyczne z jednym zatwierdzonym dostawcą
    CRITICAL_SINGLE_SOURCE = """
    MATCH (m:Material {is_critical: true, tenant_id: $tenant_id})
    OPTIONAL MATCH (m)-[:SUPPLIED_BY]->(s:Supplier {approved: true})
    WITH m, collect(s) AS suppliers
    WHERE size(suppliers) <= 1
    RETURN
      m.node_id      AS material_id,
      m.name         AS name,
      size(suppliers) AS supplier_count,
      [s IN suppliers | s.name] AS supplier_names,
      [s IN suppliers | s.country] AS countries
    ORDER BY supplier_count
    """

    # Dostawcy z wysokim ryzykiem geopolitycznym
    HIGH_RISK_SUPPLIERS = """
    MATCH (s:Supplier {approved: true})<-[:SUPPLIED_BY]-(m:Material {tenant_id: $tenant_id})
    WHERE s.risk_score >= $threshold
       OR s.country IN $sanctioned_countries
    RETURN
      s.node_id      AS supplier_id,
      s.name         AS name,
      s.country      AS country,
      s.risk_score   AS risk_score,
      collect(m.name) AS supplied_materials,
      count(m)       AS material_count
    ORDER BY s.risk_score DESC
    """

    # Łańcuch zależności (ripple effect) — co jest zagrożone gdy dostawca X wypada?
    RIPPLE_EFFECT = """
    MATCH (s:Supplier {node_id: $supplier_id})
    MATCH path = (s)<-[:SUPPLIED_BY|USED_IN*1..5]-(affected)
    WHERE affected.tenant_id = $tenant_id
    WITH DISTINCT affected,
         length(path) AS depth,
         labels(affected)[0] AS affected_type
    RETURN
      affected.node_id   AS node_id,
      affected.name      AS name,
      affected_type      AS node_type,
      min(depth)         AS min_depth,
      affected.is_critical AS is_critical
    ORDER BY min_depth, affected.is_critical DESC
    """

    # Oferty wygasające w najbliższych N dniach
    EXPIRING_OFFERS = """
    MATCH (o:Offer {status: 'active', tenant_id: $tenant_id})-[:PRICED_IN]->(m:Material)
    MATCH (o)-[:OFFERED_BY]->(s:Supplier)
    WHERE o.valid_until <= date() + duration({days: $days_ahead})
      AND o.valid_until >= date()
    RETURN
      o.node_id       AS offer_id,
      o.valid_until   AS valid_until,
      o.unit_price    AS unit_price,
      o.currency      AS currency,
      m.name          AS material_name,
      m.is_critical   AS is_critical,
      s.name          AS supplier_name,
      duration.between(date(), o.valid_until).days AS days_remaining
    ORDER BY days_remaining, m.is_critical DESC
    """


# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────

class ReportingQueries:

    # KPI grafu — liczba węzłów i krawędzi per typ
    GRAPH_KPI = """
    CALL apoc.meta.stats()
    YIELD labels, relTypesCount
    RETURN labels, relTypesCount
    """

    # Statystyki tenant
    TENANT_STATS = """
    MATCH (n {tenant_id: $tenant_id})
    RETURN
      labels(n)[0] AS node_type,
      count(n)     AS count
    ORDER BY count DESC
    """

    # Pokrycie dostawców — jaki % materiałów ma >= N zatwierdzonych dostawców?
    SUPPLIER_COVERAGE = """
    MATCH (m:Material {tenant_id: $tenant_id})
    OPTIONAL MATCH (m)-[:SUPPLIED_BY]->(s:Supplier {approved: true})
    WITH m, count(s) AS sup_count
    RETURN
      sup_count AS approved_suppliers,
      count(m)  AS material_count,
      round(count(m) * 100.0 / sum(count(m)) OVER (), 1) AS pct
    ORDER BY sup_count
    """

    # Pokrycie embeddingów — które węzły nie mają wektora?
    EMBEDDING_COVERAGE = """
    MATCH (n {tenant_id: $tenant_id})
    WHERE labels(n)[0] IN ['Material', 'Supplier', 'Product']
    RETURN
      labels(n)[0]                   AS node_type,
      count(n)                       AS total,
      count(n.embedding)             AS with_embedding,
      count(n) - count(n.embedding)  AS missing_embedding
    ORDER BY missing_embedding DESC
    """

    # Top podobne pary materiałów (do walidacji)
    TOP_SIMILAR_PAIRS = """
    MATCH (a:Material)-[r:SIMILAR_TO]-(b:Material)
    WHERE a.tenant_id = $tenant_id
      AND id(a) < id(b)
    RETURN
      a.name    AS material_a,
      b.name    AS material_b,
      r.weight  AS similarity,
      r.method  AS method
    ORDER BY r.weight DESC
    LIMIT $limit
    """


# ─────────────────────────────────────────────────────────────────────────────
# Query runner helper
# ─────────────────────────────────────────────────────────────────────────────

class CypherRunner:
    """
    Async wrapper around Neo4j AsyncDriver for executing Cypher queries.
    """

    def __init__(self, driver: Any, default_database: str = "neo4j") -> None:
        self._driver = driver
        self._db     = default_database

    async def run(
        self,
        query:    str,
        params:   dict | None = None,
        database: str | None  = None,
    ) -> list[dict]:
        db = database or self._db
        async with self._driver.session(database=db) as session:
            result = await session.run(query, **(params or {}))
            records = await result.data()
            return records

    async def run_write(
        self,
        query:    str,
        params:   dict | None = None,
    ) -> list[dict]:
        async with self._driver.session(database=self._db) as session:
            result = await session.execute_write(
                lambda tx: tx.run(query, **(params or {}))
            )
            return []   # write transactions don't return data easily; use RETURN clause

    async def batch_write(
        self,
        query:  str,
        rows:   list[dict],
        batch:  int = 500,
    ) -> int:
        """UNWIND batch write — efficient for bulk imports."""
        total = 0
        for i in range(0, len(rows), batch):
            chunk  = rows[i: i + batch]
            result = await self.run(
                f"UNWIND $rows AS row {query}",
                {"rows": chunk},
            )
            total += len(chunk)
        return total

    async def explain(self, query: str, params: dict | None = None) -> dict:
        """EXPLAIN query for query plan inspection."""
        async with self._driver.session(database=self._db) as session:
            result = await session.run(f"EXPLAIN {query}", **(params or {}))
            summary = await result.consume()
            return {
                "plan":     str(summary.plan),
                "counters": summary.counters.__dict__,
            }
