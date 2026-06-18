# Material Intelligence Engine

Centralny moduł wiedzy o materiałach w platformie Industrial Cost Intelligence.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-scope-and-boundaries.md](./01-scope-and-boundaries.md) | Zakres i granice odpowiedzialności modułu |
| [02-domain-model.md](./02-domain-model.md) | Model domenowy i wszystkie encje |
| [03-sql-schema.sql](./03-sql-schema.sql) | Kompletny schemat PostgreSQL z indeksami i funkcjami |
| [04-taxonomy-and-attributes.md](./04-taxonomy-and-attributes.md) | Taksonomia materiałów + atrybuty per klasa + Density Library |
| [05-standards-substitutions-compatibility.md](./05-standards-substitutions-compatibility.md) | Normy ISO/DIN/EN/ASTM + algorytm zamienników + Compatibility Engine |
| [06-cost-model-market-price-supplier.md](./06-cost-model-market-price-supplier.md) | Model kosztowy + warstwa cen rynkowych + mapowanie dostawców |
| [07-api-openapi.yaml](./07-api-openapi.yaml) | Kompletna specyfikacja REST API (OpenAPI 3.1) |
| [08-events-validation-search-ai.md](./08-events-validation-search-ai.md) | Kafka Events + reguły walidacji + Search Engine + AI/Embeddings + Monitoring |
| [09-security-testing-scalability-risks-roadmap.md](./09-security-testing-scalability-risks-roadmap.md) | Security + strategia testów + skalowalność + ryzyka + roadmap |

## Obsługiwane klasy materiałów

- **Metale:** S235, S355, DC01, DX51, 304, 316, Aluminium, Miedź, Mosiądz
- **Tworzywa:** ABS, PC, PA6, PA66, POM, PE, PP, PET
- **Drewno:** MDF, HDF, Sklejka, Drewno lite
- **Opakowania:** Kartony, Tektura falista, Tektura lita, Papier
- **Kompozyty:** Włókno szklane, Włókno węglowe
- **Specjalne:** Pianki, Gumy, Elastomery, Izolacje

## Kluczowe zależności techniczne

- **Baza danych:** PostgreSQL 16+ z rozszerzeniami `pgvector`, `pg_trgm`, `uuid-ossp`
- **Cache:** Redis 7+
- **Messaging:** Apache Kafka 3+
- **AI/Embeddings:** OpenAI `text-embedding-3-small` (1536 dim) + pgvector HNSW
- **Monitoring:** Prometheus + Grafana
