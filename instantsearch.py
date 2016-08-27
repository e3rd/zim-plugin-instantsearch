#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Search instantly as you type. Edvard Rejthar
# https://github.com/e3rd/zim-plugin-instantsearch
#
from collections import defaultdict
import copy
import gobject
import gtk
import logging
from pprint import pprint
from zim.actions import action
from zim.gui.widgets import Dialog
from zim.gui.widgets import InputEntry
from zim.history import HistoryList
from zim.history import HistoryPath
from zim.notebook import Path
from zim.plugins import PluginClass
from zim.plugins import WindowExtension
from zim.plugins import extends
from zim.search import *
from zim.index import IndexPath
from copy import deepcopy
import sys
import inspect

logger = logging.getLogger('zim.plugins.instantsearch')

class InstantsearchPlugin(PluginClass):

    plugin_info = {
        'name': _('Instant Search'), # T: plugin name
        'description': _('''\
Instant search allows you to filter as you type feature known from I.E. OneNote.
When you hit Ctrl+E, small window opens, in where you can type.
As you type third letter, every page that matches your search is listed.
You can walk through by UP/DOWN arrow, hit Enter to stay on the page, or Esc to cancel. Much quicker than current Zim search.

(V1.0)
'''),
        'author': "Edvard Rejthar"
        #'help': 'Plugins:Instant search',
    }

    plugin_preferences = (
                          # T: label for plugin preferences dialog
                          ('title_match_char', 'string', _('Match title only if query starting by this char'), "!"),
                          ('start_search_length', 'int', _('Start the search when number of letters written'), 3, (0, 10)),
                          ('keystroke_delay', 'int', _('Keystroke delay'), 150, (0, 5000)),
                          ('highlight_search', 'bool', _('Highlight search'), True),
                          ('ignore_subpages', 'bool', _("Ignore subpages (if ignored, search 'linux' would return page:linux but not page:linux:subpage (if in the subpage, there is no occurece of string 'linux')"), True),
                          ('isWildcarded', 'bool', _("Append wildcards to the search string: *string*"), True),
                          ('isCached', 'bool', _("Cache results of a search to be used in another search. (Till the end of zim process.)"), True)
                          # T: plugin preference
                          )


@extends('MainWindow')
class InstantsearchMainWindowExtension(WindowExtension):

    uimanager_xml = '''
    <ui>
    <menubar name='menubar'>
            <menu action='tools_menu'>
                    <placeholder name='plugin_items'>
                            <menuitem action='instantsearch'/>
                    </placeholder>
            </menu>
    </menubar>
    </ui>
    '''


    gui = "";

    @action(_('_Instantsearch'), accelerator='<ctrl>e') # T: menu item
    def instantsearch(self):

        #init
        self.cached_titles = []
        #self.menu = defaultdict(_MenuItem)
        self.lastQuery = "" # previous user input
        self.queryO = None
        self.caret = {'pos':0, 'altPos':0, 'text':""}  # cursor position
        self.originalPage = self.window.ui.page.name # we return here after escape
        self.selection = None        
        if not self.plugin.preferences['isCached']:
            # reset last search results
            State.reset()
        self.menuPage = None
        self.isClosed = False
        self.lastPage = None

        # preferences
        self.title_match_char = self.plugin.preferences['title_match_char']
        self.start_search_length = self.plugin.preferences['start_search_length']
        self.keystroke_delay = self.plugin.preferences['keystroke_delay']

        # building quick title cache
        for s in self.window.ui.notebook.index.list_pages(Path(':')):
            st = s.basename
            self.cached_titles.append((st, st.lower()))
            for s2 in self.window.ui.notebook.get_pagelist(Path(st)):
                st = s.basename + ":" + s2.basename
                self.cached_titles.append((st, st.lower()))
                for s3 in self.window.ui.notebook.get_pagelist(Path(st)):
                    st = s.basename + ":" + s2.basename + ":" + s3.basename
                    self.cached_titles.append((st, st.lower()))
                    for s4 in self.window.ui.notebook.get_pagelist(Path(st)):
                        st = s.basename + ":" + s2.basename + ":" + s3.basename + ":" + s4.basename
                        self.cached_titles.append((st, st.lower()))
                        for s5 in self.window.ui.notebook.get_pagelist(Path(st)):
                            st = s.basename + ":" + s2.basename + ":" + s3.basename + ":" + s4.basename + ":" + s5.basename
                            self.cached_titles.append((st, st.lower()))

        # Gtk
        self.gui = Dialog(self.window.ui, _('Search'), buttons=None, defaultwindowsize=(300, -1))
        self.gui.resize(300, 100) # reset size
        self.inputEntry = InputEntry()
        self.inputEntry.connect('key_press_event', self.move)
        self.inputEntry.connect('changed', self.change) # self.change is needed by GObject or something
        self.gui.vbox.pack_start(self.inputEntry, False)        
        self.labelObject = gtk.Label(_(''))
        self.labelObject.set_usize(300, -1)        
        self.gui.vbox.pack_start(self.labelObject, False)

        #gui geometry
        x, y = self.window.uistate.get("windowpos")
        w, h = self.window.uistate.get("windowsize")
        self.gui.move((w-300), 0)
        self.gui.show_all()
        self.labelVar = ""
        self.timeout = ""
        self.timeoutOpenPage = None

        
    #lastPage = ""
    #pageTitleOnly = False
    menu = []
    #queryTime = 0    

    def change(self, _): #widget, event,text
        if self.timeout:
            gobject.source_remove(self.timeout)        
        q = self.inputEntry.get_text()
        #print("Change. {} {}".format(input, self.lastQuery))
        if q == self.lastQuery: return
        if q == self.title_match_char: return
        if q and q[-1] == "∀": # easter egg: debug option for zim --standalone
            q = q[:-1]
            import ipdb; ipdb.set_trace()
        self.state = State.setCurrent(q)

        if not self.state.isFinished:
            self.isSubset = True if self.lastQuery and q.startswith(self.lastQuery) else False
            self.state.checkTitleSearch(self.title_match_char)
            self.startSearch()
        else: # search completed before
            #print("Search already cached.")
            self.startSearch() # update the results in a page has been modified meanwhile (not if something got deleted in the notebook #16 )
            self.checkLast()             
            self.soutMenu()

        self.lastQuery = q

    def startSearch(self):
        """ Search string has certainly changed. We search in indexed titles and/or we start zim search.

        Normally, zim gives 11 points bonus if the search-string appears in the titles.
        If we are ignoring subpages, the search "foo" will match only page "journal:foo",
        but not "journal:foo:subpage" (and score of the parent page will get slightly higher by 1.)
        However, if there are occurences of the string in the fulltext of the subpage,
        subpage remains in the result, but gets bonus only 2 points (not 11).
        
        """
        
        query = self.state.query
        menu = self.state.menu
        isInQuery = re.compile(r"(^|:|\s|\()" + query).search  # 'te' matches this page titles: 'test' or 'Journal:test' or 'foo test' or 'foo (test)'
        if self.isSubset and len(query) < self.start_search_length:
            # letter(s) was/were added and full search has not yet been activated                        
            for path in _MenuItem.titles:
                if path in self.state.menu and not isInQuery(path.lower()):  # 'te' didnt match 'test' etc
                    del menu[path]  # we pop out the result
                else:
                    menu[path].sure = True
        else: # perform new search in cached_titles
            _MenuItem.titles = set()
            found = 0
            if self.state.firstSeen:
                for path, pathLow in self.cached_titles: # quick search in titles
                    if isInQuery(pathLow): # 'te' matches 'test' or 'Journal:test' etc
                        _MenuItem.titles.add(path)
                        if query in path.lower() and query not in path.lower().split(":")[-1]: # "raz" in "raz:dva", but not in "dva"
                            self.state.menu[":".join(path.split(":")[:-1])].bonus += 1 # 1 point for subpage
                            menu[path].bonus = -11
                        menu[path].score += 10 # 10 points for title (zim default) (so that it gets displayed before search finishes)
                        menu[path].path = path
                        found += 1
                        if found >= 10: # we dont want more than 10 results; we would easily match all of the pages
                            break

        self.processMenu() # show for now results of title search

        if len(query) >= self.start_search_length:
            self.timeout = gobject.timeout_add(self.keystroke_delay, self.startZimSearch) # ideal delay between keystrokes
        
    def startZimSearch(self):
        """ Starts search for the input. """        
        self.timeout = ""                
        self.caret['altPos'] = 0 # possible position of caret - beginning
        s = '"*{}*"'.format(self.state.query) if self.plugin.preferences['isWildcarded'] else self.state.query
        self.queryO = Query(unicode(s)) # beware when searching for unicode character. Update the row when going to Python3.
        
        lastSel = self.selection if self.isSubset and self.state.previous.isFinished else None # it should be quicker to find the string, if we provide this subset from last time (in the case we just added a letter, so that the subset gets smaller)
        self.selection = SearchSelection(self.window.ui.notebook)
        state = self.state # this is thread, so that self.state would can before search finishes
        self.selection.search(self.queryO, selection=lastSel, callback=self._search_callback(self.state.rawQuery))
        state.isFinished = True

        for item in list(state.menu): # remove all the items that we didnt encounter during the search
            if not state.menu[item].sure:
                del state.menu[item]
        
        if state == self.state:
            self.checkLast()
        
        self.processMenu(state = state)

    def checkLast(self):
        """ opens the page if there is only one option in the menu """
        if len(self.state.menu) == 1:            
            self._open_page(Path(self.state.menu.keys()[0]), excludeFromHistory = False)
            self.close()

    def _search_callback(self,query):
        def _search_callback(results, path):
            if results is not None:                
                self._update_results(results, State.get(query)) # we finish the search even if another search is running. If we returned False, the search would be cancelled-
            while gtk.events_pending():
                gtk.main_iteration(block=False)
            return True
        return _search_callback

    def _update_results(self, results, state):
        """
        This method may run many times, due to the _update_results, which are updated many times.
        I may set that _update_results would run only once, but this is nice - the results are appearing one by one.
        """
        changed = False

        state.lastResults = results
        for option in results.scores:
            if state.pageTitleOnly and state.query not in option.name: # hledame jen v nazvu stranky
                continue            
            
            if option.name not in state.menu: # new item found                
                if state == self.state and option.name == self.caret['text']: # this is current search
                    self.caret['altPos'] = len(state.menu)-1 #karet byl na tehle pozici, pokud se zuzil vyber, budeme vedet, kam karet opravne umistit
            if option.name not in state.menu or (state.menu[option.name].bonus < 0 and state.menu[option.name].score == 0):
                changed = True
            if not state.menu[option.name].sure:
                state.menu[option.name].sure = True
                changed = True
            state.menu[option.name].score = results.scores[option] #zaradit mezi moznosti        

        if changed: # we added a page
            self.processMenu(state = state, sort = False)
        else:
            pass

    def processMenu(self, state = None, sort = True):
        """ Sort menu and generate items and sout menu. """
        if state is None:
           state = self.state
           
        if sort:
            state.items = sorted(state.menu, reverse=True, key=lambda item: (state.menu[item].score+state.menu[item].bonus , -item.count(":"), item))
        else: # when search results are being updated, it's good when the order doesnt change all the time. So that the first result does not become for a while 10th and then become first back.
            state.items = sorted(state.menu, key=lambda item: (state.menu[item].lastOrder))
    
        if state == self.state:
            self.soutMenu()        

    def soutMenu(self, displayImmediately = False):
        """ Displays menu and handles caret position. """
        if self.timeoutOpenPage:            
            gobject.source_remove(self.timeoutOpenPage)
        self.gui.resize(300, 100) # reset size
        # treat possible caret deflection
        if self.caret['pos'] < 0 or self.caret['pos'] > len(self.state.items)-1: #umistit karet na zacatek ci konec seznamu
            self.caret['pos'] = self.caret['altPos']

        text = ""
        i = 0        
        for item in self.state.items:
            score = self.state.menu[item].score + self.state.menu[item].bonus
            if score < 1:                
                continue
            self.state.menu[item].lastOrder = i
            if i == self.caret['pos']:
                # caret is at this position
                self.caret['text'] = item
                text += '→ {} ({}) {}\n'.format(item,score, "" if self.state.menu[item].sure else "?")
            else:
                try:
                    text += '{} ({}) {}\n'.format(item,score, "" if self.state.menu[item].sure else "?")
                except:
                    text += "CHYBA\n"
                    text += item[0:-1] + "\n"
            i += 1

        self.labelObject.set_text(text)        
        self.menuPage = Path(self.caret['text'])

        if not displayImmediately:
            self.timeoutOpenPage = gobject.timeout_add(self.keystroke_delay, self._open_page, self.menuPage) # ideal delay between keystrokes
        else:
            self._open_page(self.menuPage)
    
    def move(self, widget, event):
        """ Move caret up and down. Enter to confirm, Esc closes search."""
        keyname = gtk.gdk.keyval_name(event.keyval)
        if keyname == "Up":
            self.caret['pos'] -= 1
            self.soutMenu(displayImmediately = True)

        if keyname == "Down":
            self.caret['pos'] += 1
            self.soutMenu(displayImmediately = True)
        
        if keyname == "KP_Enter" or keyname == "Return":                        
            self._open_page(self.menuPage, excludeFromHistory = False)
            self.close()

        if keyname == "Escape":
            self._open_page(Path(self.originalPage), excludeFromHistory = False)
            # GTK closes the windows itself on Escape, no self.close() needed

        return

    ## Safely closes
    # Xwhen closing directly, Python gave allocation error
    def close(self):
        if not self.isClosed:
            self.isClosed = True
            self.gui.emit("close")        

    def _open_page(self, page, excludeFromHistory = True):
        """ Open page and highlight matches """
        self.timeoutOpenPage = None # no delayed page will be open
        if self.isClosed == True:            
            return        
        if page and page.name and page.name != self.lastPage:
            self.lastPage = page.name
            #print("*** HISTORY BEF", self.window.ui.history._history[-3:])
            self.window.ui.open_page(page)
            if excludeFromHistory:
                # there is no public API, so lest use protected _history instead
                self.window.ui.history._history.pop()
                self.window.ui.history._current = len(self.window.ui.history._history) - 1            
        # Popup find dialog with same query
        if self.queryO:# and self.queryO.simple_match:
            string = self.state.query
            string = string.strip('*') # support partial matches                            
            if self.plugin.preferences['highlight_search']:
                self.window.ui._mainwindow.pageview.show_find(string, highlight=True)                    

class State:
    _states = {} # the cache is held till the end of zim process. I dont know if it poses a problem after hours of use and intensive searching.
    _current = None

    @classmethod
    def reset(cls):
        """ Reset the cache. (That is normally held till the end of Zim.) """
        State._states = {}

    @classmethod
    def setCurrent(cls,query):
        """ Returns other state.
            query = rawQuery (including '!' sign for title only search)
        """        
        query = query.lower()
        if query not in State._states:
            State._states[query] = State(query = query, previous = State._current)
            State._states[query].firstSeen = True
        else:
            State._states[query].firstSeen = False
        State._current = State._states[query]
        return State._current

    @classmethod
    def get(cls, query):        
        return State._states[query.lower()]

    def __init__(self, query = "", previous = None):
        self.items = ""
        self.isFinished = False
        self.query = query
        self.rawQuery = query # including '!' sign for title only search
        self.previous = previous
        self.pageTitleOnly = False
        if previous:
            self.menu = deepcopy(previous.menu) 
            for item in self.menu.values():
                item.sure = False
        else:
            self.menu = defaultdict(_MenuItem)

    def checkTitleSearch(self, title_match_char):
        """ Check if we query page titles only, based on the special '!' sign in the query text. """
        if self.query.startswith(title_match_char): # first char is "!" -> searches in page name only
            self.pageTitleOnly = True
            self.query = self.query[len(title_match_char):].lower()
        else:
            self.pageTitleOnly = False


class _MenuItem():
    titles = set() # items that are page-titles

    def __init__(self):
        self.path = None
        self.score = 0 # defined by SearchSelection
        self.bonus = 0 # defined locally
        self.sure = True # it is certain item is in the list (it may be just a rudiment from last search)
        self.lastOrder = 0
