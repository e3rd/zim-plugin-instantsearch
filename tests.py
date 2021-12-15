from collections import defaultdict
from unittest import TestCase, main

import gi

gi.require_version('Gtk', '3.0')

from instantsearch import SearchController, _MenuItem

cached_titles = [
    'Journal',
    'Journal:2021',
    'Journal:2021:12',
    'Journal:foo',
    'Journal:foo:bar',
    'Journal:foo:bar:fourth',
    'test',
    'Journal:test',
    'foo test',
    'foo (test)'
]


class TestSearch(TestCase):
    def _search(self, query, expected):
        menu = defaultdict(_MenuItem)
        SearchController.header_search(query, menu, cached_titles)
        self.assertListEqual(expected, [*menu])

    def test_header(self):
        self._search("foo", ['Journal:foo', 'Journal:foo:bar', 'Journal:foo:bar:fourth', 'foo test', 'foo (test)'])
        self._search("tes", ['test', 'Journal:test', 'foo test', 'foo (test)'])


if __name__ == '__main__':
    main()
