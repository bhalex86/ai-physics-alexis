"""
THIS WAS GENERATED USING CLAUDE SONNET 4.6
Quantum Harmonic Oscillator
============================
Computes and plots the first five energy levels and normalized
wavefunctions of the quantum harmonic oscillator.

Functions
---------
energy_levels  – Returns E_n = hbar * (n + 0.5) * omega for n = 0..N-1
harmonic_state – Returns the normalized wavefunction psi_n(x) using
                 scipy.special.hermite (the physics convention)
main           – Plots |psi_n(x)|^2 for n = 0..4 on a shared axis

Tests (run with: pytest quantum_harmonic_oscillator.py)
-------------------------------------------------------
test_energy_formula        – E_n formula correctness
test_indexing_energy       – ground-state indexing (n=0)
test_library_validity      – no hallucinated scipy functions
test_normalization         – integral of |psi|^2 ≈ 1
test_probability_graphing  – main() plots |psi|^2 not psi
"""

import math
import numpy as np
import scipy.integrate
import scipy.special
import matplotlib
matplotlib.use("Agg")          # non-interactive backend (safe for tests)
import matplotlib.pyplot as plt
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Core physics functions
# ─────────────────────────────────────────────────────────────────────────────

def energy_levels(hbar: float = 1.0, omega: float = 1.0, n_levels: int = 5) -> np.ndarray:
    """Return the first *n_levels* energy eigenvalues of the QHO.

    E_n = hbar * omega * (n + 0.5),  n = 0, 1, …, n_levels-1

    Parameters
    ----------
    hbar    : reduced Planck constant (default 1.0 for natural units)
    omega   : angular frequency      (default 1.0 for natural units)
    n_levels: number of levels to compute (includes n=0 ground state)

    Returns
    -------
    np.ndarray of shape (n_levels,) with E_0, E_1, …, E_{n_levels-1}
    """
    n = np.arange(n_levels)
    return hbar * omega * (n + 0.5)


def harmonic_state(n: int, x: np.ndarray,
                   hbar: float = 1.0,
                   m: float = 1.0,
                   omega: float = 1.0) -> np.ndarray:
    """Return the normalized QHO wavefunction psi_n(x) in natural units.

    Uses the physics-convention Hermite polynomials via
    ``scipy.special.hermite``.

    psi_n(x) = (1 / sqrt(2^n * n!)) * (m*omega / (pi*hbar))^(1/4)
               * exp(-m*omega*x^2 / (2*hbar)) * H_n(sqrt(m*omega/hbar) * x)

    Parameters
    ----------
    n    : quantum number (0 = ground state)
    x    : position array
    hbar : reduced Planck constant
    m    : particle mass
    omega: angular frequency

    Returns
    -------
    np.ndarray of same shape as *x* containing real psi_n values
    """
    # Characteristic length scale
    xi = np.sqrt(m * omega / hbar) * x          # dimensionless position

    # Physicist's Hermite polynomial H_n (not the probabilist's version)
    Hn = scipy.special.hermite(n)               # returns a numpy poly1d object

    # Normalization prefactor
    norm = (1.0 / np.sqrt(2**n * float(math.factorial(n)))) * \
           (m * omega / (np.pi * hbar)) ** 0.25

    psi = norm * np.exp(-xi**2 / 2.0) * Hn(xi)
    return psi


# ─────────────────────────────────────────────────────────────────────────────
# Main – plot probability densities
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Plot |psi_n(x)|^2 for n = 0 … 4 on a single axes object."""
    x = np.linspace(-5, 5, 600)
    levels = energy_levels(n_levels=5)

    fig, ax = plt.subplots(figsize=(9, 6))

    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    for n in range(5):
        psi  = harmonic_state(n, x)
        prob = np.abs(psi) ** 2          # probability density

        ax.plot(x, prob, color=colors[n], linewidth=2,
                label=rf"$n={n}$,  $E_{n}={levels[n]:.2f}$")

    ax.set_xlabel("Position $x$", fontsize=13)
    ax.set_ylabel(r"Probability density $|\psi_n(x)|^2$", fontsize=13)
    ax.set_title("QHO: Probability Density of First Five Energy Levels", fontsize=14)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("qho_probability_density.png", dpi=150)
    print("Plot saved → qho_probability_density.png")
    return fig, ax


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_energy_formula():
    """Test that energy_levels computes E_n = hbar*(n+0.5)*omega correctly."""
    result   = energy_levels(hbar=1.0, omega=1.0, n_levels=3)
    expected = np.array([0.5, 1.5, 2.5])
    np.testing.assert_allclose(result, expected)


def test_indexing_energy():
    """Test that energy_levels computed E_n are indexed correctly to include the ground state."""
    result   = energy_levels(hbar=1.0, omega=1.0, n_levels=1)
    expected = 0.5
    np.testing.assert_allclose(result, expected)


def test_library_validity():
    """Test to make sure the existing proper library is being called into the function."""
    try:
        x   = np.array([0.0, 1.0, 2.0])
        psi = harmonic_state(0, x)
    except AttributeError as e:
        pytest.fail(f"Bug detected: harmonic_state calls hallucinated function: {e}")


def test_normalization():
    """Test to verify that the wave function is normalized."""
    x = np.linspace(-3, 3, 100)
    for n in range(3):
        psi         = harmonic_state(n, x)
        prob_density = psi ** 2
        integral    = scipy.integrate.trapezoid(prob_density, x)
        np.testing.assert_allclose(integral, 1.0, atol=0.01)


def test_probability_graphing():
    # this test was refined and generated using Claude
    """Test to verify that the probability density is what is being plotted/calculated rather than state."""
    x      = np.linspace(-5, 5, 600)
    states = np.array([harmonic_state(n, x) for n in range(5)])

    main()  # Run main (buggy or fixed)

    # Extract plotted data
    ax    = plt.gca()
    lines = ax.get_lines()
    for i, line in enumerate(lines):
        y_plotted  = line.get_ydata()
        y_expected = np.abs(states[i]) ** 2
        np.testing.assert_allclose(y_plotted, y_expected)

    plt.close()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
    plt.show()
