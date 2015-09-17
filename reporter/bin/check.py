#!/usr/bin/env python
"""
This script checks PHP log for fatals, warnings, DB queries errors
and reports issues to JIRA when given thresholds are reached
"""
import logging

from reporter.reporters import Jira
from reporter.sources import PHPErrorsSource, PHPExceptionsSource, DBQueryErrorsSource,\
    DBQueryNoLimitSource, NotCachedWikiaApiResponsesSource, KilledDatabaseQueriesSource, \
    PHPAssertionsSource, PandoraErrorsSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# get reports from various sources
reports = []

# PHP warnings and errors
source = PHPErrorsSource()

reports += source.query("PHP Fatal Error", threshold=5)
reports += source.query("PHP Catchable Fatal", threshold=5)
reports += source.query("PHP Warning", threshold=50)
reports += source.query("PHP Strict Standards", threshold=200)

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/Severity%20error
reports += PHPExceptionsSource().query(threshold=50)

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/DBQuery%20errors
reports += DBQueryErrorsSource().query(threshold=20)

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/PLATFORM-836
reports += DBQueryNoLimitSource().query(threshold=50)

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/wikia.php%20caching%20disabled
reports += NotCachedWikiaApiResponsesSource().query(threshold=500)  # we serve 75k not cached responses an hour

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/drozdo.pt-kill
reports += KilledDatabaseQueriesSource().query(threshold=0)

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/AssertionException
reports += PHPAssertionsSource().query(threshold=5)

# @see https://kibana.wikia-inc.com/#/dashboard/elasticsearch/PLATFORM-1420
reports += PandoraErrorsSource().query(threshold=5)

logging.info('Reporting {} issues...'.format(len(reports)))
reporter = Jira()

reported = 0
for report in reports:
    if reporter.report(report):
        reported += 1

logging.info('Reported {} tickets'.format(reported))