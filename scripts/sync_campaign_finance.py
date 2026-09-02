#!/usr/bin/env python3
"""
Refreshes the "Campaign Finance" tab of the statewide dossier in
pa-districts-map.html from PA Dept. of State's official campaign finance
bulk data export -- NOT scraped from any third-party site (see note below).

Source: https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/campaign-finance-data
File:   <year>.zip -> filer_<year>.txt / expense_<year>.txt / contrib_<year>.txt

Why not transparencyusa.org, which the tab links out to as a convenience:
that site's robots.txt explicitly disallows ClaudeBot (Disallow: /), so an
automated agent built by Claude should not scrape it on a schedule. PA DOS's
own bulk export is official, license-clean, and downloadable in bulk, so
that's what this script computes from.

Methodology notes (there is no official code legend for this export, so
these are inferred from the data and disclosed rather than silently assumed
correct -- see the "Statewide Finance Summary" blurb in the HTML too):
  - MONETARY on the filer file is PER-REPORT-PERIOD, not cumulative-to-date
    (confirmed empirically: a single committee's reports fluctuate up and
    down across the year rather than only increasing). A filer's annual
    total raised is the SUM of MONETARY across all of that filer's report
    rows in the year's export.
  - "Candidate" vs. "Committee/PAC": FILERTYPE=1 (individual candidate
    filings) are always candidates. Of FILERTYPE=2 (committees), only ones
    with a populated DISTRICT are treated as one candidate's own committee
    -- DISTRICT turned out to be a much cleaner signal than OFFICE, which
    is also populated on some institutional PAC filings (e.g. national
    union PACs). This is not perfect (rare PAC filings that also carry a
    district code can still slip into "candidates") and is disclosed in
    the tab's own text rather than presented as authoritative.
  - FILERTYPE=3 (74 filers in 2026, no office/party data at all -- an
    undocumented category) is lumped in with committees/PACs.
  - "Top Contributors" is aggregated by raw contributor name text, since PA's
    export has no unique donor ID -- near-duplicate name spellings will not
    be merged. Disclosed in the tab's own text.

Fails loudly (nonzero exit, no file write) rather than guessing if the
source's shape changes.
"""
import csv
import datetime
import html as htmllib
import io
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = REPO_ROOT / "pa-districts-map.html"

TOP_N_CANDIDATES = 10
TOP_N_COMMITTEES = 10
TOP_N_CONTRIBUTORS = 10

OFFICE_LABELS = {
    "GOV": "Governor", "LTG": "Lt. Governor", "ATT": "Attorney General",
    "AUD": "Auditor General", "TRE": "State Treasurer",
    "STS": "State Senate", "STH": "State House",
    "USC": "U.S. House", "USS": "U.S. Senate",
}


def zip_url(year: int) -> str:
    return (f"https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/"
            f"voting-and-elections/campaign-finance/campaign-finance-data/{year}.zip")


def fetch_zip_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def load_csv(zf: zipfile.ZipFile, name_substr: str):
    matches = [n for n in zf.namelist() if name_substr in n.lower()]
    if not matches:
        sys.exit(f"FATAL: no file matching '{name_substr}' in the export -- source layout may have changed.")
    with zf.open(matches[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        reader = csv.reader(text)
        header = next(reader)
        rows = list(reader)
    return header, rows


def num(s):
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def clean_name(raw: str) -> str:
    # Verbatim as published, just whitespace-trimmed -- no case "correction"
    # (naive title-casing mangles acronyms like AFSCME, PAC, AFL-CIO) and no
    # expansion of anything truncated in the source.
    name = raw.strip()
    name = htmllib.unescape(name)  # normalize any pre-escaped entities once
    return name


def display_name(raw: str) -> str:
    # Full name, untruncated -- the .cf-row CSS (text-overflow: ellipsis)
    # handles the one-line visual truncation, which adapts to actual
    # rendered width rather than a guessed character count, and keeps the
    # full name in the page source/DOM for anyone who inspects or copies it.
    return htmllib.escape(clean_name(raw), quote=False)


def money(n: float) -> str:
    return f"${n:,.0f}"


def analyze(zip_bytes: bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        fh, frows = load_csv(zf, "filer_")
        eh, erows = load_csv(zf, "expense_")
        ch, crows = load_csv(zf, "contrib_")

    def col(header, name):
        if name not in header:
            sys.exit(f"FATAL: expected column '{name}' not found in header {header}")
        return header.index(name)

    fid_i, ft_i, off_i, name_i, party_i, mon_i, ink_i, dist_i, sub_i = (
        col(fh, "FILERID"), col(fh, "FILERTYPE"), col(fh, "OFFICE"), col(fh, "FILERNAME"),
        col(fh, "PARTY"), col(fh, "MONETARY"), col(fh, "INKIND"), col(fh, "DISTRICT"), col(fh, "SubmittedDate"),
    )

    filers = {}
    max_submitted = ""
    for r in frows:
        if len(r) <= max(fid_i, ft_i, off_i, name_i, party_i, mon_i, ink_i, dist_i, sub_i):
            continue
        fid = r[fid_i].strip()
        name = r[name_i].strip()
        if not name:
            continue
        sub = r[sub_i].strip()
        if sub > max_submitted:
            max_submitted = sub
        if fid not in filers:
            filers[fid] = {"name": name, "ft": r[ft_i].strip(), "office": "",
                            "party": "", "dist": "", "raised": 0.0, "inkind": 0.0}
        filers[fid]["raised"] += num(r[mon_i])
        filers[fid]["inkind"] += num(r[ink_i])
        if r[off_i].strip():
            filers[fid]["office"] = r[off_i].strip()
        if r[party_i].strip():
            filers[fid]["party"] = r[party_i].strip()
        if r[dist_i].strip():
            filers[fid]["dist"] = r[dist_i].strip()

    if not filers:
        sys.exit("FATAL: parsed zero filers -- refusing to write an empty dataset.")
    if not max_submitted:
        sys.exit("FATAL: couldn't find any SubmittedDate in the filer file.")

    candidates = [f for f in filers.values() if f["ft"] == "1" or (f["ft"] == "2" and f["dist"])]
    committees = [f for f in filers.values() if f["ft"] in ("2", "3") and not f["dist"]]

    eamt_i = col(eh, "EXPAMT")
    total_spent = sum(num(r[eamt_i]) for r in erows if len(r) > eamt_i)
    total_raised = sum(f["raised"] for f in filers.values())

    cont_i, a1_i, a2_i, a3_i = col(ch, "CONTRIBUTOR"), col(ch, "CONTAMT1"), col(ch, "CONTAMT2"), col(ch, "CONTAMT3")
    contrib_totals = defaultdict(float)
    contrib_display = {}  # normalized key -> a representative original-cased name
    for r in crows:
        if len(r) <= max(cont_i, a1_i, a2_i, a3_i):
            continue
        raw_name = r[cont_i].strip()
        if not raw_name:
            continue
        key = clean_name(raw_name).upper()
        contrib_totals[key] += num(r[a1_i]) + num(r[a2_i]) + num(r[a3_i])
        contrib_display.setdefault(key, clean_name(raw_name))

    as_of = datetime.datetime.strptime(max_submitted, "%Y-%m-%d").date()

    return {
        "as_of": as_of,
        "total_raised": total_raised,
        "total_spent": total_spent,
        "active_filers": len(filers),
        "top_candidates": sorted(candidates, key=lambda x: -x["raised"])[:TOP_N_CANDIDATES],
        "top_committees": sorted(committees, key=lambda x: -x["raised"])[:TOP_N_COMMITTEES],
        "top_contributors": sorted(
            ((contrib_display[k], v) for k, v in contrib_totals.items()),
            key=lambda x: -x[1],
        )[:TOP_N_CONTRIBUTORS],
    }


def fmt_money_short(n: float) -> str:
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n:,.0f}"


def render_summary(data) -> str:
    return f"""
      <div class="stats-grid">
        <div class="stat-cell">
          <div class="stat-label">Total Raised</div>
          <div class="stat-value">{fmt_money_short(data['total_raised'])}</div>
          <div class="stat-sub">{data['as_of'].year} filings</div>
        </div>
        <div class="stat-cell">
          <div class="stat-label">Total Spent</div>
          <div class="stat-value">{fmt_money_short(data['total_spent'])}</div>
          <div class="stat-sub">{data['as_of'].year} filings</div>
        </div>
        <div class="stat-cell">
          <div class="stat-label">Active Filers</div>
          <div class="stat-value">{data['active_filers']:,}</div>
          <div class="stat-sub">candidates &amp; committees</div>
        </div>
      </div>
      """


def render_candidates(data) -> str:
    # office and $ amount are separate fixed-width columns (.cf-office /
    # .cf-amt) so dollar figures line up across every row regardless of
    # name or office-label length, instead of one combined string.
    rows = []
    for f in data["top_candidates"]:
        dot = "dem" if f["party"] == "DEM" else ("rep" if f["party"] == "REP" else "unk")
        office = OFFICE_LABELS.get(f["office"], f["office"] or "—")
        rows.append(
            f'<div class="cf-row"><span class="pdot {dot}"></span>'
            f'<span class="council-name">{display_name(f["name"])}</span>'
            f'<span class="cf-office">{htmllib.escape(office)}</span>'
            f'<span class="cf-amt">{money(f["raised"])}</span></div>'
        )
    return f'\n      <div class="council-list">\n        {"".join(rows)}\n      </div>\n      '


def render_committees(data) -> str:
    rows = [
        f'<div class="cf-row"><span class="council-name">{display_name(f["name"])}</span>'
        f'<span class="cf-amt">{money(f["raised"])}</span></div>'
        for f in data["top_committees"]
    ]
    return f'\n      <div class="council-list">\n        {"".join(rows)}\n      </div>\n      '


def render_contributors(data) -> str:
    rows = [
        f'<div class="cf-row"><span class="council-name">{display_name(name)}</span>'
        f'<span class="cf-amt">{money(amt)}</span></div>'
        for name, amt in data["top_contributors"]
    ]
    return f'\n      <div class="council-list">\n        {"".join(rows)}\n      </div>\n      '


def splice_block(html: str, marker: str, new_inner: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(<!-- {marker}_START -->)(.*?)(<!-- {marker}_END -->)", re.DOTALL
    )
    if not pattern.search(html):
        sys.exit(f"FATAL: could not find {marker}_START/_END markers in pa-districts-map.html.")
    new_html, n = pattern.subn(lambda m: m.group(1) + new_inner + m.group(3), html, count=1)
    return new_html, new_html != html


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, help="use a local zip instead of downloading")
    parser.add_argument("--year", type=int, default=datetime.date.today().year)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.zip:
        zip_bytes = args.zip.read_bytes()
    else:
        url = zip_url(args.year)
        print(f"Downloading {url} ...")
        zip_bytes = fetch_zip_bytes(url)

    data = analyze(zip_bytes)
    print(f"As of {data['as_of'].isoformat()}: {data['active_filers']:,} filers, "
          f"{fmt_money_short(data['total_raised'])} raised, {fmt_money_short(data['total_spent'])} spent.")

    if args.dry_run:
        print("Dry run -- not writing pa-districts-map.html.")
        return

    html = HTML_PATH.read_text(encoding="utf-8")
    changed_any = False
    for marker, renderer in [
        ("CF_SUMMARY", render_summary),
        ("CF_CANDIDATES", render_candidates),
        ("CF_COMMITTEES", render_committees),
        ("CF_CONTRIBUTORS", render_contributors),
    ]:
        html, changed = splice_block(html, marker, renderer(data))
        changed_any = changed_any or changed

    full_label = data["as_of"].strftime("%B %-d, %Y") if sys.platform != "win32" \
        else data["as_of"].strftime("%B %#d, %Y")
    date_pattern = re.compile(r'(class="h3-updated-link">Data through )[A-Za-z]+ \d{1,2}, \d{4}(</a>)')
    if date_pattern.search(html):
        html, n = date_pattern.subn(rf"\g<1>{full_label}\g<2>", html)
        changed_any = changed_any or bool(n)

    if not changed_any:
        print("No changes -- embedded campaign finance data already matches the source.")
        return

    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"Updated {HTML_PATH}.")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"changed=true\nas_of={data['as_of'].isoformat()}\n")


if __name__ == "__main__":
    main()
