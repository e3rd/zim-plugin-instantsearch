#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Search instantly as you Type. Edvard Rejthar
# https://github.com/e3rd/zim-plugin-instantsearch
#
import ConfigParser
from Tkinter import Entry
from Tkinter import Label
from Tkinter import StringVar
from Tkinter import Tk
from collections import defaultdict
import gtk
import logging
import re
import select
import subprocess
import sys
import termios
import time
import tty
from zim.actions import action
from zim.gui.widgets import Dialog
from zim.gui.widgets import MessageDialog
from zim.gui.widgets import ui_environment
from zim.notebook import Path
from zim.plugins import PluginClass
from zim.plugins import WindowExtension
from zim.plugins import extends
from zim.search import *

logger = logging.getLogger('zim.plugins.instantsearch')

class InstantsearchPlugin(PluginClass):

    plugin_info = {
        'name': _('Instant Search'), # T: plugin name
        'description': _('''\
Instant search allows you to filter as you type feature known from I.E. OneNote.
When you hit Ctrl+E, small window opens, in where you can type.
As you type third letter, every page that matches your search is listed.
You can walk through by UP/DOWN arrow, hit Enter to stay on the page, or Esc to cancel. Much quicker than current Zim search.

(V0.3)
'''),
        'author': "Edvard Rejthar"
        #'help': 'Plugins:Due date',
    }

    plugin_preferences = (
		# T: label for plugin preferences dialog
                ('title_match_char', 'string', _('Match title with query starting with the char'), "!"),
                ('start_search_length', 'int', _('Start the search when number of letters written'), 3),
                ('keystroke_delay', 'int', _('Keystroke delay'), 150),
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
        with open("/tmp/test.js","w") as f:
            #f.write(str(self.window.ui.notebook.index.list_pages(Path(':')))+"\n")
            for s in self.window.ui.notebook.index.list_pages(Path(':')):
                #f.write(str(s)+"\n")
                #f.write(str(s.basename)+"\n")
                self.cached_titles.append(s.basename)
                    #f.write(str(self.window.ui.notebook.get_pagelist(Path(s.basename))))
                for s2 in self.window.ui.notebook.get_pagelist(Path(s.basename)):
                    #f.write(str(s2)+"\n")
                    #f.write(str(s2.basename)+"\n")
                    self.cached_titles.append(s.basename+":"+s2.basename)
                    for s3 in self.window.ui.notebook.get_pagelist(Path(s.basename+":"+s2.basename)):
                    #f.write(str(s2)+"\n")
                    #f.write(str(s2.basename)+"\n")
                        self.cached_titles.append(s.basename+":"+s2.basename+":"+s3.basename)
            f.write(str(self.cached_titles))

            #        f.write(" -"+str(s2)+"\n")
            #        for s3 in self.window.ui.notebook.get_pagelist(s2):
            #            f.write(" -"+str(s2)+"\n")
        #print(str(self.window.ui.pageview))
        #print(str(self.window.ui.pageview.__dict__))    
        
            #f.write(str(x+w-200))
            #f.write(str(self.window.windowpos[0]))
            #f.write(str(self.window.windowpos[0] + self.window.windowsize[0] - 200))
            #f.write("start")
            #f.write(str(self.window.__dict__))
            #f.write("\nsecond")
            #f.write(str(self.window.ui.__dict__))
            #f.write("\n\n\nthird")
            #f.write(str(self.window.ui.page.name))            
        #DAT GUI WIDTH a pozicovat doprava:
            #self.window.windowpos': (0, 24),
            #self.window.windowsize. (1920, 1056),
            

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

        self.gui = Tk()
        Label(self.gui, text="Instantsearch").pack()
        self.gui.bind('<Up>', self.move)
        self.gui.bind('<Down>', self.move)
        self.gui.bind('<Enter>', self.move)
        self.gui.bind('<Return>', self.move)
        self.gui.bind('<Escape>', self.move)

        # input text
        self.inputText = StringVar()
        self.inputText.trace("w", self.change)
        self.entry = Entry(self.gui, width=40, textvariable=self.inputText)
        self.entry.pack()
        self.entry.focus_set()

        # output text
        self.labelText = ""
        self.labelVar = StringVar()
        self.label = Label(self.gui, textvariable=self.labelVar, justify="left")
        self.label.pack()

        #gui geometry
        x, y = self.window.uistate.get("windowpos")
        w, h = self.window.uistate.get("windowsize")
        #with open("/tmp/test.js","w") as f:
        #    f.write(str(x) + " " + str((x+w-200)))
        self.gui.geometry('+%d+0' % (x + w-200))
        #self.gui.wm_attributes("-topmost", 1)
        self.scores = defaultdict(int)
        self.gui.mainloop()

    
    lastInput = ""
    lastPage = ""
    pageTitleOnly = False
    menu = []
    #queryTime = 0    

    def change(self, one, two, text):        
        self.input = self.inputText.get() #self.entry.get() + event.char

        if self.input == self.lastInput:
            return

        self.lastInput = self.input

        if self.input[:len(self.title_match_char)] == self.title_match_char: #prvni znak vykricnik - hleda se nazev stranky
            self.pageTitleOnly = True
            self.input = self.input[len(self.title_match_char):]
        else:
            self.pageTitleOnly = False

        queryCheck = self.input
        self.menu = defaultdict(_MenuItem) #mozne prikazy uzivatele
        found = 0
        for item in self.cached_titles: # quick search in titles
            p = item.find(self.input) # if we search in titles, we want the title to start with the query
            if p == 0 or item[p-1] == ":": # 'te' matches 'test' or 'Journal:test'
                self.menu[item].score = 1
                self.menu[item].isTitle = True
                found += 1
                if found >= 10: # vic nez 10 vysledku nechceme, snadno tam budeme mit vsechny
                    break
        self.displayMenu() # zobrazit aspon vysledky hledani v titlech

        if len(self.input) >= self.start_search_length:
            self.gui.after(self.keystroke_delay, lambda: self.search(queryCheck)) # ideal delay between keystrokes        
        

    def search(self, queryCheck):
        if self.input == "" or queryCheck != self.input: # meanwhile, we added another letter → cancel search
            print("CANCEL ",self.input)
            return
        else:
            print("RUN QUERY ", self.input, queryCheck)
        
        self.caret['altPos'] = 0 #mozne umisteni karetu - na zacatek
        
        s = '"*' + self.input + '*"'
        print(s)
        self.query = Query(s)
        #self.scores = defaultdict(int)

        if self.selection:
            selection = self.selection # teoreticky by predani puvodni selection melo zrychlit vysledky (protoze pri pridani pismenka staci prohledat jen vysledky z minula, ne vsechny stranky). Ale nevim, jestli se to tak deje (protoze pri backspacu a jinem retezci by to melo vyhledat zase mnohem mene resultu, nez pri jinem retezci samostatne).
        else:
            selection = None
        self.selection = SearchSelection(self.window.ui.notebook).search(self.query, selection = None, callback=self._search_callback)
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
        self.updateI += 1
        #print("UPDATE", self.updateI)
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
        #osetrit vychyleni karetu
        if self.caret['pos'] < 0 or self.caret['pos'] > len(self.menu)-1: #umistit karet na zacatek ci konec seznamu
            self.caret['pos'] = self.caret['altPos']

        #vypsat self.menu
        #print("caret:" + str(caret['pos']))

        text = ""
        
        

        
        newlist = sorted(self.menu, reverse = True, key=lambda item: (self.menu[item].isTitle, self.menu[item].score, item))
        print(" ********** \n\n\n")
        print("menu")
        print(str(self.menu))
        for item in newlist:
            print(str(item) + " sc:" + str(self.menu[item].score) + " title:" + str(self.menu[item].isTitle))

        for i, item in enumerate(newlist):#
            if i == self.caret['pos']: #karet je na pozici
                self.caret['text'] = item
                text += '*' + item + " ("+ str(self.menu[item].score) + ")\n"#vypsat moznost tucne
            else:
                try:
                    text += item + " ("+ str(self.menu[item].score) + ")\n"
                except:
                    text += "CHYBA\n"
                    text += item[0:-1] + "\n"
        
        self.labelVar.set(text)
                        
            #subprocess.Popen('zim Notes "'+page+'"', shell=True)
        page = self.caret['text']
        self._open_page(Path(page))
            
            # krade focus po pet vterin, abych mezitim mel nahledy otevrenych oken zimu;
            #  jestli z toho bude plugin, tak tahle kulisarna snad zmizi, protoze si bude se zimem povidat interne
            #for i in range(50,5000,50):
            #    self.gui.after(i, lambda: self.entry.focus_force())

        
    def move(self, event):
        if event.keysym == "Up":
            self.caret['pos'] -= 1

        if event.keysym == "Down":
            self.caret['pos'] += 1

        if event.keysym == "Enter" or event.keysym == "Return":
            self.gui.destroy() # page has been opened when the menu item was accessed by the caret

        if event.keysym == "Escape":
            self._open_page(Path(self.originalPage))
            self.close()

        self.displayMenu()
        return

    ## Safely closes
    # when closing directly, Python gave allocation error
    def close(self):
        self.gui.after(200, lambda: self.gui.destroy())

    # open page and highlight matches
    def _open_page(self, page):
        if page and page.name and page.name != self.lastPage:
            self.lastPage = page.name
            print("page", page.name)
            self.window.ui.open_page(page)
            # Popup find dialog with same query
            if self.query:# and self.query.simple_match:
                string = self.input#self.query.simple_match
                string = string.strip('*') # support partial matches
                #self.window.ui.mainwindow.pageview.show_find(string, highlight=True)


# menu = defaultdict(_Menu)
class _MenuItem(set):
    def __init__(self):
        self.path = None
        self.score = None
        self.isTitle = False
