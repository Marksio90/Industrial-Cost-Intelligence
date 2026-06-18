# Manufacturing Process Engine — Domain Model

## 1. Domain Model

### Kontekst domenowy

Manufacturing Process Engine (MPE) modeluje wszystkie procesy produkcyjne używane do wytwarzania części i wyrobów gotowych. Jest źródłem prawdy o parametrach procesów, zasobach, kosztach i ograniczeniach technologicznych dla całej platformy Industrial Cost Intelligence.

### Mapa kontekstów (Context Map)

```
┌───────────────────────────────────────────────────────────────────────────┐
│                   MANUFACTURING PROCESS ENGINE                             │
│                                                                             │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────┐                │
│  │  Process    │   │  Resource    │   │  Cost           │                │
│  │  Catalogue  │──▶│  Model       │──▶│  Engine         │                │
│  │  (Taxonomy) │   │  (Machine/   │   │  (Setup+Runtime │                │
│  └─────────────┘   │   Tool/Op.)  │   │   +Energy+Scrap)│                │
│         │          └──────────────┘   └─────────────────┘                │
│         │                 │                    │                           │
│         ▼                 ▼                    ▼                           │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────┐                │
│  │  Process    │   │  OEE Engine  │   │  Capacity &     │                │
│  │  Parameters │   │  (Avail./    │   │  Scheduling     │                │
│  │  & Rules    │   │   Perf./Qual)│   │  Inputs         │                │
│  └─────────────┘   └──────────────┘   └─────────────────┘                │
│         │                 │                    │                           │
│         └─────────────────┴────────────────────┘                          │
│                                   │                                        │
│                          ┌────────▼────────┐                               │
│                          │   AI / ML Layer │                               │
│                          │  (Predictions,  │                               │
│                          │   Embeddings)   │                               │
│                          └─────────────────┘                               │
└───────────────────────────────────────────────────────────────────────────┘

External consumers:
  ← Material Intelligence Engine   (material-process compatibility)
  → Cost Calculation Engine         (process cost inputs)
  → BOM / Routing Engine            (operation sequences)
  → Scheduling Engine               (capacity, machine availability)
  → ERP (SAP PP / Oracle MFG)       (work centers, routings)
  → MES                             (real-time OEE, execution data)
  → RFQ Engine                      (cost estimation)
```

### Agregaty domenowe

| Agregat | Korzeń (Aggregate Root) | Encje wewnętrzne |
|---------|------------------------|-----------------|
| ProcessAggregate | ManufacturingProcess | ProcessParameter, ProcessConstraint, ProcessStandard |
| ResourceAggregate | ProductionResource | ResourceShift, ResourceCapability |
| MachineAggregate | Machine | MachineParameter, MaintenancePlan, OEERecord |
| ToolingAggregate | Tool | ToolLife, ToolChangeRecord |
| OperatorAggregate | Operator | OperatorCertification, SkillMatrix |
| CostAggregate | ProcessCostModel | SetupCost, RuntimeCost, EnergyCost, MaintenanceCost, ScrapCost |
| OEEAggregate | OEEPeriod | AvailabilityRecord, PerformanceRecord, QualityRecord |
| CapacityAggregate | CapacityPlan | CapacitySlot, CapacityConstraint |

### Zdarzenia domenowe

| Zdarzenie | Wyzwalacz | Konsumenci |
|-----------|-----------|-----------|
| ProcessCreated | Nowy proces | Search Index, ERP Sync |
| ProcessUpdated | Zmiana parametrów | Cost Calc, ERP Sync, Search Index |
| ProcessDeactivated | Wycofanie | RFQ Engine, Cost Calc |
| MachineRegistered | Nowa maszyna | Capacity Engine, OEE Service |
| MachineDowntimeStarted | Awaria/plan. postój | Scheduler, Capacity Engine |
| MachineDowntimeEnded | Powrót do pracy | Scheduler |
| OEERecorded | Koniec zmiany | Monitoring, Cost Calc |
| ToolLifeExpired | Zużycie narzędzia | Maintenance, Procurement |
| SetupCompleted | Zakończenie nastawu | MES, Costing |
| OperationCompleted | Zakończenie operacji | MES, Quality, Costing |
| ScrapRecorded | Złom zarejestrowany | Quality, Cost Calc |
| CostModelUpdated | Zmiana kosztu | Cost Calc, RFQ Engine |
| CapacityConstraintAdded | Nowe ograniczenie | Scheduler |

---

### Rdzeń modelu — relacje między agregatami

```
ManufacturingProcess
    ├── belongs to ProcessCategory (taxonomy)
    ├── has N ProcessParameters
    ├── has N ProcessConstraints (material compatibility)
    ├── requires 1..N ProductionResource (machine or workcenter)
    ├── requires 0..N Tool
    ├── requires 1..N Operator (with required skills)
    ├── has 1 ProcessCostModel
    │       ├── SetupCostComponent
    │       ├── RuntimeCostComponent
    │       ├── EnergyCostComponent
    │       ├── MaintenanceCostComponent
    │       └── ScrapCostComponent
    └── produces 0..N ProcessOutput (finished/semi-finished)

Machine (subtype of ProductionResource)
    ├── belongs to MachineClass
    ├── has N MachineParameters (power, speed, work envelope)
    ├── has 1 MaintenancePlan
    ├── generates N OEERecord (per shift)
    └── uses N Tool (via ToolMount)

Operator
    ├── has N OperatorCertification
    ├── has 1 SkillMatrix (process → level)
    └── assigned to N ProductionResource (shift assignment)
```
