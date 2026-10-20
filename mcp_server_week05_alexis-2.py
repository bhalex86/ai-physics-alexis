"""Week 05 MCP server: astronomy research tools backed by SIMBAD and arXiv.

Tools
-----
query_simbad(object_name)               -> dict   SIMBAD (CDS) lookup via astroquery.simbad (TAP)
search_arxiv(keyword, max_results)      -> list   most recent arXiv papers matching a keyword
get_arxiv_fulltext(arxiv_id, max_chars) -> dict   full text of one arXiv paper (from its PDF)

Run (stdio transport, which is what MCP clients such as Claude Desktop or your
own client script launch):

Configuration
-------------
API keys are never hard-coded. They are read from a ``.env`` file that sits next
to this module (copy ``.env.example`` to ``.env`` and fill it in). SIMBAD and
arXiv are public services and need no key; the keys in ``.env`` are for the LLM
client (ANTHROPIC_API_KEY or OPENAI_API_KEY) that connects to this server.
"""

import io
import math
import re
import ssl
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
import arxiv
import certifi
import numpy as np
from astroquery.simbad import SimbadClass
from dotenv import load_dotenv
from pydantic import Field
from pypdf import PdfReader

# mcp 2.x renamed FastMCP to MCPServer; fall back so the module runs on mcp 1.x too.
try:
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError
# ToolError is the SDK's "expected failure" type: its message is passed through to
# the LLM (any other exception is reduced to a generic "error executing tool").

# Load .env from the module's own directory (MCP clients launch servers from an
# arbitrary working directory, so don't rely on the CWD). Existing environment
# variables win over the file.
load_dotenv(Path(__file__).resolve().parent / ".env")
server = _Server("astro-research-week05")
MAX_ARXIV_RESULTS = 50
MAX_FULLTEXT_CHARS = 60000
_SIMBAD_FIELDS = ("otype", "sp_type", "plx_value", "plx_err", "rvz_radvel", "rvz_err")

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> Any:
    """Turn an astropy/numpy table cell into a JSON-safe Python value.
    Masked cells, NaN and empty strings become None.
    """
    if value is None or np.ma.is_masked(value):
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if hasattr(value, "item"):  # numpy scalar -> Python scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip() or None
    return value


@lru_cache(maxsize=1)
def _simbad_client() -> SimbadClass:
    """One configured SIMBAD client, reused across calls (adding fields costs a request)."""
    client = SimbadClass()
    client.add_votable_fields(*_SIMBAD_FIELDS)
    return client


@lru_cache(maxsize=1)
def _arxiv_client() -> arxiv.Client:
    """One shared arXiv client so its built-in 3 s rate limit applies across calls."""
    return arxiv.Client(page_size=MAX_ARXIV_RESULTS, delay_seconds=3.0, num_retries=3)


def _strip_version(short_id: str) -> str:
    """'2101.01234v2' -> '2101.01234'; 'astro-ph/0601001v1' -> 'astro-ph/0601001'."""
    return re.sub(r"v\d+$", "", short_id)


# --------------------------------------------------------------------------- #
# Tool 1: SIMBAD
# --------------------------------------------------------------------------- #
@server.tool()
def query_simbad(
    object_name: Annotated[
        str,
        Field(
            description=(
                "Name or catalogue identifier of ONE astronomical object, as "
                "SIMBAD recognises it. Common names, Messier/NGC/IC numbers, "
                "and catalogue IDs (HD, HIP, Gaia DR3, ...) all work. Do not "
                "pass sentences, coordinates, or several objects at once."
            ),
            examples=["Vega", "M 31", "HD 209458", "Proxima Cen", "NGC 1275"],
            min_length=1,
            max_length=200,
        ),
    ],
) -> dict[str, Any]:
    """Look up one astronomical object in the SIMBAD database (CDS Strasbourg).

    Returns a dict with:
      found                         true if SIMBAD matched the name, else false
      query                         the name that was looked up
      main_id                       SIMBAD's canonical identifier for the object
      object_type                   SIMBAD object-type code, e.g. "*" star, "G" galaxy,
                                    "PN" planetary nebula, "Pu*" pulsating variable
      spectral_type                 MK spectral type string, e.g. "A0Va" (null for non-stars)
      parallax_mas                  trigonometric parallax in milliarcseconds
      parallax_error_mas            1-sigma uncertainty on the parallax
      radial_velocity_km_s          radial velocity in km/s (positive = receding)
      radial_velocity_error_km_s    1-sigma uncertainty on the radial velocity
      ra_deg, dec_deg               ICRS position in decimal degrees
      source                        data provenance

    Any property SIMBAD does not have for the object is null, not omitted. When the
    name is not found, found is false and a "message" suggests what to try next.
    Raises an error if the SIMBAD service itself cannot be reached.
    """
    name = object_name.strip()
    if not name:
        raise ToolError("object_name must be a non-empty string, e.g. 'Vega' or 'M 31'.")

    try:
        table = _simbad_client().query_object(name)
    except Exception as exc:  # network / TAP service failure
        raise ToolError(f"SIMBAD query for {name!r} failed: {exc}") from exc

    if table is None or len(table) == 0:
        return {
            "found": False,
            "query": name,
            "message": (
                f"SIMBAD has no object matching {name!r}. Try another identifier "
                "(for example 'HD 172167', 'M 31', or a standard catalogue name)."
            ),
        }

    row = table[0]
    columns = {c.lower(): c for c in row.colnames}  # tolerate column-name casing

    def get(column: str) -> Any:
        actual = columns.get(column)
        return _clean(row[actual]) if actual else None

    return {
        "found": True,
        "query": name,
        "main_id": get("main_id"),
        "object_type": get("otype"),
        "spectral_type": get("sp_type"),
        "parallax_mas": get("plx_value"),
        "parallax_error_mas": get("plx_err"),
        "radial_velocity_km_s": get("rvz_radvel"),
        "radial_velocity_error_km_s": get("rvz_err"),
        "ra_deg": get("ra"),
        "dec_deg": get("dec"),
        "source": "SIMBAD (CDS) via astroquery.simbad TAP",
    }


# --------------------------------------------------------------------------- #
# Tool 2: arXiv
# --------------------------------------------------------------------------- #
@server.tool() # registering the tool
def search_arxiv(
    keyword: Annotated[
        str,
        Field( # tells the LLM what to pass and contains examples of inputs
            description=(
                "A single keyword or short phrase to search for in arXiv titles, "
                "abstracts, authors and metadata. Multi-word input is matched as an "
                "exact phrase, so keep it short and specific."
            ),
            examples=["exoplanet atmospheres", "fast radio bursts", "Cepheid"],
            min_length=1,
            max_length=200,
        ),
    ],
    max_results: Annotated[
        int,
        Field(
            description=(
                f"How many papers to return, between 1 and {MAX_ARXIV_RESULTS}. "
                "Results are the most recently submitted matches, newest first."
            ),
            ge=1,
            le=MAX_ARXIV_RESULTS,
        ),
    ] = 5, # this is a default number
) -> list[dict[str, Any]]:
    """Search arXiv and return the most recently submitted papers matching a keyword.

    Returns a list (newest first, at most max_results items, empty if nothing
    matches) of dicts, each with:
      title             paper title
      authors           list of author names, in order
      abstract          the abstract as plain text
      arxiv_id          arXiv identifier without version, e.g. "2610.01234"
      published         date of first submission, YYYY-MM-DD
      url               link to the arXiv abstract page
      primary_category  primary subject class, e.g. "astro-ph.EP"
    Only abstracts are returned; call get_arxiv_fulltext with an arxiv_id to
    read a paper's full text. Raises an error if arXiv cannot be reached.
    """
    term = " ".join(keyword.replace('"', " ").split())  # drop quotes, collapse whitespace
    if not term:
        raise ToolError("keyword must be a non-empty string, e.g. 'exoplanet atmospheres'.")
    if not 1 <= max_results <= MAX_ARXIV_RESULTS:
        raise ToolError(f"max_results must be between 1 and {MAX_ARXIV_RESULTS}.")

    search = arxiv.Search(
        query=f'all:"{term}"',
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    try:
        client = _arxiv_client()
        client.page_size = max_results  # fetch only what was asked for; keeps the shared rate limiter
        results = list(client.results(search))
    except Exception as exc:  # network / API failure
        raise ToolError(f"arXiv search for {term!r} failed: {exc}") from exc

    return [
        {
            "title": " ".join(r.title.split()),
            "authors": [a.name for a in r.authors],
            "abstract": " ".join(r.summary.split()),
            "arxiv_id": _strip_version(r.get_short_id()),
            "published": r.published.date().isoformat(),
            "url": r.entry_id,
            "primary_category": r.primary_category,
        }
        for r in results
    ]


# --------------------------------------------------------------------------- #
# Tool 3: arXiv full text
# --------------------------------------------------------------------------- #
@server.tool()
def get_arxiv_fulltext(
    arxiv_id: Annotated[
        str,
        Field(
            description=(
                "arXiv identifier of ONE paper, as returned in the arxiv_id field of "
                "search_arxiv, without a version suffix."
            ),
            examples=["2608.19339", "astro-ph/0601001"],
            min_length=4,
            max_length=40,
        ),
    ],
    max_chars: Annotated[
        int,
        Field(
            description=(
                f"Maximum number of characters of paper text to return, between 1000 and "
                f"{MAX_FULLTEXT_CHARS}. The default of 40000 covers the whole main text "
                "of most papers. If the result comes back with truncated=true and you "
                "need the end (such as the conclusions), call again with a larger value, "
                "up to total_chars."
            ),
            ge=1000,
            le=MAX_FULLTEXT_CHARS,
        ),
    ] = 40000,
) -> dict[str, Any]:
    """Download one arXiv paper's PDF and return its full text.

    Use this after search_arxiv when the abstract is not enough, for example to
    check a paper's methods or conclusions. Returns a dict with:
      arxiv_id            the identifier that was fetched
      title               paper title
      pages               number of pages in the PDF
      text                the extracted text, cut to max_chars
      truncated           true if the text was cut short
      total_chars         length of the full text (without references)
      references_removed  true if the reference list was dropped
    The reference list is removed so the budget goes to the paper itself.
    Equations, tables and figures may not extract cleanly from the PDF.
    Raises an error if the paper cannot be found or downloaded.
    """
    paper_id = _strip_version(arxiv_id.strip())
    if not paper_id:
        raise ToolError("arxiv_id must be a non-empty arXiv identifier, e.g. '2608.19339'.")
    if not 1000 <= max_chars <= MAX_FULLTEXT_CHARS:
        raise ToolError(f"max_chars must be between 1000 and {MAX_FULLTEXT_CHARS}.")

    try:
        results = list(_arxiv_client().results(arxiv.Search(id_list=[paper_id])))
    except Exception as exc:  # network / API failure
        raise ToolError(f"arXiv lookup for {paper_id!r} failed: {exc}") from exc
    if not results:
        raise ToolError(f"No arXiv paper with ID {paper_id!r}. Use an arxiv_id from search_arxiv.")
    paper = results[0]

    try:
        request = urllib.request.Request(paper.pdf_url, headers={"User-Agent": "astro-research-week05"})
        # Verify HTTPS against certifi's certificate bundle; some Python installs
        # (notably macOS) have no system certificates set up for urllib.
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=60, context=ssl_context) as response:
            reader = PdfReader(io.BytesIO(response.read()))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # download or PDF parsing failure
        raise ToolError(f"Could not download or read the PDF for {paper_id!r}: {exc}") from exc

    text = " ".join(text.split())  # collapse whitespace

    # Drop the reference list: it comes after the conclusions and would otherwise
    # use up the character budget. Only cut at a "References" heading found in the
    # second half of the paper, so a mention of the word earlier on is ignored.
    references_removed = False
    headings = list(re.finditer(r"\b(References|REFERENCES|Bibliography|BIBLIOGRAPHY)\b", text))
    if headings and headings[-1].start() > len(text) // 2:
        text = text[: headings[-1].start()].rstrip()
        references_removed = True

    return {
        "arxiv_id": paper_id,
        "title": " ".join(paper.title.split()),
        "pages": len(reader.pages),
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "total_chars": len(text),
        "references_removed": references_removed,
    }


if __name__ == "__main__":
    server.run(transport="stdio")