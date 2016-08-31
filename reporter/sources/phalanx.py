"""
Report issues from Phalanx service
"""
import re

from common import KibanaSource

from reporter.reports import Report


class PhalanxSource(KibanaSource):
    REPORT_LABEL = 'Phalanx'

    REPORT_TEMPLATE = """
h3. {message}

*Logger name*: {{{{{logger_name}}}}}
*Thread name*: {{{{{thread_name}}}}}

h3. Stacktrace

{{code}}
{stack_trace}
{{code}}
    """

    LIMIT = 10000

    def _get_entries(self, query):
        # https://kibana.wikia-inc.com/#/dashboard/elasticsearch/Phalanx%20service%20logs
        # -lvl: "INFO" - skip INFO level
        return self._kibana.query_by_string(query='appname: "phalanx" AND -lvl: "INFO"', limit=self.LIMIT)

    def _filter(self, entry):
        return True

    def _normalize(self, entry):
        message = entry.get('@message')
        message = re.sub(r'phalanx-\w\d', 'phalanx-*', message)
        message = re.sub(r'\d+.\d+.\d+.\d+:\d+', 'x.x.x.x:x', message)

        # X-Request-Id: be0babcc-86da-4b27-bc1f-9025d314f745
        message = re.sub(r'X-Request-Id: [a-z0-9-]+', '', message).rstrip()

        return '{}-{}-{}'.format(self.REPORT_LABEL, entry.get('logger_name'), message)

    def _get_kibana_url(self, entry):
        """
        Get the link to Kibana dashboard showing the provided error log entry
        """
        return self.format_kibana_url(
            query='appname: "phalanx" AND -lvl: "INFO" AND logger_name: "{logger_name}"'.format(
                logger_name=entry.get('logger_name')
            ),
            columns=['@timestamp', '@source_host', 'logger_name', 'lvl', '@message', 'stack_trace']
        )

    def _get_report(self, entry):
        """ Format the report to be sent to JIRA """
        # format the report
        description = self.REPORT_TEMPLATE.format(
            message=entry.get('@message'),
            logger_name=entry.get('logger_name'),
            thread_name=entry.get('thread_name'),
            stack_trace=entry.get('stack_trace', 'n/a').strip()
        ).strip()

        return Report(
            summary='[Phalanx] {logger_name}: {message}'.format(
                logger_name=entry.get('logger_name'), message=entry.get('@message')),
            description=description,
            label=self.REPORT_LABEL
        )
