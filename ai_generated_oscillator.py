"""AI-generated style starter file with intentional bugs for homework."""
import math
import numpy as np
import matplotlib.pyplot as plt
import scipy
from scipy import special


def energy_levels(hbar=1.0, omega=1.0, n_levels=5):
    return np.array([hbar * omega * n for n in range(n_levels)])  # BUG 1return np.arraynp.array([(hbar + 0.5)* omega * n for n in range(n_levels)])

def harmonic_state(n, x):
    herm = special.hermite_poly(n, x)  # BUG 3 Replace with special.eval_hermite(n, x)  
    norm = 1.0 / np.sqrt(math.factorial(n))  # BUG 4 Replace with 1.0 / np.sqrt(2**n * math.factorial(n) * np.sqrt(np.pi))
    return norm * np.exp(-x**2 / 2) * herm


def main():
    x = np.linspace(-5, 5, 600)
    states = np.array([harmonic_state(n, x) for n in range(5)])
    states = states[1:]  # BUG 2 # FIX REMOVE THIS LINE 

    print("First five E_n:", energy_levels())
    plt.figure(figsize=(8, 5))
    for n, psi in enumerate(states):
        plt.plot(x, psi, label=f"n={n}")  # BUG 5 This is plotting psi not the probability density 
    plt.title("Buggy AI oscillator output")
    plt.xlabel("x")
    plt.ylabel("Probability density")
    plt.legend()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()

    
def test_energy_formula():
    """Test that energy_levels computes E_n = hbar*(n+0.5)*omega correctly."""
    result = energy_levels(hbar=1.0, omega=1.0, n_levels=3)
    expected = np.array([0.5, 1.5, 2.5])
    np.testing.assert_allclose(result, expected)

def test_indexing_energy():
    """Test that energy_levels computed E_n are indexed correctly to include the ground state """
    result = energy_levels(hbar=1.0, omega=1.0, n_levels=1)
    expected = 0.5
    np.testing.assert_allclose(result, expected)

def test_library_validity():
    """ Test to make sure the existing proper library is being called into the function"""
    try:
        x = np.array([0.0, 1.0, 2.0])
        psi = harmonic_state(0, x)
    except AttributeError as e:
            pytest.fail(f"Bug detected: harmonic_state calls hallucinated function: {e}")
        
def test_normalization():
    """ Test to verify that the wave function is normalized """
    x = np.linspace(-3, 3, 100)
    for n in range(3):
        psi = harmonic_state(n, x)
        prob_density = psi**2
        integral = scipy.integrate.trapezoid(prob_density, x)
        np.testing.assert_allclose(integral, 1.0, atol=0.01)
 
def test_probability_graphing():
    #  this test was refined and generated using Claude 
    """ test to verify that the probability density is what is being plotted/calculated rather than state."""
    x = np.linspace(-5, 5, 600)
    states = np.array([harmonic_state(n, x) for n in range(5)])
    
    main()  # Run main (buggy or fixed)
    # Extract plotted data
    ax = plt.gca()
    lines = ax.get_lines()
    for i, line in enumerate(lines):
        y_plotted = line.get_ydata()
        y_expected = np.abs(states[i])**2
        np.testing.assert_allclose(y_plotted, y_expected)
    
    plt.close()  