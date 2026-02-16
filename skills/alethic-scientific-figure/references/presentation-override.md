# Presentation Override

Apply this block **after** the base configuration when creating figures for
slides. It overrides font sizes, line weights, and switches to the brighter
AFP palette variant for projection visibility.

```python
# Override for presentation slides
plt.rcParams.update({
    "figure.figsize": (10, 6),
    "font.size": 16,
    "axes.labelsize": 18,
    "axes.titlesize": 18,
    "xtick.labelsize": 14,
    "ytick.labelsize": 14,
    "legend.fontsize": 14,
    "lines.linewidth": 2.0,
    "lines.markersize": 8,
    "axes.linewidth": 1.0,
    "xtick.major.size": 5,
    "xtick.major.width": 1.0,
    "ytick.major.size": 5,
    "ytick.major.width": 1.0,
    "savefig.dpi": 150,
    # Brighter AFP palette for projection visibility
    "axes.prop_cycle": plt.cycler("color", [
        "#3e36de",  # kobalt-medium (brighter blue)
        "#db002b",  # red (already vivid)
        "#009b4e",  # pastel-emerald-medium (brighter teal)
        "#fd7400",  # orange (already vivid)
        "#1b9aaa",  # pastel-cyan-medium (brighter blueberry)
        "#bedb43",  # limegreen (already bright)
        "#cd5789",  # pastel-pink-medium (brighter pink)
        "#fabd1e",  # gold (already bright)
        "#8a57cd",  # pastel-violet-medium (brighter violet)
        "#30c13f",  # afp-LightGreen (brighter forest)
    ]),
})
```

## Poster Override

Intermediate between paper and slides. Readable at ~1 m distance.

```python
# Override for posters (A0/A1)
plt.rcParams.update({
    "figure.figsize": (8, 5),
    "font.size": 12,
    "axes.labelsize": 14,
    "axes.titlesize": 14,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "legend.fontsize": 11,
    "lines.linewidth": 1.5,
    "lines.markersize": 6,
    "axes.linewidth": 0.8,
    "savefig.dpi": 300,
})
```
