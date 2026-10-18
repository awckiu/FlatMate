import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import config
from app.rag.ingest import MAX_CHARS, parse_law_file
from app.rag.sources import TRUSTED_SOURCES, is_trusted_url
from scripts import copy_law, fetch_law, ingest_law

SOURCES = {s.stem: s for s in TRUSTED_SOURCES}

ACT_XML = b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
  xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
 <ukm:Metadata><dc:modified>2026-06-19</dc:modified></ukm:Metadata>
 <Primary><Body><Part><Number>PART 1</Number><Title>Tenant fees</Title>
  <P1group><Title>Prohibitions applying to landlords</Title>
   <P1 id="section-1" DocumentURI="http://www.legislation.gov.uk/ukpga/2019/4/section/1"><Pnumber>1</Pnumber>
    <P1para><P2><Pnumber>1</Pnumber><P2para><Text>A landlord must not require a relevant person to make a prohibited payment to the landlord.</Text></P2para></P2>
    <P2><Pnumber>2</Pnumber><P2para><Text>A landlord must not require a relevant person to enter into a contract for services.</Text></P2para></P2></P1para>
   </P1></P1group>
  <P1group><Title>Treatment of holding deposit</Title>
   <P1 id="section-5" DocumentURI="http://www.legislation.gov.uk/ukpga/2019/4/section/5"><Pnumber>5</Pnumber>
    <P1para><Text>Schedule 2 makes provision about the treatment of a holding deposit by landlords and agents.</Text>
    <BlockAmendment><P1group><Title>Quoted inserted section</Title><P1><Pnumber>99</Pnumber><P1para><Text>Quoted text that amends another Act and must not become its own section.</Text></P1para></P1></P1group></BlockAmendment>
    </P1para></P1></P1group>
  <P1group><Title>Repealed section</Title>
   <P1 id="section-5A" DocumentURI="http://www.legislation.gov.uk/ukpga/2019/4/section/5A"><Pnumber>5A</Pnumber><P1para><Text>. . .</Text></P1para></P1></P1group>
  <P1group><Title>Enforcement by district councils</Title>
   <P1 id="section-7"><Pnumber>7</Pnumber><P1para><Text>Not in the keep list, so it is left out of the file.</Text></P1para></P1></P1group>
 </Part></Body>
 <Schedules><Schedule DocumentURI="http://www.legislation.gov.uk/ukpga/2019/4/schedule/1"><Number>SCHEDULE 1</Number>
  <TitleBlock><Title>Permitted payments</Title></TitleBlock>
  <ScheduleBody><P1group><Title>Tenancy deposits</Title><P1><Pnumber>2</Pnumber><P1para><Text>A tenancy deposit is a permitted payment if it does not exceed five weeks' rent.</Text></P1para></P1></P1group></ScheduleBody>
 </Schedule></Schedules></Primary>
</Legislation>"""

GUIDE_JSON = {
    "public_updated_at": "2024-11-05T15:16:55+00:00",
    "details": {
        "parts": [
            {"title": "Overview", "slug": "overview", "body": "<p>Your landlord must put your deposit in a scheme.</p><ul><li>DPS</li><li>mydeposits</li></ul>"},
        ]
    },
}


class LawSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        patcher = patch.object(config, "LAW_TEXT_DIR", Path(self.temp_dir.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_official_https_hosts_are_trusted(self):
        self.assertTrue(is_trusted_url("https://www.legislation.gov.uk/ukpga/2019/4"))
        self.assertTrue(is_trusted_url("https://www.gov.uk/how-to-rent"))
        self.assertFalse(is_trusted_url("http://www.gov.uk/how-to-rent"))
        self.assertFalse(is_trusted_url("https://www.gov.uk.example.com/law"))
        self.assertFalse(is_trusted_url("https://shelter.org.uk/housing_advice"))
        for source in TRUSTED_SOURCES:
            self.assertTrue(is_trusted_url(source.url), source.url)

    def test_fetch_refuses_untrusted_urls_without_a_request(self):
        with patch("scripts.fetch_law.urllib.request.urlopen") as urlopen:
            with self.assertRaises(ValueError):
                fetch_law.fetch("https://example.com/fake-law")
        urlopen.assert_not_called()

    def test_legislation_is_trimmed_to_the_keep_list(self):
        with patch("scripts.fetch_law.fetch", return_value=ACT_XML):
            path = fetch_law.fetch_legislation(SOURCES["tenant_fees_act_2019"])
        law = parse_law_file(Path(path).read_text(encoding="utf-8"))

        self.assertEqual(law.source, "Tenant Fees Act 2019")
        self.assertEqual(law.last_updated, "2026-06-19")
        self.assertEqual([c.section for c in law.chunks], ["s.1", "s.5", "Sch 1"])
        s1, s5, sch1 = law.chunks
        self.assertEqual(s1.heading, "Prohibitions applying to landlords")
        self.assertEqual(s1.url, "https://www.legislation.gov.uk/ukpga/2019/4/section/1")
        self.assertIn("(1) A landlord must not require", s1.text)
        self.assertIn("(2) A landlord", s1.text)
        self.assertNotIn("Part", s1.text)
        # Text quoted inside an amendment stays part of its section, never a section of its own.
        self.assertIn("Quoted text", s5.text)
        self.assertEqual(sch1.heading, "Permitted payments")
        self.assertIn("2 A tenancy deposit is a permitted payment", sch1.text)

    def test_regulations_are_cited_as_reg(self):
        regs = replace(SOURCES["hmo_management_regulations_2006"], keep=("reg 1",))
        with patch("scripts.fetch_law.fetch", return_value=ACT_XML):
            law = parse_law_file(Path(fetch_law.fetch_legislation(regs)).read_text(encoding="utf-8"))
        self.assertEqual([c.section for c in law.chunks], ["reg 1"])

    def test_guidance_parts_become_sections(self):
        with patch("scripts.fetch_law.fetch", return_value=json.dumps(GUIDE_JSON).encode()):
            path = fetch_law.fetch_guidance(SOURCES["deposit_protection"])
        law = parse_law_file(Path(path).read_text(encoding="utf-8"))

        self.assertEqual(law.last_updated, "2024-11-05")
        self.assertEqual(len(law.chunks), 1)
        self.assertEqual(law.chunks[0].url, "https://www.gov.uk/tenancy-deposit-protection/overview")
        self.assertEqual(law.chunks[0].text, "Your landlord must put your deposit in a scheme.\nDPS\nmydeposits")

    def test_long_sections_split_at_line_breaks(self):
        line = "x" * 1500
        text = (
            "source: Example\nurl: https://www.gov.uk/example\nlast_updated: 2026-01-01\n\n"
            f"### s.1 | Long | https://www.gov.uk/example\n{line}\n{line}\n{line}\n"
        )
        chunks = parse_law_file(text).chunks
        self.assertEqual([len(c.text) for c in chunks], [3001, 1500])
        self.assertTrue(all(len(c.text) <= MAX_CHARS for c in chunks))

    def test_ingest_refuses_files_that_do_not_match_the_allowlist(self):
        source = SOURCES["assured_periodic_tenancies_tenants"]
        path = config.LAW_TEXT_DIR / f"{source.stem}.txt"
        header = f"source: {source.name}\nurl: {source.url}\nlast_updated: 2026-05-01\n\n"

        path.write_text(header + "### Rent increases | Rent increases | https://www.gov.uk/assured-periodic-tenancies-tenants/rent-increases\nText\n", encoding="utf-8")
        self.assertEqual(len(ingest_law.load(source).chunks), 1)

        path.write_text(header + "### Repairs | Repairs | https://blog.example.com/rent\nText\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            ingest_law.load(source)

        path.write_text(header.replace(source.name, "Some other guide") + "### A | A | https://www.gov.uk/a\nText\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            ingest_law.load(source)


class CopyLawTests(unittest.TestCase):
    TEST_DB = "postgres://user:pw@test.example.com:5432/tsdb?sslmode=require"

    def test_same_database_ignores_credentials_and_options(self):
        self.assertTrue(copy_law.same_database(self.TEST_DB, "postgres://other:x@test.example.com/tsdb"))
        self.assertFalse(copy_law.same_database(self.TEST_DB, "postgres://user:pw@main.example.com:5432/tsdb"))
        self.assertFalse(copy_law.same_database(self.TEST_DB, "postgres://user:pw@test.example.com:5432/other"))

    def test_refuses_to_copy_a_database_onto_itself(self):
        with (
            patch.object(config, "DATABASE_URL", self.TEST_DB),
            patch.dict("os.environ", {"MAIN_DATABASE_URL": self.TEST_DB}),
            patch("scripts.copy_law.connect") as connect,
        ):
            with self.assertRaises(SystemExit) as raised:
                copy_law.main([])
        self.assertIn("same database", str(raised.exception.code))
        self.assertNotIn(self.TEST_DB, str(raised.exception.code))
        connect.assert_not_called()

    def test_refuses_when_target_variable_is_missing(self):
        with (
            patch.object(config, "DATABASE_URL", self.TEST_DB),
            patch.dict("os.environ", {}, clear=True),
            patch("scripts.copy_law.connect") as connect,
        ):
            with self.assertRaises(SystemExit) as raised:
                copy_law.main(["--to", "NO_SUCH_DATABASE_URL"])
        self.assertIn("NO_SUCH_DATABASE_URL is not set", str(raised.exception.code))
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
