import json

from reporter.helpers import is_production_host
from reporter.reports import Report

from common import PHPLogsSource


class PHPAssertionsSource(PHPLogsSource):
    """
    Report failed asserts reported by Wikia\Util\AssertionException
    """
    REPORT_LABEL = 'PHPAssertion'

    FULL_MESSAGE_TEMPLATE = """
h1. {assertion}

h5. Backtrace
* {backtrace}
"""

    KIBANA_URL = 'https://kibana.wikia-inc.com/#/dashboard/elasticsearch/AssertionException'

    def _get_entries(self, query):
        """ Return failed assertions logs """
        # @see http://www.solrtutorial.com/solr-query-syntax.html
        return self._kibana.get_rows(match={"@exception.class": "Wikia\\Util\\AssertionException"}, limit=self.LIMIT)

    def _filter(self, entry):
        # filter out by host
        # "@source_host": "ap-s10",
        host = entry.get('@source_host', '')
        if not is_production_host(host):
            return False

        return True

    def _normalize(self, entry):
        """ Normalize using the assertion class and message """
        exception = entry.get('@exception', {})

        return '{}-{}'.format(exception.get('class'), exception.get('message'))

    def _get_report(self, entry):
        """ Format the report to be sent to JIRA """
        exception = entry.get('@exception', {})
        backtrace = exception.get('trace', [])

        # format the report
        full_message = self.FULL_MESSAGE_TEMPLATE.format(
            assertion=exception.get('message'),
            backtrace='\n* '.join(backtrace)
        ).strip()

        description = self.REPORT_TEMPLATE.format(
            env=self._get_env_from_entry(entry),
            source_host=entry.get('@source_host', 'n/a'),
            context_formatted=None,
            fields_formatted=json.dumps(entry.get('@fields', {}), indent=True),
            full_message=full_message,
            url=self._get_url_from_entry(entry) or 'n/a'
        ).strip()

        description += '\n\n*Still valid?* Check [Kibana dashboard|{url}]'.format(url=self.KIBANA_URL)

        return Report(
            summary='[Assertion failed] {assertion}'.format(assertion=exception.get('message')),
            description=description,
            label=self.REPORT_LABEL
        )
