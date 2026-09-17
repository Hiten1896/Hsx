---
name: Precision Workstation Engine
colors:
  surface: '#131316'
  surface-dim: '#131316'
  surface-bright: '#39393c'
  surface-container-lowest: '#0e0e11'
  surface-container-low: '#1b1b1e'
  surface-container: '#1f1f22'
  surface-container-high: '#2a2a2d'
  surface-container-highest: '#353438'
  on-surface: '#e4e1e6'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#e4e1e6'
  inverse-on-surface: '#303033'
  outline: '#908fa0'
  outline-variant: '#464554'
  surface-tint: '#c0c1ff'
  primary: '#c0c1ff'
  on-primary: '#1000a9'
  primary-container: '#8083ff'
  on-primary-container: '#0d0096'
  inverse-primary: '#494bd6'
  secondary: '#89ceff'
  on-secondary: '#00344d'
  secondary-container: '#00a2e6'
  on-secondary-container: '#00344e'
  tertiary: '#4edea3'
  on-tertiary: '#003824'
  tertiary-container: '#00885d'
  on-tertiary-container: '#000703'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#c9e6ff'
  secondary-fixed-dim: '#89ceff'
  on-secondary-fixed: '#001e2f'
  on-secondary-fixed-variant: '#004c6e'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#131316'
  on-background: '#e4e1e6'
  surface-variant: '#353438'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  timecode:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.04em
  telemetry-xs:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.02em
  label-caps:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '600'
    lineHeight: 12px
    letterSpacing: 0.08em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1px
  margin: 0px
  space-xs: 0.125rem
  space-sm: 0.25rem
  space-md: 0.5rem
  space-lg: 0.75rem
  space-xl: 1rem
---

## Brand & Style

This design system targets technical operators, creative engineers, and elite developers demanding deep-focus workspace ergonomics. Drawing cues from high-performance IDEs, timeline editors, and operational telemetry dashboards, the aesthetic balances dense utility with surgical clarity.

The visual style is **Precision Utility Minimalism**:
- **Workstation-First**: Information-dense, zero decorative padding, strictly functional layout logic.
- **Instrument-Grade Contrast**: Neutral obsidian baseboards accented by surgical, self-illuminating status colors.
- **Sub-Pixel Discipline**: Structural hair-lines, crisp geometry, and strictly defined focal states prevent visual fatigue during marathon sessions.

## Colors

The color palette treats color strictly as operational telemetry rather than ornamentation. Surfaces are constructed on an ultra-dark slate/zinc spectrum that absorbs ambient distraction while elevating code, timelines, and inspector values.

- **Canvas & Obsidian**: `#09090b` forms the absolute ground plane.
- **Elevated Surfaces**: Nested paneling sits between `#0f172a` (slated viewport foundations) and `#18181b` (active panels and tool trays). Sub-panels and modals elevate to `#27272a`.
- **Primary Electric Indigo**: `#6366f1` drives focused inputs, active track headers, and primary commits. Hover transitions to `#4f46e5`.
- **Telemetry System**:
  - **Sky Blue (`#0ea5e9`)**: Transient operations, selections, inspection markers, and info callouts.
  - **Precision Emerald (`#10b981`)**: Running builds, committed frames, and non-blocking validations.
  - **Amber Warning (`#f59e0b`)**: Framedrops, cache renegotiations, and lint warnings.
  - **Crimson Error (`#ef4444`)**: Compilation breaks, clipping thresholds, and destructive flags.
- **Hairline Dividers**: Fixed at `#27272a` for 1px internal boundaries, moving to `#3f3f46` on interactive hover.

## Typography

Typography relies on a dual-engine pairing: **Inter** handles narrative structure, UI chrome, and contextual dialogs, while **JetBrains Mono** governs timecodes, telemetry readouts, numeric inputs, and code streams.

- **Tabular Precision**: `JetBrains Mono` provides tabular figures natively, ensuring zero jitter on real-time scrubbing, variable frame rates, and telemetry streaming.
- **Density Over Display**: Max headline sizes are deliberately capped (`24px`). Vertical real estate is prioritized for workspace viewports, code panes, and sequence timelines.
- **Micro-Labels**: All section titles and channel identifiers leverage uppercase tracked micro-labels (`label-caps`) to differentiate workspace architecture from editable content.

## Layout & Spacing

The workspace uses an edge-to-edge multi-panel layout model. There are zero outer margins (`0px`), and panels abut directly through structural `1px` hairlines (`#27272a`).

- **Internal Padding & Density**: Component padding adheres to an ultra-compact 4px/8px base rhythm (`space-xs` = 2px, `space-sm` = 4px, `space-md` = 8px).
- **Workspace Partitioning**:
  - **Left Rail (Activity / Assets)**: Fixed collapsible width (240px to 320px).
  - **Center Canvas (Viewer / Editor / Timeline)**: Flex layout. Viewport top, sequential multi-track timeline docked bottom with resizable split heights.
  - **Right Inspector (Telemetry / Node Attributes)**: Fixed collapsible width (280px to 340px).
- **Responsive Adaptations**:
  - **Desktop (>= 1440px)**: Full multi-pane tri-column workstation layout.
  - **Laptop / Tablet (768px - 1439px)**: Inspector and sidebars transition to sliding overlay drawers; the primary canvas retains full focus.
  - **Mobile (< 768px)**: Stacked single-panel interface. Navigation shifts to a persistent bottom selector strip with swipeable tabs.

## Elevation & Depth

This design system rejects deep drop shadows in favor of **structural paneling and luminous depth**:

- **Tonal Layers**:
  - `Base`: `#09090b` (Host application container & canvas).
  - `Level 1`: `#0f172a` & `#18181b` (Toolbars, docks, inspector backdrops).
  - `Level 2`: `#27272a` (Card rows, input surfaces, inactive track blocks).
  - `Level 3`: `#3f3f46` (Active drag-states, pinned nodes, floating command palettes).
- **Hairline Ghost Borders**: Panels and modules are demarcated by 1px solid borders (`#27272a`). No ambient drop shadows are applied to docked elements.
- **Floating Overlays & Menus**: Command bars (e.g., Quick Actions `Cmd+K`) and context flyouts utilize a subtle outer glow with a sharp boundary: `box-shadow: 0 0 0 1px #3f3f46, 0 12px 32px -4px rgba(0, 0, 0, 0.7)`.

## Shapes

The interface embraces an engineered, industrial geometric profile. Soft corners (`roundedness: 1`, base `4px` / `0.25rem`) keep nested panels looking sharp and structurally contiguous.

- **Panels & Canvas Tiles**: Hard corners (`0px`) along docked edges; internal embedded elements use `4px` corner radii.
- **Controls & Input Fields**: Uniform `4px` radius across input boxes, status chips, timeline playheads, and tool buttons.
- **Scrubber Handles & Pointers**: Sharp triangular geometry or `2px` micro-radii to maintain high-precision pixel alignment on timeline rulers.

## Components

### Buttons & Tool Selectors
- **Primary**: Indigo fill (`#6366f1`), crisp white text, 0 0 12px `#6366f140` glow on focus. Height `28px` for dense layouts.
- **Ghost Tool Buttons**: Transparent background, `#a1a1aa` icon/label, active toggle transitions to `#27272a` with an `#e4e4e7` foreground.
- **Split Buttons**: Primary action joined to a dropdown trigger by a 1px border (`#4f46e5`).

### Inputs & Number Scrubbers
- **Numeric Fields**: JetBrains Mono, `#18181b` surface, hairline border (`#27272a`). Clicking and dragging horizontally scrubs values.
- **Focus Rings**: Strict 1px inset indigo border (`#6366f1`) without external blur rings that obscure neighboring data.

### Timeline Controls & Scrubber Bar
- **Ruler Track**: `#09090b` background with 1px vertical interval tick marks (`#27272a`).
- **Playhead**: 1px solid needle in Indigo (`#6366f1`) with an inverted trapezoid grab handle (`#6366f1`).
- **Clips / Audio Blocks**: Rounded `3px` container blocks with a 1px inner border. Emerald (`#10b981`), Sky Blue (`#0ea5e9`), and Indigo (`#6366f1`) represent clip categories.

### Status Indicators & Badges
- **Pill Tags**: Monospaced 11px uppercase label, solid dark background (`#18181b`), 1px border matching state, accompanied by a 4px circular LED pulse dot.
- **Telemetry Readout**: 2-column key-value rows. Left key in `#71717a` Inter; right value in `#fafafa` JetBrains Mono.

### Lists & Tree Views
- **File & Asset Trees**: 24px height per item, hover highlight `#18181b`, active item highlighted with a 2px left border accent in `#6366f1`. Indentation governed by 12px steps with 1px vertical guide lines.