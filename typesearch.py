#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Search as you Type
#
#importy
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

logger = logging.getLogger('zim.plugins.typesearch')
with open("/tmp/test.tmp","w+") as f:
    f.write(str(logger.manager.__dict__))


import threading
from functools import wraps


def delay(delay=0.):
    """
    Decorator delaying the execution of a function for a while. (http://fredericiana.com/2014/11/14/settimeout-python-delay/)
    """
    def wrap(f):
        @wraps(f)
        def delayed(*args, **kwargs):
            timer = threading.Timer(delay, f, args=args, kwargs=kwargs)
            timer.start()
        return delayed
    return wrap

class TypesearchPlugin(PluginClass):

    plugin_info = {
		'name': _('EDVARD'), # T: plugin name
		'description': _('''\
XXX

(V0.1)
'''), # T: plugin description
		'author': "Edvard Rejthar"
		#'help': 'Plugins:Due date',
	}


@extends('MainWindow')
class TypesearchMainWindowExtension(WindowExtension):

    uimanager_xml = '''
    <ui>
    <menubar name='menubar'>
            <menu action='tools_menu'>
                    <placeholder name='plugin_items'>
                            <menuitem action='typesearch'/>
                    </placeholder>
            </menu>
    </menubar>
    </ui>
    '''


    gui = "";


    @action(_('_Typesearch'), accelerator='<ctrl>e') # T: menu item
    def typesearch(self):
        #with open("/tmp/test.tmp","w+") as f:
        #    f.write(str(self.window.__dict__))
        #    f.write(str(self.window.ui.__dict__))
            
          #DAT GUI WIDTH a pozicovat doprava:
            #self.window.windowpos': (0, 24),
            #self.window.windowsize. (1920, 1056),
            

        #init
        self.query = "" #prikaz uzivatele
        self.caret = {'pos':0, 'altPos':0, 'text':""}  #pozice kurzoru
        self.matches = [] # XX lze sem dat recentne pouzite

        self.gui = Tk()
        Label(self.gui, text="Typesearch (if 1st letter is !, search in page titles only):").pack()
        self.gui.bind('<Up>', self.move)
        self.gui.bind('<Down>', self.move)
        self.gui.bind('<Enter>', self.move)
        self.gui.bind('<Return>', self.move)
        self.inputText = StringVar()
        self.inputText.trace("w", self.change)
        self.entry = Entry(self.gui, width=40, textvariable=self.inputText)
        self.entry.pack()
        self.entry.focus_set()
        self.labelText = StringVar()
        self.label = Label(self.gui, textvariable = self.labelText, justify = "left")
        self.label.pack()

        #self.gui.wm_attributes("-topmost", 1)

        self.gui.bind('<Up>', self.move) # XXX
        self.gui.bind('<Down>', self.move) # XXX
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


        #self.queryTime = int(round(time.time() * 1000))
        queryCheck = self.query
        self.gui.after(200, lambda: self.search(queryCheck))

    #@delay(0.2)
    def search(self,queryCheck):
        if self.query == "" or queryCheck != self.query: #mezitim jsme pripsali dalsi pismenko
            print("STORNO")
            return
        else:
            print("NON STORNO",self.query,queryCheck)
            #return
        #print millis
        #print("\x1B[3m" + (self.query if self.query else " ** character description **") + "\x1B[23m")# italikem vypsat prikaz
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
            self.gui.after(200, lambda: self.gui.destroy()) # kdyz se zaviralo hned, Python hazel allocation error
            #self.gui.quit()
            pass
            #subprocess.Popen('wmctrl -a zim', shell=True)

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

        self.displayMenu()
        return

        # tadyto jsem vubec neudelal
        #res.configure(text = "levo!!")
        #vyhodnotit moznost
        if len(self.menu) != 1: #stale mame vic moznosti
            key = getkey()

            if ord(key) == 27: #escape sequence
                print("Hit escape for exit...") #pokud jsem predtim napsal sipku, dalsi 2 znaky automaticky cekaji
                key = getkey()
                if ord(key) == 27: #dvojity escape - ukonceni
                    exit()
                elif ord(key) == 91: #sipka
                    key = getkey()
                    if ord(key) == 65: #sipka nahoru
                        self.caret['pos'] -= 1
                    if ord(key) == 66: #sipka dolu
                        self.caret['pos'] += 1
                elif ord(key) == 79: #home/end
                    key = getkey()
                    if ord(key) == 72: #home
                        self.caret['pos'] = 0
                    if ord(key) == 70: #end
                        self.caret['pos'] = len(self.menu) -1
                else:
                    print("NIC!")
            #elif(key == '\x08' or key == '\x7f'):#backspace
            #    self.caret['pos'] = -1
            #    self.query = self.query[:-1]
            elif(ord(key) == 13): #enter
                self.menu = [self.menu[self.caret['pos']]] #launch command at caret
            #elif(key == '\x03' or key == '\x04'):#interrupt  (zel nefunguje)
            #    exit()
            elif(49 <= ord(key) <= 57):#cisla spousti prikaz na danem radku
                self.menu = [self.menu[ord(key)-49]]
                #query = self.menu[ord(key)-49] #launch command at number
            else:
                print("ZDE")
                self.caret['pos'] = -1
                self.query += key
        self.displayMenu()
#Typesearch()
