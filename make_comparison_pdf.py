from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle, KeepTogether)

pdfmetrics.registerFont(TTFont("DV", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB")

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#52606d")
RULE = colors.HexColor("#cbd2d9")
HEAD = colors.HexColor("#243b53")
ZEBRA = colors.HexColor("#f5f7fa")
V_STRONG = colors.HexColor("#b42318")
V_PART = colors.HexColor("#b54708")
V_WEAK = colors.HexColor("#6b7280")
V_AGREE = colors.HexColor("#067647")

title = ParagraphStyle("t", fontName="DVB", fontSize=15, leading=19, textColor=INK)
sub = ParagraphStyle("s", fontName="DV", fontSize=9, leading=13.5, textColor=MUTED)
h2 = ParagraphStyle("h2", fontName="DVB", fontSize=11, leading=14, textColor=INK,
                    spaceBefore=10, spaceAfter=4)
cell = ParagraphStyle("c", fontName="DV", fontSize=8, leading=12, textColor=INK, alignment=TA_LEFT)
cellb = ParagraphStyle("cb", parent=cell, fontName="DVB")
hdr = ParagraphStyle("h", fontName="DVB", fontSize=8.2, leading=10.5, textColor=colors.white)
note = ParagraphStyle("n", fontName="DV", fontSize=7.8, leading=11.5, textColor=MUTED)


def verdict(text, color):
    return Paragraph(f'<font color="{color.hexval()}"><b>{text}</b></font>', cell)


g = "γ"
contradictions = [
    ("1",
     "Uncertainty in p<sub>0</sub> (from p<sub>max</sub>) has little effect on the derived tilt.<br/>"
     "<font color='#52606d'>Appendix A, Fig. A2 (Vela C: p<sub>max</sub> 0.15 vs 0.20)</font>",
     "Moderately ordered runs agree: max vs 99.5th percentile changes estimates by 1–3°. "
     "Strongly ordered AREPO B100: the same switch moves the 60° estimate from <b>46° to 32°</b> (14°).",
     "In very ordered fields, p<sub>0</sub> is set by the few most polarized sightlines "
     "(top ~0.1%), so it depends on how p<sub>max</sub> is chosen. B100 is more ordered "
     "(δB/B<sub>0</sub> ≈ 0.11) than any model Chen tested.",
     ("Strong (in a regime Chen did not test)", V_STRONG)),
    ("2",
     f"{g}<sub>obs</sub> is the maximum possible tilt, so {g}<sub>obs</sub> ≥ {g}<sub>2D</sub>; "
     f"used to infer {g}<sub>2D</sub> ≈ 45° for Vela C.<br/>"
     "<font color='#52606d'>Sections 2.2 and 5</font>",
     f"Violated with p<sub>0</sub> taken from the map: B100 at 60°, {g}<sub>obs</sub> = 46° vs "
     f"{g}<sub>2D</sub> = 59° (32° with the 99.5th-percentile rule); b1p1 at 90°, 66° vs 67°. "
     "Holds with the true p<sub>0</sub>: 59.7° ≥ 59.3° and 74.1° ≥ 67.2°.",
     "The inequality is correct only when p<sub>0</sub> is known exactly. Observers must "
     "estimate p<sub>0</sub> from the map, so the Vela C step that relies on it is weaker than stated.",
     ("Strong", V_STRONG)),
    ("3",
     "Error grows roughly linearly with the polarization-angle dispersion ⟨S⟩.<br/>"
     "<font color='#52606d'>Section 4.2, Fig. 5 (right)</font>",
     "Among passing runs, B100 has the smallest ⟨S⟩ but the largest error (median |err| ≈ 9° "
     "vs 1–5° for b1p1, b1p.01, B30).",
     "Chen notes the tilt also affects the error. S depends on resolution and box scale, "
     "so values are not directly comparable across studies.",
     ("Mild", V_PART)),
    ("4",
     "Keeping only pixels with S &lt; ⟨S⟩ improves the estimate (by ≲ 5°).<br/>"
     "<font color='#52606d'>Section 4.2; conclusion (v)</font>",
     "Mixed (p<sub>max</sub> = max): median |err| improves for b1p1 (3.9° → 1.9°) and B30 "
     "(4.9° → 3.6°), worsens slightly for b1p.01 (1.0° → 1.9°) and B100 (8.9° → 9.2°). "
     "Slope falls in most runs; worse near 90° in every run.",
     "Chen's improvement was small to begin with, so mixed results are not surprising.",
     ("Weak", V_WEAK)),
    ("5",
     "Method works well in less-perturbed clouds even when the field is nearly along the "
     "line of sight.<br/><font color='#52606d'>Appendix A (model L1)</font>",
     "b1p1 still reads about 24° too low at 90° (all pixels). B100 (p<sub>max</sub> = max) reaches "
     "about 82° at 90°, so the claim holds there.",
     "Depends on how ordered the field is: holds for very ordered fields, not for "
     "δB/B<sub>0</sub> ≈ 0.5–0.6.",
     ("Partial", V_PART)),
]

agreements = [
    "Error pattern: estimate too high near 0°, too low near 90°, most accurate in between.",
    "Accuracy at 30–60° in ordered fields: Chen ±10°; this work 1–9° median |err| in all four "
    "ordered runs (p<sub>max</sub> = max).",
    "Failure in disordered fields: Chen's L10/L20 compress toward the middle; our weak-field runs "
    "(δB/B<sub>0</sub> ≈ 4–6) are flat, slope ≈ 0.",
    "p<sub>max</sub> underestimates p<sub>0</sub> when an ordered field is tilted (Appendix A); "
    "seen more strongly in B100.",
]


def build(path):
    doc = SimpleDocTemplate(path, pagesize=landscape(letter),
                            leftMargin=0.55 * inch, rightMargin=0.55 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            title="Comparison with Chen et al. (2019)",
                            author="Bianca Alexis")
    story = [
        Paragraph("Where this project's results differ from Chen et al. (2019)", title),
        Spacer(1, 3),
        Paragraph("Chen C.-Y. et al., 2019, MNRAS 485, 3499. Results below use p<sub>0</sub> from "
                  "the map maximum (the pre-registered rule, as in Chen) unless stated; "
                  "low-S = pixels with S below the map median. Runs: Cho-ENO b1p1, b1p.01, b.1p1, "
                  "b.1p.01 (no gravity); AREPO B30, B100 (gravity on), all at 256<super>3</super>.",
                  sub),
        Spacer(1, 8),
    ]

    head = [Paragraph(t, hdr) for t in
            ("#", "Chen's claim (source)", "What this project found", "Why / caveat", "Strength")]
    rows = [head]
    for n, claim, found, why, (v, c) in contradictions:
        rows.append([Paragraph(n, cellb), Paragraph(claim, cell), Paragraph(found, cell),
                     Paragraph(why, cell), verdict(v, c)])
    w = doc.width
    t = Table(rows, colWidths=[0.035 * w, 0.255 * w, 0.29 * w, 0.29 * w, 0.13 * w], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(style))
    story.append(t)

    story.append(Paragraph("Where the results agree with Chen", h2))
    arows = [[verdict("Agrees", V_AGREE), Paragraph(a, cell)] for a in agreements]
    at = Table(arows, colWidths=[0.1 * w, 0.9 * w])
    at.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
    ]))
    story.append(at)

    story.append(Spacer(1, 8))
    story.append(KeepTogether([
        Paragraph("<b>How to read this.</b> These are tensions or limits, not errors in the paper. "
                  "The simulations differ from Chen's (periodic driven boxes vs. colliding-flow slabs; "
                  "Cho-ENO and AREPO vs. Athena), each run is one snapshot, and B100 is more ordered "
                  "than anything Chen tested. Items 1 and 2 are the most robust: both are clear in the "
                  "numbers and both come from estimating p<sub>0</sub> from the map, which every real "
                  "observation must do. The slope (tracking) criterion used to grade runs is this "
                  "project's addition, not Chen's.", note)]))
    doc.build(story)


if __name__ == "__main__":
    build("chen_comparison.pdf")
