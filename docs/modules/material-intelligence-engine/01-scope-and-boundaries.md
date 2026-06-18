# Material Intelligence Engine — Zakres i Granice Odpowiedzialności

## 1. Zakres odpowiedzialności modułu

Material Intelligence Engine (MIE) stanowi centralne repozytorium wiedzy o materiałach w platformie Industrial Cost Intelligence. Moduł odpowiada za:

### 1.1 Zarządzanie katalogiem materiałów
- Utrzymanie master data wszystkich materiałów używanych w produkcji, obróbce, montażu, pakowaniu i wykańczaniu powierzchni
- Wersjonowanie danych materiałowych (historia zmian parametrów)
- Zarządzanie statusami materiałów (aktywny, wycofany, zastąpiony, w przygotowaniu)
- Utrzymanie hierarchii taksonomicznej materiałów (klasa → podklasa → gatunek → odmiana)

### 1.2 Atrybuty techniczne i fizyczne
- Gęstość, masa właściwa, przewodność cieplna, elektryczna
- Parametry mechaniczne (Rm, Re, A%, twardość HB/HRC/HV)
- Parametry technologiczne (spawalność, obrabialność, formowalność)
- Normy i certyfikaty (ISO, DIN, EN, ASTM, PN)
- Klasy materiałowe wg systemów ERP (SAP MM, Oracle SCM)

### 1.3 Model kosztowy materiałów
- Baza cen zakupowych (historycznych i bieżących)
- Współczynniki naddatku technologicznego (odpady, naddatki na obróbkę)
- Koszty magazynowania, transportu i obsługi
- Koszty certyfikacji i kontroli jakości

### 1.4 Warstwy cen rynkowych
- Integracja z publicznymi indeksami cen (LME dla metali, indeksy plastiku, indeksy papieru/tektury)
- Śledzenie trendów cenowych i prognozowanie
- Korelacja cen surowców z cenami zakupowymi

### 1.5 Logika zamienników
- Baza substytutów materiałowych (techniczne i kosztowe)
- Reguły kompatybilności materiał–proces
- Scoring zamienników (podobieństwo techniczne, dostępność, koszt)

### 1.6 Mapowanie na dostawców
- Powiązanie materiałów z dostawcami i ich ofertami
- Lead time, MOQ, tolerancje cenowe per dostawca
- Ocena ryzyka dostaw (single source, geopolityka)

### 1.7 Obsługa AI i wyszukiwania
- Generowanie embeddingów wektorowych dla wyszukiwania semantycznego
- Przygotowanie danych dla modeli ML (predykcja cen, wykrywanie anomalii)
- API do pobierania kontekstu materiałowego przez agenty AI

### 1.8 Integracja systemowa
- Publikowanie zdarzeń domenowych (Kafka) na potrzeby ERP, RFQ, Supplier Intelligence
- REST API + OpenAPI dla integracji zewnętrznych
- Webhooks dla zmian cen i dostępności

---

## 2. Granice odpowiedzialności

### 2.1 Co NALEŻY do modułu MIE

| Obszar | Opis |
|--------|------|
| Material Master Data | Definicja, atrybuty, normy, statusy |
| Material Taxonomy | Hierarchia klasyfikacyjna |
| Physical Properties | Gęstość, właściwości mechaniczne, termiczne |
| Density Library | Referencyjne wartości gęstości per gatunek |
| Material Standards | Powiązania z normami ISO/DIN/EN/ASTM |
| Substitution Rules | Logika i scoring zamienników |
| Process Compatibility | Macierz materiał–proces produkcyjny |
| Cost Coefficients | Współczynniki naddatków, odpadów, obsługi |
| Market Price Layer | Ceny rynkowe, indeksy, trendy |
| Supplier–Material Mapping | Powiązanie materiał ↔ dostawca |
| Embeddings & Vector Index | Reprezentacja wektorowa do AI |
| Material Events | Zdarzenia domenowe Kafka |
| Material Search | Wyszukiwanie pełnotekstowe i filtrowanie |
| Material Validation | Reguły walidacji danych materiałowych |

### 2.2 Co NIE NALEŻY do modułu MIE

| Obszar | Właściwy moduł |
|--------|----------------|
| Kalkulacja kosztu wyrobu | Cost Calculation Engine |
| Zarządzanie BOM (Bill of Materials) | BOM Management Module |
| Tworzenie ofert RFQ | RFQ Engine |
| Ocena i scoring dostawców | Supplier Intelligence Engine |
| Planowanie zapotrzebowania materiałowego | MRP/Planning Engine |
| Zarządzanie stanami magazynowymi | Inventory Management |
| Zlecenia zakupu | Procurement Engine |
| Procesy produkcyjne (routing) | Process & Routing Engine |
| Certyfikaty jakości konkretnych partii | Quality Management Module |
| Prognozy sprzedaży | Demand Planning Module |
| Wycena projektów | Project Estimation Module |
