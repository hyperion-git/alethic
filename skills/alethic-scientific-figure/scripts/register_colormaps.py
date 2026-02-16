"""
AFP Colormaps — CIELAB-linearized institutional colormaps.

Derived from AFP LaTeX template cshade families. Each colormap preserves
the original hue/chroma character but has been resampled at uniform CIE L*
increments via PCHIP interpolation in CIELAB space. Diverging maps use
symmetric arms meeting at a neutral-white midpoint (a*=b*=0).

Usage:
    import register_colormaps
    register_colormaps.register_all()

    plt.imshow(data, cmap='afp_blue')         # sequential
    plt.imshow(residuals, cmap='afp_KbOr')    # diverging
    plt.imshow(residuals, cmap='afp_BgRo')    # multi-hue diverging
    plt.imshow(data, cmap='afp_blue_r')       # reversed

All colormaps are registered with matplotlib's colormap registry.
Reversed variants (suffix '_r') are registered automatically.
Requires: numpy, scipy (PchipInterpolator), matplotlib.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from scipy.interpolate import PchipInterpolator, interp1d


# =====================================================================
# sRGB <-> CIELAB (self-contained, no colour-science dependency)
# =====================================================================

_M_RGB2XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)
_D65 = np.array([0.95047, 1.0, 1.08883])


def _srgb_to_linear(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def _f_lab(t):
    d = 6 / 29
    return np.where(t > d**3, t ** (1 / 3), t / (3 * d**2) + 4 / 29)


def _f_lab_inv(t):
    d = 6 / 29
    return np.where(t > d, t**3, 3 * d**2 * (t - 4 / 29))


def _rgb_to_lab(rgb):
    lin = _srgb_to_linear(np.asarray(rgb, dtype=float))
    f = _f_lab(lin @ _M_RGB2XYZ.T / _D65)
    return np.stack(
        [116 * f[..., 1] - 16,
         500 * (f[..., 0] - f[..., 1]),
         200 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )


def _lab_to_rgb(lab):
    lab = np.asarray(lab, dtype=float)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116
    xyz = np.stack(
        [_f_lab_inv(a / 500 + fy), _f_lab_inv(fy), _f_lab_inv(fy - b / 200)],
        axis=-1,
    ) * _D65
    return np.clip(_linear_to_srgb(np.clip(xyz @ _M_XYZ2RGB.T, 0, None)), 0, 1)


def _hex_to_rgb(h):
    return np.array([int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)])


# =====================================================================
# Linearization engine
# =====================================================================

def _linearize_sequential(hex_list, N=256, target_L_range=None):
    """Resample a hex color list at uniform CIE L* increments."""
    rgbs = np.array([_hex_to_rgb(h) for h in hex_list])
    labs = _rgb_to_lab(rgbs)
    L = labs[:, 0].copy()
    for i in range(1, len(L)):
        if L[i] <= L[i - 1]:
            L[i] = L[i - 1] + 0.01
    ia = PchipInterpolator(L, labs[:, 1])
    ib = PchipInterpolator(L, labs[:, 2])
    Lmin, Lmax = (L[0], L[-1]) if target_L_range is None else target_L_range
    Lu = np.linspace(Lmin, Lmax, N)
    return _lab_to_rgb(np.stack([Lu, ia(Lu), ib(Lu)], axis=-1))


def _linearize_diverging(left_hexes, right_hexes, mid_hex="#f5f5f5", N=256):
    """
    Perceptually linear diverging colormap.
    Each arm has an explicit neutral-white anchor at the midpoint
    (a*=b*=0). Arms are depth-matched in L* for symmetry.
    """
    mid_lab = _rgb_to_lab(_hex_to_rgb(mid_hex))
    L_mid = mid_lab[0]
    n_half = N // 2

    def _linearize_arm(hexes):
        rgbs = np.array([_hex_to_rgb(h) for h in hexes])
        labs = _rgb_to_lab(rgbs)
        L = labs[:, 0].copy()
        for i in range(1, len(L)):
            if L[i] <= L[i - 1]:
                L[i] = L[i - 1] + 0.01
        # Neutral-white anchor at midpoint
        L = np.append(L, L_mid)
        a = np.append(labs[:, 1], 0.0)
        b = np.append(labs[:, 2], 0.0)
        ia = PchipInterpolator(L, a)
        ib = PchipInterpolator(L, b)
        Lu = np.linspace(L[0], L_mid, n_half)
        return _lab_to_rgb(np.stack([Lu, ia(Lu), ib(Lu)], axis=-1))

    left_lin = _linearize_arm(left_hexes)
    right_lin = _linearize_arm(right_hexes)

    left_L = _rgb_to_lab(left_lin)[:, 0]
    right_L = _rgb_to_lab(right_lin)[:, 0]
    L_dark = max(left_L[0], right_L[0])

    def _trim_and_resample(arm_rgb, arm_L, L_floor, n_out):
        mask = arm_L >= L_floor - 0.5
        trimmed = arm_rgb[mask]
        t_in = np.linspace(0, 1, len(trimmed))
        t_out = np.linspace(0, 1, n_out)
        return np.column_stack(
            [interp1d(t_in, trimmed[:, c])(t_out) for c in range(3)]
        )

    left_rs = _trim_and_resample(left_lin, left_L, L_dark, n_half)
    right_rs = _trim_and_resample(right_lin, right_L, L_dark, n_half)
    return np.vstack([left_rs, right_rs[::-1]])


# =====================================================================
# AFP color data
# =====================================================================

_SHADES = {
    "red":    ["#490006", "#650008", "#96030f", "#ca0011", "#f64756", "#f9baba"],
    "orange": ["#491600", "#763100", "#c65300", "#ff7715", "#f7ab6a", "#fbd0ab"],
    "yellow": ["#4f3a02", "#9b6f03", "#dfa204", "#fabd1e", "#fcd169", "#fce5ad"],
    "green":  ["#0c2b0e", "#1d4825", "#2c6b2f", "#3a9300", "#84d700", "#c6f46f"],
    "blue":   ["#16193b", "#35478c", "#4e7ac7", "#7fb2f0", "#add5f7", "#cce5fa"],
    "purple": ["#25064d", "#36175e", "#553285", "#7b52ab", "#9768d1", "#bb92ef"],
    "pink":   ["#351a23", "#532131", "#84274f", "#cc559c", "#dd99cc", "#efbee1"],
    "kobalt": ["#000c59", "#1806a0", "#0d2cd3", "#425fef", "#a8d5f5", "#dbeffa"],
}

_GRAYS = {
    "neutral": ["#323232", "#464646", "#656565", "#939393", "#c1c1c1", "#eeeeee"],
    "warm":    ["#34312e", "#46423e", "#635d58", "#938b83", "#b4aaa0", "#ede0d3"],
    "cold":    ["#2c3032", "#3d4346", "#5a6165", "#808d93", "#9aa9b1", "#cbdee8"],
    "green":   ["#2d322f", "#3e4642", "#5a655f", "#83938b", "#acc1b6", "#d3ede0"],
}

_GRADIENTS = {
    "bluegreen": [
        "#0b2559", "#183b59", "#2a5159", "#327355",
        "#5c8c46", "#afc76a", "#d9e19a",
    ],
    "redorange": [
        "#620d13", "#7b1524", "#9a2121", "#bf391f",
        "#d96518", "#e9925b", "#f1d6b5",
    ],
}

# Diverging: (left_arm_key, right_arm_key, source_left, source_right)
# source is 'shade' or 'gradient' to look up in _SHADES or _GRADIENTS
_DIVERGING = {
    # Classic shade-based
    "RdBu": ("red",       "blue",      "shade", "shade"),
    "BuRd": ("blue",      "red",       "shade", "shade"),
    "PuOr": ("purple",    "orange",    "shade", "shade"),
    "KbOr": ("kobalt",    "orange",    "shade", "shade"),  # best CVD
    "PkBu": ("pink",      "blue",      "shade", "shade"),
    "RdGn": ("red",       "green",     "shade", "shade"),  # NOT CVD-safe
    "GnPu": ("green",     "purple",    "shade", "shade"),
    "YlPu": ("yellow",    "purple",    "shade", "shade"),
    "GnOr": ("green",     "orange",    "shade", "shade"),  # NOT CVD-safe
    # Multi-hue arms (gradient <-> gradient or gradient <-> shade)
    "BgRo": ("bluegreen", "redorange", "grad",  "grad"),   # flagship
    "BgRd": ("bluegreen", "red",       "grad",  "shade"),
    "BgOr": ("bluegreen", "orange",    "grad",  "shade"),
    "RoKb": ("redorange", "kobalt",    "grad",  "shade"),
    "RoBl": ("redorange", "blue",      "grad",  "shade"),
}


def _get_hexes(key, source):
    """Look up hex list from shade or gradient source."""
    if source == "shade":
        return _SHADES[key]
    elif source == "grad":
        return _GRADIENTS[key]
    raise ValueError(f"Unknown source: {source}")


# =====================================================================
# Public API
# =====================================================================

def register_all():
    """Register all CIELAB-linearized AFP colormaps with matplotlib."""
    cmaps = {}

    # Sequential single-hue
    for key, hexes in _SHADES.items():
        name = f"afp_{key}"
        cmaps[name] = mcolors.LinearSegmentedColormap.from_list(
            name, _linearize_sequential(hexes), N=256)

    # Sequential grays
    for key, hexes in _GRAYS.items():
        name = f"afp_gray_{key}"
        cmaps[name] = mcolors.LinearSegmentedColormap.from_list(
            name, _linearize_sequential(hexes), N=256)

    # Multi-hue sequential
    for key, hexes in _GRADIENTS.items():
        name = f"afp_{key}"
        cmaps[name] = mcolors.LinearSegmentedColormap.from_list(
            name, _linearize_sequential(hexes), N=256)

    # Diverging
    for key, (lk, rk, ls, rs) in _DIVERGING.items():
        name = f"afp_{key}"
        left_h = _get_hexes(lk, ls)
        right_h = _get_hexes(rk, rs)
        cmaps[name] = mcolors.LinearSegmentedColormap.from_list(
            name, _linearize_diverging(left_h, right_h), N=256)

    # Register all + reversed variants
    for name, cmap in cmaps.items():
        try:
            plt.colormaps.register(cmap, name=name)
            plt.colormaps.register(cmap.reversed(), name=f"{name}_r")
        except ValueError:
            pass  # already registered

    return list(cmaps.keys())


def list_colormaps():
    """Print categorized summary of all AFP colormaps."""
    seq_s = len(_SHADES)
    seq_g = len(_GRAYS)
    seq_m = len(_GRADIENTS)
    div_n = len(_DIVERGING)
    total = seq_s + seq_g + seq_m + div_n

    print("AFP Colormaps (CIELAB-linearized)")
    print("=" * 60)

    print(f"\nSequential single-hue ({seq_s}):")
    for k in _SHADES:
        print(f"  afp_{k}")

    print(f"\nSequential grays ({seq_g}):")
    for k in _GRAYS:
        print(f"  afp_gray_{k}")

    print(f"\nSequential multi-hue ({seq_m}):")
    for k in _GRADIENTS:
        print(f"  afp_{k}")

    print(f"\nDiverging ({div_n}):")
    for k, (lk, rk, ls, rs) in _DIVERGING.items():
        flags = []
        if k in ("RdGn", "GnOr"):
            flags.append("[!] NOT CVD-safe")
        if k == "KbOr":
            flags.append("[*] best CVD safety")
        if ls == "grad" or rs == "grad":
            flags.append("multi-hue")
        flag_str = "  " + ", ".join(flags) if flags else ""
        print(f"  afp_{k:5s} ({lk} <-> {rk}){flag_str}")

    print(f"\nAll maps available reversed with '_r' suffix.")
    print(f"Total: {total} colormaps x 2 = {total * 2} (incl. reversed)")


if __name__ == "__main__":
    registered = register_all()
    list_colormaps()
    print(f"\nRegistered {len(registered)} colormaps "
          f"({len(registered) * 2} incl. reversed).")
