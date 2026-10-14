"""Local checksum badge, explicitly scoped to mechanical package consistency."""

import re
from html import escape


def consistency_badge(report: dict, file_sha256: str) -> str:
    if not re.fullmatch('[a-f0-9]{64}', file_sha256):
        raise ValueError('Badge requires the exact package file SHA256')
    passed = report.get('status') == 'consistent'
    checks = report.get('checks', [])
    failures = sum(c.get('status') == 'fail' for c in checks)
    raw = report.get('raw_sources', {})
    scope = (f"Original-source comparison requested; {raw.get('filings_checked', 0)} filings, "
             f"{raw.get('facts_checked', 0)} facts, {raw.get('missing', 0)} missing."
             if raw.get('requested') else 'Original-source comparison was not requested.')
    title = 'Offline package consistency: PASSED' if passed else 'Offline package consistency: FAILED'
    color = '#244c64' if passed else '#8a3434'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="820" height="154" viewBox="0 0 820 154" role="img" aria-labelledby="title description">
<title id="title">{escape(title)}</title><desc id="description">{escape(scope)} Checksums do not authenticate the author or certify semantic or investment accuracy.</desc>
<rect width="820" height="154" rx="6" fill="#f5f7f9" stroke="#bbc8d1"/><rect width="820" height="38" fill="{color}"/>
<g font-family="Arial,sans-serif"><text x="16" y="25" font-size="18" fill="white">{escape(title)}</text>
<text x="16" y="61" font-size="13">{escape(scope)} Mechanical checks: {len(checks)}, failures: {failures}.</text>
<text x="16" y="86" font-size="12">Package file SHA256: {file_sha256}</text>
<text x="16" y="115" font-size="13">Does not authenticate author/origin, certify semantic support, or validate investment judgments.</text>
<text x="16" y="138" font-size="12">Local artifact; the underlying report and caller-trusted sources define the exact checking scope.</text></g></svg>'''
