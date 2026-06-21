-- =============================================================================
-- SEED DATA
-- Tenant: 'tenant-demo'  |  All UUIDs are deterministic for repeatability.
-- Run after migrations 001-006.
-- =============================================================================

-- Set application context for audit log
SELECT set_config('app.current_user',  'seed-script@ici.internal', false);
SELECT set_config('app.request_id',    'seed-001', false);

BEGIN;

-- ---------------------------------------------------------------------------
-- SUPPLIERS (10 records)
-- ---------------------------------------------------------------------------
INSERT INTO ici.suppliers (id, tenant_id, code, name, country_code, city, address_line1, postal_code,
    contact_email, contact_phone, quality_score, delivery_score, price_score, financial_score, status, qualified_at)
VALUES
    ('11000000-0000-0000-0000-000000000001', 'tenant-demo', 'SUP-DE-001', 'Schulz Metallwerk GmbH',
     'DE', 'Stuttgart', 'Industriestraße 42', '70376',
     'kontakt@schulz-metallwerk.de', '+49-711-1234567',
     0.91, 0.88, 0.76, 0.85, 'QUALIFIED', '2023-03-15'),

    ('11000000-0000-0000-0000-000000000002', 'tenant-demo', 'SUP-PL-001', 'Metalex Polska Sp. z o.o.',
     'PL', 'Wrocław', 'ul. Robotnicza 12', '53-607',
     'office@metalex.pl', '+48-71-3456789',
     0.84, 0.91, 0.88, 0.72, 'QUALIFIED', '2023-05-20'),

    ('11000000-0000-0000-0000-000000000003', 'tenant-demo', 'SUP-CZ-001', 'Brno Plastics s.r.o.',
     'CZ', 'Brno', 'Hněvkovského 65', '617 00',
     'info@brnoplastics.cz', '+420-541-123456',
     0.78, 0.82, 0.93, 0.80, 'QUALIFIED', '2023-07-01'),

    ('11000000-0000-0000-0000-000000000004', 'tenant-demo', 'SUP-IT-001', 'Fonderie Nord S.p.A.',
     'IT', 'Brescia', 'Via Industriale 88', '25124',
     'vendite@fonderienord.it', '+39-030-9876543',
     0.93, 0.79, 0.71, 0.88, 'QUALIFIED', '2022-11-10'),

    ('11000000-0000-0000-0000-000000000005', 'tenant-demo', 'SUP-ES-001', 'Talleres Vascos S.A.',
     'ES', 'Bilbao', 'Polígono Industrial Arasur 22', '48004',
     'compras@talleresvascos.es', '+34-944-123456',
     0.86, 0.85, 0.80, 0.78, 'QUALIFIED', '2023-02-28'),

    ('11000000-0000-0000-0000-000000000006', 'tenant-demo', 'SUP-CN-001', 'Shenzhen Precision Co., Ltd.',
     'CN', 'Shenzhen', 'Longhua District, No. 100', '518131',
     'sales@szdprecision.com', '+86-755-12345678',
     0.72, 0.65, 0.97, 0.68, 'QUALIFIED', '2023-09-15'),

    ('11000000-0000-0000-0000-000000000007', 'tenant-demo', 'SUP-DE-002', 'Kunststoff Bayern AG',
     'DE', 'Augsburg', 'Fuggerstraße 15', '86150',
     'info@kbayern.de', '+49-821-9876543',
     0.89, 0.90, 0.74, 0.91, 'QUALIFIED', '2022-08-01'),

    ('11000000-0000-0000-0000-000000000008', 'tenant-demo', 'SUP-FR-001', 'Acier Dupont SARL',
     'FR', 'Lyon', '12 Rue de la Forge', '69001',
     'contact@acierdupont.fr', '+33-4-72345678',
     0.81, 0.83, 0.79, 0.76, 'QUALIFIED', '2023-04-10'),

    ('11000000-0000-0000-0000-000000000009', 'tenant-demo', 'SUP-RO-001', 'Metalurgia Ploiești S.A.',
     'RO', 'Ploiești', 'Strada Petrolului 5', '100370',
     'office@metalurgia-ploiesti.ro', '+40-244-123456',
     0.70, 0.74, 0.95, 0.61, 'PENDING', NULL),

    ('11000000-0000-0000-0000-000000000010', 'tenant-demo', 'SUP-TW-001', 'Taiwan CNC Masters Co.',
     'TW', 'Taichung', 'No. 258, Sec. 3, Zhongshan Rd', '40457',
     'sales@twcncmasters.tw', '+886-4-22334455',
     0.88, 0.77, 0.86, 0.82, 'QUALIFIED', '2023-06-30');

-- ---------------------------------------------------------------------------
-- MATERIALS (20 records)
-- ---------------------------------------------------------------------------
INSERT INTO ici.materials (id, tenant_id, material_number, name, description, material_class,
    base_unit, density_g_cm3, price_eur, currency, lead_time_days, min_order_qty, supplier_id, status)
VALUES
    -- Metals
    ('22000000-0000-0000-0000-000000000001', 'tenant-demo', 'MAT-ST-001',
     'Steel S235JR', 'Hot-rolled structural steel EN 10025-2', 'METAL',
     'KG', 7.85, 0.72, 'EUR', 7, 1000, '11000000-0000-0000-0000-000000000001', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000002', 'tenant-demo', 'MAT-ST-002',
     'Steel S355J2', 'High-strength structural steel EN 10025-2', 'METAL',
     'KG', 7.85, 0.95, 'EUR', 10, 500, '11000000-0000-0000-0000-000000000001', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000003', 'tenant-demo', 'MAT-AL-001',
     'Aluminium 6061-T6', 'Heat-treated aluminium alloy, extrusion grade', 'METAL',
     'KG', 2.70, 3.20, 'EUR', 14, 100, '11000000-0000-0000-0000-000000000002', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000004', 'tenant-demo', 'MAT-AL-002',
     'Aluminium 7075-T651', 'Aerospace-grade aluminium, high fatigue resistance', 'METAL',
     'KG', 2.81, 6.80, 'EUR', 21, 50, '11000000-0000-0000-0000-000000000004', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000005', 'tenant-demo', 'MAT-TI-001',
     'Titanium Grade 5 (Ti-6Al-4V)', 'Aerospace titanium alloy, ELI grade available', 'METAL',
     'KG', 4.43, 42.50, 'EUR', 45, 10, '11000000-0000-0000-0000-000000000004', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000006', 'tenant-demo', 'MAT-CU-001',
     'Copper C11000 (ETP)', 'Electrolytic tough pitch copper, coil', 'METAL',
     'KG', 8.96, 8.90, 'EUR', 14, 200, '11000000-0000-0000-0000-000000000008', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000007', 'tenant-demo', 'MAT-SS-001',
     'Stainless Steel 316L', 'Austenitic stainless, low carbon, sheet 2B', 'METAL',
     'KG', 8.00, 4.10, 'EUR', 14, 200, '11000000-0000-0000-0000-000000000001', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000008', 'tenant-demo', 'MAT-ST-003',
     'Tool Steel D2 (X153CrMoV12)', 'Cold work tool steel, annealed bar', 'METAL',
     'KG', 7.70, 5.60, 'EUR', 21, 50, '11000000-0000-0000-0000-000000000002', 'ACTIVE'),

    -- Plastics
    ('22000000-0000-0000-0000-000000000009', 'tenant-demo', 'MAT-PL-001',
     'POM-C Delrin', 'Acetal homopolymer rod/sheet, natural colour', 'PLASTIC',
     'KG', 1.42, 3.80, 'EUR', 7, 50, '11000000-0000-0000-0000-000000000003', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000010', 'tenant-demo', 'MAT-PL-002',
     'PEEK 450G', 'Polyether ether ketone, injection moulding grade', 'PLASTIC',
     'KG', 1.32, 88.00, 'EUR', 30, 5, '11000000-0000-0000-0000-000000000007', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000011', 'tenant-demo', 'MAT-PL-003',
     'PA66 GF30', 'Glass-fibre reinforced polyamide 6.6, 30%', 'PLASTIC',
     'KG', 1.38, 4.20, 'EUR', 10, 100, '11000000-0000-0000-0000-000000000007', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000012', 'tenant-demo', 'MAT-PL-004',
     'ABS HH-112', 'High-heat ABS, UL94 V-0 rated', 'PLASTIC',
     'KG', 1.06, 2.90, 'EUR', 7, 200, '11000000-0000-0000-0000-000000000003', 'ACTIVE'),

    -- Composites
    ('22000000-0000-0000-0000-000000000013', 'tenant-demo', 'MAT-CF-001',
     'Carbon Fibre Prepreg T700', 'Unidirectional CF/epoxy, 125°C cure', 'COMPOSITE',
     'M2', 1.60, 95.00, 'EUR', 35, 5, '11000000-0000-0000-0000-000000000005', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000014', 'tenant-demo', 'MAT-GF-001',
     'Glass Fibre E-glass Woven', 'Balanced woven roving 600 g/m²', 'COMPOSITE',
     'M2', 2.54, 12.50, 'EUR', 14, 20, '11000000-0000-0000-0000-000000000005', 'ACTIVE'),

    -- Electronics
    ('22000000-0000-0000-0000-000000000015', 'tenant-demo', 'MAT-EL-001',
     'FR4 PCB Laminate 1.6mm', 'Glass-epoxy laminate, TG 150°C, UL94 V-0', 'ELECTRONIC',
     'M2', 1.85, 18.00, 'EUR', 21, 10, '11000000-0000-0000-0000-000000000010', 'ACTIVE'),

    ('22000000-0000-0000-0000-000000000016', 'tenant-demo', 'MAT-EL-002',
     'Copper Clad Laminate 35µm', 'Double-sided, 35µm Cu, FR4 base', 'ELECTRONIC',
     'M2', 2.05, 22.00, 'EUR', 21, 10, '11000000-0000-0000-0000-000000000010', 'ACTIVE'),

    -- Rubber
    ('22000000-0000-0000-0000-000000000017', 'tenant-demo', 'MAT-RU-001',
     'NBR 70 Shore A', 'Nitrile rubber compound, oil resistant', 'RUBBER',
     'KG', 1.20, 6.50, 'EUR', 14, 25, '11000000-0000-0000-0000-000000000009', 'ACTIVE'),

    -- Chemicals
    ('22000000-0000-0000-0000-000000000018', 'tenant-demo', 'MAT-CH-001',
     'Epoxy Resin LY1564', 'Bisphenol-A liquid epoxy, low viscosity', 'CHEMICAL',
     'KG', 1.16, 7.20, 'EUR', 10, 50, '11000000-0000-0000-0000-000000000009', 'ACTIVE'),

    -- Deprecated
    ('22000000-0000-0000-0000-000000000019', 'tenant-demo', 'MAT-ST-DEPR',
     'Steel S185 (legacy)', 'Superseded by S235JR — do not order', 'METAL',
     'KG', 7.85, 0.60, 'EUR', 0, 5000, NULL, 'DEPRECATED'),

    -- Prototype
    ('22000000-0000-0000-0000-000000000020', 'tenant-demo', 'MAT-CF-PROTO',
     'Carbon Fibre Thermoplastic PEEK', 'CF/PEEK tape, APC-2 grade — qualification ongoing', 'COMPOSITE',
     'KG', 1.56, 380.00, 'EUR', 60, 2, '11000000-0000-0000-0000-000000000005', 'PROTOTYPE');

-- ---------------------------------------------------------------------------
-- PROCESSES (10 records)
-- ---------------------------------------------------------------------------
INSERT INTO ici.processes (id, tenant_id, code, name, process_type, description,
    machine_rate_eur_hr, labor_rate_eur_hr, cycle_time_seconds, setup_time_seconds, scrap_rate)
VALUES
    ('33000000-0000-0000-0000-000000000001', 'tenant-demo', 'PROC-CNC-001',
     '5-Axis CNC Milling', 'MACHINING', 'DMG MORI DMU 50, 5-axis simultaneous machining',
     95.00, 28.00, 180.0, 1800.0, 0.02),

    ('33000000-0000-0000-0000-000000000002', 'tenant-demo', 'PROC-CNC-002',
     'CNC Turning', 'MACHINING', 'Mazak Quick Turn 250 turning centre',
     55.00, 24.00, 90.0, 900.0, 0.01),

    ('33000000-0000-0000-0000-000000000003', 'tenant-demo', 'PROC-CAST-001',
     'Aluminium HPDC', 'CASTING', 'High-pressure die casting, 400T press, 2K shots/hr',
     120.00, 32.00, 45.0, 3600.0, 0.04),

    ('33000000-0000-0000-0000-000000000004', 'tenant-demo', 'PROC-STAMP-001',
     'Progressive Die Stamping', 'STAMPING', '200T stamping press, 300 strokes/min',
     80.00, 22.00, 0.2, 7200.0, 0.015),

    ('33000000-0000-0000-0000-000000000005', 'tenant-demo', 'PROC-INJ-001',
     'Injection Moulding', 'INJECTION_MOLDING', '280T injection moulding machine',
     65.00, 20.00, 30.0, 2400.0, 0.025),

    ('33000000-0000-0000-0000-000000000006', 'tenant-demo', 'PROC-WELD-001',
     'MIG/MAG Welding', 'WELDING', 'Robotic MIG welding cell, Fanuc R2000iC',
     75.00, 30.00, 120.0, 900.0, 0.005),

    ('33000000-0000-0000-0000-000000000007', 'tenant-demo', 'PROC-FORGE-001',
     'Drop Forging', 'FORGING', 'Hot die forging, 1000T hammer, steel blanks',
     140.00, 35.00, 15.0, 3600.0, 0.03),

    ('33000000-0000-0000-0000-000000000008', 'tenant-demo', 'PROC-SURF-001',
     'Hard Anodising', 'SURFACE_TREATMENT', 'Type III hard anodise 25µm, aluminium only',
     45.00, 18.00, 3600.0, 1200.0, 0.01),

    ('33000000-0000-0000-0000-000000000009', 'tenant-demo', 'PROC-ASSY-001',
     'Manual Assembly Line A', 'ASSEMBLY', 'Torque-controlled fastener assembly, 8 stations',
     25.00, 22.00, 600.0, 300.0, 0.005),

    ('33000000-0000-0000-0000-000000000010', 'tenant-demo', 'PROC-EDM-001',
     'Wire EDM', 'MACHINING', 'Sodick AQ750L wire EDM, 0.25mm wire',
     85.00, 20.00, 600.0, 1800.0, 0.005);

-- ---------------------------------------------------------------------------
-- COST SNAPSHOTS (12 records — monthly snapshots for 3 key materials)
-- ---------------------------------------------------------------------------
INSERT INTO ici.cost_snapshots (id, tenant_id, material_id, snapshot_date,
    material_cost_eur, process_cost_eur, overhead_cost_eur, logistics_cost_eur, tooling_cost_eur,
    reference_quantity, source, model_version, notes)
VALUES
    -- Steel S235JR — quarterly snapshots
    ('44000000-0000-0000-0001-202401010000', 'tenant-demo', '22000000-0000-0000-0000-000000000001',
     '2024-01-01', 720.00, 180.00, 90.00, 45.00, 30.00, 1000, 'ML_MODEL', '2.1', 'Q1 2024 estimate'),
    ('44000000-0000-0000-0001-202404010000', 'tenant-demo', '22000000-0000-0000-0000-000000000001',
     '2024-04-01', 740.00, 182.00, 92.00, 48.00, 30.00, 1000, 'ML_MODEL', '2.1', 'Q2 2024 estimate'),
    ('44000000-0000-0000-0001-202407010000', 'tenant-demo', '22000000-0000-0000-0000-000000000001',
     '2024-07-01', 755.00, 185.00, 93.00, 47.00, 32.00, 1000, 'ML_MODEL', '2.2', 'Q3 2024 estimate'),
    ('44000000-0000-0000-0001-202410010000', 'tenant-demo', '22000000-0000-0000-0000-000000000001',
     '2024-10-01', 730.00, 183.00, 91.00, 46.00, 31.00, 1000, 'MANUAL', NULL,  'Q4 2024 actual'),

    -- Aluminium 6061-T6
    ('44000000-0000-0000-0003-202401010000', 'tenant-demo', '22000000-0000-0000-0000-000000000003',
     '2024-01-01', 320.00, 95.00, 48.00, 28.00, 15.00, 100, 'ML_MODEL', '2.1', 'Q1 2024 estimate'),
    ('44000000-0000-0000-0003-202404010000', 'tenant-demo', '22000000-0000-0000-0000-000000000003',
     '2024-04-01', 335.00, 98.00, 50.00, 29.00, 15.00, 100, 'ML_MODEL', '2.1', 'Q2 2024 estimate'),
    ('44000000-0000-0000-0003-202407010000', 'tenant-demo', '22000000-0000-0000-0000-000000000003',
     '2024-07-01', 342.00, 99.00, 51.00, 30.00, 16.00, 100, 'ML_MODEL', '2.2', 'Q3 2024 estimate'),
    ('44000000-0000-0000-0003-202410010000', 'tenant-demo', '22000000-0000-0000-0000-000000000003',
     '2024-10-01', 328.00, 97.00, 49.00, 29.00, 15.00, 100, 'MANUAL', NULL,  'Q4 2024 actual'),

    -- PEEK 450G
    ('44000000-0000-0000-000a-202401010000', 'tenant-demo', '22000000-0000-0000-0000-000000000010',
     '2024-01-01', 8800.00, 650.00, 320.00, 120.00, 80.00, 100, 'ML_MODEL', '2.1', 'Q1 2024 estimate'),
    ('44000000-0000-0000-000a-202404010000', 'tenant-demo', '22000000-0000-0000-0000-000000000010',
     '2024-04-01', 8650.00, 645.00, 318.00, 118.00, 80.00, 100, 'ML_MODEL', '2.1', 'Q2 2024 estimate'),
    ('44000000-0000-0000-000a-202407010000', 'tenant-demo', '22000000-0000-0000-0000-000000000010',
     '2024-07-01', 8900.00, 660.00, 325.00, 122.00, 82.00, 100, 'ML_MODEL', '2.2', 'Q3 2024 estimate'),
    ('44000000-0000-0000-000a-202410010000', 'tenant-demo', '22000000-0000-0000-0000-000000000010',
     '2024-10-01', 8750.00, 652.00, 321.00, 119.00, 81.00, 100, 'MANUAL', NULL,  'Q4 2024 actual');

-- ---------------------------------------------------------------------------
-- RFQs (3 records)
-- ---------------------------------------------------------------------------
INSERT INTO ici.rfqs (id, tenant_id, rfq_number, title, description, status,
    deadline, delivery_date, delivery_terms, payment_terms, currency, created_at)
VALUES
    ('55000000-0000-0000-0000-000000000001', 'tenant-demo', 'RFQ-2026-001',
     'Aluminium Housing Batch Q2 2026',
     'CNC machined aluminium 6061-T6 housings for servo motor controllers. Qty 500 pcs.',
     'SENT', '2026-07-15', '2026-09-01', 'DAP', 'NET30', 'EUR', '2026-06-01 08:00:00+00'),

    ('55000000-0000-0000-0000-000000000002', 'tenant-demo', 'RFQ-2026-002',
     'Steel Structural Brackets — Annual Frame Agreement',
     'S355J2 laser-cut and bent structural brackets. Annual volume ~50,000 pcs.',
     'DRAFT', '2026-08-01', '2026-10-01', 'EXW', 'NET60', 'EUR', '2026-06-15 09:00:00+00'),

    ('55000000-0000-0000-0000-000000000003', 'tenant-demo', 'RFQ-2025-045',
     'PEEK Injection Moulded Bushings',
     'PA66-GF30 alternative evaluation — PEEK 450G bushings for high-temp application.',
     'AWARDED', '2025-11-30', '2026-02-01', 'DAP', 'NET30', 'EUR', '2025-10-15 10:00:00+00');

-- RFQ Line Items
INSERT INTO ici.rfq_line_items (id, rfq_id, rfq_created_at, material_id, description, quantity, unit_of_measure, target_price_eur)
VALUES
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000001', '2026-06-01 08:00:00+00',
     '22000000-0000-0000-0000-000000000003', 'Housing body AL6061-T6, CNC machined', 500, 'PCS', 28.50),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000001', '2026-06-01 08:00:00+00',
     '22000000-0000-0000-0000-000000000003', 'Cover plate AL6061-T6', 500, 'PCS', 12.00),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000002', '2026-06-15 09:00:00+00',
     '22000000-0000-0000-0000-000000000002', 'Bracket type A, S355J2, 3mm sheet', 20000, 'PCS', 3.80),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000002', '2026-06-15 09:00:00+00',
     '22000000-0000-0000-0000-000000000002', 'Bracket type B, S355J2, 5mm sheet', 30000, 'PCS', 6.20),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000003', '2025-10-15 10:00:00+00',
     '22000000-0000-0000-0000-000000000010', 'PEEK 450G Bushing Ø32×20mm', 2000, 'PCS', 9.50);

-- RFQ Suppliers
INSERT INTO ici.rfq_suppliers (id, rfq_id, rfq_created_at, supplier_id, invited_at, responded_at)
VALUES
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000001', '2026-06-01 08:00:00+00',
     '11000000-0000-0000-0000-000000000002', '2026-06-01 09:00:00+00', '2026-06-20 14:30:00+00'),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000001', '2026-06-01 08:00:00+00',
     '11000000-0000-0000-0000-000000000010', '2026-06-01 09:00:00+00', '2026-06-18 11:00:00+00'),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000002', '2026-06-15 09:00:00+00',
     '11000000-0000-0000-0000-000000000001', '2026-06-15 10:00:00+00', NULL),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000002', '2026-06-15 09:00:00+00',
     '11000000-0000-0000-0000-000000000008', '2026-06-15 10:00:00+00', NULL),
    (gen_random_uuid(), '55000000-0000-0000-0000-000000000003', '2025-10-15 10:00:00+00',
     '11000000-0000-0000-0000-000000000007', '2025-10-15 11:00:00+00', '2025-11-05 09:00:00+00');

-- ---------------------------------------------------------------------------
-- QUOTES (3 records)
-- ---------------------------------------------------------------------------
INSERT INTO ici.quotes (id, tenant_id, rfq_id, supplier_id, quote_number, validity_date, status,
    currency, delivery_terms, payment_terms, created_at)
VALUES
    ('66000000-0000-0000-0000-000000000001', 'tenant-demo',
     '55000000-0000-0000-0000-000000000001', '11000000-0000-0000-0000-000000000002',
     'Q-METALEX-2026-0412', '2026-08-15', 'RECEIVED', 'EUR', 'DAP', 'NET30',
     '2026-06-20 15:00:00+00'),

    ('66000000-0000-0000-0000-000000000002', 'tenant-demo',
     '55000000-0000-0000-0000-000000000001', '11000000-0000-0000-0000-000000000010',
     'TW-CNC-Q26-0781', '2026-08-20', 'UNDER_REVIEW', 'EUR', 'CIF', 'NET60',
     '2026-06-18 12:00:00+00'),

    ('66000000-0000-0000-0000-000000000003', 'tenant-demo',
     '55000000-0000-0000-0000-000000000003', '11000000-0000-0000-0000-000000000007',
     'KB-2025-4512', '2026-01-31', 'ACCEPTED', 'EUR', 'DAP', 'NET30',
     '2025-11-05 10:00:00+00');

-- Quote Line Items
INSERT INTO ici.quote_line_items (id, quote_id, quote_created_at, material_id, description,
    quantity, unit_of_measure, unit_price_eur, lead_time_days)
VALUES
    (gen_random_uuid(), '66000000-0000-0000-0000-000000000001', '2026-06-20 15:00:00+00',
     '22000000-0000-0000-0000-000000000003', 'Housing body AL6061-T6', 500, 'PCS', 26.80, 45),
    (gen_random_uuid(), '66000000-0000-0000-0000-000000000001', '2026-06-20 15:00:00+00',
     '22000000-0000-0000-0000-000000000003', 'Cover plate AL6061-T6', 500, 'PCS', 11.20, 45),
    (gen_random_uuid(), '66000000-0000-0000-0000-000000000002', '2026-06-18 12:00:00+00',
     '22000000-0000-0000-0000-000000000003', 'Housing body AL6061-T6', 500, 'PCS', 22.50, 60),
    (gen_random_uuid(), '66000000-0000-0000-0000-000000000002', '2026-06-18 12:00:00+00',
     '22000000-0000-0000-0000-000000000003', 'Cover plate AL6061-T6', 500, 'PCS', 9.80, 60),
    (gen_random_uuid(), '66000000-0000-0000-0000-000000000003', '2025-11-05 10:00:00+00',
     '22000000-0000-0000-0000-000000000010', 'PEEK 450G Bushing Ø32×20mm', 2000, 'PCS', 9.10, 35);

-- ---------------------------------------------------------------------------
-- RISK SCORES (5 records)
-- ---------------------------------------------------------------------------
INSERT INTO ici.risk_scores (id, tenant_id, category, title, description,
    probability, impact, detectability, estimated_cost_eur, cost_variance_pct, impact_area,
    affected_material_id, affected_supplier_id, status, created_at)
VALUES
    ('77000000-0000-0000-0000-000000000001', 'tenant-demo', 'SUPPLY',
     'Single-source PEEK dependency',
     'PEEK 450G sourced from single approved supplier (Kunststoff Bayern). Any disruption halts production.',
     0.35, 0.90, 0.20, 180000.00, 40.0, 'COST',
     '22000000-0000-0000-0000-000000000010', '11000000-0000-0000-0000-000000000007',
     'OPEN', '2026-01-15 08:00:00+00'),

    ('77000000-0000-0000-0000-000000000002', 'tenant-demo', 'PRICE',
     'Aluminium price volatility LME',
     'LME aluminium spot price increased 18% YTD. Affects 7 BOM positions.',
     0.70, 0.60, 0.70, 95000.00, 25.0, 'COST',
     '22000000-0000-0000-0000-000000000003', NULL,
     'ACKNOWLEDGED', '2026-02-01 09:00:00+00'),

    ('77000000-0000-0000-0000-000000000003', 'tenant-demo', 'GEOPOLITICAL',
     'Taiwan supply chain exposure',
     'PCB substrate and CNC subcontractor in Taiwan. Cross-strait risk assessment required.',
     0.15, 0.95, 0.30, 500000.00, 80.0, 'DELIVERY',
     '22000000-0000-0000-0000-000000000015', '11000000-0000-0000-0000-000000000010',
     'MITIGATING', '2026-03-10 10:00:00+00'),

    ('77000000-0000-0000-0000-000000000004', 'tenant-demo', 'QUALITY',
     'Shenzhen Precision scrap rate trend',
     'Q1 2026 outgoing quality reports show 3.2% scrap vs 1.5% contract SLA.',
     0.60, 0.50, 0.85, 28000.00, 15.0, 'QUALITY',
     NULL, '11000000-0000-0000-0000-000000000006',
     'OPEN', '2026-04-05 11:00:00+00'),

    ('77000000-0000-0000-0000-000000000005', 'tenant-demo', 'FINANCIAL',
     'Metalurgia Ploiești credit risk',
     'Supplier credit rating downgraded to B-. Payment terms NET60 outstanding €240k.',
     0.25, 0.70, 0.60, 240000.00, 100.0, 'FINANCIAL',
     NULL, '11000000-0000-0000-0000-000000000009',
     'ACKNOWLEDGED', '2026-05-20 14:00:00+00');

-- Mitigation Actions
INSERT INTO ici.mitigation_actions (id, risk_id, risk_created_at, description, owner, due_date)
VALUES
    (gen_random_uuid(), '77000000-0000-0000-0000-000000000001', '2026-01-15 08:00:00+00',
     'Qualify alternative PEEK supplier (Solvay KetaSpire)', 'procurement@ici.internal',
     '2026-09-30 00:00:00+00'),
    (gen_random_uuid(), '77000000-0000-0000-0000-000000000001', '2026-01-15 08:00:00+00',
     'Increase safety stock to 6-month cover', 'supply-chain@ici.internal',
     '2026-08-01 00:00:00+00'),
    (gen_random_uuid(), '77000000-0000-0000-0000-000000000003', '2026-03-10 10:00:00+00',
     'Identify EU-based PCB alternative supplier', 'engineering@ici.internal',
     '2026-10-01 00:00:00+00'),
    (gen_random_uuid(), '77000000-0000-0000-0000-000000000004', '2026-04-05 11:00:00+00',
     'Conduct on-site quality audit at Shenzhen Precision', 'quality@ici.internal',
     '2026-07-31 00:00:00+00');

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification queries
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_sup  INTEGER; v_mat INTEGER; v_proc INTEGER;
    v_rfq  INTEGER; v_quo  INTEGER; v_risk INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_sup  FROM ici.suppliers    WHERE tenant_id = 'tenant-demo';
    SELECT COUNT(*) INTO v_mat  FROM ici.materials    WHERE tenant_id = 'tenant-demo';
    SELECT COUNT(*) INTO v_proc FROM ici.processes    WHERE tenant_id = 'tenant-demo';
    SELECT COUNT(*) INTO v_rfq  FROM ici.rfqs         WHERE tenant_id = 'tenant-demo';
    SELECT COUNT(*) INTO v_quo  FROM ici.quotes       WHERE tenant_id = 'tenant-demo';
    SELECT COUNT(*) INTO v_risk FROM ici.risk_scores  WHERE tenant_id = 'tenant-demo';
    RAISE NOTICE 'Seed complete: suppliers=% materials=% processes=% rfqs=% quotes=% risks=%',
        v_sup, v_mat, v_proc, v_rfq, v_quo, v_risk;
END;
$$;
