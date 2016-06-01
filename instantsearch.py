#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Search instantly as you Type. Edvard Rejthar
# https://github.com/e3rd/zim-plugin-instantsearch
#
#import Tkinter
#from Tkinter import Entry
#from Tkinter import Label
#from Tkinter import StringVar
#from Tkinter import Tk
import gobject
from collections import defaultdict
import gtk
import logging
from zim.actions import action
#from zim.gui.widgets import BrowserTreeView
from zim.gui.widgets import Dialog
#from zim.gui.widgets import ErrorDialog
from zim.gui.widgets import InputEntry
#from zim.gui.widgets import ScrolledWindow
from zim.notebook import Path
from zim.plugins import PluginClass
from zim.plugins import WindowExtension
from zim.plugins import extends
from zim.search import *
#from zim.parsing import Re
from pprint import pprint
from zim.history import HistoryList, HistoryPath
import copy

logger = logging.getLogger('zim.plugins.instantsearch')

class InstantsearchPlugin(PluginClass):

    plugin_info = {
        'name': _('Instant Search'), # T: plugin name
        'description': _('''\
Instant search allows you to filter as you type feature known from I.E. OneNote.
When you hit Ctrl+E, small window opens, in where you can type.
As you type third letter, every page that matches your search is listed.
You can walk through by UP/DOWN arrow, hit Enter to stay on the page, or Esc to cancel. Much quicker than current Zim search.

(V0.4)
'''),
        'author': "Edvard Rejthar"
        #'help': 'Plugins:Due date',
    }

    plugin_preferences = (
                          # T: label for plugin preferences dialog
                          ('title_match_char', 'string', _('Match title only if query starting by this char'), "!"),
                          ('start_search_length', 'int', _('Start the search when number of letters written'), 3,(0,10)),
                          ('keystroke_delay', 'int', _('Keystroke delay'), 150,(0,5000)),
                          ('highlight_search','bool', _('Highlight search'), True),
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

        self.cached_titles = []
        
        #with open("/tmp/test.txt","w") as f:
        #    f.write(str(self.__dict__))
        #print("****************************************")
        #print(str(self.window.ui.notebook.__dict__))
        
        
        #pprint(self.window.ui.history.uistate['list'])
        #self.history = {}
        #self.history["list"] = list(self.window.ui.history.uistate['list'])
        #self.history["recent"] = list(self.window.ui.history.uistate['recent'])
        #self.history["current"] = self.window.ui.history.uistate['current']
        #pprint("ulozeno")
        #pprint(self.history)

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

        # preferences
        self.title_match_char = self.plugin.preferences['title_match_char']
        self.start_search_length = self.plugin.preferences['start_search_length']
        self.keystroke_delay = self.plugin.preferences['keystroke_delay']

        #init
        self.input = "" # user input
        self.query = None
        self.caret = {'pos':0, 'altPos':0, 'text':""}  # cursor position
        #self.matches = [] # XX lze sem dat recentne pouzite
        self.originalPage = self.window.ui.page.name # we return here after escape
        self.selection = None
        self.scores = defaultdict(int)


        # Gtk
        self.gui = Dialog(self.window.ui, _('Search'), buttons = None, defaultwindowsize=(300, -1))
        self.gui.resize(300,100) # reset size
        #hbox = gtk.HBox(spacing=5)
        #self.gui.vbox.pack_start(hbox, expand=True,fill=True,padding=0)
        self.inputEntry = InputEntry()
        self.inputEntry.connect('key_press_event', self.move)
        self.inputEntry.connect('changed', self.change)        
        self.gui.vbox.pack_start(self.inputEntry, False)
        #hbox.add(self.inputEntry)
        
        self.labelObject = gtk.Label(_(''))
        self.labelObject.set_usize(300,-1)
        #hbox.pack_start(self.labelObject, False) # T: input label
        self.gui.vbox.pack_start(self.labelObject, False)
        #a.set_value("test")


        #gui geometry
        x, y = self.window.uistate.get("windowpos")
        w, h = self.window.uistate.get("windowsize")
        self.gui.move((w-300),0)

        self.gui.show_all()
        
        self.labelVar = ""
        self.timeout = ""

    
    lastInput = ""
    lastPage = ""
    pageTitleOnly = False
    menu = []
    #queryTime = 0    

    def change(self, editable): #widget, event,text        
        if self.timeout:
            gobject.source_remove(self.timeout)
        self.input = self.inputEntry.get_text() #self.inputText.get() #self.entry.get() + event.char
        
        if self.input == self.lastInput:            
            return

        self.lastInput = self.input

        if self.input[:len(self.title_match_char)] == self.title_match_char: # first char is "!" -> searches in page name only
            self.pageTitleOnly = True
            self.input = self.input[len(self.title_match_char):]
        else:
            self.pageTitleOnly = False


        # quick search in titles
        #queryCheck = self.input
        self.menu = defaultdict(_MenuItem) #mozne prikazy uzivatele
        found = 0
        input = self.input.lower()
        print("INPUT!!! ",input)
        for item,lowered in self.cached_titles:
            p = lowered.find(input) # if we search in titles, we want the title to start with the query
            #print("item: ",lowered, p)
            if re.search(r"(^|:|\s)"+input,lowered): # 'te' matches 'test' or 'Journal:test'
            #if p == 0 or lowered[p-1] == ":": # 'te' matches 'test' or 'Journal:test'
                #print("FOUND")
                self.menu[item].score = 1
                self.menu[item].isTitle = True
                found += 1
                if found >= 10: # vic nez 10 vysledku nechceme, snadno tam budeme mit vsechny
                    break
        #if found > 0:
        self.displayMenu() # zobrazit aspon vysledky hledani v titlech

        if len(self.input) >= self.start_search_length:
            #self.gui.after(self.keystroke_delay, lambda: self.search(queryCheck)) # ideal delay between keystrokes
            print("TIMEOUT START",self.timeout)
            self.timeout = gobject.timeout_add(self.keystroke_delay, self.search)
        

    def search(self): #, queryCheck
        self.timeout = ""
        print("VYHODNOCUJU")
        #if self.input == "" or queryCheck != self.input: # meanwhile, we added another letter → cancel search
        #    print("CANCEL ",self.input)
        #    return
        #else:
        #    print("RUN QUERY ", self.input, queryCheck)
        
        self.caret['altPos'] = 0 #mozne umisteni karetu - na zacatek
        
        s = '"*' + self.input + '*"'
        #print(s)
        self.query = Query(s)
        #self.scores = defaultdict(int)

        #if self.selection:
        #    selection = self.selection # teoreticky by predani puvodni selection melo zrychlit vysledky (protoze pri pridani pismenka staci prohledat jen vysledky z minula, ne vsechny stranky). Ale nevim, jestli se to tak deje (protoze pri backspacu a jinem retezci by to melo vyhledat zase mnohem mene resultu, nez pri jinem retezci samostatne).
        #else:
        #    selection = None
        self.selection = SearchSelection(self.window.ui.notebook).search(self.query, selection = self.selection, callback=self._search_callback)
        if len(self.menu) == 1:
            for page in self.menu:
                self._open_page(Path(page))
                break # first only, jak se to dela jinak?
            self.close()
        self.displayMenu()

        #self._search_callback(results)

        #call = "zim --search Notes '*" + self.input + "*'"
        #print(call)
        #process = subprocess.Popen(call, stdout=subprocess.PIPE, shell=True)


    def _search_callback(self, results, path):
        # Returning False will cancel the search
        #~ print '!! CB', path
        if results is not None:
            self._update_results(results)

        while gtk.events_pending():
            gtk.main_iteration(block=False)

        return True

    updateI = 0

    def _update_results(self, results):
        #if self.updateI == 0:
        #    self.menu = defaultdict(_MenuItem) #mozne prikazy uzivatele
        #    print("RESET now")
        self.updateI += 1
        #print("UPDATE", results)
        #self.matches = str(process.communicate()[0]).split("\n")[:-1] #, "utf-8"        
        for option in results.scores:
            if self.pageTitleOnly and self.input not in option.name: # hledame jen v nazvu stranky
                continue

            if option.name in self.menu: # we ignore 'score'
                continue

            #results.scores[option]
            #print("SCORES")
            self.scores[option.name] += 1
            #print(self.scores)
            self.menu[option.name].score = results.scores[option] #zaradit mezi moznosti
            if option.name == self.caret['text']: #karet byl na tehle pozici, pokud se zuzil vyber, budeme vedet, kam karet opravne umistit
                self.caret['altPos'] = len(self.menu)-1
        #self.displayMenu()


    def displayMenu(self):
        print("Displaying menu")
        self.gui.resize(300,100) # reset size
        #osetrit vychyleni karetu
        if self.caret['pos'] < 0 or self.caret['pos'] > len(self.menu)-1: #umistit karet na zacatek ci konec seznamu
            self.caret['pos'] = self.caret['altPos']

        #vypsat self.menu
        #print("caret:" + str(caret['pos']))
        text = ""                


        
        newlist = sorted(self.menu, reverse = True, key=lambda item: (self.menu[item].isTitle, self.menu[item].score, -item.count(":"),item))        
        #print(" ********** \n\n\n")
        #print("menu")
        #print(str(self.menu))
        #for item in newlist:
        #    print(str(item) + " sc:" + str(self.menu[item].score) + " title:" + str(self.menu[item].isTitle))

        for i, item in enumerate(newlist):#
            if i == self.caret['pos']: #karet je na pozici
                self.caret['text'] = item
                text += '→' + item + " ("+ str(self.menu[item].score) + ")\n"#vypsat moznost tucne
            else:
                try:
                    text += item + " ("+ str(self.menu[item].score) + ")\n"
                except:
                    text += "CHYBA\n"
                    text += item[0:-1] + "\n"

        self.labelObject.set_text(text)
        #self.labelVar.set(text)
        print("Displaying menu ended.")
            #subprocess.Popen('zim Notes "'+page+'"', shell=True)
        #print("*** VYHODNOCENI ***")
        page = self.caret['text']
        #print(page)
        self._open_page(Path(page))        
            # krade focus po pet vterin, abych mezitim mel nahledy otevrenych oken zimu;
            #  jestli z toho bude plugin, tak tahle kulisarna snad zmizi, protoze si bude se zimem povidat interne
            #for i in range(50,5000,50):
            #    self.gui.after(i, lambda: self.entry.focus_force())

        
    def move(self, widget, event):
        keyname = gtk.gdk.keyval_name(event.keyval)
        if keyname == "Up":
            self.caret['pos'] -= 1
            self.displayMenu()

        if keyname == "Down":
            self.caret['pos'] += 1
            self.displayMenu()
        
        if keyname == "KP_Enter" or keyname == "Return":
            #self.gui.destroy() # page has been opened when the menu item was accessed by the caret
            self.gui.emit("close")

        if keyname == "Escape":
            self._open_page(Path(self.originalPage))
            #pprint("to bychom dali")
            #self.window.ui.history.uistate['list'] = self.history["list"]
            #self.window.ui.history.uistate['recent'] = self.history["recent"]
            #self.window.ui.history.uistate['current'] = self.history["current"]
            #self.window.ui.history.set_current(HistoryPath(self.originalPage))
            #pprint(self.window.ui.history.uistate['list'])
            # GTK to resi sam    self.close()

        #self.displayMenu()
        return

    ## Safely closes
    # when closing directly, Python gave allocation error
    def close(self):
        #self.gui.after(200, lambda: self.gui.destroy())
        self.timeout = gobject.timeout_add(self.keystroke_delay + 100, self.gui.emit,"close")

    # open page and highlight matches
    def _open_page(self, page):
        #print(self.lastPage)
        if page and page.name and page.name != self.lastPage:
            self.lastPage = page.name
            #print("page", page.name)
            self.window.ui.open_page(page)            
            # Popup find dialog with same query
            if self.query:# and self.query.simple_match:
                string = self.input#self.query.simple_match
                string = string.strip('*') # support partial matches                
                print(self.plugin.preferences['highlight_search'])
                if self.plugin.preferences['highlight_search']:                    
                    self.window.ui._mainwindow.pageview.show_find(string, highlight=True)
                    


# menu = defaultdict(_Menu)
class _MenuItem(set):
    def __init__(self):
        self.path = None
        self.score = None
        self.isTitle = False
