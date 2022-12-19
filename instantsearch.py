#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Search instantly as you type. Edvard Rejthar
# https://github.com/e3rd/zim-plugin-instantsearch
#
# Note that the search might not work well in case of case-folded letters
# because re.IGNORECASE seem to perform str.lower only. A fix might be implemented if requested.
# Use case:
#   re.match("tsChüß".casefold(), "Tschüß".casefold()) # matches
#   re.match("tsChüss", "Tschüß", re.IGNORECASE) # does not match
#
#
import logging
from os.path import abspath
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from time import time, perf_counter
from types import SimpleNamespace
from typing import Dict, List, DefaultDict, NamedTuple, Optional, Union

from gi.repository import GObject, Gtk, Gdk
from gi.repository.GLib import markup_escape_text
from zim.actions import action
from zim.gui.mainwindow import MainWindow, MainWindowExtension
from zim.gui.widgets import Dialog
from zim.gui.widgets import InputEntry
from zim.history import HistoryList
from zim.newfs import base, File, LocalFile
from zim.notebook import Path as ZimPath
from zim.plugins import PluginClass
from zim.search import Query, SearchSelection

logger = logging.getLogger('zim.plugins.instantsearch')


class _FileCache(NamedTuple):
    path: ZimPath
    contents: str


file_cache: Dict[Path, _FileCache] = {}
# if search dialog closes, file cached are no longer fresh, might have been changed meanwhile
file_cache_fresh = True


class InstantSearchPlugin(PluginClass):
    plugin_info = {
        'name': _('Instant Search'),  # T: plugin name
        'description': _('''\
Instant search allows you to filter as you type feature known from I.E. OneNote.
When you hit Ctrl+E, small window opens, in where you can type.
As you type third letter, every page that matches your search is listed.
You can walk through by UP/DOWN arrow, hit Enter to stay on the page, or Esc to cancel.
 Much quicker than current Zim search.

(V1.2)
'''),
        'author': "Edvard Rejthar"

    }

    POSITION_CENTER = _('center')  # T: option value
    POSITION_RIGHT = _('right')  # T: option value

    PREVIEW_ONLY = "preview_only"
    PREVIEW_THEN_FULL = "preview_then_full"
    FULL_ONLY = "full_only"

    PREVIEW_MODE = (
        (PREVIEW_THEN_FULL, _('Preview then full view')),
        (PREVIEW_ONLY, _('Preview only')),
        (FULL_ONLY, _('Full view only')),
    )

    plugin_preferences = (
        # T: label for plugin preferences dialog
        ('title_match_char', 'string', _('Match title only if query starting by this char'), "!"),
        ('start_search_length', 'int', _('Start the search when number of letters written'), 3, (0, 10)),
        ('keystroke_delay', 'int', _('Keystroke delay before search'), 150, (0, 5000)),
        ('keystroke_delay_open', 'int', _('Keystroke delay for opening page in full view'
                                          '\n(Low value might prevent search list smooth navigation'
                                          ' if page is big.)'), 1500, (0, 5000)),
        ('preview_mode', 'choice', _('Preview mode'), PREVIEW_THEN_FULL, PREVIEW_MODE),
        ('preview_short', 'bool', _('Preview only matching lines'
                                    '\nOtherwise whole page is displayed if not too long.)'), False),
        ('highlight_search', 'bool', _('Highlight search'), True),
        ('ignore_subpages', 'bool', _("Ignore sub-pages (if ignored, search 'linux'"
                                      " would return page:linux but not page:linux:subpage"
                                      " (if in the subpage, there is no occurrence of string 'linux')"), True),
        # ('is_cached', 'bool',
        #  _("Cache results of a search to be used in another search. (Till the end of zim process.)"), True),
        ('open_when_unique', 'bool', _('When only one page is found, open it automatically.'), True),
        ('position', 'choice', _('Popup position'), POSITION_RIGHT, (POSITION_RIGHT, POSITION_CENTER))
    )


class InstantSearchMainWindowExtension(MainWindowExtension):
    gui: "Dialog"
    state: "State"
    cached_titles: List[str]
    window: MainWindow
    prevent_closing = False  # if `open_when_unique` is active, having single query in the result would immediately re-close the dialog

    def __init__(self, plugin, window):
        super().__init__(plugin, window)
        self.timeout = None
        self.timeout_open_page = None  # will open page after keystroke delay
        self.timeout_open_page_preview = None  # will open page after keystroke delay
        self.last_query = None
        self.query_o = None
        self.caret = None
        self.original_page = None
        self.original_history = None
        self.selection = None
        self.menu_page = None
        self.is_closed = None
        self.last_page = self.last_page_preview = None
        self.label_object = None
        self.input_entry = None
        self.label_preview = None
        self.preview_pane = None
        self._last_update = 0
        self.state = None
        self.caret = SimpleNamespace(pos=0, text="", stick=False)  # cursor position

        # preferences
        State.title_match_char = self.plugin.preferences['title_match_char']
        State.start_search_length = self.plugin.preferences['start_search_length']
        self.keystroke_delay_open = self.plugin.preferences['keystroke_delay_open']
        self.keystroke_delay = self.plugin.preferences['keystroke_delay']

    # noinspection PyArgumentList,PyUnresolvedReferences
    @action(_('_Instant search'), accelerator='<ctrl>e', menuhints='tools')  # T: menu item
    def instant_search(self):

        # init
        self.cached_titles: List[ZimPathStr] = []
        self.last_query = ""  # previous user input
        self.query_o = None
        self.original_page = self.window.page.name  # we return here after escape
        self.original_history = list(self.window.history.uistate["list"])
        self.selection = None
        # if not self.plugin.preferences['is_cached']:
            # reset last search results
            # State.reset()
        self.menu_page = None
        self.is_closed = False
        self.last_page = None

        # building quick title cache
        def build(start=""):
            o = self.window.notebook.pages
            for s in o.list_pages(ZimPath(start or ":")):
                start2 = (start + ":" if start else "") + s.basename
                self.cached_titles.append(start2)
                build(start2)

        build()

        # Gtk
        self.gui = Dialog(self.window, _('Search'), buttons=None, defaultwindowsize=(300, -1))
        self.gui.resize(300, 100)  # reset size
        self.input_entry = InputEntry()
        self.input_entry.connect('key_press_event', self.move)
        self.input_entry.connect('changed', self.change)  # self.change is needed by GObject or something
        self.gui.vbox.pack_start(self.input_entry, expand=False, fill=True, padding=0)
        # noinspection PyArgumentList
        self.label_object = Gtk.Label(label='')
        self.label_object.set_size_request(300, -1)
        self.gui.vbox.pack_start(self.label_object, expand=False, fill=True, padding=0)

        # preview pane
        self.label_preview = Gtk.Label(label='...loading...')
        # not sure if this has effect, longer lines without spaces still make window inflate
        self.label_preview.set_line_wrap(True)
        self.label_preview.set_xalign(0)  # align to the left
        self.label_preview.set_valign(Gtk.Align.START)  # align to the top
        self.preview_pane = Gtk.VBox()

        inner_container = Gtk.ScrolledWindow()
        inner_container.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        inner_container.add(self.label_preview)
        h = self.window.pageview.textview.get_allocated_height() - 25
        inner_container.set_min_content_height(h)
        inner_container.set_max_content_height(h)

        self.preview_pane.pack_start(inner_container, False, False, 5)
        self.window.pageview.pack_start(self.preview_pane, False, False, 5)

        # gui geometry
        self.geometry(init=True)

        self.gui.show_all()

        if self.state:
            self.prevent_closing = True
            self.input_entry.set_text(self.state.raw_query)
            self.input_entry.select_region(0, -1)
            self.change(None)
            self.prevent_closing = False

    def geometry(self, init=False, repeat=True, force=False):
        if repeat and not init:
            # I do not know how to catch callback when result list's width is final, so we align several times
            [GObject.timeout_add(x, lambda: self.geometry(repeat=False, force=force)) for x in (30, 50, 70, 400)]
            # it is not worthy we continue now because often the Gtk redraw is delayed which would mean
            # the Dialog dimensions change twice in a row
            return

        px, py = self.window.get_position()
        pw, ph = self.window.get_size()
        init_w, init_h = 300, 100
        if init:
            x, y = None, None
            w, h = init_w, init_h
        else:
            x, y = self.gui.get_position()
            w, h = self.gui.get_allocated_width(), self.gui.get_allocated_height()
        if self.plugin.preferences['position'] == InstantSearchPlugin.POSITION_RIGHT:
            x2, y2 = px + pw - w, py
        elif self.plugin.preferences['position'] == InstantSearchPlugin.POSITION_CENTER:
            x2, y2 = px + (pw / 2) - w / 2, py + (ph / 2) - 250
        else:
            raise AttributeError("Instant search: Wrong position preference.")

        if init or x != x2 or force:
            self.gui.resize(init_w, init_h)
            self.gui.move(x2, y2)

    def title(self, title=""):
        self.gui.set_title("Search " + title)

    def change(self, _):  # widget, event,text
        if self.timeout:
            GObject.source_remove(self.timeout)
            self.timeout = None
        q = self.input_entry.get_text()
        if q == self.last_query:
            return
        if q == State.title_match_char:
            return
        if q and q[-1] == "∀":  # easter egg: debug option for zim --standalone
            q = q[:-1]
            import ipdb
            ipdb.set_trace()
        self.state = State.set_current(q)

        if not self.state.is_finished:
            if self.start_search():
                self.process_menu()
        else:  # search completed before
            # If we would not clear the cache in .close(), we had to reset scores
            # and re-start search by self.start_search() for the case a page changed meanwhile.
            self.check_last()
            self.sout_menu()

        self.last_query = q

    def start_search(self):
        """ Search string has certainly changed. We search in indexed titles and/or we start fulltext search.
        :rtype: True if no other search is needed and we may output the menu immediately.
            
        """

        query = self.state.query
        menu = self.state.menu

        if not query:
            return True

        SearchController.header_search(query, menu, self.cached_titles)

        if self.state.page_name_only:
            return True
        else:
            if not self.state.previous or len(query) == State.start_search_length:
                # quickly show page title search results before longer fulltext search is ready
                # Either there is no previous state – query might have been copied into input
                # or the query is finally long enough to start fulltext search.
                # It is handy to show out filtered page names before because
                # it is often use case to jump to queries matched in page names.
                self.process_menu(ignore_geometry=True)

            self.title("..")
            self.timeout = GObject.timeout_add(self.keystroke_delay,
                                               self.start_zim_search)  # ideal delay between keystrokes

    def start_zim_search(self):
        """ Starts search for the input. """
        self.title("...")
        if self.timeout:
            GObject.source_remove(self.timeout)
            self.timeout = None
        self.query_o = Query(self.state.query)

        # it should be quicker to find the string, if we provide this subset from last time
        # (in the case we just added a letter, so that the subset gets smaller)
        # last_sel = self.selection if self.is_subset and self.state.previous and self.state.previous.is_finished
        #   else None
        selection = self.selection = SearchSelection(self.window.notebook)
        state = self.state  # this is a thread, so that self.state might change before search finishes

        # internal search disabled - it was way too slower
        # selection.search(self.query_o, selection=last_sel, callback=self._search_callback(state))
        # self._update_results(selection, state, force=True)
        # self.title("....")

        # fulltext external search
        # Loop either all .txt files in the notebook or narrow the search with a previous state
        if state.previous and state.previous.is_finished and state.previous.matching_files is not None:
            paths = state.previous.matching_files
            # see below paths_cached_set = (p for p in files_set if p in InstantSearchPlugin.file_cache)
        else:
            extension = "*" + self.window.notebook.config["Notebook"]["default_file_extension"]  # ex: "*.txt"
            # Why the slash "/" after the notebook folder? #51
            # If the notebook sits on the root dir in Windows, joining the notebook path "G:"
            # and the rglob produces path like "G:file.txt" which is a perfectly valid Windows path.
            # Missing slash means relative CWD on the drive G in Windows system
            # but Zim seems not to be aware of such a strange Windows behaviour. Hence, putting it into
            # self.window.notebook.layout.map_file / base.FilePath.relpath gives ValueError 'Not a parent path G:'.
            # Resolving files to the absolute paths by `f.resolve()` might fail as well because the drive G:
            # may point to another folder like C:\mount, and C:\mount\file.txt is not under the notebook parent
            # path "G:" as well.
            # The best solution is to force the notebook folder to have the slash to be sure we get such
            # half-absolute paths.
            # It's IMHO the bug of the Zim that it does not include trailing slash which is ok till the dir
            # is the root drive, while the path reported becomes relative ("G:" – relative to CWD on G, "G:\\" – absolute).
            paths = (f for f in Path(abspath(str(self.window.notebook.folder))).rglob(extension) if f.is_file())            
            # see below paths_cached_set = (p for p in InstantSearchPlugin.file_cache)
        state.matching_files = []

        # This cached search takes about 60 ms, so I let it commented.
        # However on HDD disks this may boost performance.
        # We may do an option: "empty cache immediately after close (default)",
        #                      "search cache first and then do the fresh search (HDD)"
        #                      "use cache always (empties cache after Zim restart)"
        #                      "empty cache after 5 minutes"
        #                      and then prevent to clear the cache in .close().
        # Or rather we may read file mtime and re-read if only it has been changed since last search.
        # if not InstantSearchPlugin.file_cache_fresh:
        #     # Cache might not be fresh but since it is quick, perform quick non-fresh-cached search
        #     # and then do a fresh search. If we are lucky enough, results will not change.
        #     # using temporary selection so that files will not received double points for both cached and fresh loop
        #     selection_temp = SearchSelection(self.window.notebook)
        #     self.start_external_search(selection_temp, state, paths_cached_set)
        #     InstantSearchPlugin.file_cache_fresh = True
        #     InstantSearchPlugin.file_cache.clear()
        self.start_external_search(selection, state, paths)

        state.is_finished = True

        if state == self.state:
            self.check_last()

        self.process_menu(state=state)
        self.title()

    def start_external_search(self, selection, state: "State", paths: List[Path]):
        """ Zim internal search is not able to find out text with markup.
                 Ex:
                  'economical' is not recognized as 'economi**cal**' (however highlighting works great),
                                                 as 'economi[[inserted link]]cal'
                                                 as 'any text with [[http://economical.example.com|link]]'

                 This fulltext search loops all .txt files in the notebook directory
                 and tries to recognize the patterns.
                 """

        # divide query to independent words "foo economical" -> "foo", "economical", page has to contain both
        # strip markup: **bold**, //italic//,  __underline__, ''verbatim'', ~~strike through~~
        # matches query "economi**cal**"

        def letter_split(q):
            """ Every letter is divided by a any-formatting-match-group and escaped.
                'foo.' -> 'f[*/'_~]o[*/'_~]o[*/'_~]\\.'
            """
            return r"[*/'_~]*".join((re.escape(c) for c in list(q)))

        sub_queries = state.query.split(" ")

        # regex to identify in all sub_queries present in the text
        queries = [(q, re.compile(letter_split(q), re.IGNORECASE)) for q in sub_queries]

        # regex to identify the very query is present
        exact_query = re.compile(letter_split(state.query), re.IGNORECASE) if len(sub_queries) > 1 else None

        # regex to count the number of the sub_queries present and to optionally add information about header used
        header_queries = [re.compile("(\n=+ .*)?" + letter_split(q), re.IGNORECASE) for q in sub_queries]

        # regex to identify inner link contents
        link = re.compile(r"\[\[(.*?)\]\]", re.IGNORECASE)  # matches all links "economi[[inserted link]]cal"

        start = perf_counter()

        for path in paths:
            if path not in file_cache:
                try:
                    contents = path.read_text(encoding='UTF-8', errors='replace') 
                except UnicodeDecodeError as err:
                    # Ignore file an skip to next path
                    logger.warning("Skipping path %s due to invalid character encoding error: %s", path, err)
                    continue 
                # strip header
                if contents.startswith('Content-Type: text/x-zim-wiki'):
                    # XX will that work on Win?
                    # I should use more general separator IMHO in the whole file rather than '\n'.
                    contents = contents[contents.find("\n\n"):]
                zim_path = self._path2zim(path)
                file_cache[path] = _FileCache(zim_path, contents)
            else:
                zim_path, contents = file_cache[path].path, file_cache[path].contents

            matched_links = []

            def matched_link(match):
                matched_links.append(match.group(1))
                return ""

            # pull out links "economi[[inserted link]]cal" -> "economical" + "inserted link"
            txt_body = link.sub(matched_link, contents)
            txt_links = "".join(matched_links)

            # wanted terms do not occur in the page name, waiting to be found in the page contents
            wanted = [(None, reg) for q, reg in queries if q not in str(zim_path).casefold()]

            def found(it):  # whether sub queries are found in the text
                return (reg.search(txt_body) or reg.search(txt_links) for _, reg in it)

            # Process, if not all query-terms (pieces, words, bits) are found in the page name
            # and thus the page would be ignored, but all of the remaining terms are found withing the page contents.
            # Or if all the terms are included in the page name (anywhere in the page name,
            # it does not have to be in its final part, in the least subpage), process if any of the terms are found
            # within the page contents as a bonus.
            if wanted and all(found(wanted)) or not wanted and any(found(queries)):
                # if remaining and all(reg.search(txt_body) or reg.search(txt_links) for reg in remaining):
                # score = header order * 3 + body match count * 1 
                # if there are '=' equal chars before the query, it is header. The bigger number, the bigger header.
                # Header 5 corresponds to 3 points, Header 1 to 7 points. XX it seems Header 5 ~ 3 points, Header 1 ~ 15 points. Might be more IMHO, like * 5 instead of * 3.
                score = sum([len(m.group(1)) * 3 if m.group(1) else 1
                             for q in header_queries for m in q.finditer(txt_body)])
                if exact_query:  # there are sub-queries, we favourize full-match
                    score += 100 * len(exact_query.findall(txt_body))

                # noinspection PyProtectedMember
                # score might be zero because we are not re-checking against txt_links matches
                selection._count_score(zim_path, score or 1)
                state.matching_files.append(path)
            elif not wanted:
                # The page is not eligible for fulltext search now. However, a term (part of the query) may appear
                # that will render the page thrown up from the page name search alone
                # but is included in the page contents.
                # Use case:
                # Step 1: Query "linux foo" matches page "Linux:foo" while neither term is in the page contents ('bar').
                # Step 2: Query "linux foo b" matches page "Linux:foo" because 'bar' is in the page contents.
                state.matching_files.append(path)

        logger.info("[Instantsearch] External search: %g s", perf_counter() - start)
        self._update_results(selection, state, force=True)

    def check_last(self):
        """ opens the page if there is only one option in the menu """
        if len(self.state.menu) == 1 and self.plugin.preferences['open_when_unique']:
            self._open_page(ZimPath(list(self.state.menu)[0]), exclude_from_history=False)
            if not self.prevent_closing:
                self.close()
        elif not len(self.state.menu):
            self._open_original()

    def _search_callback(self, state):
        def _(results, _path):
            if results is not None:
                # we finish the search even if another search is running.
                # If returned False, the search would be cancelled
                self._update_results(results, state)
            while Gtk.events_pending():
                Gtk.main_iteration()
            return True

        return _

    def _update_results(self, results, state: "State", force=False):
        """
        This method may run many times, due to the _update_results, which are updated many times,
         the results are appearing one by one. However, if called earlier than 0.2 s, ignored.

        Measures:
            If every callback would be counted, it takes 3500 ms to build a result set.
            If callbacks earlier than 0.6 s -> 2300 ms, 0.3 -> 2600 ms, 0.1 -> 2800 ms.
        """
        if not force and time() < self._last_update + 0.2:  # if update callback called earlier than 200 ms, ignore
            return
        self._last_update = time()

        changed = False

        for option in results.scores:
            if option.name not in state.menu or (
                    state.menu[option.name].page_score < 0 and state.menu[option.name].score == 0):
                changed = True
            o: _MenuItem = state.menu[option.name]
            o.score = results.scores[option]  # includes into options
            o.path = option.name

        if changed:  # we added a page
            self.process_menu(state=state, sort=False)
        else:
            pass

    def process_menu(self, state=None, sort=True, ignore_geometry=False):
        """ Sort menu and generate items and sout menu. """
        if state is None:
            state = self.state

        if sort:
            state.items = sorted(state.menu.values(), reverse=True, key=lambda item: (
                item.page_highlight, item.score + item.page_score, -item.path.count(":"), item.path))
        else:
            # when search results are being updated, it's good when the order does not change all the time.
            # So that the first result does not become for a while 10th and then become first back.
            state.items = sorted(state.menu.values(), reverse=True,
                                 key=lambda item: (item.page_highlight, -item.last_order))

        # Items appear only if they have score either from the page contents or the page name search.
        # And if the score comes from the page name search only, page_insufficient must be True
        # (at least one term appears in the least subpage name).
        # Note: I do not know why there are items with score 0 if internal Zim search used
        state.items = [page for page in state.items if
                       (page.score or not page.page_insufficient)
                       and (page.score + page.page_score) > 0]

        if state == self.state:
            self.sout_menu(ignore_geometry=ignore_geometry)

    def sout_menu(self, display_immediately=False, caret_move=None, ignore_geometry=False):
        """ Displays menu and handles caret position. """
        if self.timeout_open_page:
            GObject.source_remove(self.timeout_open_page)
            self.timeout_open_page = None
        if self.timeout_open_page_preview:
            GObject.source_remove(self.timeout_open_page_preview)
            self.timeout_open_page_preview = None

        # caret:
        #   by default stays at position 0
        #   If moved to a page, it keeps the page.
        #   If moved back to position 0, stays there.
        if caret_move is not None:
            if caret_move == 0:
                self.caret.pos = 0
            else:
                self.caret.pos += caret_move
            self.caret.stick = self.caret.pos != 0
        elif self.state.items and self.caret.stick:
            # identify current caret position, depending on the text
            self.caret.pos = next((i for i, item in enumerate(self.state.items) if item.path == self.caret.text), 0)
        # treat possible caret deflection
        if self.caret.pos < 0:
            # place the caret to the beginning or the end of list
            self.caret.pos = 0
        elif self.caret.pos >= len(self.state.items):
            self.caret.pos = 0 if caret_move == 1 else len(self.state.items) - 1

        text = []
        for i, page in enumerate(self.state.items):
            score = page.score + page.page_score
            page.last_order = i
            pieces = page.path.split(":")
            pieces[-1] = f"<b>{pieces[-1]}</b>"
            s = ":".join(pieces)
            if i == self.caret.pos:
                self.caret.text = page.path  # caret is at this position
                text.append(f'→ {s} ({score})')
            else:
                text.append(f'{s} ({score})')
        text = "No result" if not text and self.state.is_finished else "\n".join(text)

        self.label_object.set_markup(text)
        self.menu_page = ZimPath(self.caret.text if len(self.state.items) else self.original_page)

        if not display_immediately:
            if self.plugin.preferences['preview_mode'] != InstantSearchPlugin.PREVIEW_ONLY:
                self.timeout_open_page = GObject.timeout_add(self.keystroke_delay_open, self._open_page,
                                                             self.menu_page)  # ideal delay between keystrokes
            if self.plugin.preferences['preview_mode'] != InstantSearchPlugin.FULL_ONLY:
                self.timeout_open_page_preview = GObject.timeout_add(self.keystroke_delay, self._open_page_preview,
                                                                     self.menu_page)  # ideal delay between keystrokes
        else:
            self._open_page(self.menu_page)
        # we force here geometry to redraw because often we end up with "No result" page that is very tall
        # because of a many records just hidden

        if not ignore_geometry:
            self.geometry(force=True)

    def move(self, widget, event):
        """ Move caret up and down. Enter to confirm, Esc closes search."""
        key_name = Gdk.keyval_name(event.keyval)

        # handle basic caret movement
        moves = {"Up": -1, "ISO_Left_Tab": -1, "Down": 1, "Tab": 1, "Page_Up": -10, "Page_Down": 10}
        if key_name in moves:
            self.sout_menu(display_immediately=False, caret_move=moves[key_name])
        elif key_name in ("Home", "End"):
            if event.state & Gdk.ModifierType.CONTROL_MASK or event.state & Gdk.ModifierType.SHIFT_MASK:
                # Ctrl/Shift+Home jumps to the query input text start
                return
            if key_name == "Home":  # Home jumps at the result list start
                self.sout_menu(display_immediately=False, caret_move=0)
                widget.emit_stop_by_name("key-press-event")
            else:
                self.sout_menu(display_immediately=False, caret_move=float("inf"))
                widget.emit_stop_by_name("key-press-event")

        # confirm or cancel
        elif key_name == "KP_Enter" or key_name == "Return":
            self._open_page(self.menu_page, exclude_from_history=False)
            self.close()
        elif key_name == "Escape":
            self._open_original()
            self.is_closed = True  # few more timeouts are on the way probably
            self.close()

        return

    def close(self):
        """ Safely (closes gets called when hit Enter) """
        if not self.is_closed:  # if hit Esc, GTK has already emitted close itself
            self.is_closed = True
            self.gui.emit("close")

        # remove preview pane and show current text editor
        self._hide_preview()
        self.preview_pane.destroy()
        file_cache.clear()  # until next search, pages might change

    def _open_original(self):
        self._open_page(ZimPath(self.original_page))
        # we already have HistoryPath objects in the self.original_history, we cannot add them in the constructor
        # XX I do not know what is that good for
        hl = HistoryList([])
        hl.extend(self.original_history)
        self.window.history.uistate["list"] = hl

    # noinspection PyProtectedMember
    def _open_page(self, page, exclude_from_history=True):
        """ Open page and highlight matches """
        self._hide_preview()
        if self.timeout_open_page:  # no delayed page will be open
            GObject.source_remove(self.timeout_open_page)
            self.timeout_open_page = None
        if self.timeout_open_page_preview:  # no delayed preview page will be open
            GObject.source_remove(self.timeout_open_page_preview)
            self.timeout_open_page_preview = None

        # open page
        if page and page.name and page.name != self.last_page:
            self.last_page = page.name
            self.window.navigation.open_page(page)
            if exclude_from_history and list(self.window.history._history)[-1:][0].name != self.original_page:
                # there is no public API, so lets use protected _history instead
                self.window.history._history.pop()
                self.window.history._current = len(self.window.history._history) - 1
        if not exclude_from_history and self.window.history.get_current().name is not page.name:
            # we insert the page to the history because it was likely to be just visited and excluded
            self.window.history.append(page)

        # Popup find dialog with same query
        if self.query_o:  # and self.query_o.simple_match:
            string = self.state.query
            string = string.strip('*')  # support partial matches
            if self.plugin.preferences['highlight_search']:
                # unfortunately, we can highlight single word only
                self.window.pageview.show_find(string.split(" ")[0], highlight=True)

    def _hide_preview(self):
        self.preview_pane.hide()
        # noinspection PyProtectedMember
        self.window.pageview._hack_hbox.show()
    
    def _path2zim(self, path: Path) -> ZimPath:
        return self.window.notebook.layout.map_file(LocalFile(str(path)))[0]

    def _open_page_preview(self, page: ZimPath):
        """ Open preview which is far faster then loading and
         building big parse trees into text editor buffer when opening page. """
        # note: if the dialog is already closed, we do not want a preview to open, but page still can be open
        # (ex: after hitting Enter the dialog can close before opening the page)

        if self.timeout_open_page_preview:
            # no delayed preview page will be open, however self.timeout_open_page might be still running
            GObject.source_remove(self.timeout_open_page_preview)
            self.timeout_open_page_preview = None

        # it does not pose a problem if we re-load preview on the same page;
        # the query text might got another letter to highlight
        if page and not self.is_closed:
            # show preview pane and hide current text editor
            self.last_page_preview = page.name

            local_file: File = self.window.notebook.layout.map_page(page)[0]
            path = Path(str(local_file))
            if path in file_cache:
                s = file_cache[path].contents
            else:
                try:
                    s = local_file.read()
                    file_cache[path] = _FileCache(self._path2zim(path), s)
                except base.FileNotFoundError:
                    s = f"page {page} has no content"  # page has not been created yet
            lines = s.splitlines()

            # the file length is very small, prefer to not use preview here
            if self.plugin.preferences['preview_mode'] != InstantSearchPlugin.PREVIEW_ONLY and len(lines) < 50:
                return self._open_page(page, exclude_from_history=True)
            self.label_preview.set_markup(self._get_preview_text(lines, self.state.query))

            # shows GUI (hidden in self._hide_preview()
            self.preview_pane.show_all()
            # noinspection PyProtectedMember
            self.window.pageview._hack_hbox.hide()

    def _get_preview_text(self, lines, query):
        max_lines = 200

        # check if the file is a Zim markup file and if so, skip header
        if lines[0] == 'Content-Type: text/x-zim-wiki':
            for i, line in enumerate(lines):
                if line == "":
                    lines = lines[i + 1:]
                    break

        if query.strip() == "":
            return "\n".join(line for line in lines[:max_lines])

        # searching for "a" cannot match "&a", since markup_escape_text("&") -> "&apos;"
        # Ignoring q == "b", it would interfere with multiple queries:
        # Ex: query "f b", text "foo", matched with "f" -> "<b>f</b>oo", matched with "b" -> "<<b>b</b>>f</<b>b</b>>"
        query_match = (re.compile("(" + re.escape(q) + ")", re.IGNORECASE) for q in query.split(" ") if q != "b")
        # too long lines caused strange Gtk behaviour – monitor brightness set to maximum, without any logged warning
        # so that I decided to put just extract of such long lines in preview
        # This regex matches query chunk in the line, prepends characters before and after.
        # When there should be the same query chunk after the first, it stops.
        # Otherwise, the second chunk might be halved and thus not highlighted.
        # Ex: query "test", text: "lorem ipsum text dolor text text sit amet consectetur" ->
        #   ["ipsum text dolor ", "text ", "text sit amet"] (words "lorem" and "consectetur" are strip)
        line_extract = [re.compile("(.{0,80}" + re.escape(q) + "(?:(?!" + re.escape(q) + ").){0,80})", re.IGNORECASE)
                        for q in query.split(" ") if q != "b"]

        # grep some lines
        keep_all = not self.plugin.preferences["preview_short"] and len(lines) < max_lines
        lines_iter = iter(lines)
        chosen = [next(lines_iter)]  # always include header as the first line, even if it does not contain the query
        for line in lines_iter:
            if len(chosen) > max_lines:  # file is too long which would result the preview to not be smooth
                break
            elif keep_all or any(q in line.lower() for q in query.split(" ")):
                # keep this line since it contains a query chunk
                if len(line) > 100:
                    # however, this line is too long to display, try to extract query and its neighbourhood
                    s = "...".join("...".join(q.findall(line)) for q in line_extract).strip(".")
                    if not s:  # no query chunk was find on this line, the keep_all is True for sure
                        chosen.append(line[:100] + "...")
                    else:
                        chosen.append("..." + s + "...")
                else:
                    chosen.append(line)
        if not keep_all or len(chosen) > max_lines:
            # note that query might not been found, ex: query "foo" would not find line with a bold 'o': "f**o**o"
            chosen.append("...")
        txt = markup_escape_text("\n".join(line for line in chosen))

        # bold query chunks in the text
        for q in query_match:
            txt = q.sub(r"<b>\g<1></b>", txt)

        # preserve markup_escape_text entities
        # correct ex: '&a<b>m</b>p;' -> '&amp;' if searching for 'm'
        bold_tag = re.compile("</?b>")
        broken_entity = re.compile("&[a-z]*<b[^;]*;")
        txt = broken_entity.sub(lambda m: bold_tag.sub("", m.group(0)), txt)
        return txt


class State:
    matching_files: Optional[List[Path]]  # None if state search has not been started
    # the cache is held till the end of zim process. I dont know if it poses a problem
    # after hours of use and intensive searching.
    _states: Dict[str, "State"] = {}
    _current: "State" = None
    previous: Optional["State"]
    title_match_char: str
    start_search_length: int
    page_name_only: bool

    @classmethod
    def reset(cls):
        """ Reset the cache. (That is normally held till the end of Zim.) """
        State._states = {}

    @classmethod
    def set_current(cls, raw_query) -> "State":
        """ Returns other state.
            raw_query may include '!' sign for title only search
        """
        raw_query = raw_query.lower()
        if raw_query not in State._states:
            State._states[raw_query] = State(raw_query)
        else:
            State._states[raw_query].first_seen = False
        State._current = State._states[raw_query]
        return State._current

    @classmethod
    def get(cls, query):
        return State._states[query.lower()]

    def __init__(self, raw_query):
        self.items: List[_MenuItem] = []
        self.is_finished = False
        self.raw_query = r = raw_query  # including '!' sign for title only search
        self.first_seen = True

        # we are subset of this state from the longest shorter query
        self.previous = next((State._states[r[:i]] for i in range(len(r), 0, -1) if r[:i] in State._states), None)

        # since having <= 3 letters uses less benevolent searching method, we cannot reduce the next step
        # ex: "!est" should not match "testing" but "!esti" should
        if self.previous and self.previous.page_name_only:
            self.previous = None

        if self.previous:
            self.menu = deepcopy(self.previous.menu)
            [item.reset_score() for item in self.menu.values()]
        else:
            self.menu: Menu = defaultdict(_MenuItem)

        # check if we query page titles only, based on the special '!' sign in the query text
        # first char is "!" -> searches in page name only
        self.page_name_only, self.query = (True, raw_query[len(State.title_match_char):].lower()) \
            if raw_query.startswith(State.title_match_char) \
            else (False, raw_query)
        if len(self.query) < State.start_search_length:
            self.page_name_only = True  # search only in page names, not in page contents


class _MenuItem:

    def __init__(self):
        self.path: Optional[ZimPathStr] = None
        self.score = 0  # score given by SearchSelection (page contents search)
        self.page_score = 0  # score from the page name search
        self.page_highlight = False  # page name search priority match (query term is not in the middle of the word)
        self.last_order = 0

        # None of the query terms is in the least subpage name. Such results may appear only
        # if some of the term is found in the page context too. But the page search is insufficient.
        self.page_insufficient = False

    def reset_score(self):
        """ The item has been just copied from a previous state to narrow down the search.
            However, score will be re-counted. """
        self.page_score = self.score = 0
        self.page_highlight = False


ZimPathStr = str  # may serve as an argument to the ZimPath constructor
Menu = DefaultDict[ZimPathStr, _MenuItem]


class SearchController:
    @staticmethod
    def header_search(query: str, menu: Menu, cached_titles: List[ZimPathStr]) -> None:
        # 'te' matches these page titles: 'test' or 'Journal:test' or 'foo test' or 'foo (test)'
        sub_queries_benevolent = [re.compile(r"(^|:|\s|\()?" + q, re.IGNORECASE) for q in query.split(" ")]
        # 'st' does not match those
        sub_queries_strict = [re.compile(r"(^|:|\s|\()" + q, re.IGNORECASE) for q in query.split(" ")]

        def in_query(txt) -> Union[int, bool]:
            """ False if any part of the query does not match.
                If the query is longer >3 characters:
                    * +10 for every query part that matches a title part beginning
                        Ex: query 'te' -> +10 for these page titles:
                            'test' or 'Journal:test' or 'foo test' or 'foo (test)'
                    * +1 for every query part
                        Ex: query 'st' -> +1 for those page titles

                If the query is shorter <=3 characters:
                    +10 for every query part that matches a title part beginning 'te' for 'test'
                    False otherwise ('st' for 'test') so that you do not end up messed
                     with page titles, after writing a single letter.
            """
            try:
                if len(query) <= 3:
                    # raises if subquery m does not match or is not at a page chunk beginning
                    return sum(10 if m.group(1) is not None else None
                               for m in (q.search(txt) for q in sub_queries_strict))
                else:
                    # raises if subquery m does not match
                    return sum(10 if m.group(1) is not None else 1
                               for m in (q.search(txt) for q in sub_queries_benevolent))
            except (AttributeError, TypeError):  # one of the sub_queries is not part of the page title
                return False

        # we loop either all cached page titles or menu that should be built from previous superset-query menu
        for path in list(menu) or cached_titles:  # quick search in titles
            path_lower = path.casefold()
            path_end = path_lower[path_lower.rfind(":") + 1:]
            score = in_query(path_lower)

            if score:  # 'te' matches 'test' or 'Journal:test' etc
                # "foo" in "foo:bar", but not in "bar"
                # when looping "foo:bar", page "foo" receives +1 for having a subpage
                # if all(q in path.lower() for q in query) \
                #         and any(q not in path.lower().split(":")[-1] for q in query):
                # menu[":".join(path.split(":")[:-1])].bonus += 1  # 1 point for having a subpage

                # Normally, zim search gives 11 points bonus if the search-string appears in the titles.
                # If we are ignoring sub-pages, the search "foo" will match only page "journal:foo",
                # but not "journal:foo:subpage"
                # (and score of the parent page will get slightly higher by 1.)
                # However, if there are occurrences of the string in the fulltext of the subpage,
                # subpage remains in the result, but gets bonus only 2 points (not 11).
                # But internal zim search is now disabled.
                # menu[path].bonus = -11

                # 10 points for title (zim default) (so that it gets displayed before the search finishes)
                m = menu[path]
                m.page_score += score  # will be added to score (score will be reset)
                # if score > 9, it means this might be priority match, not fulltext page name search
                # ex "te" for "test" is priority, whereas "st" is just fulltext
                m.page_highlight = True if score > 9 else False
                m.path = path

                if not any(q in path_end for q in query.split()):
                    m.page_insufficient = True
                else:
                    m.page_insufficient = False
            else:  # remove the item from menu if it was there before
                menu.pop(path, None)
