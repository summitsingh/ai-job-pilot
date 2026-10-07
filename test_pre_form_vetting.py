import unittest

from sweep import filter_postings, sponsorship_ok


class PreFormVettingTests(unittest.TestCase):
    def test_opt_in_requires_explicit_positive_sponsorship(self):
        base = {'title': 'Staff Backend Engineer', 'location': 'Remote (US)',
                'company': 'ExampleCo', 'salary_min': '120k', 'salary_max': '170k'}
        rows = [dict(base, url='https://jobs.ashbyhq.com/exampleco/role1', description='General engineering role'),
                dict(base, url='https://jobs.ashbyhq.com/exampleco/role2', description='We provide visa sponsorship, including H-1B transfers.'),
                dict(base, url='https://jobs.ashbyhq.com/exampleco/role3', description='We cannot provide visa sponsorship.')]
        ready, stats = filter_postings(rows, require_sponsorship=True)
        self.assertEqual([x['url'] for x in ready], [rows[1]['url']])
        self.assertEqual(stats['sponsor_fail'], 2)
        self.assertFalse(sponsorship_ok('We cannot provide visa sponsorship.', require_positive=True))
        self.assertFalse(sponsorship_ok('We do not offer visa sponsorship.', require_positive=True))
        self.assertFalse(sponsorship_ok('We do not sponsor visas.', require_positive=True))

    def test_imported_history_without_url_blocks_matching_company_title(self):
        posting = {'title': 'Senior Infrastructure Engineer', 'location': 'Remote (US)',
                   'company': 'ExampleCo', 'url': 'https://jobs.ashbyhq.com/exampleco/role9',
                   'description': 'Visa sponsorship available.'}
        past = [{'Company': 'exampleco', 'Title': 'Senior Infrastructure Engineer', 'Job URL': ''}]
        ready, stats = filter_postings([posting], history=past)
        self.assertEqual(ready, [])
        self.assertEqual(stats['dup'], 1)

    def test_distinct_job_ids_are_not_collapsed(self):
        base = {'title': 'Staff Backend Engineer', 'location': 'Remote (US)', 'company': 'ExampleCo',
                'description': 'Visa sponsorship available.'}
        rows = [dict(base, url='https://jobs.ashbyhq.com/exampleco/role1'),
                dict(base, url='https://jobs.ashbyhq.com/exampleco/role2')]
        ready, stats = filter_postings(rows, history=[{'url': rows[0]['url'], 'company': 'ExampleCo', 'title': rows[0]['title']}])
        # Existing fuzzy duplicate review can still withhold a distinct ID for
        # human review; exact URL-history dedup must not label it a duplicate.
        self.assertEqual(stats['dup'], 1)


if __name__ == '__main__':
    unittest.main()
