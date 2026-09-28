import unittest
from datetime import date
from unittest.mock import Mock, patch

import monitor


OAI_PAGE = b'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header><identifier>oai:arXiv.org:2609.12345</identifier></header>
      <metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
        <id>2609.12345</id><created>2026-09-24</created>
        <authors><author><keyname>Smith</keyname><forenames>Jane</forenames></author></authors>
        <title>  Battery research  </title><categories>cs.AI cs.LG</categories>
        <abstract>Machine learning for battery materials</abstract>
      </arXiv></metadata>
    </record>
    <record>
      <metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
        <id>2609.12346</id><created>2026-09-24</created>
        <title>Unrelated</title><abstract>Machine learning for finance</abstract>
      </arXiv></metadata>
    </record>
    <record>
      <metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
        <id>2609.12347</id><created>2026-09-20</created>
        <title>Old</title><abstract>Machine learning for battery materials</abstract>
      </arXiv></metadata>
    </record>
  </ListRecords>
</OAI-PMH>'''


class OaiFallbackTests(unittest.TestCase):
    @patch.object(monitor.time, "sleep")
    @patch.object(monitor.requests, "get")
    def test_filters_and_deduplicates_cross_listed_records(self, get, sleep):
        get.return_value = Mock(content=OAI_PAGE)
        query = "(cat:cs.AI+OR+cat:cs.LG)+AND+(abs:%22machine+learning%22)+AND+(abs:battery)"

        papers = monitor.search_arxiv_oai(query, date(2026, 9, 22))

        self.assertEqual([paper["arxiv_id"] for paper in papers], ["2609.12345"])
        self.assertEqual(papers[0]["authors"], "Jane Smith")
        self.assertEqual(papers[0]["published_date"], "2026-09-24")
        self.assertEqual(papers[0]["pdf_url"], "https://arxiv.org/pdf/2609.12345")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["set"], "cs:cs:AI")
        sleep.assert_called_once_with(monitor.REQUEST_INTERVAL)

    @patch.object(monitor.requests, "get")
    def test_invalid_query_fails_closed(self, get):
        with self.assertRaises(monitor.ArxivQueryError):
            monitor.search_arxiv_oai("cat:cs.AI", date(2026, 9, 22))
        get.assert_not_called()

    @patch.object(monitor, "export_viewer_json_from_excel")
    @patch.object(monitor, "write_llm_output_json")
    @patch.object(monitor, "clear_prelim_state")
    @patch.object(monitor, "save_crawled_ids_batch")
    @patch.object(monitor, "load_prelim_keep_ids", return_value=set())
    @patch.object(monitor, "load_prelim_candidates", return_value=[{"arxiv_id": "2609.12345"}])
    def test_rejected_prelim_papers_are_not_requeued(
        self, candidates, keep, save_ids, clear, output, viewer
    ):
        monitor.process_prelim_approved_papers({}, set())
        save_ids.assert_called_once_with(["2609.12345"])
        clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
