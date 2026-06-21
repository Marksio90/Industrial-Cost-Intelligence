"""
ICI Knowledge Graph Agent

Architecture:
  models.py         § 1. Ontology + data models (NodeType, MaterialNode, SupplierNode, …)
  taxonomy.py       § 2. Industrial taxonomy (eCl@ss, UNSPSC, material/process/standard trees)
  graph_model.py    § 3. In-memory graph (adjacency, BFS/DFS/Dijkstra, NetworkX bridge)
  neo4j_schema.py   § 4. Neo4j DDL — constraints, indexes (B-tree, fulltext, vector HNSW)
  cypher_queries.py § 5. Cypher query library (CRUD, BOM, routing, analytics, recommendations)
  embeddings.py     § 6. Embedding pipeline (SentenceTransformer, batch upsert, similarity)
  search.py         § 7. Hybrid search (fulltext + vector ANN + RRF fusion)
  recommendations.py§ 8. Recommendation engine (graph + embedding + collaborative ensemble)
  api.py            § 9. FastAPI router (nodes, edges, search, path, recommend, analytics)
  monitoring.py     §10. Prometheus metrics (graph size, latency, embedding coverage)
  events.py         §11. Domain events → Redis Streams

Node labels:
  Material  — raw materials, semi-finished parts, components
  Process   — manufacturing operations (turning, welding, coating…)
  Supplier  — vendors, manufacturers, distributors
  Product   — finished goods / SKUs
  Machine   — equipment, production lines
  Standard  — ISO/DIN/EN/ASTM norms
  Offer     — commercial quotes (RFQ → Quote)

Relationship types:
  MADE_OF, PROCESSED_BY, SUPPLIED_BY, CONFORMS_TO,
  SIMILAR_TO, USED_IN, REQUIRES, PRODUCES, PRICED_IN,
  OFFERED_BY, COMPATIBLE_WITH, CERTIFIED_FOR,
  ALTERNATIVE_SUPPLIER, REPLACED_BY, PART_OF
"""

from .api import router as knowledge_graph_router

from .models import (
    NodeType,
    RelationType,
    MaterialClass,
    MaterialForm,
    ProcessType,
    StandardBody,
    GraphNode,
    MaterialNode,
    ProcessNode,
    SupplierNode,
    ProductNode,
    MachineNode,
    StandardNode,
    OfferNode,
    GraphEdge,
    NodeCreate,
    EdgeCreate,
    GraphSearchRequest,
    PathRequest,
    RecommendRequest,
    NodeOut,
    PathOut,
    RecommendationOut,
)

from .taxonomy import (
    TaxonomyNode,
    MATERIAL_TAXONOMY,
    PROCESS_TAXONOMY,
    STANDARD_TAXONOMY,
    SUPPLIER_TAXONOMY,
    ECLASSCC_MAP,
    get_taxonomy_path,
    get_all_leaf_codes,
    get_siblings,
    taxonomy_to_dict,
)

from .graph_model import (
    InMemoryNode,
    InMemoryEdge,
    InMemoryGraph,
    PathResult,
    bfs_shortest_path,
    dijkstra_path,
    dfs_all_paths,
    extract_ego_graph,
    extract_connected_component,
    degree_centrality,
    pagerank,
    find_cycles,
    GraphLoader,
    GraphCache,
    to_networkx,
    from_networkx,
)

from .neo4j_schema import (
    CONSTRAINTS,
    INDEXES,
    NODE_PROPERTY_SCHEMA,
    RELATIONSHIP_SCHEMA,
    apply_schema,
    schema_summary,
)

from .cypher_queries import (
    CRUDQueries,
    TraversalQueries,
    AnalysisQueries,
    RecommendationQueries,
    SearchQueries,
    RiskQueries,
    ReportingQueries,
    CypherRunner,
)

from .embeddings import (
    EMBEDDING_DIM,
    build_node_prompt,
    cosine_similarity,
    l2_normalize,
    pad_or_project,
    deterministic_embedding,
    EmbeddingEncoder,
    EmbeddingPipeline,
    EmbeddingStats,
    upsert_embedding,
    batch_upsert_embeddings,
    property_similarity_materials,
    compute_similarity_edges,
)

from .search import (
    SearchHit,
    SearchResult,
    SearchEngine,
    GraphContextRanker,
    reciprocal_rank_fusion,
)

from .recommendations import (
    Recommendation,
    RecommendationEngine,
    build_similarity_graph,
)

from .monitoring import (
    MetricsCollector,
    GraphHealthSnapshot,
    neo4j_health_check,
    prometheus_metrics_response,
    record_search,
    record_cypher,
    record_recommendation,
    record_embedding_run,
    record_cache_hit,
    record_cache_miss,
)

from .events import (
    KGEventType,
    KGEvent,
    KGEventPublisher,
    KGEventConsumer,
)

__all__ = [
    "knowledge_graph_router",
    # Models
    "NodeType", "RelationType",
    "MaterialClass", "MaterialForm", "ProcessType", "StandardBody",
    "GraphNode", "MaterialNode", "ProcessNode", "SupplierNode",
    "ProductNode", "MachineNode", "StandardNode", "OfferNode",
    "GraphEdge",
    "NodeCreate", "EdgeCreate", "GraphSearchRequest",
    "PathRequest", "RecommendRequest",
    "NodeOut", "PathOut", "RecommendationOut",
    # Taxonomy
    "TaxonomyNode",
    "MATERIAL_TAXONOMY", "PROCESS_TAXONOMY", "STANDARD_TAXONOMY", "SUPPLIER_TAXONOMY",
    "ECLASSCC_MAP",
    "get_taxonomy_path", "get_all_leaf_codes", "get_siblings", "taxonomy_to_dict",
    # Graph model
    "InMemoryNode", "InMemoryEdge", "InMemoryGraph", "PathResult",
    "bfs_shortest_path", "dijkstra_path", "dfs_all_paths",
    "extract_ego_graph", "extract_connected_component",
    "degree_centrality", "pagerank", "find_cycles",
    "GraphLoader", "GraphCache",
    "to_networkx", "from_networkx",
    # Neo4j schema
    "CONSTRAINTS", "INDEXES", "NODE_PROPERTY_SCHEMA", "RELATIONSHIP_SCHEMA",
    "apply_schema", "schema_summary",
    # Cypher
    "CRUDQueries", "TraversalQueries", "AnalysisQueries",
    "RecommendationQueries", "SearchQueries", "RiskQueries", "ReportingQueries",
    "CypherRunner",
    # Embeddings
    "EMBEDDING_DIM",
    "build_node_prompt", "cosine_similarity", "l2_normalize",
    "pad_or_project", "deterministic_embedding",
    "EmbeddingEncoder", "EmbeddingPipeline", "EmbeddingStats",
    "upsert_embedding", "batch_upsert_embeddings",
    "property_similarity_materials", "compute_similarity_edges",
    # Search
    "SearchHit", "SearchResult", "SearchEngine",
    "GraphContextRanker", "reciprocal_rank_fusion",
    # Recommendations
    "Recommendation", "RecommendationEngine", "build_similarity_graph",
    # Monitoring
    "MetricsCollector", "GraphHealthSnapshot",
    "neo4j_health_check", "prometheus_metrics_response",
    "record_search", "record_cypher", "record_recommendation",
    "record_embedding_run", "record_cache_hit", "record_cache_miss",
    # Events
    "KGEventType", "KGEvent", "KGEventPublisher", "KGEventConsumer",
]
