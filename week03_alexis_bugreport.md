# Week 03 Bug Report
**File:** `ai_generated_oscillator.py`
This document was generated on Claud from a google document chart written by the user for formatting purposes
---

## Bug #1 — Wrong Physics Formula
**Location:** Line 9

**What the AI produced and why it's wrong:**
The AI produced a formula for the energy eigenvalues which states that `En = ℏ·n·ω`. However, this formula is incorrect and completely ignores the ground energy state. The true value of the energy eigenvalues of a quantum harmonic oscillator is:

```
En = ℏ·(n + 0.5)·ω
```

**Citation:** Zettili, N. (2009). *Quantum Mechanics: Concepts and Applications* (3rd ed., p. 266, eq. 4.126). Wiley.

---

## Bug #2 — Off-by-One Error
**Location:** Line 21

**What the AI produced and why it's wrong:**
The AI produced a set of states indexed starting from 1 to 5. However, this indexing causes a complete disregard for the ground energy state. It is better to simply remove this line and proceed with line 20 as the final return value.

---

## Bug #3 — Hallucinated API
**Location:** Line 13

**What the AI produced and why it's wrong:**
As shown by the error message, the function `special.hermite_poly(n, x)` does not exist. The correct function to generate the Hermite polynomial at specific points is:

```python
special.eval_hermite(n, x)
```

**Citation:** SciPy developers. (n.d.). `scipy.special.eval_hermite`. In *SciPy Documentation*. https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.eval_hermite.html

---

## Bug #4 — Incorrect Normalization
**Location:** Line 14

**What the AI produced and why it's wrong:**
The normalization provided by the AI is incorrect for a Hermite polynomial. The norm, which was set as:

```python
1.0 / np.sqrt(math.factorial(n))
```

needs to be replaced by:

```python
1.0 / np.sqrt(2**n * math.factorial(n) * np.sqrt(np.pi))
```

**Citation:** Zettili, N. (2009). *Quantum Mechanics: Concepts and Applications* (3rd ed., p. 267, eq. 4.127). Wiley.

---

## Bug #5 — Wrong Output Quantity
**Location:** Line 26

**What the AI produced and why it's wrong:**
The plotting that the AI provided was ψ against x, when it was supposed to plot the probability density against x. The formula for probability density is the absolute value of ψ squared:

```
|ψ|²
```

**Citation:** Zettili, N. (2009). *Quantum Mechanics: Concepts and Applications* (3rd ed., p. 189, eq. 3.8). Wiley.
