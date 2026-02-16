# Color Palette Reference

Complete reference for all AFP-derived colors: qualitative cycle, colormaps,
semantic assignments, and fallback palettes.

## Table of Contents

1. [Qualitative Cycle (10 colors)](#qualitative-cycle)
2. [Semantic Color Shorthand](#semantic-shorthand)
3. [AFP Colormaps](#afp-colormaps)
4. [Colormap Selection Guide](#colormap-selection-guide)
5. [Seaborn Fallback Palettes](#seaborn-fallbacks)
6. [Full AFP Shade Families](#shade-families)

---

## Qualitative Cycle

Default `axes.prop_cycle` — 10 colors, warm/cool alternation, min adjacent
ΔE = 98 (CIEDE76).

| Pos | Name      | Hex       | Source                          |
|-----|-----------|-----------|----------------------------------|
| C0  | blue      | `#2e2cb8` | afp-cset-blue                    |
| C1  | red       | `#db002b` | afp-cset-red                     |
| C2  | green     | `#1f8a70` | afp-cset-green                   |
| C3  | orange    | `#fd7400` | afp-cset-orange                  |
| C4  | blueberry | `#1c809e` | afp-cset-blueberry               |
| C5  | limegreen | `#bedb43` | afp-cset-limegreen               |
| C6  | pink      | `#9b2f5c` | afp-theme-pastel-pink-dark       |
| C7  | gold      | `#fabd1e` | afp-cshade-yellow-4              |
| C8  | violet    | `#5c2d99` | afp-theme-pastel-violet-dark     |
| C9  | forest    | `#2c6b2f` | afp-cshade-green-3               |

**Reserve colors** (not in default cycle):

| Name      | Hex       | Note                                    |
|-----------|-----------|-----------------------------------------|
| turquoise | `#004358` | Too dark for lines on white; dark bg    |
| yellow    | `#ffe119` | Invisible on white; fills or dark bg    |

### Brighter Presentation Variant

For slides projected at distance. Used in the presentation override block.

| Pos | Name      | Hex       | Source                    |
|-----|-----------|-----------|---------------------------|
| C0  | blue      | `#3e36de` | pastel-kobalt-medium      |
| C1  | red       | `#db002b` | (same)                    |
| C2  | green     | `#009b4e` | pastel-emerald-medium     |
| C3  | orange    | `#fd7400` | (same)                    |
| C4  | blueberry | `#1b9aaa` | pastel-cyan-medium        |
| C5  | limegreen | `#bedb43` | (same)                    |
| C6  | pink      | `#cd5789` | pastel-pink-medium        |
| C7  | gold      | `#fabd1e` | (same)                    |
| C8  | violet    | `#8a57cd` | pastel-violet-medium      |
| C9  | forest    | `#30c13f` | afp-LightGreen            |

---

## Semantic Shorthand

Common semantic assignments using AFP palette colors:

```python
COLORS = {
    "cold":      "#2e2cb8",  # blue (C0)
    "hot":       "#db002b",  # red (C1)
    "reference": "#919191",  # afp gray (pastel-gray-medium)
    "growth":    "#1f8a70",  # teal-green (C2)
    "warning":   "#fd7400",  # orange (C3)
    "highlight": "#5c2d99",  # violet (C8)
}
```

**Usage:**
```python
ax.plot(x, T_hot,  color="#db002b", label="Hot reservoir")
ax.plot(x, T_cold, color="#2e2cb8", label="Cold reservoir")
ax.axhline(T_ref,  color="#919191", ls="--", lw=0.5, label="Reference")
```

---

## AFP Colormaps (CIELAB-linearized)

All colormaps are derived from AFP cshade families and resampled at uniform
CIE L* increments via PCHIP interpolation in CIELAB space. This eliminates the
L* kinks present in the raw hex-stop interpolation while preserving each map's
hue/chroma character. Diverging maps have symmetric arms meeting at a
neutral-white midpoint (a\*=b\*=0, L\*≈97).

Register with matplotlib via:

```python
import sys; sys.path.insert(0, "<skill-path>/scripts")
import register_colormaps
register_colormaps.register_all()  # registers 28 maps + 28 reversed = 56 total
```

All maps are available reversed with `_r` suffix (e.g. `afp_blue_r`,
`afp_KbOr_r`).

### Sequential — single hue (8 maps)

Source: `afp-cshade-{color}` 6-stop families → PCHIP in Lab → uniform L\*.

| Name         | Character                    | L\* range | ΔL\* RMS | CVD safe |
|--------------|------------------------------|-----------|----------|----------|
| `afp_blue`   | Navy → sky blue              | 10–90     | 0.00     | ✓        |
| `afp_kobalt` | Deep cobalt → electric → ice | 9–93      | 0.04     | ✓        |
| `afp_red`    | Oxblood → crimson → blush    | 12–81     | 0.44     | ✓        |
| `afp_orange` | Burnt sienna → peach         | 15–86     | 0.12     | ✓        |
| `afp_yellow` | Dark amber → cream           | 26–92     | 0.00     | ✓        |
| `afp_green`  | Forest → chartreuse          | 15–91     | 0.02     | ✓        |
| `afp_purple` | Deep indigo → lavender       | 10–68     | 0.00     | ✓        |
| `afp_pink`   | Dark plum → rose             | 13–82     | 0.00     | ✓        |

Residual ΔL\* RMS > 0 comes from sRGB gamut clipping in high-chroma regions
(unavoidable without desaturation).

### Sequential — multi-hue (2 maps)

Source: `afp-theme-bluegreen` and `afp-theme-redorange` 7-stop families.

| Name            | Character                      | L\* range | ΔL\* RMS | CVD safe |
|-----------------|--------------------------------|-----------|----------|----------|
| `afp_bluegreen` | Deep navy → teal → chartreuse  | 16–88     | 0.00     | ✓        |
| `afp_redorange` | Dark crimson → orange → cream  | 20–87     | 0.00     | ✓        |

### Sequential — grays (4 maps)

Source: `afp-cshade-gray-{variant}` 6-stop families. Useful for grayscale
print, background fills, or inactive elements.

| Name               | Character          | L\* range | ΔL\* RMS |
|--------------------|--------------------|-----------|----------|
| `afp_gray_neutral` | Pure neutral gray  | 21–94     | 0.00     |
| `afp_gray_warm`    | Warm brownish gray | 21–90     | 0.00     |
| `afp_gray_cold`    | Cool bluish gray   | 20–87     | 0.00     |
| `afp_gray_green`   | Sage / green-gray  | 20–92     | 0.00     |

### Diverging — shade-based (9 maps)

Two single-hue arms meeting at neutral white (a\*=b\*=0). Each arm is
L\*-linearized independently; arms are depth-matched for symmetry.

| Name        | Left arm  | Right arm | Δhue (deut.) | CVD safe |
|-------------|-----------|-----------|--------------|----------|
| `afp_RdBu`  | red       | blue      | 126°         | ✓        |
| `afp_BuRd`  | blue      | red       | 126°         | ✓        |
| `afp_PuOr`  | purple    | orange    | 132°         | ✓        |
| `afp_KbOr`  | kobalt    | orange    | 144°         | ✓ ★ best |
| `afp_PkBu`  | pink      | blue      | 47°          | ✓        |
| `afp_GnPu`  | green     | purple    | 118°         | ✓        |
| `afp_YlPu`  | yellow    | purple    | 127°         | ✓        |
| `afp_RdGn`  | red       | green     | 12°          | ✗ ⚠      |
| `afp_GnOr`  | green     | orange    | 16°          | ✗ ⚠      |

### Diverging — multi-hue (5 maps)

One or both arms use multi-hue gradients (bluegreen, redorange), giving
richer hue variation along each arm. Particularly effective for data where
readers need to estimate magnitude from color alone.

| Name        | Left arm    | Right arm  | Δhue (deut.) | CVD safe |
|-------------|-------------|------------|--------------|----------|
| `afp_BgRo`  | bluegreen   | redorange  | 122°         | ✓        |
| `afp_BgRd`  | bluegreen   | red        | 132°         | ✓        |
| `afp_BgOr`  | bluegreen   | orange     | 134°         | ✓        |
| `afp_RoKb`  | redorange   | kobalt     | 138°         | ✓        |
| `afp_RoBl`  | redorange   | blue       | 124°         | ✓        |

`afp_BgRo` is the flagship multi-hue diverging map — each arm sweeps through
multiple hues on the way to dark, giving a topographic-map aesthetic with
strong discriminability at every value level.

**Summary:** 28 colormaps total (14 sequential + 14 diverging) × 2 = 56
registered names including reversed variants.

---

## Colormap Selection Guide

| Data type                    | Default recommendation          | AFP alternative                    |
|-----------------------------|---------------------------------|------------------------------------|
| Sequential (continuous)      | `viridis`                       | `afp_bluegreen`, `afp_blue`       |
| Sequential (temperature)     | —                               | `afp_redorange`                    |
| Sequential (grayscale print) | `Greys`                         | `afp_gray_neutral`                 |
| Diverging (general)          | `coolwarm`                      | `afp_RdBu`, `afp_BgRo`            |
| Diverging (CVD-safe)         | `coolwarm`                      | `afp_KbOr` ★                      |
| Diverging (multi-hue, rich)  | —                               | `afp_BgRo`, `afp_RoKb`            |
| Categorical (≤10)            | AFP qualitative cycle           | —                                  |
| Fill / confidence bands      | —                               | pastel triplets (see §Shade Families) |
| Background wash              | —                               | `afp_gray_warm`                    |

**When to use AFP colormaps vs. matplotlib defaults:**

- **Use AFP** for institutional branding, visual consistency across
  publications, or when the on-brand color identity matters. The linearized
  AFP maps now have comparable L\* uniformity to viridis (ΔL\* RMS < 0.5
  for most maps).
- **Use viridis/coolwarm** when you need a well-known standard that reviewers
  won't question, or when multi-hue perceptual encoding (viridis has 3+ hues)
  would help distinguish subtle gradients in a single-hue AFP map.

---

## Seaborn Fallback Palettes

Alternative qualitative palettes when you need a different aesthetic or
maximum color-blind safety. All have 10 colors.

| Palette        | Character                    | Best for                        |
|----------------|------------------------------|---------------------------------|
| **muted**      | Softer, lower saturation     | Many categories, low visual load|
| **bright**     | High saturation, vivid       | Presentations, posters          |
| **dark**       | Low luminance, rich          | Dark backgrounds, serious tone  |
| **pastel**     | High luminance, gentle       | Secondary data, fills, bands    |
| **colorblind** | Optimized for CVD            | Maximum accessibility           |

First 6 hex values per palette:

| Palette        | Colors                                                                      |
|----------------|-----------------------------------------------------------------------------|
| **muted**      | `#4878d0` `#ee854a` `#6acc64` `#d65f5f` `#956cb4` `#8c613c`               |
| **bright**     | `#023eff` `#ff7c00` `#1ac938` `#e8000b` `#8b2be2` `#9f4800`               |
| **dark**       | `#001c7f` `#b1400d` `#12711c` `#8c0800` `#591e71` `#592f0d`               |
| **pastel**     | `#a1c9f4` `#ffb482` `#8de5a1` `#ff9f9b` `#d0bbff` `#debb9b`               |
| **colorblind** | `#0173b2` `#de8f05` `#029e73` `#d55e00` `#cc78bc` `#ca9161`               |

---

## Shade Families

Complete hex values for all AFP cshade families (source data for colormaps).
Steps numbered 1 (darkest) → 6 (lightest).

### Chromatic shades

| Family | 1         | 2         | 3         | 4         | 5         | 6         |
|--------|-----------|-----------|-----------|-----------|-----------|-----------|
| red    | `#490006` | `#650008` | `#96030f` | `#ca0011` | `#f64756` | `#f9baba` |
| orange | `#491600` | `#763100` | `#c65300` | `#ff7715` | `#f7ab6a` | `#fbd0ab` |
| yellow | `#4f3a02` | `#9b6f03` | `#dfa204` | `#fabd1e` | `#fcd169` | `#fce5ad` |
| green  | `#0c2b0e` | `#1d4825` | `#2c6b2f` | `#3a9300` | `#84d700` | `#c6f46f` |
| blue   | `#16193b` | `#35478c` | `#4e7ac7` | `#7fb2f0` | `#add5f7` | `#cce5fa` |
| purple | `#25064d` | `#36175e` | `#553285` | `#7b52ab` | `#9768d1` | `#bb92ef` |
| pink   | `#351a23` | `#532131` | `#84274f` | `#cc559c` | `#dd99cc` | `#efbee1` |
| kobalt | `#000c59` | `#1806a0` | `#0d2cd3` | `#425fef` | `#a8d5f5` | `#dbeffa` |

### Gray shades

| Family  | 1         | 2         | 3         | 4         | 5         | 6         |
|---------|-----------|-----------|-----------|-----------|-----------|-----------|
| neutral | `#323232` | `#464646` | `#656565` | `#939393` | `#c1c1c1` | `#eeeeee` |
| warm    | `#34312e` | `#46423e` | `#635d58` | `#938b83` | `#b4aaa0` | `#ede0d3` |
| cold    | `#2c3032` | `#3d4346` | `#5a6165` | `#808d93` | `#9aa9b1` | `#cbdee8` |
| green   | `#2d322f` | `#3e4642` | `#5a655f` | `#83938b` | `#acc1b6` | `#d3ede0` |

### Multi-hue gradients (7 stops)

| Family    | 1         | 2         | 3         | 4         | 5         | 6         | 7         |
|-----------|-----------|-----------|-----------|-----------|-----------|-----------|-----------|
| bluegreen | `#0b2559` | `#183b59` | `#2a5159` | `#327355` | `#5c8c46` | `#afc76a` | `#d9e19a` |
| redorange | `#620d13` | `#7b1524` | `#9a2121` | `#bf391f` | `#d96518` | `#e9925b` | `#f1d6b5` |

### Pastel triplets (dark / medium / light)

| Family  | Dark      | Medium    | Light     |
|---------|-----------|-----------|-----------|
| emerald | `#096637` | `#009b4e` | `#56cd84` |
| lime    | `#476609` | `#608e05` | `#9cce38` |
| yellow  | `#c18711` | `#ecac2d` | `#ffd265` |
| orange  | `#bf5c06` | `#f9943b` | `#ffb678` |
| red     | `#b11e13` | `#e83e34` | `#fd7d7d` |
| pink    | `#9b2f5c` | `#cd5789` | `#f891bd` |
| violet  | `#5c2d99` | `#8a57cd` | `#c191f8` |
| kobalt  | `#1c1896` | `#3e36de` | `#a9a6ff` |
| blue    | `#005694` | `#137fcc` | `#b2d5fc` |
| cyan    | `#005f6a` | `#1b9aaa` | `#89e9f6` |
| gray    | `#4b4b4b` | `#919191` | `#bebebe` |
