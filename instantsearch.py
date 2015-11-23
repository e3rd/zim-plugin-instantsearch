#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Search instantly as you Type. Edvard Rejthar
#
import ConfigParser
from Tkinter import Tk, Entry, Label, StringVar
import re
import select
import subprocess
import sys
import termios
import time
import tty
import time

import gtk

from zim.plugins import PluginClass, extends, WindowExtension
from zim.actions import action
from zim.notebook import Path
from zim.gui.widgets import ui_environment, MessageDialog, Dialog

import logging

logger = logging.getLogger('zim.plugins.instantsearch')

class InstantsearchPlugin(PluginClass):

    plugin_info = {
		'name': _('Instantsearch'), # T: plugin name
		'description': _('''\
XXX

(V0.1)
'''), # T: plugin description
		'author': "Edvard Rejthar"
		#'help': 'Plugins:Due date',
	}


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
        #print("EDVAAAAAAAAARD")
        #print(str(self.window.ui.pageview))
        #print(str(self.window.ui.pageview.__dict__))
        

        #with open("/tmp/test.js","w") as f:
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
            

        #init
        self.query = "" # user input
        self.caret = {'pos':0, 'altPos':0, 'text':""}  # cursor position
        #self.matches = [] # XX lze sem dat recentne pouzite
        self.originalPage = self.window.ui.page.name # we return here after escape

        self.gui = Tk()
        Label(self.gui, text="Instantsearch (if 1st letter is !, search in page titles only):").pack()
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
        self.labelText = StringVar()
        self.label = Label(self.gui, textvariable = self.labelText, justify = "left")
        self.label.pack()

        #gui geometry
        x,y = self.window.uistate.get("windowpos")
        w,h = self.window.uistate.get("windowsize")
        #with open("/tmp/test.js","w") as f:
        #    f.write(str(x) + " " + str((x+w-200)))
        self.gui.geometry('+%d+0' % (x+w-200))
        #self.gui.wm_attributes("-topmost", 1)

        self.gui.mainloop()

    
    lastQuery = ""
    lastPage = ""
    pageTitleOnly = False
    menu = []
    #queryTime = 0    

    def change(self,one, two, text):
        self.query = self.inputText.get() #self.entry.get() + event.char

        if len(self.query) < 3 or self.query == self.lastQuery:
            return

        self.lastQuery = self.query

        if self.query[:1] == "!": #prvni znak vykricnik - hleda se nazev stranky
            self.pageTitleOnly = True
            self.query = self.query[1:]
        else:
            self.pageTitleOnly = False

        queryCheck = self.query
        self.gui.after(100, lambda: self.search(queryCheck))

    def search(self,queryCheck):
        if self.query == "" or queryCheck != self.query: # meanwhile, we added another letter
            print("STORNO")
            return
        else:
            print("NON STORNO",self.query,queryCheck)

        self.menu = [] #mozne prikazy uzivatele
        self.caret['altPos'] = 0 #mozne umisteni karetu - na zacatek

        call = "zim --search Notes '*" + self.query + "*'"
        print(call)
        process = subprocess.Popen(call, stdout=subprocess.PIPE, shell=True)            
        self.matches = str(process.communicate()[0]).split("\n")[:-1] #, "utf-8"

        print("matches",self.matches)
        for option in self.matches:
            if self.pageTitleOnly and self.query not in option: # hledame jen v nazvu stranky
                continue

            self.menu.append(option) #zaradit mezi moznosti
            if option == self.caret['text']: #karet byl na tehle pozici, pokud se zuzil vyber, budeme vedet, kam karet opravne umistit
                self.caret['altPos'] = len(self.menu)-1
        self.displayMenu()

    def displayMenu(self):
        #osetrit vychyleni karetu
        if self.caret['pos'] < 0 or self.caret['pos'] > len(self.menu)-1: #umistit karet na zacatek ci konec seznamu
            self.caret['pos'] = self.caret['altPos']

        #vypsat self.menu
        #print("caret:" + str(caret['pos']))

        text = ""
        for i,item in enumerate(self.menu):
            if i == self.caret['pos']: #karet je na pozici
                self.caret['text'] = item
                text += '*' + item + "\n"#vypsat moznost tucne
            else:
                try:
                    text += item + "\n"
                except:
                    text += "CHYBA\n"
                    text += item[0:-1] + "\n"

        self.labelText.set(text)
        
        page = self.caret['text']
        if page and page != self.lastPage:
            self.lastPage = page
            print("page",page)
            #subprocess.Popen('zim Notes "'+page+'"', shell=True)
            self.window.ui.open_page(Path(page))
            
            # krade focus po pet vterin, abych mezitim mel nahledy otevrenych oken zimu;
            #  jestli z toho bude plugin, tak tahle kulisarna snad zmizi, protoze si bude se zimem povidat interne
            #for i in range(50,5000,50):
            #    self.gui.after(i, lambda: self.entry.focus_force())

        print("len", len(self.menu))
        print("len?", len(self.menu) == 1)
        if len(self.menu) == 1:
            if self.lastPage != page:
                self.window.ui.open_page(Path(page))
            self.close()

    def move(self,event):
        if event.keysym == "Up":
            self.caret['pos'] -= 1

        if event.keysym == "Down":
            self.caret['pos'] += 1

        if event.keysym == "Enter" or event.keysym == "Return":
            #self.menu = [self.menu[self.caret['pos']]] #launch command at caret
            self.gui.destroy()
            #exit(0)
            pass

        if event.keysym == "Escape":
            self.window.ui.open_page(Path(self.originalPage))
            self.close()

        self.displayMenu()
        return

    ## Safely closes
    def close(self):
        self.gui.after(200, lambda: self.gui.destroy()) # when closing directly, Python gave allocation error
