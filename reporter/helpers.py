"""
Set of helper functions
"""
import re


def is_production_host(host):
    """
    Return true if given host is from our main datacenter (i.e. SJC)
    """
    return re.search(r'^(ap|task|cron|liftium|staging)\-s', host) is not None


def generalize_sql(sql):
    """
    Removes most variables from an SQL query and replaces them with X or N for numbers.

    Based on Mediawiki's DatabaseBase::generalizeSQL
    """
    if sql is None:
        return None

    # MW comments
    # e.g. /* CategoryDataService::getMostVisited N.N.N.N */
    sql = re.sub(r'\s?/\*.+\*/', '', sql)

    sql = re.sub(r"\\\\", '', sql)
    sql = re.sub(r"\\'", '', sql)
    sql = re.sub(r'\\"', '', sql)
    sql = re.sub(r"'[^\']+'", 'X', sql)
    sql = re.sub(r'"[^\"]+"', 'X', sql)

    # All newlines, tabs, etc replaced by single space
    sql = re.sub(r'\s+', ' ', sql)

    # All numbers => N
    sql = re.sub(r'-?[0-9]+', 'N', sql)

    # WHERE foo IN ('880987','882618','708228','522330')
    sql = re.sub(r' IN\s*\([^)]+\)', ' IN (XYZ)', sql)

    return sql.strip()


def get_method_from_query(sql):
    """
    Gets method name from SQL query comment

    SELECT /* Foo::Bar 157.55.39.174 */ column from table where foo = 1

    Return: "Foo::bar"
    """
    matches = re.search(r'/\*([^\*]+)\*/', sql)

    # return the first part of "Foo::Bar 157.55.39.174" (i.e. without IP)
    return matches.group(1).strip().split(' ')[0] if matches else None
