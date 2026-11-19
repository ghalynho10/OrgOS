"""The page's stylesheet, written from the design tokens of spec 0016 (*Page design*).

One 645px text column, flat striped tables that scroll inside their box with a
sticky first column, a dark header and footer band, the system font stack and
no colour on data (AC-20). The tokens are constants so a test can compute the
contrast of every text and background pair (AC-38).
"""

from __future__ import annotations

from typing import Final

PAGE: Final = "#FFFFFF"
TEXT: Final = "#212A2A"
HEADING: Final = "#090C0C"
MUTED: Final = "#3A4848"
HAIRLINE: Final = "#E3E4E4"
STRIPE: Final = "#F5F5F5"
BAND: Final = "#072B33"
BAND_TEXT: Final = "#FFFFFF"
COLUMN_PX: Final = 645
TOC_FROM_PX: Final = 1100
FONT: Final = '-apple-system, system-ui, "Segoe UI", Roboto, sans-serif'

# Every text colour against every background it is drawn on.
TEXT_PAIRS: Final = (
    (TEXT, PAGE),
    (HEADING, PAGE),
    (MUTED, PAGE),
    (TEXT, STRIPE),
    (HEADING, STRIPE),
    (MUTED, STRIPE),
    (BAND_TEXT, BAND),
)

STYLESHEET: Final = f"""\
*, *::before, *::after {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }}
body {{
  margin: 0;
  background: {PAGE};
  color: {TEXT};
  font-family: {FONT};
  font-size: 18px;
  line-height: 1.6;
  overflow-wrap: break-word;
}}
a {{ color: inherit; text-decoration: underline; text-underline-offset: 2px; }}
:focus-visible {{ outline: 2px solid {HEADING}; outline-offset: 2px; }}
.band {{ background: {BAND}; color: {BAND_TEXT}; padding: 0 16px; }}
.band a {{ color: {BAND_TEXT}; }}
.band :focus-visible {{ outline-color: {BAND_TEXT}; }}
.band-inner {{ max-width: {COLUMN_PX}px; margin: 0 auto; }}
.band-top {{ height: 60px; display: flex; align-items: center; }}
.band-top .band-inner {{ width: 100%; }}
.band-top p {{ margin: 0; font-weight: 600; }}
.band-bottom {{ margin-top: 4em; padding-top: 2em; padding-bottom: 2em; }}
.band-bottom p {{ margin: 0 0 1em; }}
.layout {{ padding: 0 16px; }}
.column {{ max-width: {COLUMN_PX}px; margin: 0 auto; }}
.toc {{ display: none; }}
h1, h2, h3 {{ color: {HEADING}; font-weight: 600; line-height: 1.2; }}
h1 {{ font-size: 46px; margin: 1em 0 0.4em; }}
h2 {{ font-size: 30px; margin: 60px 0 20px; }}
h3 {{ font-size: 26px; margin: 2em 0 0.5em; }}
p, ul, ol {{ margin: 0 0 1em; }}
.byline {{ color: {MUTED}; }}
.dek {{ font-size: 20px; font-weight: 400; color: {MUTED}; }}
.scroll {{ overflow-x: auto; margin: 2em 0 0; container-type: inline-size; }}
table {{
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}}
caption {{
  caption-side: bottom;
  position: sticky;
  left: 0;
  max-width: 100cqw;
  text-align: left;
  font-size: 14px;
  padding-top: 20px;
  color: {MUTED};
}}
th {{
  font-weight: 600;
  text-align: left;
  vertical-align: bottom;
  color: {HEADING};
  border-bottom: 2px solid {HEADING};
  padding: 10px;
}}
td {{ padding: 10px; border-bottom: 1px solid {HAIRLINE}; vertical-align: top; }}
thead tr, tbody tr {{ background: {PAGE}; }}
tbody tr:nth-child(even) {{ background: {STRIPE}; }}
th:first-child, td:first-child {{ position: sticky; left: 0; background: inherit; }}
.caveat {{ font-size: 14px; color: {MUTED}; margin: 10px 0 2em; }}
.limitation {{ margin: 0 0 1em; }}
.lim-id {{ font-weight: 600; color: {HEADING}; }}
.lim-source {{ display: block; font-size: 16px; color: {MUTED}; }}
.note {{ color: {MUTED}; }}
@media (min-width: {TOC_FROM_PX}px) {{
  .layout {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) {COLUMN_PX}px minmax(0, 1fr);
    column-gap: 48px;
  }}
  .toc {{
    display: block;
    grid-column: 1;
    justify-self: end;
    align-self: start;
    position: sticky;
    top: 24px;
    max-width: 220px;
    margin-top: 60px;
    font-size: 14px;
  }}
  .toc ul {{ list-style: none; padding: 0; margin: 0; }}
  .toc li {{ margin: 0 0 0.6em; }}
  .toc-label {{ font-weight: 600; color: {HEADING}; }}
  .column {{ grid-column: 2; max-width: none; margin: 0; }}
}}
"""
