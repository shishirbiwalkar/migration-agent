'use client'

/**
 * ABASE LEGACY DATABASE VIEWER
 *
 * Intentionally looks like IBM SPSS / Excel 2003.
 * No modern UI. No Tailwind classes. Pure inline styles.
 * System fonts only. Dense. Dated. Raw.
 */

import { useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Record {
  rowNum:     number
  recordId:   string
  scientist:  string
  plate:      string
  well:       string
  rawValue:   number
  timestamp:  string
}

type SortKey = keyof Omit<Record, 'rowNum'>
type SortDir = 'asc' | 'desc'

// ── Mock Data (25 rows) ───────────────────────────────────────────────────────

const RAW_DATA: Omit<Record, 'rowNum'>[] = [
  { recordId: 'REC-001', scientist: 'Smith_J',     plate: 'PLT-A001', well: 'A01', rawValue: 14.52, timestamp: '2023-01-15 08:00:12' },
  { recordId: 'REC-002', scientist: 'Chen_L',      plate: 'PLT-A001', well: 'A02', rawValue:  8.73, timestamp: '2023-01-15 08:03:44' },
  { recordId: 'REC-003', scientist: 'Patel_R',     plate: 'PLT-A001', well: 'B01', rawValue: 12.18, timestamp: '2023-01-15 08:07:02' },
  { recordId: 'REC-004', scientist: 'Smith_J',     plate: 'PLT-A001', well: 'B02', rawValue:  3.45, timestamp: '2023-01-15 08:10:55' },
  { recordId: 'REC-005', scientist: 'Williams_K',  plate: 'PLT-A002', well: 'C01', rawValue:  9.87, timestamp: '2023-01-16 09:00:33' },
  { recordId: 'REC-006', scientist: 'Rodriguez_M', plate: 'PLT-A002', well: 'C02', rawValue: 11.23, timestamp: '2023-01-16 09:04:17' },
  { recordId: 'REC-007', scientist: 'Kim_S',       plate: 'PLT-A002', well: 'D01', rawValue:  7.65, timestamp: '2023-01-16 09:08:49' },
  { recordId: 'REC-008', scientist: 'Mueller_T',   plate: 'PLT-A002', well: 'D02', rawValue:  5.91, timestamp: '2023-01-16 09:12:31' },
  { recordId: 'REC-009', scientist: 'Okonkwo_A',   plate: 'PLT-B001', well: 'E01', rawValue: 13.44, timestamp: '2023-01-17 10:00:08' },
  { recordId: 'REC-010', scientist: 'Chen_L',      plate: 'PLT-B001', well: 'E02', rawValue:  2.87, timestamp: '2023-01-17 10:03:55' },
  { recordId: 'REC-011', scientist: 'Patel_R',     plate: 'PLT-B001', well: 'F01', rawValue:  6.12, timestamp: '2023-01-17 10:07:22' },
  { recordId: 'REC-012', scientist: 'Smith_J',     plate: 'PLT-B001', well: 'F02', rawValue: 10.78, timestamp: '2023-01-17 10:11:04' },
  { recordId: 'REC-013', scientist: 'Williams_K',  plate: 'PLT-B002', well: 'G01', rawValue:  4.33, timestamp: '2023-01-18 11:00:19' },
  { recordId: 'REC-014', scientist: 'Rodriguez_M', plate: 'PLT-B002', well: 'G02', rawValue: 15.01, timestamp: '2023-01-18 11:04:47' },
  { recordId: 'REC-015', scientist: 'Kim_S',       plate: 'PLT-B002', well: 'H01', rawValue:  8.56, timestamp: '2023-01-18 11:08:33' },
  { recordId: 'REC-016', scientist: 'Mueller_T',   plate: 'PLT-B002', well: 'H02', rawValue:  1.23, timestamp: '2023-01-18 11:12:11' },
  { recordId: 'REC-017', scientist: 'Okonkwo_A',   plate: 'PLT-C001', well: 'A03', rawValue:  9.99, timestamp: '2023-01-19 12:00:44' },
  { recordId: 'REC-018', scientist: 'Chen_L',      plate: 'PLT-C001', well: 'A04', rawValue:  7.77, timestamp: '2023-01-19 12:04:28' },
  { recordId: 'REC-019', scientist: 'Patel_R',     plate: 'PLT-C001', well: 'B03', rawValue: 12.50, timestamp: '2023-01-19 12:08:16' },
  { recordId: 'REC-020', scientist: 'Smith_J',     plate: 'PLT-C001', well: 'B04', rawValue:  3.89, timestamp: '2023-01-19 12:12:03' },
  { recordId: 'REC-021', scientist: 'Williams_K',  plate: 'PLT-C002', well: 'C03', rawValue:  6.44, timestamp: '2023-01-20 13:00:57' },
  { recordId: 'REC-022', scientist: 'Rodriguez_M', plate: 'PLT-C002', well: 'C04', rawValue: 11.67, timestamp: '2023-01-20 13:04:39' },
  { recordId: 'REC-023', scientist: 'Kim_S',       plate: 'PLT-C002', well: 'D03', rawValue:  0.95, timestamp: '2023-01-20 13:08:22' },
  { recordId: 'REC-024', scientist: 'Mueller_T',   plate: 'PLT-C002', well: 'D04', rawValue: 14.88, timestamp: '2023-01-20 13:12:08' },
  { recordId: 'REC-025', scientist: 'Okonkwo_A',   plate: 'PLT-D001', well: 'E03', rawValue:  5.32, timestamp: '2023-01-21 14:00:36' },
]

const DATA: Record[] = RAW_DATA.map((r, i) => ({ rowNum: i + 1, ...r }))

// ── Style constants (Windows 98 palette) ─────────────────────────────────────

const WIN_GRAY   = '#d4d0c8'
const WIN_DARK   = '#808080'
const WIN_WHITE  = '#ffffff'
const WIN_BG     = '#ffffff'
const WIN_SEL    = '#000080'    // classic Windows selection blue
const WIN_ALTROW = '#f0f0f0'
const FONT       = 'Arial, Helvetica, sans-serif'
const FONT_SIZE  = '12px'

// ── Raised button (Win98 style) ───────────────────────────────────────────────

function ToolbarButton({
  label,
  onClick,
}: {
  label: string
  onClick?: () => void
}) {
  const [pressed, setPressed] = useState(false)

  return (
    <button
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      onMouseLeave={() => setPressed(false)}
      onClick={onClick}
      style={{
        fontFamily:    FONT,
        fontSize:      '11px',
        background:    WIN_GRAY,
        padding:       '1px 7px',
        cursor:        'default',
        outline:       'none',
        border:        'none',
        borderTop:     pressed ? `1px solid ${WIN_DARK}`  : `1px solid ${WIN_WHITE}`,
        borderLeft:    pressed ? `1px solid ${WIN_DARK}`  : `1px solid ${WIN_WHITE}`,
        borderBottom:  pressed ? `1px solid ${WIN_WHITE}` : `1px solid ${WIN_DARK}`,
        borderRight:   pressed ? `1px solid ${WIN_WHITE}` : `1px solid ${WIN_DARK}`,
        height:        '22px',
        whiteSpace:    'nowrap',
        userSelect:    'none',
      }}
    >
      {label}
    </button>
  )
}

// ── Toolbar divider ───────────────────────────────────────────────────────────

function ToolbarDivider() {
  return (
    <div style={{
      display:     'inline-block',
      width:       '1px',
      height:      '18px',
      background:  WIN_DARK,
      borderRight: `1px solid ${WIN_WHITE}`,
      margin:      '0 5px',
      verticalAlign: 'middle',
    }} />
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function AbasePage() {
  const [selectedRow, setSelectedRow] = useState<number | null>(null)
  const [sortKey,     setSortKey]     = useState<SortKey>('recordId')
  const [sortDir,     setSortDir]     = useState<SortDir>('asc')

  // Click on a column header — toggle sort direction or switch sort key
  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  // Sort the data
  const sorted = [...DATA].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    const cmp = typeof av === 'number' && typeof bv === 'number'
      ? av - bv
      : String(av).localeCompare(String(bv))
    return sortDir === 'asc' ? cmp : -cmp
  })

  // Column definitions
  const COLUMNS: { key: SortKey; label: string; width: number; align?: 'right' | 'center' }[] = [
    { key: 'recordId',  label: 'Record_ID',      width: 90  },
    { key: 'scientist', label: 'Scientist_Name',  width: 120 },
    { key: 'plate',     label: 'Plate_Barcode',   width: 100 },
    { key: 'well',      label: 'Well_Idx',        width: 70, align: 'center' },
    { key: 'rawValue',  label: 'Raw_Value',       width: 90, align: 'right'  },
    { key: 'timestamp', label: 'Timestamp',       width: 160 },
  ]

  // ── Header cell style ────────────────────────────────────────────────────
  const thStyle = (col: typeof COLUMNS[0]): React.CSSProperties => ({
    width:           col.width,
    minWidth:        col.width,
    background:      WIN_GRAY,
    borderRight:     `1px solid ${WIN_DARK}`,
    borderBottom:    `2px solid ${WIN_DARK}`,
    padding:         '2px 4px',
    fontWeight:      'bold',
    fontSize:        '11px',
    fontFamily:      FONT,
    cursor:          'default',
    userSelect:      'none',
    textAlign:       col.align ?? 'left',
    whiteSpace:      'nowrap',
    position:        'sticky',
    top:             0,
    zIndex:          1,
  })

  // ── Data cell style ──────────────────────────────────────────────────────
  const tdStyle = (isSelected: boolean, isAlt: boolean, align?: string): React.CSSProperties => ({
    padding:       '1px 4px',
    fontFamily:    FONT,
    fontSize:      FONT_SIZE,
    borderRight:   `1px solid #cccccc`,
    borderBottom:  `1px solid #cccccc`,
    background:    isSelected ? WIN_SEL   : isAlt ? WIN_ALTROW : WIN_BG,
    color:         isSelected ? WIN_WHITE : '#000000',
    whiteSpace:    'nowrap',
    textAlign:     (align as React.CSSProperties['textAlign']) ?? 'left',
    cursor:        'default',
  })

  return (
    <div style={{
      display:        'flex',
      flexDirection:  'column',
      height:         '100vh',
      fontFamily:     FONT,
      fontSize:       FONT_SIZE,
      background:     WIN_BG,
      overflow:       'hidden',
      userSelect:     'none',
    }}>

      {/* ── Title Bar ─────────────────────────────────────────────────────────
          Simulates the Windows application title bar.
      ── */}
      <div style={{
        background:    WIN_SEL,
        color:         WIN_WHITE,
        padding:       '2px 6px',
        fontSize:      '12px',
        fontWeight:    'bold',
        display:       'flex',
        alignItems:    'center',
        justifyContent:'space-between',
        flexShrink:    0,
      }}>
        <span>ABASE Scientific Database Manager — [PLT-SCREEN-001]</span>
        <div style={{ display: 'flex', gap: '2px' }}>
          {['_', '□', '✕'].map(c => (
            <button key={c} style={{
              width:      '16px',
              height:     '14px',
              background: WIN_GRAY,
              border:     `1px solid ${WIN_DARK}`,
              fontSize:   '9px',
              lineHeight: '12px',
              cursor:     'default',
              padding:    0,
              fontFamily: FONT,
            }}>{c}</button>
          ))}
        </div>
      </div>

      {/* ── Classic Menu Bar ──────────────────────────────────────────────────
          Thin text menu. Each item has the first letter underlined
          to simulate Alt-key keyboard shortcuts (classic Windows convention).
      ── */}
      <div style={{
        background:   WIN_GRAY,
        borderBottom: `1px solid ${WIN_DARK}`,
        padding:      '1px 2px',
        display:      'flex',
        gap:          '2px',
        flexShrink:   0,
      }}>
        {[
          ['F', 'ile'],
          ['E', 'dit'],
          ['V', 'iew'],
          ['D', 'ata'],
          ['T', 'ransform'],
          ['A', 'nalyze'],
          ['G', 'raphs'],
          ['U', 'tilities'],
          ['H', 'elp'],
        ].map(([first, rest]) => (
          <button key={first + rest} style={{
            background:  'transparent',
            border:      '1px solid transparent',
            padding:     '1px 6px',
            fontSize:    '12px',
            fontFamily:  FONT,
            cursor:      'default',
            outline:     'none',
          }}
          onMouseEnter={e => {
            (e.target as HTMLElement).style.background = WIN_SEL
            ;(e.target as HTMLElement).style.color = WIN_WHITE
          }}
          onMouseLeave={e => {
            (e.target as HTMLElement).style.background = 'transparent'
            ;(e.target as HTMLElement).style.color = '#000'
          }}>
            <span style={{ textDecoration: 'underline' }}>{first}</span>{rest}
          </button>
        ))}
      </div>

      {/* ── Toolbar ───────────────────────────────────────────────────────────
          Raised 3D buttons with classic labels.
          Dividers separate logical groups (File ops | Edit ops | View ops).
      ── */}
      <div style={{
        background:   WIN_GRAY,
        borderBottom: `1px solid ${WIN_DARK}`,
        padding:      '3px 4px',
        display:      'flex',
        alignItems:   'center',
        gap:          '2px',
        flexShrink:   0,
      }}>
        <ToolbarButton label="New" />
        <ToolbarButton label="Open" />
        <ToolbarButton label="Save" />
        <ToolbarDivider />
        <ToolbarButton label="Print" />
        <ToolbarButton label="Print Preview" />
        <ToolbarDivider />
        <ToolbarButton label="Cut" />
        <ToolbarButton label="Copy" />
        <ToolbarButton label="Paste" />
        <ToolbarButton label="Undo" />
        <ToolbarDivider />
        <ToolbarButton label="Find..." />
        <ToolbarButton label="Sort Asc" />
        <ToolbarButton label="Sort Desc" />
        <ToolbarDivider />
        <ToolbarButton label="Export CSV" />
        <ToolbarButton label="Export XLS" />
        <ToolbarDivider />
        <ToolbarButton label="Filter" />
        <ToolbarButton label="Clear Filter" />

        {/* Right-side info label — common in SPSS */}
        <div style={{ marginLeft: 'auto', fontSize: '11px', color: '#333', paddingRight: '4px' }}>
          Rows: 25 of 5,000 | Filter: OFF
        </div>
      </div>

      {/* ── Secondary Toolbar (Formula / Query Bar) ───────────────────────────
          Simulates the SPSS "Go to case" / Excel formula bar.
      ── */}
      <div style={{
        background:   WIN_GRAY,
        borderBottom: `1px solid ${WIN_DARK}`,
        padding:      '2px 4px',
        display:      'flex',
        alignItems:   'center',
        gap:          '4px',
        flexShrink:   0,
      }}>
        <span style={{ fontSize: '11px', color: '#333', marginRight: '4px' }}>Go to record:</span>
        <input
          type="text"
          defaultValue="1"
          style={{
            width:        '40px',
            height:       '16px',
            border:       `1px inset ${WIN_DARK}`,
            fontFamily:   FONT,
            fontSize:     '11px',
            padding:      '0 2px',
            background:   WIN_WHITE,
            outline:      'none',
          }}
        />
        <ToolbarButton label="Go" />
        <div style={{ width: '1px', background: WIN_DARK, height: '16px', margin: '0 4px' }} />
        <span style={{ fontSize: '11px', color: '#333' }}>Query:</span>
        <input
          type="text"
          placeholder="SELECT * FROM abase_legacy_users WHERE..."
          style={{
            width:        '320px',
            height:       '16px',
            border:       `1px inset ${WIN_DARK}`,
            fontFamily:   FONT,
            fontSize:     '11px',
            padding:      '0 2px',
            background:   WIN_WHITE,
            color:        '#000080',
            outline:      'none',
          }}
        />
        <ToolbarButton label="Run" />
      </div>

      {/* ── Main Data Grid ────────────────────────────────────────────────────
          The core of the ABASE interface.
          - Fixed row-number column on the left (like Excel row numbers)
          - Sortable column headers (click to sort, shows ▲/▼)
          - Alternating row colors
          - Click a row to select it (highlights in Windows navy blue)
          - Horizontal + vertical scrolling
      ── */}
      <div style={{ flex: 1, overflow: 'auto', background: WIN_BG }}>
        <table style={{
          borderCollapse: 'collapse',
          width:          '100%',
          tableLayout:    'fixed',
        }}>
          <colgroup>
            {/* Row number column */}
            <col style={{ width: '38px' }} />
            {COLUMNS.map(c => <col key={c.key} style={{ width: c.width }} />)}
          </colgroup>

          <thead>
            <tr>
              {/* Row # header cell */}
              <th style={{
                ...thStyle(COLUMNS[0]),
                width:    '38px',
                minWidth: '38px',
                cursor:   'default',
              }}>
                #
              </th>

              {COLUMNS.map(col => (
                <th
                  key={col.key}
                  style={thStyle(col)}
                  onClick={() => handleSort(col.key)}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: col.align === 'right' ? 'flex-end' : 'space-between' }}>
                    <span>{col.label}</span>
                    {sortKey === col.key && (
                      <span style={{ marginLeft: '4px', fontSize: '9px' }}>
                        {sortDir === 'asc' ? '▲' : '▼'}
                      </span>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {sorted.map((row, i) => {
              const isSelected = selectedRow === row.rowNum
              const isAlt      = i % 2 === 1

              return (
                <tr
                  key={row.recordId}
                  onClick={() => setSelectedRow(isSelected ? null : row.rowNum)}
                  style={{ cursor: 'default' }}
                >
                  {/* Row number cell */}
                  <td style={{
                    ...tdStyle(isSelected, isAlt),
                    background:  isSelected ? WIN_SEL : WIN_GRAY,
                    color:       isSelected ? WIN_WHITE : WIN_DARK,
                    textAlign:   'right',
                    borderRight: `2px solid ${WIN_DARK}`,
                    fontWeight:  'bold',
                    fontSize:    '11px',
                    padding:     '1px 4px',
                  }}>
                    {row.rowNum}
                  </td>

                  <td style={tdStyle(isSelected, isAlt)}>{row.recordId}</td>
                  <td style={tdStyle(isSelected, isAlt)}>{row.scientist}</td>
                  <td style={tdStyle(isSelected, isAlt)}>{row.plate}</td>
                  <td style={tdStyle(isSelected, isAlt, 'center')}>{row.well}</td>
                  <td style={tdStyle(isSelected, isAlt, 'right')}>
                    {row.rawValue.toFixed(2)}
                  </td>
                  <td style={tdStyle(isSelected, isAlt)}>{row.timestamp}</td>
                </tr>
              )
            })}

            {/* Empty filler rows to fill the grid space (like SPSS blank rows) */}
            {Array.from({ length: 10 }).map((_, i) => (
              <tr key={`empty-${i}`}>
                <td style={{ ...tdStyle(false, i % 2 === 1), background: WIN_GRAY, color: WIN_DARK, textAlign: 'right', borderRight: `2px solid ${WIN_DARK}`, fontSize: '11px', fontWeight: 'bold', padding: '1px 4px' }}>
                  {25 + i + 1}
                </td>
                {COLUMNS.map(c => (
                  <td key={c.key} style={tdStyle(false, i % 2 === 1)}>&nbsp;</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Selected Row Info Bar ─────────────────────────────────────────────
          Sits just above the status bar. Shows details of the selected record.
          Common in SPSS — selecting a case shows its variable values inline.
      ── */}
      <div style={{
        background:    WIN_GRAY,
        borderTop:     `1px solid ${WIN_WHITE}`,
        borderBottom:  `1px solid ${WIN_DARK}`,
        padding:       '1px 6px',
        fontSize:      '11px',
        color:         '#000',
        flexShrink:    0,
        display:       'flex',
        gap:           '16px',
      }}>
        {selectedRow ? (() => {
          const r = DATA.find(d => d.rowNum === selectedRow)!
          return (
            <>
              <span><b>Selected:</b> {r.recordId}</span>
              <span><b>Scientist:</b> {r.scientist}</span>
              <span><b>Plate:</b> {r.plate}</span>
              <span><b>Well:</b> {r.well}</span>
              <span><b>Raw Value:</b> {r.rawValue.toFixed(4)}</span>
              <span><b>Timestamp:</b> {r.timestamp}</span>
            </>
          )
        })() : (
          <span style={{ color: WIN_DARK }}>No record selected. Click a row to inspect its values.</span>
        )}
      </div>

      {/* ── Status Bar ────────────────────────────────────────────────────────
          Classic desktop app status bar at the very bottom.
          Shows connection info, total records, active users.
          Divided into panes separated by inset borders (like Windows Explorer).
      ── */}
      <div style={{
        background:   WIN_GRAY,
        borderTop:    `2px solid ${WIN_DARK}`,
        padding:      '1px 0',
        display:      'flex',
        flexShrink:   0,
        height:       '20px',
        alignItems:   'center',
      }}>
        {[
          'Connected to ABASE-PROD',
          'Total Records: 5,000',
          'Showing: 25',
          'Active Users: 50',
          'Sort: ' + sortKey + ' ' + sortDir.toUpperCase(),
          selectedRow ? `Selected: REC-${String(selectedRow).padStart(3,'0')}` : 'Ready',
        ].map((text, i) => (
          <div key={i} style={{
            padding:     '0 8px',
            fontSize:    '11px',
            fontFamily:  FONT,
            borderRight: `1px solid ${WIN_DARK}`,
            borderLeft:  i === 0 ? `1px solid ${WIN_DARK}` : 'none',
            height:      '100%',
            display:     'flex',
            alignItems:  'center',
            whiteSpace:  'nowrap',
          }}>
            {text}
          </div>
        ))}
      </div>

    </div>
  )
}
