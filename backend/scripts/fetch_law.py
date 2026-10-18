"""Download the trusted law sources into backend/data/law/<stem>.txt.

Only the sources in app/rag/sources.py are fetched, and every request (including
redirects) must stay on legislation.gov.uk or gov.uk. Acts come from legislation.gov.uk's
revised XML (trimmed to the sections listed in sources.py); guidance comes from the gov.uk
content API. Run from backend/:

    .venv\\Scripts\\python -m scripts.fetch_law            # fetch every source
    .venv\\Scripts\\python -m scripts.fetch_law --only how_to_rent
    .venv\\Scripts\\python -m scripts.fetch_law --list housing_act_1988   # show an Act's sections

Then run scripts.ingest_law to embed and store them.
"""

import argparse
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from app import config
from app.rag.sources import TRUSTED_SOURCES, TrustedSource, is_trusted_url

LEG = "{http://www.legislation.gov.uk/namespaces/legislation}"
DC = "{http://purl.org/dc/elements/1.1/}"
USER_AGENT = "FlatMate law ingest (IKU Hackathon)"


def fetch(url: str) -> bytes:
    if not is_trusted_url(url):
        raise ValueError(f"refusing to fetch untrusted URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        if not is_trusted_url(response.geturl()):
            raise ValueError(f"{url} redirected to untrusted URL {response.geturl()}")
        return response.read()


# ===== legislation.gov.uk =====


def _flat(text: str | None) -> str:
    """XML layout whitespace (some files are pretty-printed) is a space; line breaks come from the structure."""
    return re.sub(r"\s*\n\s*", " ", text or "")


def _render(el: ET.Element, number_paragraphs: bool, parent: str = "") -> str:
    """Plain text of a CLML element, one line per paragraph."""
    tag = el.tag.removeprefix(LEG)
    if tag in ("Commentaries", "CommentaryRef", "Repeal"):
        # Repeal wraps old text kept for display next to what replaced it; keep the law as amended.
        return ""
    if tag == "Pnumber":
        number = _text(el)
        if not number:  # the whole paragraph was repealed
            return ""
        if parent == "P1":
            # A section's own number is already its section_ref; schedule paragraphs keep theirs.
            return f"\n{number} " if number_paragraphs else ""
        return f"\n({number}) "
    out = _flat(el.text)
    for child in el:
        out += _render(child, number_paragraphs, tag) + _flat(child.tail)
    if tag in ("Text", "Title"):
        out += "\n"
    return out


def _clean(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _version_key(number: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d+)([A-Z]*)", number.strip())
    if not match:
        return (-1, number)
    # Inserted sections run 14, 14ZA, 14ZB, 14A, 14B: a Z prefix sorts before plain letters.
    suffix = match.group(2)
    return (int(match.group(1)), "!" + suffix[1:] if suffix.startswith("Z") and len(suffix) > 1 else suffix)


def _keeps(spec: str, kind: str, number: str, label: str = "s.") -> bool:
    if kind == "section" and spec.startswith(label):
        low, _, high = spec[len(label) :].partition("-")
        key = _version_key(number)
        return _version_key(low) <= key <= _version_key(high or low)
    return kind == "schedule" and spec.casefold() == f"sch {number}".casefold()


def _inline(el: ET.Element) -> str:
    out = el.text or ""
    for child in el:
        if child.tag.removeprefix(LEG) not in ("Commentaries", "CommentaryRef", "Repeal"):
            out += _inline(child)
        out += child.tail or ""
    return out


def _text(el: ET.Element | None) -> str:
    """One line of text, as amended; numbers often carry markup like <CommentaryRef/>4 or <Addition>4A</Addition>."""
    return "" if el is None else " ".join(_inline(el).split())


def _own_p1s(el: ET.Element):
    """The section P1s of a P1group, not those quoted inside an amendment it makes."""
    for child in el:
        tag = child.tag.removeprefix(LEG)
        if tag == "P1":
            yield child
        elif tag not in ("BlockAmendment", "Versions"):
            yield from _own_p1s(child)


def _walk_units(el: ET.Element, units: list, in_schedules: bool = False):
    """Collect sections and schedules, skipping text quoted inside amendments."""
    tag = el.tag.removeprefix(LEG)
    if tag in ("BlockAmendment", "Versions"):
        return
    if tag == "P1group" and not in_schedules:
        heading = _text(el.find(LEG + "Title"))
        for p1 in _own_p1s(el):
            number = _text(p1.find(LEG + "Pnumber"))
            if number:
                units.append(("section", number, heading, p1.get("DocumentURI", ""), p1))
        return
    if tag == "Schedule":
        match = re.search(r"schedule\s+(\w+)", _text(el.find(LEG + "Number")), re.IGNORECASE)
        title = el.find(f"{LEG}TitleBlock/{LEG}Title")
        heading = _text(title)
        if match:
            body = el.find(LEG + "ScheduleBody")
            units.append(("schedule", match.group(1).upper(), heading, el.get("DocumentURI", ""), el if body is None else body))
        return
    for child in el:
        _walk_units(child, units, in_schedules or tag == "Schedules")


def legislation_units(source: TrustedSource) -> tuple[str, list]:
    root = ET.fromstring(fetch(f"{source.url}/data.xml"))
    last_updated = (root.findtext(f".//{DC}modified") or "").strip()[:10]
    units = []
    for part in ("Body", "Schedules"):
        for el in root.iter(LEG + part):
            _walk_units(el, units)
    return last_updated, units


def fetch_legislation(source: TrustedSource) -> str:
    last_updated, units = legislation_units(source)
    sections = []
    used = set()
    for kind, number, heading, uri, el in units:
        matched = [spec for spec in source.keep if _keeps(spec, kind, number, source.label)]
        if not matched:
            continue
        used.update(matched)
        text = _clean(_render(el, number_paragraphs=kind == "schedule"))
        if len(re.sub(r"[^A-Za-z]", "", text)) < 40:
            print(f"  skip {kind} {number}: repealed or empty")
            continue
        ref = f"{source.label}{number}" if kind == "section" else f"Sch {number}"
        url = uri.replace("http://", "https://") or source.url
        sections.append((ref, heading, url, text))
    for spec in source.keep:
        if spec not in used:
            print(f"  warning: {source.stem}: nothing matched {spec!r}")
    return _write(source, last_updated, sections)


# ===== gov.uk =====


class _GuideHTML(HTMLParser):
    """Turns gov.uk body HTML into text, split into (heading, text) at each <h2>."""

    BLOCKS = {"p", "li", "h2", "h3", "h4", "tr", "br", "div", "table"}

    def __init__(self):
        super().__init__()
        self.parts = [["", ""]]
        self.in_h2 = False

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self.parts.append(["", ""])
            self.in_h2 = True
        elif tag in self.BLOCKS:
            self.parts[-1][1] += "\n"

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_h2 = False
        elif tag in self.BLOCKS:
            self.parts[-1][1] += "\n"

    def handle_data(self, data):
        self.parts[-1][0 if self.in_h2 else 1] += data


def _html_parts(html: str) -> list[tuple[str, str]]:
    parser = _GuideHTML()
    parser.feed(html)
    return [(_clean(h), _clean(t)) for h, t in parser.parts if _clean(t)]


def _content(url: str) -> dict:
    path = url.removeprefix("https://www.gov.uk")
    return json.loads(fetch(f"https://www.gov.uk/api/content{path}"))


def fetch_guidance(source: TrustedSource) -> str:
    item = _content(source.url)
    last_updated = item.get("public_updated_at", "")[:10]
    details = item.get("details", {})
    sections = []
    if details.get("parts"):
        for part in details["parts"]:
            text = "\n\n".join(t for _, t in _html_parts(part["body"]))
            sections.append((part["title"], part["title"], f"{source.url}/{part['slug']}", text))
    else:
        page_url, body = source.url, details.get("body", "")
        if not body:
            html = [a for a in details.get("attachments", []) if a.get("attachment_type") == "html"]
            if not html:
                raise ValueError(f"{source.url}: no body, parts or HTML attachment found")
            page_url = "https://www.gov.uk" + html[0]["url"]
            body = _content(page_url).get("details", {}).get("body", "")
        for heading, text in _html_parts(body):
            heading = heading or item.get("title", source.name)
            sections.append((heading, heading, page_url, text))
    return _write(source, last_updated, sections)


# ===== output =====


def _write(source: TrustedSource, last_updated: str, sections: list) -> str:
    if not last_updated:
        raise ValueError(f"{source.stem}: no last-updated date found")
    if not sections:
        raise ValueError(f"{source.stem}: no sections found")
    lines = [f"source: {source.name}", f"url: {source.url}", f"last_updated: {last_updated}", ""]
    for ref, heading, url, text in sections:
        if not is_trusted_url(url):
            raise ValueError(f"{source.stem}: section link {url} is not on a trusted host")
        lines += [f"### {ref.replace('|', '/')} | {heading.replace('|', '/')} | {url}", text, ""]
    path = config.LAW_TEXT_DIR / f"{source.stem}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{source.name}: {len(sections)} sections, updated {last_updated} -> {path.name}")
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="fetch one source by its stem")
    parser.add_argument("--list", metavar="STEM", help="print every section and schedule of an Act")
    args = parser.parse_args()
    by_stem = {s.stem: s for s in TRUSTED_SOURCES}

    if args.list:
        source = by_stem[args.list]
        _, units = legislation_units(source)
        for kind, number, heading, _, _ in units:
            print(f"{source.label if kind == 'section' else 'Sch '}{number}  {heading}")
        return

    if args.only and args.only not in by_stem:
        sys.exit(f"unknown source {args.only!r}; choose from {', '.join(by_stem)}")
    failed = []
    for source in [by_stem[args.only]] if args.only else TRUSTED_SOURCES:
        try:
            (fetch_legislation if source.kind == "legislation" else fetch_guidance)(source)
        except Exception as error:  # keep going so one bad source doesn't block the rest
            print(f"FAILED {source.name}: {error}")
            failed.append(source.stem)
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
