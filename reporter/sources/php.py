"""
Report problems found in PHP logs
"""

import json
import re
import urllib

from common import KibanaSource

from reporter.helpers import is_main_dc_host, generalize_sql
from reporter.reports import Report


class PHPLogsSource(KibanaSource):
    """ Shared between PHP logs providers """
    REPORT_TEMPLATE = """
{full_message}

*URL*: {url}
*Env*: {env}

{{code}}
@source_host = {source_host}

@context = {context_formatted}

@fields = {fields_formatted}
{{code}}
    """


class PHPErrorsSource(PHPLogsSource):
    """ Get PHP errors from elasticsearch """
    REPORT_LABEL = 'PHPErrors'

    def _get_entries(self, query):
        """ Return matching entries by given prefix """
        return self._kibana.query_by_string(query='@message:"^{}"'.format(query), limit=self.LIMIT)

    def _filter(self, entry):
        """ Remove log entries that are not coming from main DC or lack key information """
        message = entry.get('@message', '')
        host = entry.get('@source_host', '')

        # filter out by host
        # "@source_host": "ap-s10",
        if not is_main_dc_host(host):
            return False

        # filter out errors without a clear context
        # on line 115
        if re.search(r'on line \d+', message) is None:
            return False

        return True

    def _get_kibana_url(self, entry):
        """
        Get the link to Kibana dashboard showing the provided error log entry
        """
        message = entry.get('@message')
        if not message:
            return None

        # match appropriate hosts
        # ap-s42:  ap-s*
        # task-s1: task-s*
        host = entry.get('@source_host')
        if host is not None:
            host_regexp = host.split('-')[0] + '-s*'
        else:
            host_regexp = 'ap-s*'

        # split the message
        # PHP Warning: Invalid argument supplied for foreach() in /usr/wikia/slot1/3823/src/extensions/wikia/PhalanxII/templates/PhalanxSpecial_main.php on line 141
        matches = re.match(r'^(.*) in /usr/wikia/slot1/\d+(.*)$', message)

        if not matches:
            return None

        return self.KIBANA_URL.format(
            query=urllib.quote('@source_host: {host} AND "{message}" AND "{file}"'.format(
                host=host_regexp, message=matches.group(1).replace(',', ''), file=matches.group(2)
            )),
            fields=','.join(['@timestamp', '@message', '@fields.url', '@source_host'])
        )

    def _normalize(self, entry):
        """
        Normalize given message by removing variables like server name
        to improve grouping of messages

        PHP Fatal Error: Call to a member function getText() on a non-object in /usr/wikia/slot1/3006/src/includes/api/ApiParse.php on line 20

        will become:

        Call to a member function getText() on a non-object in /includes/api/ApiParse.php on line 20
        """
        message = entry.get('@message')

        # remove exception prefix
        # Exception from line 141 of /includes/wikia/nirvana/WikiaView.class.php:
        message = re.sub(r'Exception from line \d+ of [^:]+:', 'Exception:', message)

        # remove HTTP adresses
        # Missing or invalid pubid from http://dragonball.wikia.com/__varnish_liftium/config in /var/www/liftium/delivery/config.php on line 17
        message = re.sub(r'https?://[^\s]+', '<URL>', message)

        # remove release-specific part
        # /usr/wikia/slot1/3006/src
        message = re.sub(r'/usr/wikia/slot1/\d+/src', '', message)

        # remove XML parsing errors details
        # Tag figure invalid in Entity, line: 286
        # Tag X invalid in Entity, line: N
        message = re.sub(r'Tag \w+ invalid in Entity, line: \d+', 'Tag X invalid in Entity, line: N', message)

        # remove popen() arguments
        message = re.sub(r'popen\([^\)]+\)', 'popen(X)', message)

        # remove exec arguments
        message = re.sub(r'Unable to fork \[[^\]]+\]', 'Unable to fork [X]', message)

        # normalize /tmp paths
        message = re.sub(r'/tmp/\w+', '/tmp/X', message)

        # normalize "17956864 bytes"
        message = re.sub(r'\d+ bytes', 'N bytes', message)

        # normalize preg_match() related warnings
        message = re.sub(r'Unknown modifier \'\w+\'', 'Unknown modifier X', message)
        message = re.sub(r'Compilation failed: unmatched parentheses at offset \d+',
                         'Compilation failed: unmatched parentheses at offset N', message)

        # update the entry
        entry['@message_normalized'] = message

        # production or preview?
        env = self._get_env_from_entry(entry)

        return 'PHP-{}-{}'.format(message, env)

    def _get_report(self, entry):
        """ Format the report to be sent to JIRA """
        description = self.REPORT_TEMPLATE.format(
            env=self._get_env_from_entry(entry),
            source_host=entry.get('@source_host', 'n/a'),
            context_formatted=json.dumps(entry.get('@context', {}), indent=True),
            fields_formatted=json.dumps(entry.get('@fields', {}), indent=True),
            full_message=entry.get('@message'),
            url=self._get_url_from_entry(entry) or 'n/a'
        ).strip()

        kibana_url = self._get_kibana_url(entry)
        if kibana_url:
            description += '\n\n*Still valid?* Check [Kibana dashboard|{url}]'.format(url=kibana_url)

        return Report(
            summary=entry.get('@message_normalized'),
            description=description,
            label=self.REPORT_LABEL
        )


class DBQueryErrorsSource(PHPLogsSource):
    """ Get DB errors triggered by PHP application from elasticsearch """
    REPORT_LABEL = 'DBQueryErrors'

    FULL_MESSAGE_TEMPLATE = """
*Query*: {{noformat}}{query}{{noformat}}
*Function*: {function}
*DB server*: {server}
*Error*: {error}

h5. Backtrace
* {backtrace}
"""

    # MySQL error codes
    # @see https://dev.mysql.com/doc/refman/5.5/en/error-messages-server.html
    ER_LOCK_WAIT_TIMEOUT = 1205
    ER_LOCK_DEADLOCK = 1213

    def _get_entries(self, query):
        """ Return matching exception logs """
        return self._kibana.get_rows(match={"@exception.class": 'DBQueryError'}, limit=self.LIMIT)

    def _filter(self, entry):
        """ Remove log entries that are not coming from main DC """
        host = entry.get('@source_host', '')

        # filter out by host
        # "@source_host": "ap-s10",
        if not is_main_dc_host(host):
            return False

        # skip deadlocks (PLATFORM-1110)
        context = entry.get('@context', {})
        if context.get('errno') in [self.ER_LOCK_DEADLOCK, self.ER_LOCK_WAIT_TIMEOUT]:
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
                return '{}-{}'.format(generalize_sql(query), context.get('errno'))

        return None

    def _get_report(self, entry):
        """ Format the report to be sent to JIRA """
        context = entry.get('@context')

        query = context.get('query')
        normalized = generalize_sql(query)

        backtrace = entry.get('@exception', {}).get('trace', [])

        # remove server IP from error message
        error_no_ip = context.get('error').\
            replace('({})'.format(context.get('server')), '').\
            strip()

        # format the report
        full_message = self.FULL_MESSAGE_TEMPLATE.format(
            query=query,
            error=error_no_ip,
            function=context.get('function'),
            server=context.get('server'),
            backtrace='\n* '.join(backtrace)
        ).strip()

        description = self.REPORT_TEMPLATE.format(
            env=self._get_env_from_entry(entry),
            source_host=entry.get('@source_host', 'n/a'),
            context_formatted=json.dumps(entry.get('@context', {}), indent=True),
            fields_formatted=json.dumps(entry.get('@fields', {}), indent=True),
            full_message=full_message,
            url=self._get_url_from_entry(entry) or 'n/a'
        ).strip()

        return Report(
            summary='[DB error {err}] {function} - {query}'.format(
                err=error_no_ip, function=context.get('function'), query=normalized),
            description=description,
            label=self.REPORT_LABEL
        )

    @staticmethod
    def _get_context_from_entry(entry):
        """ Parse message coming from MediaWiki and extract key information """
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
            if ':' in line:
                [key, value] = line.split(":", 1)
                parsed[key] = value.strip()

        # normalize "DatabaseBase::sourceFile( /usr/wikia/slot1/3690/src/maintenance/cleanupStarter.sql )"
        function = parsed.get('Function', '')
        function = re.sub(r'/usr/wikia/slot1/\d+/src', '', function)

        context = {
            'query': parsed.get('Query'),
            'function': function,
            'error': '{} {}'.format(context.get('errno'), context.get('err')),
        }

        return context


class DBQueryNoLimitSource(PHPLogsSource):
    """ Get DB queries that return excessive number of rows """
    REPORT_LABEL = 'DBQueryNoLimit'

    ROWS_THRESHOLD = 2000

    FULL_MESSAGE_TEMPLATE = """
The database query below returned far too many rows. Please use a proper LIMIT statement.

*Query*: {{noformat}}{query}{{noformat}}
*Function*: {function}
*Rows returned*: {num_rows}

h5. Backtrace
* {backtrace}
"""

    def _get_entries(self, query):
        """ Return matching exception logs """
        # @see http://www.solrtutorial.com/solr-query-syntax.html
        return self._kibana.query_by_string(
            query='@context.num_rows: [{} TO *]'.format(self.ROWS_THRESHOLD),
            limit=self.LIMIT
        )

    def _filter(self, entry):
        """ Remove log entries that are not coming from main DC """
        if not entry.get('@message', '').startswith('SQL '):
            return False

        # filter out by host
        # "@source_host": "ap-s10",
        host = entry.get('@source_host', '')
        if not is_main_dc_host(host):
            return False

        # remove those that do not return enough rows
        context = entry.get('@context', dict())
        if context.get('num_rows', 0) < self.ROWS_THRESHOLD:
            return False

        return True

    def _normalize(self, entry):
        """ Normalize the entry using the query and the method that made it """
        message = entry.get('@message')
        context = entry.get('@context', dict())

        return '{}-{}-no-limit'.format(generalize_sql(message), context.get('method'))

    def _get_report(self, entry):
        """ Format the report to be sent to JIRA """
        context = entry.get('@context')

        query = entry.get('@message')
        query = re.sub(r'^SQL', '', query).strip()  # remove "SQL" message prefix
        backtrace = entry.get('@exception', {}).get('trace', [])

        # format the report
        full_message = self.FULL_MESSAGE_TEMPLATE.format(
            query=query,
            function=context.get('method'),
            num_rows=context.get('num_rows'),
            backtrace='\n* '.join(backtrace)
        ).strip()

        description = self.REPORT_TEMPLATE.format(
            env=self._get_env_from_entry(entry),
            source_host=entry.get('@source_host', 'n/a'),
            context_formatted=json.dumps(entry.get('@context', {}), indent=True),
            fields_formatted=json.dumps(entry.get('@fields', {}), indent=True),
            full_message=full_message,
            url=self._get_url_from_entry(entry) or 'n/a'
        ).strip()

        return Report(
            summary='[{method}] The database query returns {rows}k+ rows'.format(
                method=context.get('method'), rows=context.get('num_rows') / 1000),
            description=description,
            label=self.REPORT_LABEL
        )
