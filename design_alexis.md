# MCP server design: `mcp_server_alexis.py`

**Project:** Testing a dust-polarization estimator of magnetic-field inclination in public MHD simulations (PHYS 7440, B. Alexis). **Server name:** `cats-tilt` · Python `mcp` SDK 2.x (`MCPServer`) · stdio transport.

## (a) The two core tool endpoints

| | `cats_catalog` | `estimate_tilt_from_cubes` |
|---|---|---|
| **Purpose** | List what exists in the public CATS archive (runs, snapshots, files) before downloading. | Estimate the field tilt γ from simulation cubes with Chen et al. (2019)'s method, with the true tilt, an uncertainty and a comparison with Chen. |
| **(d) Data source** | **External API:** the CATS archive (Burkhart et al. 2020, ApJ 905, 14), HTTPS directory listings at [`users.flatironinstitute.org/~bburkhart/data/CATS/`](https://www.mhdturbulence.com/) . | **Dataset:** CATS simulation cubes (Cho-ENO FITS; AREPO HDF5 regridded to FITS) read from disk; `results.db` (SQLite, this project's graded results) for the calibration part of the uncertainty. |

Eleven supporting endpoints share the same conventions: `inspect_fits`, `estimate_tilt_from_stokes`, `list_runs`, `run_properties`, `load_cube`, `make_map`, `estimate_tilt`, `lookup_result`, `grade_run`, `get_call_log`, and `read_source`, which lets the LLM read the physics code and the Chen equation each function implements.

## (b) inputSchema

`cats_catalog`
```json
{"type": "object", "properties": {"folder": {"type": "string", "default": "MHD/256/",
  "pattern": "^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*/$", "maxLength": 200,
  "description": "A folder of the CATS archive, relative to its root and ending in '/'"}}}
```
`estimate_tilt_from_cubes`
```json
{"type": "object", "required": ["density", "bx", "by", "bz"], "properties": {
  "density": {"type": "string", "description": "3-D gas density cube: path to a FITS file ..."},
  "bx": {"type": "string"}, "by": {"type": "string"}, "bz": {"type": "string"},
  "line_of_sight": {"type": "string", "enum": ["z", "y", "x", "tilted"], "default": "z"},
  "angle": {"type": "number", "minimum": 0, "maximum": 90, "default": 0.0},
  "pmax_percentile": {"type": "number", "minimum": 90, "maximum": 100, "default": 100.0}}}
```
(Every property carries a `description` in the published schema; shortened here.)

## (c) Output format

Every reply is a JSON object (MCP `structuredContent`) beginning with `verification_id` (e.g. `V-05ae-001-6cfe0d43642a`) and `call_number`. Arrays are never returned: maps are summarized.

- `cats_catalog` → `{url, folders: [str], files: [str], n_entries, retrieved_at}`
- `estimate_tilt_from_cubes` → `{tilt_deg_low_S, tilt_deg_all_pixels, true_tilt_deg, uncertainty_deg_low_S` (flat copies for single-key clients such as the course `eval_harness.py`)`, input_kind, view, pmax_percentile, estimate_deg: {all_pixels, low_S}, true_tilt_gamma_B_deg, error_deg, map_statistics, field_label_checks, uncertainty: {low_S: {estimate_deg, components_deg: {pmax_rule_sensitivity, estimator_spread_all_vs_lowS, project_calibration_median_abs_err}, rough_combined_deg, project_calibration}, all_pixels: {...}, comparison_with_chen_2019: {citation, quoted_accuracy, table_A1_low_S_error_deg, nearest_tabulated_tilt_deg, error_at_that_tilt_deg}, caveats: [str]}, warnings?: [str]}`

## Error handling

- **Wrong-type or missing arguments** are rejected by the SDK against the inputSchema before the tool runs.
- **Every other failure** returns `is_error: true` with `{"error": {"code, message, tool, verification_id"}}`. Codes: `INVALID_INPUT`, `UNKNOWN_RUN`, `PATH_NOT_ALLOWED`, `FILE_NOT_FOUND`, `NO_DATA`, `NOT_FOUND` (HTTP 404), `TIMEOUT`, `NETWORK_ERROR`, `UPSTREAM_ERROR`, `SERVER_NOT_CONFIGURED`, `UNKNOWN_NAME`, `INTERNAL_ERROR`. Unexpected exceptions are caught and reported as `INTERNAL_ERROR`; the server never crashes on input.
- **`cats_catalog`:** timeout (default 20 s), one retry, then `TIMEOUT`; 404 → `NOT_FOUND`; TLS/DNS → `NETWORK_ERROR`.
- **`estimate_tilt_from_cubes`:** checks ranges, file existence, equal n³ shapes and positive density, and the field labelling (∇·B and anisotropy, reported as warnings).
- **Physically impossible stored values** (e.g. γ outside 0–90°) are returned as stored but flagged in `warnings`.

## Authentication and safety

- **No credentials:** CATS is public and the server runs locally over stdio, so there is no network-facing authentication.
- **Read-only:** FITS files are only read, `results.db` is opened read-only, and paths are restricted to the project folder (plus `CATS_ALLOWED_DIRS`). The only write is the audit log.
- **Verification instead of authentication:** every call (successful or not) is signed with HMAC-SHA256 using a local secret key (`~/.cats_mcp_key`) that the LLM never sees, and appended to `audit_log.jsonl`. `verify.py` checks any `verification_id`, the input files' SHA-256 fingerprints, the call order, and numbers quoted by the LLM.
