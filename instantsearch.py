#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Search instantly as you type. Edvard Rejthar
# https://github.com/e3rd/zim-plugin-instantsearch
#
from collections import defaultdict
from copy import deepcopy
from time import time

from gi.repository import GObject, Gtk, Gdk
from gi.repository.GLib import markup_escape_text
from zim import newfs
from zim.actions import action
from zim.gui.mainwindow import MainWindowExtension
from zim.gui.widgets import Dialog
from zim.gui.widgets import InputEntry
from zim.history import HistoryList
from zim.plugins import PluginClass
from zim.search import *

logger = logging.getLogger('zim.plugins.instantsearch')


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

    plugin_preferences = (
        # T: label for plugin preferences dialog
        ('title_match_char', 'string', _('Match title only if query starting by this char'), "!"),
        ('start_search_length', 'int', _('Start the search when number of letters written'), 3, (0, 10)),
        ('keystroke_delay', 'int', _('Keystroke delay for displaying preview'), 150, (0, 5000)),
        ('keystroke_delay_open', 'int', _('Keystroke delay for opening page'
                                          '\n(Low value might prevent search list smooth navigation'
                                          ' if page is big.)'), 1500, (0, 5000)),
        ('highlight_search', 'bool', _('Highlight search'), True),
        ('ignore_subpages', 'bool', _("Ignore sub-pages (if ignored, search 'linux'"
                                      " would return page:linux but not page:linux:subpage"
                                      " (if in the subpage, there is no occurrence of string 'linux')"), True),
        ('is_wildcarded', 'bool', _("Append wildcards to the search string: *string*"), True),
        ('is_cached', 'bool',
         _("Cache results of a search to be used in another search. (Till the end of zim process.)"), True),
        ('open_when_unique', 'bool', _('When only one page is found, open it automatically.'), True),
        ('position', 'choice', _('Popup position'), POSITION_RIGHT, (POSITION_RIGHT, POSITION_CENTER))
        # T: plugin preference
    )


class InstantSearchMainWindowExtension(MainWindowExtension):
    gui = ""

    def __init__(self, plugin, window):
        super().__init__(plugin, window)
        self.label_var = None
        self.timeout = None
        self.timeout_open_page = None  # will open page after keystroke delay
        self.timeout_open_page_preview = None  # will open page after keystroke delay
        self.cached_titles = None
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
        self.state: "State" = None
        self.is_subset = None
        self.label_preview = None
        self.preview_pane = None
        self._last_update = 0
        # self.position = None

        # preferences
        self.title_match_char = self.plugin.preferences['title_match_char']
        self.start_search_length = self.plugin.preferences['start_search_length']
        self.keystroke_delay_open = self.plugin.preferences['keystroke_delay_open']
        self.keystroke_delay = self.plugin.preferences['keystroke_delay']
        self.open_when_unique = self.plugin.preferences['open_when_unique']

    @action(_('_Instant search'), accelerator='<ctrl>e')  # T: menu item
    def instant_search(self):

        # init
        self.cached_titles = []
        self.last_query = ""  # previous user input
        self.query_o = None
        self.caret = {'pos': 0, 'altPos': 0, 'text': ""}  # cursor position
        self.original_page = self.window.page.name  # we return here after escape
        self.original_history = list(self.window.history.uistate["list"])
        self.selection = None
        if not self.plugin.preferences['is_cached']:
            # reset last search results
            State.reset()
        self.menu_page = None
        self.is_closed = False
        self.last_page = None

        # building quick title cache
        def build(start=""):
            o = self.window.notebook.pages
            for s in o.list_pages(Path(start or ":")):
                start2 = (start + ":" if start else "") + s.basename
                self.cached_titles.append((start2, start2.lower()))
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

        # gui geometry
        px, py = self.window.get_position()
        pw, ph = self.window.get_size()
        # Xx, y = self.gui.get_position()

        self.label_preview = Gtk.Label(label='...loading...')
        # not sure if this has effect, longer lines without spaces still make window inflate
        self.label_preview.set_line_wrap(True)
        self.label_preview.set_xalign(0)  # align to the left
        self.preview_pane = Gtk.VBox()
        self.preview_pane.pack_start(self.label_preview, False, False, 5)
        self.window.pageview.pack_start(self.preview_pane, False, False, 5)

        if self.plugin.preferences['position'] == InstantSearchPlugin.POSITION_RIGHT:
            self.gui.move((pw - 300), 0)
        elif self.plugin.preferences['position'] == InstantSearchPlugin.POSITION_CENTER:
            self.gui.resize(300, 100)
            self.gui.move(px + (pw / 2) - 150, py + (ph / 2) - 250)
        else:
            raise AttributeError("Instant search: Wrong position preference.")

        # self.position = self.gui.get_position() # works bad -> when menu is displayed first, the position changes
        self.gui.show_all()

        self.label_var = ""  # XX remove?
        self.timeout = ""  # XX remove?

    # last_page = ""
    # page_title_only = False
    menu = []

    # queryTime = 0

    def title(self, title=""):
        self.gui.set_title("Search " + title)

    def change(self, _):  # widget, event,text
        if self.timeout:
            GObject.source_remove(self.timeout)
        q = self.input_entry.get_text()
        # print("Change. {} {}".format(input, self.last_query))
        if q == self.last_query:
            return
        if q == self.title_match_char:
            return
        if q and q[-1] == "∀":  # easter egg: debug option for zim --standalone
            q = q[:-1]
            import ipdb
            ipdb.set_trace()
        self.state = State.set_current(q)

        if not self.state.is_finished:
            self.is_subset = True if self.last_query and q.startswith(self.last_query) else False
            self.state.check_title_search(self.title_match_char)
            self.start_search()
        else:  # search completed before
            # print("Search already cached.")
            # update the results in a page has been modified meanwhile
            # (not if something got deleted in the notebook #16 )
            self.start_search()
            self.check_last()
            self.sout_menu()

        self.last_query = q

    def start_search(self):
        """ Search string has certainly changed. We search in indexed titles and/or we start zim search.

        Normally, zim gives 11 points bonus if the search-string appears in the titles.
        If we are ignoring sub-pages, the search "foo" will match only page "journal:foo",
        but not "journal:foo:subpage" (and score of the parent page will get slightly higher by 1.)
        However, if there are occurrences of the string in the fulltext of the subpage,
        subpage remains in the result, but gets bonus only 2 points (not 11).

        """

        query = self.state.query
        menu = self.state.menu
        # 'te' matches this page titles: 'test' or 'Journal:test' or 'foo test' or 'foo (test)'
        is_in_query = re.compile(r"(^|:|\s|\()" + query).search
        if self.is_subset and len(query) < self.start_search_length:
            # letter(s) was/were added and full search has not yet been activated
            for path in _MenuItem.titles:
                if path in self.state.menu and not is_in_query(path.lower()):  # 'te' didnt match 'test' etc
                    del menu[path]  # we pop out the result
                else:
                    menu[path].sure = True
        else:  # perform new search in cached_titles
            _MenuItem.titles = set()
            found = 0
            if self.state.first_seen:
                for path, pathLow in self.cached_titles:  # quick search in titles
                    if is_in_query(pathLow):  # 'te' matches 'test' or 'Journal:test' etc
                        _MenuItem.titles.add(path)
                        # "raz" in "raz:dva", but not in "dva"
                        if query in path.lower() and query not in path.lower().split(":")[-1]:
                            self.state.menu[":".join(path.split(":")[:-1])].bonus += 1  # 1 point for subpage
                            menu[path].bonus = -11
                        # 10 points for title (zim default) (so that it gets displayed before search finishes)
                        menu[path].score += 10
                        menu[path].in_title = True
                        menu[path].path = path
                        found += 1
                        if found >= 10:  # we dont want more than 10 results; we would easily match all of the pages
                            break
                    else:
                        menu[path].in_title = False

        self.process_menu()  # show for now results of title search

        if len(query) >= self.start_search_length:
            self.title("..")
            self.timeout = GObject.timeout_add(self.keystroke_delay,
                                               self.start_zim_search)  # ideal delay between keystrokes

    def start_zim_search(self):
        """ Starts search for the input. """
        self.title("...")
        self.timeout = ""
        self.caret['altPos'] = 0  # possible position of caret - beginning
        self.query_o = Query(f'"*{self.state.query}*"' if self.plugin.preferences['is_wildcarded']
                             else self.state.query)

        # it should be quicker to find the string, if we provide this subset from last time
        # (in the case we just added a letter, so that the subset gets smaller)
        last_sel = self.selection if self.is_subset and self.state.previous.is_finished else None
        self.selection = SearchSelection(self.window.notebook)
        state = self.state  # this is thread, so that self.state would can before search finishes
        self.selection.search(self.query_o, selection=last_sel, callback=self._search_callback(self.state.raw_query))
        self._update_results(self.selection, State.get(self.state.raw_query), force=True)
        self.title()

        state.is_finished = True

        for item in list(state.menu):  # remove all the items that we didnt encounter during the search
            if not state.menu[item].sure:
                del state.menu[item]

        if state == self.state:
            self.check_last()

        self.process_menu(state=state)

    def check_last(self):
        """ opens the page if there is only one option in the menu """
        if self.open_when_unique and len(self.state.menu) == 1:
            self._open_page(Path(self.state.menu.keys()[0]), exclude_from_history=False)
            self.close()

    def _search_callback(self, query):
        def _search_callback(results, _path):
            if results is not None:
                # we finish the search even if another search is running.
                # If returned False, the search would be cancelled
                self._update_results(results, State.get(query))
            while Gtk.events_pending():
                Gtk.main_iteration()
            return True

        return _search_callback

    def _update_results(self, results, state, force=False):
        """
        This method may run many times, due to the _update_results, which are updated many times.
        I may set that _update_results would run only once, but this is nice - the results are appearing one by one.
        """
        if not force and time() < self._last_update + 0.2:
            # if update callback called earlier than before 200 ms, ignore
            # If every callback is counted, it takes 3500 ms to build a result set.
            # If 0.6 -> 2300 ms, 0.3 -> 2600 ms, 0.1 -> 2800 ms
            return
        self._last_update = time()

        changed = False

        state.lastResults = results
        for option in results.scores:
            if state.page_title_only and state.query not in option.name:  # searching in the page name only
                continue

            if option.name not in state.menu:  # new item found
                if state == self.state and option.name == self.caret['text']:  # this is current search
                    # caret was on this positions; if selection narrows we know where to re-place the caret back                    
                    self.caret['altPos'] = len(state.menu) - 1
            if option.name not in state.menu or (
                    state.menu[option.name].bonus < 0 and state.menu[option.name].score == 0):
                changed = True
            if not state.menu[option.name].sure:
                state.menu[option.name].sure = True
                changed = True
            state.menu[option.name].score = results.scores[option]  # includes into options

        if changed:  # we added a page
            self.process_menu(state=state, sort=False)
        else:
            pass

    def process_menu(self, state=None, sort=True):
        """ Sort menu and generate items and sout menu. """
        if state is None:
            state = self.state

        if sort:
            state.items = sorted(state.menu, reverse=True, key=lambda item: (
                state.menu[item].in_title, state.menu[item].score + state.menu[item].bonus, -item.count(":"), item))
        else:
            # when search results are being updated, it's good when the order doesnt change all the time.
            # So that the first result does not become for a while 10th and then become first back.
            state.items = sorted(state.menu, reverse=True,
                                 key=lambda item: (state.menu[item].in_title, -state.menu[item].last_order))

        # I do not know why there are items with score 0
        state.items = [item for item in state.items if (state.menu[item].score + state.menu[item].bonus) > 0]

        if state == self.state:
            self.sout_menu()

    def sout_menu(self, display_immediately=False):
        """ Displays menu and handles caret position. """
        if self.timeout_open_page:
            GObject.source_remove(self.timeout_open_page)
            self.timeout_open_page = None
        if self.timeout_open_page_preview:
            GObject.source_remove(self.timeout_open_page_preview)
            self.timeout_open_page_preview = None
        # if self.plugin.preferences['position'] == InstantSearchPlugin.POSITION_RIGHT:
        #     if not self.position:
        #
        #     w = self.gui.get_allocated_width()
        #     if w > 500:
        #         w = 600
        #     elif w > 400:
        #         w = 500
        #     elif w > 300:
        #         w = 400
        #     x2 = self.position[0]+300-w  # get X-position where window should be
        #     x, y = self.gui.get_position()
        #     if x2 != x:  # this is not the current position
        #         print(f"  *** Moving to {x2}, current {x},
        #         default {self.position[0]}, width {w}, alloc {self.gui.get_allocated_width()}")
        #         self.gui.resize(w, -1)  # reset size
        #         self.gui.move(x2, y)
        
        # treat possible caret deflection
        if self.caret['pos'] < 0 or self.caret['pos'] > len(self.state.items) - 1:
            # place the caret to the beginning or the end of list
            self.caret['pos'] = self.caret['altPos']

        text = ""
        i = 0
        for item in self.state.items:
            score = self.state.menu[item].score + self.state.menu[item].bonus
            self.state.menu[item].last_order = i
            if i == self.caret['pos']:
                self.caret['text'] = item  # caret is at this position
                text += f'→ {item} ({score}) {"" if self.state.menu[item].sure else "?"}\n'
            else:
                text += f'{item} ({score}) {"" if self.state.menu[item].sure else "?"}\n'
            i += 1

        self.label_object.set_text(text)
        self.menu_page = Path(self.caret['text'] if len(self.state.items) else self.original_page)

        if not display_immediately:
            self.timeout_open_page = GObject.timeout_add(self.keystroke_delay_open, self._open_page,
                                                         self.menu_page)  # ideal delay between keystrokes
            self.timeout_open_page_preview = GObject.timeout_add(self.keystroke_delay, self._open_page_preview,
                                                                 self.menu_page)  # ideal delay between keystrokes
        else:
            self._open_page(self.menu_page)

    def move(self, _widget, event):
        """ Move caret up and down. Enter to confirm, Esc closes search."""
        key_name = Gdk.keyval_name(event.keyval)
        if key_name == "Up" or key_name == "ISO_Left_Tab":
            self.caret['pos'] -= 1
            self.sout_menu(display_immediately=False)

        if key_name == "Down" or key_name == "Tab":
            self.caret['pos'] += 1
            self.sout_menu(display_immediately=False)

        if key_name == "KP_Enter" or key_name == "Return":
            self._open_page(self.menu_page, exclude_from_history=False)
            self.close()

        if key_name == "Escape":
            self._open_original()
            self.is_closed = True  # few more timeouts are on the way probably
            # no self.close() call needed, GTK emits this itself on Escape

        return

    def close(self):
        """ Safely (closes gets called when hit Enter) """
        if not self.is_closed:
            self.is_closed = True
            self.gui.emit("close")

        # remove preview pane and show current text editor
        self._hide_preview()
        self.preview_pane.destroy()

    def _open_original(self):
        self._open_page(Path(self.original_page))
        # we already have HistoryPath objects in the self.original_history, we cannot add them in te constructor
        hl = HistoryList([])
        hl.extend(self.original_history)
        self.window.history.uistate["list"] = hl

    # noinspection PyProtectedMember
    def _open_page(self, page, exclude_from_history=True):
        """ Open page and highlight matches """
        print("OPEN PAGE CALL", page)
        self._hide_preview()
        if self.timeout_open_page:  # no delayed page will be open
            GObject.source_remove(self.timeout_open_page)
            self.timeout_open_page = None
        if self.timeout_open_page_preview:  # no delayed preview page will be open
            GObject.source_remove(self.timeout_open_page_preview)
            self.timeout_open_page_preview = None

        # if self.is_closed is True:
        #     return
        # print("*** History1: ", self.window.history._history, self.window.history._current)

        # open page
        if page and page.name and page.name != self.last_page:
            self.last_page = page.name
            print("OPENING", page)
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
                self.window.pageview.show_find(string, highlight=True)

    def _hide_preview(self):
        self.preview_pane.hide()
        # noinspection PyProtectedMember
        self.window.pageview._hack_hbox.show()

    def _open_page_preview(self, page):
        """ Open preview which is far faster then loading and
         building big parse trees into text editor buffer when opening page. """
        # note: if the dialog is already closed, we do not want a preview to open, but page still can be open
        # (ex: after hitting Enter the dialog can close before opening the page)

        if self.timeout_open_page_preview:
            # no delayed preview page will be open, however self.timeout_open_page might be still running
            GObject.source_remove(self.timeout_open_page_preview)
            self.timeout_open_page_preview = None

        if page and page.name and page.name != self.last_page_preview and not self.is_closed:
            # show preview pane and hide current text editor
            self.last_page_preview = page.name
            try:
                lines = markup_escape_text(self.window.notebook.layout.map_page(page)[0].read()).splitlines()
            except newfs.base.FileNotFoundError:
                lines = [f"page {page} has no content"]  # page has not been created yet

            if len(lines) < 50:  # the file length is very small, do not use preview here
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

        # grep some lines
        bold = re.compile("(" + query + ")", re.IGNORECASE)
        keep_all = len(lines) < max_lines
        g = iter(lines)
        chosen = [next(g)]  # always include header as the first line, even if it does not contain the query
        for line in g:
            if len(chosen) > max_lines:  # file is too long which would result the preview to not be smooth
                break
            elif keep_all or query in line.lower():
                chosen.append(bold.sub(r"<b>\g<1></b>", line))
        if not keep_all or len(chosen) > max_lines:
            # note that query might not been found, ex: query "foo" would not find line with a bold 'o': "f**o**o"
            chosen.append("...")
        return "\n".join(line for line in chosen)


class State:
    # the cache is held till the end of zim process. I dont know if it poses a problem
    # after hours of use and intensive searching.
    _states = {}
    _current = None

    @classmethod
    def reset(cls):
        """ Reset the cache. (That is normally held till the end of Zim.) """
        State._states = {}

    @classmethod
    def set_current(cls, query) -> "State":
        """ Returns other state.
            query = raw_query (including '!' sign for title only search)
        """
        query = query.lower()
        if query not in State._states:
            State._states[query] = State(query=query, previous=State._current)
            State._states[query].first_seen = True
        else:
            State._states[query].first_seen = False
        State._current = State._states[query]
        return State._current

    @classmethod
    def get(cls, query):
        return State._states[query.lower()]

    def __init__(self, query="", previous=None):
        self.items = []
        self.is_finished = False
        self.query = query
        self.raw_query = query  # including '!' sign for title only search
        self.previous: "State" = previous
        self.page_title_only = False
        self.first_seen = None
        if previous:
            self.menu = deepcopy(previous.menu)
            for item in self.menu.values():
                item.sure = False
        else:
            self.menu = defaultdict(_MenuItem)

    def check_title_search(self, title_match_char):
        """ Check if we query page titles only, based on the special '!' sign in the query text. """
        if self.query.startswith(title_match_char):  # first char is "!" -> searches in page name only
            self.page_title_only = True
            self.query = self.query[len(title_match_char):].lower()
        else:
            self.page_title_only = False


class _MenuItem:
    titles = set()  # items that are page-titles

    def __init__(self):
        self.path = None
        self.score = 0  # defined by SearchSelection
        self.bonus = 0  # defined locally
        self.in_title = False  # query is in title
        self.sure = True  # it is certain item is in the list (it may be just a rudiment from last search)
        self.last_order = 0
