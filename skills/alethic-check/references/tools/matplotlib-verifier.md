### Visual Verification with Matplotlib

Matplotlib is available for visual spot-checks. Use it when a plot can reveal errors that algebra alone might miss:

- **Function behavior**: Plot claimed solutions, asymptotic forms, and boundary conditions to visually confirm they match
- **Comparison plots**: Overlay analytic solutions with numerical solutions (e.g., from `scipy.integrate.solve_ivp`) to check agreement
- **Singularity detection**: Plot functions near claimed poles or branch points to verify behavior
- **Convergence visualization**: Plot partial sums vs claimed closed form to verify convergence claims
- **Phase portraits**: For dynamical systems, plot phase portraits to verify qualitative behavior claims

**IMPORTANT**: Always use the Agg backend to avoid display issues:
```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

Save all plots to files in the worklog directory:
```python
plt.savefig("{worklog_path}/check_{description}.png", dpi=150, bbox_inches="tight")
plt.close()
```

Visual checks are supplementary --- they strengthen confidence but do not replace algebraic or numerical verification. A visual discrepancy should prompt more rigorous analytical investigation.
