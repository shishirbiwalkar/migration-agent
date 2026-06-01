-- ============================================================
-- ABASE Seed Data v2 — 20 scientists, 80 wells (4 per scientist)
-- Run this in your ABASE Supabase project AFTER schema_abase.sql
-- OR use reset_demo.py which runs this automatically
-- ============================================================

-- Clear existing data first
TRUNCATE experiments RESTART IDENTITY CASCADE;
TRUNCATE users RESTART IDENTITY CASCADE;

-- ── 20 Scientists ──────────────────────────────────────────────────────────

INSERT INTO users (name, department, email, last_login, active_time_minutes) VALUES
('Smith_J',     'Biochemistry',      'smith.j@lab.internal',      '2024-01-20 09:15:00+00', 1847),
('Chen_L',      'Molecular Biology', 'chen.l@lab.internal',       '2024-01-19 14:30:00+00', 2103),
('Patel_R',     'Pharmacology',      'patel.r@lab.internal',      '2024-01-18 11:00:00+00', 1562),
('Williams_K',  'Immunology',        'williams.k@lab.internal',   '2024-01-17 16:45:00+00',  987),
('Rodriguez_M', 'Genomics',          'rodriguez.m@lab.internal',  '2024-01-21 08:00:00+00', 3241),
('Kim_S',       'Proteomics',        'kim.s@lab.internal',        '2024-01-15 13:20:00+00',  445),
('Mueller_T',   'Toxicology',        'mueller.t@lab.internal',    '2024-01-16 10:30:00+00', 1123),
('Okonkwo_A',   'Cell Biology',      'okonkwo.a@lab.internal',    '2024-01-20 15:00:00+00', 2876),
('Tanaka_Y',    'Biochemistry',      'tanaka.y@lab.internal',     '2024-01-22 09:00:00+00', 1654),
('Gupta_P',     'Molecular Biology', 'gupta.p@lab.internal',      '2024-01-21 11:30:00+00', 2234),
('Johnson_B',   'Pharmacology',      'johnson.b@lab.internal',    '2024-01-20 14:00:00+00', 1876),
('Lee_H',       'Immunology',        'lee.h@lab.internal',        '2024-01-19 10:15:00+00', 3102),
('Fernandez_C', 'Genomics',          'fernandez.c@lab.internal',  '2024-01-18 08:45:00+00', 1234),
('Nakamura_S',  'Proteomics',        'nakamura.s@lab.internal',   '2024-01-17 13:00:00+00',  876),
('Brown_E',     'Toxicology',        'brown.e@lab.internal',      '2024-01-16 15:30:00+00', 2341),
('Adebayo_F',   'Cell Biology',      'adebayo.f@lab.internal',    '2024-01-15 09:45:00+00', 1567),
('Martinez_L',  'Biochemistry',      'martinez.l@lab.internal',   '2024-01-22 11:00:00+00', 2890),
('Singh_A',     'Molecular Biology', 'singh.a@lab.internal',      '2024-01-21 14:30:00+00', 1432),
('Thompson_R',  'Pharmacology',      'thompson.r@lab.internal',   '2024-01-20 08:30:00+00', 2156),
('Walsh_D',     'Immunology',        'walsh.d@lab.internal',      '2024-01-19 16:00:00+00', 1789)
ON CONFLICT (name) DO NOTHING;

-- ── 80 Wells — 4 per scientist, 2 scientists per plate ────────────────────
-- Normal signal range: ~6–15
-- Intentional outliers marked with ← OUTLIER (triggers agent review flag)

INSERT INTO experiments (user_id, plate_barcode, well_position, raw_value, recorded_at,
                         compound_id, concentration_um, assay_type)
SELECT u.id, e.plate_barcode, e.well_position, e.raw_value, e.recorded_at,
       -- Deterministic screening metadata derived from existing fields
       'CMP-' || upper(substr(md5(e.plate_barcode || e.well_position), 1, 6))  AS compound_id,
       CASE
           WHEN e.plate_barcode IN ('PLT-2001','PLT-2002','PLT-2003','PLT-2004') THEN 10.0
           WHEN e.plate_barcode IN ('PLT-2005','PLT-2006','PLT-2007')            THEN  1.0
           ELSE 30.0
       END                                                                       AS concentration_um,
       CASE u.department
           WHEN 'Biochemistry'      THEN 'Biochemical'
           WHEN 'Molecular Biology' THEN 'Functional'
           WHEN 'Pharmacology'      THEN 'Binding'
           WHEN 'Immunology'        THEN 'Cell-based'
           WHEN 'Genomics'          THEN 'Reporter Gene'
           WHEN 'Proteomics'        THEN 'Biochemical'
           WHEN 'Toxicology'        THEN 'Cytotoxicity'
           WHEN 'Cell Biology'      THEN 'Cell-based'
           ELSE 'Biochemical'
       END                                                                       AS assay_type
FROM (VALUES
    -- PLT-2001: Smith_J + Chen_L
    ('Smith_J',     'PLT-2001', 'A01', 14.52, '2023-01-15 08:00:00+00'::timestamptz),
    ('Smith_J',     'PLT-2001', 'A02',  8.73, '2023-01-15 08:05:00+00'),
    ('Smith_J',     'PLT-2001', 'A03', 11.20, '2023-01-15 08:10:00+00'),
    ('Smith_J',     'PLT-2001', 'A04',  9.45, '2023-01-15 08:15:00+00'),
    ('Chen_L',      'PLT-2001', 'B01',  9.10, '2023-01-15 08:20:00+00'),
    ('Chen_L',      'PLT-2001', 'B02',  7.65, '2023-01-15 08:25:00+00'),
    ('Chen_L',      'PLT-2001', 'B03', 13.40, '2023-01-15 08:30:00+00'),
    ('Chen_L',      'PLT-2001', 'B04',  0.45, '2023-01-15 08:35:00+00'), -- OUTLIER: near-zero signal

    -- PLT-2002: Patel_R + Williams_K
    ('Patel_R',     'PLT-2002', 'A01', 11.78, '2023-02-10 09:00:00+00'),
    ('Patel_R',     'PLT-2002', 'A02',  6.34, '2023-02-10 09:05:00+00'),
    ('Patel_R',     'PLT-2002', 'A03',  9.56, '2023-02-10 09:10:00+00'),
    ('Patel_R',     'PLT-2002', 'A04',  8.90, '2023-02-10 09:15:00+00'),
    ('Williams_K',  'PLT-2002', 'B01', 12.45, '2023-02-10 09:20:00+00'),
    ('Williams_K',  'PLT-2002', 'B02',  7.23, '2023-02-10 09:25:00+00'),
    ('Williams_K',  'PLT-2002', 'B03', 14.67, '2023-02-10 09:30:00+00'),
    ('Williams_K',  'PLT-2002', 'B04', 21.30, '2023-02-10 09:35:00+00'), -- OUTLIER: spike

    -- PLT-2003: Rodriguez_M + Kim_S
    ('Rodriguez_M', 'PLT-2003', 'A01', 15.01, '2023-03-05 10:00:00+00'),
    ('Rodriguez_M', 'PLT-2003', 'A02', 11.23, '2023-03-05 10:05:00+00'),
    ('Rodriguez_M', 'PLT-2003', 'A03',  8.77, '2023-03-05 10:10:00+00'),
    ('Rodriguez_M', 'PLT-2003', 'A04',  9.45, '2023-03-05 10:15:00+00'),
    ('Kim_S',       'PLT-2003', 'B01',  7.65, '2023-03-05 10:20:00+00'),
    ('Kim_S',       'PLT-2003', 'B02',  8.56, '2023-03-05 10:25:00+00'),
    ('Kim_S',       'PLT-2003', 'B03',  6.34, '2023-03-05 10:30:00+00'),
    ('Kim_S',       'PLT-2003', 'B04',  9.12, '2023-03-05 10:35:00+00'),

    -- PLT-2004: Mueller_T + Okonkwo_A
    ('Mueller_T',   'PLT-2004', 'A01',  5.91, '2023-04-12 11:00:00+00'),
    ('Mueller_T',   'PLT-2004', 'A02',  1.23, '2023-04-12 11:05:00+00'), -- OUTLIER: very low
    ('Mueller_T',   'PLT-2004', 'A03', 10.44, '2023-04-12 11:10:00+00'),
    ('Mueller_T',   'PLT-2004', 'A04',  8.88, '2023-04-12 11:15:00+00'),
    ('Okonkwo_A',   'PLT-2004', 'B01', 13.44, '2023-04-12 11:20:00+00'),
    ('Okonkwo_A',   'PLT-2004', 'B02',  9.99, '2023-04-12 11:25:00+00'),
    ('Okonkwo_A',   'PLT-2004', 'B03',  5.32, '2023-04-12 11:30:00+00'),
    ('Okonkwo_A',   'PLT-2004', 'B04', 11.76, '2023-04-12 11:35:00+00'),

    -- PLT-2005: Tanaka_Y + Gupta_P
    ('Tanaka_Y',    'PLT-2005', 'A01', 10.23, '2023-05-08 08:00:00+00'),
    ('Tanaka_Y',    'PLT-2005', 'A02',  8.45, '2023-05-08 08:05:00+00'),
    ('Tanaka_Y',    'PLT-2005', 'A03', 12.67, '2023-05-08 08:10:00+00'),
    ('Tanaka_Y',    'PLT-2005', 'A04',  9.34, '2023-05-08 08:15:00+00'),
    ('Gupta_P',     'PLT-2005', 'B01',  7.89, '2023-05-08 08:20:00+00'),
    ('Gupta_P',     'PLT-2005', 'B02', 11.45, '2023-05-08 08:25:00+00'),
    ('Gupta_P',     'PLT-2005', 'B03',  0.23, '2023-05-08 08:30:00+00'), -- OUTLIER: near-zero
    ('Gupta_P',     'PLT-2005', 'B04',  8.90, '2023-05-08 08:35:00+00'),

    -- PLT-2006: Johnson_B + Lee_H
    ('Johnson_B',   'PLT-2006', 'A01', 13.56, '2023-06-14 09:00:00+00'),
    ('Johnson_B',   'PLT-2006', 'A02',  9.78, '2023-06-14 09:05:00+00'),
    ('Johnson_B',   'PLT-2006', 'A03', 11.23, '2023-06-14 09:10:00+00'),
    ('Johnson_B',   'PLT-2006', 'A04',  7.45, '2023-06-14 09:15:00+00'),
    ('Lee_H',       'PLT-2006', 'B01',  8.90, '2023-06-14 09:20:00+00'),
    ('Lee_H',       'PLT-2006', 'B02', 12.34, '2023-06-14 09:25:00+00'),
    ('Lee_H',       'PLT-2006', 'B03', 19.87, '2023-06-14 09:30:00+00'), -- OUTLIER: high spike
    ('Lee_H',       'PLT-2006', 'B04', 10.56, '2023-06-14 09:35:00+00'),

    -- PLT-2007: Fernandez_C + Nakamura_S
    ('Fernandez_C', 'PLT-2007', 'A01',  9.45, '2023-07-20 10:00:00+00'),
    ('Fernandez_C', 'PLT-2007', 'A02', 11.78, '2023-07-20 10:05:00+00'),
    ('Fernandez_C', 'PLT-2007', 'A03',  7.23, '2023-07-20 10:10:00+00'),
    ('Fernandez_C', 'PLT-2007', 'A04', 10.34, '2023-07-20 10:15:00+00'),
    ('Nakamura_S',  'PLT-2007', 'B01', 14.56, '2023-07-20 10:20:00+00'),
    ('Nakamura_S',  'PLT-2007', 'B02',  8.90, '2023-07-20 10:25:00+00'),
    ('Nakamura_S',  'PLT-2007', 'B03', 12.34, '2023-07-20 10:30:00+00'),
    ('Nakamura_S',  'PLT-2007', 'B04',  6.78, '2023-07-20 10:35:00+00'),

    -- PLT-2008: Brown_E + Adebayo_F
    ('Brown_E',     'PLT-2008', 'A01', 11.23, '2023-08-15 11:00:00+00'),
    ('Brown_E',     'PLT-2008', 'A02',  6.78, '2023-08-15 11:05:00+00'),
    ('Brown_E',     'PLT-2008', 'A03',  9.45, '2023-08-15 11:10:00+00'),
    ('Brown_E',     'PLT-2008', 'A04',  0.89, '2023-08-15 11:15:00+00'), -- OUTLIER: near-zero
    ('Adebayo_F',   'PLT-2008', 'B01', 10.34, '2023-08-15 11:20:00+00'),
    ('Adebayo_F',   'PLT-2008', 'B02', 13.56, '2023-08-15 11:25:00+00'),
    ('Adebayo_F',   'PLT-2008', 'B03',  8.90, '2023-08-15 11:30:00+00'),
    ('Adebayo_F',   'PLT-2008', 'B04', 11.23, '2023-08-15 11:35:00+00'),

    -- PLT-2009: Martinez_L + Singh_A
    ('Martinez_L',  'PLT-2009', 'A01',  8.45, '2023-09-22 08:00:00+00'),
    ('Martinez_L',  'PLT-2009', 'A02', 12.67, '2023-09-22 08:05:00+00'),
    ('Martinez_L',  'PLT-2009', 'A03', 10.23, '2023-09-22 08:10:00+00'),
    ('Martinez_L',  'PLT-2009', 'A04',  9.56, '2023-09-22 08:15:00+00'),
    ('Singh_A',     'PLT-2009', 'B01', 13.45, '2023-09-22 08:20:00+00'),
    ('Singh_A',     'PLT-2009', 'B02',  7.89, '2023-09-22 08:25:00+00'),
    ('Singh_A',     'PLT-2009', 'B03', 23.45, '2023-09-22 08:30:00+00'), -- OUTLIER: extreme spike
    ('Singh_A',     'PLT-2009', 'B04', 10.12, '2023-09-22 08:35:00+00'),

    -- PLT-2010: Thompson_R + Walsh_D
    ('Thompson_R',  'PLT-2010', 'A01',  9.78, '2023-10-18 09:00:00+00'),
    ('Thompson_R',  'PLT-2010', 'A02', 11.34, '2023-10-18 09:05:00+00'),
    ('Thompson_R',  'PLT-2010', 'A03',  8.90, '2023-10-18 09:10:00+00'),
    ('Thompson_R',  'PLT-2010', 'A04', 12.45, '2023-10-18 09:15:00+00'),
    ('Walsh_D',     'PLT-2010', 'B01',  7.23, '2023-10-18 09:20:00+00'),
    ('Walsh_D',     'PLT-2010', 'B02', 10.56, '2023-10-18 09:25:00+00'),
    ('Walsh_D',     'PLT-2010', 'B03',  9.34, '2023-10-18 09:30:00+00'),
    ('Walsh_D',     'PLT-2010', 'B04', 25.67, '2023-10-18 09:35:00+00')  -- OUTLIER: extreme spike

) AS e(scientist_name, plate_barcode, well_position, raw_value, recorded_at)
JOIN users u ON u.name = e.scientist_name
ON CONFLICT (plate_barcode, well_position) DO NOTHING;
