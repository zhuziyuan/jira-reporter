"""
Various data providers
"""
import hashlib
import json
import logging
import re

from reporter.helpers import is_main_dc_host
from reporter.reports import Report
from wikia.common.kibana import Kibana


class Source(object):
    """ An abstract class for data providers to inherit from """
    def __init__(self):
        self._logger = logging.getLogger(self.__class__.__name__)

    def query(self, query, threshold=50):
        # filter the entries
        entries = [entry for entry in self._get_entries(query) if self._filter(entry)]
        self._logger.info("Got {} entries after filtering".format(len(entries)))

        # group them
        normalized = self._normalize_entries(entries)

        # generate reports
        reports = self._generate_reports(normalized, threshold)

        # log all reports
        self._logger.info("Returning {} reports (with threshold set to {} applied)".format(len(reports), threshold))

        for report in reports:
            self._logger.info("> {summary} ({counter} instances)".format(
                summary=report.get_summary(),
                counter=report.get_counter()
            ))

        return reports

    def _normalize_entries(self, entries):
        """ Run all entries through _normalize method """
        normalized = dict()

        for entry in entries:
            key = self._normalize(entry)

            # all entries will be grouped
            # using the key return by _normalize method
            if key is not None:
                if key not in normalized:
                    normalized[key] = {
                        'cnt': 0,
                        'entry': entry
                    }

                normalized[key]['cnt'] += 1
            else:
                # self._logger.info('Entry not normalized: {}'.format(entry))
                pass

        return normalized

    def _generate_reports(self, items, threshold):
        reports = list()

        for key, item in items.iteritems():
            report = self._get_report(item['entry'])

            if item['cnt'] < threshold:
                self._logger.info('Skipped "{}" ({} occurrences)'.format(report.get_summary(), item['cnt']))
                continue

            # update the report with the "hash" generated previously via _normalize
            m = hashlib.md5()
            m.update(key)
            report.set_unique_id(m.hexdigest())

            report.set_counter(item['cnt'])
            reports.append(report)

        return reports

    def _get_entries(self, query):
        raise Exception("This method needs to be overwritten in your class!")

    def _filter(self, entry):
        raise Exception("This method needs to be overwritten in your class!")

    def _normalize(self, entry):
        """
        Normalize given message by removing variables like server name
        to improve grouping of messages
        """
        raise Exception("This method needs to be overwritten in your class!")

    def _get_report(self, entry):
        """
        Return a report for a given entry
        """
        raise Exception("This method needs to be overwritten in your class!")


class KibanaSource(Source):
    """ elasticsearch-powered data provider """
    LIMIT = 100000

    ENV_PREVIEW = 'Preview'
    ENV_PRODUCTION = 'Production'

    PREVIEW_HOST = 'staging-s3'

    """ Kibana/elasticsearch-powered data-provider"""
    def __init__(self, period=3600):
        super(KibanaSource, self).__init__()
        self._kibana = Kibana(period=period)

    def _get_entries(self, query):
        return self._kibana.get_rows(query, limit=self.LIMIT)

    # helper methods
    def _get_url_from_entry(self, entry):
        """
        Get URL from given log entry
        :param entry: dict
        :return: bool|string
        """
        fields = entry.get('@fields', {})

        url = False
        try:
            if fields.get('server') and fields.get('url'):
                url = 'http://{}{}'.format(fields.get('server'), fields.get('url'))
        except UnicodeEncodeError:
            self._logger.error('URL parsing failed', exc_info=True)

        return url

    def _get_env_from_entry(self, entry):
        """
        Get environment for given log entry
        :param entry: dict
        :return: string
        """
        # preview -> staging-s3
        is_preview = entry.get('@source_host', '') == self.PREVIEW_HOST
        env = self.ENV_PREVIEW if is_preview is True else self.ENV_PRODUCTION

        return env


class PHPLogsSource(KibanaSource):
    """ Shared between PHP logs providers """
    REPORT_TEMPLATE = """
{full_message}

URL: {url}
Env: {env}

{{code}}
@context = {context_formatted}

@fields = {fields_formatted}
{{code}}
    """


class PHPErrorsSource(PHPLogsSource):
    """ Get PHP errors from elasticsearch """
    REPORT_LABEL = 'PHPErrors'

    _query = ''

    def query(self, query, threshold=50):
        self._query = query
        self._logger.info("Query: '{}'".format(query))

        """
        Search for messages starting with "query"
        """
        return super(PHPErrorsSource, self).query(query, threshold)

    def _get_entries(self, query):
        return self._kibana.query_by_string(query='@message:"^{}"'.format(query), limit=self.LIMIT)

    def _filter(self, entry):
        message = entry.get('@message', '')
        host = entry.get('@source_host', '')

        if not message.startswith(self._query):
            return False

        # filter out by host
        # "@source_host": "ap-s10",
        if not is_main_dc_host(host):
            return False

        # filter out errors without a clear context
        # on line 115
        if re.search(r'on line \d+', message) is None:
            return False

        return True

    def _normalize(self, entry):
        """
        Normalize given message by removing variables like server name
        to improve grouping of messages

        PHP Fatal Error: Call to a member function getText() on a non-object in /usr/wikia/slot1/3006/src/includes/api/ApiParse.php on line 20

        will become:

        Call to a member function getText() on a non-object in /includes/api/ApiParse.php on line 20
        """
        message = entry.get('@message')

        # remove the prefix
        # PHP Fatal error:
        message = re.sub(r'PHP (Fatal error|Warning):', '', message, flags=re.IGNORECASE).strip()

        # remove exception prefix
        # Exception from line 141 of /includes/wikia/nirvana/WikiaView.class.php:
        message = re.sub(r'Exception from line \d+ of [^:]+:', 'Exception:', message)

        # remove HTTP adresses
        # Missing or invalid pubid from http://dragonball.wikia.com/__varnish_liftium/config in /var/www/liftium/delivery/config.php on line 17
        message = re.sub(r'https?://[^\s]+', '<URL>', message)

        # remove release-specific part
        # /usr/wikia/slot1/3006/src
        message = re.sub(r'/usr/wikia/slot1/\d+/src', '', message)

        # update the entry
        entry['@message_normalized'] = message

        # production or preview?
        env = self._get_env_from_entry(entry)

        return 'PHP-{}-{}-{}'.format(self._query, message, env)

    def _get_report(self, entry):
        """
        Format the report to be reported to JIRA
        """
        description = self.REPORT_TEMPLATE.format(
            env=self._get_env_from_entry(entry),
            context_formatted=json.dumps(entry.get('@context', {}), indent=True),
            fields_formatted=json.dumps(entry.get('@fields', {}), indent=True),
            full_message=entry.get('@message'),
            url=self._get_url_from_entry(entry) or 'n/a'
        ).strip()

        return Report(
            summary='{}: {}'.format(self._query, entry.get('@message_normalized')),
            description=description,
            label=self.REPORT_LABEL
        )


class DBQueryErrorsSource(PHPLogsSource):
    """ Get DB errors triggered by PHP application from elasticsearch """
    REPORT_LABEL = 'DBQueryErrors'

    FULL_MESSAGE_TEMPLATE = """
Query: {query}
Function: {function}
Error: {error}

Backtrace:
* {backtrace}
"""

    def query(self, query='DBQueryError', threshold=50):
        self._logger.info("Query: exceptions of class '{}'".format(query))
        return super(DBQueryErrorsSource, self).query({"@exception.class": query}, threshold)

    def _filter(self, entry):
        host = entry.get('@source_host', '')

        # filter out by host
        # "@source_host": "ap-s10",
        if not is_main_dc_host(host):
            return False

        return True

    def _normalize(self, entry):
        """
        Normalize given SQL error using normalized query and error code
        """
        context = self._get_context_from_entry(entry)

        if context is not None:
            query = context.get('query')

            if query is not None:
                entry.get('@context', {}).update(context)
                return '{}-{}'.format(self._generalize_sql(query), context.get('errno'))

        return None

    def _get_report(self, entry):
        context = entry.get('@context')

        query = context.get('query')
        normalized = self._generalize_sql(query)

        backtrace = entry.get('@exception', {}).get('trace', [])

        # format the report
        full_message = self.FULL_MESSAGE_TEMPLATE.format(
            query=query,
            error=context.get('error'),
            function=context.get('function'),
            backtrace='\n* '.join(backtrace)
        ).strip()

        description = self.REPORT_TEMPLATE.format(
            env=self._get_env_from_entry(entry),
            context_formatted=json.dumps(entry.get('@context', {}), indent=True),
            fields_formatted=json.dumps(entry.get('@fields', {}), indent=True),
            full_message=full_message,
            url=self._get_url_from_entry(entry) or 'n/a'
        ).strip()

        return Report(
            summary='[DB error {}] {}'.format(context.get('error'), normalized),
            description=description,
            label=self.REPORT_LABEL
        )

    @staticmethod
    def _get_context_from_entry(entry):
        exception = entry.get('@exception', {})
        context = entry.get('@context', {})
        message = exception.get('message')

        if message is None:
            return None

        message = message.strip()

        """
        A database error has occurred.  Did you forget to run maintenance/update.php after upgrading?  See: https://www.mediawiki.org/wiki/Manual:Upgrading#Run_the_update_script
        Query: SELECT  DISTINCT `page`.page_namespace AS page_namespace,`page`.page_title AS page_title,`page`.page_id AS page_id, `page`.page_title  as sortkey FROM `page` WHERE 1=1  AND `page`.page_namespace IN ('6') AND `page`.page_is_redirect=0 AND 'Hal Homsar Solo' = (SELECT rev_user_text FROM `revision` WHERE `revision`.rev_page=page_id ORDER BY `revision`.rev_timestamp ASC LIMIT 1) ORDER BY page_title ASC LIMIT 0, 500
        Function: DPLMain:dynamicPageList
        Error: 1317 Query execution was interrupted (10.8.38.37)
        """

        # parse multiline message
        parsed = dict()

        for line in message.split("\n")[1:]:
            [key, value] = line.split(":", 1)
            parsed[key] = value.strip()

        context = {
            'query': parsed.get('Query'),
            'function': parsed.get('Function'),
            'error': '{} {}'.format(context.get('errno'), context.get('err')),
        }

        return context

    @staticmethod
    def _generalize_sql(sql):
        if sql is None:
            return None

        """
        Based on Mediawiki's Database::generalizeSQL
        """
        sql = re.sub(r"\\\\", '', sql)
        sql = re.sub(r"\\'", '', sql)
        sql = re.sub(r'\\"', '', sql)
        sql = re.sub(r"'.*'", 'X', sql)
        sql = re.sub(r'".*"', 'X', sql)

        # All newlines, tabs, etc replaced by single space
        sql = re.sub(r'\s+', ' ', sql)

        # All numbers => N
        sql = re.sub(r'-?[0-9]+', 'N', sql)

        return sql.strip()
