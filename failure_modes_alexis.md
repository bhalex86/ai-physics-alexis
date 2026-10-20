# Failure Modes Alexis

| ID  | Layer attacked       | Trigger                                              |
|-----|----------------------|------------------------------------------------------|
| FM1 | Max results request  | Maximum results requested is outside of the MCP range |
| FM2 | arXiv keyword search | An ambiguous keyword                                 |

---

## FM1 — Max results outside the accepted range

**Call**

```python
async with mcp_session() as session:
    raw = await session.call_tool("search_arxiv", {"keyword": "Betelgeuse", "max_results": -300})
    _, direct_text, direct_is_error = unpack_tool_result(raw)
```

**Observed output**

```
is_error: True
Error executing tool search_arxiv: 1 validation error for search_arxivArguments
max_results
  Input should be greater than or equal to 1 [type=greater_than_equal, input_value=-300, input_type=int]
```

**What went wrong**

`max_results` is outside the accepted range, so the tool rejects the call at argument validation and no search runs.

**Proposed fix**

If a request for `max_results` is outside the accepted range, choose a random number within the accepted values and run the search from there.

---

## FM2 — Ambiguous or misspelled input

### Case A: Ambiguous property ("same property as the Sun")

**Query:** `list all the stars with the same property as Sun.`

**Observed behaviour** (2 LLM turns, 10 tool calls)

- The agent noted the request was ambiguous and chose its own interpretation: the Sun's spectral type, **G2V**.
- `query_simbad("Sun")` returned `found: false` — SIMBAD has no object matching "Sun".
- The agent then looked up a hand-picked set of known solar twins/analogs instead of producing a full list (SIMBAD only resolves one named object at a time).

| Star                   | SIMBAD spectral type | Distance (from parallax) | Notes                                   |
|------------------------|----------------------|--------------------------|-----------------------------------------|
| α Centauri A           | G2V                  | ~1.35 pc (4.4 ly)        | Exact spectral-type match               |
| 18 Scorpii             | G2Va                 | ~14.1 pc (46 ly)         | Exact match; often called best solar twin |
| HD 76151               | G2V                  | ~16.8 pc (55 ly)         | Exact match                             |
| 16 Cygni A             | G1.5Vb               | ~21.1 pc (69 ly)         | Very close match; binary system         |
| HIP 102152 (HD 197027) | G3V                  | ~78 pc (255 ly)          | Close match                             |
| HD 143436              | G3V                  | ~44.6 pc (146 ly)        | Close match                             |
| β Hydri                | G0V                  | ~7.5 pc (24 ly)          | Slightly hotter and older than the Sun  |

HD 195034 and HIP 56948 (HD 101364) are listed in SIMBAD only as "G5", too coarse to confirm a match.

**What went wrong**

- "All the stars" cannot be satisfied with the available tools; the answer is a curated sample, not a list.
- The "property" was chosen by the agent (spectral type) rather than confirmed with the user. It did flag the assumption and offered alternatives (planets, mass, age, magnetic cycle).

### Case B: Misspelled object name ("Betelzeus")

**Query:** `Parallax of Betelzeus. return only the value`

**Observed behaviour** (2 LLM turns, 1 tool call)

- The agent silently rewrote the name and called `query_simbad("Betelgeuse")`.
- SIMBAD returned α Ori: parallax **6.55 ± 0.83 mas**.
- Final answer: `6.55 mas`

**What went wrong**

The agent did not report that the returned values do not correspond to "Betelzeus". It automatically applied an autocorrect where it may not be applicable, and the "return only the value" instruction hid the substitution entirely.

**Proposed fix**

Add a function that checks whether the queried object name matches the requested name character for character (case-insensitive). On a mismatch, report the substitution to the user instead of returning the value silently.
