-- ============================================================
-- ABASE Seed Data v4 — 20 scientists, 240 wells
-- 3 plates. Each scientist occupies one row (A-H).
-- Columns 01-10: sample wells (10 log-spaced concentrations)
-- Column  11: negative control — DMSO (0% inhibition reference)
-- Column  12: positive control — reference inhibitor (100% inhibition)
--
-- raw_value = RAW FLUORESCENCE (RFU), NOT pre-normalized %
--   neg_ctrl DMSO:  ~88,000-92,000 RFU (high = no inhibition)
--   pos_ctrl ref:    ~4,600- 6,000 RFU (low  = full inhibition)
--   sample wells:   RFU = neg_ctrl - (neg_ctrl-pos_ctrl) * pct/100 + noise
--
-- The migration agent detects control wells, computes the normalization
-- window, normalizes samples to % inhibition, then fits the Hill curve.
--
-- Concentration series (columns 01-10 in uM):
--   0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0
-- ============================================================

TRUNCATE experiments RESTART IDENTITY CASCADE;
TRUNCATE users RESTART IDENTITY CASCADE;

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

INSERT INTO experiments (user_id, plate_barcode, well_position, raw_value, recorded_at,
                         compound_id, concentration_um, well_type, assay_type)
SELECT u.id, e.plate_barcode, e.well_position, e.raw_value, e.recorded_at,
       e.compound_id, e.concentration_um, e.well_type,
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
       END AS assay_type
FROM (VALUES

    -- ═══════════════════════════════════════════════════════════════
    -- PLT-001  (96 wells, fully filled)
    -- ═══════════════════════════════════════════════════════════════
    -- Row A: Smith_J  CMP-SJ01  EC50=1.5uM Hill=1.5 GOOD
    -- neg=90200 pos=4950  window=85250 RFU
    ('Smith_J','PLT-001','A01', 90150,'2024-01-15 08:00:00+00'::timestamptz,'CMP-SJ01',  0.001,'sample'),
    ('Smith_J','PLT-001','A02', 90140,'2024-01-15 08:01:00+00','CMP-SJ01',  0.003,'sample'),
    ('Smith_J','PLT-001','A03', 90080,'2024-01-15 08:02:00+00','CMP-SJ01',  0.010,'sample'),
    ('Smith_J','PLT-001','A04', 89890,'2024-01-15 08:03:00+00','CMP-SJ01',  0.030,'sample'),
    ('Smith_J','PLT-001','A05', 88720,'2024-01-15 08:04:00+00','CMP-SJ01',  0.100,'sample'),
    ('Smith_J','PLT-001','A06', 82380,'2024-01-15 08:05:00+00','CMP-SJ01',  0.300,'sample'),
    ('Smith_J','PLT-001','A07', 57490,'2024-01-15 08:06:00+00','CMP-SJ01',  1.000,'sample'),
    ('Smith_J','PLT-001','A08', 27040,'2024-01-15 08:07:00+00','CMP-SJ01',  3.000,'sample'),
    ('Smith_J','PLT-001','A09',  9820,'2024-01-15 08:08:00+00','CMP-SJ01', 10.000,'sample'),
    ('Smith_J','PLT-001','A10',  5450,'2024-01-15 08:09:00+00','CMP-SJ01', 30.000,'sample'),
    ('Smith_J','PLT-001','A11', 90050,'2024-01-15 08:10:00+00','CMP-SJ01',  NULL,'neg_ctrl'),
    ('Smith_J','PLT-001','A12',  4870,'2024-01-15 08:11:00+00','CMP-SJ01',  NULL,'pos_ctrl'),

    -- Row B: Chen_L  CMP-CL01  base EC50=2.0uM BAD (A08 and A10 are outlier wells)
    -- neg=89500 pos=5200  window=84300
    -- A08 (3uM): expected ~35000 (64% inh) -> stuck at 85120 (0% inh = well failure)
    -- A10 (30uM): expected ~5800 (98% inh) -> stuck at 77050 (15% inh = incomplete inhibition)
    ('Chen_L','PLT-001','B01', 89490,'2024-01-15 08:12:00+00','CMP-CL01',  0.001,'sample'),
    ('Chen_L','PLT-001','B02', 89482,'2024-01-15 08:13:00+00','CMP-CL01',  0.003,'sample'),
    ('Chen_L','PLT-001','B03', 89450,'2024-01-15 08:14:00+00','CMP-CL01',  0.010,'sample'),
    ('Chen_L','PLT-001','B04', 89345,'2024-01-15 08:15:00+00','CMP-CL01',  0.030,'sample'),
    ('Chen_L','PLT-001','B05', 88560,'2024-01-15 08:16:00+00','CMP-CL01',  0.100,'sample'),
    ('Chen_L','PLT-001','B06', 84830,'2024-01-15 08:17:00+00','CMP-CL01',  0.300,'sample'),
    ('Chen_L','PLT-001','B07', 66970,'2024-01-15 08:18:00+00','CMP-CL01',  1.000,'sample'),
    ('Chen_L','PLT-001','B08', 85120,'2024-01-15 08:19:00+00','CMP-CL01',  3.000,'sample'),
    ('Chen_L','PLT-001','B09', 14050,'2024-01-15 08:20:00+00','CMP-CL01', 10.000,'sample'),
    ('Chen_L','PLT-001','B10', 77050,'2024-01-15 08:21:00+00','CMP-CL01', 30.000,'sample'),
    ('Chen_L','PLT-001','B11', 89180,'2024-01-15 08:22:00+00','CMP-CL01',  NULL,'neg_ctrl'),
    ('Chen_L','PLT-001','B12',  5150,'2024-01-15 08:23:00+00','CMP-CL01',  NULL,'pos_ctrl'),

    -- Row C: Patel_R  CMP-PR01  EC50=0.8uM Hill=1.2 GOOD
    -- neg=91500 pos=5200  window=86300
    ('Patel_R','PLT-001','C01', 91470,'2024-01-15 08:24:00+00','CMP-PR01',  0.001,'sample'),
    ('Patel_R','PLT-001','C02', 91390,'2024-01-15 08:25:00+00','CMP-PR01',  0.003,'sample'),
    ('Patel_R','PLT-001','C03', 90950,'2024-01-15 08:26:00+00','CMP-PR01',  0.010,'sample'),
    ('Patel_R','PLT-001','C04', 89820,'2024-01-15 08:27:00+00','CMP-PR01',  0.030,'sample'),
    ('Patel_R','PLT-001','C05', 84680,'2024-01-15 08:28:00+00','CMP-PR01',  0.100,'sample'),
    ('Patel_R','PLT-001','C06', 70160,'2024-01-15 08:29:00+00','CMP-PR01',  0.300,'sample'),
    ('Patel_R','PLT-001','C07', 40090,'2024-01-15 08:30:00+00','CMP-PR01',  1.000,'sample'),
    ('Patel_R','PLT-001','C08', 15210,'2024-01-15 08:31:00+00','CMP-PR01',  3.000,'sample'),
    ('Patel_R','PLT-001','C09',  9380,'2024-01-15 08:32:00+00','CMP-PR01', 10.000,'sample'),
    ('Patel_R','PLT-001','C10',  6350,'2024-01-15 08:33:00+00','CMP-PR01', 30.000,'sample'),
    ('Patel_R','PLT-001','C11', 91280,'2024-01-15 08:34:00+00','CMP-PR01',  NULL,'neg_ctrl'),
    ('Patel_R','PLT-001','C12',  5140,'2024-01-15 08:35:00+00','CMP-PR01',  NULL,'pos_ctrl'),

    -- Row D: Williams_K  CMP-WK01  base EC50=10uM BAD
    -- neg=90200 pos=5500  window=84700
    -- D04 (0.03uM): expected 89945 (0.3% inh) -> 51530 (45% inh = contamination spike)
    -- D10 (30uM): expected ~47800 (75% inh) -> 82980 (8% inh = well failure)
    ('Williams_K','PLT-001','D01', 90190,'2024-01-15 08:36:00+00','CMP-WK01',  0.001,'sample'),
    ('Williams_K','PLT-001','D02', 90175,'2024-01-15 08:37:00+00','CMP-WK01',  0.003,'sample'),
    ('Williams_K','PLT-001','D03', 90115,'2024-01-15 08:38:00+00','CMP-WK01',  0.010,'sample'),
    ('Williams_K','PLT-001','D04', 51530,'2024-01-15 08:39:00+00','CMP-WK01',  0.030,'sample'),
    ('Williams_K','PLT-001','D05', 90105,'2024-01-15 08:40:00+00','CMP-WK01',  0.100,'sample'),
    ('Williams_K','PLT-001','D06', 89940,'2024-01-15 08:41:00+00','CMP-WK01',  0.300,'sample'),
    ('Williams_K','PLT-001','D07', 89420,'2024-01-15 08:42:00+00','CMP-WK01',  1.000,'sample'),
    ('Williams_K','PLT-001','D08', 67400,'2024-01-15 08:43:00+00','CMP-WK01',  3.000,'sample'),
    ('Williams_K','PLT-001','D09', 47450,'2024-01-15 08:44:00+00','CMP-WK01', 10.000,'sample'),
    ('Williams_K','PLT-001','D10', 82980,'2024-01-15 08:45:00+00','CMP-WK01', 30.000,'sample'),
    ('Williams_K','PLT-001','D11', 90060,'2024-01-15 08:46:00+00','CMP-WK01',  NULL,'neg_ctrl'),
    ('Williams_K','PLT-001','D12',  5470,'2024-01-15 08:47:00+00','CMP-WK01',  NULL,'pos_ctrl'),

    -- Row E: Rodriguez_M  CMP-RM01  EC50=5.0uM Hill=2.0 GOOD
    -- neg=89800 pos=5100  window=84700
    ('Rodriguez_M','PLT-001','E01', 89800,'2024-01-15 08:48:00+00','CMP-RM01',  0.001,'sample'),
    ('Rodriguez_M','PLT-001','E02', 89800,'2024-01-15 08:49:00+00','CMP-RM01',  0.003,'sample'),
    ('Rodriguez_M','PLT-001','E03', 89798,'2024-01-15 08:50:00+00','CMP-RM01',  0.010,'sample'),
    ('Rodriguez_M','PLT-001','E04', 89796,'2024-01-15 08:51:00+00','CMP-RM01',  0.030,'sample'),
    ('Rodriguez_M','PLT-001','E05', 89764,'2024-01-15 08:52:00+00','CMP-RM01',  0.100,'sample'),
    ('Rodriguez_M','PLT-001','E06', 89490,'2024-01-15 08:53:00+00','CMP-RM01',  0.300,'sample'),
    ('Rodriguez_M','PLT-001','E07', 86550,'2024-01-15 08:54:00+00','CMP-RM01',  1.000,'sample'),
    ('Rodriguez_M','PLT-001','E08', 66840,'2024-01-15 08:55:00+00','CMP-RM01',  3.000,'sample'),
    ('Rodriguez_M','PLT-001','E09', 17530,'2024-01-15 08:56:00+00','CMP-RM01', 10.000,'sample'),
    ('Rodriguez_M','PLT-001','E10',  7530,'2024-01-15 08:57:00+00','CMP-RM01', 30.000,'sample'),
    ('Rodriguez_M','PLT-001','E11', 89640,'2024-01-15 08:58:00+00','CMP-RM01',  NULL,'neg_ctrl'),
    ('Rodriguez_M','PLT-001','E12',  5060,'2024-01-15 08:59:00+00','CMP-RM01',  NULL,'pos_ctrl'),

    -- Row F: Kim_S  CMP-KS01  EC50=20uM Hill=1.0 GOOD
    -- neg=88500 pos=5400  window=83100
    ('Kim_S','PLT-001','F01', 88492,'2024-01-15 09:00:00+00','CMP-KS01',  0.001,'sample'),
    ('Kim_S','PLT-001','F02', 88484,'2024-01-15 09:01:00+00','CMP-KS01',  0.003,'sample'),
    ('Kim_S','PLT-001','F03', 88460,'2024-01-15 09:02:00+00','CMP-KS01',  0.010,'sample'),
    ('Kim_S','PLT-001','F04', 88377,'2024-01-15 09:03:00+00','CMP-KS01',  0.030,'sample'),
    ('Kim_S','PLT-001','F05', 88086,'2024-01-15 09:04:00+00','CMP-KS01',  0.100,'sample'),
    ('Kim_S','PLT-001','F06', 86847,'2024-01-15 09:05:00+00','CMP-KS01',  0.300,'sample'),
    ('Kim_S','PLT-001','F07', 84551,'2024-01-15 09:06:00+00','CMP-KS01',  1.000,'sample'),
    ('Kim_S','PLT-001','F08', 77533,'2024-01-15 09:07:00+00','CMP-KS01',  3.000,'sample'),
    ('Kim_S','PLT-001','F09', 60581,'2024-01-15 09:08:00+00','CMP-KS01', 10.000,'sample'),
    ('Kim_S','PLT-001','F10', 38870,'2024-01-15 09:09:00+00','CMP-KS01', 30.000,'sample'),
    ('Kim_S','PLT-001','F11', 88420,'2024-01-15 09:10:00+00','CMP-KS01',  NULL,'neg_ctrl'),
    ('Kim_S','PLT-001','F12',  5380,'2024-01-15 09:11:00+00','CMP-KS01',  NULL,'pos_ctrl'),

    -- Row G: Mueller_T  CMP-MT01  base EC50=1.0uM BAD
    -- neg=88800 pos=4900  window=83900
    -- G06 (0.3uM): expected 76350 (14% inh) -> 23080 (78% inh = detector spike)
    -- G08 (3.0uM): expected 15270 (87% inh) -> 78360 (12% inh = well failure)
    ('Mueller_T','PLT-001','G01', 88800,'2024-01-15 09:12:00+00','CMP-MT01',  0.001,'sample'),
    ('Mueller_T','PLT-001','G02', 88783,'2024-01-15 09:13:00+00','CMP-MT01',  0.003,'sample'),
    ('Mueller_T','PLT-001','G03', 88716,'2024-01-15 09:14:00+00','CMP-MT01',  0.010,'sample'),
    ('Mueller_T','PLT-001','G04', 88364,'2024-01-15 09:15:00+00','CMP-MT01',  0.030,'sample'),
    ('Mueller_T','PLT-001','G05', 86222,'2024-01-15 09:16:00+00','CMP-MT01',  0.100,'sample'),
    ('Mueller_T','PLT-001','G06', 23080,'2024-01-15 09:17:00+00','CMP-MT01',  0.300,'sample'),
    ('Mueller_T','PLT-001','G07', 46680,'2024-01-15 09:18:00+00','CMP-MT01',  1.000,'sample'),
    ('Mueller_T','PLT-001','G08', 78360,'2024-01-15 09:19:00+00','CMP-MT01',  3.000,'sample'),
    ('Mueller_T','PLT-001','G09',  7430,'2024-01-15 09:20:00+00','CMP-MT01', 10.000,'sample'),
    ('Mueller_T','PLT-001','G10',  5300,'2024-01-15 09:21:00+00','CMP-MT01', 30.000,'sample'),
    ('Mueller_T','PLT-001','G11', 88650,'2024-01-15 09:22:00+00','CMP-MT01',  NULL,'neg_ctrl'),
    ('Mueller_T','PLT-001','G12',  4870,'2024-01-15 09:23:00+00','CMP-MT01',  NULL,'pos_ctrl'),

    -- Row H: Okonkwo_A  CMP-OA01  EC50=0.3uM Hill=1.8 GOOD
    -- neg=92000 pos=4800  window=87200
    ('Okonkwo_A','PLT-001','H01', 92000,'2024-01-15 09:24:00+00','CMP-OA01',  0.001,'sample'),
    ('Okonkwo_A','PLT-001','H02', 91965,'2024-01-15 09:25:00+00','CMP-OA01',  0.003,'sample'),
    ('Okonkwo_A','PLT-001','H03', 91810,'2024-01-15 09:26:00+00','CMP-OA01',  0.010,'sample'),
    ('Okonkwo_A','PLT-001','H04', 89960,'2024-01-15 09:27:00+00','CMP-OA01',  0.030,'sample'),
    ('Okonkwo_A','PLT-001','H05', 80220,'2024-01-15 09:28:00+00','CMP-OA01',  0.100,'sample'),
    ('Okonkwo_A','PLT-001','H06', 45690,'2024-01-15 09:29:00+00','CMP-OA01',  0.300,'sample'),
    ('Okonkwo_A','PLT-001','H07',  9360,'2024-01-15 09:30:00+00','CMP-OA01',  1.000,'sample'),
    ('Okonkwo_A','PLT-001','H08',  6820,'2024-01-15 09:31:00+00','CMP-OA01',  3.000,'sample'),
    ('Okonkwo_A','PLT-001','H09',  5850,'2024-01-15 09:32:00+00','CMP-OA01', 10.000,'sample'),
    ('Okonkwo_A','PLT-001','H10',  5050,'2024-01-15 09:33:00+00','CMP-OA01', 30.000,'sample'),
    ('Okonkwo_A','PLT-001','H11', 91860,'2024-01-15 09:34:00+00','CMP-OA01',  NULL,'neg_ctrl'),
    ('Okonkwo_A','PLT-001','H12',  4760,'2024-01-15 09:35:00+00','CMP-OA01',  NULL,'pos_ctrl'),

    -- ═══════════════════════════════════════════════════════════════
    -- PLT-002  (96 wells, fully filled)
    -- ═══════════════════════════════════════════════════════════════
    -- Row A: Tanaka_Y  CMP-TY01  EC50=3.0uM Hill=1.3 GOOD
    -- neg=90500 pos=5300  window=85200
    ('Tanaka_Y','PLT-002','A01', 90500,'2024-02-20 08:00:00+00','CMP-TY01',  0.001,'sample'),
    ('Tanaka_Y','PLT-002','A02', 90491,'2024-02-20 08:01:00+00','CMP-TY01',  0.003,'sample'),
    ('Tanaka_Y','PLT-002','A03', 90449,'2024-02-20 08:02:00+00','CMP-TY01',  0.010,'sample'),
    ('Tanaka_Y','PLT-002','A04', 90270,'2024-02-20 08:03:00+00','CMP-TY01',  0.030,'sample'),
    ('Tanaka_Y','PLT-002','A05', 89468,'2024-02-20 08:04:00+00','CMP-TY01',  0.100,'sample'),
    ('Tanaka_Y','PLT-002','A06', 85040,'2024-02-20 08:05:00+00','CMP-TY01',  0.300,'sample'),
    ('Tanaka_Y','PLT-002','A07', 68580,'2024-02-20 08:06:00+00','CMP-TY01',  1.000,'sample'),
    ('Tanaka_Y','PLT-002','A08', 45400,'2024-02-20 08:07:00+00','CMP-TY01',  3.000,'sample'),
    ('Tanaka_Y','PLT-002','A09', 15430,'2024-02-20 08:08:00+00','CMP-TY01', 10.000,'sample'),
    ('Tanaka_Y','PLT-002','A10',  8990,'2024-02-20 08:09:00+00','CMP-TY01', 30.000,'sample'),
    ('Tanaka_Y','PLT-002','A11', 90350,'2024-02-20 08:10:00+00','CMP-TY01',  NULL,'neg_ctrl'),
    ('Tanaka_Y','PLT-002','A12',  5280,'2024-02-20 08:11:00+00','CMP-TY01',  NULL,'pos_ctrl'),

    -- Row B: Gupta_P  CMP-GP01  base EC50=4.0uM BAD
    -- neg=91000 pos=5100  window=85900
    -- B03 (0.01uM): expected 90966 (0.04% inh) -> 46280 (52% inh = contamination)
    -- B09 (10uM): expected 25830 (76% inh) -> 86650 (5% inh = well failure)
    ('Gupta_P','PLT-002','B01', 90999,'2024-02-20 08:12:00+00','CMP-GP01',  0.001,'sample'),
    ('Gupta_P','PLT-002','B02', 90990,'2024-02-20 08:13:00+00','CMP-GP01',  0.003,'sample'),
    ('Gupta_P','PLT-002','B03', 46280,'2024-02-20 08:14:00+00','CMP-GP01',  0.010,'sample'),
    ('Gupta_P','PLT-002','B04', 90837,'2024-02-20 08:15:00+00','CMP-GP01',  0.030,'sample'),
    ('Gupta_P','PLT-002','B05', 90295,'2024-02-20 08:16:00+00','CMP-GP01',  0.100,'sample'),
    ('Gupta_P','PLT-002','B06', 87920,'2024-02-20 08:17:00+00','CMP-GP01',  0.300,'sample'),
    ('Gupta_P','PLT-002','B07', 78820,'2024-02-20 08:18:00+00','CMP-GP01',  1.000,'sample'),
    ('Gupta_P','PLT-002','B08', 57920,'2024-02-20 08:19:00+00','CMP-GP01',  3.000,'sample'),
    ('Gupta_P','PLT-002','B09', 86650,'2024-02-20 08:20:00+00','CMP-GP01', 10.000,'sample'),
    ('Gupta_P','PLT-002','B10',  8150,'2024-02-20 08:21:00+00','CMP-GP01', 30.000,'sample'),
    ('Gupta_P','PLT-002','B11', 90860,'2024-02-20 08:22:00+00','CMP-GP01',  NULL,'neg_ctrl'),
    ('Gupta_P','PLT-002','B12',  5090,'2024-02-20 08:23:00+00','CMP-GP01',  NULL,'pos_ctrl'),

    -- Row C: Johnson_B  CMP-JB01  EC50=8.0uM Hill=1.5 GOOD
    -- neg=91200 pos=5600  window=85600
    ('Johnson_B','PLT-002','C01', 91200,'2024-02-20 08:24:00+00','CMP-JB01',  0.001,'sample'),
    ('Johnson_B','PLT-002','C02', 91200,'2024-02-20 08:25:00+00','CMP-JB01',  0.003,'sample'),
    ('Johnson_B','PLT-002','C03', 91200,'2024-02-20 08:26:00+00','CMP-JB01',  0.010,'sample'),
    ('Johnson_B','PLT-002','C04', 91183,'2024-02-20 08:27:00+00','CMP-JB01',  0.030,'sample'),
    ('Johnson_B','PLT-002','C05', 91082,'2024-02-20 08:28:00+00','CMP-JB01',  0.100,'sample'),
    ('Johnson_B','PLT-002','C06', 90576,'2024-02-20 08:29:00+00','CMP-JB01',  0.300,'sample'),
    ('Johnson_B','PLT-002','C07', 87595,'2024-02-20 08:30:00+00','CMP-JB01',  1.000,'sample'),
    ('Johnson_B','PLT-002','C08', 73944,'2024-02-20 08:31:00+00','CMP-JB01',  3.000,'sample'),
    ('Johnson_B','PLT-002','C09', 37948,'2024-02-20 08:32:00+00','CMP-JB01', 10.000,'sample'),
    ('Johnson_B','PLT-002','C10', 11430,'2024-02-20 08:33:00+00','CMP-JB01', 30.000,'sample'),
    ('Johnson_B','PLT-002','C11', 91060,'2024-02-20 08:34:00+00','CMP-JB01',  NULL,'neg_ctrl'),
    ('Johnson_B','PLT-002','C12',  5580,'2024-02-20 08:35:00+00','CMP-JB01',  NULL,'pos_ctrl'),

    -- Row D: Lee_H  CMP-LH01  base EC50=6.0uM BAD
    -- neg=89500 pos=5200  window=84300
    -- D03 (0.01uM): expected 89494 (0.01% inh) -> 48980 (48% inh = contamination)
    -- D10 (30uM): expected 12110 (92% inh) -> 85120 (5% inh = well failure)
    ('Lee_H','PLT-002','D01', 89500,'2024-02-20 08:36:00+00','CMP-LH01',  0.001,'sample'),
    ('Lee_H','PLT-002','D02', 89499,'2024-02-20 08:37:00+00','CMP-LH01',  0.003,'sample'),
    ('Lee_H','PLT-002','D03', 48980,'2024-02-20 08:38:00+00','CMP-LH01',  0.010,'sample'),
    ('Lee_H','PLT-002','D04', 89466,'2024-02-20 08:39:00+00','CMP-LH01',  0.030,'sample'),
    ('Lee_H','PLT-002','D05', 89322,'2024-02-20 08:40:00+00','CMP-LH01',  0.100,'sample'),
    ('Lee_H','PLT-002','D06', 88560,'2024-02-20 08:41:00+00','CMP-LH01',  0.300,'sample'),
    ('Lee_H','PLT-002','D07', 83880,'2024-02-20 08:42:00+00','CMP-LH01',  1.000,'sample'),
    ('Lee_H','PLT-002','D08', 66840,'2024-02-20 08:43:00+00','CMP-LH01',  3.000,'sample'),
    ('Lee_H','PLT-002','D09', 27020,'2024-02-20 08:44:00+00','CMP-LH01', 10.000,'sample'),
    ('Lee_H','PLT-002','D10', 85120,'2024-02-20 08:45:00+00','CMP-LH01', 30.000,'sample'),
    ('Lee_H','PLT-002','D11', 89390,'2024-02-20 08:46:00+00','CMP-LH01',  NULL,'neg_ctrl'),
    ('Lee_H','PLT-002','D12',  5170,'2024-02-20 08:47:00+00','CMP-LH01',  NULL,'pos_ctrl'),

    -- Row E: Fernandez_C  CMP-FC01  EC50=0.5uM Hill=1.6 GOOD
    -- neg=89000 pos=4600  window=84400
    ('Fernandez_C','PLT-002','E01', 89000,'2024-02-20 08:48:00+00','CMP-FC01',  0.001,'sample'),
    ('Fernandez_C','PLT-002','E02', 88958,'2024-02-20 08:49:00+00','CMP-FC01',  0.003,'sample'),
    ('Fernandez_C','PLT-002','E03', 88840,'2024-02-20 08:50:00+00','CMP-FC01',  0.010,'sample'),
    ('Fernandez_C','PLT-002','E04', 87380,'2024-02-20 08:51:00+00','CMP-FC01',  0.030,'sample'),
    ('Fernandez_C','PLT-002','E05', 82540,'2024-02-20 08:52:00+00','CMP-FC01',  0.100,'sample'),
    ('Fernandez_C','PLT-002','E06', 60010,'2024-02-20 08:53:00+00','CMP-FC01',  0.300,'sample'),
    ('Fernandez_C','PLT-002','E07', 22560,'2024-02-20 08:54:00+00','CMP-FC01',  1.000,'sample'),
    ('Fernandez_C','PLT-002','E08',  6790,'2024-02-20 08:55:00+00','CMP-FC01',  3.000,'sample'),
    ('Fernandez_C','PLT-002','E09',  5550,'2024-02-20 08:56:00+00','CMP-FC01', 10.000,'sample'),
    ('Fernandez_C','PLT-002','E10',  4920,'2024-02-20 08:57:00+00','CMP-FC01', 30.000,'sample'),
    ('Fernandez_C','PLT-002','E11', 88960,'2024-02-20 08:58:00+00','CMP-FC01',  NULL,'neg_ctrl'),
    ('Fernandez_C','PLT-002','E12',  4590,'2024-02-20 08:59:00+00','CMP-FC01',  NULL,'pos_ctrl'),

    -- Row F: Nakamura_S  CMP-NS01  EC50=12uM Hill=1.1 GOOD
    -- neg=90800 pos=5100  window=85700
    ('Nakamura_S','PLT-002','F01', 90800,'2024-02-20 09:00:00+00','CMP-NS01',  0.001,'sample'),
    ('Nakamura_S','PLT-002','F02', 90783,'2024-02-20 09:01:00+00','CMP-NS01',  0.003,'sample'),
    ('Nakamura_S','PLT-002','F03', 90766,'2024-02-20 09:02:00+00','CMP-NS01',  0.010,'sample'),
    ('Nakamura_S','PLT-002','F04', 90689,'2024-02-20 09:03:00+00','CMP-NS01',  0.030,'sample'),
    ('Nakamura_S','PLT-002','F05', 90363,'2024-02-20 09:04:00+00','CMP-NS01',  0.100,'sample'),
    ('Nakamura_S','PLT-002','F06', 89239,'2024-02-20 09:05:00+00','CMP-NS01',  0.300,'sample'),
    ('Nakamura_S','PLT-002','F07', 85468,'2024-02-20 09:06:00+00','CMP-NS01',  1.000,'sample'),
    ('Nakamura_S','PLT-002','F08', 74293,'2024-02-20 09:07:00+00','CMP-NS01',  3.000,'sample'),
    ('Nakamura_S','PLT-002','F09', 52340,'2024-02-20 09:08:00+00','CMP-NS01', 10.000,'sample'),
    ('Nakamura_S','PLT-002','F10', 28050,'2024-02-20 09:09:00+00','CMP-NS01', 30.000,'sample'),
    ('Nakamura_S','PLT-002','F11', 90700,'2024-02-20 09:10:00+00','CMP-NS01',  NULL,'neg_ctrl'),
    ('Nakamura_S','PLT-002','F12',  5080,'2024-02-20 09:11:00+00','CMP-NS01',  NULL,'pos_ctrl'),

    -- Row G: Brown_E  CMP-BE01  base EC50=0.9uM BAD
    -- neg=90500 pos=4800  window=85700
    -- G07 (1uM): expected 43760 (53% inh) -> 11580 (92% inh = early saturation)
    -- G09 (10uM): expected 7180 (97% inh) -> 83260 (8% inh = well failure)
    ('Brown_E','PLT-002','G01', 90499,'2024-02-20 09:12:00+00','CMP-BE01',  0.001,'sample'),
    ('Brown_E','PLT-002','G02', 90483,'2024-02-20 09:13:00+00','CMP-BE01',  0.003,'sample'),
    ('Brown_E','PLT-002','G03', 90397,'2024-02-20 09:14:00+00','CMP-BE01',  0.010,'sample'),
    ('Brown_E','PLT-002','G04', 89977,'2024-02-20 09:15:00+00','CMP-BE01',  0.030,'sample'),
    ('Brown_E','PLT-002','G05', 87443,'2024-02-20 09:16:00+00','CMP-BE01',  0.100,'sample'),
    ('Brown_E','PLT-002','G06', 75660,'2024-02-20 09:17:00+00','CMP-BE01',  0.300,'sample'),
    ('Brown_E','PLT-002','G07', 11580,'2024-02-20 09:18:00+00','CMP-BE01',  1.000,'sample'),
    ('Brown_E','PLT-002','G08', 16860,'2024-02-20 09:19:00+00','CMP-BE01',  3.000,'sample'),
    ('Brown_E','PLT-002','G09', 83260,'2024-02-20 09:20:00+00','CMP-BE01', 10.000,'sample'),
    ('Brown_E','PLT-002','G10',  5250,'2024-02-20 09:21:00+00','CMP-BE01', 30.000,'sample'),
    ('Brown_E','PLT-002','G11', 90380,'2024-02-20 09:22:00+00','CMP-BE01',  NULL,'neg_ctrl'),
    ('Brown_E','PLT-002','G12',  4780,'2024-02-20 09:23:00+00','CMP-BE01',  NULL,'pos_ctrl'),

    -- Row H: Adebayo_F  CMP-AF01  base EC50=0.6uM BAD
    -- neg=88000 pos=4600  window=83400
    -- H02 (0.003uM): expected 87991 (0.01% inh) -> 36300 (62% inh = contamination)
    -- H07 (1uM): expected 28330 (71% inh) -> 81330 (8% inh = well failure)
    ('Adebayo_F','PLT-002','H01', 87999,'2024-02-20 09:24:00+00','CMP-AF01',  0.001,'sample'),
    ('Adebayo_F','PLT-002','H02', 36300,'2024-02-20 09:25:00+00','CMP-AF01',  0.003,'sample'),
    ('Adebayo_F','PLT-002','H03', 87950,'2024-02-20 09:26:00+00','CMP-AF01',  0.010,'sample'),
    ('Adebayo_F','PLT-002','H04', 87425,'2024-02-20 09:27:00+00','CMP-AF01',  0.030,'sample'),
    ('Adebayo_F','PLT-002','H05', 84815,'2024-02-20 09:28:00+00','CMP-AF01',  0.100,'sample'),
    ('Adebayo_F','PLT-002','H06', 69265,'2024-02-20 09:29:00+00','CMP-AF01',  0.300,'sample'),
    ('Adebayo_F','PLT-002','H07', 81330,'2024-02-20 09:30:00+00','CMP-AF01',  1.000,'sample'),
    ('Adebayo_F','PLT-002','H08',  9770,'2024-02-20 09:31:00+00','CMP-AF01',  3.000,'sample'),
    ('Adebayo_F','PLT-002','H09',  5430,'2024-02-20 09:32:00+00','CMP-AF01', 10.000,'sample'),
    ('Adebayo_F','PLT-002','H10',  4970,'2024-02-20 09:33:00+00','CMP-AF01', 30.000,'sample'),
    ('Adebayo_F','PLT-002','H11', 87850,'2024-02-20 09:34:00+00','CMP-AF01',  NULL,'neg_ctrl'),
    ('Adebayo_F','PLT-002','H12',  4620,'2024-02-20 09:35:00+00','CMP-AF01',  NULL,'pos_ctrl'),

    -- ═══════════════════════════════════════════════════════════════
    -- PLT-003  (48 wells, rows A-D only)
    -- ═══════════════════════════════════════════════════════════════
    -- Row A: Martinez_L  CMP-ML01  base EC50=15uM BAD
    -- neg=91200 pos=5400  window=85800
    -- A09 (10uM): expected 57770 (39% inh) -> 15680 (88% inh = biphasic jump)
    -- A10 (30uM): expected 32410 (69% inh) -> 75740 (18% inh = biphasic drop)
    ('Martinez_L','PLT-003','A01', 91200,'2024-03-08 08:00:00+00','CMP-ML01',  0.001,'sample'),
    ('Martinez_L','PLT-003','A02', 91191,'2024-03-08 08:01:00+00','CMP-ML01',  0.003,'sample'),
    ('Martinez_L','PLT-003','A03', 91174,'2024-03-08 08:02:00+00','CMP-ML01',  0.010,'sample'),
    ('Martinez_L','PLT-003','A04', 91114,'2024-03-08 08:03:00+00','CMP-ML01',  0.030,'sample'),
    ('Martinez_L','PLT-003','A05', 90857,'2024-03-08 08:04:00+00','CMP-ML01',  0.100,'sample'),
    ('Martinez_L','PLT-003','A06', 89981,'2024-03-08 08:05:00+00','CMP-ML01',  0.300,'sample'),
    ('Martinez_L','PLT-003','A07', 86044,'2024-03-08 08:06:00+00','CMP-ML01',  1.000,'sample'),
    ('Martinez_L','PLT-003','A08', 77590,'2024-03-08 08:07:00+00','CMP-ML01',  3.000,'sample'),
    ('Martinez_L','PLT-003','A09', 15680,'2024-03-08 08:08:00+00','CMP-ML01', 10.000,'sample'),
    ('Martinez_L','PLT-003','A10', 75740,'2024-03-08 08:09:00+00','CMP-ML01', 30.000,'sample'),
    ('Martinez_L','PLT-003','A11', 91060,'2024-03-08 08:10:00+00','CMP-ML01',  NULL,'neg_ctrl'),
    ('Martinez_L','PLT-003','A12',  5350,'2024-03-08 08:11:00+00','CMP-ML01',  NULL,'pos_ctrl'),

    -- Row B: Singh_A  CMP-SA01  base EC50=3.5uM BAD
    -- neg=89800 pos=5000  window=84800
    -- B07 (1uM): expected 76400 (14% inh) -> 22760 (79% inh = contamination spike)
    -- B09 (10uM): expected 21920 (81% inh) -> 85520 (5% inh = well failure)
    ('Singh_A','PLT-003','B01', 89800,'2024-03-08 08:12:00+00','CMP-SA01',  0.001,'sample'),
    ('Singh_A','PLT-003','B02', 89791,'2024-03-08 08:13:00+00','CMP-SA01',  0.003,'sample'),
    ('Singh_A','PLT-003','B03', 89774,'2024-03-08 08:14:00+00','CMP-SA01',  0.010,'sample'),
    ('Singh_A','PLT-003','B04', 89698,'2024-03-08 08:15:00+00','CMP-SA01',  0.030,'sample'),
    ('Singh_A','PLT-003','B05', 89214,'2024-03-08 08:16:00+00','CMP-SA01',  0.100,'sample'),
    ('Singh_A','PLT-003','B06', 86920,'2024-03-08 08:17:00+00','CMP-SA01',  0.300,'sample'),
    ('Singh_A','PLT-003','B07', 22760,'2024-03-08 08:18:00+00','CMP-SA01',  1.000,'sample'),
    ('Singh_A','PLT-003','B08', 50680,'2024-03-08 08:19:00+00','CMP-SA01',  3.000,'sample'),
    ('Singh_A','PLT-003','B09', 85520,'2024-03-08 08:20:00+00','CMP-SA01', 10.000,'sample'),
    ('Singh_A','PLT-003','B10',  5740,'2024-03-08 08:21:00+00','CMP-SA01', 30.000,'sample'),
    ('Singh_A','PLT-003','B11', 89660,'2024-03-08 08:22:00+00','CMP-SA01',  NULL,'neg_ctrl'),
    ('Singh_A','PLT-003','B12',  4980,'2024-03-08 08:23:00+00','CMP-SA01',  NULL,'pos_ctrl'),

    -- Row C: Thompson_R  CMP-TR01  EC50=2.5uM Hill=1.4 GOOD
    -- neg=91000 pos=5300  window=85700
    ('Thompson_R','PLT-003','C01', 91000,'2024-03-08 08:24:00+00','CMP-TR01',  0.001,'sample'),
    ('Thompson_R','PLT-003','C02', 90991,'2024-03-08 08:25:00+00','CMP-TR01',  0.003,'sample'),
    ('Thompson_R','PLT-003','C03', 90966,'2024-03-08 08:26:00+00','CMP-TR01',  0.010,'sample'),
    ('Thompson_R','PLT-003','C04', 90829,'2024-03-08 08:27:00+00','CMP-TR01',  0.030,'sample'),
    ('Thompson_R','PLT-003','C05', 90065,'2024-03-08 08:28:00+00','CMP-TR01',  0.100,'sample'),
    ('Thompson_R','PLT-003','C06', 85946,'2024-03-08 08:29:00+00','CMP-TR01',  0.300,'sample'),
    ('Thompson_R','PLT-003','C07', 75604,'2024-03-08 08:30:00+00','CMP-TR01',  1.000,'sample'),
    ('Thompson_R','PLT-003','C08', 37938,'2024-03-08 08:31:00+00','CMP-TR01',  3.000,'sample'),
    ('Thompson_R','PLT-003','C09', 10844,'2024-03-08 08:32:00+00','CMP-TR01', 10.000,'sample'),
    ('Thompson_R','PLT-003','C10',  8570,'2024-03-08 08:33:00+00','CMP-TR01', 30.000,'sample'),
    ('Thompson_R','PLT-003','C11', 90860,'2024-03-08 08:34:00+00','CMP-TR01',  NULL,'neg_ctrl'),
    ('Thompson_R','PLT-003','C12',  5270,'2024-03-08 08:35:00+00','CMP-TR01',  NULL,'pos_ctrl'),

    -- Row D: Walsh_D  CMP-WD01  base EC50=7.0uM BAD
    -- neg=90800 pos=5100  window=85700
    -- D07 (1uM): expected 86390 (5% inh) -> 16180 (87% inh = contamination spike)
    -- D09 (10uM): expected 36620 (63% inh) -> 77920 (15% inh = well failure)
    ('Walsh_D','PLT-003','D01', 90800,'2024-03-08 08:36:00+00','CMP-WD01',  0.001,'sample'),
    ('Walsh_D','PLT-003','D02', 90800,'2024-03-08 08:37:00+00','CMP-WD01',  0.003,'sample'),
    ('Walsh_D','PLT-003','D03', 90791,'2024-03-08 08:38:00+00','CMP-WD01',  0.010,'sample'),
    ('Walsh_D','PLT-003','D04', 90774,'2024-03-08 08:39:00+00','CMP-WD01',  0.030,'sample'),
    ('Walsh_D','PLT-003','D05', 90654,'2024-03-08 08:40:00+00','CMP-WD01',  0.100,'sample'),
    ('Walsh_D','PLT-003','D06', 90047,'2024-03-08 08:41:00+00','CMP-WD01',  0.300,'sample'),
    ('Walsh_D','PLT-003','D07', 16180,'2024-03-08 08:42:00+00','CMP-WD01',  1.000,'sample'),
    ('Walsh_D','PLT-003','D08', 71870,'2024-03-08 08:43:00+00','CMP-WD01',  3.000,'sample'),
    ('Walsh_D','PLT-003','D09', 77920,'2024-03-08 08:44:00+00','CMP-WD01', 10.000,'sample'),
    ('Walsh_D','PLT-003','D10', 13760,'2024-03-08 08:45:00+00','CMP-WD01', 30.000,'sample'),
    ('Walsh_D','PLT-003','D11', 90660,'2024-03-08 08:46:00+00','CMP-WD01',  NULL,'neg_ctrl'),
    ('Walsh_D','PLT-003','D12',  5120,'2024-03-08 08:47:00+00','CMP-WD01',  NULL,'pos_ctrl')

) AS e(scientist_name, plate_barcode, well_position, raw_value, recorded_at,
       compound_id, concentration_um, well_type)
JOIN users u ON u.name = e.scientist_name
ON CONFLICT (plate_barcode, well_position) DO NOTHING;
