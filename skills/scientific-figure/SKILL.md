---
name: scientific-figure
description: >
  Create publication-quality scientific figures, plots, and data visualizations.
  Trigger when the user asks to plot data, create a figure, generate a chart for
  a paper or talk, visualize experimental or simulation results, or improve an
  existing figure. Also trigger when the user mentions matplotlib, pgfplots,
  seaborn, plotly, or any scientific plotting library. Applies to sensitivity
  curves, noise spectra, Allan deviations, error bar plots, heatmaps, phase
  diagrams, or any scientific data visualization.
argument-hint: '"<description of desired figure>"'
allowed-tools:
  - Bash
  - Read
  - Write
  - Glob
  - Grep
---

# /scientific-figure — Scientific Figures

A figure is not a data dump — it is a **graphical interface between people and
data**. Treat every figure as a UI/UX design problem: you have a user (the
reader), a context (journal, slide deck, poster), and a message to transmit
with maximum signal-to-noise ratio.

Above all else, show the data.

These principles are distilled from Rougier, Droettboom & Bourne, "Ten Simple
Rules for Better Figures" (PLoS Comput. Biol. 2014) and Tufte's principles of
graphical excellence. The matplotlib configuration uses a custom AFP
institutional color palette with perceptually optimized ordering.

The user's input is: $ARGUMENTS

**Bundled resources** (read on demand):
- `references/color-palette.md` — Full qualitative cycle, AFP colormaps,
  shade families, semantic shorthand, seaborn fallbacks
- `references/presentation-override.md` — Slide and poster rcParams overrides
- `scripts/register_colormaps.py` — Register all AFP colormaps with matplotlib

---

## Core Principles

Apply these in order. Each principle constrains downstream decisions.

### 1. Audience First

Before writing a single line of plotting code, determine who will read the
figure. This drives *every* subsequent choice.

| Audience | Design implications |
|---|---|
| Self / collaborators | Dense is fine; shared context assumed |
| Journal reviewers & readers | Self-explanatory; complete axis labels, units, legends |
| Oral presentation | Simplified; thick lines, large fonts, high contrast, no fine detail |
| General public | Single salient message; approximation acceptable; minimal jargon |

When the user doesn't specify, **default to journal-quality**: self-contained,
fully labeled, and readable at single-column width (~3.3 in / 84 mm for Nature).

### 2. One Figure, One Message

Identify the single core message before choosing a plot type. The message
drives everything: which data to include, what to emphasize, what to suppress.
If a figure tries to say two things, it says neither clearly — split it.

Ask yourself (or the user): *"What should the reader conclude at first glance?"*

### 3. Match the Medium

Never reuse the same figure across media without adaptation.

**For papers (long viewing time):**
- Fine detail is acceptable
- Use thin lines (0.5-1 pt), font sizes 7 pt (Nature standard)
- Exploit captions for explanation
- Insets and zoomed panels add value

**For slides (time-limited, viewed from distance):**
- Strip to essentials — fewer curves, fewer ticks, no fine structure
- Thicken everything: lines >= 1.5 pt, fonts >= 14 pt, markers >= 6 pt
- High-contrast colors on dark or light backgrounds
- No vertical text; minimize legend boxes (label curves directly)
- Add visual anchors (colored boxes, dashed reference lines)
- For slide/poster rcParams, read `references/presentation-override.md`

**For posters:**
- Intermediate between paper and slide
- Must be readable at ~1 m distance

### 4. Captions Are Mandatory

Always generate a caption suggestion alongside the figure. A good caption:
- Explains *how to read* the figure (what axes mean, what colors encode)
- Points out the key takeaway
- Provides numerical precision that the graphic cannot convey
- Anticipates reader questions

### 5. Never Trust Defaults — Direct Labeling over Legends

Default settings in any plotting library are generic compromises. The style
block below replaces them with a curated baseline. Beyond that, always verify:

- **Tick density**: Reduce to the minimum needed.
- **Font sizes**: Scale to the medium (7 pt for Nature, >=14 pt for slides).
- **Line widths & marker sizes**: 1.0 pt data, 0.5 pt axes in journal figures.
- **Figure size & aspect ratio**: Set explicitly for the target column width.
- **Legend placement**: Prefer direct annotation on curves over a separate
  legend box. Legends force an eye-travel penalty (data -> legend -> back).
  Place labels at the end of each curve:
  ```python
  ax.text(x[-1], y[-1], "  Series A", va="center", fontsize=7,
          color=line.get_color())
  ```
  For dense plots with overlapping endpoints, use the `adjustText` library
  for automated label placement. Fall back to a legend only when direct
  annotation would create clutter worse than the lookup cost.

### 6. Use Color with Intent

Color is the most powerful — and most abused — visual channel.

**Color assignment strategy (priority order):**
1. **Semantic mapping**: If the data has natural color associations, use them.
   Override the cycle explicitly (e.g. red/blue for hot/cold).
2. **Cycle through the palette**: When no semantics exist, use the AFP color
   cycle in order. The first 6 colors have good mutual contrast.
3. **Highlight by exception**: One colored series among many; rest in light
   gray (`#d0d0d0`).

**Colormap defaults (all AFP maps are CIELAB-linearized):**
- **Sequential**: `viridis` for maximum perceptual uniformity, or AFP
  alternatives (`afp_blue`, `afp_bluegreen`) for brand consistency. All AFP
  sequential maps have uniform L* ramps (Delta-L* RMS < 0.5).
- **Diverging**: `afp_KbOr` (kobalt<->orange, 144 deg hue separation under
  deuteranopia — best CVD safety). Multi-hue options: `afp_BgRo`
  (bluegreen<->redorange) for rich hue variation. Avoid `afp_RdGn` and
  `afp_GnOr` (both fail CVD).
- **Categorical**: AFP qualitative cycle (default) or seaborn `colorblind`.
- **Never use `jet` / `rainbow`** for continuous data.
- See `references/color-palette.md` for the full set (28 maps x 2 with
  reversed `_r` variants), selection guide, and CVD analysis.

To register all AFP colormaps with matplotlib, run:
```python
import sys, os
skill_dir = os.path.dirname(os.path.abspath(__file__))  # adjust as needed
sys.path.insert(0, os.path.join(skill_dir, "scripts"))
import register_colormaps; register_colormaps.register_all()
```

**Color-blind safety (~8% of males):**
- Avoid red-green as the sole distinguishing pair.
- Supplement qualitative colors with line style or marker shape.
- For full palette hex values, seaborn fallbacks, and CVD analysis, read
  `references/color-palette.md`.

**When there are many (>4) overlapping series**: Ask the user which strategy
to use — small multiples (see below), highlight-by-exception, or full cycle with
legend. Do not assume a default; the best choice depends on the message.

### 7. Do Not Mislead

Show data variation, not design variation. Visual differences should come from
the data, never from gratuitous design choices (varying fonts, line styles for
no reason, inconsistent scales).

**Lie Factor** (Tufte): the ratio of the size of an effect shown in the
graphic to the size of the effect in the data. Honest graphics have a Lie
Factor near 1.0; anything outside 0.95-1.05 warrants scrutiny. Check this
whenever encoding value as visual area, length, or angle.

**Common traps to actively avoid:**
- **Truncated axes**: Bar charts must start at zero. Line plots may truncate
  if zero is meaningless, but label clearly.
- **Area vs. radius**: Map value -> **area**, not value -> radius. The `s`
  parameter in `scatter()` is already points-squared, so pass values directly.
- **Pie charts**: Avoid entirely in scientific contexts. Use bar or dot plots.
- **3D charts**: Avoid for quantitative comparison. Use only for genuine 3D
  data (surface plots of 2D fields).
- **Dual y-axes**: Use with extreme caution. Clearly label and color-code.
- **Aspect ratio manipulation**: Choose ratios that honestly represent
  variability.

### 8. Eliminate Chartjunk — Layering & Separation

Every visual element must earn its place. Remove anything that doesn't carry
information: colored backgrounds, decorative gridlines, redundant labels,
excessive tick marks, 3D effects on 2D data.

**Data density** (Tufte): entries in the data matrix divided by the area of
the graphic. Most published figures are sparse (<10 entries/cm-squared); the best
achieve thousands. After removing chartjunk, ask whether you can *add*
information without adding clutter.

**The 1+1=3 problem** (Tufte, via Albers): two visual elements create a third
perceptual artifact at their boundary. When that artifact is noise rather than
information, the design fails. Actionable in matplotlib:
- Muted gridlines: `color="#d0d0d0"`, `linewidth=0.3`, `zorder=0`
- Reference elements behind data: set `zorder` explicitly
- Confidence bands: `alpha=0.15-0.25`, same color as the parent line
- Spine color lighter than data lines: `ax.spines[...].set_color("#999999")`

### 9. Message Over Aesthetics

Scientific figures serve communication first. Beauty is welcome but never at
the expense of clarity. Follow domain conventions where they exist.

### 10. Use the Right Tool

| Tool | Best for |
|---|---|
| **matplotlib** | Publication-quality 2D; full control; Python ecosystem |
| **seaborn** | Statistical plots with sane defaults on top of matplotlib |
| **pgfplots / TikZ** | LaTeX-native; perfect font matching; programmatic |
| **plotly** | Interactive exploration; web dashboards |
| **R / ggplot2** | Statistical graphics with grammar-of-graphics approach |

When generating code, prefer matplotlib unless the user specifies otherwise.

### 11. Small Multiples

Tufte's most powerful technique for complex data. Repeat the same plot frame
across a grid, varying one parameter per panel. The reader's eye learns the
frame once and then reads the data across panels.

**When to prefer small multiples over overlaid curves:**
- More than 4 series that would overlap or create spaghetti
- The message is "each behaves differently" rather than "compare these two"
- Different panels represent different experimental conditions or time windows

**Implementation:**
```python
fig, axes = plt.subplots(nrows, ncols, sharex=True, sharey=True,
                          figsize=(ncols*2.5, nrows*2))
fig.align_ylabels()  # critical for honest visual comparison
```
Shared axes are essential — without them the panels lie about relative
magnitudes. Label only the outer axes to minimize ink. Add a single shared
colorbar or legend outside the grid if needed.

### 12. Micro/Macro Readings

The best graphics work at two scales simultaneously (Tufte, *Envisioning
Information*). At the macro level the reader grasps the overall pattern; at
the micro level they can extract individual data points or values.

Design for both:
- **Macro**: overall line shape, trend direction, dominant features
- **Micro**: `ax.annotate()` for key points (minima, crossings, outliers),
  `ax.inset_axes()` for zoomed regions, minor ticks for interpolation
- Choose line widths and marker sizes that serve both scales — thick enough
  to see trends, thin enough to resolve close points

### 13. Sparklines

Tufte's invention (*Beautiful Evidence*): tiny, word-sized graphics that live
inline with text or inside table cells. A sparkline conveys a trend, range,
or distribution in the space of a word — no axes, no labels, just the data
shape. Underused in scientific papers, but powerful for comparison tables
(e.g., noise spectra across sensor variants, time-series thumbnails in a
parameter sweep).

**Implementation:**
```python
fig, ax = plt.subplots(figsize=(1.5, 0.3))
ax.plot(x, y, color="#2e2cb8", linewidth=0.8)
ax.fill_between(x, y, alpha=0.1, color="#2e2cb8")
ax.axis("off")
fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
fig.savefig("sparkline.png", dpi=300, transparent=True)
```
For embedding in LaTeX tables, save as tight PNGs or PDFs and use
`\includegraphics[height=1em]{sparkline.png}`. For multi-row comparison
tables, ensure all sparklines share the same y-limits so visual heights are
comparable across rows.

---

## Implementation Checklist

Before delivering any figure, verify:

- [ ] Message identifiable at first glance
- [ ] Axes labeled with quantities *and* units
- [ ] Font sizes appropriate for target medium
- [ ] Colors are semantically meaningful where possible
- [ ] Colormap perceptually appropriate (viridis for sequential, no rainbow)
- [ ] Color-blind accessible (or supplemented with line styles / markers)
- [ ] No misleading axis ranges (bar charts start at zero)
- [ ] Chartjunk removed (unnecessary gridlines, backgrounds, decorations)
- [ ] No 1+1=3 artifacts (grid behind data, muted spines, transparent bands)
- [ ] Tick density minimal but sufficient
- [ ] Legend clean or replaced by direct annotation
- [ ] Minor ticks visible on both axes (science style convention)
- [ ] Caption drafted with reading instructions and key takeaway
- [ ] **Revision pass**: Can I remove any element without losing information?
  Can I add any element that increases information density?

---

## Base Style Configuration

This block merges `scienceplots` styles `science + nature + no-latex` into a
standalone `rcParams.update()` requiring **no external dependencies** beyond
matplotlib. The line color cycle uses the AFP institutional palette.

Apply at the top of every plotting script. Adjust `figure.figsize` for
double-column (6.6 in) or presentation as needed.

```python
import matplotlib.pyplot as plt

plt.rcParams.update({
    # --- Figure geometry ---
    "figure.figsize": (3.3, 2.5),
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,

    # --- Font: Nature requires sans-serif ---
    "font.family": "sans-serif",
    "font.sans-serif": [
        "DejaVu Sans", "Arial", "Helvetica",
        "Lucida Grande", "Verdana", "sans-serif",
    ],
    "font.size": 7,
    "axes.labelsize": 9,
    "axes.titlesize": 7,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,

    # --- Math text (no LaTeX dependency) ---
    "text.usetex": False,
    "axes.formatter.use_mathtext": True,
    "mathtext.fontset": "dejavusans",

    # --- Axes & spines ---
    "axes.linewidth": 0.5,

    # --- Ticks: inward, visible on all sides, minor ticks on ---
    "xtick.direction": "in",
    "xtick.major.size": 3,
    "xtick.major.width": 0.5,
    "xtick.minor.size": 1.5,
    "xtick.minor.width": 0.5,
    "xtick.minor.visible": True,
    "xtick.top": True,

    "ytick.direction": "in",
    "ytick.major.size": 3,
    "ytick.major.width": 0.5,
    "ytick.minor.size": 1.5,
    "ytick.minor.width": 0.5,
    "ytick.minor.visible": True,
    "ytick.right": True,

    # --- Lines & markers ---
    "lines.linewidth": 1.0,
    "lines.markersize": 3,
    "grid.linewidth": 0.5,
    "axes.grid": False,

    # --- Legend ---
    "legend.frameon": False,

    # --- Color cycle: AFP palette (10 colors) ---
    # Ordered for warm/cool alternation; min adjacent delta-E = 98 (CIEDE76).
    "axes.prop_cycle": plt.cycler("color", [
        "#2e2cb8",  # C0 blue
        "#db002b",  # C1 red
        "#1f8a70",  # C2 green/teal
        "#fd7400",  # C3 orange
        "#1c809e",  # C4 blueberry
        "#bedb43",  # C5 limegreen
        "#9b2f5c",  # C6 pink
        "#fabd1e",  # C7 gold
        "#5c2d99",  # C8 violet
        "#2c6b2f",  # C9 forest
    ]),

    # --- Default colormap ---
    "image.cmap": "viridis",
})
```
