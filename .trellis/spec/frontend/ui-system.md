# UI System

## Styling Boundary

`static/ui-system.css` is the semantic UI layer and is loaded after the compatibility styles. New visual rules belong there in this order: tokens, foundations, components, page patterns and responsive rules. Do not add a fourth append-only override file.

New templates consume existing semantic classes and variables. Raw colors belong only in the light/dark token maps; components use roles such as `--color-primary`, `--color-surface`, `--color-text-muted`, `--color-success`, `--color-warning` and `--color-danger`.

```css
/* Correct */
.status-note { color: var(--color-warning); background: var(--color-warning-soft); }

/* Wrong: theme-specific values leak into a component */
.status-note { color: #a8550b; background: #fff7e6; }
```

## Component Hierarchy

- Each screen or visible region has one filled primary action. Secondary choices use tonal, outline or text treatment.
- Broad button rules must use `:where(...)` so structural buttons can override them without `!important`.
- Icon-only buttons need an accessible name and a 44px hit area. Navigation icons use the shared inline SVG macro and consistent stroke width.
- Cards are reserved for bounded tasks, forms, dialogs and focused content. Ordinary statistics use sections, dividers and whitespace.
- Desktop management views may use compact tables; 390px views use structured record lists and hide the table.

> **Specificity gotcha**: a selector such as `button:not(.icon-button):not(...)` has enough specificity to turn knowledge-tree toggles and secondary buttons into primary CTAs. Wrap the generic selector in `:where()` and add explicit structural-button styles.

## Navigation And Responsive Contracts

- Desktop navigation groups are `学习`, `内容` and `系统`; optional module links remain controlled by `current_modules`.
- Mobile bottom navigation contains exactly five items: today, practice, review, progress and more. The more sheet updates `aria-expanded`, moves focus inside, closes with Escape and restores focus.
- Expanded sidebar width is 252px and collapsed width is 72px. The collapsed custom-property override must appear after root tokens or token cascade order will keep the wide rail.
- Mobile fixed navigation reserves bottom safe-area padding. Inputs use at least 16px text and interactive controls are at least 44px high.
- Dense charts and flex/grid children must set `min-width: 0`; mobile trend columns reduce their minimum width so 30-day charts do not widen the document.

## Theme And Accessibility Contracts

- Default theme remains `system`; explicit `light` and `dark` token maps must be designed and checked independently.
- Filled dark-theme buttons use `--color-action`, not the lighter link/accent token, so white text remains at least 4.5:1.
- Focus indicators remain visible for links, controls, dialogs and custom interactive rows.
- Status never relies on color alone; include readable text, icon, shape or label.
- Motion is limited to 150–200ms state transitions and disabled by `prefers-reduced-motion`.

## Knowledge Tree And Management Card Contracts

- Knowledge-tree subjects, chapters and points use one nested outlined-card family. Depth is expressed with indentation, guide lines and the radius sequence `13px → 11px → 9px`, not unrelated row/card treatments.
- All three knowledge levels use the shared inline SVG helper and a `32px` tonal icon marker. The semantic icon set is book for subject, document list for chapter and connected nodes for point.
- Visual refactors must preserve every `data-tree-*` hook, node ID, form field and ARIA expansion attribute; styling must not duplicate or replace the tree interaction logic.
- A project card separates its statistic strip from direct actions with `var(--space-6)` (`24px`). Keep the existing action-button gap and wrapping behavior.

```css
.knowledge-level-mark { width: 32px; height: 32px; }
.knowledge-subject { border-radius: var(--radius-md); }
.knowledge-chapter { border-radius: 11px; }
.knowledge-point { border-radius: var(--radius-sm); }
.project-card > .actions { margin-top: var(--space-6); }
```

## Required Tests

- Template contract: grouped navigation, skip link, mobile sheet control and exactly one dashboard primary action.
- Browser QA: all main routes at 390px report `scrollWidth <= clientWidth`; verify 1440px sidebar expand/collapse and both themes.
- Contrast assertions: body, muted copy and filled primary button must meet WCAG AA in light and dark themes.
- Portable smoke: the archive contains `static/ui-system.css`, starts with an empty database and passes `/health`.
- Knowledge/project regression: assert all three level icons render, nested card radii remain ordered and the project statistics-to-actions gap computes to `24px` at desktop and mobile widths.
