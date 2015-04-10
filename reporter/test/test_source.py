"""
Set of unit tests for Source class
"""
import json
import unittest

from ..sources import Source
from ..reports import Report


class DummySource(Source):
    """ Dummy class used for testing Source class """

    SKIP_ME_MESSAGE = 'Skip me!'

    def __init__(self):
        super(DummySource, self).__init__()

        self._reports_count = 0

    def _get_entries(self, query):
        """ Get mocked entries """
        return [
            {
                '@message': 'Foo Bar',
                '@context': [123, query]
            },
            {
                '@message': 'test'
            },
            {
                '@message': 'Foo-Bar',
                '@context': [456, query]
            },
            {
                'foo': 'bar'
            },
            {
                '@message': self.SKIP_ME_MESSAGE
            }
        ]

    def _filter(self, entry):
        """ Remove entries without the message """
        return entry.get('@message') is not None

    def _normalize(self, entry):
        """ Group entries by the message """
        msg = entry.get('@message')

        if msg == self.SKIP_ME_MESSAGE:
            return None

        return msg.replace(' ', '-')

    def _get_report(self, entry):
        """ Generate Report instance for a given entry """
        self._reports_count += 1

        return Report(
            summary='[Error] {msg}'.format(msg=entry.get('@message')),
            description=json.dumps(entry.get('@context'))
        )

    def _send_stats(self, query, entries, reports):
        """ Do not send any stats """
        pass

    def get_reports_count(self):
        """ Used by test class """
        return self._reports_count

    pass


class SourceTestClass(unittest.TestCase):
    """ Test Source class via DummySource """

    QUERY = 'foo'

    def test_source_flow(self):
        """ Test the flow in Source class"""
        source = DummySource()

        # threshold set to '2' means that 'Foo Bar' report will be returned
        reports = source.query(query=self.QUERY, threshold=2)

        # two various reports where generated by the source (threshold not yet applied)
        assert source.get_reports_count() == 2

        # one report to be returned (threshold applied)
        assert len(reports) == 1

        # test the report
        report = reports[0]
        print report  # print the report in case the assertion below fails

        assert report.get_counter() == 2
        assert report.get_summary() == '[Error] Foo Bar'
        assert report.get_description() == '[123, "{query}"]'.format(query=self.QUERY)
        assert report.get_unique_id() == 'e5f9ec048d1dbe19c70f720e002f9cb1'
