#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Reggie! - New Super Mario Bros. Wii Level Editor
# Copyright (C) 2009-2010 Treeki, Tempus


# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from ctypes import create_string_buffer
import encodings # fixes "LookupError: no codec search functions
                 # registered: can't find encoding" on
                 # Py2+cx_Freeze+Linux
import os.path
import pickle
import pickletools
import struct
import sys
import time
import warnings
from xml.dom import minidom

import archive
import lz77
import sprites
from qt_compat import (qm, execQtObject, importQt,
    QValidatorValidateCompat, QtCoreSignal, QtCoreSlot, PyObject)

QtCore, QtGui, QtWidgets, QtCompatVersion, QtBindingsVersion, QtName = importQt()

ReggieID = 'Reggie-Updated by Treeki, Tempus'
ApplicationDisplayName = 'Reggie! Level Editor'

# from C <float.h>
# useful because sys.float_info is for double and not float
FLT_MAX = 3.402823466e+38
FLT_DIG = 6


LEVEL_FILE_FORMATS_FILTER_ALL_SUPPORTED = 'All supported files (*.arc *.arc.LZ)'
LEVEL_FILE_FORMATS_FILTER_ARC = 'Level archives (*.arc)'
LEVEL_FILE_FORMATS_FILTER_ARC_LZ = 'LZ11-compressed level archives (*.arc.LZ)'
LEVEL_FILE_FORMATS_FILTER_ALL = 'All Files (*)'

LEVEL_FILE_FORMATS_FILTER_SAVE = ';;'.join([
    LEVEL_FILE_FORMATS_FILTER_ARC,
    LEVEL_FILE_FORMATS_FILTER_ARC_LZ,
    LEVEL_FILE_FORMATS_FILTER_ALL])
LEVEL_FILE_FORMATS_FILTER_OPEN = ';;'.join([
    LEVEL_FILE_FORMATS_FILTER_ALL_SUPPORTED,
    LEVEL_FILE_FORMATS_FILTER_ARC,
    LEVEL_FILE_FORMATS_FILTER_ARC_LZ,
    LEVEL_FILE_FORMATS_FILTER_ALL])


# use psyco for optimisation if available
try:
    import psyco
    HavePsyco = True
except ImportError:
    HavePsyco = False

# use nsmblib if possible
try:
    import nsmblib
    HaveNSMBLib = True
except ImportError:
    HaveNSMBLib = False

# Some Py2/Py3 compatibility helpers

if sys.version_info.major >= 3:
    unicode = str
    intsToBytes = bytes
    unichr = chr

    def keyInAttribs(key, node):
        return key in node.attributes

else:

    def intsToBytes(L):
        return b''.join(chr(x) for x in L)

    def keyInAttribs(key, node):
        return node.attributes.has_key(key)

_ord = ord
def ord(x):
    if isinstance(x, int):
        return x
    return _ord(x)


app = None
mainWindow = None
settings = None

gamePath = None

def module_path():
    """
    This will get us the program's directory, even if we are frozen
    using PyInstaller
    """

    if hasattr(sys, 'frozen') and hasattr(sys, '_MEIPASS'):  # PyInstaller
        if sys.platform == 'darwin':  # macOS
            # sys.executable is /x/y/z/reggie.app/Contents/MacOS/reggie
            # We need to return /x/y/z/reggie.app/Contents/Resources/

            macos = os.path.dirname(sys.executable)
            if os.path.basename(macos) != 'MacOS':
                return None

            return os.path.join(os.path.dirname(macos), 'Resources')

        else:  # Windows, Linux
            return os.path.dirname(sys.executable)

    if __name__ == '__main__':
        return os.path.dirname(os.path.abspath(__file__))

    return None

def IsNSMBLevel(filename):
    """Does some basic checks to confirm a file is a NSMB level"""
    if not os.path.isfile(filename): return False
    with open(filename, 'rb') as f:
        data = f.read()

    if data.startswith(b'\x11'):
        # LZ-compressed -- not much we can do without decompressing it,
        # so let's just assume it's probably valid...
        return True

    elif data.startswith(b'U\xAA8-'):
        # uncompressed U8 data -- we can do some more sanity checks

        if b'course\0' not in data and b'course1.bin\0' not in data and b'\0\0\0\x80' not in data:
            return False

        return True

    else:
        return False

def FilesAreMissing():
    """Checks to see if any of the required files for Reggie are missing"""

    if not os.path.isdir('reggiedata'):
        QtWidgets.QMessageBox.warning(None, 'Error', "Sorry, you seem to be missing the required data files for Reggie! to work. If you're running Reggie! from within a zip file, please extract it and try again. Otherwise, please redownload your copy of the editor.")
        return True

    required = ['entrances.png', 'entrancetypes.txt', 'icon_reggie.png', 'levelnames.txt', 'overrides.png',
                'spritedata.xml', 'tilesets.txt', 'bga/000A.png', 'bga.txt', 'bgb/000A.png', 'bgb.txt',
                'music.txt', 'about.html', 'spritecategories.xml']

    missing = []

    for check in required:
        if not os.path.isfile('reggiedata/' + check):
            missing.append(check)

    if len(missing) > 0:
        QtWidgets.QMessageBox.warning(None, 'Error',  'Sorry, you seem to be missing some of the required data files for Reggie! to work. Please redownload your copy of the editor. These are the files you are missing: ' + ', '.join(missing))
        return True

    return False


def GetIcon(name):
    """Helper function to grab a specific icon"""
    return QtGui.QIcon('reggiedata/icon_%s.png' % name)


def SetGamePath(newpath):
    """Sets the NSMBWii game path"""

    global gamePath

    # you know what's fun?
    # isValidGamePath crashes in os.path.join if QString is used..
    # so we must change it to a Python string manually
    gamePath = unicode(newpath)


def isValidGamePath(check='ug'):
    """Checks to see if the path for NSMBWii contains a valid game"""
    if check == 'ug': check = gamePath

    if check is None or check == '': return False
    if not os.path.isdir(check): return False
    if not (os.path.isdir(os.path.join(check, 'Texture')) or os.path.isdir(os.path.join(check, '../Tilesets'))): return False
    if not (os.path.isfile(os.path.join(check, '01-01.arc')) or os.path.isfile(os.path.join(check, '01-01.arc.LZ'))): return False

    return True


def PromptUserForNewGamePath():
    """Repeatedly prompt the user until they select a game path or cancel"""
    path = None
    while True:
        path = QtWidgets.QFileDialog.getExistingDirectory(None, "Choose the game's Stage folder")
        if not path:
            return None

        path = unicode(path)

        if isValidGamePath(path):
            return path
        else:
            result = QtWidgets.QMessageBox.warning(None, 'Warning',  "This folder doesn't have all of the files from the extracted <i>New Super Mario Bros. Wii</i> Stage folder. You've probably selected the wrong folder.<br><br>Are you sure you want to choose this folder?", QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel)
            if result == QtWidgets.QMessageBox.StandardButton.Yes:
                return path


def setUpDarkMode():
    """Sets up dark mode theming"""
    # Taken from https://gist.github.com/QuantumCD/6245215

    app.setStyle(QtWidgets.QStyleFactory.create('Fusion'))

    darkPalette = QtGui.QPalette()
    darkPalette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(53,53,53))
    darkPalette.setColor(QtGui.QPalette.ColorRole.WindowText, QtCore.Qt.GlobalColor.white)
    darkPalette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(25,25,25))
    darkPalette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(53,53,53))
    darkPalette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtCore.Qt.GlobalColor.white)
    darkPalette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtCore.Qt.GlobalColor.white)
    darkPalette.setColor(QtGui.QPalette.ColorRole.Text, QtCore.Qt.GlobalColor.white)
    darkPalette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(53,53,53))
    darkPalette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtCore.Qt.GlobalColor.white)
    darkPalette.setColor(QtGui.QPalette.ColorRole.BrightText, QtCore.Qt.GlobalColor.red)
    darkPalette.setColor(QtGui.QPalette.ColorRole.Link, QtGui.QColor(42, 130, 218))

    darkPalette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(42, 130, 218))
    darkPalette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtCore.Qt.GlobalColor.black)

    # fix for disabled menu options
    darkPalette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QtGui.QColor(127,127,127))
    darkPalette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Light, QtGui.QColor(53,53,53))

    app.setPalette(darkPalette)

    app.setStyleSheet("""
        QToolTip { color: #ffffff; background-color: #2a82da; border: 1px solid white }
        #qt_toolbar_ext_button { background-color: #555; border: 1px solid #888; border-radius: 2px }
        #qt_toolbar_ext_button::hover { background-color: #666 }
    """)


LevelNames = None
def LoadLevelNames():
    """Ensures that the level name info is loaded"""
    global LevelNames
    if LevelNames is not None: return

    with open('reggiedata/levelnames.txt') as f:
        raw = [x.strip() for x in f.readlines()]

    LevelNames = []
    CurrentWorldName = None
    CurrentWorld = None

    for line in raw:
        if line == '': continue
        if line.startswith('-'):
            if CurrentWorld is not None:
                LevelNames.append((CurrentWorldName,CurrentWorld))

            CurrentWorldName = line[1:]
            CurrentWorld = []
        else:
            d = line.split('|')
            CurrentWorld.append((d[0], d[1]))

    if CurrentWorld is not None:
        LevelNames.append((CurrentWorldName,CurrentWorld))


TilesetNames = None
def LoadTilesetNames():
    """Ensures that the tileset name info is loaded"""
    global TilesetNames
    if TilesetNames is not None: return

    with open('reggiedata/tilesets.txt') as f:
        raw = [x.strip() for x in f.readlines()]

    TilesetNames = []
    StandardSuite = []
    StageSuite = []
    BackgroundSuite = []
    InteractiveSuite = []

    for line in raw:
        if line.startswith('Pa0'):
            w = line.split('=')
            StandardSuite.append((w[0], w[1]))
        if line.startswith('Pa1'):
            w = line.split('=')
            StageSuite.append((w[0], w[1]))
        if line.startswith('Pa2'):
            w = line.split('=')
            BackgroundSuite.append((w[0], w[1]))
        if line.startswith('Pa3'):
            w = line.split('=')
            InteractiveSuite.append((w[0], w[1]))
    TilesetNames.append(StandardSuite)
    TilesetNames.append(StageSuite)
    TilesetNames.append(BackgroundSuite)
    TilesetNames.append(InteractiveSuite)


ObjDesc = None
def LoadObjDescriptions():
    """Ensures that the object description is loaded"""
    global ObjDesc
    if ObjDesc is not None: return

    with open('reggiedata/ts1_descriptions.txt') as f:
        raw = [x.strip() for x in f.readlines()]

    ObjDesc = {}
    for line in raw:
        w = line.split('=')
        ObjDesc[int(w[0])] = w[1]


BgANames = None
def LoadBgANames():
    """Ensures that the background name info is loaded"""
    global BgANames
    if BgANames is not None: return

    with open('reggiedata/bga.txt') as f:
        raw = [x.strip() for x in f.readlines()]

    BgANames = []

    for line in raw:
        w = line.split('=')
        BgANames.append([w[0], w[1]])


BgBNames = None
def LoadBgBNames():
    """Ensures that the background name info is loaded"""
    global BgBNames
    if BgBNames is not None: return

    with open('reggiedata/bgb.txt') as f:
        raw = [x.strip() for x in f.readlines()]

    BgBNames = []

    for line in raw:
        w = line.split('=')
        BgBNames.append([w[0], w[1]])




BgScrollRates = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0, 0.0, 1.2, 1.5, 2.0, 4.0]
BgScrollRateStrings = ['None', '0.125x', '0.25x', '0.375x', '0.5x', '0.625x', '0.75x', '0.875x', '1x', 'None', '1.2x', '1.5x', '2x', '4x']

ZoneThemeValues = [
    'Overworld', 'Underground', 'Underwater', 'Lava Underground',
    'Desert', 'Beach', 'Forest', 'Snow Overworld',
    'Sky/Bonus*', 'Mountains', 'Tower', 'Castle',
    'Ghost House', 'River Cave', 'Ghost House Exit', 'Underwater Cave',
    'Desert Cave', 'Icy Cave*', 'Lava', 'Final Battle',
    'World 8 Tower/Castle', 'World 8 Airship*', 'World 7 Tower Indoors',
]

ZoneTerrainThemeValues = [
    'Normal', 'Underground*', 'Underwater*', 'Lava*',
]

Sprites = None
SpriteCategories = None

class SpriteDefinition():
    """Stores and manages the data info for a specific sprite"""

    class ListPropertyModel(QtCore.QAbstractListModel):
        """Contains all the possible values for a list property on a sprite"""

        def __init__(self, entries, existingLookup, max):
            """Constructor"""
            super(SpriteDefinition.ListPropertyModel, self).__init__()
            self.entries = entries
            self.existingLookup = existingLookup
            self.max = max

        def rowCount(self, parent=None):
            """Required by Qt"""
            return len(self.entries)

        def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
            """Get what we have for a specific row"""
            if not index.isValid(): return None
            n = index.row()
            if n < 0: return None
            if n >= len(self.entries): return None

            if role == QtCore.Qt.ItemDataRole.DisplayRole:
                return '%d: %s' % self.entries[n]

            return None


    def loadFrom(self, elem):
        """Loads in all the field data from an XML node"""
        self.fields = []
        fields = self.fields

        for field in elem.childNodes:
            if field.nodeType != field.ELEMENT_NODE: continue
            if field.nodeName in ['dependency', 'suggested']: continue  # Reggie Next compatibility

            attribs = field.attributes
            if field.nodeName in ['dualbox', 'multidualbox']:  # Reggie Next compatibility
                title = attribs['title2'].nodeValue
            else:
                title = attribs['title'].nodeValue

            commentParts = []
            if keyInAttribs('comment', field):
                commentParts.append(field.attributes['comment'].nodeValue)
            if keyInAttribs('comment2', field):  # Reggie Next compatibility
                commentParts.append(field.attributes['comment2'].nodeValue)
            if keyInAttribs('advancedcomment', field):  # Reggie Next compatibility
                commentParts.append(field.attributes['advancedcomment'].nodeValue)
            if commentParts:
                comment = '<b>%s</b>:<br>%s' % (title, '<br/><br/>'.join(commentParts))
            else:
                comment = None


            maskHint = None
            if keyInAttribs('nybble', field):
                snybble = attribs['nybble'].nodeValue
            elif keyInAttribs('bit', field):  # Reggie Next compatibility
                bit = attribs['bit'].nodeValue
                if ',' in bit:  # just take the least significant part -- close enough
                    bit = bit.split(',')[-1]
                bit = bit.strip()
                if bit.count('-') == 0:  # one bit -- we can infer a mask from this for checkboxes, too
                    bit = int(bit)
                    snybble = str(((bit - 1) // 4) + 1)
                    maskHint = 1 << (3 - ((bit - 1) % 4))
                elif bit.count('-') == 1:  # multiple bits; hopefully it aligns to a nybble
                    startbit, endbit = bit.split('-')
                    startbit, endbit = int(startbit), int(endbit)
                    if endbit % 4 != 0:
                        # We're going to seriously lose precision here -- possibly
                        # catastrophically -- but maybe the code for the individual
                        # nodeName can use the maskHint to produce some reasonable behavior
                        maskHint = 1 << (3 - ((endbit - 1) % 4))
                    startnyb, endnyb = ((startbit - 1) // 4) + 1, ((endbit - 1) // 4) + 1
                    if startnyb == endnyb:
                        snybble = str(startnyb)
                    else:
                        snybble = '%d-%d' % (startnyb, endnyb)
                else:
                    raise ValueError('Invalid "bit" field')

            if keyInAttribs('mask', field):
                mask = int(attribs['mask'].nodeValue)
            elif maskHint is not None:
                mask = maskHint
            else:
                mask = 1

            if field.nodeName in ['checkbox', 'dualbox']:
                # parameters: title, nybble, mask, comment
                if '-' not in snybble:
                    nybble = int(snybble) - 1
                else:
                    getit = snybble.split('-')
                    nybble = (int(getit[0]) - 1, int(getit[1]))

                fields.append((0, title, nybble, mask, comment))

            elif field.nodeName == 'list':
                # parameters: title, nybble, model, comment

                if '-' not in snybble:
                    nybble = int(snybble) - 1
                    max = 16
                else:
                    getit = snybble.split('-')
                    nybble = (int(getit[0]) - 1, int(getit[1]))
                    max = (16 << ((nybble[1] - nybble[0] - 1) * 4))

                entries = []
                existing = [None for i in range(max)]
                for e in field.childNodes:
                    if e.nodeType != e.ELEMENT_NODE: continue
                    if e.nodeName != 'entry': continue

                    i = int(e.attributes['value'].nodeValue)
                    if e.childNodes:
                        name = e.childNodes[0].nodeValue
                    else:  # Reggie Next compatibility
                        name = str(i)

                    if maskHint:
                        # Reggie Next compatibility
                        valuesToAdd = []
                        for j in range(maskHint):
                            valuesToAdd.append(i * maskHint + j)
                    else:
                        valuesToAdd = [i]

                    for v in valuesToAdd:
                        entries.append((v, name))
                        existing[v] = True

                fields.append((1, title, nybble, SpriteDefinition.ListPropertyModel(entries, existing, max), comment))

            elif field.nodeName in ['value', 'multidualbox']:
                # parameters: title, nybble, max, comment

                # if it's 5-12 skip it
                # fixes tobias's crashy "unknown values"
                if snybble == '5-12': continue

                if '-' not in snybble:
                    nybble = int(snybble) - 1
                    max = 16
                else:
                    getit = snybble.split('-')
                    nybble = (int(getit[0]) - 1, int(getit[1]))
                    max = (16 << ((nybble[1] - nybble[0] - 1) * 4))

                fields.append((2, title, nybble, max, comment))

            else:
                raise ValueError(field.nodeName)


def LoadSpriteData():
    """Ensures that the sprite data info is loaded"""
    global Sprites
    if Sprites is not None: return

    Sprites = [None] * 483

    sd = minidom.parse('reggiedata/spritedata.xml')
    root = sd.documentElement
    errors = []
    errortext = []

    for sprite in root.childNodes:
        if sprite.nodeType != sprite.ELEMENT_NODE: continue
        if sprite.nodeName != 'sprite': continue

        spriteid = int(sprite.attributes['id'].nodeValue)
        spritename = unicode(sprite.attributes['name'].nodeValue)

        notesParts = []
        if keyInAttribs('notes', sprite):
            notesParts.append(sprite.attributes['notes'].nodeValue)
        if keyInAttribs('advancednotes', sprite):  # Reggie Next compatibility
            notesParts.append(sprite.attributes['advancednotes'].nodeValue)
        if notesParts:
            notes = '<b>Sprite Notes:</b> ' + '<br/><br/>'.join(notesParts)
        else:
            notes = None

        sdef = SpriteDefinition()
        sdef.id = spriteid
        sdef.name = spritename
        sdef.notes = notes
        try:
            sdef.loadFrom(sprite)
        except Exception as e:
            errors.append(str(spriteid))
            errortext.append(str(e))

        Sprites[spriteid] = sdef

    sd.unlink()

    if len(errors) > 0:
        QtWidgets.QMessageBox.warning(None, 'Warning',  "The sprite data file didn't load correctly. The following sprites have incorrect and/or broken data in them, and may not be editable correctly in the editor: " + (', '.join(errors)), QtWidgets.QMessageBox.StandardButton.Ok)
        QtWidgets.QMessageBox.warning(None, 'Errors', repr(errortext))


def LoadSpriteCategories():
    """Ensures that the sprite category info is loaded"""
    global Sprites, SpriteCategories
    if SpriteCategories is not None: return

    SpriteCategories = []

    sd = minidom.parse('reggiedata/spritecategories.xml')
    root = sd.documentElement

    CurrentView = None
    for view in root.childNodes:
        if view.nodeType != view.ELEMENT_NODE: continue
        if view.nodeName != 'view': continue

        viewname = unicode(view.attributes['name'].nodeValue)
        CurrentView = []
        SpriteCategories.append((viewname, CurrentView, []))

        CurrentCategory = None
        for category in view.childNodes:
            if category.nodeType != category.ELEMENT_NODE: continue
            if category.nodeName != 'category': continue

            catname = unicode(category.attributes['name'].nodeValue)
            CurrentCategory = []
            CurrentView.append((catname, CurrentCategory))

            for attach in category.childNodes:
                if attach.nodeType != attach.ELEMENT_NODE: continue
                if attach.nodeName != 'attach': continue

                sprite = attach.attributes['sprite'].nodeValue
                if '-' not in sprite:
                    CurrentCategory.append(int(sprite))
                else:
                    x = sprite.split('-')
                    for i in range(int(x[0]), int(x[1])+1):
                        CurrentCategory.append(i)

    sd.unlink()

    SpriteCategories.append(('Search', [('Search Results', list(range(0,483)))], []))
    SpriteCategories[-1][1][0][1].append(9999) # "no results" special case


EntranceTypeNames = None
def LoadEntranceNames():
    """Ensures that the entrance names are loaded"""
    global EntranceTypeNames
    if EntranceTypeNames is not None: return

    with open('reggiedata/entrancetypes.txt', 'r') as getit:
        EntranceTypeNames = [x.strip() for x in getit.readlines()]


MusicNames = None
def LoadMusicNames():
    """Ensures that the music names are loaded"""
    global MusicNames
    if MusicNames is not None: return

    with open('reggiedata/music.txt', 'r') as getit:
        MusicNames = [x.strip() for x in getit.readlines()]



def DecodeReggieInfo(data, validKeys):
    """
    Decode the provided level info data into a dictionary, which will
    have only the keys specified. Raises an exception if the data can't
    be parsed.
    """
    # The idea here is that we implement just enough of the pickle
    # protocol (v2) to be able to parse the dictionaries that past
    # Reggies have pickled, even if PyQt4 isn't available.
    #
    # We keep track of the stack and memo, just enough to figure out
    # in what order the strings are pushed to the stack. (We need to
    # implement the memo because default level info uses memoization to
    # avoid encoding the '-' string more than once.) Then we filter out
    # 'PyQt4.QtCore' and 'QString'. Assuming nobody's crazy enough to
    # use those as actual level info field values, that should leave us
    # with exactly 12 strings (6 field names and 6 fields). Then we just
    # put the dictionary together in the same way as the SETITEMS pickle
    # instruction, and we're done.

    # Figure out in what order strings are pushed to the pickle stack
    stack = []
    memo = {}
    for inst, arg, _ in pickletools.genops(data):
        if inst.name in ['SHORT_BINSTRING', 'BINSTRING', 'BINUNICODE']:
            stack.append(arg)
        elif inst.name == 'GLOBAL':
            # In practice, this is used to push sip._unpickle_type,
            # which then gets BINGET'd over and over. So we have to take
            # it into account, or else we get confused and end up
            # pushing some random string to the stack repeatedly instead
            stack.append(None)
        elif inst.name == 'BINPUT' and stack:
            memo[arg] = stack[-1]
        elif inst.name == 'BINGET' and arg in memo:
            stack.append(memo[arg])

    # Filter out uninteresting strings and check that the length is right
    strings = [s for s in stack if s not in {'PyQt4.QtCore', 'QString', None}]
    if len(strings) != 12:
        raise ValueError('Wrong number of strings in level metadata (%d)' % len(strings))

    # Convert e.g. [a, b, c, d, e, f] -> {a: b, c: d, e: f}
    # https://stackoverflow.com/a/12739974
    it = iter(strings)
    levelinfo = dict(zip(it, it))

    # Double-check that the keys are as expected, and return
    if set(levelinfo) != validKeys:
        raise ValueError('Wrong keys in level metadata: ' + str(set(levelinfo)))

    return levelinfo


class ChooseLevelNameDialog(QtWidgets.QDialog):
    """Dialog which lets you choose a level from a list"""

    def __init__(self):
        """Creates and initialises the dialog"""
        super(ChooseLevelNameDialog, self).__init__()
        self.setWindowTitle('Choose Level')
        LoadLevelNames()
        self.currentlevel = None

        # create the tree
        tree = QtWidgets.QTreeWidget()
        tree.setColumnCount(1)
        tree.setHeaderHidden(True)
        tree.setIndentation(16)
        tree.currentItemChanged.connect(self.HandleItemChange)
        tree.itemActivated.connect(self.HandleItemActivated)

        for worldname, world in LevelNames:
            wnode = QtWidgets.QTreeWidgetItem()
            wnode.setText(0, worldname)

            for levelname, level in world:
                lnode = QtWidgets.QTreeWidgetItem()
                lnode.setText(0, levelname)
                lnode.setData(0, QtCore.Qt.ItemDataRole.UserRole, level)
                lnode.setToolTip(0, level + '.arc')
                wnode.addChild(lnode)

            tree.addTopLevelItem(wnode)

        self.leveltree = tree

        # create the buttons
        self.buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.buttonBox.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        # create the layout
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.leveltree)
        layout.addWidget(self.buttonBox)

        self.setLayout(layout)
        self.layout = layout

    @QtCoreSlot(QtWidgets.QTreeWidgetItem, QtWidgets.QTreeWidgetItem)
    def HandleItemChange(self, current, previous):
        """Catch the selected level and enable/disable OK button as needed"""
        self.currentlevel = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if self.currentlevel is None:
            self.buttonBox.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        else:
            self.buttonBox.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(True)
            self.currentlevel = unicode(qm(self.currentlevel))


    @QtCoreSlot(QtWidgets.QTreeWidgetItem, int)
    def HandleItemActivated(self, item, column):
        """Handle a doubleclick on a level"""
        self.currentlevel = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if self.currentlevel is not None:
            self.currentlevel = unicode(qm(self.currentlevel))
            self.accept()


Tiles = None # 256 tiles per tileset, plus 64 for each type of override
Overrides = None # 320 tiles, this is put into Tiles usually
TileBehaviours = None
ObjectDefinitions = None # 4 tilesets

class ObjectDef():
    """Class for the object definitions"""

    def __init__(self):
        """Constructor"""
        self.width = 0
        self.height = 0
        self.rows = []

    def load(self, source, offset, tileoffset):
        """Load an object definition"""
        i = offset
        row = []

        while True:
            cbyte = ord(source[i])

            if cbyte == 0xFE:
                self.rows.append(row)
                i += 1
                row = []
            elif cbyte == 0xFF:
                return
            elif (cbyte & 0x80) != 0:
                row.append((cbyte,))
                i += 1
            else:
                extra = ord(source[i+2])
                tile = (cbyte, ord(source[i+1]) | ((extra & 3) << 8), extra >> 2)
                row.append(tile)
                i += 3


def RenderObject(tileset, objnum, width, height, fullslope=False):
    """Render a tileset object into an array"""
    # allocate an array
    errorDest = []
    for i in range(height): errorDest.append([None]*width)
    dest = []
    for i in range(height): dest.append([0]*width)

    # ignore non-existent objects
    tileset_defs = ObjectDefinitions[tileset]
    if tileset_defs is None: return errorDest
    obj = tileset_defs[objnum]
    if obj is None: return errorDest
    if len(obj.rows) == 0: return errorDest

    # diagonal objects are rendered differently
    if (obj.rows[0][0][0] & 0x80) != 0:
        RenderDiagonalObject(dest, obj, width, height, fullslope)
    else:
        # standard object
        repeatFound = False
        beforeRepeat = []
        inRepeat = []
        afterRepeat = []

        for row in obj.rows:
            if len(row) == 0: continue
            if (row[0][0] & 2) != 0:
                repeatFound = True
                inRepeat.append(row)
            else:
                if repeatFound:
                    afterRepeat.append(row)
                else:
                    beforeRepeat.append(row)

        bc = len(beforeRepeat); ic = len(inRepeat); ac = len(afterRepeat)
        if ic == 0:
            for y in range(height):
                RenderStandardRow(dest[y], beforeRepeat[y % bc], y, width)
        else:
            afterthreshold = height - ac - 1
            for y in range(height):
                if y < bc:
                    RenderStandardRow(dest[y], beforeRepeat[y], y, width)
                elif y > afterthreshold:
                    RenderStandardRow(dest[y], afterRepeat[y - height + ac], y, width)
                else:
                    RenderStandardRow(dest[y], inRepeat[(y - bc) % ic], y, width)

    if TilesetSlotsModEnabled:
        for row in dest:
            for i, tile in enumerate(row):
                if 0 < tile < 1024:
                    row[i] = (tileset * 256) + (tile % 256)

    return dest


def RenderStandardRow(dest, row, y, width):
    """Render a row from an object"""
    repeatFound = False
    beforeRepeat = []
    inRepeat = []
    afterRepeat = []

    for tile in row:
        if (tile[0] & 1) != 0:
            repeatFound = True
            inRepeat.append(tile)
        else:
            if repeatFound:
                afterRepeat.append(tile)
            else:
                beforeRepeat.append(tile)

    bc = len(beforeRepeat); ic = len(inRepeat); ac = len(afterRepeat)
    if ic == 0:
        for x in range(width):
            dest[x] = beforeRepeat[x % bc][1]
    else:
        afterthreshold = width - ac - 1
        for x in range(width):
            if x < bc:
                dest[x] = beforeRepeat[x][1]
            elif x > afterthreshold:
                dest[x] = afterRepeat[x - width + ac][1]
            else:
                dest[x] = inRepeat[(x - bc) % ic][1]


def RenderDiagonalObject(dest, obj, width, height, fullslope):
    """Render a diagonal object"""
    # set all to empty tiles
    for row in dest:
        for x in range(width):
            row[x] = -1

    # get sections
    mainBlock,subBlock = GetSlopeSections(obj)
    cbyte = obj.rows[0][0][0]

    # get direction
    goLeft = ((cbyte & 1) != 0)
    goDown = ((cbyte & 2) != 0)

    # base the amount to draw by seeing how much we can fit in each direction
    if fullslope:
        drawAmount = max(height // len(mainBlock), width // len(mainBlock[0]))
    else:
        drawAmount = min(height // len(mainBlock), width // len(mainBlock[0]))

    # if it's not goingLeft and not goingDown:
    if not goLeft and not goDown:
        # slope going from SW => NE
        # start off at the bottom left
        x = 0
        y = height - len(mainBlock) - (0 if subBlock is None else len(subBlock))
        xi = len(mainBlock[0])
        yi = -len(mainBlock)

    # ... and if it's goingLeft and not goingDown:
    elif goLeft and not goDown:
        # slope going from SE => NW
        # start off at the top left
        x = 0
        y = 0
        xi = len(mainBlock[0])
        yi = len(mainBlock)

    # ... and if it's not goingLeft but it's goingDown:
    elif not goLeft and goDown:
        # slope going from NW => SE
        # start off at the top left
        x = 0
        y = (0 if subBlock is None else len(subBlock))
        xi = len(mainBlock[0])
        yi = len(mainBlock)

    # ... and finally, if it's goingLeft and goingDown:
    else:
        # slope going from SW => NE
        # start off at the bottom left
        x = 0
        y = height - len(mainBlock)
        xi = len(mainBlock[0])
        yi = -len(mainBlock)


    # finally draw it
    for i in range(drawAmount):
        PutObjectArray(dest, x, y, mainBlock, width, height)
        if subBlock is not None:
            xb = x
            if goLeft: xb = x + len(mainBlock[0]) - len(subBlock[0])
            if goDown:
                PutObjectArray(dest, xb, y - len(subBlock), subBlock, width, height)
            else:
                PutObjectArray(dest, xb, y + len(mainBlock), subBlock, width, height)
        x += xi
        y += yi


def PutObjectArray(dest, xo, yo, block, width, height):
    """Places a tile array into an object"""
    #for y in range(yo,min(yo+len(block),height)):
    for y in range(yo,yo+len(block)):
        if y < 0: continue
        if y >= height: continue
        drow = dest[y]
        srow = block[y-yo]
        #for x in range(xo,min(xo+len(srow),width)):
        for x in range(xo,xo+len(srow)):
            if x < 0: continue
            if x >= width: continue
            drow[x] = srow[x-xo][1]


def GetSlopeSections(obj):
    """Sorts the slope data into sections"""
    sections = []
    currentSection = None

    for row in obj.rows:
        if len(row) > 0 and (row[0][0] & 0x80) != 0: # begin new section
            if currentSection is not None:
                sections.append(CreateSection(currentSection))
            currentSection = []
        currentSection.append(row)

    if currentSection is not None: # end last section
        sections.append(CreateSection(currentSection))

    if len(sections) == 1:
        return (sections[0],None)
    else:
        return (sections[0],sections[1])


def CreateSection(rows):
    """Create a slope section"""
    # calculate width
    width = 0
    for row in rows:
        thiswidth = CountTiles(row)
        if width < thiswidth: width = thiswidth

    # create the section
    section = []
    for row in rows:
        drow = [0] * width
        x = 0
        for tile in row:
            if (tile[0] & 0x80) == 0:
                drow[x] = tile
                x += 1
        section.append(drow)

    return section


def CountTiles(row):
    """Counts the amount of real tiles in an object row"""
    res = 0
    for tile in row:
        if (tile[0] & 0x80) == 0:
            res += 1
    return res


def CreateTilesets():
    """Blank out the tileset arrays"""
    global Tiles, TileBehaviours, ObjectDefinitions

    Tiles = [None]*1024
    Tiles += Overrides
    #TileBehaviours = [0]*1024
    ObjectDefinitions = [None]*4
    sprites.Tiles = Tiles


def LoadTileset(idx, name):
    try:
        return _LoadTileset(idx, name)
    except:
        QtWidgets.QMessageBox.warning(None, 'Error',  'An error occurred while trying to load %s.arc. Check your Texture or Tilesets folder to make sure it is complete and not corrupted. The editor may run in a broken state or crash after this.' % name)
        return False


def _LoadTileset(idx, name):
    """Load in a tileset into a specific slot"""
    # read the archive
    arcname = os.path.join(gamePath, 'Texture', name+'.arc')

    if not os.path.isfile(arcname):
        arcname = os.path.join(gamePath, '../Tilesets', name+'.arc')

    if not os.path.isfile(arcname):
        QtWidgets.QMessageBox.warning(None, 'Error',  'Cannot find the required tileset file %s.arc for this level. Check your Texture or Tilesets folder and make sure it contains the required file.' % name)
        return False

    with open(arcname, 'rb') as arcf:
        arcdata = arcf.read()

    arc = archive.U8.load(arcdata)

    # decompress the textures
    try:
        comptiledata = arc['BG_tex/%s_tex.bin.LZ' % name]
    except KeyError:
        QtWidgets.QMessageBox.warning(None, 'Error',  'Cannot find the required texture within the tileset file %s.arc, so it will not be loaded. Keep in mind that the tileset file cannot be renamed without changing the names of the texture/object files within the archive as well!' % name)
        return False

    # load in the textures - uses a different method if nsmblib exists
    if HaveNSMBLib:
        tiledata = nsmblib.decompress11LZS(comptiledata)
        if hasattr(nsmblib, 'decodeTilesetNoAlpha') and not EnableAlpha:
            rgbdata = nsmblib.decodeTilesetNoAlpha(tiledata)
        else:
            rgbdata = nsmblib.decodeTileset(tiledata)
        img = QtGui.QImage(rgbdata, 1024, 256, 4096, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    else:
        lz = lz77.LZS11()
        img = LoadTextureUsingOldMethod(lz.Decompress11LZS(comptiledata))

    # crop the tiles out
    dest = QtGui.QPixmap.fromImage(img)

    sourcex = 4
    sourcey = 4
    tileoffset = idx*256
    for i in range(tileoffset,tileoffset+256):
        Tiles[i] = dest.copy(sourcex,sourcey,24,24)
        sourcex += 32
        if sourcex >= 1024:
            sourcex = 4
            sourcey += 32

    # tile behaviours aren't needed yet?

    # load the object definitions
    defs = [None]*256

    indexfile = arc['BG_unt/%s_hd.bin' % name]
    deffile = arc['BG_unt/%s.bin' % name]
    objcount = len(indexfile) // 4
    indexstruct = struct.Struct('>HBB')

    for i in range(objcount):
        data = indexstruct.unpack_from(indexfile, i << 2)
        obj = ObjectDef()
        obj.width = data[1]
        obj.height = data[2]
        obj.load(deffile,data[0],tileoffset)
        defs[i] = obj

    ObjectDefinitions[idx] = defs

    ProcessOverrides(idx, name)


RGB4A3LUT = []
RGB4A3LUT_NoAlpha = []
def PrepareRGB4A3LUTs():
    global RGB4A3LUT, RGB4A3LUT_NoAlpha

    RGB4A3LUT = [None] * 0x10000
    RGB4A3LUT_NoAlpha = [None] * 0x10000
    for LUT, hasA in [(RGB4A3LUT, True), (RGB4A3LUT_NoAlpha, False)]:

        # RGB4A3
        for d in range(0x8000):
            if hasA:
                alpha = d >> 12
                alpha = alpha << 5 | alpha << 2 | alpha >> 1
            else:
                alpha = 0xFF
            red = ((d >> 8) & 0xF) * 17
            green = ((d >> 4) & 0xF) * 17
            blue = (d & 0xF) * 17
            LUT[d] = blue | (green << 8) | (red << 16) | (alpha << 24)

        # RGB555
        for d in range(0x8000):
            red = d >> 10
            red = red << 3 | red >> 2
            green = (d >> 5) & 0x1F
            green = green << 3 | green >> 2
            blue = d & 0x1F
            blue = blue << 3 | blue >> 2
            LUT[d + 0x8000] = blue | (green << 8) | (red << 16) | 0xFF000000

PrepareRGB4A3LUTs()


def LoadTextureUsingOldMethod(tiledata):
    tx = 0; ty = 0
    iter = tiledata.__iter__()
    dest = [0] * 262144

    LUT = RGB4A3LUT if EnableAlpha else RGB4A3LUT_NoAlpha

    # Loop over all texels (of which there are 16384)
    for i in range(16384):
        temp1 = (i // 256) % 8
        if temp1 == 0 or temp1 == 7:
            # Skip every row of texels that is a multiple of 8 or (a
            # multiple of 8) - 1
            # Unrolled loop for performance.
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
            next(iter); next(iter); next(iter); next(iter)
        else:
            temp2 = i % 8
            if temp2 == 0 or temp2 == 7:
                # Skip every column of texels that is a multiple of 8
                # or (a multiple of 8) - 1
                # Unrolled loop for performance.
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
                next(iter); next(iter); next(iter); next(iter)
            else:
                # Actually render this texel
                for y in range(ty, ty+4):
                    for x in range(tx, tx+4):
                        dest[x + y * 1024] = LUT[next(iter) << 8 | next(iter)]

        # Move on to the next texel
        tx += 4
        if tx >= 1024: tx = 0; ty += 4

    # Convert the list of ARGB color values into a bytes object, and
    # then convert that into a QImage
    return QtGui.QImage(struct.pack('<262144I', *dest), 1024, 256, QtGui.QImage.Format.Format_ARGB32)


def UnloadTileset(idx):
    """Unload the tileset from a specific slot"""
    for i in range(idx*256, idx*256+256):
        Tiles[i] = None

    ObjectDefinitions[idx] = None


def ProcessOverrides(idx, name):
    """Load overridden tiles if there are any"""

    try:
        tsindexes = ['Pa0_jyotyu', 'Pa0_jyotyu_chika', 'Pa0_jyotyu_setsugen', 'Pa0_jyotyu_yougan', 'Pa0_jyotyu_staffRoll']
        if name in tsindexes:
            offset = 1024 + tsindexes.index(name) * 64
            # Setsugen/Snow is unused for some reason? but we still override it
            # StaffRoll is the same as plain Jyotyu, so if it's used, let's be lazy and treat it as the normal one
            if offset == 1280: offset = 1024

            defs = ObjectDefinitions[idx]
            t = Tiles

            # Invisible blocks
            # these are all the same so let's just load them from the first row
            replace = 1024
            for i in [3,4,5,6,7,8,9,10,13]:
                t[i] = t[replace]
                replace += 1

            # Question and brick blocks
            # these don't have their own tiles so we have to do them by objects
            replace = offset + 9
            for i in range(38, 49):
                defs[i].rows[0][0] = (0, replace, 0)
                replace += 1
            for i in range(26, 38):
                defs[i].rows[0][0] = (0, replace, 0)
                replace += 1

            # now the extra stuff (invisible collisions etc)
            t[1] = t[1280] # solid
            t[2] = t[1311] # vine stopper
            t[11] = t[1310] # jumpthrough platform
            t[12] = t[1309] # 16x8 roof platform

            t[16] = t[1291] # 1x1 slope going up
            t[17] = t[1292] # 1x1 slope going down
            t[18] = t[1281] # 2x1 slope going up (part 1)
            t[19] = t[1282] # 2x1 slope going up (part 2)
            t[20] = t[1283] # 2x1 slope going down (part 1)
            t[21] = t[1284] # 2x1 slope going down (part 2)
            t[22] = t[1301] # 4x1 slope going up (part 1)
            t[23] = t[1302] # 4x1 slope going up (part 2)
            t[24] = t[1303] # 4x1 slope going up (part 3)
            t[25] = t[1304] # 4x1 slope going up (part 4)
            t[26] = t[1305] # 4x1 slope going down (part 1)
            t[27] = t[1306] # 4x1 slope going down (part 2)
            t[28] = t[1307] # 4x1 slope going down (part 3)
            t[29] = t[1308] # 4x1 slope going down (part 4)
            t[30] = t[1062] # coin

            t[32] = t[1289] # 1x1 roof going down
            t[33] = t[1290] # 1x1 roof going up
            t[34] = t[1285] # 2x1 roof going down (part 1)
            t[35] = t[1286] # 2x1 roof going down (part 2)
            t[36] = t[1287] # 2x1 roof going up (part 1)
            t[37] = t[1288] # 2x1 roof going up (part 2)
            t[38] = t[1293] # 4x1 roof going down (part 1)
            t[39] = t[1294] # 4x1 roof going down (part 2)
            t[40] = t[1295] # 4x1 roof going down (part 3)
            t[41] = t[1296] # 4x1 roof going down (part 4)
            t[42] = t[1297] # 4x1 roof going up (part 1)
            t[43] = t[1298] # 4x1 roof going up (part 2)
            t[44] = t[1299] # 4x1 roof going up (part 3)
            t[45] = t[1300] # 4x1 roof going up (part 4)
            t[46] = t[1312] # P-switch coins

            t[53] = t[1314] # donut lift
            t[61] = t[1063] # multiplayer coin
            t[63] = t[1313] # instant death tile

        elif name == 'Pa1_nohara' or name == 'Pa1_nohara2' or name == 'Pa1_daishizen':
            # flowers
            t = Tiles
            t[416] = t[1092] # grass
            t[417] = t[1093]
            t[418] = t[1094]
            t[419] = t[1095]
            t[420] = t[1096]

            if name == 'Pa1_nohara' or name == 'Pa1_nohara2':
                t[432] = t[1068] # flowers
                t[433] = t[1069] # flowers
                t[434] = t[1070] # flowers

                t[448] = t[1158] # flowers on grass
                t[449] = t[1159]
                t[450] = t[1160]
            elif name == 'Pa1_daishizen':
                # forest flowers
                t[432] = t[1071] # flowers
                t[433] = t[1072] # flowers
                t[434] = t[1073] # flowers

                t[448] = t[1222] # flowers on grass
                t[449] = t[1223]
                t[450] = t[1224]

        elif name == 'Pa3_rail' or name == 'Pa3_rail_white' or name == 'Pa3_daishizen':
            # These are the line guides
            # Pa3_daishizen has less though

            t = Tiles

            t[768] = t[1088] # horizontal line
            t[769] = t[1089] # vertical line
            t[770] = t[1090] # bottomright corner
            t[771] = t[1091] # topleft corner

            t[784] = t[1152] # left red blob (part 1)
            t[785] = t[1153] # top red blob (part 1)
            t[786] = t[1154] # top red blob (part 2)
            t[787] = t[1155] # right red blob (part 1)
            t[788] = t[1156] # topleft red blob
            t[789] = t[1157] # topright red blob

            t[800] = t[1216] # left red blob (part 2)
            t[801] = t[1217] # bottom red blob (part 1)
            t[802] = t[1218] # bottom red blob (part 2)
            t[803] = t[1219] # right red blob (part 2)
            t[804] = t[1220] # bottomleft red blob
            t[805] = t[1221] # bottomright red blob

            # Those are all for Pa3_daishizen
            if name == 'Pa3_daishizen': return

            t[816] = t[1056] # 1x2 diagonal going up (top edge)
            t[817] = t[1057] # 1x2 diagonal going down (top edge)

            t[832] = t[1120] # 1x2 diagonal going up (part 1)
            t[833] = t[1121] # 1x2 diagonal going down (part 1)
            t[834] = t[1186] # 1x1 diagonal going up
            t[835] = t[1187] # 1x1 diagonal going down
            t[836] = t[1058] # 2x1 diagonal going up (part 1)
            t[837] = t[1059] # 2x1 diagonal going up (part 2)
            t[838] = t[1060] # 2x1 diagonal going down (part 1)
            t[839] = t[1061] # 2x1 diagonal going down (part 2)

            t[848] = t[1184] # 1x2 diagonal going up (part 2)
            t[849] = t[1185] # 1x2 diagonal going down (part 2)
            t[850] = t[1250] # 1x1 diagonal going up
            t[851] = t[1251] # 1x1 diagonal going down
            t[852] = t[1122] # 2x1 diagonal going up (part 1)
            t[853] = t[1123] # 2x1 diagonal going up (part 2)
            t[854] = t[1124] # 2x1 diagonal going down (part 1)
            t[855] = t[1125] # 2x1 diagonal going down (part 2)

            t[866] = t[1065] # big circle piece 1st row
            t[867] = t[1066] # big circle piece 1st row
            t[870] = t[1189] # medium circle piece 1st row
            t[871] = t[1190] # medium circle piece 1st row

            t[881] = t[1128] # big circle piece 2nd row
            t[882] = t[1129] # big circle piece 2nd row
            t[883] = t[1130] # big circle piece 2nd row
            t[884] = t[1131] # big circle piece 2nd row
            t[885] = t[1252] # medium circle piece 2nd row
            t[886] = t[1253] # medium circle piece 2nd row
            t[887] = t[1254] # medium circle piece 2nd row
            t[888] = t[1188] # small circle

            t[896] = t[1191] # big circle piece 3rd row
            t[897] = t[1192] # big circle piece 3rd row
            t[900] = t[1195] # big circle piece 3rd row
            t[901] = t[1316] # medium circle piece 3rd row
            t[902] = t[1317] # medium circle piece 3rd row
            t[903] = t[1318] # medium circle piece 3rd row

            t[912] = t[1255] # big circle piece 4th row
            t[913] = t[1256] # big circle piece 4th row
            t[916] = t[1259] # big circle piece 4th row

            t[929] = t[1320] # big circle piece 5th row
            t[930] = t[1321] # big circle piece 5th row
            t[931] = t[1322] # big circle piece 5th row
            t[932] = t[1323] # big circle piece 5th row

        elif name == 'Pa3_MG_house_ami_rail':
            t = Tiles

            t[832] = t[1088] # horizontal line
            t[833] = t[1090] # bottomright corner
            t[834] = t[1088] # horizontal line

            t[848] = t[1089] # vertical line
            t[849] = t[1089] # vertical line
            t[850] = t[1091] # topleft corner

            t[835] = t[1152] # left red blob (part 1)
            t[836] = t[1153] # top red blob (part 1)
            t[837] = t[1154] # top red blob (part 2)
            t[838] = t[1155] # right red blob (part 1)

            t[851] = t[1216] # left red blob (part 2)
            t[852] = t[1217] # bottom red blob (part 1)
            t[853] = t[1218] # bottom red blob (part 2)
            t[854] = t[1219] # right red blob (part 2)

            t[866] = t[1065] # big circle piece 1st row
            t[867] = t[1066] # big circle piece 1st row
            t[870] = t[1189] # medium circle piece 1st row
            t[871] = t[1190] # medium circle piece 1st row

            t[881] = t[1128] # big circle piece 2nd row
            t[882] = t[1129] # big circle piece 2nd row
            t[883] = t[1130] # big circle piece 2nd row
            t[884] = t[1131] # big circle piece 2nd row
            t[885] = t[1252] # medium circle piece 2nd row
            t[886] = t[1253] # medium circle piece 2nd row
            t[887] = t[1254] # medium circle piece 2nd row

            t[896] = t[1191] # big circle piece 3rd row
            t[897] = t[1192] # big circle piece 3rd row
            t[900] = t[1195] # big circle piece 3rd row
            t[901] = t[1316] # medium circle piece 3rd row
            t[902] = t[1317] # medium circle piece 3rd row
            t[903] = t[1318] # medium circle piece 3rd row

            t[912] = t[1255] # big circle piece 4th row
            t[913] = t[1256] # big circle piece 4th row
            t[916] = t[1259] # big circle piece 4th row

            t[929] = t[1320] # big circle piece 5th row
            t[930] = t[1321] # big circle piece 5th row
            t[931] = t[1322] # big circle piece 5th row
            t[932] = t[1323] # big circle piece 5th row
    except:
        # Fail silently
        pass


def LoadOverrides():
    """Load overrides"""
    global Overrides
    Overrides = [None]*320

    OverrideBitmap = QtGui.QPixmap('reggiedata/overrides.png')
    idx = 0
    xcount = OverrideBitmap.width() // 24
    ycount = OverrideBitmap.height() // 24
    sourcex = 0
    sourcey = 0

    for y in range(ycount):
        for x in range(xcount):
            Overrides[idx] = OverrideBitmap.copy(sourcex, sourcey, 24, 24)
            idx += 1
            sourcex += 24
        sourcex = 0
        sourcey += 24
        if idx % 64 != 0:
            idx -= (idx % 64)
            idx += 64


Level = None
Dirty = False
DirtyOverride = 0
AutoSaveDirty = False
OverrideSnapping = False
CurrentPaintType = 0
CurrentObject = -1
CurrentSprite = -1
CurrentLayer = 1
ShowLayer0 = True
ShowLayer1 = True
ShowLayer2 = True
ShowSprites = True
ShowSpriteImages = True
ShowEntrances = True
ShowLocations = True
ShowPaths = True
TilesetSlotsModEnabled = False
ObjectsNonFrozen = True
SpritesNonFrozen = True
EntrancesNonFrozen = True
LocationsNonFrozen = True
PathsNonFrozen = True
PaintingEntrance = None
PaintingEntranceListIndex = None
NumberFont = None
NumberFontBold = None
GridEnabled = False
RestoredFromAutoSave = False
AutoSavePath = ''
AutoSaveData = b''

def createHorzLine():
    f = QtWidgets.QFrame()
    f.setFrameStyle(QtWidgets.QFrame.Shape.HLine | QtWidgets.QFrame.Shadow.Sunken)
    return f

def LoadNumberFont():
    """Creates a valid font we can use to display the item numbers"""
    global NumberFont
    if NumberFont is not None: return

    # this is a really crappy method, but I can't think of any other way
    # normal Qt defines Q_WS_WIN and Q_WS_MAC but we don't have that here
    s = QtCore.QSysInfo()
    if hasattr(s, 'WindowsVersion'):
        NumberFont = QtGui.QFont('Tahoma', 7)
    elif hasattr(s, 'MacintoshVersion'):
        NumberFont = QtGui.QFont('Lucida Grande', 9)
    else:
        NumberFont = QtGui.QFont('Sans', 8)

def LoadNumberFontBold():
    """Creates a valid font we can use to display the item numbers"""
    global NumberFontBold
    if NumberFontBold is not None: return

    # this is a really crappy method, but I can't think of any other way
    # normal Qt defines Q_WS_WIN and Q_WS_MAC but we don't have that here
    s = QtCore.QSysInfo()
    if hasattr(s, 'WindowsVersion'):
        NumberFontBold = QtGui.QFont('Tahoma', 7, QtGui.QFont.Weight.Bold)
    elif hasattr(s, 'MacintoshVersion'):
        NumberFontBold = QtGui.QFont('Lucida Grande', 9)
    else:
        NumberFontBold = QtGui.QFont('Sans', 8, QtGui.QFont.Weight.Bold)

def SetDirty(noautosave=False):
    global Dirty, DirtyOverride, AutoSaveDirty
    if DirtyOverride > 0: return

    if not noautosave: AutoSaveDirty = True
    if Dirty: return

    Dirty = True
    try:
        mainWindow.UpdateTitle()
    except:
        pass


def MapPositionToZoneID(zones, x, y):
    """Returns the zone ID containing or nearest the specified position"""
    id = 0
    minimumdist = -1
    rval = -1

    for zone in zones:
        r = zone.ZoneRect
        if r.contains(x,y): return id

        xdist = 0
        ydist = 0
        if x <= r.left(): xdist = r.left() - x
        if x >= r.right(): xdist = x - r.right()
        if y <= r.top(): ydist = r.top() - y
        if y >= r.bottom(): ydist = y - r.bottom()

        dist = (xdist**2+ydist**2)**0.5
        if dist < minimumdist or minimumdist == -1:
            minimumdist = dist
            rval = zone.zoneID

        id += 1

    return rval


class LevelUnit():
    """Class for a full NSMBWii level archive"""
    def newLevel(self):
        """Creates a completely new level"""
        self.arcname = None
        self.filename = 'untitled'
        self.hasName = False
        self.isCompressed = False
        arc = archive.U8()
        arc['course'] = None
        arc['course/course1.bin'] = ''
        self.arc = arc
        self.areanum = 1
        self.areacount = 1

        # we don't parse blocks 4, 11, 12, 13, 14
        # we can create the rest manually
        self.blocks = [None]*14
        self.blocks[3] = b'\0\0\0\0\0\0\0\0'
        # other known values for block 4: 0000 0002 0042 0000,
        #            0000 0002 0002 0000, 0000 0003 0003 0000
        self.blocks[11] = '' # never used
        self.blocks[12] = '' # paths
        self.blocks[13] = '' # path points

        # prepare all data
        self.tileset0 = 'Pa0_jyotyu'
        self.tileset1 = 'Pa1_nohara'
        self.tileset2 = ''
        self.tileset3 = ''

        self.defEvents = 0
        self.wrapFlag = 0
        self.timeLimit = 300
        self.unk1 = 0
        self.startEntrance = 0
        self.unk2 = 0
        self.unk3 = 0

        self.entrances = []
        self.sprites = []
        self.zones = []
        self.locations = []
        self.camprofiles = []
        self.pathdata = []
        self.paths = []

        self.LoadReggieInfo(None)

        CreateTilesets()
        LoadTileset(0, 'Pa0_jyotyu')
        LoadTileset(1, 'Pa1_nohara')

        self.layers = [[], [], []]


    def loadLevel(self, name, area, progress=None):
        """Loads a specific level and area"""

        self.arcname = name

        if not os.path.isfile(self.arcname):
            QtWidgets.QMessageBox.warning(None, 'Error',  'Cannot find the level file %s. Check your Stage folder and make sure it exists.' % self.arcname)
            return False

        self.filename = os.path.basename(self.arcname)
        self.hasName = True

        with open(self.arcname, 'rb') as arcf:
            arcdata = arcf.read()

        return self.loadLevelData(arcdata, area, progress)


    def loadLevelFromAutosave(self, progress=None):
        """Loads auto-saved level data"""
        global AutoSavePath, AutoSaveData

        if str(AutoSavePath).lower() == 'none':
            self.arcname = None
            self.filename = 'untitled'
            self.hasName = False
        else:
            self.arcname = AutoSavePath
            self.filename = os.path.basename(self.arcname)
            self.hasName = True

        result = self.loadLevelData(AutoSaveData, 1, progress)
        SetDirty(noautosave=True)
        return result


    def loadLevelData(self, arcdata, area, progress=None):
        """Loads a specific level and area from bytes data"""
        startTime = time.time()

        self.isCompressed = arcdata.startswith(b'\x11')

        if self.isCompressed:
            # decompress the data
            if HaveNSMBLib:
                arcdata = nsmblib.decompress11LZS(arcdata)
            else:
                arcdata = lz77.LZS11().Decompress11LZS(arcdata)

        # read the archive
        self.arc = archive.U8.load(arcdata)

        # this is a hackish method but let's go through the U8 files
        reqcourse = 'course%d.bin' % area
        reql0 = 'course%d_bgdatL0.bin' % area
        reql1 = 'course%d_bgdatL1.bin' % area
        reql2 = 'course%d_bgdatL2.bin' % area

        course = None
        l0 = None
        l1 = None
        l2 = None
        self.areanum = area
        self.areacount = 0

        for item,val in self.arc.files:
            if val is not None:
                # it's a file
                fname = item[item.rfind('/')+1:]
                if fname == reqcourse:
                    course = val
                elif fname == reql0:
                    l0 = val
                elif fname == reql1:
                    l1 = val
                elif fname == reql2:
                    l2 = val

                if fname.startswith('course'):
                    maxarea = int(fname[6])
                    if maxarea > self.areacount: self.areacount = maxarea

        # load in the course file and blocks
        self.blocks = [None]*14
        getblock = struct.Struct('>II')
        for i in range(14):
            data = getblock.unpack_from(course, i*8)
            if data[1] == 0:
                self.blocks[i] = b''
            else:
                self.blocks[i] = course[data[0]:data[0]+data[1]]

        # load stuff from individual blocks
        self.LoadMetadata() # block 1
        self.LoadOptions() # block 2
        self.LoadEntrances() # block 7
        self.LoadSprites() # block 8
        self.LoadZones() # block 10 (also blocks 3, 5, and 6)
        self.LoadLocations() # block 11
        self.LoadCamProfiles() # block 12
        self.LoadPaths() # blocks 13 and 14

        # load the editor metadata
        block1pos = getblock.unpack_from(course, 0)
        if block1pos[0] != 0x70:
            rdsize = block1pos[0] - 0x70
            rddata = course[0x70:block1pos[0]]
            self.LoadReggieInfo(rddata)
        else:
            self.LoadReggieInfo(None)

        # load the tilesets
        if progress is not None: progress.setLabelText('Loading tilesets...')

        CreateTilesets()
        if progress is not None: progress.setValue(1)
        if self.tileset0 != '': LoadTileset(0, self.tileset0)
        if progress is not None: progress.setValue(2)
        if self.tileset1 != '': LoadTileset(1, self.tileset1)
        if progress is not None: progress.setValue(3)
        if self.tileset2 != '': LoadTileset(2, self.tileset2)
        if progress is not None: progress.setValue(4)
        if self.tileset3 != '': LoadTileset(3, self.tileset3)

        # load the object layers
        if progress is not None:
            progress.setLabelText('Loading layers...')
            progress.setValue(5)

        self.layers = [[],[],[]]

        if l0 is not None:
            self.LoadLayer(0,l0)

        if l1 is not None:
            self.LoadLayer(1,l1)

        if l2 is not None:
            self.LoadLayer(2,l2)

        endTime = time.time()
        total = endTime - startTime
        #print('Level loaded in %f seconds' % total)

        return True

    def save(self, compress=None):
        """Save the level back to a file"""
        # prepare this because else the game shits itself and refuses to load some sprites
        self.SortSpritesByZone()

        # save each block first
        success = True
        warnings.simplefilter('ignore') # blocks DeprecationWarnings from struct module
        self.SaveMetadata() # block 1
        self.SaveOptions() # block 2
        self.SaveEntrances() # block 7
        self.SaveSprites() # block 8
        self.SaveLoadedSprites() # block 9
        self.SaveZones() # block 10 (and 3, 5 and 6)
        self.SaveLocations() # block 11
        self.SaveCamProfiles() # block 12
        self.SavePaths()  # blocks 13 and 14
        warnings.resetwarnings()

        rdata = self.SaveReggieInfo()
        if len(rdata) % 4 != 0:
            rdata += b'\0' * (4 - (len(rdata) % 4))

        # save the main course file
        # we'll be passing over the blocks array two times
        # using ctypes.create_string_buffer here because it offers mutable strings
        # and works directly with struct.pack_into(), so it's a win-win situation for me
        FileLength = (14 * 8) + len(rdata)
        for block in self.blocks:
            FileLength += len(block)

        course = create_string_buffer(FileLength)
        saveblock = struct.Struct('>II')

        HeaderOffset = 0
        FileOffset = (14 * 8) + len(rdata)
        struct.pack_into('{0}s'.format(len(rdata)), course, 0x70, rdata)
        for block in self.blocks:
            blocksize = len(block)
            saveblock.pack_into(course, HeaderOffset, FileOffset, blocksize)
            if blocksize > 0:
                course[FileOffset:FileOffset+blocksize] = block
            HeaderOffset += 8
            FileOffset += blocksize



        # place it into the U8 archive
        arc = self.arc
        areanum = self.areanum
        arc['course/course%d.bin' % areanum] = course.raw
        arc['course/course%d_bgdatL0.bin' % areanum] = self.SaveLayer(0)
        arc['course/course%d_bgdatL1.bin' % areanum] = self.SaveLayer(1)
        arc['course/course%d_bgdatL2.bin' % areanum] = self.SaveLayer(2)

        # save the U8 archive
        arcdata = arc._dump()

        if compress is None:
            compress = self.isCompressed
        if compress:
            arcdata = lz77.LZS11().Compress11LZS(arcdata)

        return arcdata

    def LoadMetadata(self):
        """Loads block 1, the tileset names"""
        data = struct.unpack_from('32s32s32s32s', self.blocks[0])
        self.tileset0 = data[0].strip(b'\0').decode('latin-1')
        self.tileset1 = data[1].strip(b'\0').decode('latin-1')
        self.tileset2 = data[2].strip(b'\0').decode('latin-1')
        self.tileset3 = data[3].strip(b'\0').decode('latin-1')

    def LoadOptions(self):
        """Loads block 2, the general options"""
        optdata = self.blocks[1]
        optstruct = struct.Struct('>IIHhLBBBx')
        offset = 0
        data = optstruct.unpack_from(optdata,offset)
        defEventsA, defEventsB, self.wrapFlag, self.timeLimit, self.unk1, self.startEntrance, self.unk2, self.unk3 = data
        self.defEvents = defEventsA | defEventsB << 32

    def LoadEntrances(self):
        """Loads block 7, the entrances"""
        entdata = self.blocks[6]
        entcount = len(entdata) // 20
        entstruct = struct.Struct('>HHxxxxBBBBxBBBHBB')
        offset = 0
        entrances = []
        for i in range(entcount):
            data = entstruct.unpack_from(entdata,offset)
            entrances.append(EntranceEditorItem(data[0], data[1], data[2], data[3], data[4], data[5], data[6], data[7], data[8], data[9], data[10], data[11]))
            offset += 20
        self.entrances = entrances

    def LoadSprites(self):
        """Loads block 8, the sprites"""
        spritedata = self.blocks[7]
        sprcount = len(spritedata) // 16
        sprstruct = struct.Struct('>HHH8sxx')
        offset = 0
        sprites = []

        unpack = sprstruct.unpack_from
        append = sprites.append
        obj = SpriteEditorItem
        for i in range(sprcount):
            data = unpack(spritedata,offset)
            append(obj(data[0], data[1], data[2], data[3]))
            offset += 16
        self.sprites = sprites

    def LoadZones(self):
        """Loads block 3, the bounding preferences"""
        bdngdata = self.blocks[2]
        count = len(bdngdata) // 24
        bdngstruct = struct.Struct('>4lHHhh')
        offset = 0
        bounding = []
        for i in range(count):
            datab = bdngstruct.unpack_from(bdngdata,offset)
            bounding.append([datab[0], datab[1], datab[2], datab[3], datab[4], datab[5], datab[6], datab[7]])
            offset += 24
        self.bounding = bounding

        """Loads block 5, the top level background values"""
        bgAdata = self.blocks[4]
        bgAcount = len(bgAdata) // 24
        bgAstruct = struct.Struct('>xBhhhhHHHxxxBxxxx')
        offset = 0
        bgA = []
        for i in range(bgAcount):
            data = bgAstruct.unpack_from(bgAdata,offset)
            bgA.append([data[0], data[1], data[2], data[3], data[4], data[5], data[6], data[7], data[8]])
            offset += 24
        self.bgA = bgA

        """Loads block 6, the bottom level background values"""
        bgBdata = self.blocks[5]
        bgBcount = len(bgBdata) // 24
        bgBstruct = struct.Struct('>xBhhhhHHHxxxBxxxx')
        offset = 0
        bgB = []
        for i in range(bgBcount):
            datab = bgBstruct.unpack_from(bgBdata,offset)
            bgB.append([datab[0], datab[1], datab[2], datab[3], datab[4], datab[5], datab[6], datab[7], datab[8]])
            offset += 24
        self.bgB = bgB

        """Loads block 10, the zone data"""
        zonedata = self.blocks[9]
        zonestruct = struct.Struct('>HHHHHHBBBBxBBBBxBB')
        count = len(zonedata) // 24
        offset = 0
        zones = []
        for i in range(count):
            dataz = zonestruct.unpack_from(zonedata,offset)
            zones.append(ZoneItem(dataz[0], dataz[1], dataz[2], dataz[3], dataz[4], dataz[5], dataz[6], dataz[7], dataz[8], dataz[9], dataz[10], dataz[11], dataz[12], dataz[13], dataz[14], dataz[15], bounding, bgA, bgB, i))
            offset += 24
        self.zones = zones

    def LoadLocations(self):
        """Loads block 11, the locations"""
        locdata = self.blocks[10]
        locstruct = struct.Struct('>HHHHBxxx')
        count = len(locdata) // 12
        offset = 0
        locations = []
        for i in range(count):
            data = locstruct.unpack_from(locdata, offset)
            locations.append(LocationEditorItem(data[0], data[1], data[2], data[3], data[4]))
            offset += 12
        self.locations = locations

    def LoadCamProfiles(self):
        """Loads block 12, the camera profiles"""
        profiledata = self.blocks[11]
        profilestruct = struct.Struct('>xxxxxxxxxxxxBBBBxxBx')
        count = len(profiledata) // 20
        offset = 0
        camprofiles = []
        for i in range(count):
            data = profilestruct.unpack_from(profiledata, offset)
            if i > 0 or any(data):
                camprofiles.append([data[4], data[1], data[2]])
            offset += 20
        self.camprofiles = camprofiles


    def LoadLayer(self, idx, layerdata):
        """Loads a specific object layer from a string"""
        objcount = len(layerdata) // 10
        objstruct = struct.Struct('>HHHHH')
        offset = 0
        z = (2 - idx) * 8192

        layer = self.layers[idx]
        append = layer.append
        obj = LevelObjectEditorItem
        unpack = objstruct.unpack_from
        for i in range(objcount):
            data = unpack(layerdata,offset)
            append(obj(data[0] >> 12, data[0] & 4095, idx, data[1], data[2], data[3], data[4], z))
            z += 1
            offset += 10

    def LoadPaths(self):
        # Path struct: >BxHHH
        # PathNode struct: >HHffhxx
        #[20:28:38]  [@Treeki] struct Path { unsigned char id; char padding; unsigned short startNodeIndex; unsigned short nodeCount; unsigned short unknown; };
        #[20:29:04]  [@Treeki] struct PathNode { unsigned short x; unsigned short y; float speed; float unknownMaybeAccel; short unknown; char padding[2]; }
        # path block 12, node block 13

        # TODO: Render path, and everything above that
        """Loads paths"""
        pathdata = self.blocks[12]
        pathcount = len(pathdata) // 8
        pathstruct = struct.Struct('>BxHHH')
        offset = 0
        unpack = pathstruct.unpack_from
        pathinfo = []
        paths = []
        for i in range(pathcount):
            data = unpack(pathdata, offset)
            nodes = self.LoadPathNodes(data[1], data[2])
            add2p = {'id': int(data[0]),
                     'nodes': [],
                     'loops': data[3] == 2
                     }
            for node in nodes:
                add2p['nodes'].append(node)
            pathinfo.append(add2p)


            offset += 8

        for i in range(pathcount):
            xpi = pathinfo[i]
            for j in range(len(xpi['nodes'])):
                xpj = xpi['nodes'][j]
                nobjx = None if ((j+1) == len(xpi['nodes'])) else xpi['nodes'][j+1]['x']
                nobjy = None if ((j+1) == len(xpi['nodes'])) else xpi['nodes'][j+1]['y']
                paths.append(PathEditorItem(xpj['x'], xpj['y'], nobjx, nobjy, xpi, xpj))


        self.pathdata = pathinfo
        self.paths = paths


    def LoadPathNodes(self, startindex, count):
        ret = []
        nodedata = self.blocks[13]
        nodestruct = struct.Struct('>HHffhxx')
        offset = startindex*16
        unpack = nodestruct.unpack_from
        for i in range(count):
            data = unpack(nodedata, offset)
            ret.append({'x':int(data[0]),
                        'y':int(data[1]),
                        'speed':float(data[2]),
                        'accel':float(data[3]),
                        'delay':int(data[4])
                        #'id':i
            })
            offset += 16
        return ret


    def SaveMetadata(self):
        """Saves the tileset names back to block 1"""
        self.blocks[0] = b''.join([self.tileset0.encode('latin-1').ljust(32, b'\0'),
                                   self.tileset1.encode('latin-1').ljust(32, b'\0'),
                                   self.tileset2.encode('latin-1').ljust(32, b'\0'),
                                   self.tileset3.encode('latin-1').ljust(32, b'\0')])

    def SaveOptions(self):
        """Saves block 2, the general options"""
        optstruct = struct.Struct('>IIHhLBBBx')
        buffer = create_string_buffer(20)
        optstruct.pack_into(buffer, 0, self.defEvents & 0xFFFFFFFF, self.defEvents >> 32, self.wrapFlag, self.timeLimit, self.unk1, self.startEntrance, self.unk2, self.unk3)
        self.blocks[1] = buffer.raw

    def SaveLayer(self, idx):
        """Saves an object layer to a string"""
        layer = self.layers[idx]
        offset = 0
        objstruct = struct.Struct('>HHHHH')
        buffer = create_string_buffer((len(layer) * 10) + 2)
        f_int = int
        for obj in layer:
            objstruct.pack_into(buffer, offset, f_int((obj.tileset << 12) | obj.type), f_int(obj.objx), f_int(obj.objy), f_int(obj.width), f_int(obj.height))
            offset += 10
        buffer[offset] = b'\xff'
        buffer[offset+1] = b'\xff'
        return buffer.raw

    def SaveEntrances(self):
        """Saves the entrances back to block 7"""
        offset = 0
        entstruct = struct.Struct('>HHxxxxBBBBxBBBHBB')
        buffer = create_string_buffer(len(self.entrances) * 20)
        zonelist = self.zones
        for entrance in self.entrances:
            zoneID = MapPositionToZoneID(zonelist, entrance.objx, entrance.objy)
            if zoneID < 0:
                # This can happen if the level has no zones
                zoneID = 0
            entstruct.pack_into(buffer, offset, int(entrance.objx), int(entrance.objy), int(entrance.entid), int(entrance.destarea), int(entrance.destentrance), int(entrance.enttype), zoneID, int(entrance.entlayer), int(entrance.entpath), int(entrance.entsettings), int(entrance.exittomap), int(entrance.cpdirection))
            offset += 20
        self.blocks[6] = buffer.raw

    def SavePaths(self):
        """Saves the paths back to block 13"""
        pathstruct = struct.Struct('>BxHHH')
        nodecount = 0
        for path in self.pathdata:
            nodecount += len(path['nodes'])
        nodebuffer = create_string_buffer(nodecount * 16)
        nodeoffset = 0
        nodeindex = 0
        offset = 0
        buffer = create_string_buffer(len(self.pathdata) * 8)
        #[20:28:38]  [@Treeki] struct Path { unsigned char id; char padding; unsigned short startNodeIndex; unsigned short nodeCount; unsigned short unknown; };
        for path in self.pathdata:
            if len(path['nodes']) < 1: continue
            nodebuffer = self.SavePathNodes(nodebuffer, nodeoffset, path['nodes'])

            pathstruct.pack_into(buffer, offset, int(path['id']), int(nodeindex), int(len(path['nodes'])), 2 if path['loops'] else 0)
            offset += 8
            nodeoffset += len(path['nodes']) * 16
            nodeindex += len(path['nodes'])
        self.blocks[12] = buffer.raw
        self.blocks[13] = nodebuffer.raw

    def SavePathNodes(self, buffer, offst, nodes):
        """Saves the pathnodes back to block 14"""
        offset = int(offst)
        #[20:29:04]  [@Treeki] struct PathNode { unsigned short x; unsigned short y; float speed; float unknownMaybeAccel; short unknown; char padding[2]; }
        nodestruct = struct.Struct('>HHffhxx')
        for node in nodes:
            nodestruct.pack_into(buffer, offset, int(node['x']), int(node['y']), float(node['speed']), float(node['accel']), int(node['delay']))
            offset += 16
        return buffer

    def SaveSprites(self):
        """Saves the sprites back to block 8"""
        offset = 0
        sprstruct = struct.Struct('>HHH6sBBxx')
        buffer = create_string_buffer((len(self.sprites) * 16) + 4)
        f_int = int
        for sprite in self.sprites:
            sprstruct.pack_into(buffer, offset, f_int(sprite.type), f_int(sprite.objx), f_int(sprite.objy), sprite.spritedata[:6], sprite.zoneID, ord(sprite.spritedata[7]))
            offset += 16
        buffer[offset] = b'\xff'
        buffer[offset+1] = b'\xff'
        buffer[offset+2] = b'\xff'
        buffer[offset+3] = b'\xff'
        self.blocks[7] = buffer.raw

    def SaveLoadedSprites(self):
        """Saves the list of loaded sprites back to block 9"""
        ls = []
        for sprite in self.sprites:
            if sprite.type not in ls: ls.append(sprite.type)
        ls.sort()

        offset = 0
        sprstruct = struct.Struct('>Hxx')
        buffer = create_string_buffer(len(ls) * 4)
        for s in ls:
            sprstruct.pack_into(buffer, offset, int(s))
            offset += 4
        self.blocks[8] = buffer.raw


    def SaveZones(self):
        """Saves blocks 10, 3, 5 and 6, the zone data, boundings, bgA and bgB data respectively"""
        bdngstruct = struct.Struct('>4lHHhh')
        bgAstruct = struct.Struct('>xBhhhhHHHxxxBxxxx')
        bgBstruct = struct.Struct('>xBhhhhHHHxxxBxxxx')
        zonestruct = struct.Struct('>HHHHHHBBBBxBBBBxBB')
        offset = 0
        i = 0
        zcount = len(Level.zones)
        buffer2 = create_string_buffer(24*zcount)
        buffer4 = create_string_buffer(24*zcount)
        buffer5 = create_string_buffer(24*zcount)
        buffer9 = create_string_buffer(24*zcount)
        for z in Level.zones:
            if z.objx < 0: z.objx = 0
            if z.objy < 0: z.objy = 0
            bdngstruct.pack_into(buffer2, offset, z.yupperbound, z.ylowerbound, z.yupperbound2, z.ylowerbound2, i, z.mpcamzoomadjust, z.yupperbound3, z.ylowerbound3)
            bgAstruct.pack_into(buffer4, offset, i, z.XscrollA, z.YscrollA, z.YpositionA, z.XpositionA, z.bg1A, z.bg2A, z.bg3A, z.ZoomA)
            bgBstruct.pack_into(buffer5, offset, i, z.XscrollB, z.YscrollB, z.YpositionB, z.XpositionB, z.bg1B, z.bg2B, z.bg3B, z.ZoomB)
            zonestruct.pack_into(buffer9, offset, z.objx, z.objy, z.width, z.height, z.modeldark, z.terraindark, i, i, z.cammode, z.camzoom, z.visibility, i, i, z.direction, z.music, z.sfxmod)
            offset += 24
            i += 1

        self.blocks[2] = buffer2.raw
        self.blocks[4] = buffer4.raw
        self.blocks[5] = buffer5.raw
        self.blocks[9] = buffer9.raw


    def SaveLocations(self):
        """Saves block 11, the location data"""
        locstruct = struct.Struct('>HHHHBxxx')
        offset = 0
        zcount = len(Level.locations)
        buffer = create_string_buffer(12*zcount)

        for z in Level.locations:
            locstruct.pack_into(buffer, offset, int(z.objx), int(z.objy), int(z.width), int(z.height), int(z.id))
            offset += 12

        self.blocks[10] = buffer.raw


    def SaveCamProfiles(self):
        """Saves block 12, the camera profiles data"""
        if not Level.camprofiles:
            self.blocks[11] = b''
            return

        # Camera profiles include a bounding-block ID, but the game only
        # uses it for one frame before reverting to the zone defaults.
        # So it's not really useful for anything, but we also need to
        # ensure we don't point the camera profiles to any invalid or
        # otherwise terrible bounding settings for that one frame.
        # So we make an extra, all-defaults bounding block and use it
        # for every camera profile.

        # We also make an empty (all 00s) first profile to work around a
        # game bug: the game initially thinks that the first profile is
        # active (rather than "no profile"), and thus will refuse to
        # activate it until you switch to some other profile first. So
        # we just make a dummy first profile to avoid triggering this
        # confusing behavior. (It can never be activated because it's
        # tied to "event 0," which doesn't exist.)

        profilestruct = struct.Struct('>xxxxxxxxxxxxBBBBxxBx')
        bdngstruct = struct.Struct('>4lHHhh')

        buffer = create_string_buffer(20 * (len(Level.camprofiles) + 1))
        buffer2 = create_string_buffer(len(self.blocks[2]) + 24)
        buffer2[:len(self.blocks[2])] = self.blocks[2]

        offset2 = len(self.blocks[2])
        bdngid = offset2 // 20

        offset = 20  # empty first profile to work around game bug
        for p in Level.camprofiles:
            profilestruct.pack_into(buffer, offset, bdngid, p[1], p[2], 0, p[0])
            offset += 20

        bdngstruct.pack_into(buffer2, offset2, 0, 0, 0, 0, bdngid, 15, 0, 0)

        self.blocks[11] = buffer.raw
        self.blocks[2] = buffer2.raw


    def RemoveFromLayer(self, obj):
        """Removes a specific object from the level and updates Z indexes accordingly"""
        layer = self.layers[obj.layer]
        idx = layer.index(obj)
        del layer[idx]
        for i in range(idx,len(layer)):
            upd = layer[i]
            upd.setZValue(upd.zValue() - 1)

    def SortSpritesByZone(self):
        """Sorts the sprite list by zone ID so it will work in-game"""

        split = {}
        zones = []

        f_MapPositionToZoneID = MapPositionToZoneID
        zonelist = self.zones

        for sprite in self.sprites:
            zone = f_MapPositionToZoneID(zonelist, sprite.objx, sprite.objy)
            if zone < 0:
                # This can happen if the level has no zones
                zone = 0
            sprite.zoneID = zone
            if zone not in split:
                split[zone] = []
                zones.append(zone)
            split[zone].append(sprite)

        newlist = []
        zones.sort()
        for z in zones:
            newlist += split[z]

        self.sprites = newlist


    def LoadReggieInfo(self, data):
        info = {
            'Creator': '(unknown)',
            'Title': '-',
            'Author': '-',
            'Group': '-',
            'Webpage': '-',
            'Password': ''
        }

        if data is not None:
            try:
                info = DecodeReggieInfo(data, set(info.keys()))
            except Exception:
                pass

        for k,v in info.items():
            self.__dict__[k] = v

    def SaveReggieInfo(self):
        info = {
            'Creator': ReggieID,
            'Title': self.Title,
            'Author': self.Author,
            'Group': self.Group,
            'Webpage': self.Webpage,
            'Password': self.Password
        }
        return pickletools.optimize(pickle.dumps(info, 2))


def itemBoxFillOpacities():
    """Return opacities for selected and unselected states"""
    if DarkMode:
        return 255, 180
    else:
        return 240, 120


class LevelEditorItem(QtWidgets.QGraphicsItem):
    """Class for any type of item that can show up in the level editor control"""
    positionChanged = None # Callback: positionChanged(LevelEditorItem obj, int oldx, int oldy, int x, int y)

    def __init__(self):
        """Generic constructor for level editor items"""
        super(LevelEditorItem, self).__init__()
        self.setFlag(qm(QtWidgets.QGraphicsItem.GraphicsItemFlag).ItemSendsGeometryChanges, True)

    def itemChange(self, change, value):
        """Makes sure positions don't go out of bounds and updates them as necessary"""

        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            # snap to 24x24
            newpos = qm(value)

            # snap even further if Shift isn't held
            # but -only- if OverrideSnapping is off
            if not OverrideSnapping:
                if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.AltModifier:
                    newpos.setX(int(int((newpos.x() + 0.75) / 1.5) * 1.5))
                    newpos.setY(int(int((newpos.y() + 0.75) / 1.5) * 1.5))
                else:
                    newpos.setX(int(int((newpos.x() + 6) / 12) * 12))
                    newpos.setY(int(int((newpos.y() + 6) / 12) * 12))

            x = newpos.x()
            y = newpos.y()

            # don't let it get out of the boundaries
            if x < 0: newpos.setX(0)
            if x > 24552: newpos.setX(24552)
            if y < 0: newpos.setY(0)
            if y > 12264: newpos.setY(12264)

            # update the data
            x = int(newpos.x() / 1.5)
            y = int(newpos.y() / 1.5)
            if x != self.objx or y != self.objy:
                updRect = QtCore.QRectF(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())
                if self.scene() is not None:
                    self.scene().update(updRect)

                oldx = self.objx
                oldy = self.objy
                self.objx = x
                self.objy = y
                if self.positionChanged is not None:
                    self.positionChanged(self, oldx, oldy, x, y)

                SetDirty()

            return newpos

        return QtWidgets.QGraphicsItem.itemChange(self, change, value)

    def boundingRect(self):
        """Required for Qt"""
        return self.BoundingRect


class LevelObjectEditorItem(LevelEditorItem):
    """Level editor item that represents an ingame object"""

    def __init__(self, tileset, type, layer, x, y, width, height, z):
        """Creates an object with specific data"""
        super(LevelObjectEditorItem, self).__init__()

        self.tileset = tileset
        self.type = type
        self.objx = x
        self.objy = y
        self.layer = layer
        self.width = width
        self.height = height
        self.objdata = None

        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, ObjectsNonFrozen)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, ObjectsNonFrozen)
        self.UpdateRects()

        self.dragging = False
        self.dragstartx = -1
        self.dragstarty = -1

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(x*24,y*24)
        DirtyOverride -= 1

        self.setZValue(z)
        self.setToolTip('Tileset %d object %d' % (tileset+1, type))

        if layer == 0:
            self.setVisible(ShowLayer0)
        elif layer == 1:
            self.setVisible(ShowLayer1)
        elif layer == 2:
            self.setVisible(ShowLayer2)

        self.updateObjCache()


    def SetType(self, tileset, type):
        """Sets the type of the object"""
        self.setToolTip('Tileset %d object %d' % (tileset+1, type))
        self.tileset = tileset
        self.type = type
        self.updateObjCache()
        self.update()


    def updateObjCache(self):
        """Updates the rendered object data"""
        self.objdata = RenderObject(self.tileset, self.type, self.width, self.height)


    def UpdateRects(self):
        """Recreates the bounding and selection rects"""
        self.prepareGeometryChange()
        self.BoundingRect = QtCore.QRectF(0,0,24*self.width,24*self.height)
        self.SelectionRect = QtCore.QRectF(0,0,24*self.width-1,24*self.height-1)
        self.GrabberRect = QtCore.QRectF(24*self.width-5,24*self.height-5,5,5)
        self.LevelRect = QtCore.QRectF(self.objx,self.objy,self.width,self.height)


    def itemChange(self, change, value):
        """Makes sure positions don't go out of bounds and updates them as necessary"""

        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            scene = self.scene()
            if scene is None: return value

            # snap to 24x24
            newpos = qm(value)
            newpos.setX(int((newpos.x() + 12) / 24) * 24)
            newpos.setY(int((newpos.y() + 12) / 24) * 24)
            x = newpos.x()
            y = newpos.y()

            # don't let it get out of the boundaries
            if x < 0: newpos.setX(0)
            if x > 24576: newpos.setX(24576)
            if y < 0: newpos.setY(0)
            if y > 12288: newpos.setY(12288)

            # update the data
            x = int(newpos.x() / 24)
            y = int(newpos.y() / 24)
            if x != self.objx or y != self.objy:
                self.LevelRect.moveTo(x,y)

                oldx = self.objx
                oldy = self.objy
                self.objx = x
                self.objy = y
                if self.positionChanged is not None:
                    self.positionChanged(self, oldx, oldy, x, y)

                SetDirty()

                #updRect = QtCore.QRectF(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())
                #scene.invalidate(updRect)

                scene.invalidate(self.x(), self.y(), self.width*24, self.height*24, QtWidgets.QGraphicsScene.SceneLayer.BackgroundLayer)
                #scene.invalidate(newpos.x(), newpos.y(), self.width*24, self.height*24, QtWidgets.QGraphicsScene.SceneLayer.BackgroundLayer)

            return newpos

        return QtWidgets.QGraphicsItem.itemChange(self, change, value)


    def paint(self, painter, option, widget):
        """Paints the object"""
        if self.isSelected():
            painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1, QtCore.Qt.PenStyle.DotLine))
            painter.drawRect(self.SelectionRect)
            painter.fillRect(self.SelectionRect, QtGui.QColor.fromRgb(255,255,255,64))

            painter.fillRect(self.GrabberRect, QtGui.QColor.fromRgb(255,255,255,255))


    def mousePressEvent(self, event):
        """Overrides mouse pressing events if needed for resizing"""
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.ControlModifier:
                layer = Level.layers[self.layer]
                if len(layer) == 0:
                    newZ = (2 - self.layer) * 8192
                else:
                    newZ = layer[-1].zValue() + 1

                currentZ = self.zValue()
                self.setZValue(newZ) # swap the Z values so it doesn't look like the cloned item is the old one
                newitem = LevelObjectEditorItem(self.tileset, self.type, self.layer, self.objx, self.objy, self.width, self.height, currentZ)
                layer.append(newitem)
                mainWindow.scene.addItem(newitem)
                mainWindow.scene.clearSelection()
                self.setSelected(True)

                SetDirty()

        if self.isSelected() and self.GrabberRect.contains(event.pos()):
            # start dragging
            self.dragging = True
            self.dragstartx = int((event.pos().x() - 10) / 24)
            self.dragstarty = int((event.pos().y() - 10) / 24)
            event.accept()
        else:
            LevelEditorItem.mousePressEvent(self, event)
            self.dragging = False


    def mouseMoveEvent(self, event):
        """Overrides mouse movement events if needed for resizing"""
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton and self.dragging:
            # resize it
            dsx = self.dragstartx
            dsy = self.dragstarty
            clickedx = int((event.pos().x() - 10) / 24)
            clickedy = int((event.pos().y() - 10) / 24)

            cx = self.objx
            cy = self.objy

            if clickedx < 0: clickedx = 0
            if clickedy < 0: clickedy = 0

            #print('%d %d' % (clickedx - dsx, clickedy - dsy))

            if clickedx != dsx or clickedy != dsy:
                self.dragstartx = clickedx
                self.dragstarty = clickedy

                self.width += clickedx - dsx
                self.height += clickedy - dsy

                self.updateObjCache()

                oldrect = self.BoundingRect
                oldrect.translate(cx * 24, cy * 24)
                newrect = QtCore.QRectF(self.x(), self.y(), self.width * 24, self.height * 24)
                updaterect = oldrect.united(newrect)

                self.UpdateRects()
                self.scene().update(updaterect)
                SetDirty()
                mainWindow.levelOverview.update()

            event.accept()
        else:
            LevelEditorItem.mouseMoveEvent(self, event)


    def delete(self):
        """Delete the object from the level"""
        Level.RemoveFromLayer(self)
        self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())


class ZoneItem(LevelEditorItem):
    """Level editor item that represents a zone"""

    def __init__(self, a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p, boundings, bgA, bgB, id):
        """Creates a zone with specific data"""
        super(ZoneItem, self).__init__()

        self.font = NumberFontBold
        self.id = id
        self.TitlePos = QtCore.QPointF(10,18)
        self.UpdateTitle()

        self.objx = a
        self.objy = b
        self.width = c
        self.height = d
        self.modeldark = e
        self.terraindark = f
        self.zoneID = g
        self.block3id = h
        self.cammode = i
        self.camzoom = j
        self.visibility = k
        self.block5id = l
        self.block6id = m
        self.direction = n
        self.music = o
        self.sfxmod = p
        self.UpdateRects()

        bounding = None
        id = self.block3id
        for block in boundings:
            if block[4] == id: bounding = block

        self.yupperbound = bounding[0]
        self.ylowerbound = bounding[1]
        self.yupperbound2 = bounding[2]
        self.ylowerbound2 = bounding[3]
        self.entryid = bounding[4]
        self.mpcamzoomadjust = bounding[5]
        self.yupperbound3 = bounding[6]
        self.ylowerbound3 = bounding[7]

        bgABlock = None
        id = self.block5id
        for block in bgA:
            if block[0] == id: bgABlock = block

        self.entryidA = bgABlock[0]
        self.XscrollA = bgABlock[1]
        self.YscrollA = bgABlock[2]
        self.YpositionA = bgABlock[3]
        self.XpositionA = bgABlock[4]
        self.bg1A = bgABlock[5]
        self.bg2A = bgABlock[6]
        self.bg3A = bgABlock[7]
        self.ZoomA = bgABlock[8]

        bgBBlock = None
        id = self.block6id
        for block in bgB:
            if block[0] == id: bgBBlock = block

        self.entryidB = bgBBlock[0]
        self.XscrollB = bgBBlock[1]
        self.YscrollB = bgBBlock[2]
        self.YpositionB = bgBBlock[3]
        self.XpositionB = bgBBlock[4]
        self.bg1B = bgBBlock[5]
        self.bg2B = bgBBlock[6]
        self.bg3B = bgBBlock[7]
        self.ZoomB = bgBBlock[8]

        self.dragging = False
        self.dragstartx = -1
        self.dragstarty = -1

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(int(a*1.5),int(b*1.5))
        DirtyOverride -= 1
        self.setZValue(50000)


    def UpdateTitle(self):
        """Updates the zone's title"""
        self.title = 'Zone %d' % (self.id+1)


    def UpdateRects(self):
        """Updates the zone's bounding rectangle"""
        self.prepareGeometryChange()
        self.BoundingRect = QtCore.QRectF(0,0,self.width*1.5,self.height*1.5)
        self.ZoneRect = QtCore.QRectF(self.objx,self.objy,self.width,self.height)
        self.DrawRect = QtCore.QRectF(3,3,int(self.width*1.5)-6,int(self.height*1.5)-6)
        self.GrabberRectTL = QtCore.QRectF(0,0,5,5)
        self.GrabberRectTR = QtCore.QRectF(int(self.width*1.5)-5,0,5,5)
        self.GrabberRectBL = QtCore.QRectF(0,int(self.height*1.5)-5,5,5)
        self.GrabberRectBR = QtCore.QRectF(int(self.width*1.5)-5,int(self.height*1.5)-5,5,5)


    def paint(self, painter, option, widget):
        """Paints the zone on screen"""
        #painter.setClipRect(option.exposedRect)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        if DarkMode:
            textColor = QtGui.QColor.fromRgba(0xFFCAE0F9)
        else:
            textColor = QtGui.QColor.fromRgba(0xFF2C4054)

        painter.setPen(QtGui.QPen(QtGui.QColor.fromRgba(0xB093C9FF), 3))
        painter.drawRect(self.DrawRect)

        painter.setPen(QtGui.QPen(textColor, 3))
        painter.setFont(self.font)
        painter.drawText(self.TitlePos, self.title)

        GrabberColour = QtGui.QColor.fromRgb(255,255,255,255)
        painter.fillRect(self.GrabberRectTL, GrabberColour)
        painter.fillRect(self.GrabberRectTR, GrabberColour)
        painter.fillRect(self.GrabberRectBL, GrabberColour)
        painter.fillRect(self.GrabberRectBR, GrabberColour)


    def mousePressEvent(self, event):
        """Overrides mouse pressing events if needed for resizing"""

        if self.GrabberRectTL.contains(event.pos()):
            self.dragging = True
            self.dragcorner = 1
        elif self.GrabberRectTR.contains(event.pos()):
            self.dragging = True
            self.dragcorner = 2
        elif self.GrabberRectBL.contains(event.pos()):
            self.dragging = True
            self.dragcorner = 3
        elif self.GrabberRectBR.contains(event.pos()):
            self.dragging = True
            self.dragcorner = 4
        else:
            self.dragging = False

        if self.dragging:
            # start dragging
            self.dragstartx = int(event.scenePos().x() / 1.5)
            self.dragstarty = int(event.scenePos().y() / 1.5)
            self.draginitialx1 = self.objx
            self.draginitialy1 = self.objy
            self.draginitialx2 = self.objx + self.width
            self.draginitialy2 = self.objy + self.height
            event.accept()
        else:
            LevelEditorItem.mousePressEvent(self, event)


    def mouseMoveEvent(self, event):
        """Overrides mouse movement events if needed for resizing"""
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton and self.dragging:
            # resize it
            clickedx = int(event.scenePos().x() / 1.5)
            clickedy = int(event.scenePos().y() / 1.5)

            x1 = self.draginitialx1
            y1 = self.draginitialy1
            x2 = self.draginitialx2
            y2 = self.draginitialy2

            oldx = self.x()
            oldy = self.y()
            oldw = self.width * 1.5
            oldh = self.height * 1.5

            deltax = clickedx - self.dragstartx
            deltay = clickedy - self.dragstarty

            MIN_X = 16
            MIN_Y = 16
            MIN_W = 300
            MIN_H = 200

            if self.dragcorner == 1: # TL
                x1 += deltax
                y1 += deltay
                if x1 < MIN_X: x1 = MIN_X
                if y1 < MIN_Y: y1 = MIN_Y
                if x2 - x1 < MIN_W: x1 = x2 - MIN_W
                if y2 - y1 < MIN_H: y1 = y2 - MIN_H

            elif self.dragcorner == 2: # TR
                x2 += deltax
                y1 += deltay
                if y1 < MIN_Y: y1 = MIN_Y
                if x2 - x1 < MIN_W: x2 = x1 + MIN_W
                if y2 - y1 < MIN_H: y1 = y2 - MIN_H

            elif self.dragcorner == 3: # BL
                x1 += deltax
                y2 += deltay
                if x1 < MIN_X: x1 = MIN_X
                if x2 - x1 < MIN_W: x1 = x2 - MIN_W
                if y2 - y1 < MIN_H: y2 = y1 + MIN_H

            elif self.dragcorner == 4: # BR
                x2 += deltax
                y2 += deltay
                if x2 - x1 < MIN_W: x2 = x1 + MIN_W
                if y2 - y1 < MIN_H: y2 = y1 + MIN_H

            self.objx = x1
            self.objy = y1
            self.width = x2 - x1
            self.height = y2 - y1

            oldrect = QtCore.QRectF(oldx, oldy, oldw, oldh)
            newrect = QtCore.QRectF(self.x(), self.y(), self.width * 1.5, self.height * 1.5)
            updaterect = oldrect.united(newrect)
            updaterect.setTop(updaterect.top() - 3)
            updaterect.setLeft(updaterect.left() - 3)
            updaterect.setRight(updaterect.right() + 3)
            updaterect.setBottom(updaterect.bottom() + 3)

            self.UpdateRects()
            self.setPos(int(self.objx * 1.5), int(self.objy * 1.5))
            self.scene().update(updaterect)

            mainWindow.levelOverview.update()
            SetDirty()

            event.accept()

        else:
            LevelEditorItem.mouseMoveEvent(self, event)

    def itemChange(self, change, value):
        """Avoids snapping for zones"""
        return QtWidgets.QGraphicsItem.itemChange(self, change, value)

class LocationEditorItem(LevelEditorItem):
    """Level editor item that represents a location"""
    sizeChanged = None # Callback: sizeChanged(SpriteEditorItem obj, int width, int height)

    def __init__(self, x, y, width, height, id):
        """Creates a location with specific data"""
        super(LocationEditorItem, self).__init__()

        self.font = NumberFontBold
        self.TitleRect = QtCore.QRectF(4,4,26,20)
        self.objx = x
        self.objy = y
        self.width = width
        self.height = height
        self.id = id
        self.UpdateTitle()
        self.UpdateRects()

        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, LocationsNonFrozen)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, LocationsNonFrozen)

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(int(x*1.5),int(y*1.5))
        DirtyOverride -= 1

        self.dragging = False
        self.setZValue(24000)
        self.setVisible(ShowLocations)


    def UpdateTitle(self):
        """Updates the location's title"""
        self.title = '%d' % (self.id)


    def UpdateRects(self):
        """Updates the location's bounding rectangle"""
        self.prepareGeometryChange()
        self.BoundingRectWithoutTitleRect = QtCore.QRectF(0,0,self.width*1.5,self.height*1.5)
        self.BoundingRect = self.BoundingRectWithoutTitleRect | self.TitleRect
        self.SelectionRect = QtCore.QRectF(self.objx*1.5,self.objy*1.5,self.width*1.5,self.height*1.5)
        self.ZoneRect = QtCore.QRectF(self.objx,self.objy,self.width,self.height)
        self.DrawRect = QtCore.QRectF(1,1,self.width*1.5-2,self.height*1.5-2)
        self.GrabberRect = QtCore.QRectF(1.5*self.width-6,1.5*self.height-6,5,5)


    def shape(self):
        """
        self.BoundingRect is big enough to include self.TitleRect (so
        the ID text can be painted), but that makes the hit-detection
        region too large if the rect is small.
        """
        # We basically make a vertically-flipped "L" shape if the location
        # is small, so that you can click on the ID number to select the location
        qpp = QtGui.QPainterPath()
        qpp.addRect(self.BoundingRectWithoutTitleRect)
        qpp.addRect(self.TitleRect)
        return qpp


    def paint(self, painter, option, widget):
        """Paints the location on screen"""
        painter.setClipRect(option.exposedRect)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(114,42,188,70)))
        painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 2))
        painter.drawRect(self.DrawRect)

        painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))
        painter.setFont(self.font)
        painter.drawText(self.TitleRect, self.title)

        if self.isSelected():
            painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1, QtCore.Qt.PenStyle.DotLine))
            painter.drawRect(self.DrawRect)
            painter.fillRect(self.DrawRect, QtGui.QColor.fromRgb(255,255,255,40))

            painter.fillRect(self.GrabberRect, QtGui.QColor.fromRgb(255,255,255,255))


    def mousePressEvent(self, event):
        """Overrides mouse pressing events if needed for resizing"""
        if self.isSelected() and self.GrabberRect.contains(event.pos()):
            # start dragging
            self.dragging = True
            self.dragstartx = int(event.pos().x() / 1.5)
            self.dragstarty = int(event.pos().y() / 1.5)
            event.accept()
        else:
            LevelEditorItem.mousePressEvent(self, event)
            self.dragging = False


    def mouseMoveEvent(self, event):
        """Overrides mouse movement events if needed for resizing"""
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton and self.dragging:
            # resize it
            dsx = self.dragstartx
            dsy = self.dragstarty
            clickedx = event.pos().x() / 1.5
            clickedy = event.pos().y() / 1.5

            cx = self.objx
            cy = self.objy

            if clickedx < 0: clickedx = 0
            if clickedy < 0: clickedy = 0

            #print('%d %d' % (clickedx - dsx, clickedy - dsy))

            if clickedx != dsx or clickedy != dsy:
                self.dragstartx = clickedx
                self.dragstarty = clickedy

                self.width += clickedx - dsx
                self.height += clickedy - dsy

                oldrect = self.BoundingRect
                oldrect.translate(cx*1.5, cy*1.5)
                newrect = QtCore.QRectF(self.x(), self.y(), self.width*1.5, self.height*1.5)
                updaterect = oldrect.united(newrect)

                self.UpdateRects()
                self.scene().update(updaterect)
                SetDirty()

                if self.sizeChanged is not None:
                    self.sizeChanged(self, self.width, self.height)

            event.accept()
        else:
            LevelEditorItem.mouseMoveEvent(self, event)


    def delete(self):
        """Delete the zone from the level"""
        Level.locations.remove(self)
        self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())




class SpriteEditorItem(LevelEditorItem):
    """Level editor item that represents a sprite"""
    BoundingRect = QtCore.QRectF(0,0,24,24)
    SelectionRect = QtCore.QRectF(0,0,23,23)
    RoundedRect = QtCore.QRectF(1,1,22,22)

    def __init__(self, type, x, y, data):
        """Creates a sprite with specific data"""
        super(SpriteEditorItem, self).__init__()

        self.font = NumberFont
        self.type = type
        self.objx = x
        self.objy = y
        self.spritedata = data
        self.LevelRect = (QtCore.QRectF(self.objx/16, self.objy/16, 24/16, 24/16))
        self.ChangingPos = False

        self.InitialiseSprite()

        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, SpritesNonFrozen)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, SpritesNonFrozen)

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(int((x+self.xoffset)*1.5),int((y+self.yoffset)*1.5))
        DirtyOverride -= 1

        sname = Sprites[type].name if type < len(Sprites) else 'UNKNOWN'
        self.name = sname
        self.setToolTip('<b>Sprite %d:</b><br>%s' % (type,sname))

        self.setVisible(ShowSprites)

    def SetType(self, type):
        """Sets the type of the sprite"""
        self.name = Sprites[type].name if type < len(Sprites) else 'UNKNOWN'
        self.setToolTip('<b>Sprite %d:</b><br>%s' % (type, self.name))
        self.type = type

        #CurrentRect = QtCore.QRectF(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())

        self.InitialiseSprite()

        #self.scene().update(CurrentRect)
        #self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())

    def InitialiseSprite(self):
        """Initialises sprite and creates any auxiliary objects needed"""
        if hasattr(self, 'aux'):
            self.aux.scene().removeItem(self.aux)
            del self.aux

        type = self.type

        self.setZValue(25000)
        self.resetTransform()

        xo = 0
        yo = 0
        xs = 16
        ys = 16
        self.dynamicSize = False
        self.customPaint = False

        if ShowSpriteImages and type in sprites.Initialisers:
            init = sprites.Initialisers[type]
            xo, yo, xs, ys = init(self)

        self.xoffset = xo
        self.yoffset = yo
        self.xsize = xs
        self.ysize = ys

        self.UpdateDynamicSizing()
        self.UpdateRects()
        self.ChangingPos = True
        self.setPos(int((self.objx+self.xoffset)*1.5),int((self.objy+self.yoffset)*1.5))
        self.ChangingPos = False

    def UpdateDynamicSizing(self):
        """Updates the sizes for dynamically sized sprites"""
        if self.dynamicSize:
            #CurrentRect = QtCore.QRectF(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())

            self.dynSizer(self)
            self.UpdateRects()

            self.ChangingPos = True
            self.setPos(int((self.objx+self.xoffset)*1.5),int((self.objy+self.yoffset)*1.5))
            self.ChangingPos = False

            #if self.scene() is not None:
            #    self.scene().update(CurrentRect)
            #    self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())

    def UpdateRects(self):
        """Creates all the rectangles for the sprite"""
        type = self.type

        self.prepareGeometryChange()

        xs = self.xsize
        ys = self.ysize

        self.BoundingRect = QtCore.QRectF(0,0,xs*1.5,ys*1.5)
        self.SelectionRect = QtCore.QRectF(0,0,int(xs*1.5-1),int(ys*1.5-1))
        self.RoundedRect = QtCore.QRectF(1,1,xs*1.5-2,ys*1.5-2)
        self.LevelRect = (QtCore.QRectF((self.objx + self.xoffset) / 16, (self.objy + self.yoffset) / 16, self.xsize/16, self.ysize/16))

    def itemChange(self, change, value):
        """Makes sure positions don't go out of bounds and updates them as necessary"""

        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self.scene() is None: return value
            if self.ChangingPos: return value

            xOffset = self.xoffset
            yOffset = self.yoffset

            # snap to 24x24
            newpos = qm(value)

            # snap even further if Shift isn't held
            # but -only- if OverrideSnapping is off
            if not OverrideSnapping:
                if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.AltModifier:
                    newpos.setX((int((newpos.x() + 0.75) / 1.5) * 1.5))
                    newpos.setY((int((newpos.y() + 0.75) / 1.5) * 1.5))
                else:
                    #xCompensation = (xOffset % 16) * 1.5
                    #yCompensation = (yOffset % 16) * 1.5
                    #newpos.setX((int((newpos.x() + 6 - xCompensation) / 12) * 12) + xCompensation)
                    #newpos.setY((int((newpos.y() + 6 - yCompensation) / 12) * 12) + yCompensation)

                    newpos.setX((int((int((newpos.x() + 6) / 1.5) - xOffset) / 8) * 8 + xOffset) * 1.5)
                    newpos.setY((int((int((newpos.y() + 6) / 1.5) - yOffset) / 8) * 8 + yOffset) * 1.5)

            x = newpos.x()
            y = newpos.y()

            # don't let it get out of the boundaries
            if x < 0: newpos.setX(0)
            if x > 24552: newpos.setX(24552)
            if y < 0: newpos.setY(0)
            if y > 12264: newpos.setY(12264)

            # update the data
            x = int(newpos.x() / 1.5 - xOffset)
            y = int(newpos.y() / 1.5 - yOffset)

            if x != self.objx or y != self.objy:
                #oldrect = self.BoundingRect
                #oldrect.translate(self.objx*1.5, self.objy*1.5)
                updRect = QtCore.QRectF(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())
                #self.scene().update(updRect.united(oldrect))
                self.scene().update(updRect)

                self.LevelRect.moveTo((x+xOffset) / 16, (y+yOffset) / 16)

                if hasattr(self, 'aux'):
                    auxUpdRect = QtCore.QRectF(self.x()+self.aux.x(), self.y()+self.aux.y(), self.aux.BoundingRect.width(), self.aux.BoundingRect.height())
                    self.scene().update(auxUpdRect)

                oldx = self.objx
                oldy = self.objy
                self.objx = x
                self.objy = y
                if self.positionChanged is not None:
                    self.positionChanged(self, oldx, oldy, x, y)

                SetDirty()
                mainWindow.levelOverview.update()

            return newpos

        return QtWidgets.QGraphicsItem.itemChange(self, change, value)

    def mousePressEvent(self, event):
        """Overrides mouse pressing events if needed for cloning"""
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.ControlModifier:
                newitem = SpriteEditorItem(self.type, self.objx, self.objy, self.spritedata)
                Level.sprites.append(newitem)
                mainWindow.scene.addItem(newitem)
                mainWindow.scene.clearSelection()
                self.setSelected(True)
                SetDirty()
                return

        LevelEditorItem.mousePressEvent(self, event)

    def paint(self, painter, option, widget):
        """Paints the object"""
        painter.setClipRect(option.exposedRect)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        selectedOpacity, unselectedOpacity = itemBoxFillOpacities()
        if DarkMode:
            fillR, fillG, fillB = 30, 110, 196
        else:
            fillR, fillG, fillB = 0, 92, 196

        if self.customPaint:
            self.customPainter(self, painter)
            if self.isSelected():
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1, QtCore.Qt.PenStyle.DotLine))
                painter.drawRect(self.SelectionRect)
                painter.fillRect(self.SelectionRect, QtGui.QColor.fromRgb(255,255,255,64))
        else:
            if self.isSelected():
                painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(fillR,fillG,fillB,selectedOpacity)))
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))
            else:
                painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(fillR,fillG,fillB,unselectedOpacity)))
                painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 1))
            painter.drawRoundedRect(self.RoundedRect, 4, 4)

            painter.setFont(self.font)
            painter.drawText(self.BoundingRect,QtCore.Qt.AlignmentFlag.AlignCenter,str(self.type))

    def delete(self):
        """Delete the sprite from the level"""
        Level.sprites.remove(self)
        self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())


class EntranceEditorItem(LevelEditorItem):
    """Level editor item that represents an entrance"""
    EntranceImages = None

    BoundingRect = QtCore.QRectF(0,0,24,24)

    def __init__(self, x, y, id, destarea, destentrance, type, zone, layer, path, settings, exittomap, cpd):
        """Creates an entrance with specific data"""
        if EntranceEditorItem.EntranceImages is None:
            ei = []
            src = QtGui.QPixmap('reggiedata/entrances.png')
            for i in range(18):
                ei.append(src.copy(i*24,0,24,24))
            EntranceEditorItem.EntranceImages = ei

        super(EntranceEditorItem, self).__init__()

        self.font = NumberFont
        self.objx = x
        self.objy = y
        self.entid = id
        self.destarea = destarea
        self.destentrance = destentrance
        self.enttype = type
        self.entzone = zone
        self.entsettings = settings
        self.entlayer = layer
        self.entpath = path
        self.exittomap = exittomap
        self.cpdirection = cpd
        self.listitem = None

        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, EntrancesNonFrozen)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, EntrancesNonFrozen)

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(int(x*1.5),int(y*1.5))
        DirtyOverride -= 1

        self.setZValue(25001)
        self.UpdateTooltip()
        self.UpdateRects()
        self.setVisible(ShowEntrances)

    def itemChange(self, change, value):
        """Makes sure positions don't go out of bounds and updates them as necessary"""
        retVal = super(EntranceEditorItem, self).itemChange(change, value)
        try:
            self.UpdateRects()
            mainWindow.levelOverview.update()
        except AttributeError:
            # Can happen during initialization. We can just ignore this
            pass
        return retVal

    def UpdateTooltip(self):
        """Updates the entrance object's tooltip"""
        if self.enttype >= len(EntranceTypeNames):
            name = 'Unknown'
        else:
            name = EntranceTypeNames[self.enttype]

        if (self.entsettings & 0x80) != 0:
            destination = '(cannot be entered)'
        elif self.exittomap != 0:
            destination = '(goes to world map)'
        elif self.destarea == 0:
            destination = '(arrives at entrance %d in this area)' % self.destentrance
        else:
            destination = '(arrives at entrance %d in area %d)' % (self.destentrance,self.destarea)

        self.name = name
        self.destination = destination
        self.setToolTip('<b>Entrance %d:</b><br>Type: %s<br><i>%s</i>' % (self.entid,name,destination))

    def ListString(self):
        """Returns a string that can be used to describe the entrance in a list"""
        if self.enttype >= len(EntranceTypeNames):
            name = 'Unknown'
        else:
            name = EntranceTypeNames[self.enttype]

        if (self.entsettings & 0x80) != 0:
            return '%d: %s (cannot be entered; at %d,%d)' % (self.entid,name,self.objx,self.objy)
        else:
            return '%d: %s (enterable; at %d,%d)' % (self.entid,name,self.objx,self.objy)

    def paint(self, painter, option, widget):
        """Paints the object"""
        painter.setClipRect(option.exposedRect)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        selectedOpacity, unselectedOpacity = itemBoxFillOpacities()
        if DarkMode:
            fillR, fillG, fillB = 255, 50, 50
        else:
            fillR, fillG, fillB = 190, 0, 0

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        if self.isSelected():
            painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(fillR,fillG,fillB,selectedOpacity)))
        else:
            painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(fillR,fillG,fillB,unselectedOpacity)))
        painter.drawRoundedRect(self.RoundedRect, 4, 4)

        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        if self.isSelected():
            painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))
        else:
            painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 1))


        icontype = 0
        enttype = self.enttype
        if enttype == 0 or enttype == 1: icontype = 1 # normal
        if enttype == 2: icontype = 2 # door exit
        if enttype == 3: icontype = 4 # pipe up
        if enttype == 4: icontype = 5 # pipe down
        if enttype == 5: icontype = 6 # pipe left
        if enttype == 6: icontype = 7 # pipe right
        if enttype == 8: icontype = 12 # ground pound
        if enttype == 9: icontype = 13 # sliding
        #0F/15 is unknown?
        if enttype == 16: icontype = 8 # mini pipe up
        if enttype == 17: icontype = 9 # mini pipe down
        if enttype == 18: icontype = 10 # mini pipe left
        if enttype == 19: icontype = 11 # mini pipe right
        if enttype == 20: icontype = 15 # jump out facing right
        if enttype == 21: icontype = 17 # vine entrance
        if enttype == 23: icontype = 14 # boss battle entrance
        if enttype == 24: icontype = 16 # jump out facing left
        if enttype == 27: icontype = 3 # door entrance

        painter.drawPixmap(1,1,22,22,EntranceEditorItem.EntranceImages[icontype],1,1,22,22)

        #painter.drawText(self.BoundingRect,QtCore.Qt.AlignmentFlag.AlignLeft,str(self.entid))
        painter.setFont(self.font)
        fontheight = QtGui.QFontMetrics(self.font).ascent() * 2/3
        painter.drawText(QtCore.QPointF(3,7+fontheight/2),str(self.entid))

        painter.drawRoundedRect(self.RoundedRect, 4, 4)

        if self.isSelected():
            #painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
            #painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 1, QtCore.Qt.PenStyle.DotLine))
            #painter.drawRect(self.SelectionRect)
            pass

    def delete(self):
        """Delete the entrance from the level"""
        elist = mainWindow.entranceList
        mainWindow.UpdateFlag = True
        elist.takeItem(elist.row(self.listitem))
        mainWindow.UpdateFlag = False
        elist.selectionModel().clearSelection()
        Level.entrances.remove(self)
        self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())

    def UpdateRects(self):
        """Recreates the bounding and selection rects"""
        self.prepareGeometryChange()

        if self.enttype in {3, 4}:
            w, h = 2, 1
        elif self.enttype in {5, 6}:
            w, h = 1, 2
        else:
            w, h = 1, 1

        self.BoundingRect = QtCore.QRectF(0, 0, 24 * w, 24 * h)
        self.SelectionRect = QtCore.QRectF(0, 0, 24 * w - 1, 24 * h - 1)
        self.LevelRect = QtCore.QRectF(self.objx / 16, self.objy / 16, 24/16 * w, 24/16 * h)
        self.RoundedRect = QtCore.QRectF(1, 1, 24 * w - 2, 24 * h - 2)


class PathEditorItem(LevelEditorItem):
    """Level editor item that represents a pathnode"""
    BoundingRect = QtCore.QRectF(0,0,24,24)
    SelectionRect = QtCore.QRectF(0,0,23,23)
    RoundedRect = QtCore.QRectF(1,1,22,22)


    def __init__(self, objx, objy, nobjx, nobjy, pathinfo, nodeinfo):
        """Creates a path with specific data"""

        global mainWindow
        super(PathEditorItem, self).__init__()

        self.font = NumberFont
        self.objx = objx
        self.objy = objy
        self.pathid = pathinfo['id']
        self.nodeid = pathinfo['nodes'].index(nodeinfo)
        self.pathinfo = pathinfo
        self.nodeinfo = nodeinfo
        self.nobjx = nobjx
        self.nobjy = nobjy
        self.listitem = None
        self.LevelRect = (QtCore.QRectF(self.objx/16, self.objy/16, 24/16, 24/16))
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, PathsNonFrozen)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, PathsNonFrozen)
        # handle path freezing later

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(int(objx*1.5),int(objy*1.5))
        nodeinfo['x'] = self.objx
        nodeinfo['y'] = self.objy
        DirtyOverride -= 1

        self.setZValue(25003)
        self.UpdateTooltip()
        self.setVisible(ShowPaths)

        # now that we're inited, set
        self.nodeinfo['graphicsitem'] = self

    def UpdateTooltip(self):
        """Updates the path object's tooltip"""
        self.setToolTip('<b>Path ID: %d</b><br>Node ID: %s' % (self.pathid,self.nodeid))

    def ListString(self):
        """Returns a string that can be used to describe the entrance in a list"""
        return 'Path ID %d, Node ID: %s' % (self.pathid,self.nodeid)

    def updatePos(self):
        """Our x/y was changed, update pathinfo"""
        self.pathinfo['nodes'][self.nodeid]['x'] = self.objx
        self.pathinfo['nodes'][self.nodeid]['y'] = self.objy

    def updateId(self):
        """Path was changed, find our new nodeid"""
        # called when 1. add node 2. delete node 3. change node order
        # hacky code but it works. considering how pathnodes are stored.
        self.nodeid = self.pathinfo['nodes'].index(self.nodeinfo)
        self.UpdateTooltip()
        self.listitem.setText(self.ListString())
        self.scene().update()

        # if node doesn't exist, let Reggie implode!

    def paint(self, painter, option, widget):
        """Paints the object"""
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.exposedRect)

        selectedOpacity, unselectedOpacity = itemBoxFillOpacities()

        if self.isSelected():
            painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(6,249,20,selectedOpacity)))
            painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.white, 1))
        else:
            painter.setBrush(QtGui.QBrush(QtGui.QColor.fromRgb(6,249,20,unselectedOpacity)))
            painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 1))
        painter.drawRoundedRect(self.RoundedRect, 4, 4)

        icontype = 0

        painter.setFont(self.font)
        fontheight = QtGui.QFontMetrics(self.font).ascent() * 2/3
        painter.drawText(QtCore.QPointF(4,7+fontheight/2),str(self.pathid))
        painter.drawText(QtCore.QPointF(4,17+fontheight/2),str(self.nodeid))

        if self.isSelected():
            #painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
            #painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 1, QtCore.Qt.PenStyle.DotLine))
            #painter.drawRect(self.SelectionRect)
            pass

    def delete(self):
        """Delete the path node from the level"""
        global mainWindow
        plist = mainWindow.pathList
        mainWindow.UpdateFlag = True
        plist.takeItem(plist.row(self.listitem))
        mainWindow.UpdateFlag = False
        plist.selectionModel().clearSelection()
        Level.paths.remove(self)
        self.pathinfo['nodes'].remove(self.nodeinfo)

        if len(self.pathinfo['nodes']) < 1:
            Level.pathdata.remove(self.pathinfo)
            self.scene().removeItem(self.pathinfo['peline'])

        # update other nodes' IDs
        for pathnode in self.pathinfo['nodes']:
            pathnode['graphicsitem'].updateId()

        self.scene().update(self.x(), self.y(), self.BoundingRect.width(), self.BoundingRect.height())



class PathEditorLineItem(LevelEditorItem):
    """Level editor item to draw a line between two pathnodes"""
    BoundingRect = QtCore.QRectF(0,0,1,1) #compute later #QtCore.QRectF(0,0,max(sys.float_info),max(sys.float_info)) #Compute later
    #SelectionRect = QtCore.QRectF(0,0,0,0)



    def __init__(self, nodelist):
        """Creates a path with specific data"""

        global mainWindow
        super(PathEditorLineItem, self).__init__()

        self.font = NumberFont
        self.objx = 0
        self.objy = 0
        self.nodelist = nodelist
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.computeBoundRectAndPos()
        self.setZValue(25002)
        self.UpdateTooltip()
        self.setVisible(ShowPaths)

    def itemChange(self, change, value):
        """Avoids snapping for path lines"""
        return QtWidgets.QGraphicsItem.itemChange(self, change, value)

    def UpdateTooltip(self):
        """For compatibility, just in case"""
        self.setToolTip('')

    def ListString(self):
        """Returns an empty string"""
        return ''

    def nodePosChanged(self):
        self.computeBoundRectAndPos()
        self.scene().update()

    def computeBoundRectAndPos(self):
        if self.nodelist:
            xcoords = []
            ycoords = []
            for node in self.nodelist:
                xcoords.append(int(node['x']))
                ycoords.append(int(node['y']))

            self.objx = (min(xcoords)-4)#*1.5
            self.objy = (min(ycoords)-4)#*1.5
            mywidth = (8 + (max(xcoords) - self.objx))*1.5
            myheight = (8 + (max(ycoords) - self.objy))*1.5

        else:
            self.objx = self.objy = 0
            mywidth = myheight = 16

        global DirtyOverride
        DirtyOverride += 1
        self.setPos(self.objx * 1.5, self.objy * 1.5)
        DirtyOverride -= 1
        self.prepareGeometryChange()
        self.BoundingRect = QtCore.QRectF(0,0,mywidth,myheight)



    def paint(self, painter, option, widget):
        """Paints the object"""
        if not self.nodelist:
            return

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.exposedRect)

        linecolor = QtGui.QColor.fromRgb(6,249,20)
        painter.setBrush(QtGui.QBrush(linecolor))
        painter.setPen(QtGui.QPen(linecolor, 3, join = QtCore.Qt.PenJoinStyle.RoundJoin, cap = QtCore.Qt.PenCapStyle.RoundCap))
        ppath = QtGui.QPainterPath()

        lines = []

        firstn = True

        snl = self.nodelist
        for j in range(len(self.nodelist)):
            if (j+1) < len(self.nodelist):
                lines.append(QtCore.QLineF(
                    float(snl[j]['x']*1.5) - self.x(),
                    float(snl[j]['y']*1.5) - self.y(),
                    float(snl[j+1]['x']*1.5) - self.x(),
                    float(snl[j+1]['y']*1.5) - self.y()))

        painter.drawLines(lines)

        painter.setPen(QtGui.QPen(linecolor, 3, join = QtCore.Qt.PenJoinStyle.RoundJoin, cap = QtCore.Qt.PenCapStyle.RoundCap, style = QtCore.Qt.PenStyle.DotLine))
        if self.nodelist[0]['graphicsitem'].pathinfo['loops']:
            painter.drawLine(QtCore.QLineF(
                float(snl[-1]['x']*1.5) - self.x(),
                float(snl[-1]['y']*1.5) - self.y(),
                float(snl[0]['x']*1.5) - self.x(),
                float(snl[0]['y']*1.5) - self.y()))


    def delete(self):
        """Delete the line from the level"""


        self.scene().update()


class LevelOverviewWidget(QtWidgets.QWidget):
    """Widget that shows an overview of the level and can be clicked to move the view"""
    moveIt = QtCoreSignal(int, int)

    def __init__(self):
        """Constructor for the level overview widget"""
        super(LevelOverviewWidget, self).__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding, QtWidgets.QSizePolicy.Policy.MinimumExpanding))

        if DarkMode:
            bgcolor = QtGui.QColor.fromRgb(32, 32, 32)
        else:
            bgcolor = QtGui.QColor.fromRgb(119,136,153)
        self.bgbrush = QtGui.QBrush(bgcolor)
        self.objbrush = QtGui.QBrush(QtGui.QColor.fromRgb(255,255,255))
        self.viewbrush = QtGui.QBrush(QtGui.QColor.fromRgb(47,79,79,120))
        self.view = QtCore.QRectF(0,0,0,0)
        self.spritebrush = QtGui.QBrush(QtGui.QColor.fromRgb(0,92,196))
        self.entrancebrush = QtGui.QBrush(QtGui.QColor.fromRgb(255,0,0))
        self.locationbrush = QtGui.QBrush(QtGui.QColor.fromRgb(114,42,188,50))

        self.CalcSize()
        self.Rescale()

        self.Xposlocator = 0
        self.Yposlocator = 0
        self.Hlocator = 50
        self.Wlocator = 80
        self.mainWindowScale = 1

    def Reset(self):
        """Resets the max and scale variables"""
        self.scale = 0.375
        self.CalcSize()
        self.Rescale()

    def mouseMoveEvent(self, event):
        """Handles mouse movement over the widget"""
        QtWidgets.QWidget.mouseMoveEvent(self, event)

        if event.buttons() == QtCore.Qt.MouseButton.LeftButton:
            pos = qm(event).position()
            self.moveIt.emit(int(pos.x() * self.posmult), int(pos.y() * self.posmult))

    def mousePressEvent(self, event):
        """Handles mouse pressing events over the widget"""
        QtWidgets.QWidget.mousePressEvent(self, event)

        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            pos = qm(event).position()
            self.moveIt.emit(int(pos.x() * self.posmult), int(pos.y() * self.posmult))

    def paintEvent(self, event):
        """Paints the level overview widget"""

        if not hasattr(Level, 'layers'):
            # fixes race condition where this widget is painted after
            # the level is created, but before it's loaded
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        self.CalcSize()
        self.Rescale()
        painter.fillRect(event.rect(), self.bgbrush)
        painter.scale(self.scale, self.scale)
        transform = QtGui.QTransform() / 24

        dr = painter.drawRect
        fr = painter.fillRect


        b = self.viewbrush
        painter.setPen(QtGui.QPen(QtGui.QColor.fromRgb(0,255,255), 1))

        for zone in Level.zones:
            rect = transform.mapRect(zone.sceneBoundingRect())
            fr(rect, b)
            dr(rect)

        b = self.objbrush

        for layer in Level.layers:
            for obj in layer:
                fr(obj.LevelRect, b)


        b = self.spritebrush

        for sprite in Level.sprites:
            fr(sprite.LevelRect, b)


        b = self.entrancebrush

        for ent in Level.entrances:
            fr(ent.LevelRect, b)


        b = self.locationbrush
        painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.black, 1))

        for location in Level.locations:
            rect = transform.mapRect(location.sceneBoundingRect())
            fr(rect, b)
            dr(rect)

        b = self.locationbrush
        painter.setPen(QtGui.QPen(QtCore.Qt.GlobalColor.blue, 1))
        painter.drawRect(QtCore.QRectF(self.Xposlocator/24/self.mainWindowScale,
                                       self.Yposlocator/24/self.mainWindowScale,
                                       self.Wlocator/24/self.mainWindowScale,
                                       self.Hlocator/24/self.mainWindowScale))


    def CalcSize(self):
        """Calculates self.maxX and self.maxY"""
        if Level is None:
            # fixes race condition where this widget's size is calculated
            # after the level is created, but before it's loaded
            self.maxX = 100
            self.maxY = 40
            return

        transform = QtGui.QTransform() / 24
        rect = QtCore.QRectF()

        for zone in Level.zones:
            rect |= transform.mapRect(zone.sceneBoundingRect())

        for layer in Level.layers:
            for obj in layer:
                rect |= obj.LevelRect

        for sprite in Level.sprites:
            rect |= sprite.LevelRect

        for ent in Level.entrances:
            rect |= ent.LevelRect

        for location in Level.locations:
            rect |= transform.mapRect(location.sceneBoundingRect())

        self.maxX = rect.right()
        self.maxY = rect.bottom()


    def Rescale(self):
        """Calculates self.scale and self.posmult"""
        self.Xscale = (float(self.width())/float(self.maxX+45))
        self.Yscale = (float(self.height())/float(self.maxY+25))

        if self.Xscale <= self.Yscale:
            self.scale = self.Xscale
        else:
            self.scale = self.Yscale

        if self.scale == 0: self.scale = 1

        self.posmult = 24.0 / self.scale



class ObjectPickerWidget(QtWidgets.QListView):
    """Widget that shows a list of available objects"""

    def __init__(self):
        """Initialises the widget"""

        super(ObjectPickerWidget, self).__init__()
        self.setFlow(QtWidgets.QListView.Flow.LeftToRight)
        self.setLayoutMode(QtWidgets.QListView.LayoutMode.SinglePass)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setWrapping(True)

        self.m0 = ObjectPickerWidget.ObjectListModel()
        self.m1 = ObjectPickerWidget.ObjectListModel()
        self.m2 = ObjectPickerWidget.ObjectListModel()
        self.m3 = ObjectPickerWidget.ObjectListModel()
        self.setModel(self.m0)

        self.setItemDelegate(ObjectPickerWidget.ObjectItemDelegate())

        self.clicked.connect(self.HandleObjReplace)

    def LoadFromTilesets(self):
        """Renders all the object previews"""
        self.m0.LoadFromTileset(0)
        self.m1.LoadFromTileset(1)
        self.m2.LoadFromTileset(2)
        self.m3.LoadFromTileset(3)

    def ShowTileset(self, id):
        """Shows a specific tileset in the picker"""
        sel = self.currentIndex().row()
        if id == 0: self.setModel(self.m0)
        if id == 1: self.setModel(self.m1)
        if id == 2: self.setModel(self.m2)
        if id == 3: self.setModel(self.m3)
        self.setCurrentIndex(self.model().index(sel, 0, QtCore.QModelIndex()))

    @QtCoreSlot(QtCore.QModelIndex, QtCore.QModelIndex)
    def currentChanged(self, current, previous):
        """Throws a signal when the selected object changed"""
        self.ObjChanged.emit(current.row())

    @QtCoreSlot(QtCore.QModelIndex)
    def HandleObjReplace(self, index):
        """Throws a signal when the selected object is used as a replacement"""
        if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.AltModifier:
            self.ObjReplace.emit(index.row())

    ObjChanged = QtCoreSignal(int)
    ObjReplace = QtCoreSignal(int)


    class ObjectItemDelegate(QtWidgets.QAbstractItemDelegate):
        """Handles tileset objects and their rendering"""

        def __init__(self):
            """Initialises the delegate"""
            super(ObjectPickerWidget.ObjectItemDelegate, self).__init__()

        def paint(self, painter, option, index):
            """Paints an object"""
            if option.state & QtWidgets.QStyle.StateFlag.State_Selected:
                painter.fillRect(option.rect, option.palette.highlight())

            p = index.model().data(index, QtCore.Qt.ItemDataRole.DecorationRole)
            painter.drawPixmap(option.rect.x()+2, option.rect.y()+2, p)
            #painter.drawText(option.rect, str(index.row()))

        def sizeHint(self, option, index):
            """Returns the size for the object"""
            p = index.model().data(index, QtCore.Qt.ItemDataRole.UserRole)
            return p
            #return QtCore.QSize(76,76)


    class ObjectListModel(QtCore.QAbstractListModel):
        """Model containing all the objects in a tileset"""

        def __init__(self):
            """Initialises the model"""
            self.items = []
            self.ritems = []
            self.itemsize = []
            super(ObjectPickerWidget.ObjectListModel, self).__init__()

            #for i in range(256):
            #    self.items.append(None)
            #    self.ritems.append(None)

        def rowCount(self, parent=None):
            """Required by Qt"""
            return len(self.items)

        def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
            """Get what we have for a specific row"""
            if not index.isValid(): return None
            n = index.row()
            if n < 0: return None
            if n >= len(self.items): return None

            if role == QtCore.Qt.ItemDataRole.DecorationRole:
                return self.ritems[n]

            if role == QtCore.Qt.ItemDataRole.BackgroundRole:
                return app.palette().base()

            if role == QtCore.Qt.ItemDataRole.UserRole:
                return self.itemsize[n]

            if role == QtCore.Qt.ItemDataRole.ToolTipRole:
                return self.tooltips[n]

            return None

        def LoadFromTileset(self, idx):
            """Renders all the object previews for the model"""
            if ObjectDefinitions[idx] is None: return

            # begin/endResetModel are only in Qt 4.6...
            if QtCompatVersion >= (4,6,0):
                self.beginResetModel()

            self.items = []
            self.ritems = []
            self.itemsize = []
            self.tooltips = []
            defs = ObjectDefinitions[idx]

            for i in range(256):
                if defs[i] is None: break
                obj = RenderObject(idx, i, defs[i].width, defs[i].height, True)
                self.items.append(obj)

                pm = QtGui.QPixmap(defs[i].width * 24, defs[i].height * 24)
                pm.fill(QtCore.Qt.GlobalColor.transparent)
                p = QtGui.QPainter()
                p.begin(pm)
                y = 0

                for row in obj:
                    x = 0
                    for tile in row:
                        if tile != -1:
                            if isinstance(Tiles[tile], QtGui.QImage):
                                p.drawImage(x, y, Tiles[tile])
                            elif isinstance(Tiles[tile], QtGui.QPixmap):
                                p.drawPixmap(x, y, Tiles[tile])
                            # Else, it's probably None, so we shouldn't draw it
                        x += 24
                    y += 24
                p.end()

                self.ritems.append(pm)
                self.itemsize.append(QtCore.QSize(defs[i].width * 24 + 4, defs[i].height * 24 + 4))
                if idx == 0 and i in ObjDesc:
                    self.tooltips.append('<b>Object %d:</b><br>%s' % (i, ObjDesc[i]))
                else:
                    self.tooltips.append('Object %d' % i)

            if QtCompatVersion >= (4,6,0):
                self.endResetModel()
            else:
                self.reset()


class SpritePickerWidget(QtWidgets.QTreeWidget):
    """Widget that shows a list of available sprites"""

    def __init__(self):
        """Initialises the widget"""

        super(SpritePickerWidget, self).__init__()
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setIndentation(16)
        self.currentItemChanged.connect(self.HandleItemChange)

        LoadSpriteData()
        LoadSpriteCategories()

        loc = QtWidgets.QTreeWidgetItem()
        loc.setText(0, 'Paint New Location')
        loc.setData(0, QtCore.Qt.ItemDataRole.UserRole, 1000)
        self.addTopLevelItem(loc)

        for viewname, view, nodelist in SpriteCategories:
            for catname, category in view:
                cnode = QtWidgets.QTreeWidgetItem()
                cnode.setText(0, catname)
                cnode.setData(0, QtCore.Qt.ItemDataRole.UserRole, -1)

                isSearch = (catname == 'Search Results')
                if isSearch:
                    self.SearchResultsCategory = cnode
                    SearchableItems = []

                for id in category:
                    snode = QtWidgets.QTreeWidgetItem()
                    if id == 9999:
                        snode.setText(0, 'No sprites found')
                        snode.setData(0, QtCore.Qt.ItemDataRole.UserRole, -2)
                        self.NoSpritesFound = snode
                    else:
                        snode.setText(0, '%d: %s' % (id, Sprites[id].name))
                        snode.setData(0, QtCore.Qt.ItemDataRole.UserRole, id)

                    if isSearch:
                        SearchableItems.append(snode)

                    cnode.addChild(snode)

                self.addTopLevelItem(cnode)
                cnode.setHidden(True)
                nodelist.append(cnode)

        self.ShownSearchResults = SearchableItems
        self.NoSpritesFound.setHidden(True)

        self.itemClicked.connect(self.HandleSprReplace)


    def SwitchView(self, view):
        """Changes the selected sprite view"""

        for i in range(1, self.topLevelItemCount()):
            self.topLevelItem(i).setHidden(True)

        for node in view[2]:
            node.setHidden(False)


    @QtCoreSlot(QtWidgets.QTreeWidgetItem, QtWidgets.QTreeWidgetItem)
    def HandleItemChange(self, current, previous):
        """Throws a signal when the selected object changed"""
        id = qm(current.data(0, QtCore.Qt.ItemDataRole.UserRole))
        if id != -1:
            self.SpriteChanged.emit(id)


    def SetSearchString(self, searchfor):
        """Shows the items containing that string"""
        check = self.SearchResultsCategory

        rawresults = self.findItems(searchfor, QtCore.Qt.MatchFlag.MatchContains | QtCore.Qt.MatchFlag.MatchRecursive)
        results = list(filter((lambda x: x.parent() == check), rawresults))

        for x in self.ShownSearchResults: x.setHidden(True)
        for x in results: x.setHidden(False)
        self.ShownSearchResults = results

        self.NoSpritesFound.setHidden((len(results) != 0))
        self.SearchResultsCategory.setExpanded(True)


    @QtCoreSlot(QtWidgets.QTreeWidgetItem, int)
    def HandleSprReplace(self, item, column):
        """Throws a signal when the selected sprite is used as a replacement"""
        if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.AltModifier:
            id = qm(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
            if id != -1:
                self.SpriteReplace.emit(id)

    SpriteChanged = QtCoreSignal(int)
    SpriteReplace = QtCoreSignal(int)


class SpriteEditorWidget(QtWidgets.QWidget):
    """Widget for editing sprite data"""
    DataUpdate = QtCoreSignal(PyObject)

    def __init__(self):
        """Constructor"""
        super(SpriteEditorWidget, self).__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed))

        # create the raw editor
        font = QtGui.QFont()
        font.setPointSize(8)
        editbox = QtWidgets.QLabel('Modify Raw Data:')
        editbox.setFont(font)
        edit = QtWidgets.QLineEdit()
        edit.textEdited.connect(self.HandleRawDataEdited)
        self.raweditor = edit

        editboxlayout = QtWidgets.QHBoxLayout()
        editboxlayout.addWidget(editbox)
        editboxlayout.addWidget(edit)
        editboxlayout.setStretch(1, 1)

        # "Editing Sprite #" label
        self.spriteLabel = QtWidgets.QLabel('-')
        self.spriteLabel.setWordWrap(True)

        self.noteButton = QtWidgets.QToolButton()
        self.noteButton.setIcon(GetIcon('note'))
        self.noteButton.setText('Notes')
        self.noteButton.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.noteButton.setAutoRaise(True)
        self.noteButton.clicked.connect(self.ShowNoteTooltip)

        toplayout = QtWidgets.QHBoxLayout()
        toplayout.addWidget(self.spriteLabel)
        toplayout.addWidget(self.noteButton)
        toplayout.setStretch(0, 1)

        subLayout = QtWidgets.QVBoxLayout()

        # create a layout
        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addLayout(toplayout)
        mainLayout.addLayout(subLayout)

        layout = QtWidgets.QGridLayout()
        self.editorlayout = layout
        subLayout.addLayout(layout)
        subLayout.addLayout(editboxlayout)

        self.setLayout(mainLayout)

        self.spritetype = -1
        self.data = b'\0\0\0\0\0\0\0\0'
        self.fields = []
        self.UpdateFlag = False


    class PropertyDecoder(QtCore.QObject):
        """Base class for all the sprite data decoder/encoders"""
        updateData = QtCoreSignal(PyObject)

        def __init__(self):
            """Generic constructor"""
            super(SpriteEditorWidget.PropertyDecoder, self).__init__()

        def retrieve(self, data):
            """Extracts the value from the specified nybble(s)"""
            nybble = self.nybble

            if isinstance(nybble, tuple):
                if nybble[1] == (nybble[0] + 2) and (nybble[0] | 1) == 0:
                    # optimise if it's just one byte
                    return ord(data[nybble[0] >> 1])
                else:
                    # we have to calculate it sadly
                    # just do it by looping, shouldn't be that bad
                    value = 0
                    for n in range(nybble[0], nybble[1]):
                        value <<= 4
                        value |= (ord(data[n >> 1]) >> (0 if (n & 1) == 1 else 4)) & 15
                    return value
            else:
                # we just want one nybble
                return (ord(data[nybble >> 1]) >> (0 if (nybble & 1) == 1 else 4)) & 15


        def insertvalue(self, data, value):
            """Assigns a value to the specified nybble(s)"""
            nybble = self.nybble
            sdata = [ord(x) for x in data]

            if isinstance(nybble, tuple):
                if nybble[1] == (nybble[0] + 2) and (nybble[0] | 1) == 0:
                    # just one byte, this is easier
                    sdata[nybble[0] >> 1] = value & 255
                else:
                    # AAAAAAAAAAA
                    for n in reversed(range(nybble[0], nybble[1])):
                        cbyte = ord(sdata[n >> 1])
                        if (n & 1) == 1:
                            cbyte = (cbyte & 240) | (value & 15)
                        else:
                            cbyte = ((value & 15) << 4) | (cbyte & 15)
                        sdata[n >> 1] = cbyte
                        value >>= 4
            else:
                # only overwrite one nybble
                cbyte = ord(sdata[nybble >> 1])
                if (nybble & 1) == 1:
                    cbyte = (cbyte & 240) | (value & 15)
                else:
                    cbyte = ((value & 15) << 4) | (cbyte & 15)
                sdata[nybble >> 1] = cbyte

            return intsToBytes(sdata)


    class CheckboxPropertyDecoder(PropertyDecoder):
        """Class that decodes/encodes sprite data to/from a checkbox"""

        def __init__(self, title, nybble, mask, comment, layout, row):
            """Creates the widget"""
            super(SpriteEditorWidget.CheckboxPropertyDecoder, self).__init__()

            self.widget = QtWidgets.QCheckBox(title)
            if comment is not None: self.widget.setToolTip(comment)
            self.widget.clicked.connect(self.HandleClick)

            if isinstance(nybble, tuple):
                length = nybble[1] - nybble[0] + 1
            else:
                length = 1

            xormask = 0
            for i in range(length):
                xormask |= 0xF << (i * 4)

            self.nybble = nybble
            self.mask = mask
            self.xormask = xormask
            layout.addWidget(self.widget, row, 0, 1, 2)

        def update(self, data):
            """Updates the value shown by the widget"""
            value = ((self.retrieve(data) & self.mask) == self.mask)
            self.widget.setChecked(value)

        def assign(self, data):
            """Assigns the selected value to the data"""
            value = self.retrieve(data) & (self.mask ^ self.xormask)
            if self.widget.isChecked():
                value |= self.mask
            return self.insertvalue(data, value)

        @QtCoreSlot(bool)
        def HandleClick(self, clicked=False):
            """Handles clicks on the checkbox"""
            self.updateData.emit(self)


    class ListPropertyDecoder(PropertyDecoder):
        """Class that decodes/encodes sprite data to/from a combobox"""

        def __init__(self, title, nybble, model, comment, layout, row):
            """Creates the widget"""
            super(SpriteEditorWidget.ListPropertyDecoder, self).__init__()

            self.model = model
            self.widget = QtWidgets.QComboBox()
            self.widget.setModel(model)
            if comment is not None: self.widget.setToolTip(comment)
            self.widget.currentIndexChanged.connect(self.HandleIndexChanged)

            self.nybble = nybble
            layout.addWidget(QtWidgets.QLabel(title+':'), row, 0, QtCore.Qt.AlignmentFlag.AlignRight)
            layout.addWidget(self.widget, row, 1)

        def update(self, data):
            """Updates the value shown by the widget"""
            value = self.retrieve(data)
            if not self.model.existingLookup[value]:
                self.widget.setCurrentIndex(-1)
                return

            i = 0
            for x in self.model.entries:
                if x[0] == value:
                    self.widget.setCurrentIndex(i)
                    break
                i += 1

        def assign(self, data):
            """Assigns the selected value to the data"""
            return self.insertvalue(data, self.model.entries[self.widget.currentIndex()][0])

        @QtCoreSlot(int)
        def HandleIndexChanged(self, index):
            """Handle the current index changing in the combobox"""
            self.updateData.emit(self)


    class ValuePropertyDecoder(PropertyDecoder):
        """Class that decodes/encodes sprite data to/from a spinbox"""

        def __init__(self, title, nybble, max, comment, layout, row):
            """Creates the widget"""
            super(SpriteEditorWidget.ValuePropertyDecoder, self).__init__()

            self.widget = QtWidgets.QSpinBox()
            self.widget.setRange(0, max - 1)
            if comment is not None: self.widget.setToolTip(comment)
            self.widget.valueChanged.connect(self.HandleValueChanged)

            self.nybble = nybble
            layout.addWidget(QtWidgets.QLabel(title+':'), row, 0, QtCore.Qt.AlignmentFlag.AlignRight)
            layout.addWidget(self.widget, row, 1)

        def update(self, data):
            """Updates the value shown by the widget"""
            value = self.retrieve(data)
            self.widget.setValue(value)

        def assign(self, data):
            """Assigns the selected value to the data"""
            return self.insertvalue(data, self.widget.value())

        @QtCoreSlot(int)
        def HandleValueChanged(self, value):
            """Handle the value changing in the spinbox"""
            self.updateData.emit(self)


    def setSprite(self, type):
        """Change the sprite type used by the data editor"""
        if self.spritetype == type: return

        self.spritetype = type
        if type != 1000 and type < len(Sprites):
            sprite = Sprites[type]
        else:
            sprite = None

        # remove all the existing widgets in the layout
        layout = self.editorlayout
        for row in range(2, layout.rowCount()):
            for column in range(0, layout.columnCount()):
                w = layout.itemAtPosition(row, column)
                if w is not None:
                    widget = w.widget()
                    layout.removeWidget(widget)
                    widget.setParent(None)

        if sprite is None:
            self.spriteLabel.setText('<b>Unidentified/Unknown Sprite (%d)</b>' % type)
            self.noteButton.setVisible(False)

            # use the raw editor if nothing is there
            self.raweditor.setVisible(True)
            if len(self.fields) > 0: self.fields = []

        else:
            self.spriteLabel.setText('<b>%s (%d)</b>' % (sprite.name, type))
            self.noteButton.setVisible((sprite.notes is not None))
            self.notes = sprite.notes

            # create all the new fields
            fields = []
            row = 2

            for f in sprite.fields:
                if f[0] == 0:
                    nf = SpriteEditorWidget.CheckboxPropertyDecoder(f[1], f[2], f[3], f[4], layout, row)
                elif f[0] == 1:
                    nf = SpriteEditorWidget.ListPropertyDecoder(f[1], f[2], f[3], f[4], layout, row)
                elif f[0] == 2:
                    nf = SpriteEditorWidget.ValuePropertyDecoder(f[1], f[2], f[3], f[4], layout, row)

                nf.updateData.connect(self.HandleFieldUpdate)
                fields.append(nf)
                row += 1

            self.fields = fields

        # done


    def update(self):
        """Updates all the fields to display the appropriate info"""
        self.UpdateFlag = True

        data = self.data
        self.raweditor.setText('%02x%02x %02x%02x %02x%02x %02x%02x' % (ord(data[0]), ord(data[1]), ord(data[2]), ord(data[3]), ord(data[4]), ord(data[5]), ord(data[6]), ord(data[7])))
        #self.raweditor.setText(data.encode('hex'))
        self.raweditor.setStyleSheet('')

        # Go through all the data
        for f in self.fields:
            f.update(data)

        self.UpdateFlag = False


    @QtCoreSlot()
    def ShowNoteTooltip(self):
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), self.notes, self)


    @QtCoreSlot(PyObject)
    def HandleFieldUpdate(self, field):
        """Triggered when a field's data is updated"""
        if self.UpdateFlag: return

        data = field.assign(self.data)
        self.data = data

        self.raweditor.setText('%02x%02x %02x%02x %02x%02x %02x%02x' % (ord(data[0]), ord(data[1]), ord(data[2]), ord(data[3]), ord(data[4]), ord(data[5]), ord(data[6]), ord(data[7])))
        #self.raweditor.setText(data.encode('hex'))
        self.raweditor.setStyleSheet('')

        for f in self.fields:
            if f != field: f.update(data)

        self.DataUpdate.emit(data)


    def HandleRawDataEdited(self, text):
        """Triggered when the raw data textbox is edited"""

        raw = text.replace(' ', '')
        valid = False

        if len(raw) == 16:
            try:
                if sys.version_info.major >= 3:
                    data = bytes.fromhex(str(raw))
                else:
                    data = str(raw).decode('hex')
                valid = True
            except:
                pass

        # if it's valid, let it go
        if valid:
            self.raweditor.setStyleSheet('')
            self.data = data

            self.UpdateFlag = True
            for f in self.fields: f.update(data)
            self.UpdateFlag = False

            self.DataUpdate.emit(data)
        else:
            self.raweditor.setStyleSheet('QLineEdit { background-color: #8d0000; color: #ffffff; font: bold}') #reddish background, bold white text


class EntranceEditorWidget(QtWidgets.QWidget):
    """Widget for editing entrance properties"""

    def __init__(self):
        """Constructor"""
        super(EntranceEditorWidget, self).__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed))

        self.CanUseFlag40 = {0,1,7,8,9,12,20,21,22,23,24,27}
        self.CanUseFlag8 = {3,4,5,6,16,17,18,19}
        self.CanUseFlag4 = {3,4,5,6}

        # create widgets
        self.entranceType = QtWidgets.QComboBox()
        LoadEntranceNames()
        self.entranceType.addItems(EntranceTypeNames)
        self.entranceType.setToolTip('<b>Type:</b><br>Sets how the entrance behaves')
        self.entranceType.activated.connect(self.HandleEntranceTypeChanged)

        self.entranceID = QtWidgets.QSpinBox()
        self.entranceID.setRange(0, 255)
        self.entranceID.setToolTip('<b>ID:</b><br>Must be different from all other IDs')
        self.entranceID.valueChanged.connect(self.HandleEntranceIDChanged)

        self.activeLayer = QtWidgets.QComboBox()
        self.activeLayer.addItems(['Layer 1', 'Layer 2', 'Layer 0'])
        self.activeLayer.setToolTip('<b>Active on:</b><br>Allows you to change the collision layer which this entrance is active on. This option is very glitchy and not used in the default levels - for almost all normal cases, you will want to use layer 1.')
        self.activeLayer.activated.connect(self.HandleActiveLayerChanged)

        self.enterableCheckbox = QtWidgets.QCheckBox('Enterable')
        self.enterableCheckbox.setToolTip("<b>Enterable:</b><br>If this box is checked on a pipe or door entrance, Mario will be able to enter the pipe/door. If it's not checked, he won't be able to enter it. Behaviour on other types of entrances is unknown/undefined.")
        self.enterableCheckbox.clicked.connect(self.HandleEnterableClicked)

        self.unknownFlagCheckbox = QtWidgets.QCheckBox('Unknown Flag')
        self.unknownFlagCheckbox.setToolTip("<b>Unknown Flag:</b><br>This box is checked on a few entrances in the game, but we haven't managed to figure out what it does (or if it does anything).")
        self.unknownFlagCheckbox.clicked.connect(self.HandleUnknownFlagClicked)

        self.destEntranceLabel = QtWidgets.QLabel('Dest. ID:')
        self.destEntrance = QtWidgets.QSpinBox()
        self.destEntrance.setRange(0, 255)
        self.destEntrance.setToolTip('<b>Dest. ID:</b><br>If this entrance leads nowhere, set this to 0.')
        self.destEntrance.valueChanged.connect(self.HandleDestEntranceChanged)

        self.destAreaLabel = QtWidgets.QLabel('Dest. Area:')
        self.destArea = QtWidgets.QSpinBox()
        self.destArea.setRange(0, 4)
        self.destArea.setToolTip('<b>Dest. Area:</b><br>If this entrance leads nowhere or the destination is in this area, set this to 0.')
        self.destArea.valueChanged.connect(self.HandleDestAreaChanged)

        self.sendToEntrance = QtWidgets.QRadioButton('Send to Entrance')
        self.sendToEntrance.setToolTip('<b>Send to Entrance:</b><br>If this is chosen, this entrance will send Mario to a different entrance in the same level when entered.')

        self.sendToWorldMap = QtWidgets.QRadioButton('Send to World Map')
        self.sendToWorldMap.setToolTip('<b>Send to World Map:</b><br>If this is chosen, this entrance will send Mario back to the world map when entered, without finishing the level.')

        self.sendToEntranceOrWMGroup = QtWidgets.QButtonGroup(self)
        self.sendToEntranceOrWMGroup.addButton(self.sendToEntrance, 0)
        self.sendToEntranceOrWMGroup.addButton(self.sendToWorldMap, 1)
        qm(self.sendToEntranceOrWMGroup).idClicked.connect(self.SendToEntranceOrWMChanged)

        self.spawnHalfTileLeftCheckbox = QtWidgets.QCheckBox('Spawn Half a Tile Left')
        self.spawnHalfTileLeftCheckbox.setToolTip("<b>Spawn Half a Tile Left:</b><br>If this is checked, the entrance will spawn Mario half a tile to the left.")
        self.spawnHalfTileLeftCheckbox.clicked.connect(self.HandleSpawnHalfTileLeftClicked)

        self.forwardPipeCheckbox = QtWidgets.QCheckBox('Links to Forward Pipe')
        self.forwardPipeCheckbox.setToolTip('<b>Links to Forward Pipe:</b><br>If this option is set on a pipe, the destination entrance/area values will be ignored - Mario will pass through the pipe and then reappear several tiles ahead, coming out of a pipe that faces the screen.')
        self.forwardPipeCheckbox.clicked.connect(self.HandleForwardPipeClicked)

        self.connectedPipeCheckbox = QtWidgets.QCheckBox('Connected Pipe')
        self.connectedPipeCheckbox.setToolTip("<b>Connected Pipe:</b><br>This box allows you to enable an unused/broken feature in the game. It allows the pipe to function like the pipes in SMB3 where Mario simply goes through the pipe. It doesn't work correctly in NSMBW, but it's been fixed in Newer SMBW and some other mods.")
        self.connectedPipeCheckbox.clicked.connect(self.HandleConnectedPipeClicked)

        self.pathID = QtWidgets.QSpinBox()
        self.pathID.setRange(0, 255)
        self.pathID.setToolTip('<b>Path ID:</b><br>Use this option to set the path number that the connected pipe will follow.')
        self.pathID.valueChanged.connect(self.HandlePathIDChanged)

        self.connectedPipeReverseCheckbox = QtWidgets.QCheckBox('Reverse')
        self.connectedPipeReverseCheckbox.setToolTip("<b>Reverse:</b><br>This must be checked on the entrance at the end of the path.")
        self.connectedPipeReverseCheckbox.clicked.connect(self.HandleConnectedPipeReverseClicked)

        self.connectedPipeDirection = QtWidgets.QComboBox()
        self.connectedPipeDirection.addItems(['Up', 'Down', 'Left', 'Right'])
        self.connectedPipeDirection.setToolTip('<b>Direction of Other Side (Newer SMBW):</b><br>Sets the direction the player will exit out of the other side of the connected pipe. (This should match the other entrance\'s "Type" setting.)<br><br>This is a custom setting invented by Newer SMBW. NSMBW just uses the other entrance\'s "Type" setting directly.')
        self.connectedPipeDirection.activated.connect(self.HandleConnectedPipeDirectionChanged)

        # create a layout
        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        # First part: "Editing Entrance #" label, and Type box
        self.editingLabel = QtWidgets.QLabel('-')
        layout.addWidget(self.editingLabel, 0, 0, 1, 4, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addWidget(QtWidgets.QLabel('Type:'), 1, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.entranceType, 1, 1, 1, 3)
        layout.addWidget(createHorzLine(), 2, 0, 1, 4)

        # Second part: other general settings
        layout.addWidget(QtWidgets.QLabel('ID:'), 3, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.entranceID, 3, 1)
        layout.addWidget(QtWidgets.QLabel('Active on:'), 4, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.activeLayer, 4, 1)
        layout.addWidget(self.enterableCheckbox, 5, 0, 1, 2, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.unknownFlagCheckbox, 6, 0, 1, 2, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.destEntranceLabel, 3, 2, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.destEntrance, 3, 3)
        layout.addWidget(self.destAreaLabel, 4, 2, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.destArea, 4, 3)
        layout.addWidget(self.sendToEntrance, 5, 2, 1, 2)
        layout.addWidget(self.sendToWorldMap, 6, 2, 1, 2)

        # Third part: type-specific settings groupbox
        self.typeSpecificSettingsGroup = QtWidgets.QGroupBox('Type-Specific Settings')
        tssLayout = QtWidgets.QGridLayout(self.typeSpecificSettingsGroup)
        layout.addWidget(self.typeSpecificSettingsGroup, 7, 0, 1, 4)

        tssLayout.addWidget(self.spawnHalfTileLeftCheckbox, 0, 0, 1, 4, QtCore.Qt.AlignmentFlag.AlignLeft)
        tssLayout.addWidget(self.forwardPipeCheckbox, 1, 0, 1, 2, QtCore.Qt.AlignmentFlag.AlignRight)
        tssLayout.addWidget(self.connectedPipeCheckbox, 1, 2, 1, 2, QtCore.Qt.AlignmentFlag.AlignRight)

        self.connectedPipeGroup = QtWidgets.QGroupBox('Connected Pipe Settings')
        cpLayout = QtWidgets.QGridLayout(self.connectedPipeGroup)
        tssLayout.addWidget(self.connectedPipeGroup, 2, 0, 1, 4)

        cpLayout.addWidget(QtWidgets.QLabel('Path ID:'), 0, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        cpLayout.addWidget(self.pathID, 0, 1)
        cpLayout.addWidget(self.connectedPipeReverseCheckbox, 0, 2, 1, 2, QtCore.Qt.AlignmentFlag.AlignRight)
        cpLayout.addWidget(QtWidgets.QLabel('Direction of Other Side (Newer SMBW):'), 1, 0, 1, 3, QtCore.Qt.AlignmentFlag.AlignRight)
        cpLayout.addWidget(self.connectedPipeDirection, 1, 3)

        self.ent = None
        self.UpdateFlag = False


    def setEntrance(self, ent):
        """Change the entrance being edited by the editor, update all fields"""
        if self.ent == ent: return

        self.updateTitle(ent.entid)
        self.ent = ent
        self.UpdateFlag = True

        if ent.enttype < 0: ent.enttype = 0
        if ent.enttype >= len(EntranceTypeNames): ent.enttype = len(EntranceTypeNames) - 1
        if ent.entlayer < 0: ent.entlayer = 0
        if ent.entlayer >= 3: ent.entlayer = 2

        self.entranceType.setCurrentIndex(ent.enttype)

        self.entranceID.setValue(ent.entid)
        self.activeLayer.setCurrentIndex(ent.entlayer)

        self.destEntrance.setValue(ent.destentrance)
        self.destArea.setValue(ent.destarea)
        self.sendToEntrance.setChecked(ent.exittomap == 0)
        self.sendToWorldMap.setChecked(ent.exittomap != 0)

        self.enterableCheckbox.setChecked((ent.entsettings & 0x80) == 0)
        self.unknownFlagCheckbox.setChecked((ent.entsettings & 2) != 0)

        self.spawnHalfTileLeftCheckbox.setChecked((ent.entsettings & 0x40) != 0)

        self.forwardPipeCheckbox.setChecked((ent.entsettings & 4) != 0)

        self.connectedPipeCheckbox.setChecked((ent.entsettings & 8) != 0)

        self.pathID.setValue(ent.entpath)
        self.connectedPipeReverseCheckbox.setChecked((ent.entsettings & 1) != 0)
        self.connectedPipeDirection.setCurrentIndex(ent.cpdirection)

        self.updateWidgetVisibilities(ent.enttype, ent.entsettings, ent.exittomap)

        self.UpdateFlag = False


    def updateTitle(self, id):
        """Update the title label with the entrance ID"""
        self.editingLabel.setText('<b>Editing Entrance %d:</b>' % id)


    def updateWidgetVisibilities(self, type, settings, exitToWorldMap):
        """Update visibility for all widgets as necessary"""
        self.destEntranceLabel.setVisible(not exitToWorldMap)
        self.destEntrance.setVisible(not exitToWorldMap)
        self.destAreaLabel.setVisible(not exitToWorldMap)
        self.destArea.setVisible(not exitToWorldMap)

        self.typeSpecificSettingsGroup.setVisible(type in (self.CanUseFlag40 | self.CanUseFlag8 | self.CanUseFlag4))

        self.spawnHalfTileLeftCheckbox.setVisible(type in self.CanUseFlag40)

        self.forwardPipeCheckbox.setVisible(type in self.CanUseFlag4)
        self.connectedPipeCheckbox.setVisible(type in self.CanUseFlag8)

        self.connectedPipeGroup.setVisible(type in self.CanUseFlag8 and ((settings & 8) != 0))


    @QtCoreSlot(int)
    def HandleEntranceIDChanged(self, i):
        """Handler for the entrance ID changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.entid = i
        self.ent.update()
        self.ent.UpdateTooltip()
        self.ent.listitem.setText(self.ent.ListString())
        self.updateTitle(i)


    @QtCoreSlot(int)
    def HandleEntranceTypeChanged(self, i):
        """Handler for the entrance type changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.enttype = i
        self.ent.UpdateRects()
        self.ent.update()
        self.ent.UpdateTooltip()
        self.ent.listitem.setText(self.ent.ListString())
        self.updateWidgetVisibilities(i, self.ent.entsettings, self.ent.exittomap)


    @QtCoreSlot(int)
    def HandleDestAreaChanged(self, i):
        """Handler for the destination area changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.destarea = i
        self.ent.UpdateTooltip()
        self.ent.listitem.setText(self.ent.ListString())


    @QtCoreSlot(int)
    def HandleDestEntranceChanged(self, i):
        """Handler for the destination entrance changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.destentrance = i
        self.ent.UpdateTooltip()
        self.ent.listitem.setText(self.ent.ListString())


    @QtCoreSlot(bool)
    def HandleEnterableClicked(self, checked):
        """Handle for the Enterable checkbox being clicked"""
        if self.UpdateFlag: return
        SetDirty()
        if not checked:
            self.ent.entsettings |= 0x80
        else:
            self.ent.entsettings &= ~0x80
        self.ent.UpdateTooltip()
        self.ent.listitem.setText(self.ent.ListString())


    @QtCoreSlot(bool)
    def HandleUnknownFlagClicked(self, checked):
        """Handle for the Unknown Flag checkbox being clicked"""
        if self.UpdateFlag: return
        SetDirty()
        if checked:
            self.ent.entsettings |= 2
        else:
            self.ent.entsettings &= ~2


    @QtCoreSlot(int)
    def SendToEntranceOrWMChanged(self, id):
        """Handle for the "Send to Entrance"/"Send to World Map" setting being changed"""
        if self.UpdateFlag: return
        SetDirty()
        if id != 0:
            self.ent.exittomap = 1
        else:
            self.ent.exittomap = 0
        self.ent.UpdateTooltip()
        self.updateWidgetVisibilities(self.ent.enttype, self.ent.entsettings, self.ent.exittomap)


    @QtCoreSlot(bool)
    def HandleSpawnHalfTileLeftClicked(self, checked):
        """Handle for the Spawn Half a Tile Left checkbox being clicked"""
        if self.UpdateFlag: return
        SetDirty()
        if checked:
            self.ent.entsettings |= 0x40
        else:
            self.ent.entsettings &= ~0x40


    @QtCoreSlot(bool)
    def HandleConnectedPipeClicked(self, checked):
        """Handle for the connected pipe checkbox being clicked"""
        if self.UpdateFlag: return
        SetDirty()
        if checked:
            self.ent.entsettings |= 8
        else:
            self.ent.entsettings &= ~8
        self.updateWidgetVisibilities(self.ent.enttype, self.ent.entsettings, self.ent.exittomap)

    @QtCoreSlot(bool)
    def HandleConnectedPipeReverseClicked(self, checked):
        """Handle for the connected pipe reverse checkbox being clicked"""
        if self.UpdateFlag: return
        SetDirty()
        if checked:
            self.ent.entsettings |= 1
        else:
            self.ent.entsettings &= ~1

    @QtCoreSlot(int)
    def HandlePathIDChanged(self, i):
        """Handler for the path ID changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.entpath = i

    @QtCoreSlot(bool)
    def HandleForwardPipeClicked(self, checked):
        """Handle for the forward pipe checkbox being clicked"""
        if self.UpdateFlag: return
        SetDirty()
        if checked:
            self.ent.entsettings |= 4
        else:
            self.ent.entsettings &= ~4

    @QtCoreSlot(int)
    def HandleActiveLayerChanged(self, i):
        """Handler for the active layer changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.entlayer = i

    @QtCore.pyqtSlot(int)
    def HandleConnectedPipeDirectionChanged(self, i):
        """Handler for connected-pipe direction changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.ent.cpdirection = i


class PathNodeEditorWidget(QtWidgets.QWidget):
    """Widget for editing path node properties"""

    def __init__(self):
        """Constructor"""
        super(PathNodeEditorWidget, self).__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed))


        # create widgets
        #[20:52:41]  [Angel-SL] 1. (readonly) pathid 2. (readonly) nodeid 3. x 4. y 5. speed (float spinner) 6. accel (float spinner)
        #not doing [20:52:58]  [Angel-SL] and 2 buttons - 7. "Move Up" 8. "Move Down"
        self.speed = QtWidgets.QDoubleSpinBox()
        self.speed.setRange(-FLT_MAX, FLT_MAX)
        self.speed.setToolTip('<b>Speed:</b><br>Unknown units. Mess around and report your findings!')
        self.speed.setDecimals(FLT_DIG)
        self.speed.valueChanged.connect(self.HandleSpeedChanged)

        self.accel = QtWidgets.QDoubleSpinBox()
        self.accel.setRange(-FLT_MAX, FLT_MAX)
        self.accel.setToolTip('<b>Accel:</b><br>Unknown units. Mess around and report your findings!')
        self.accel.setDecimals(FLT_DIG)
        self.accel.valueChanged.connect(self.HandleAccelChanged)

        self.delay = QtWidgets.QSpinBox()
        self.delay.setRange(0, 65535)
        self.delay.setToolTip('<b>Delay:</b><br>Amount of time to stop here (at this node) before continuing to next node. Unit is 1/60 of a second (60 for 1 second)')
        self.delay.valueChanged.connect(self.HandleDelayChanged)

        self.loops = QtWidgets.QCheckBox()
        self.loops.setToolTip('<b>Loops:</b><br>Anything following this path will wait for any delay set at the last node and then proceed back in a straight line to the first node, and continue.')
        self.loops.stateChanged.connect(self.HandleLoopsChanged)

        # create a layout
        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        # "Editing Entrance #" label
        self.editingLabel = QtWidgets.QLabel('-')
        self.editingPathLabel = QtWidgets.QLabel('-')
        layout.addWidget(self.editingLabel, 3, 0, 1, 4, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.editingPathLabel, 0, 0, 1, 4, QtCore.Qt.AlignmentFlag.AlignTop)
        # add labels
        layout.addWidget(QtWidgets.QLabel('Loops:'), 1, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QtWidgets.QLabel('Speed:'), 4, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QtWidgets.QLabel('Accel:'), 5, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QtWidgets.QLabel('Delay:'), 6, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(createHorzLine(), 2, 0, 1, -1)

        # add the widgets

        layout.addWidget(self.loops, 1, 1, 1, -1)
        layout.addWidget(self.speed, 4, 1, 1, -1)
        layout.addWidget(self.accel, 5, 1, 1, -1)
        layout.addWidget(self.delay, 6, 1, 1, -1)


        self.path = None
        self.UpdateFlag = False


    def setPath(self, path):
        """Change the path node being edited by the editor, update all fields"""
        if self.path == path: return
        self.editingPathLabel.setText('<b>Editing Path %d</b>' % (path.pathid))
        self.editingLabel.setText('<b>Editing Node %d</b>' % (path.nodeid))
        self.path = path
        self.UpdateFlag = True

        self.speed.setValue(path.nodeinfo['speed'])
        self.accel.setValue(path.nodeinfo['accel'])
        self.delay.setValue(path.nodeinfo['delay'])
        self.loops.setChecked(path.pathinfo['loops'])

        self.UpdateFlag = False


    @QtCoreSlot(float)
    def HandleSpeedChanged(self, i):
        """Handler for the speed changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.path.nodeinfo['speed'] = i



    @QtCoreSlot(float)
    def HandleAccelChanged(self, i):
        """Handler for the accel changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.path.nodeinfo['accel'] = i


    @QtCoreSlot(int)
    def HandleDelayChanged(self, i):
        """Handler for the 2nd unk changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.path.nodeinfo['delay'] = i

    @QtCoreSlot(int)
    def HandleLoopsChanged(self, i):
        if self.UpdateFlag: return
        SetDirty()
        self.path.pathinfo['loops'] = (i == QtCore.Qt.CheckState.Checked)
        self.path.pathinfo['peline'].update()



class LocationEditorWidget(QtWidgets.QWidget):
    """Widget for editing location properties"""

    def __init__(self):
        """Constructor"""
        super(LocationEditorWidget, self).__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed))

        # create widgets
        self.locationID = QtWidgets.QSpinBox()
        self.locationID.setToolTip('<b>ID:</b><br>Must be different from all other IDs')
        self.locationID.setRange(0, 255)
        self.locationID.valueChanged.connect(self.HandleLocationIDChanged)

        self.locationX = QtWidgets.QSpinBox()
        self.locationX.setToolTip('<b>X pos:</b><br>Specifies the X position of the location')
        self.locationX.setRange(16, 65535)
        self.locationX.valueChanged.connect(self.HandleLocationXChanged)

        self.locationY = QtWidgets.QSpinBox()
        self.locationY.setToolTip('<b>Y pos:</b><br>Specifies the Y position of the location')
        self.locationY.setRange(16, 65535)
        self.locationY.valueChanged.connect(self.HandleLocationYChanged)

        self.locationWidth = QtWidgets.QSpinBox()
        self.locationWidth.setToolTip('<b>Width:</b><br>Specifies the width of the location')
        self.locationWidth.setRange(0, 65535)
        self.locationWidth.valueChanged.connect(self.HandleLocationWidthChanged)

        self.locationHeight = QtWidgets.QSpinBox()
        self.locationHeight.setToolTip('<b>Height:</b><br>Specifies the height of the location')
        self.locationHeight.setRange(0, 65535)
        self.locationHeight.valueChanged.connect(self.HandleLocationHeightChanged)

        self.snapButton = QtWidgets.QPushButton('Snap to Grid')
        self.snapButton.clicked.connect(self.HandleSnapToGrid)

        # create a layout
        layout = QtWidgets.QGridLayout()
        self.setLayout(layout)

        # "Editing Location #" label
        self.editingLabel = QtWidgets.QLabel('-')
        layout.addWidget(self.editingLabel, 0, 0, 1, 4, QtCore.Qt.AlignmentFlag.AlignTop)

        # add labels
        layout.addWidget(QtWidgets.QLabel('ID:'), 1, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)

        layout.addWidget(createHorzLine(), 2, 0, 1, 4)

        layout.addWidget(QtWidgets.QLabel('X pos:'), 3, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QtWidgets.QLabel('Y pos:'), 4, 0, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)

        layout.addWidget(QtWidgets.QLabel('Width:'), 3, 2, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QtWidgets.QLabel('Height:'), 4, 2, 1, 1, QtCore.Qt.AlignmentFlag.AlignRight)

        # add the widgets
        layout.addWidget(self.locationID, 1, 1, 1, 1)
        layout.addWidget(self.snapButton, 1, 3, 1, 1)

        layout.addWidget(self.locationX, 3, 1, 1, 1)
        layout.addWidget(self.locationY, 4, 1, 1, 1)

        layout.addWidget(self.locationWidth, 3, 3, 1, 1)
        layout.addWidget(self.locationHeight, 4, 3, 1, 1)


        self.loc = None
        self.UpdateFlag = False


    def setLocation(self, loc):
        """Change the location being edited by the editor, update all fields"""
        if self.UpdateFlag: return
        self.loc = loc
        self.UpdateFlag = True

        self.FixTitle()
        self.locationID.setValue(loc.id)
        self.locationX.setValue(loc.objx)
        self.locationY.setValue(loc.objy)
        self.locationWidth.setValue(int(loc.width))
        self.locationHeight.setValue(int(loc.height))

        self.UpdateFlag = False


    def FixTitle(self):
        self.editingLabel.setText('<b>Editing Location %d:</b>' % (self.loc.id))


    @QtCoreSlot(int)
    def HandleLocationIDChanged(self, i):
        """Handler for the location ID changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.loc.id = i
        self.loc.update()
        self.loc.UpdateTitle()
        self.FixTitle()

    @QtCoreSlot(int)
    def HandleLocationXChanged(self, i):
        """Handler for the location X-pos changing"""
        global OverrideSnapping
        if self.UpdateFlag: return

        self.UpdateFlag = True
        OverrideSnapping = True

        SetDirty()
        self.loc.setX(int(i*1.5))
        self.loc.objx = i
        self.loc.UpdateRects()
        self.loc.update()

        self.UpdateFlag = False
        OverrideSnapping = False

    @QtCoreSlot(int)
    def HandleLocationYChanged(self, i):
        """Handler for the location Y-pos changing"""
        global OverrideSnapping
        if self.UpdateFlag: return

        self.UpdateFlag = True
        OverrideSnapping = True

        SetDirty()
        self.loc.setY(int(i*1.5))
        self.loc.objy = i
        self.loc.UpdateRects()
        self.loc.update()

        self.UpdateFlag = False
        OverrideSnapping = False

    @QtCoreSlot(int)
    def HandleLocationWidthChanged(self, i):
        """Handler for the location width changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.loc.width = i
        self.loc.UpdateRects()
        self.loc.update()

    @QtCoreSlot(int)
    def HandleLocationHeightChanged(self, i):
        """Handler for the location height changing"""
        if self.UpdateFlag: return
        SetDirty()
        self.loc.height = i
        self.loc.UpdateRects()
        self.loc.update()

    @QtCoreSlot()
    def HandleSnapToGrid(self):
        """Snaps the current location to an 8x8 grid"""
        SetDirty()

        loc = self.loc
        left = loc.objx
        top = loc.objy
        right = left+loc.width
        bottom = top+loc.height

        if left % 8 < 4:
            left -= (left % 8)
        else:
            left += 8 - (left % 8)

        if top % 8 < 4:
            top -= (top % 8)
        else:
            top += 8 - (top % 8)

        if right % 8 < 4:
            right -= (right % 8)
        else:
            right += 8 - (right % 8)

        if bottom % 8 < 4:
            bottom -= (bottom % 8)
        else:
            bottom += 8 - (bottom % 8)

        if right <= left: right += 8
        if bottom <= top: bottom += 8

        loc.objx = left
        loc.objy = top
        loc.width = right - left
        loc.height = bottom - top

        loc.setPos(int(left*1.5), int(top*1.5))
        loc.UpdateRects()
        loc.update()
        self.setLocation(loc) # updates the fields


class ItemEditorDockWidget(QtWidgets.QDockWidget):
    """DockWidget subclass that switches between show/hide and
    enable/disable depending on docking status"""
    def __init__(self, *args, **kwargs):
        super(ItemEditorDockWidget, self).__init__(*args, **kwargs)
        self.topLevelChanged.connect(self.handleTopLevelChanged)

        # During the very first launch (empty QSettings), the following
        # sequence of things happens:
        # - initialization
        # - .setActive(False), with self.isFloating() == False
        # - .handleTopLevelChanged(True)
        # Normally, we would leave the widget visible in that case,
        # since it corresponds to the user undocking an item editor for
        # an unselected type of object (and of course they'd want to
        # continue to see it while they drag it somewhere). But during
        # the first launch, all of the docks need to be hidden. So we
        # special-case this using self.initialSetup.
        self.initialSetup = (not settings.contains('MainWindowGeometry'))

    def handleTopLevelChanged(self, topLevel):
        if self.initialSetup:
            self.setVisible(False)
            self.initialSetup = False

    def setActive(self, active):
        if self.isFloating():
            self.setVisible(active)
            self.setEnabled(True)
        else:
            self.setVisible(True)
            self.setEnabled(active)

    def isActive(self):
        if self.isFloating():
            return self.isVisible()
        else:
            return self.isEnabled()


class LevelScene(QtWidgets.QGraphicsScene):
    """GraphicsScene subclass for the level scene"""
    def __init__(self, *args):
        if DarkMode:
            bgcolor = QtGui.QColor.fromRgb(32, 32, 32)
        else:
            bgcolor = QtGui.QColor.fromRgb(119,136,153)
        self.bgbrush = QtGui.QBrush(bgcolor)
        super(LevelScene, self).__init__(*args)

    def drawBackground(self, painter, rect):
        """Draws all visible tiles"""
        painter.fillRect(rect, self.bgbrush)
        if not hasattr(Level, 'layers'): return

        drawrect = QtCore.QRectF(rect.x() / 24, rect.y() / 24, rect.width() / 24 + 1, rect.height() / 24 + 1)
        #print('painting ' + repr(drawrect))
        isect = drawrect.intersects

        layer0 = []
        layer1 = []
        layer2 = []

        local_Tiles = Tiles

        x1 = 1024
        y1 = 512
        x2 = 0
        y2 = 0

        # iterate through each object
        funcs = [layer0.append, layer1.append, layer2.append]
        show = [ShowLayer0, ShowLayer1, ShowLayer2]
        for layer, add, process in zip(Level.layers, funcs, show):
            if not process: continue
            for item in layer:
                if not isect(item.LevelRect): continue
                add(item)
                xs = item.objx
                xe = xs+item.width
                ys = item.objy
                ye = ys+item.height
                if xs < x1: x1 = xs
                if xe > x2: x2 = xe
                if ys < y1: y1 = ys
                if ye > y2: y2 = ye

        width = x2 - x1
        height = y2 - y1

        # create and draw the tilemaps
        for layer in [layer2, layer1, layer0]:
            if not layer:
                continue

            tmap = [([-1] * width) for _ in range(height)]

            for item in layer:
                startx = item.objx - x1
                desty = item.objy - y1

                for row in item.objdata:
                    destrow = tmap[desty]
                    destx = startx
                    for tile in row:
                        if tile is None or tile > 0:
                            destrow[destx] = tile
                        destx += 1
                    desty += 1

            painter.save()
            painter.translate(x1*24, y1*24)
            drawPixmap = painter.drawPixmap
            desty = 0
            for row in tmap:
                destx = 0
                for tile in row:
                    if tile is None:
                        # Magenta/black checkerboard for tiles from nonexistent objects
                        painter.fillRect(destx, desty, 24, 24, QtGui.QColor.fromRgb(192, 0, 192))
                        painter.fillRect(destx + 12, desty, 12, 12, QtCore.Qt.GlobalColor.black)
                        painter.fillRect(destx, desty + 12, 12, 12, QtCore.Qt.GlobalColor.black)
                    elif tile > 0 and local_Tiles[tile] is not None:
                        drawPixmap(destx, desty, local_Tiles[tile])
                    destx += 24
                desty += 24
            painter.restore()



class LevelViewWidget(QtWidgets.QGraphicsView):
    """GraphicsView subclass for the level view"""
    PositionHover = QtCoreSignal(int, int)
    FrameSize = QtCoreSignal(int, int)

    def __init__(self, scene, parent):
        """Constructor"""
        super(LevelViewWidget, self).__init__(scene, parent)

        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        #self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor.fromRgb(119,136,153)))
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        #self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setMouseTracking(True)
        #self.setOptimizationFlags(QtWidgets.QGraphicsView.IndirectPainting)
        self.YScrollBar = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Vertical, parent)
        self.XScrollBar = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal, parent)
        self.setVerticalScrollBar(self.YScrollBar)
        self.setHorizontalScrollBar(self.XScrollBar)

        self.currentobj = None
        self.lastCursorPosForMidButtonScroll = None
        self.cursorEdgeScrollTimer = None


    def mousePressEvent(self, event):
        """Overrides mouse pressing events if needed"""

        if event.buttons() & QtCore.Qt.MouseButton.MiddleButton or event.buttons() & QtCore.Qt.MouseButton.RightButton:
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)

        if event.buttons() & QtCore.Qt.MouseButton.RightButton and not (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            if CurrentPaintType <= 3 and CurrentObject != -1:
                # paint an object
                clicked = mainWindow.view.mapToScene(qm(event).position().toPoint())
                if clicked.x() < 0: clicked.setX(0)
                if clicked.y() < 0: clicked.setY(0)
                clickedx = int(clicked.x() / 24)
                clickedy = int(clicked.y() / 24)

                ln = CurrentLayer
                layer = Level.layers[CurrentLayer]
                if len(layer) == 0:
                    z = (2 - ln) * 8192
                else:
                    z = layer[-1].zValue() + 1

                obj = LevelObjectEditorItem(CurrentPaintType, CurrentObject, ln, clickedx, clickedy, 1, 1, z)
                layer.append(obj)
                mw = mainWindow
                obj.positionChanged = mw.HandleObjPosChange
                mw.scene.addItem(obj)

                self.currentobj = obj
                self.dragstartx = clickedx
                self.dragstarty = clickedy
                SetDirty()

            elif CurrentPaintType == 4 and CurrentSprite != -1:
                # common stuff
                clicked = mainWindow.view.mapToScene(qm(event).position().toPoint())
                if clicked.x() < 0: clicked.setX(0)
                if clicked.y() < 0: clicked.setY(0)

                if CurrentSprite == 1000:
                    # paint a location
                    clickedx = int(clicked.x() / 1.5)
                    clickedy = int(clicked.y() / 1.5)

                    allID = []
                    newID = 1
                    for i in Level.locations:
                        allID.append(i.id)

                    allID = set(allID) # faster "x in y" lookups for sets

                    while newID <= 255:
                        if newID not in allID:
                            break
                        newID += 1

                    global OverrideSnapping
                    OverrideSnapping = True
                    loc = LocationEditorItem(clickedx, clickedy, 4, 4, newID)
                    OverrideSnapping = False

                    mw = mainWindow
                    loc.positionChanged = mw.HandleLocPosChange
                    loc.sizeChanged = mw.HandleLocSizeChange
                    mw.scene.addItem(loc)

                    Level.locations.append(loc)

                    self.currentobj = loc
                    self.dragstartx = clickedx
                    self.dragstarty = clickedy

                elif CurrentSprite >= 0: # fixes a bug -Treeki
                    #[18:15:36]  Angel-SL: I found a bug in Reggie
                    #[18:15:42]  Angel-SL: you can paint a "No sprites found"
                    #[18:15:47]  Angel-SL: results in a sprite -2

                    # paint a sprite
                    #clickedx = int((clicked.x()) / 1.5)
                    #clickedy = int((clicked.y()) / 1.5)
                    #print('clicked on %d,%d divided to %d,%d' % (clicked.x(),clicked.y(),clickedx,clickedy))

                    clickedx = int((clicked.x() - 12) / 12) * 8
                    clickedy = int((clicked.y() - 12) / 12) * 8

                    data = mainWindow.defaultDataEditor.data
                    spr = SpriteEditorItem(CurrentSprite, clickedx, clickedy, data)

                    #clickedx -= int(spr.xsize / 2)
                    #clickedy -= int(spr.ysize / 2)
                    #print('subtracted %d,%d for %d,%d' % (int(spr.xsize/2),int(spr.ysize/2),clickedx,clickedy))
                    #newX = int((int(clickedx / 8) * 12) + (spr.xoffset * 1.5))
                    #newY = int((int(clickedy / 8) * 12) + (spr.yoffset * 1.5))
                    #print('offset is %d,%d' % (spr.xoffset,spr.yoffset))
                    #print('moving to %d,%d' % (newX,newY))
                    #spr.setPos(newX, newY)
                    #print('ended up at %d,%d' % (spr.x(),spr.y()))

                    mw = mainWindow
                    spr.positionChanged = mw.HandleSprPosChange
                    mw.scene.addItem(spr)

                    Level.sprites.append(spr)

                    self.currentobj = spr
                    self.dragstartx = clickedx
                    self.dragstarty = clickedy


                SetDirty()

            elif CurrentPaintType == 5:
                # paint an entrance
                clicked = mainWindow.view.mapToScene(qm(event).position().toPoint())
                if clicked.x() < 0: clicked.setX(0)
                if clicked.y() < 0: clicked.setY(0)
                clickedx = int((clicked.x() - 12) / 1.5)
                clickedy = int((clicked.y() - 12) / 1.5)
                #print('%d,%d %d,%d' % (clicked.x(), clicked.y(), clickedx, clickedy))

                getids = [False for x in range(256)]
                for ent in Level.entrances: getids[ent.entid] = True
                minimumID = getids.index(False)

                ent = EntranceEditorItem(clickedx, clickedy, minimumID, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                mw = mainWindow
                ent.positionChanged = mw.HandleEntPosChange
                mw.scene.addItem(ent)

                elist = mw.entranceList
                # if it's the first available ID, all the other indexes should match right?
                # so I can just use the ID to insert
                ent.listitem = QtWidgets.QListWidgetItem(ent.ListString())
                elist.insertItem(minimumID, ent.listitem)

                global PaintingEntrance, PaintingEntranceListIndex
                PaintingEntrance = ent
                PaintingEntranceListIndex = minimumID

                Level.entrances.insert(minimumID, ent)

                self.currentobj = ent
                self.dragstartx = clickedx
                self.dragstarty = clickedy
                SetDirty()
            elif CurrentPaintType == 6:
                # paint a pathnode
                clicked = mainWindow.view.mapToScene(qm(event).position().toPoint())
                if clicked.x() < 0: clicked.setX(0)
                if clicked.y() < 0: clicked.setY(0)
                clickedx = int((clicked.x() - 12) / 1.5)
                clickedy = int((clicked.y() - 12) / 1.5)
                #print('%d,%d %d,%d' % (clicked.x(), clicked.y(), clickedx, clickedy))
                mw = mainWindow
                plist = mw.pathList
                selectedpn = None if len(plist.selectedItems()) < 1 else plist.selectedItems()[0]
                #if selectedpn is None:
                #    QtWidgets.QMessageBox.warning(None, 'Error', 'No pathnode selected. Select a pathnode of the path you want to create a new node in.')
                if selectedpn is None:
                    getids = [False for x in range(256)]
                    getids[0] = True
                    for pathdatax in Level.pathdata:
                        #if len(pathdatax['nodes']) > 0:
                        getids[int(pathdatax['id'])] = True

                    newpathid = getids.index(False)
                    newpathdata = { 'id': newpathid,
                                   'nodes': [{'x':clickedx, 'y':clickedy, 'speed':0.5, 'accel':0.00498, 'delay':0}],
                                   'loops': False
                    }
                    Level.pathdata.append(newpathdata)
                    newnode = PathEditorItem(clickedx, clickedy, None, None, newpathdata, newpathdata['nodes'][0])
                    newnode.positionChanged = mw.HandlePathPosChange

                    mw.scene.addItem(newnode)

                    peline = PathEditorLineItem(newpathdata['nodes'])
                    newpathdata['peline'] = peline
                    mw.scene.addItem(peline)

                    Level.pathdata.sort(key=lambda path: int(path['id']))



                    newnode.listitem = QtWidgets.QListWidgetItem(newnode.ListString())
                    plist.clear()
                    for fpath in Level.pathdata:
                        for fpnode in fpath['nodes']:
                            fpnode['graphicsitem'].listitem = QtWidgets.QListWidgetItem(fpnode['graphicsitem'].ListString())
                            plist.addItem(fpnode['graphicsitem'].listitem)
                            fpnode['graphicsitem'].updateId()
                    newnode.listitem.setSelected(True)
                    Level.paths.append(newnode)
                    self.currentobj = newnode
                    self.dragstartx = clickedx
                    self.dragstarty = clickedy
                    SetDirty()
                else:
                    pathd = None
                    for pathnode in Level.paths:
                        if pathnode.listitem == selectedpn:
                            pathd = pathnode.pathinfo

                    if pathd is None: return # shouldn't happen

                    pathid = pathd['id']
                    newnodedata = {'x':clickedx, 'y':clickedy, 'speed':0.5, 'accel':0.00498,'delay':0}
                    pathd['nodes'].append(newnodedata)
                    nodeid = pathd['nodes'].index(newnodedata)


                    newnode = PathEditorItem(clickedx, clickedy, None, None, pathd, newnodedata)

                    newnode.positionChanged = mw.HandlePathPosChange
                    mw.scene.addItem(newnode)

                    newnode.listitem = QtWidgets.QListWidgetItem(newnode.ListString())
                    plist.clear()
                    for fpath in Level.pathdata:
                        for fpnode in fpath['nodes']:
                            fpnode['graphicsitem'].listitem = QtWidgets.QListWidgetItem(fpnode['graphicsitem'].ListString())
                            plist.addItem(fpnode['graphicsitem'].listitem)
                            fpnode['graphicsitem'].updateId()
                    newnode.listitem.setSelected(True)
                    #global PaintingEntrance, PaintingEntranceListIndex
                    #PaintingEntrance = ent
                    #PaintingEntranceListIndex = minimumID

                    Level.paths.append(newnode)
                    pathd['peline'].nodePosChanged()
                    self.currentobj = newnode
                    self.dragstartx = clickedx
                    self.dragstarty = clickedy
                    SetDirty()

            event.accept()

        elif (event.button() == QtCore.Qt.MouseButton.LeftButton) and (QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.ShiftModifier):
            mw = mainWindow

            pos = mw.view.mapToScene(qm(event).position().toPoint())
            addsel = mw.scene.items(pos)
            for i in addsel:
                if i.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                    i.setSelected(not i.isSelected())
                    break

        elif event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self.lastCursorPosForMidButtonScroll = event.pos()
            QtWidgets.QGraphicsView.mousePressEvent(self, event)

        else:
            QtWidgets.QGraphicsView.mousePressEvent(self, event)
        mainWindow.levelOverview.update()


    def resizeEvent(self, event):
        """Catches resize events"""
        self.FrameSize.emit(event.size().width(), event.size().height())
        event.accept()
        QtWidgets.QGraphicsView.resizeEvent(self, event)


    def mouseMoveEvent(self, event):
        """Overrides mouse movement events if needed"""

        pos = mainWindow.view.mapToScene(qm(event).position().toPoint())
        if pos.x() < 0: pos.setX(0)
        if pos.y() < 0: pos.setY(0)
        self.PositionHover.emit(int(pos.x()), int(pos.y()))

        if ((event.buttons() & (QtCore.Qt.MouseButton.LeftButton | QtCore.Qt.MouseButton.RightButton))
                and not self.cursorEdgeScrollTimer):
            # We set this up here instead of in mousePressEvent because
            # otherwise the view would jerk to one side if you clicked
            # near its edge. This way, it'll only scroll if you click
            # and drag.
            self.cursorEdgeScrollTimer = QtCore.QTimer()
            self.cursorEdgeScrollTimer.timeout.connect(self.scrollIfCursorNearEdge)
            self.cursorEdgeScrollTimer.start(1000 // 60)  # ~ 60 fps

        if self.updatePaintDraggedItems():
            event.accept()

        elif event.buttons() == QtCore.Qt.MouseButton.MiddleButton and self.lastCursorPosForMidButtonScroll is not None:
            # https://stackoverflow.com/a/15785851
            delta = event.pos() - self.lastCursorPosForMidButtonScroll
            self.XScrollBar.setValue(self.XScrollBar.value() + (delta.x() if self.isRightToLeft() else -delta.x()))
            self.YScrollBar.setValue(self.YScrollBar.value() - delta.y())
            self.lastCursorPosForMidButtonScroll = event.pos()

        else:
            QtWidgets.QGraphicsView.mouseMoveEvent(self, event)


    def mouseReleaseEvent(self, event):
        """Overrides mouse release events if needed"""
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.currentobj = None

        if (not event.buttons() & QtCore.Qt.MouseButton.MiddleButton) and (not event.buttons() & QtCore.Qt.MouseButton.RightButton):
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)

        if self.cursorEdgeScrollTimer:
            self.cursorEdgeScrollTimer.stop()
            self.cursorEdgeScrollTimer = None

        QtWidgets.QGraphicsView.mouseReleaseEvent(self, event)


    def wheelEvent(self, event):
        """Handle wheel events for zooming in/out"""
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            if QtCompatVersion >= (5,0,0):
                angleDelta = event.angleDelta().y()
            else:
                angleDelta = event.delta()

            if angleDelta > 0:
                mainWindow.HandleZoomIn(towardsCursor=True)
            else:
                mainWindow.HandleZoomOut(towardsCursor=True)

        else:
            QtWidgets.QGraphicsView.wheelEvent(self, event)


    def updatePaintDraggedItems(self):
        """Update items that are being paint-dragged (painted with
        right-click, and dragged while it's still held down). Returns
        True if any items are being paint-dragged, False otherwise"""
        if app.mouseButtons() != QtCore.Qt.MouseButton.RightButton or self.currentobj is None:
            return False

        obj = self.currentobj

        # possibly a small optimisation
        type_obj = LevelObjectEditorItem
        type_spr = SpriteEditorItem
        type_ent = EntranceEditorItem
        type_loc = LocationEditorItem
        type_path = PathEditorItem

        if isinstance(obj, type_obj):
            # resize/move the current object
            cx = obj.objx
            cy = obj.objy
            cwidth = obj.width
            cheight = obj.height

            dsx = self.dragstartx
            dsy = self.dragstarty
            clicked = mainWindow.view.mapToScene(self.mapFromGlobal(QtGui.QCursor.pos()))
            if clicked.x() < 0: clicked.setX(0)
            if clicked.y() < 0: clicked.setY(0)
            clickx = int(clicked.x() / 24)
            clicky = int(clicked.y() / 24)

            # allow negative width/height and treat it properly :D
            if clickx >= dsx:
                x = dsx
                width = clickx - dsx + 1
            else:
                x = clickx
                width = dsx - clickx + 1

            if clicky >= dsy:
                y = dsy
                height = clicky - dsy + 1
            else:
                y = clicky
                height = dsy - clicky + 1

            # if the position changed, set the new one
            if cx != x or cy != y:
                obj.objx = x
                obj.objy = y
                obj.setPos(x * 24, y * 24)

            # if the size changed, recache it and update the area
            if cwidth != width or cheight != height:
                obj.width = width
                obj.height = height
                obj.updateObjCache()

                oldrect = obj.BoundingRect
                oldrect.translate(cx * 24, cy * 24)
                newrect = QtCore.QRectF(obj.x(), obj.y(), obj.width * 24, obj.height * 24)
                updaterect = oldrect.united(newrect)

                obj.UpdateRects()
                obj.scene().update(updaterect)

        elif isinstance(obj, type_loc):
            # resize/move the current location
            cx = obj.objx
            cy = obj.objy
            cwidth = obj.width
            cheight = obj.height

            dsx = self.dragstartx
            dsy = self.dragstarty
            clicked = mainWindow.view.mapToScene(self.mapFromGlobal(QtGui.QCursor.pos()))
            if clicked.x() < 0: clicked.setX(0)
            if clicked.y() < 0: clicked.setY(0)
            clickx = int(clicked.x() / 1.5)
            clicky = int(clicked.y() / 1.5)

            # allow negative width/height and treat it properly :D
            if clickx >= dsx:
                x = dsx
                width = clickx - dsx + 1
            else:
                x = clickx
                width = dsx - clickx + 1

            if clicky >= dsy:
                y = dsy
                height = clicky - dsy + 1
            else:
                y = clicky
                height = dsy - clicky + 1

            # if the position changed, set the new one
            if cx != x or cy != y:
                obj.objx = x
                obj.objy = y

                global OverrideSnapping
                OverrideSnapping = True
                obj.setPos(x * 1.5, y * 1.5)
                OverrideSnapping = False

            # if the size changed, recache it and update the area
            if cwidth != width or cheight != height:
                obj.width = width
                obj.height = height
#                    obj.updateObjCache()

                oldrect = obj.BoundingRect
                oldrect.translate(cx * 1.5, cy * 1.5)
                newrect = QtCore.QRectF(obj.x(), obj.y(), obj.width * 1.5, obj.height * 1.5)
                updaterect = oldrect.united(newrect)

                obj.UpdateRects()
                obj.scene().update(updaterect)


        elif isinstance(obj, type_spr):
            # move the created sprite
            clicked = mainWindow.view.mapToScene(self.mapFromGlobal(QtGui.QCursor.pos()))
            if clicked.x() < 0: clicked.setX(0)
            if clicked.y() < 0: clicked.setY(0)
            clickedx = int((clicked.x() - 12) / 12) * 8
            clickedy = int((clicked.y() - 12) / 12) * 8
            if obj.objx != clickedx or obj.objy != clickedy:
                obj.objx = clickedx
                obj.objy = clickedy
                obj.setPos(int((clickedx+obj.xoffset) * 1.5), int((clickedy+obj.yoffset) * 1.5))

        elif isinstance(obj, type_ent):
            # move the created entrance
            clicked = mainWindow.view.mapToScene(self.mapFromGlobal(QtGui.QCursor.pos()))
            if clicked.x() < 0: clicked.setX(0)
            if clicked.y() < 0: clicked.setY(0)
            clickedx = int((clicked.x() - 12) / 1.5)
            clickedy = int((clicked.y() - 12) / 1.5)

            if obj.objx != clickedx or obj.objy != clickedy:
                obj.objx = clickedx
                obj.objy = clickedy
                obj.setPos(int(clickedx * 1.5), int(clickedy * 1.5))
        elif isinstance(obj, type_path):
            # move the created path
            clicked = mainWindow.view.mapToScene(self.mapFromGlobal(QtGui.QCursor.pos()))
            if clicked.x() < 0: clicked.setX(0)
            if clicked.y() < 0: clicked.setY(0)
            clickedx = int((clicked.x() - 12) / 1.5)
            clickedy = int((clicked.y() - 12) / 1.5)

            if obj.objx != clickedx or obj.objy != clickedy:
                obj.objx = clickedx
                obj.objy = clickedy
                obj.setPos(int(clickedx * 1.5), int(clickedy * 1.5))

        return True


    def scrollIfCursorNearEdge(self):
        """Scroll the view if the cursor is dragging and near the edge"""
        pos = self.mapFromGlobal(QtGui.QCursor.pos())

        distFromL = pos.x()
        distFromR = self.width() - self.YScrollBar.width() - pos.x()
        distFromT = pos.y()
        distFromB = self.height() - self.XScrollBar.height() - pos.y()

        EDGE_PAD = 60
        SCALE_FACTOR = 0.3

        scrollDx = scrollDy = 0

        if distFromL < EDGE_PAD:
            scrollDx = -(EDGE_PAD - distFromL) * SCALE_FACTOR
        if distFromR < EDGE_PAD:
            scrollDx = (EDGE_PAD - distFromR) * SCALE_FACTOR
        if distFromT < EDGE_PAD:
            scrollDy = -(EDGE_PAD - distFromT) * SCALE_FACTOR
        if distFromB < EDGE_PAD:
            scrollDy = (EDGE_PAD - distFromB) * SCALE_FACTOR

        if scrollDx:
            self.XScrollBar.setValue(int(self.XScrollBar.value() + scrollDx))
        if scrollDy:
            self.YScrollBar.setValue(int(self.YScrollBar.value() + scrollDy))

        self.updatePaintDraggedItems()


    def drawForeground(self, painter, rect):
        """Draws a grid"""
        if not GridEnabled: return

        Zoom = mainWindow.ZoomLevel
        drawLine = painter.drawLine

        if DarkMode:
            opacity = 50
        else:
            opacity = 100

        if Zoom >= 50:
            startx = rect.x()
            startx -= (startx % 24)
            endx = startx + rect.width() + 24

            starty = rect.y()
            starty -= (starty % 24)
            endy = starty + rect.height() + 24

            painter.setPen(QtGui.QPen(QtGui.QColor.fromRgb(255,255,255,opacity), 1, QtCore.Qt.PenStyle.DotLine))

            x = startx
            y1 = rect.top()
            y2 = rect.bottom()
            while x <= endx:
                drawLine(QtCore.QLineF(x, starty, x, endy))
                x += 24

            y = starty
            x1 = rect.left()
            x2 = rect.right()
            while y <= endy:
                drawLine(QtCore.QLineF(startx, y, endx, y))
                y += 24


        if Zoom >= 25:
            startx = rect.x()
            startx -= (startx % 96)
            endx = startx + rect.width() + 96

            starty = rect.y()
            starty -= (starty % 96)
            endy = starty + rect.height() + 96

            painter.setPen(QtGui.QPen(QtGui.QColor.fromRgb(255,255,255,opacity), 1, QtCore.Qt.PenStyle.DashLine))

            x = startx
            y1 = rect.top()
            y2 = rect.bottom()
            while x <= endx:
                drawLine(QtCore.QLineF(x, starty, x, endy))
                x += 96

            y = starty
            x1 = rect.left()
            x2 = rect.right()
            while y <= endy:
                drawLine(QtCore.QLineF(startx, y, endx, y))
                y += 96


        startx = rect.x()
        startx -= (startx % 192)
        endx = startx + rect.width() + 192

        starty = rect.y()
        starty -= (starty % 192)
        endy = starty + rect.height() + 192

        painter.setPen(QtGui.QPen(QtGui.QColor.fromRgb(255,255,255,opacity), 2, QtCore.Qt.PenStyle.DashLine))

        x = startx
        y1 = rect.top()
        y2 = rect.bottom()
        while x <= endx:
            drawLine(QtCore.QLineF(x, starty, x, endy))
            x += 192

        y = starty
        x1 = rect.left()
        x2 = rect.right()
        while y <= endy:
            drawLine(QtCore.QLineF(startx, y, endx, y))
            y += 192



####################################################################
####################################################################
####################################################################

class HexSpinBox(QtWidgets.QSpinBox):
    class HexValidator(QtGui.QValidator):
        def __init__(self, min, max):
            super(HexSpinBox.HexValidator, self).__init__()
            self.valid = set('0123456789abcdef')
            self.min = min
            self.max = max

        @QValidatorValidateCompat
        def validate(self, input, pos):
            try:
                input = str(input).lower()
            except:
                return self.State.Invalid, input, pos
            valid = self.valid

            for char in input:
                if char not in valid:
                    return self.State.Invalid, input, pos

            value = int(input, 16)
            if value < self.min or value > self.max:
                return self.State.Intermediate, input, pos

            return self.State.Acceptable, input, pos


    def __init__(self, format='%04X', *args):
        self.format = format
        super(HexSpinBox, self).__init__(*args)
        self.validator = self.HexValidator(self.minimum(), self.maximum())

    def setMinimum(self, value):
        self.validator.min = value
        QtWidgets.QSpinBox.setMinimum(self, value)

    def setMaximum(self, value):
        self.validator.max = value
        QtWidgets.QSpinBox.setMaximum(self, value)

    def setRange(self, min, max):
        self.validator.min = min
        self.validator.max = max
        QtWidgets.QSpinBox.setMinimum(self, min)
        QtWidgets.QSpinBox.setMaximum(self, max)

    def validate(self, text, pos):
        return self.validator.validate(text, pos)

    def textFromValue(self, value):
        return self.format % value

    def valueFromText(self, value):
        return int(str(value), 16)

class InputBox(QtWidgets.QDialog):
    Type_TextBox = 1
    Type_SpinBox = 2
    Type_HexSpinBox = 3

    def __init__(self, type=Type_TextBox):
        super(InputBox, self).__init__()

        self.label = QtWidgets.QLabel('-')
        self.label.setWordWrap(True)

        if type == InputBox.Type_TextBox:
            self.textbox = QtWidgets.QLineEdit()
            widget = self.textbox
        elif type == InputBox.Type_SpinBox:
            self.spinbox = QtWidgets.QSpinBox()
            widget = self.spinbox
        elif type == InputBox.Type_HexSpinBox:
            self.spinbox = HexSpinBox()
            widget = self.spinbox

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        self.layout = QtWidgets.QVBoxLayout()
        self.layout.addWidget(self.label)
        self.layout.addWidget(widget)
        self.layout.addWidget(self.buttons)
        self.setLayout(self.layout)


class AboutDialog(QtWidgets.QDialog):
    """The About info for Reggie"""
    def __init__(self):
        """Creates and initialises the dialog"""
        super(AboutDialog, self).__init__()
        self.setFixedWidth(550)
        self.setFixedHeight(350)
        self.setWindowTitle('About Reggie!')
        self.setWindowIcon(GetIcon('about'))

        with open('reggiedata/about.html', 'r') as f:
            data = f.read()

        self.pageWidget = QtWidgets.QTextBrowser()
        self.pageWidget.setHtml(data)

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        buttonBox.accepted.connect(self.accept)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.pageWidget)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)


class ObjectShiftDialog(QtWidgets.QDialog):
    """Lets you pick an amount to shift the selected objects by"""
    def __init__(self):
        """Creates and initialises the dialog"""
        super(ObjectShiftDialog, self).__init__()
        self.setWindowTitle('Shift Objects')

        self.XOffset = QtWidgets.QSpinBox()
        self.XOffset.setRange(-16384, 16383)

        self.YOffset = QtWidgets.QSpinBox()
        self.YOffset.setRange(-8192, 8191)

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

        moveLayout = QtWidgets.QFormLayout()
        offsetlabel = QtWidgets.QLabel("Enter an offset in pixels - each block is 16 pixels wide/high. Note that normal objects can only be placed on 16x16 boundaries, so if the offset you enter isn't a multiple of 16, they won't be moved correctly. Positive values move right/down, negative values move left/up.")
        offsetlabel.setWordWrap(True)
        moveLayout.addWidget(offsetlabel)
        moveLayout.addRow('X:', self.XOffset)
        moveLayout.addRow('Y:', self.YOffset)

        moveGroupBox = QtWidgets.QGroupBox('Move objects by:')
        moveGroupBox.setLayout(moveLayout)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(moveGroupBox)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)


class MetaInfoDialog(QtWidgets.QDialog):
    """Allows the user to enter in various meta-info to be kept in the level for display"""
    def __init__(self):
        """Creates and initialises the dialog"""
        super(MetaInfoDialog, self).__init__()
        self.setWindowTitle('Level Information')

        self.levelName = QtWidgets.QLineEdit()
        self.levelName.setMaxLength(32)
        self.levelName.setReadOnly(True)
        self.levelName.setMinimumWidth(320)
        self.levelName.setText(Level.Title)

        self.Author = QtWidgets.QLineEdit()
        self.Author.setMaxLength(32)
        self.Author.setReadOnly(True)
        self.Author.setMinimumWidth(320)
        self.Author.setText(Level.Author)

        self.Group = QtWidgets.QLineEdit()
        self.Group.setMaxLength(32)
        self.Group.setReadOnly(True)
        self.Group.setMinimumWidth(320)
        self.Group.setText(Level.Group)

        self.Website = QtWidgets.QLineEdit()
        self.Website.setMaxLength(64)
        self.Website.setReadOnly(True)
        self.Website.setMinimumWidth(320)
        self.Website.setText(Level.Webpage)

        self.Password = QtWidgets.QLineEdit()
        self.Password.setMaxLength(32)
        self.Password.textChanged.connect(self.PasswordEntry)
        self.Password.setMinimumWidth(320)

        self.changepw = QtWidgets.QPushButton('Add/Change Password')


        if Level.Password == '':
            self.levelName.setReadOnly(False)
            self.Author.setReadOnly(False)
            self.Group.setReadOnly(False)
            self.Website.setReadOnly(False)
            self.changepw.setDisabled(False)


        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttonBox.addButton(self.changepw, QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        self.changepw.clicked.connect(self.ChangeButton)
        self.changepw.setDisabled(True)

        self.lockedLabel = QtWidgets.QLabel("This level's information is locked.\nPlease enter the password below in order to modify it.")
        self.lockedLabel.setWordWrap(True)

        infoLayout = QtWidgets.QFormLayout()
        infoLayout.addWidget(self.lockedLabel)
        infoLayout.addRow('Password:', self.Password)
        infoLayout.addRow('Title:', self.levelName)
        infoLayout.addRow('Author:', self.Author)
        infoLayout.addRow('Group:', self.Group)
        infoLayout.addRow('Website:', self.Website)

        self.PasswordLabel = infoLayout.labelForField(self.Password)

        levelIsLocked = Level.Password != ''
        self.lockedLabel.setVisible(levelIsLocked)
        self.PasswordLabel.setVisible(levelIsLocked)
        self.Password.setVisible(levelIsLocked)

        infoGroupBox = QtWidgets.QGroupBox('Created with ' + Level.Creator)
        infoGroupBox.setLayout(infoLayout)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(infoGroupBox)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)

        self.PasswordEntry('')

    @QtCoreSlot(str)
    def PasswordEntry(self, text):
        if text == Level.Password:
            self.levelName.setReadOnly(False)
            self.Author.setReadOnly(False)
            self.Group.setReadOnly(False)
            self.Website.setReadOnly(False)
            self.changepw.setDisabled(False)
        else:
            self.levelName.setReadOnly(True)
            self.Author.setReadOnly(True)
            self.Group.setReadOnly(True)
            self.Website.setReadOnly(True)
            self.changepw.setDisabled(True)


#   To all would be crackers who are smart enough to reach here:
#
#   Make your own damn levels.
#
#
#
#       - The management
#


    def ChangeButton(self):
        """Allows the changing of a given password"""

        class ChangePWDialog(QtWidgets.QDialog):
            """Dialog"""
            def __init__(self):
                super(ChangePWDialog, self).__init__()
                self.setWindowTitle('Change Password')

                self.New = QtWidgets.QLineEdit()
                self.New.setMaxLength(64)
                self.New.textChanged.connect(self.PasswordMatch)
                self.New.setMinimumWidth(320)

                self.Verify = QtWidgets.QLineEdit()
                self.Verify.setMaxLength(64)
                self.Verify.textChanged.connect(self.PasswordMatch)
                self.Verify.setMinimumWidth(320)

                self.Ok = QtWidgets.QPushButton('OK')
                self.Cancel = QtWidgets.QDialogButtonBox.StandardButton.Cancel

                buttonBox = QtWidgets.QDialogButtonBox()
                buttonBox.addButton(self.Ok, QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
                buttonBox.addButton(self.Cancel)

                buttonBox.accepted.connect(self.accept)
                buttonBox.rejected.connect(self.reject)
                self.Ok.setDisabled(True)

                infoLayout = QtWidgets.QFormLayout()
                infoLayout.addRow('New Password:', self.New)
                infoLayout.addRow('Verify Password:', self.Verify)

                infoGroupBox = QtWidgets.QGroupBox('Level Information')

                infoLabel = QtWidgets.QVBoxLayout()
                infoLabel.addWidget(QtWidgets.QLabel('Password may be composed of any ASCII character,\nand up to 64 characters long.\n'), 0, QtCore.Qt.AlignmentFlag.AlignCenter)
                infoLabel.addLayout(infoLayout)
                infoGroupBox.setLayout(infoLabel)

                mainLayout = QtWidgets.QVBoxLayout()
                mainLayout.addWidget(infoGroupBox)
                mainLayout.addWidget(buttonBox)
                self.setLayout(mainLayout)

            @QtCoreSlot(str)
            def PasswordMatch(self, text):
                self.Ok.setDisabled(self.New.text() != self.Verify.text() and self.New.text() != '')

        dlg = ChangePWDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            self.lockedLabel.setVisible(True)
            self.Password.setVisible(True)
            self.PasswordLabel.setVisible(True)
            Level.Password = dlg.Verify.text()
            self.Password.setText(Level.Password)
            SetDirty()

            self.levelName.setReadOnly(False)
            self.Author.setReadOnly(False)
            self.Group.setReadOnly(False)
            self.Website.setReadOnly(False)
            self.changepw.setDisabled(False)



#Sets up the Area Options Menu
class AreaOptionsDialog(QtWidgets.QDialog):
    """Dialog which lets you choose among various area options from tabs"""
    def __init__(self):
        """Creates and initialises the tab dialog"""
        super(AreaOptionsDialog, self).__init__()
        self.setWindowTitle('Area Settings')
        self.setWindowIcon(GetIcon('area'))

        self.tabWidget = QtWidgets.QTabWidget()
        self.LoadingTab = LoadingTab()
        self.TilesetsTab = TilesetsTab()
        self.tabWidget.addTab(self.TilesetsTab, 'Tilesets')
        self.tabWidget.addTab(self.LoadingTab, 'Settings')

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.tabWidget)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)


class LoadingTab(QtWidgets.QWidget):
    def __init__(self):
        super(LoadingTab, self).__init__()

        self.timer = QtWidgets.QSpinBox()
        self.timer.setRange(0, 999)
        self.timer.setToolTip('<b>Timer:</b><br>Sets the countdown timer on load from the world map.')
        self.timer.setValue(Level.timeLimit + 200)

        self.entrance = QtWidgets.QSpinBox()
        self.entrance.setRange(0, 255)
        self.entrance.setToolTip('<b>Starting Entrance ID:</b><br>Sets the entrance ID to load into when loading from the world map.')
        self.entrance.setValue(Level.startEntrance)

        self.wrap = QtWidgets.QCheckBox('Wrap across Edges')
        self.wrap.setToolTip('<b>Wrap across Edges:</b><br>Makes the stage edges wrap.<br>Warning: This option may cause the game to crash or behave weirdly. Wrapping only works correctly where the area is set up in the right way; see Coin Battle 1 for an example.')
        self.wrap.setChecked((Level.wrapFlag & 1) != 0)

        settingsLayout = QtWidgets.QFormLayout()
        settingsLayout.addRow('Timer:', self.timer)
        settingsLayout.addRow('Starting Entrance ID:', self.entrance)
        settingsLayout.addRow(self.wrap)

        self.eventChooser = QtWidgets.QListWidget()
        defEvent = Level.defEvents
        item = QtWidgets.QListWidgetItem
        checked = QtCore.Qt.CheckState.Checked
        unchecked = QtCore.Qt.CheckState.Unchecked
        flags = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled

        for id in range(64):
            i = item('Event %d' % (id+1))
            value = 1 << id
            i.setCheckState(checked if (defEvent & value) != 0 else unchecked)
            i.setFlags(flags)
            self.eventChooser.addItem(i)

        eventLayout = QtWidgets.QVBoxLayout()
        eventLayout.addWidget(self.eventChooser)

        eventBox = QtWidgets.QGroupBox('Default Events')
        eventBox.setToolTip('<b>Default Events:</b><br>Check the following Event IDs to make them start already activated.')
        eventBox.setLayout(eventLayout)

        Layout = QtWidgets.QVBoxLayout()
        Layout.addLayout(settingsLayout)
        Layout.addWidget(eventBox)
        Layout.addStretch(1)
        self.setLayout(Layout)


class TilesetsTab(QtWidgets.QWidget):
    def __init__(self):
        super(TilesetsTab, self).__init__()

        self.tile0 = QtWidgets.QComboBox()
        self.tile1 = QtWidgets.QComboBox()
        self.tile2 = QtWidgets.QComboBox()
        self.tile3 = QtWidgets.QComboBox()

        self.widgets = [self.tile0, self.tile1, self.tile2, self.tile3]
        names = [Level.tileset0, Level.tileset1, Level.tileset2, Level.tileset3]
        slots = [self.HandleTileset0Choice, self.HandleTileset1Choice, self.HandleTileset2Choice, self.HandleTileset3Choice]

        self.currentChoices = [None, None, None, None]

        for idx, widget, name, data, slot in zip(range(4), self.widgets, names, TilesetNames, slots):
            if name == '':
                ts_index = 'None'
                custom = ''
                custom_fname = '[CUSTOM]'
            else:
                ts_index = 'Custom filename... (%s)' % name
                custom = ' (%s)' % name
                custom_fname = '[CUSTOM]' + name

            widget.addItem('None', '')
            for tfile, tname in data:
                text = '%s (%s)' % (tname,tfile)
                widget.addItem(text, tfile)
                if name == tfile:
                    ts_index = text
                    custom = ''
            widget.addItem('Custom filename...%s' % custom, custom_fname)

            item_idx = widget.findText(ts_index)
            self.currentChoices[idx] = item_idx

            widget.setCurrentIndex(item_idx)
            widget.activated.connect(slot)

        # don't allow ts0 to be removable
        self.tile0.removeItem(0)

        mainLayout = QtWidgets.QVBoxLayout()
        tile0Box = QtWidgets.QGroupBox('Standard Suite')
        tile1Box = QtWidgets.QGroupBox('Stage Suite')
        tile2Box = QtWidgets.QGroupBox('Background Suite')
        tile3Box = QtWidgets.QGroupBox('Interactive Suite')

        t0 = QtWidgets.QVBoxLayout()
        t0.addWidget(self.tile0)
        t1 = QtWidgets.QVBoxLayout()
        t1.addWidget(self.tile1)
        t2 = QtWidgets.QVBoxLayout()
        t2.addWidget(self.tile2)
        t3 = QtWidgets.QVBoxLayout()
        t3.addWidget(self.tile3)

        tile0Box.setLayout(t0)
        tile1Box.setLayout(t1)
        tile2Box.setLayout(t2)
        tile3Box.setLayout(t3)

        mainLayout.addWidget(tile0Box)
        mainLayout.addWidget(tile1Box)
        mainLayout.addWidget(tile2Box)
        mainLayout.addWidget(tile3Box)
        mainLayout.addStretch(1)
        self.setLayout(mainLayout)

    @QtCoreSlot(int)
    def HandleTileset0Choice(self, index):
        self.HandleTilesetChoice(0, index)

    @QtCoreSlot(int)
    def HandleTileset1Choice(self, index):
        self.HandleTilesetChoice(1, index)

    @QtCoreSlot(int)
    def HandleTileset2Choice(self, index):
        self.HandleTilesetChoice(2, index)

    @QtCoreSlot(int)
    def HandleTileset3Choice(self, index):
        self.HandleTilesetChoice(3, index)

    def HandleTilesetChoice(self, tileset, index):
        w = self.widgets[tileset]

        if index == (w.count() - 1):
            fname = unicode(qm(w.itemData(index)))
            fname = fname[8:]

            dbox = InputBox()
            dbox.setWindowTitle('Enter a Filename')
            dbox.label.setText('Enter the name of a custom tileset file to use. It must be placed in the game\'s Stage\\Texture folder (or Tilesets folder, in Newer SMBW) in order for Reggie to recognise it. Do not add the ".arc" extension at the end of the filename.')
            dbox.textbox.setMaxLength(31)
            dbox.textbox.setText(fname)
            result = execQtObject(dbox)

            if result == QtWidgets.QDialog.DialogCode.Accepted:
                fname = unicode(dbox.textbox.text())
                if fname.endswith('.arc'): fname = fname[:-4]

                w.setItemText(index, 'Custom filename... (%s)' % fname)
                w.setItemData(index, '[CUSTOM]'+fname)
            else:
                w.setCurrentIndex(self.currentChoices[tileset])
                return

        self.currentChoices[tileset] = index


class CameraModeZoomSettingsLayout(QtWidgets.QFormLayout):
    """Widget (actually layout) for editing cammode/camzoom"""
    edited = QtCoreSignal()

    updating = False

    def __init__(self, showMode5):
        super(CameraModeZoomSettingsLayout, self).__init__()
        self.updating = True

        comboboxSizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding, QtWidgets.QSizePolicy.Policy.Fixed)

        self.zm = -1

        self.modeButtonGroup = QtWidgets.QButtonGroup()
        modebuttons = []
        for i, name, tooltip in [
                    (0, 'Normal', 'The standard camera mode, appropriate for most situations.'),
                    (3, 'Static Zoom', 'In this mode, the camera will not zoom out during multiplayer.'),
                    (4, 'Static Zoom, Y Tracking Only', 'In this mode, the camera will not zoom out during multiplayer, and will be centered horizontally in the zone.'),
                    (5, 'Static Zoom, Event-Controlled', 'In this mode, the camera will not zoom out during multiplayer, and will use event-controlled camera settings from the Camera Profiles dialog.'),
                    (6, 'X Tracking Only', 'In this mode, the camera will only move horizontally. It will be aligned to the bottom edge of the zone.'),
                    (7, 'X Expanding Only', 'In this mode, the camera will only zoom out during multiplayer if the players are far apart horizontally.'),
                    (1, 'Y Tracking Only', 'In this mode, the camera will only move vertically. It will be centered horizontally in the zone.'),
                    (2, 'Y Expanding Only', 'In this mode, the camera will zoom out during multiplayer if the players are far apart vertically. The largest screen size will only be used if a player is flying with a Propeller Suit or Block.'),
                ]:

            rb = QtWidgets.QRadioButton(name)
            rb.setToolTip('<b>' + name + ':</b><br>' + tooltip)
            self.modeButtonGroup.addButton(rb, i)
            modebuttons.append(rb)

            if i == 5 and not showMode5:
                rb.setVisible(False)

            rb.clicked.connect(self.handleModeChanged)

        self.screenSizes = QtWidgets.QComboBox()
        self.screenSizes.setToolTip("<b>Screen Sizes:</b><br>Selects screen sizes the camera can use during multiplayer. The camera will zoom out if the players are too far apart, and zoom back in when they get closer together. Values represent screen heights, measured in tiles.<br><br>In single-player, only the smallest size will be used.<br><br>Options marked with * or ** are glitchy if zone bounds are set to 0; see the Upper/Lower Bounds tooltips for more info.<br>Options marked with ** are also unplayably glitchy in multiplayer.")
        self.screenSizes.setSizePolicy(comboboxSizePolicy)
        self.screenSizes.currentIndexChanged.connect(self.handleScreenSizesChanged)

        ModesLayout = QtWidgets.QGridLayout()
        for i, b in enumerate(modebuttons):
            ModesLayout.addWidget(b, i % 4, i // 4)

        self.addRow(ModesLayout)
        self.addRow('Screen Sizes:', self.screenSizes)

        self.updating = False


    @QtCoreSlot()
    def ChangeCamModeList(self):
        mode = self.modeButtonGroup.checkedId()

        oldListChoice = [1, 1, 2, 3, 3, 3, 1, 1][self.zm]
        newListChoice = [1, 1, 2, 3, 3, 3, 1, 1][mode]

        if self.zm == -1 or oldListChoice != newListChoice:

            if newListChoice == 1:
                items = [
                    '14, 19',
                    '14, 19, 24',
                    '14, 19, 28',
                    '20, 24',
                    '19, 24, 28',
                    '17, 24',
                    '17, 24, 28',
                    '17, 20',
                    '7, 11, 28**',
                    '17, 20.5, 24',
                    '17, 20, 28',
                ]
            elif newListChoice == 2:
                items = [
                    '14, 19',
                    '14, 19, 24',
                    '14, 19, 28',
                    '19, 19, 24',
                    '19, 24, 28',
                    '19, 24, 28',
                    '17, 24, 28',
                    '17, 20.5, 24',
                ]
            else:
                items = [
                    '14',
                    '19',
                    '24',
                    '28',
                    '17',
                    '20',
                    '16',
                    '28',
                    '7*',
                    '10.5*',
                ]

            self.screenSizes.clear()
            self.screenSizes.addItems(items)
            self.screenSizes.setCurrentIndex(0)
            self.zm = mode

    def setValues(self, cammode, camzoom):
        self.updating = True

        if cammode < 0: cammode = 0
        if cammode >= 8: cammode = 7

        self.modeButtonGroup.button(cammode).setChecked(True)
        self.ChangeCamModeList()

        if camzoom < 0: camzoom = 0
        if camzoom >= self.screenSizes.count(): camzoom = self.screenSizes.count() - 1

        self.screenSizes.setCurrentIndex(camzoom)

        self.updating = False

    def handleModeChanged(self):
        if self.updating: return
        self.ChangeCamModeList()
        self.edited.emit()

    def handleScreenSizesChanged(self):
        if self.updating: return
        self.edited.emit()


#Sets up the Zones Menu
class ZonesDialog(QtWidgets.QDialog):
    """Dialog which lets you choose among various from tabs"""
    def __init__(self):
        """Creates and initialises the tab dialog"""
        super(ZonesDialog, self).__init__()
        self.setWindowTitle('Zones')
        self.setWindowIcon(GetIcon('zones'))

        self.tabWidget = QtWidgets.QTabWidget()

        i = 0
        self.zoneTabs = []
        for z in Level.zones:
            i = i+1
            ZoneTabName = 'Zone ' + str(i)
            tab = ZoneTab(z)
            self.zoneTabs.append(tab)
            self.tabWidget.addTab(tab, ZoneTabName)


        if self.tabWidget.count() > 5:
            for tab in range(0, self.tabWidget.count()):
                self.tabWidget.setTabText(tab, str(tab + 1))


        self.NewButton = QtWidgets.QPushButton('New')
        self.DeleteButton = QtWidgets.QPushButton('Delete')

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttonBox.addButton(self.NewButton, QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        buttonBox.addButton(self.DeleteButton, QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        #self.NewButton.setEnabled(len(self.zoneTabs) < 8)
        self.NewButton.clicked.connect(self.NewZone)
        self.DeleteButton.clicked.connect(self.DeleteZone)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.tabWidget)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)

    @QtCoreSlot()
    def NewZone(self):
        if len(self.zoneTabs) >= 6:
            result = QtWidgets.QMessageBox.warning(self, 'Warning', 'You are trying to add more than 6 zones to a level - keep in mind that without the proper fix to the game, this will cause your level to <b>crash</b> or have other strange issues!<br><br>Are you sure you want to do this?', QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
            if result == QtWidgets.QMessageBox.StandardButton.No:
                return

        a = []
        b = []

        a.append([0, 0, 0, 0, 0, 15, 0, 0])
        b.append([0, 0, 0, 0, 0, 10, 10, 10, 0])
        id = len(self.zoneTabs)
        z = ZoneItem(16, 16, 448, 224, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, a, b, b, id)
        ZoneTabName = 'Zone ' + str(id+1)
        tab = ZoneTab(z)
        self.zoneTabs.append(tab)
        self.tabWidget.addTab(tab, ZoneTabName)
        if self.tabWidget.count() > 5:
            for tab in range(0, self.tabWidget.count()):
                self.tabWidget.setTabText(tab, str(tab + 1))

        self.tabWidget.setCurrentIndex(self.tabWidget.count() - 1)

        #self.NewButton.setEnabled(len(self.zoneTabs) < 8)


    @QtCoreSlot()
    def DeleteZone(self):
        curindex = self.tabWidget.currentIndex()
        tabamount = self.tabWidget.count()
        if tabamount == 0: return
        self.tabWidget.removeTab(curindex)

        for tab in range(curindex, tabamount):
            if self.tabWidget.count() < 6:
                self.tabWidget.setTabText(tab, 'Zone ' + str(tab + 1))
            if self.tabWidget.count() > 5:
                self.tabWidget.setTabText(tab, str(tab + 1))

        self.zoneTabs.pop(curindex)
        if self.tabWidget.count() < 6:
            for tab in range(0, self.tabWidget.count()):
                self.tabWidget.setTabText(tab, 'Zone ' + str(tab + 1))

        #self.NewButton.setEnabled(len(self.zoneTabs) < 8)



class ZoneTab(QtWidgets.QWidget):
    updatingMusic = False

    def __init__(self, z):
        super(ZoneTab, self).__init__()

        self.zoneObj = z

        self.createDimensions(z)
        self.createCamera(z)
        self.createRendering(z)
        self.createBounds(z)
        self.createAudio(z)

        leftLayout = QtWidgets.QVBoxLayout()
        leftLayout.addWidget(self.Dimensions)
        leftLayout.addWidget(self.Rendering)
        leftLayout.addWidget(self.Audio)

        rightLayout = QtWidgets.QVBoxLayout()
        rightLayout.addWidget(self.Camera)
        rightLayout.addWidget(self.Bounds)

        mainLayout = QtWidgets.QHBoxLayout()
        mainLayout.addLayout(leftLayout)
        mainLayout.addLayout(rightLayout)
        self.setLayout(mainLayout)



    def createDimensions(self, z):
        self.Dimensions = QtWidgets.QGroupBox('Dimensions')

        self.Zone_xpos = QtWidgets.QSpinBox()
        self.Zone_xpos.setRange(16, 65535)
        self.Zone_xpos.setToolTip('<b>X position:</b><br>Sets the X position of the upper left corner')
        self.Zone_xpos.setValue(z.objx)

        self.Zone_ypos = QtWidgets.QSpinBox()
        self.Zone_ypos.setRange(16, 65535)
        self.Zone_ypos.setToolTip('<b>Y position:</b><br>Sets the Y position of the upper left corner')
        self.Zone_ypos.setValue(z.objy)

        self.Zone_width = QtWidgets.QSpinBox()
        self.Zone_width.setRange(300, 65535)
        self.Zone_width.setToolTip('<b>X size:</b><br>Sets the width of the zone')
        self.Zone_width.setValue(z.width)

        self.Zone_height = QtWidgets.QSpinBox()
        self.Zone_height.setRange(200, 65535)
        self.Zone_height.setToolTip('<b>Y size:</b><br>Sets the height of the zone')
        self.Zone_height.setValue(z.height)


        ZonePositionLayout = QtWidgets.QFormLayout()
        ZonePositionLayout.addRow('X position:', self.Zone_xpos)
        ZonePositionLayout.addRow('Y position:', self.Zone_ypos)

        ZoneSizeLayout = QtWidgets.QFormLayout()
        ZoneSizeLayout.addRow('X size:', self.Zone_width)
        ZoneSizeLayout.addRow('Y size:', self.Zone_height)


        innerLayout = QtWidgets.QHBoxLayout()

        innerLayout.addLayout(ZonePositionLayout)
        innerLayout.addLayout(ZoneSizeLayout)
        self.Dimensions.setLayout(innerLayout)



    def createCamera(self, z):
        self.Camera = QtWidgets.QGroupBox('Camera')

        self.Zone_cammodezoom = CameraModeZoomSettingsLayout(True)
        self.Zone_cammodezoom.setValues(z.cammode, z.camzoom)

        self.Zone_direction = QtWidgets.QComboBox()
        self.Zone_direction.setToolTip('<b>Zone Direction:</b><br>Sets the general direction of progression through this zone. This is mainly used in multiplayer mode to help the camera decide which player is "in front of" the others.<br><br>"Bias" sets the camera\'s preferred movement direction perpendicular to the main one. The default bias is downward or rightward. Upward bias causes more bottom-of-screen deaths and is not recommended.')
        addList = ['Right', 'Right (upward bias)', 'Left', 'Left (upward bias)', 'Down', 'Down (leftward bias)', 'Up', 'Up (leftward bias)']
        self.Zone_direction.addItems(addList)
        if z.direction < 0: z.direction = 0
        if z.direction >= len(addList): z.direction = len(addList) - 1
        self.Zone_direction.setCurrentIndex(z.direction)

        self.Zone_yrestrict = QtWidgets.QCheckBox()
        self.Zone_yrestrict.setToolTip('<b>Only Scroll Upwards If Flying:</b><br>Prevents the screen from scrolling upwards unless the player uses a Propeller Suit or Block.<br><br>This feature looks rather glitchy and is not recommended.')
        self.Zone_yrestrict.setChecked(z.mpcamzoomadjust != 15)
        self.Zone_yrestrict.stateChanged.connect(self.ChangeMPZoomAdjust)

        self.Zone_mpzoomadjust = QtWidgets.QSpinBox()
        self.Zone_mpzoomadjust.setRange(0, 14)
        self.Zone_mpzoomadjust.setToolTip('<b>Multiplayer Screen Size Adjust:</b><br>Increases the height of the screen during multiplayer mode. Requires "Only Scroll Upwards If Flying" to be checked.<br><br>This causes very glitchy behavior if the zone is much taller than the adjusted screen height, if the screen becomes more than 28 tiles tall, or when the camera zooms in during the end-of-level celebration.')

        self.ChangeMPZoomAdjust()
        if z.mpcamzoomadjust < 15:
            self.Zone_mpzoomadjust.setValue(z.mpcamzoomadjust)

        ZoneCameraLayout = QtWidgets.QFormLayout()
        ZoneCameraLayout.addRow(self.Zone_cammodezoom)
        ZoneCameraLayout.addRow('Zone Direction:', self.Zone_direction)
        ZoneCameraLayout.addRow('Only Scroll Upwards If Flying:', self.Zone_yrestrict)
        ZoneCameraLayout.addRow('Multiplayer Screen Size Adjust:', self.Zone_mpzoomadjust)
        self.Camera.setLayout(ZoneCameraLayout)



    @QtCoreSlot(int)
    def ChangeMPZoomAdjust(self):
        self.Zone_mpzoomadjust.setEnabled(self.Zone_yrestrict.isChecked())
        self.Zone_mpzoomadjust.setValue(0)



    def createRendering(self, z):
        self.Rendering = QtWidgets.QGroupBox('Rendering')

        comboboxSizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding, QtWidgets.QSizePolicy.Policy.Fixed)

        self.Zone_modeldark = QtWidgets.QComboBox()
        self.Zone_modeldark.addItems(ZoneThemeValues)
        self.Zone_modeldark.setToolTip('<b>Zone Theme:</b><br>Changes the way models and parts of the background are rendered (for blurring, darkness, lava effects, and so on). Themes with * next to them are used in the game, but look the same as the overworld theme.')
        self.Zone_modeldark.setSizePolicy(comboboxSizePolicy)
        if z.modeldark < 0: z.modeldark = 0
        if z.modeldark >= len(ZoneThemeValues): z.modeldark = len(ZoneThemeValues) - 1
        self.Zone_modeldark.setCurrentIndex(z.modeldark)

        self.Zone_terraindark = QtWidgets.QComboBox()
        self.Zone_terraindark.addItems(ZoneTerrainThemeValues)
        self.Zone_terraindark.setToolTip("<b>Terrain Lighting:</b><br>Changes the way the terrain is rendered. It also affects the parts of the background which the normal theme doesn't change. Nintendo always used \"Normal\" terrain lighting in levels; options with * next to them are unused and not recommended.")
        self.Zone_terraindark.setSizePolicy(comboboxSizePolicy)
        if z.terraindark < 0: z.terraindark = 0
        if z.terraindark >= len(ZoneTerrainThemeValues): z.terraindark = len(ZoneTerrainThemeValues) - 1
        self.Zone_terraindark.setCurrentIndex(z.terraindark)

        self.Zone_vspotlight = QtWidgets.QCheckBox('Layer 0 Spotlight')
        self.Zone_vspotlight.setToolTip('<b>Layer 0 Spotlight:</b><br>Sets the visibility mode to spotlight. In spotlight mode, moving behind layer 0 objects enables a spotlight that follows Mario around.')

        self.Zone_vfulldark = QtWidgets.QCheckBox('Full Darkness')
        self.Zone_vfulldark.setToolTip('<b>Full Darkness:</b><br>Sets the visibility mode to full darkness. In full darkness mode, the screen is completely black and visibility is only provided by the available spotlight effect. Stars and some sprites can enhance the default visibility.')

        self.Zone_visibility = QtWidgets.QComboBox()

        self.zv = z.visibility

        self.Zone_vspotlight.setChecked(self.zv & 0x10)
        self.Zone_vfulldark.setChecked(self.zv & 0x20)


        self.ChangeVisibilityList()
        self.Zone_vspotlight.clicked.connect(self.ChangeVisibilityList)
        self.Zone_vfulldark.clicked.connect(self.ChangeVisibilityList)

        ZoneRenderingLayout = QtWidgets.QFormLayout()
        ZoneRenderingLayout.addRow('Zone Theme:', self.Zone_modeldark)
        ZoneRenderingLayout.addRow('Terrain Lighting:', self.Zone_terraindark)

        ZoneVisibilityLayout = QtWidgets.QHBoxLayout()
        ZoneVisibilityLayout.addWidget(self.Zone_vspotlight)
        ZoneVisibilityLayout.addWidget(self.Zone_vfulldark)

        InnerLayout = QtWidgets.QVBoxLayout()
        InnerLayout.addLayout(ZoneRenderingLayout)
        InnerLayout.addLayout(ZoneVisibilityLayout)
        InnerLayout.addWidget(self.Zone_visibility)
        self.Rendering.setLayout(InnerLayout)


    @QtCoreSlot(bool)
    def ChangeVisibilityList(self):
        VChoice = self.zv % 16

        addList = toolTip = None
        if not self.Zone_vfulldark.isChecked():
            if not self.Zone_vspotlight.isChecked():
                addList = ['Layer 0: Hidden', 'Layer 0: On Top']
                toolTip = '<b>Hidden</b> - Mario is hidden when moving behind objects on Layer 0<br><b>On Top</b> - Mario is displayed above Layer 0 at all times<br><br>Note: Entities behind layer 0 other than Mario are never visible'
            else:
                addList = ['Spotlight: Small', 'Spotlight: Large', 'Spotlight: Extremely Large']
                toolTip = '<b>Small</b> - A small, centered spotlight affords visibility through layer 0<br><b>Large</b> - A large, centered spotlight affords visibility through layer 0<br><b>Extremely Large</b> - An extremely large, centered spotlight, which spans the whole screen at all but the largest zoom levels, affords visibility through layer 0'
        else:
            if not self.Zone_vspotlight.isChecked():
                addList = ['Darkness: Large Foglight', 'Darkness: Lightbeam', 'Darkness: Large Focus Light', 'Darkness: Small Foglight', 'Darkness: Small Focus Light', 'Darkness: Absolute Black']
                toolTip = '<b>Large Foglight</b> - A large, organic light source surrounds Mario<br><b>Lightbeam</b> - Mario is able to aim a conical lightbeam through use of the Wiimote<br><b>Large Focus Light</b> - A large spotlight which changes size based upon player movement<br><b>Small Foglight</b> - A small, organic light source surrounds Mario<br><b>Small Focus Light</b> - A small spotlight which changes size based on player movement<br><b>Absolute Black</b> - Visibility is provided only by fireballs, stars, and certain sprites'
            else:
                addList = ['Small Spotlight and Small Focus Light']
                toolTip = '<b>Small Spotlight and Small Focus Light</b> - A small, centered spotlight affords visibility through layer 0, and a small spotlight which changes size based on player movement provides visibility through darkness'

        if addList is not None and toolTip is not None:
            self.Zone_visibility.clear()
            self.Zone_visibility.addItems(addList)
            self.Zone_visibility.setToolTip(toolTip)

            if VChoice >= len(addList): VChoice = len(addList) - 1
            self.Zone_visibility.setCurrentIndex(VChoice)


    def createBounds(self, z):
        self.Bounds = QtWidgets.QGroupBox('Bounds')

        #Block3 = Level.bounding[z.block3id]

        self.Zone_yboundup = QtWidgets.QSpinBox()
        self.Zone_yboundup.setRange(-32768, 32767)
        self.Zone_yboundup.setToolTip('<b>Upper Bounds:</b><br>Controls how close Mario needs to be to the top edge of the screen to move the camera upwards. Units are 1/16 of a tile.<br><br>Value "0": 5 tiles away from the top edge of the screen<br>Positive values: Easier to scroll upwards<br>Negative values: Harder to scroll upwards (-80 is the top edge of the screen)<br><br>Very high values (larger than the screen size) cause instant death upon screen scrolling.<br>Very negative values prevent the screen from scrolling upwards at all.')
        self.Zone_yboundup.setSpecialValueText('32')
        self.Zone_yboundup.setValue(z.yupperbound)

        self.Zone_ybounddown = QtWidgets.QSpinBox()
        self.Zone_ybounddown.setRange(-32768, 32767)
        self.Zone_ybounddown.setToolTip('<b>Lower Bounds:</b><br>Controls how close Mario needs to be to the bottom edge of the screen to move the camera downwards. Units are 1/16 of a tile.<br><br>Value "0": 4.5 tiles away from the bottom edge of the screen<br>Positive values: Harder to scroll downwards (72 is the bottom edge of the screen)<br>Negative values: Easier to scroll downwards<br><br>Very high values prevent the screen from scrolling downwards at all.<br>Very negative values (larger than the screen size) cause instant death upon screen scrolling.')
        self.Zone_ybounddown.setValue(z.ylowerbound)

        self.Zone_yboundup2 = QtWidgets.QSpinBox()
        self.Zone_yboundup2.setRange(-32768, 32767)
        self.Zone_yboundup2.setToolTip('<b>Lakitu Upper Bounds:</b><br>Used instead of Upper Bounds when at least one player is riding a Lakitu cloud.<br><br>The values are a little different from the regular Upper Bounds setting: value "0" represents 5.5 tiles away from the top edge of the screen, and the edge is at -88.')
        self.Zone_yboundup2.setSpecialValueText('32')
        self.Zone_yboundup2.setValue(z.yupperbound2)

        self.Zone_ybounddown2 = QtWidgets.QSpinBox()
        self.Zone_ybounddown2.setRange(-32768, 32767)
        self.Zone_ybounddown2.setToolTip('<b>Lakitu Lower Bounds:</b><br>Used instead of Lower Bounds when at least one player is riding a Lakitu cloud.<br><br>The values are a little different from the regular Lower Bounds setting: value "0" represents 5.5 tiles away from the bottom edge of the screen, and the edge is at 88.')
        self.Zone_ybounddown2.setValue(z.ylowerbound2)

        self.Zone_yboundup3 = QtWidgets.QSpinBox()
        self.Zone_yboundup3.setRange(-32768, 32767)
        self.Zone_yboundup3.setToolTip('<b>Multiplayer Upper Bounds Adjust:</b><br>Added to the upper bounds value (regular or Lakitu) during multiplayer mode, and during the transition back to normal camera behavior after an Auto-Scrolling Controller reaches the end of its path.')
        self.Zone_yboundup3.setSpecialValueText('32')
        self.Zone_yboundup3.setValue(z.yupperbound3)

        self.Zone_ybounddown3 = QtWidgets.QSpinBox()
        self.Zone_ybounddown3.setRange(-32768, 32767)
        self.Zone_ybounddown3.setToolTip('<b>Multiplayer Lower Bounds Adjust:</b><br>Added to the lower bounds value (regular or Lakitu) during multiplayer mode, and during the transition back to normal camera behavior after an Auto-Scrolling Controller reaches the end of its path.')
        self.Zone_ybounddown3.setValue(z.ylowerbound3)


        TopLeftLayout = QtWidgets.QFormLayout()
        TopLeftLayout.addRow('Upper Bounds:', self.Zone_yboundup)
        TopLeftLayout.addRow('Lower Bounds:', self.Zone_ybounddown)

        TopRightLayout = QtWidgets.QFormLayout()
        TopRightLayout.addRow('Lakitu Upper Bounds:', self.Zone_yboundup2)
        TopRightLayout.addRow('Lakitu Lower Bounds:', self.Zone_ybounddown2)

        TopLayout = QtWidgets.QHBoxLayout()
        TopLayout.addLayout(TopLeftLayout)
        TopLayout.addLayout(TopRightLayout)

        ZoneBoundsLayout = QtWidgets.QFormLayout()
        ZoneBoundsLayout.addRow(TopLayout)
        ZoneBoundsLayout.addRow('Multiplayer Upper Bounds Adjust:', self.Zone_yboundup3)
        ZoneBoundsLayout.addRow('Multiplayer Lower Bounds Adjust:', self.Zone_ybounddown3)

        self.Bounds.setLayout(ZoneBoundsLayout)


    def createAudio(self, z):
        self.Audio = QtWidgets.QGroupBox('Audio')

        musicIdTooltip = '<b>Background Music:</b><br>Changes the background music'

        self.Zone_music_id = QtWidgets.QSpinBox()
        self.Zone_music_id.setRange(0, 255)
        self.Zone_music_id.setToolTip(musicIdTooltip)
        self.Zone_music_id.setValue(z.music)

        self.Zone_music = QtWidgets.QComboBox()
        self.Zone_music.setToolTip(musicIdTooltip)
        self.Zone_music.addItems(MusicNames)
        self.Zone_music.setCurrentIndex(z.music)

        self.Zone_music_id.valueChanged.connect(self.musicIDChanged)
        self.Zone_music.currentIndexChanged.connect(self.musicListItemChanged)

        music_layout = QtWidgets.QHBoxLayout()
        music_layout.addWidget(self.Zone_music_id)
        music_layout.addWidget(self.Zone_music, 1)

        self.Zone_sfx = QtWidgets.QComboBox()
        self.Zone_sfx.setToolTip('<b>Sound Modulation:</b><br>Changes the sound effect modulation')
        newItems3 = ['Normal', 'Wall Echo', 'Room Echo', 'Double Echo', 'Cave Echo', 'Underwater Echo', 'Triple Echo', 'High Pitch Echo', 'Tinny Echo', 'Flat', 'Dull', 'Hollow Echo', 'Rich', 'Triple Underwater', 'Ring Echo']
        self.Zone_sfx.addItems(newItems3)
        if z.sfxmod < 0: z.sfxmod = 0
        if z.sfxmod // 16 >= len(newItems3): z.sfxmod = ((len(newItems3) - 1) * 16) | (z.sfxmod & 15)
        self.Zone_sfx.setCurrentIndex(z.sfxmod // 16)

        self.Zone_boss = QtWidgets.QCheckBox()
        self.Zone_boss.setToolTip('<b>Boss Flag:</b><br>Set for bosses to allow proper music switching by sprites')
        self.Zone_boss.setChecked(z.sfxmod % 16)


        ZoneAudioLayout = QtWidgets.QFormLayout()
        ZoneAudioLayout.addRow('Background Music:', music_layout)
        ZoneAudioLayout.addRow('Sound Modulation:', self.Zone_sfx)
        ZoneAudioLayout.addRow('Boss Flag:', self.Zone_boss)

        self.Audio.setLayout(ZoneAudioLayout)


    @QtCoreSlot(int)
    def musicIDChanged(self, id):
        if self.updatingMusic:
            return

        self.updatingMusic = True
        self.Zone_music.setCurrentIndex(id)
        self.updatingMusic = False


    @QtCoreSlot(int)
    def musicListItemChanged(self, id):
        if self.updatingMusic:
            return

        self.updatingMusic = True
        self.Zone_music_id.setValue(id)
        self.updatingMusic = False



#Sets up the Background Dialog
class BGDialog(QtWidgets.QDialog):
    """Dialog which lets you choose among various from tabs"""
    def __init__(self):
        """Creates and initialises the tab dialog"""
        super(BGDialog, self).__init__()
        self.setWindowTitle('Backgrounds')
        self.setWindowIcon(GetIcon('background'))

        self.tabWidget = QtWidgets.QTabWidget()

        i = 0
        self.BGTabs = []
        for z in Level.zones:
            i = i+1
            BGTabName = 'Zone ' + str(i)
            tab = BGTab(z)
            self.BGTabs.append(tab)
            self.tabWidget.addTab(tab, BGTabName)


        if self.tabWidget.count() > 5:
            for tab in range(0, self.tabWidget.count()):
                self.tabWidget.setTabText(tab, str(tab + 1))


        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.tabWidget)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)


class BGTab(QtWidgets.QWidget):
    def __init__(self, z):
        super(BGTab, self).__init__()

        self.createBGa(z)
        self.createBGb(z)
        self.createBGaViewer(z)
        self.createBGbViewer(z)

        mainLayout = QtWidgets.QGridLayout()
        mainLayout.addWidget(self.BGa, 0, 0)
        mainLayout.addWidget(self.BGb, 1, 0)
        mainLayout.addWidget(self.BGaViewer, 0, 1)
        mainLayout.addWidget(self.BGbViewer, 1, 1)
        self.setLayout(mainLayout)


    def createBGa(self, z):
        self.BGa = QtWidgets.QGroupBox('Scenery')



        self.xposA = QtWidgets.QSpinBox()
        self.xposA.setToolTip('<b>X:</b><br>Sets the horizontal offset of your background')
        self.xposA.setRange(-256, 255)
        self.xposA.setValue(z.XpositionA)

        self.yposA = QtWidgets.QSpinBox()
        self.yposA.setToolTip('<b>Y:</b><br>Sets the vertical offset of your background')
        self.yposA.setRange(-255, 256)
        self.yposA.setValue(-z.YpositionA)

        self.scrollrate = QtWidgets.QLabel('Scroll Rate:')
        self.positionlabel = QtWidgets.QLabel('Position:')

        self.xscrollA = QtWidgets.QComboBox()
        self.xscrollA.addItems(BgScrollRateStrings)
        self.xscrollA.setToolTip('<b>X:</b><br>Changes the rate that the background moves in relation to Mario when he moves horizontally.<br>Values higher than 1x may be glitchy!')
        if z.XscrollA < 0: z.XscrollA = 0
        if z.XscrollA >= len(BgScrollRates): z.XscrollA = len(BgScrollRates) - 1
        self.xscrollA.setCurrentIndex(z.XscrollA)

        self.yscrollA = QtWidgets.QComboBox()
        self.yscrollA.addItems(BgScrollRateStrings)
        self.yscrollA.setToolTip('<b>Y:</b><br>Changes the rate that the background moves in relation to Mario when he moves vertically.<br>Values higher than 1x may be glitchy!')
        if z.YscrollA < 0: z.YscrollA = 0
        if z.YscrollA >= len(BgScrollRates): z.YscrollA = len(BgScrollRates) - 1
        self.yscrollA.setCurrentIndex(z.YscrollA)


        self.zoomA = QtWidgets.QComboBox()
        addstr = ['100%', '125%', '150%', '200%']
        self.zoomA.addItems(addstr)
        self.zoomA.setToolTip('<b>Zoom:</b><br>Sets the zoom level of the background image')
        if z.ZoomA < 0: z.ZoomA = 0
        if z.ZoomA >= len(addstr): z.ZoomA = len(addstr) - 1
        self.zoomA.setCurrentIndex(z.ZoomA)

        self.toscreenA = QtWidgets.QRadioButton()
        self.toscreenA.setToolTip('<b>Screen:</b><br>Aligns the background baseline to the bottom of the screen')
        self.toscreenLabel = QtWidgets.QLabel('Screen')
        self.tozoneA = QtWidgets.QRadioButton()
        self.tozoneA.setToolTip('<b>Zone:</b><br>Aligns the background baseline to the bottom of the zone')
        self.tozoneLabel = QtWidgets.QLabel('Zone')
        if z.bg2A == 0x000A:
            self.tozoneA.setChecked(1)
        else:
            self.toscreenA.setChecked(1)

        self.alignLabel = QtWidgets.QLabel('Align to: ')

        Lone = QtWidgets.QFormLayout()
        Lone.addRow('Zoom: ', self.zoomA)

        Ltwo = QtWidgets.QHBoxLayout()
        Ltwo.addWidget(self.toscreenLabel)
        Ltwo.addWidget(self.toscreenA)
        Ltwo.addWidget(self.tozoneLabel)
        Ltwo.addWidget(self.tozoneA)

        Lthree = QtWidgets.QFormLayout()
        Lthree.addRow('X:', self.xposA)
        Lthree.addRow('Y:', self.yposA)

        Lfour = QtWidgets.QFormLayout()
        Lfour.addRow('X: ', self.xscrollA)
        Lfour.addRow('Y: ', self.yscrollA)


        mainLayout = QtWidgets.QGridLayout()
        mainLayout.addWidget(self.positionlabel, 0, 0)
        mainLayout.addLayout(Lthree, 1, 0)
        mainLayout.addWidget(self.scrollrate, 0, 1)
        mainLayout.addLayout(Lfour, 1, 1)
        mainLayout.addLayout(Lone, 2, 0, 1, 2)
        mainLayout.addWidget(self.alignLabel, 3, 0, 1, 2)
        mainLayout.addLayout(Ltwo, 4, 0, 1, 2)
        mainLayout.setRowStretch(5, 1)
        self.BGa.setLayout(mainLayout)


    def createBGb(self, z):
        self.BGb = QtWidgets.QGroupBox('Backdrop')


        self.xposB = QtWidgets.QSpinBox()
        self.xposB.setToolTip('<b>X:</b><br>Sets the horizontal offset of your background')
        self.xposB.setRange(-256, 255)
        self.xposB.setValue(z.XpositionB)

        self.yposB = QtWidgets.QSpinBox()
        self.yposB.setToolTip('<b>Y:</b><br>Sets the vertical offset of your background')
        self.yposB.setRange(-255, 256)
        self.yposB.setValue(-z.YpositionB)

        self.scrollrate = QtWidgets.QLabel('Scroll Rate:')
        self.positionlabel = QtWidgets.QLabel('Position:')

        self.xscrollB = QtWidgets.QComboBox()
        self.xscrollB.addItems(BgScrollRateStrings)
        self.xscrollB.setToolTip('<b>X:</b><br>Changes the rate that the background moves in relation to Mario when he moves horizontally.<br>Values higher than 1x may be glitchy!')
        if z.XscrollB < 0: z.XscrollB = 0
        if z.XscrollB >= len(BgScrollRates): z.XscrollB = len(BgScrollRates) - 1
        self.xscrollB.setCurrentIndex(z.XscrollB)

        self.yscrollB = QtWidgets.QComboBox()
        self.yscrollB.addItems(BgScrollRateStrings)
        self.yscrollB.setToolTip('<b>Y:</b><br>Changes the rate that the background moves in relation to Mario when he moves vertically.<br>Values higher than 1x may be glitchy!')
        if z.YscrollB < 0: z.YscrollB = 0
        if z.YscrollB >= len(BgScrollRates): z.YscrollB = len(BgScrollRates) - 1
        self.yscrollB.setCurrentIndex(z.YscrollB)


        self.zoomB = QtWidgets.QComboBox()
        addstr = ['100%', '125%', '150%', '200%']
        self.zoomB.addItems(addstr)
        self.zoomB.setToolTip('<b>Zoom:</b><br>Sets the zoom level of the background image')
        if z.ZoomB < 0: z.ZoomB = 0
        if z.ZoomB >= len(addstr): z.ZoomB = len(addstr) - 1
        self.zoomB.setCurrentIndex(z.ZoomB)

        self.toscreenB = QtWidgets.QRadioButton()
        self.toscreenB.setToolTip('<b>Screen:</b><br>Aligns the background baseline to the bottom of the screen')
        self.toscreenLabel = QtWidgets.QLabel('Screen')
        self.tozoneB = QtWidgets.QRadioButton()
        self.tozoneB.setToolTip('<b>Zone:</b><br>Aligns the background baseline to the bottom of the zone')
        self.tozoneLabel = QtWidgets.QLabel('Zone')
        if z.bg2B == 0x000A:
            self.tozoneB.setChecked(1)
        else:
            self.toscreenB.setChecked(1)

        self.alignLabel = QtWidgets.QLabel('Align to: ')

        Lone = QtWidgets.QFormLayout()
        Lone.addRow('Zoom: ', self.zoomB)

        Ltwo = QtWidgets.QHBoxLayout()
        Ltwo.addWidget(self.toscreenLabel)
        Ltwo.addWidget(self.toscreenB)
        Ltwo.addWidget(self.tozoneLabel)
        Ltwo.addWidget(self.tozoneB)

        Lthree = QtWidgets.QFormLayout()
        Lthree.addRow('X:', self.xposB)
        Lthree.addRow('Y:', self.yposB)

        Lfour = QtWidgets.QFormLayout()
        Lfour.addRow('X: ', self.xscrollB)
        Lfour.addRow('Y: ', self.yscrollB)


        mainLayout = QtWidgets.QGridLayout()
        mainLayout.addWidget(self.positionlabel, 0, 0)
        mainLayout.addLayout(Lthree, 1, 0)
        mainLayout.addWidget(self.scrollrate, 0, 1)
        mainLayout.addLayout(Lfour, 1, 1)
        mainLayout.addLayout(Lone, 2, 0, 1, 2)
        mainLayout.addWidget(self.alignLabel, 3, 0, 1, 2)
        mainLayout.addLayout(Ltwo, 4, 0, 1, 2)
        mainLayout.setRowStretch(5, 1)
        self.BGb.setLayout(mainLayout)


    def createBGaViewer(self, z):
        self.BGaViewer = QtWidgets.QGroupBox('Preview')

        self.background_nameA = QtWidgets.QComboBox()
        self.previewA = QtWidgets.QLabel()

        #image = QtGui.QImage('reggiedata/bga/000A.png')
        #self.previewA.setPixmap(QtGui.QPixmap.fromImage(image))

        if z.bg1A == 0x000A:
            currentBG = z.bg2A
        else:
            currentBG = z.bg1A

        found_it = False

        for bfile_raw, bname in BgANames:
            bfile = int(bfile_raw, 16)
            self.background_nameA.addItem('%s (%04X)' % (bname,bfile), bfile)

            if currentBG == bfile:
                self.background_nameA.setCurrentIndex(self.background_nameA.count() - 1)
                found_it = True

        if found_it:
            custom = ''
        else:
            custom = ' (%04X)' % currentBG

        self.background_nameA.addItem('Custom background ID...%s' % custom, currentBG)
        if not found_it:
            self.background_nameA.setCurrentIndex(self.background_nameA.count() - 1)

        self.currentIndexA = self.background_nameA.currentIndex()

        self.background_nameA.activated.connect(self.viewboxA)
        self.viewboxA(self.background_nameA.currentIndex(), True)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.background_nameA)
        mainLayout.addWidget(self.previewA)
        self.BGaViewer.setLayout(mainLayout)


    @QtCoreSlot(int)
    def viewboxA(self, indexid, loadFlag=False):
        if not loadFlag:
            if indexid == (self.background_nameA.count() - 1):
                w = self.background_nameA
                id = qm(w.itemData(indexid))

                dbox = InputBox(InputBox.Type_HexSpinBox)
                dbox.setWindowTitle('Choose a Background ID')
                dbox.label.setText("Enter the hex ID of a custom background to use. The file must be named using the bgA_12AB.arc format and located within the game's Object folder.")
                dbox.spinbox.setRange(0, 0xFFFF)
                if id is not None: dbox.spinbox.setValue(id)
                result = execQtObject(dbox)

                if result == QtWidgets.QDialog.DialogCode.Accepted:
                    id = dbox.spinbox.value()
                    w.setItemText(indexid, 'Custom background ID... (%04X)' % id)
                    w.setItemData(indexid, id)
                else:
                    w.setCurrentIndex(self.currentIndexA)
                    return

        id = qm(self.background_nameA.itemData(indexid))
        filename = 'reggiedata/bga/%04X.png' % id

        if not os.path.isfile(filename):
            filename = 'reggiedata/bga/no_preview.png'

        image = QtGui.QImage(filename)
        self.previewA.setPixmap(QtGui.QPixmap.fromImage(image))

        self.currentIndexA = indexid



    def createBGbViewer(self, z):
        self.BGbViewer = QtWidgets.QGroupBox('Preview')

        self.background_nameB = QtWidgets.QComboBox()
        self.previewB = QtWidgets.QLabel()

        #image = QtGui.QImage('reggiedata/bgb/000A.png')
        #self.previewB.setPixmap(QtGui.QPixmap.fromImage(image))

        if z.bg1B == 0x000A:
            currentBG = z.bg2B
        else:
            currentBG = z.bg1B

        found_it = False

        for bfile_raw, bname in BgBNames:
            bfile = int(bfile_raw, 16)
            self.background_nameB.addItem('%s (%04X)' % (bname,bfile), bfile)

            if currentBG == bfile:
                self.background_nameB.setCurrentIndex(self.background_nameB.count() - 1)
                found_it = True

        if found_it:
            custom = ''
        else:
            custom = ' (%04X)' % currentBG

        self.background_nameB.addItem('Custom background ID...%s' % custom, currentBG)
        if not found_it:
            self.background_nameB.setCurrentIndex(self.background_nameB.count() - 1)

        self.currentIndexB = self.background_nameB.currentIndex()

        self.background_nameB.activated.connect(self.viewboxB)
        self.viewboxB(self.background_nameB.currentIndex(), True)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.background_nameB)
        mainLayout.addWidget(self.previewB)
        self.BGbViewer.setLayout(mainLayout)


    @QtCoreSlot(int)
    def viewboxB(self, indexid, loadFlag=False):
        if not loadFlag:
            if indexid == (self.background_nameB.count() - 1):
                w = self.background_nameB
                id = qm(w.itemData(indexid))

                dbox = InputBox(InputBox.Type_HexSpinBox)
                dbox.setWindowTitle('Choose a Background ID')
                dbox.label.setText("Enter the hex ID of a custom background to use. The file must be named using the bgB_12AB.arc format and located within the game's Object folder.")
                dbox.spinbox.setRange(0, 0xFFFF)
                if id is not None: dbox.spinbox.setValue(id)
                result = execQtObject(dbox)

                if result == QtWidgets.QDialog.DialogCode.Accepted:
                    id = dbox.spinbox.value()
                    w.setItemText(indexid, 'Custom background ID... (%04X)' % id)
                    w.setItemData(indexid, id)
                else:
                    w.setCurrentIndex(self.currentIndexB)
                    return

        id = qm(self.background_nameB.itemData(indexid))
        filename = 'reggiedata/bgb/%04X.png' % id

        if not os.path.isfile(filename):
            filename = 'reggiedata/bgb/no_preview.png'

        image = QtGui.QImage(filename)
        self.previewB.setPixmap(QtGui.QPixmap.fromImage(image))

        self.currentIndexB = indexid



class CustomSortableListWidgetItem(QtWidgets.QListWidgetItem):
    """ListWidgetItem subclass that allows sorting by arbitrary key"""
    sortKey = 0

    def __lt__(self, other):
        if hasattr(self, 'sortKey') and hasattr(other, 'sortKey'):
            return self.sortKey < other.sortKey
        else:
            return False


class CameraProfilesDialog(QtWidgets.QDialog):
    """Dialog for editing camera profiles"""
    def __init__(self):
        """Creates and initialises the dialog"""
        super(CameraProfilesDialog, self).__init__()
        self.setWindowTitle('Camera Profiles')
        self.setWindowIcon(GetIcon('camprofile'))
        self.setMinimumHeight(450)

        self.list = QtWidgets.QListWidget()
        self.list.itemSelectionChanged.connect(self.handleSelectionChanged)
        self.list.setSortingEnabled(True)

        self.addButton = QtWidgets.QPushButton('Add')
        self.addButton.clicked.connect(self.handleAdd)
        self.removeButton = QtWidgets.QPushButton('Remove')
        self.removeButton.clicked.connect(self.handleRemove)
        self.removeButton.setEnabled(False)

        listLayout = QtWidgets.QGridLayout()
        listLayout.addWidget(self.addButton, 0, 0)
        listLayout.addWidget(self.removeButton, 0, 1)
        listLayout.addWidget(self.list, 1, 0, 1, 2)

        self.eventid = QtWidgets.QSpinBox()
        self.eventid.setRange(0, 255)
        self.eventid.setToolTip("<b>Triggering Event ID:</b><br>Sets the event ID that will trigger the camera profile. If switching away from a different profile, the previous profile's event ID will be automatically deactivated (so the game doesn't instantly switch back to it).")
        self.eventid.valueChanged.connect(self.handleEventIDChanged)

        self.camsettings = CameraModeZoomSettingsLayout(False)
        self.camsettings.setValues(0, 0)
        self.camsettings.edited.connect(self.handleCamSettingsChanged)

        profileLayout = QtWidgets.QFormLayout()
        profileLayout.addRow('Triggering Event ID:', self.eventid)
        profileLayout.addRow(createHorzLine())
        profileLayout.addRow(self.camsettings)

        self.profileBox = QtWidgets.QGroupBox('Modify Selected Camera Profile Properties')
        self.profileBox.setLayout(profileLayout)
        self.profileBox.setEnabled(False)
        self.profileBox.setToolTip('<b>Modify Selected Camera Profile Properties:</b><br>Camera Profiles can only be used with the "Event-Controlled" camera mode in the "Zones" dialog.<br><br>Transitions between zoom levels are instant, but can be hidden through careful use of zoom sprites (206).')

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

        Layout = QtWidgets.QGridLayout()
        Layout.addLayout(listLayout, 0, 0)
        Layout.addWidget(self.profileBox, 0, 1)
        Layout.addWidget(buttonBox, 1, 0, 1, 2)
        self.setLayout(Layout)

        for profile in Level.camprofiles:
            item = CustomSortableListWidgetItem()
            item.setData(QtCore.Qt.ItemDataRole.UserRole, profile)
            item.sortKey = profile[0]
            self.updateItemTitle(item)
            self.list.addItem(item)

        self.list.sortItems()

    def handleAdd(self, item=None):
        newId = 1
        for row in range(self.list.count()):
            item = self.list.item(row)
            values = qm(item.data(QtCore.Qt.ItemDataRole.UserRole))
            newId = max(newId, values[0] + 1)

        item = CustomSortableListWidgetItem()
        item.setData(QtCore.Qt.ItemDataRole.UserRole, [newId, 0, 0])
        item.sortKey = newId
        self.updateItemTitle(item)
        self.list.addItem(item)

    def handleRemove(self):
        self.list.takeItem(self.list.currentRow())

    def handleSelectionChanged(self):
        selItems = self.list.selectedItems()

        self.removeButton.setEnabled(bool(selItems))
        self.profileBox.setEnabled(bool(selItems))

        if selItems:
            selItem = selItems[0]
            values = qm(selItem.data(QtCore.Qt.ItemDataRole.UserRole))

            self.eventid.setValue(values[0])
            self.camsettings.setValues(values[1], values[2])

    def handleEventIDChanged(self, eventid):
        selItem = self.list.selectedItems()[0]
        values = qm(selItem.data(QtCore.Qt.ItemDataRole.UserRole))
        values[0] = eventid
        selItem.setData(QtCore.Qt.ItemDataRole.UserRole, values)
        selItem.sortKey = eventid
        self.updateItemTitle(selItem)

    def handleCamSettingsChanged(self):
        selItem = self.list.selectedItems()[0]
        values = qm(selItem.data(QtCore.Qt.ItemDataRole.UserRole))
        values[1] = self.camsettings.modeButtonGroup.checkedId()
        values[2] = self.camsettings.screenSizes.currentIndex()
        selItem.setData(QtCore.Qt.ItemDataRole.UserRole, values)

    def updateItemTitle(self, item):
        item.setText('Camera Profile on Event %d' % qm(item.data(QtCore.Qt.ItemDataRole.UserRole))[0])



#Sets up the Screen Cap Choice Dialog
class ScreenCapChoiceDialog(QtWidgets.QDialog):
    """Dialog which lets you choose which zone to take a pic of"""
    def __init__(self):
        """Creates and initialises the dialog"""
        super(ScreenCapChoiceDialog, self).__init__()
        self.setWindowTitle('Choose a Screenshot source')
        self.setWindowIcon(GetIcon('screenshot'))

        i=0
        self.zoneCombo = QtWidgets.QComboBox()
        self.zoneCombo.addItem('Current Screen')
        self.zoneCombo.addItem('All Zones')
        for z in Level.zones:
            i = i+1
            self.zoneCombo.addItem('Zone ' + str(i))


        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.zoneCombo)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)



class AutoSavedInfoDialog(QtWidgets.QDialog):
    """Dialog which lets you know that an auto saved level exists"""

    def __init__(self, filename):
        """Creates and initialises the dialog"""
        super(AutoSavedInfoDialog, self).__init__()
        self.setWindowTitle('Auto-saved backup found')

        mainlayout = QtWidgets.QVBoxLayout(self)

        hlayout = QtWidgets.QHBoxLayout()

        icon = QtWidgets.QLabel()
        hlayout.addWidget(icon)

        label = QtWidgets.QLabel("Reggie! has found some level data which wasn't saved - possibly due to a crash within the editor or by your computer. Do you want to restore this level?<br><br>If you pick No, the autosaved level data will be deleted and will no longer be accessible.<br><br>Original file path: " + filename)
        label.setWordWrap(True)
        hlayout.addWidget(label)
        hlayout.setStretch(1, 1)

        buttonbox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.No | QtWidgets.QDialogButtonBox.StandardButton.Yes)
        buttonbox.accepted.connect(self.accept)
        buttonbox.rejected.connect(self.reject)

        mainlayout.addLayout(hlayout)
        mainlayout.addWidget(buttonbox)


class AreaChoiceDialog(QtWidgets.QDialog):
    """Dialog which lets you choose an area"""

    def __init__(self, areacount):
        """Creates and initialises the dialog"""
        super(AreaChoiceDialog, self).__init__()
        self.setWindowTitle('Choose an Area')

        self.areaCombo = QtWidgets.QComboBox()
        for i in range(areacount):
            self.areaCombo.addItem('Area %d' % (i+1))

        buttonBox = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)

        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.areaCombo)
        mainLayout.addWidget(buttonBox)
        self.setLayout(mainLayout)

####################################################################
####################################################################
####################################################################



class ReggieWindow(QtWidgets.QMainWindow):
    """Reggie main level editor window"""

    def CreateAction(self, shortname, function, icon, text, statustext, shortcut, toggle=False):
        """Helper function to create an action"""

        if icon is not None:
            act = qm(QtGui).QAction(icon, text, self)
        else:
            act = qm(QtGui).QAction(text, self)

        if shortcut is not None:
            if isinstance(shortcut, list):
                act.setShortcuts(shortcut)
            else:
                act.setShortcut(shortcut)
        if statustext is not None: act.setStatusTip(statustext)
        if toggle:
            act.setCheckable(True)
        act.triggered.connect(function)

        self.actions[shortname] = act


    def __init__(self):
        """Editor window constructor"""
        super(ReggieWindow, self).__init__(None)

        self.setUnifiedTitleAndToolBarOnMac(True)

        # Reggie Version number goes below here. 64 char max (32 if non-ascii).
        self.ReggieInfo = ReggieID

        self.ZoomLevels = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 85.0, 90.0, 95.0, 100.0, 125.0, 150.0, 175.0, 200.0, 250.0, 300.0]

        self.AutosaveTimer = QtCore.QTimer()
        self.AutosaveTimer.timeout.connect(self.Autosave)
        self.AutosaveTimer.start(20000)

        # required variables
        self.UpdateFlag = False
        self.SelectionUpdateFlag = False
        self.selObj = None
        self.CurrentSelection = []

        # set up the window
        self.setWindowTitle('Reggie! Level Editor')
        appIcon = QtGui.QIcon('reggiedata/icon_reggie.png')
        appIcon.addPixmap(QtGui.QPixmap('reggiedata/icon_reggie_lg.png'))
        app.setWindowIcon(appIcon)
        if QtCompatVersion >= (5,0,0):
            app.setApplicationDisplayName(ApplicationDisplayName)

        # create the actions
        self.SetupActionsAndMenus()

        # set up the status bar
        self.posLabel = QtWidgets.QLabel()
        self.statusBar().addWidget(self.posLabel)

        # create the various panels
        self.SetupDocksAndPanels()

        # create the level view
        self.scene = LevelScene(0, 0, 1024*24, 512*24, self)
        self.scene.setItemIndexMethod(QtWidgets.QGraphicsScene.ItemIndexMethod.NoIndex)
        self.scene.selectionChanged.connect(self.ChangeSelectionHandler)

        self.view = LevelViewWidget(self.scene, self)
        self.view.centerOn(0,0) # this scrolls to the top left
        self.view.PositionHover.connect(self.PositionHovered)
        self.view.XScrollBar.valueChanged.connect(self.XScrollChange)
        self.view.YScrollBar.valueChanged.connect(self.YScrollChange)
        self.view.FrameSize.connect(self.HandleWindowSizeChange)

        # done creating the window!
        self.setCentralWidget(self.view)

        # set up the clipboard stuff
        self.clipboard = None
        self.systemClipboard = QtWidgets.QApplication.clipboard()
        self.systemClipboard.dataChanged.connect(self.TrackClipboardUpdates)

        # we might have something there already, activate Paste if so
        self.TrackClipboardUpdates()

        # let's restore the geometry
        if settings.contains('MainWindowState'):
            self.restoreState(qm(settings.value('MainWindowState')), 0)
        if settings.contains('MainWindowGeometry'):
            self.restoreGeometry(qm(settings.value('MainWindowGeometry')))

        # now get stuff ready
        loaded = False

        global RestoredFromAutoSave
        if RestoredFromAutoSave:
            RestoredFromAutoSave = False
            loaded = self.LoadLevelFromAutosave()

        if not loaded:
            if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) and IsNSMBLevel(sys.argv[1]):
                loaded = self.LoadLevel(sys.argv[1], 1)
            elif settings.contains('LastLevel'):
                lastlevel = unicode(qm(settings.value('LastLevel')))
                settings.remove('LastLevel')

                if lastlevel != 'None':
                    loaded = self.LoadLevel(lastlevel, 1)

        if not loaded:
            self.LoadLevelFromName('01-01', 1)

        QtCore.QTimer.singleShot(100, self.levelOverview.update)

    def SetupActionsAndMenus(self):
        """Sets up Reggie's actions, menus and toolbars"""
        self.actions = {}
        self.CreateAction('newlevel', self.HandleNewLevel, GetIcon('new'), 'New Level', 'Create a new, blank level', QtGui.QKeySequence.StandardKey.New)
        self.CreateAction('openfromname', self.HandleOpenFromName, GetIcon('open'), 'Open Level by Name...', 'Open a level based on its in-game world/number', QtGui.QKeySequence.StandardKey.Open)
        self.CreateAction('openfromfile', self.HandleOpenFromFile, GetIcon('openfromfile'), 'Open Level by File...', 'Open a level based on its filename', QtGui.QKeySequence('Ctrl+Shift+O'))
        self.CreateAction('save', self.HandleSave, GetIcon('save'), 'Save Level', 'Save a level back to the archive file', QtGui.QKeySequence.StandardKey.Save)
        self.CreateAction('saveas', self.HandleSaveAs, GetIcon('saveas'), 'Save Level As...', 'Save a level with a new filename', QtGui.QKeySequence('Ctrl+Shift+S'))
        self.CreateAction('screenshot', self.HandleScreenshot, GetIcon('screenshot'), 'Level Screenshot...', 'Takes a full size screenshot of your level for you to share.', QtGui.QKeySequence('Ctrl+Alt+3'))
        self.CreateAction('changegamepath', self.HandleChangeGamePath, None, 'Change Game Path...', 'Set a different folder to load the game files from', QtGui.QKeySequence('Ctrl+Alt+G'))
        self.CreateAction('exit', self.HandleExit, None, 'Exit Reggie!', 'Exit the editor', QtGui.QKeySequence.StandardKey.Quit)

        self.CreateAction('showlayer0', self.HandleUpdateLayer0, None, 'Layer 0', 'Toggle viewing of object layer 0', QtGui.QKeySequence('Ctrl+1'), True)
        self.CreateAction('showlayer1', self.HandleUpdateLayer1, None, 'Layer 1', 'Toggle viewing of object layer 1', QtGui.QKeySequence('Ctrl+2'), True)
        self.CreateAction('showlayer2', self.HandleUpdateLayer2, None, 'Layer 2', 'Toggle viewing of object layer 2', QtGui.QKeySequence('Ctrl+3'), True)
        self.CreateAction('showsprites', self.HandleUpdateSprites, None, 'Sprites', 'Toggle viewing of sprites', QtGui.QKeySequence('Ctrl+4'), True)
        self.CreateAction('showspriteimages', self.HandleUpdateSpriteImages, None, 'Sprite Images', 'Toggle viewing of sprite images', QtGui.QKeySequence('Ctrl+5'), True)
        self.CreateAction('showentrances', self.HandleUpdateEntrances, None, 'Entrances', 'Toggle viewing of entrances', QtGui.QKeySequence('Ctrl+6'), True)
        self.CreateAction('showlocations', self.HandleUpdateLocations, None, 'Locations', 'Toggle viewing of locations', QtGui.QKeySequence('Ctrl+7'), True)
        self.CreateAction('showpaths', self.HandleUpdatePaths, None, 'Paths', 'Toggle viewing of paths', QtGui.QKeySequence('Ctrl+8'), True)
        self.CreateAction('tsetslots', self.HandleTilesetSlotsMod, GetIcon('objects'), 'Tileset Slots Mod', 'Render objects with a common code mod that lets tilesets behave the same in any slot ' + unichr(0x2014) + ' only use this if your game has that mod applied', QtGui.QKeySequence('Ctrl+T'), True)
        self.actions['tsetslots'].setChecked(TilesetSlotsModEnabled)
        self.CreateAction('grid', self.HandleShowGrid, GetIcon('grid_white' if DarkMode else 'grid'), 'Show Grid', 'Show a grid over the level view', QtGui.QKeySequence('Ctrl+G'), True)
        self.actions['grid'].setChecked(GridEnabled)

        self.CreateAction('darkmode', self.HandleDarkMode, GetIcon('darkmode'), 'Dark Mode', 'Turn dark mode on or off', None, True)
        self.actions['darkmode'].setChecked(DarkMode)

        if hasattr(QtGui.QKeySequence.StandardKey, 'FullScreen'):
            # On my system, this is Ctrl+Shift+F on Qt 5 and F11 on Qt 6
            shortcut = QtGui.QKeySequence.StandardKey.FullScreen
        else:  # Qt 4
            shortcut = QtGui.QKeySequence('F11')
        self.CreateAction('fullscreen', self.HandleFullScreenMode, None, 'Full Screen', 'Turn full-screen mode on or off', shortcut, True)

        self.CreateAction('freezeobjects', self.HandleObjectsFreeze, None, 'Freeze Objects', 'Make objects non-selectable', QtGui.QKeySequence('Ctrl+Shift+1'), True)
        self.actions['freezeobjects'].setChecked(not ObjectsNonFrozen)
        self.CreateAction('freezesprites', self.HandleSpritesFreeze, None, 'Freeze Sprites', 'Make sprites non-selectable', QtGui.QKeySequence('Ctrl+Shift+2'), True)
        self.actions['freezesprites'].setChecked(not SpritesNonFrozen)
        self.CreateAction('freezeentrances', self.HandleEntrancesFreeze, None, 'Freeze Entrances', 'Make entrances non-selectable', QtGui.QKeySequence('Ctrl+Shift+3'), True)
        self.actions['freezeentrances'].setChecked(not EntrancesNonFrozen)
        self.CreateAction('freezelocations', self.HandleLocationsFreeze, None, 'Freeze Locations', 'Make locations non-selectable', QtGui.QKeySequence('Ctrl+Shift+4'), True)
        self.actions['freezelocations'].setChecked(not LocationsNonFrozen)
        self.CreateAction('freezepaths', self.HandlePathsFreeze, None, 'Freeze Paths', 'Make paths non-selectable', QtGui.QKeySequence('Ctrl+Shift+5'), True)
        self.actions['freezepaths'].setChecked(not PathsNonFrozen)

        self.CreateAction('zoommax', self.HandleZoomMax, GetIcon('zoommax'), 'Maximum Zoom', 'Zoom in all the way', QtGui.QKeySequence('Ctrl+PgDown'), False)
        self.CreateAction('zoomin', self.HandleZoomIn, GetIcon('zoomin'), 'Zoom In', 'Zoom into the main level view', [QtGui.QKeySequence.StandardKey.ZoomIn, QtGui.QKeySequence('Ctrl+=')], False)
        self.CreateAction('zoomactual', self.HandleZoomActual, GetIcon('zoomactual'), 'Zoom 100%', 'Show the level at the default zoom', QtGui.QKeySequence('Ctrl+0'), False)
        self.CreateAction('zoomout', self.HandleZoomOut, GetIcon('zoomout'), 'Zoom Out', 'Zoom out of the main level view', QtGui.QKeySequence.StandardKey.ZoomOut, False)
        self.CreateAction('zoommin', self.HandleZoomMin, GetIcon('zoommin'), 'Minimum Zoom', 'Zoom out all the way', QtGui.QKeySequence('Ctrl+PgUp'), False)

        self.CreateAction('areaoptions', self.HandleAreaOptions, GetIcon('area'), 'Area Settings...', 'Controls tileset swapping, stage timer, entrance on load, and stage wrap', QtGui.QKeySequence('Ctrl+Alt+A'))
        self.CreateAction('zones', self.HandleZones, GetIcon('zones'), 'Zones...', 'Zone creation, deletion, and preference editing', QtGui.QKeySequence('Ctrl+Alt+Z'))
        self.CreateAction('backgrounds', self.HandleBG, GetIcon('background'), 'Backgrounds...', 'Apply backgrounds to individual zones in the current area', QtGui.QKeySequence('Ctrl+Alt+B'))
        self.CreateAction('camprofiles', self.HandleCameraProfiles, GetIcon('camprofile'), 'Camera Profiles...', 'Edit event-activated camera settings', QtGui.QKeySequence('Ctrl+Alt+C'))
        self.CreateAction('metainfo', self.HandleInfo, None, 'Level Information...', 'Add title and author information to the metadata', QtGui.QKeySequence('Ctrl+Alt+I'))

        self.CreateAction('aboutqt', app.aboutQt, None, 'About Qt...', 'About the Qt library Reggie! is based on', QtGui.QKeySequence('Ctrl+Shift+Y'))
        self.CreateAction('infobox', self.InfoBox, GetIcon('about'), 'About Reggie!', 'Info about the program, and the team behind it', QtGui.QKeySequence('Ctrl+Shift+I'))
        self.CreateAction('helpbox', self.HelpBox, GetIcon('contents'), 'Reggie! Help...', 'Help Documentation for the needy newbie', QtGui.QKeySequence('Ctrl+Shift+H'))
        self.CreateAction('tipbox', self.TipBox, GetIcon('tips'), 'Reggie! Tips...', 'Tips and controls for beginners and power users', QtGui.QKeySequence('Ctrl+Shift+T'))

#        self.CreateAction('undo', self.Undo, None, 'Undo', 'Undoes a single action', QtGui.QKeySequence('Ctrl+Z'))
#        self.CreateAction('redo', self.Redo, None, 'Redo', 'Redoes a single action', QtGui.QKeySequence('Ctrl+Shift+Z'))

        self.CreateAction('selectall', self.SelectAll, None, 'Select All', 'Selects all items on screen', QtGui.QKeySequence.StandardKey.SelectAll)
        self.CreateAction('cut', self.Cut, GetIcon('cut'), 'Cut', 'Cut out the current selection to the clipboard', QtGui.QKeySequence.StandardKey.Cut)
        self.CreateAction('copy', self.Copy, GetIcon('copy'), 'Copy', 'Copies the current selection to the clipboard', QtGui.QKeySequence.StandardKey.Copy)
        self.CreateAction('paste', self.Paste, GetIcon('paste'), 'Paste', 'Pastes the current selection from the clipboard', QtGui.QKeySequence.StandardKey.Paste)
        self.CreateAction('shiftobjects', self.ShiftObjects, None, 'Shift Objects...', 'Moves all the selected objects by an offset', QtGui.QKeySequence('Ctrl+Alt+Shift+S'))
        self.CreateAction('mergelocations', self.MergeLocations, None, 'Merge Locations', 'Merges selected locations into a single large box', QtGui.QKeySequence('Ctrl+Shift+E'))

        self.actions['cut'].setEnabled(False)
        self.actions['copy'].setEnabled(False)
        self.actions['paste'].setEnabled(False)
        self.actions['shiftobjects'].setEnabled(False)
        self.actions['mergelocations'].setEnabled(False)

        self.CreateAction('addarea', self.HandleAddNewArea, GetIcon('add'), 'Add New Area', 'Adds a new area/sublevel to this level', QtGui.QKeySequence('Ctrl+Alt+N'))
        self.CreateAction('importarea', self.HandleImportArea, None, 'Import Area from Level...', 'Imports an area/sublevel from another level file', QtGui.QKeySequence('Ctrl+Alt+O'))
        self.CreateAction('deletearea', self.HandleDeleteArea, GetIcon('delete'), 'Delete Current Area...', 'Deletes the area/sublevel currently open from the level', QtGui.QKeySequence('Ctrl+Alt+D'))

        self.CreateAction('reloadgfx', self.ReloadTilesets, GetIcon('reload'), 'Reload Tilesets', 'Reloads the tileset data files, including any changes made since the level was loaded', QtGui.QKeySequence('Ctrl+Shift+R'))

        # create a menubar
        menubar = self.menuBar()

        fmenu = menubar.addMenu('&File')
        fmenu.addAction(self.actions['newlevel'])
        fmenu.addAction(self.actions['openfromname'])
        fmenu.addAction(self.actions['openfromfile'])
        fmenu.addSeparator()
        fmenu.addAction(self.actions['save'])
        fmenu.addAction(self.actions['saveas'])
        fmenu.addAction(self.actions['metainfo'])
        fmenu.addSeparator()
        fmenu.addAction(self.actions['screenshot'])
        fmenu.addAction(self.actions['changegamepath'])
        fmenu.addSeparator()
        fmenu.addAction(self.actions['exit'])

        emenu = menubar.addMenu('&Edit')
#        emenu.addAction(self.actions['undo'])
#        emenu.addAction(self.actions['redo'])
#        emenu.addSeparator()
        emenu.addAction(self.actions['selectall'])
        emenu.addSeparator()
        emenu.addAction(self.actions['cut'])
        emenu.addAction(self.actions['copy'])
        emenu.addAction(self.actions['paste'])
        emenu.addSeparator()
        emenu.addAction(self.actions['shiftobjects'])
        emenu.addAction(self.actions['mergelocations'])
        emenu.addSeparator()
        emenu.addAction(self.actions['freezeobjects'])
        emenu.addAction(self.actions['freezesprites'])
        emenu.addAction(self.actions['freezeentrances'])
        emenu.addAction(self.actions['freezelocations'])
        emenu.addAction(self.actions['freezepaths'])

        vmenu = menubar.addMenu('&View')
        vmenu.addAction(self.actions['showlayer0'])
        vmenu.addAction(self.actions['showlayer1'])
        vmenu.addAction(self.actions['showlayer2'])
        vmenu.addAction(self.actions['showsprites'])
        vmenu.addAction(self.actions['showspriteimages'])
        vmenu.addAction(self.actions['showentrances'])
        vmenu.addAction(self.actions['showlocations'])
        vmenu.addAction(self.actions['showpaths'])
        vmenu.addSeparator()
        vmenu.addAction(self.actions['tsetslots'])
        vmenu.addSeparator()
        vmenu.addAction(self.actions['grid'])
        vmenu.addSeparator()
        vmenu.addAction(self.actions['zoommax'])
        vmenu.addAction(self.actions['zoomin'])
        vmenu.addAction(self.actions['zoomactual'])
        vmenu.addAction(self.actions['zoomout'])
        vmenu.addAction(self.actions['zoommin'])
        vmenu.addSeparator()
        vmenu.addAction(self.actions['darkmode'])
        vmenu.addAction(self.actions['fullscreen'])
        vmenu.addSeparator()
        # self.levelOverviewDock.toggleViewAction() is added here later
        # so we assign it to self.vmenu
        self.vmenu = vmenu

        lmenu = menubar.addMenu('&Settings')
        lmenu.addAction(self.actions['areaoptions'])
        lmenu.addAction(self.actions['zones'])
        lmenu.addAction(self.actions['backgrounds'])
        lmenu.addAction(self.actions['camprofiles'])
        lmenu.addSeparator()
        lmenu.addAction(self.actions['addarea'])
        lmenu.addAction(self.actions['importarea'])
        lmenu.addAction(self.actions['deletearea'])
        lmenu.addSeparator()
        lmenu.addAction(self.actions['reloadgfx'])

        if HaveNSMBLib:
            if hasattr(nsmblib, 'getUpdatedVersion'):
                updatedVersion = nsmblib.getUpdatedVersion()
                updatedVersionStr = '%04d.%02d.%02d.%d' % (updatedVersion // 1000000,
                                                           (updatedVersion // 10000) % 100,
                                                           (updatedVersion // 100) % 100,
                                                           updatedVersion % 100)
                nsmblib_msg = 'Using NSMBLib-Updated %s' % (updatedVersionStr)
            else:
                nsmblib_msg = 'Using NSMBLib %d' % nsmblib.getVersion()
        else:
            nsmblib_msg = 'Not using NSMBLib'

        hmenu = menubar.addMenu('&Help')
        hmenu.addAction(self.actions['infobox'])
        hmenu.addAction(self.actions['helpbox'])
        hmenu.addAction(self.actions['tipbox'])
        hmenu.addSeparator()
        hmenu.addAction(self.actions['aboutqt'])
        hmenu.addSeparator()
        pyVerAct = hmenu.addAction('Using Python %d.%d.%d' % sys.version_info[:3])
        pyVerAct.setEnabled(False)
        bindingsVerAct = hmenu.addAction('Using %s %d.%d.%d' % (QtName, QtBindingsVersion[0], QtBindingsVersion[1], QtBindingsVersion[2]))
        bindingsVerAct.setEnabled(False)
        qtVerAct = hmenu.addAction('Using Qt %d.%d.%d' % QtCompatVersion)
        qtVerAct.setEnabled(False)
        nsmblibVerAct = hmenu.addAction(nsmblib_msg)
        nsmblibVerAct.setEnabled(False)

        # create a toolbar
        self.toolbar = self.addToolBar('Level Editor')
        self.toolbar.setObjectName('maintoolbar') #needed for the state to save/restore correctly
        self.toolbar.addAction(self.actions['newlevel'])
        self.toolbar.addAction(self.actions['openfromname'])
        self.toolbar.addAction(self.actions['save'])
        self.toolbar.addAction(self.actions['screenshot'])
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.actions['cut'])
        self.toolbar.addAction(self.actions['copy'])
        self.toolbar.addAction(self.actions['paste'])
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.actions['zoommax'])
        self.toolbar.addAction(self.actions['zoomin'])
        self.toolbar.addAction(self.actions['zoomactual'])
        self.toolbar.addAction(self.actions['zoomout'])
        self.toolbar.addAction(self.actions['zoommin'])
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.actions['grid'])
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.actions['showlayer0'])
        self.toolbar.addAction(self.actions['showlayer1'])
        self.toolbar.addAction(self.actions['showlayer2'])
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.actions['areaoptions'])
        self.toolbar.addAction(self.actions['zones'])
        self.toolbar.addAction(self.actions['backgrounds'])
        self.toolbar.addSeparator()

        self.areaComboBox = QtWidgets.QComboBox()
        self.areaComboBox.activated.connect(self.HandleSwitchArea)
        self.toolbar.addWidget(self.areaComboBox)



    def SetupDocksAndPanels(self):
        """Sets up the dock widgets and panels"""
        # level overview
        dock = QtWidgets.QDockWidget('Level Overview', self)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable)
        #dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setObjectName('leveloverview') #needed for the state to save/restore correctly

        self.levelOverview = LevelOverviewWidget()
        self.levelOverview.moveIt.connect(self.HandleOverviewClick)
        self.levelOverviewDock = dock
        dock.setWidget(self.levelOverview)


        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.setVisible(True)
        act = dock.toggleViewAction()
        act.setShortcut(QtGui.QKeySequence('Ctrl+M'))
        self.vmenu.addAction(act)

        # create the sprite editor panel
        dock = ItemEditorDockWidget('Modify Selected Sprite Properties', self)
        dock.setActive(False)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setObjectName('spriteeditor') #needed for the state to save/restore correctly

        self.spriteDataEditor = SpriteEditorWidget()
        self.spriteDataEditor.DataUpdate.connect(self.SpriteDataUpdated)
        dock.setWidget(self.spriteDataEditor)
        self.spriteEditorDock = dock

        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.setFloating(True)

        # create the entrance editor panel
        dock = ItemEditorDockWidget('Modify Selected Entrance Properties', self)
        dock.setActive(False)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setObjectName('entranceeditor') #needed for the state to save/restore correctly

        self.entranceEditor = EntranceEditorWidget()
        dock.setWidget(self.entranceEditor)
        self.entranceEditorDock = dock

        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.setFloating(True)

        # create the path editor panel
        dock = ItemEditorDockWidget('Modify Selected Path Node Properties', self)
        dock.setActive(False)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setObjectName('pathnodeeditor') #needed for the state to save/restore correctly

        self.pathEditor = PathNodeEditorWidget()
        dock.setWidget(self.pathEditor)

        self.pathEditorDock = dock

        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.setFloating(True)

        # create the location editor panel
        dock = ItemEditorDockWidget('Modify Selected Location Properties', self)
        dock.setActive(False)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setObjectName('locationeditor') #needed for the state to save/restore correctly

        self.locationEditor = LocationEditorWidget()
        dock.setWidget(self.locationEditor)
        self.locationEditorDock = dock

        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.setFloating(True)

        # create the palette
        dock = QtWidgets.QDockWidget('Palette', self)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setObjectName('palette') #needed for the state to save/restore correctly
        self.creationDock = dock
        act = dock.toggleViewAction()
        act.setShortcut(QtGui.QKeySequence('Ctrl+P'))
        self.vmenu.addAction(act)

        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.setVisible(True)

        # add tabs to it
        tabsWrapper = QtWidgets.QWidget()
        tabsWrapperLayout = QtWidgets.QVBoxLayout(tabsWrapper)
        if app.style().metaObject().className() == 'QMacStyle':
            # workaround for a weird macOS bug where the tab bar is too
            # high
            tabsWrapperLayout.setContentsMargins(0, 12, 0, 0)
        else:
            tabsWrapperLayout.setContentsMargins(0, 0, 0, 0)

        tabs = QtWidgets.QTabWidget()
        tabsWrapperLayout.addWidget(tabs)
        tabBar = QtWidgets.QTabBar()
        tabBar.setUsesScrollButtons(True)  # for macOS
        tabs.setTabBar(tabBar)
        tabs.setIconSize(QtCore.QSize(16, 16))
        tabs.currentChanged.connect(self.CreationTabChanged)
        dock.setWidget(tabsWrapper)
        self.creationTabs = tabs

        # object choosing tabs
        tsicon = GetIcon('objects')

        self.objTS0Tab = QtWidgets.QWidget()
        self.objTS1Tab = QtWidgets.QWidget()
        self.objTS2Tab = QtWidgets.QWidget()
        self.objTS3Tab = QtWidgets.QWidget()
        tabs.addTab(self.objTS0Tab, tsicon, '1')
        tabs.setTabToolTip(tabs.count() - 1, 'Tileset 1')
        tabs.addTab(self.objTS1Tab, tsicon, '2')
        tabs.setTabToolTip(tabs.count() - 1, 'Tileset 2')
        tabs.addTab(self.objTS2Tab, tsicon, '3')
        tabs.setTabToolTip(tabs.count() - 1, 'Tileset 3')
        tabs.addTab(self.objTS3Tab, tsicon, '4')
        tabs.setTabToolTip(tabs.count() - 1, 'Tileset 4')

        oel = QtWidgets.QVBoxLayout(self.objTS0Tab)
        self.createObjectLayout = oel

        ll = QtWidgets.QHBoxLayout()
        self.objUseLayer0 = QtWidgets.QRadioButton('0')
        self.objUseLayer0.setToolTip("<b>Layer 0:</b><br>This layer is mostly used for the hidden Yoshi's Island-style caves, but can also be used to overlay tiles to create effects. The flashlight effect will occur if Mario walks behind a tile on layer 0 and the zone has it enabled.")
        self.objUseLayer1 = QtWidgets.QRadioButton('1')
        self.objUseLayer1.setToolTip('<b>Layer 1:</b><br>All or most of your normal level objects should be placed on this layer. This is the only layer where tile interactions (solids, slopes, etc) will work.')
        self.objUseLayer2 = QtWidgets.QRadioButton('2')
        self.objUseLayer2.setToolTip('<b>Layer 2:</b><br>Background/wall tiles (such as those in the hidden caves) should be placed on this layer. Tiles on layer 2 have no effect on collisions.')
        ll.addWidget(QtWidgets.QLabel('Paint on Layer:'))
        ll.addWidget(self.objUseLayer0)
        ll.addWidget(self.objUseLayer1)
        ll.addWidget(self.objUseLayer2)
        ll.addStretch(1)
        oel.addLayout(ll)

        lbg = QtWidgets.QButtonGroup(self)
        lbg.addButton(self.objUseLayer0, 0)
        lbg.addButton(self.objUseLayer1, 1)
        lbg.addButton(self.objUseLayer2, 2)
        qm(lbg).idClicked.connect(self.LayerChoiceChanged)
        self.LayerButtonGroup = lbg

        self.objPicker = ObjectPickerWidget()
        self.objPicker.ObjChanged.connect(self.ObjectChoiceChanged)
        self.objPicker.ObjReplace.connect(self.ObjectReplace)
        oel.addWidget(self.objPicker, 1)

        # sprite choosing tabs
        self.sprPickerTab = QtWidgets.QWidget()
        tabs.addTab(self.sprPickerTab, GetIcon('sprites'), '')
        tabs.setTabToolTip(tabs.count() - 1, 'Sprites')

        spl = QtWidgets.QVBoxLayout(self.sprPickerTab)
        self.sprPickerLayout = spl

        svpl = QtWidgets.QHBoxLayout()
        svpl.addWidget(QtWidgets.QLabel('View:'))

        sspl = QtWidgets.QHBoxLayout()
        sspl.addWidget(QtWidgets.QLabel('Search:'))

        LoadSpriteCategories()
        viewpicker = QtWidgets.QComboBox()
        for view in SpriteCategories:
            viewpicker.addItem(view[0])
        viewpicker.currentIndexChanged.connect(self.SelectNewSpriteView)

        self.spriteViewPicker = viewpicker
        svpl.addWidget(viewpicker, 1)

        self.spriteSearchTerm = QtWidgets.QLineEdit()
        self.spriteSearchTerm.textChanged.connect(self.NewSearchTerm)
        sspl.addWidget(self.spriteSearchTerm, 1)

        spl.addLayout(svpl)
        spl.addLayout(sspl)

        self.spriteSearchLayout = sspl
        sspl.itemAt(0).widget().setVisible(False)
        sspl.itemAt(1).widget().setVisible(False)

        self.sprPicker = SpritePickerWidget()
        self.sprPicker.SpriteChanged.connect(self.SpriteChoiceChanged)
        self.sprPicker.SpriteReplace.connect(self.SpriteReplace)
        self.sprPicker.SwitchView(SpriteCategories[0])
        spl.addWidget(self.sprPicker, 1)

        viewpicker.setCurrentIndex(int(qm(settings.value('SpriteView', 0))))

        self.defaultPropButton = QtWidgets.QPushButton('Set Default Properties')
        self.defaultPropButton.setEnabled(False)
        self.defaultPropButton.clicked.connect(self.ShowDefaultProps)

        sdpl = QtWidgets.QHBoxLayout()
        sdpl.addStretch(1)
        sdpl.addWidget(self.defaultPropButton)
        sdpl.addStretch(1)
        spl.addLayout(sdpl)

        # default data editor
        ddock = QtWidgets.QDockWidget('Default Properties', self)
        ddock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable)
        ddock.setAllowedAreas(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea | QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        ddock.setObjectName('defaultprops') #needed for the state to save/restore correctly

        self.defaultDataEditor = SpriteEditorWidget()
        self.defaultDataEditor.setVisible(False)
        ddock.setWidget(self.defaultDataEditor)

        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, ddock)
        ddock.setVisible(False)
        ddock.setFloating(True)
        self.defaultPropDock = ddock

        # entrance tab
        self.entEditorTab = QtWidgets.QWidget()
        tabs.addTab(self.entEditorTab, GetIcon('entrances'), '')
        tabs.setTabToolTip(tabs.count() - 1, 'Entrances')

        eel = QtWidgets.QVBoxLayout(self.entEditorTab)
        self.entEditorLayout = eel

        elabel = QtWidgets.QLabel('Entrances currently in the level:<br>(Double-click one to jump to it instantly)')
        elabel.setWordWrap(True)
        self.entranceList = QtWidgets.QListWidget()
        self.entranceList.itemActivated.connect(self.HandleEntranceSelectByList)

        eel.addWidget(elabel)
        eel.addWidget(self.entranceList)

        # paths tab
        self.pathEditorTab = QtWidgets.QWidget()
        tabs.addTab(self.pathEditorTab, GetIcon('paths'), '')
        tabs.setTabToolTip(tabs.count() - 1, 'Paths')

        pathel = QtWidgets.QVBoxLayout(self.pathEditorTab)
        self.pathEditorLayout = pathel

        pathlabel = QtWidgets.QLabel('Path nodes currently in the level:<br>(Double-click one to jump to its first node instantly)<br>To delete a path, remove all its nodes one by one.<br>To add new paths, hit the button below and right click.')
        pathlabel.setWordWrap(True)
        deselectbtn = QtWidgets.QPushButton('Deselect (then right click for new path)')
        deselectbtn.clicked.connect(self.DeselectPathSelection)
        self.pathList = QtWidgets.QListWidget()
        self.pathList.itemActivated.connect(self.HandlePathSelectByList)

        pathel.addWidget(pathlabel)
        pathel.addWidget(deselectbtn)
        pathel.addWidget(self.pathList)

    def DeselectPathSelection(self, checked):
        """meh"""
        for selecteditem in self.pathList.selectedItems():
            selecteditem.setSelected(False)

    @QtCoreSlot()
    def Autosave(self):
        """Auto saves the level"""
        #print('Saving!')
        global AutoSaveDirty
        if not AutoSaveDirty: return

        data = Level.save(compress=False)
        settings.setValue('AutoSaveFilePath', Level.arcname)
        settings.setValue('AutoSaveFileData', QtCore.QByteArray(data))
        AutoSaveDirty = False
        #print('Level autosaved')


    @QtCoreSlot()
    def TrackClipboardUpdates(self):
        """Catches systemwide clipboard updates"""
        clip = self.systemClipboard.text()
        if clip is not None and clip != '':
            try:
                clip = unicode(clip).strip()
            except UnicodeEncodeError:
                # HELLO MY NAME IS PYTHON 2.X.
                # I AM OLD AND THEREFORE I FAIL AT PUTTING ANYTHING
                # HIGHER THAN \x7F INTO A STR. THANKS!
                self.clipboard = None
                self.actions['paste'].setEnabled(False)
                return

            if clip.startswith('ReggieClip|') and clip.endswith('|%'):
                self.clipboard = clip.replace(' ', '').replace('\n', '').replace('\r', '').replace('\t', '')
                self.actions['paste'].setEnabled(True)
            else:
                self.clipboard = None
                self.actions['paste'].setEnabled(False)


    # We limit how often the level overview can be updated in order to
    # improve efficiency -- in particular, this helps on macOS when
    # running from a .dmg file
    lastOverviewUpdateTimeViaScrolling = 0
    OVERVIEW_SCROLL_UPDATE_INTERVAL = 0.1 # seconds

    @QtCoreSlot(int)
    def XScrollChange(self, pos):
        """Moves the Overview current position box based on X scroll bar value"""
        self.levelOverview.Xposlocator = pos

        currentTime = time.time()
        if currentTime - self.lastOverviewUpdateTimeViaScrolling > self.OVERVIEW_SCROLL_UPDATE_INTERVAL:
            self.lastOverviewUpdateTimeViaScrolling = currentTime
            self.levelOverview.update()

    @QtCoreSlot(int)
    def YScrollChange(self, pos):
        """Moves the Overview current position box based on Y scroll bar value"""
        self.levelOverview.Yposlocator = pos

        currentTime = time.time()
        if currentTime - self.lastOverviewUpdateTimeViaScrolling > self.OVERVIEW_SCROLL_UPDATE_INTERVAL:
            self.lastOverviewUpdateTimeViaScrolling = currentTime
            self.levelOverview.update()

    @QtCoreSlot(int, int)
    def HandleWindowSizeChange(self, w, h):
        self.levelOverview.Hlocator = h
        self.levelOverview.Wlocator = w
        self.levelOverview.update()

    def UpdateTitle(self):
        """Sets the window title accordingly"""
        windowTitle = Level.filename + (' [unsaved]' if Dirty else '')
        if QtCompatVersion < (5,0,0):
            # On Qt 4, we can't use setApplicationDisplayName(), so
            # we have to append ApplicationDisplayName manually.
            # I'm also avoiding using unicode literals (u'') because
            # some versions of Python 3 don't support them.
            windowTitle += b' \xe2\x80\x94 '.decode('utf-8') + ApplicationDisplayName
        self.setWindowTitle(windowTitle)


    def CheckDirty(self):
        """Checks if the level is unsaved and asks for a confirmation if so - if it returns True, Cancel was picked"""
        if not Dirty: return False

        msg = QtWidgets.QMessageBox()
        msg.setText('The level has unsaved changes in it.')
        msg.setInformativeText('Do you want to save them?')
        msg.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Discard | QtWidgets.QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Save)
        ret = execQtObject(msg)

        if ret == QtWidgets.QMessageBox.StandardButton.Save:
            if not self.HandleSave():
                # save failed
                return True
            return False
        elif ret == QtWidgets.QMessageBox.StandardButton.Discard:
            return False
        elif ret == QtWidgets.QMessageBox.StandardButton.Cancel:
            return True

    @QtCoreSlot()
    def InfoBox(self):
        """Shows the about box"""
        execQtObject(AboutDialog())
        return

    @QtCoreSlot()
    def HandlePaths(self):
        """Creates the Path Editing mode"""

        # Step 1: Freeze all scene items
        # Step 2: Gray out the scene
        # Step 3: Exchange the palette dock with a path palette
        # Step 4: Allow the creation of path nodes on scene through right-clicking
        # Step 5: Floating Palette to allow editing of the numerical values manually
        # Step 6: Chain path nodes together in order
        # Step 7: Smart handling of node creation and deletion
        # Step 8: UI intuitive visualization of accel/speed
        # Step 9: Mouse controls for the above
        # Step 10: Attaching an instance of the path to all sprites which refer to it

        return


    @QtCoreSlot()
    def HandleInfo(self):
        """Records the Level Meta Information"""
        if Level.areanum == 1:
            dlg = MetaInfoDialog()
            if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
                Level.Title = dlg.levelName.text()
                Level.Author = dlg.Author.text()
                Level.Group = dlg.Group.text()
                Level.Webpage = dlg.Website.text()

                SetDirty()
                return
        else:
            dlg = QtWidgets.QMessageBox()
            dlg.setText('Sorry!\n\nYou can only view or edit Level Information in Area 1.')
            execQtObject(dlg)

    @QtCoreSlot()
    def HelpBox(self):
        """Shows the help box"""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.join(module_path(), 'reggiedata', 'help', 'index.html')))


    @QtCoreSlot()
    def TipBox(self):
        """Reggie! Tips and Commands"""
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.join(module_path(), 'reggiedata', 'help', 'tips.html')))


    @QtCoreSlot()
    def SelectAll(self):
        """Selects all objects in the level"""
        paintRect = QtGui.QPainterPath()
        paintRect.addRect(float(0), float(0), float(1024*24), float(512*24))
        self.scene.setSelectionArea(paintRect)


    @QtCoreSlot()
    def Cut(self):
        """Cuts the selected items"""
        self.SelectionUpdateFlag = True
        selitems = self.scene.selectedItems()
        self.scene.clearSelection()

        if len(selitems) > 0:
            clipboard_o = []
            clipboard_s = []
            ii = isinstance
            type_obj = LevelObjectEditorItem
            type_spr = SpriteEditorItem

            for obj in selitems:
                if ii(obj, type_obj):
                    obj.delete()
                    obj.setSelected(False)
                    self.scene.removeItem(obj)
                    clipboard_o.append(obj)
                elif ii(obj, type_spr):
                    obj.delete()
                    obj.setSelected(False)
                    self.scene.removeItem(obj)
                    clipboard_s.append(obj)

            if len(clipboard_o) > 0 or len(clipboard_s) > 0:
                SetDirty()
                self.actions['cut'].setEnabled(False)
                self.actions['paste'].setEnabled(True)
                self.clipboard = self.encodeObjects(clipboard_o, clipboard_s)
                self.systemClipboard.setText(self.clipboard)

        self.levelOverview.update()
        self.SelectionUpdateFlag = False
        self.ChangeSelectionHandler()

    @QtCoreSlot()
    def Copy(self):
        """Copies the selected items"""
        selitems = self.scene.selectedItems()
        if len(selitems) > 0:
            clipboard_o = []
            clipboard_s = []
            ii = isinstance
            type_obj = LevelObjectEditorItem
            type_spr = SpriteEditorItem

            for obj in selitems:
                if ii(obj, type_obj):
                    clipboard_o.append(obj)
                elif ii(obj, type_spr):
                    clipboard_s.append(obj)

            if len(clipboard_o) > 0 or len(clipboard_s) > 0:
                self.actions['paste'].setEnabled(True)
                self.clipboard = self.encodeObjects(clipboard_o, clipboard_s)
                self.systemClipboard.setText(self.clipboard)

    @QtCoreSlot()
    def Paste(self):
        """Paste the selected items"""
        if self.clipboard is not None:
            self.placeEncodedObjects(self.clipboard)

    def encodeObjects(self, clipboard_o, clipboard_s):
        """Encode a set of objects and sprites into a string"""
        convclip = ['ReggieClip']

        # get objects
        clipboard_o.sort(key=lambda x: x.zValue())

        for item in clipboard_o:
            convclip.append('0:%d:%d:%d:%d:%d:%d:%d' % (item.tileset, item.type, item.layer, item.objx, item.objy, item.width, item.height))

        # get sprites
        o = ord
        for item in clipboard_s:
            data = item.spritedata
            convclip.append('1:%d:%d:%d:%d:%d:%d:%d:%d:%d:%d' % (item.type, item.objx, item.objy, o(data[0]), o(data[1]), o(data[2]), o(data[3]), o(data[4]), o(data[5]), o(data[7])))

        convclip.append('%')
        return '|'.join(convclip)

    def placeEncodedObjects(self, encoded):
        """Decode and place a set of objects"""
        self.SelectionUpdateFlag = True
        self.scene.clearSelection()
        layer0 = []
        layer1 = []
        layer2 = []
        added = []

        x1 = 1024
        x2 = 0
        y1 = 512
        y2 = 0

        global OverrideSnapping
        OverrideSnapping = True

        # possibly a small optimisation
        type_obj = LevelObjectEditorItem
        type_spr = SpriteEditorItem
        func_len = len
        func_chr = chr
        func_map = map
        func_int = int
        func_ii = isinstance
        layers = Level.layers
        sprites = Level.sprites
        scene = self.scene

        try:
            clip = encoded[11:-2].split('|')

            if func_len(clip) > 300:
                result = QtWidgets.QMessageBox.warning(self, 'Reggie!', "You're trying to paste over 300 items at once.\nThis may take a while (depending on your computer speed), are you sure you want to continue?", QtWidgets.QMessageBox.StandardButton.Yes, QtWidgets.QMessageBox.StandardButton.No)
                if result == QtWidgets.QMessageBox.StandardButton.No:
                    return

            for item in clip:
                # Check to see whether it's an object or sprite
                # and add it to the correct stack
                split = item.split(':')
                if split[0] == '0':
                    # object
                    if func_len(split) != 8: continue

                    tileset = func_int(split[1])
                    type = func_int(split[2])
                    layer = func_int(split[3])
                    objx = func_int(split[4])
                    objy = func_int(split[5])
                    width = func_int(split[6])
                    height = func_int(split[7])

                    # basic sanity checks
                    if tileset < 0 or tileset > 3: continue
                    if type < 0 or type > 255: continue
                    if layer < 0 or layer > 2: continue
                    if objx < 0 or objx > 1023: continue
                    if objy < 0 or objy > 511: continue
                    if width < 1 or width > 1023: continue
                    if height < 1 or height > 511: continue

                    xs = objx
                    xe = objx+width-1
                    ys = objy
                    ye = objy+height-1
                    if xs < x1: x1 = xs
                    if xe > x2: x2 = xe
                    if ys < y1: y1 = ys
                    if ye > y2: y2 = ye

                    newitem = type_obj(tileset, type, layer, objx, objy, width, height, 1)
                    added.append(newitem)
                    scene.addItem(newitem)
                    newitem.setSelected(True)
                    if layer == 0:
                        layer0.append(newitem)
                    elif layer == 1:
                        layer1.append(newitem)
                    else:
                        layer2.append(newitem)

                elif split[0] == '1':
                    # sprite
                    if func_len(split) != 11: continue

                    objx = func_int(split[2])
                    objy = func_int(split[3])
                    if sys.version_info.major < 3:
                        data = ''.join(func_map(func_chr, func_map(func_int, [split[4], split[5], split[6], split[7], split[8], split[9], '0', split[10]])))
                    else:
                        data = bytes(func_map(func_int, [split[4], split[5], split[6], split[7], split[8], split[9], '0', split[10]]))

                    x = objx / 16
                    y = objy / 16
                    if x < x1: x1 = x
                    if x > x2: x2 = x
                    if y < y1: y1 = y
                    if y > y2: y2 = y

                    newitem = type_spr(func_int(split[1]), objx, objy, data)
                    sprites.append(newitem)
                    added.append(newitem)
                    scene.addItem(newitem)
                    newitem.setSelected(True)

        except ValueError:
            # an int() probably failed somewhere
            pass

        if func_len(layer0) > 0:
            layer = layers[0]
            if func_len(layer) > 0:
                z = layer[-1].zValue() + 1
            else:
                z = 16384
            for obj in layer0:
                layer.append(obj)
                obj.setZValue(z)
                z += 1

        if func_len(layer1) > 0:
            layer = layers[1]
            if func_len(layer) > 0:
                z = layer[-1].zValue() + 1
            else:
                z = 8192
            for obj in layer1:
                layer.append(obj)
                obj.setZValue(z)
                z += 1

        if func_len(layer2) > 0:
            layer = layers[2]
            if func_len(layer) > 0:
                z = layer[-1].zValue() + 1
            else:
                z = 0
            for obj in layer2:
                layer.append(obj)
                obj.setZValue(z)
                z += 1

        # now center everything
        zoomscaler = (self.ZoomLevel / 100.0)
        width = x2 - x1 + 1
        height = y2 - y1 + 1
        viewportx = (self.view.XScrollBar.value() / zoomscaler) / 24
        viewporty = (self.view.YScrollBar.value() / zoomscaler) / 24
        viewportwidth = (self.view.width() / zoomscaler) / 24
        viewportheight = (self.view.height() / zoomscaler) / 24

        # tiles
        xoffset = int(0 - x1 + viewportx + ((viewportwidth / 2) - (width / 2)))
        yoffset = int(0 - y1 + viewporty + ((viewportheight / 2) - (height / 2)))
        xpixeloffset = int(0 - x1 + viewportx + ((viewportwidth / 2) - (width / 2))) * 16
        ypixeloffset = int(0 - y1 + viewporty + ((viewportheight / 2) - (height / 2))) * 16

        for item in added:
            if func_ii(item, type_spr):
                item.setPos((item.objx + xpixeloffset + item.xoffset) * 1.5, (item.objy + ypixeloffset + item.yoffset) * 1.5)
            elif func_ii(item, type_obj):
                item.setPos((item.objx + xoffset) * 24, (item.objy + yoffset) * 24)

        OverrideSnapping = False

        self.levelOverview.update()
        SetDirty()
        self.SelectionUpdateFlag = False
        self.ChangeSelectionHandler()


    @QtCoreSlot()
    def ShiftObjects(self):
        """Shifts the selected object(s)"""
        items = self.scene.selectedItems()
        if len(items) == 0: return

        dlg = ObjectShiftDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            xoffset = dlg.XOffset.value()
            yoffset = dlg.YOffset.value()
            if xoffset == 0 and yoffset == 0: return

            type_obj = LevelObjectEditorItem
            type_spr = SpriteEditorItem
            type_ent = EntranceEditorItem
            type_loc = LocationEditorItem

            if ((xoffset % 16) != 0) or ((yoffset % 16) != 0):
                # warn if any objects exist
                objectsExist = False
                spritesExist = False
                for obj in items:
                    if isinstance(obj, type_obj):
                        objectsExist = True
                    elif isinstance(obj, type_spr) or isinstance(obj, type_ent):
                        spritesExist = True

                if objectsExist and spritesExist:
                    # no point in warning them if there are only objects
                    # since then, it will just silently reduce the offset and it won't be noticed
                    result = QtWidgets.QMessageBox.information(None, 'Warning',  "You are trying to move object(s) by an offset which isn't a multiple of 16. It will work, but the objects will not be able to move exactly the same amount as the sprites. Are you sure you want to do this?", QtWidgets.QMessageBox.StandardButton.Yes, QtWidgets.QMessageBox.StandardButton.No)
                    if result == QtWidgets.QMessageBox.StandardButton.No:
                        return

            xpoffset = xoffset * 1.5
            ypoffset = yoffset * 1.5

            global OverrideSnapping
            OverrideSnapping = True

            for obj in items:
                obj.setPos(obj.x() + xpoffset, obj.y() + ypoffset)

            OverrideSnapping = False

            SetDirty()

    @QtCoreSlot()
    def MergeLocations(self):
        """Merges selected locations"""
        items = self.scene.selectedItems()
        if len(items) == 0: return

        newx = 999999
        newy = 999999
        neww = 0
        newh = 0

        type_loc = LocationEditorItem
        for obj in items:
            if isinstance(obj, type_loc):
                if obj.objx < newx:
                    newx = obj.objx
                if obj.objy < newy:
                    newy = obj.objy
                if obj.width + obj.objx > neww:
                    neww = obj.width + obj.objx
                if obj.height + obj.objy > newh:
                    newh = obj.height + obj.objy
                obj.delete()
                obj.setSelected(False)
                self.scene.removeItem(obj)
                self.levelOverview.update()
                SetDirty()

        if newx != 999999 and newy != 999999:
            allID = []
            newID = 1
            for i in Level.locations:
                allID.append(i.id)

            allID = set(allID) # faster "x in y" lookups for sets

            while newID <= 255:
                if newID not in allID:
                    break
                newID += 1

            loc = LocationEditorItem(newx, newy, neww - newx, newh - newy, newID)

            mw = mainWindow
            loc.positionChanged = mw.HandleObjPosChange
            mw.scene.addItem(loc)

            Level.locations.append(loc)
            loc.setSelected(True)


    @QtCoreSlot()
    def HandleAddNewArea(self):
        """Adds a new area to the level"""
        if Level.areacount >= 4:
            QtWidgets.QMessageBox.warning(self, 'Reggie!', "You have reached the maximum amount of areas in this level.\nDue to the game's limitations, Reggie! only allows you to add up to 4 areas to a level.")
            return

        if self.CheckDirty():
            return

        with open('reggiedata/blankcourse.bin', 'rb') as getit:
            blank = getit.read()

        newID = Level.areacount + 1
        Level.arc['course/course%d.bin' % newID] = blank

        if not self.HandleSave(): return
        self.LoadLevel(Level.arcname, newID)


    @QtCoreSlot()
    def HandleImportArea(self):
        """Imports an area from another level"""
        if Level.areacount >= 4:
            QtWidgets.QMessageBox.warning(self, 'Reggie!', "You have reached the maximum amount of areas in this level.\nDue to the game's limitations, Reggie! only allows you to add up to 4 areas to a level.")
            return

        if self.CheckDirty():
            return

        fn = qm(QtWidgets.QFileDialog.getOpenFileName)(self, 'Choose a level archive', '', LEVEL_FILE_FORMATS_FILTER_OPEN)[0]
        if fn == '': return

        with open(unicode(fn), 'rb') as getit:
            arcdata = getit.read()

        arc = archive.U8.load(arcdata)

        # get the area count
        areacount = 0

        for item,val in arc.files:
            if val is not None:
                # it's a file
                fname = item[item.rfind('/')+1:]
                if fname.startswith('course'):
                    maxarea = int(fname[6])
                    if maxarea > areacount: areacount = maxarea

        # choose one
        dlg = AreaChoiceDialog(areacount)
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Rejected:
            return

        area = dlg.areaCombo.currentIndex()+1

        # get the required files
        reqcourse = 'course%d.bin' % area
        reql0 = 'course%d_bgdatL0.bin' % area
        reql1 = 'course%d_bgdatL1.bin' % area
        reql2 = 'course%d_bgdatL2.bin' % area

        course = None
        l0 = None
        l1 = None
        l2 = None

        for item,val in arc.files:
            if val is not None:
                fname = item[item.rfind('/')+1:]
                if fname == reqcourse:
                    course = val
                elif fname == reql0:
                    l0 = val
                elif fname == reql1:
                    l1 = val
                elif fname == reql2:
                    l2 = val

        # add them to our U8
        newID = Level.areacount + 1
        Level.arc['course/course%d.bin' % newID] = course
        if l0 is not None: Level.arc['course/course%d_bgdatL0.bin' % newID] = l0
        if l1 is not None: Level.arc['course/course%d_bgdatL1.bin' % newID] = l1
        if l2 is not None: Level.arc['course/course%d_bgdatL2.bin' % newID] = l2

        if not self.HandleSave(): return
        self.LoadLevel(Level.arcname, newID)


    @QtCoreSlot()
    def HandleDeleteArea(self):
        """Deletes the current area"""
        result = QtWidgets.QMessageBox.warning(self, 'Reggie!', 'Are you <b>sure</b> you want to delete this area?<br><br>The level will automatically save afterwards - there is no way<br>you can undo the deletion or get it back afterwards!', QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No, QtWidgets.QMessageBox.StandardButton.No)
        if result == QtWidgets.QMessageBox.StandardButton.No: return

        if not self.HandleSave(): return

        # this is really going to be annoying >_<
        deleting = Level.areanum

        newfiles = []
        for item,val in Level.arc.files:
            if val is not None:
                if item.startswith('course/course'):
                    id = int(item[13])
                    if id < deleting:
                        # pass it through anyway
                        newfiles.append((item,val))
                    elif id == deleting:
                        # remove it
                        continue
                    else:
                        # push the number down by one
                        fname = 'course/course%d%s' % (id - 1, item[14:])
                        newfiles.append((fname,val))
            else:
                newfiles.append((item,val))

        Level.arc.files = newfiles

        # no error checking. if it saved last time, it will probably work now
        with open(Level.arcname, 'wb') as f:
            f.write(Level.arc._dump())
        self.LoadLevel(Level.arcname, 1)


    @QtCoreSlot()
    def HandleChangeGamePath(self):
        """Change the game path used"""
        if self.CheckDirty(): return

        path = PromptUserForNewGamePath()
        if path:
            settings.setValue('GamePath', path)
            SetGamePath(path)
            self.LoadLevelFromName('01-01', 1)


    @QtCoreSlot()
    def HandleNewLevel(self):
        """Create a new level"""
        if self.CheckDirty(): return
        self.LoadNewLevel()


    @QtCoreSlot()
    def HandleOpenFromName(self):
        """Open a level using the level picker"""
        if self.CheckDirty(): return

        dlg = ChooseLevelNameDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            #start = time.time()
            self.LoadLevelFromName(dlg.currentlevel, 1)
            #end = time.time()
            #print('Loaded in ' + str(end - start))


    @QtCoreSlot()
    def HandleOpenFromFile(self):
        """Open a level using the filename"""
        if self.CheckDirty(): return

        if Level.hasName:
            dirname = os.path.dirname(Level.arcname)
        else:
            dirname = ''

        fn = qm(QtWidgets.QFileDialog.getOpenFileName)(self, 'Choose a level archive', dirname, LEVEL_FILE_FORMATS_FILTER_OPEN)[0]
        if fn == '': return
        self.LoadLevel(unicode(fn), 1)


    @QtCoreSlot()
    def HandleSave(self):
        """Save a level back to the archive"""
        if not Level.hasName:
            return self.HandleSaveAs()

        global Dirty, AutoSaveDirty
        data = Level.save()
        try:
            with open(Level.arcname, 'wb') as f:
                f.write(data)
        except IOError as e:
            QtWidgets.QMessageBox.warning(None, 'Error', 'Error while Reggie was trying to save the level:\n(#%d) %s\n\n(Your work has not been saved! Try saving it under a different filename or in a different folder.)' % (e.args[0], e.args[1]))
            return False

        Dirty = False
        AutoSaveDirty = False
        self.UpdateTitle()

        settings.setValue('AutoSaveFilePath', Level.arcname)
        settings.setValue('AutoSaveFileData', b'x')
        return True


    @QtCoreSlot()
    def HandleSaveAs(self):
        """Save a level back to the archive, with a new filename"""
        if Level.isCompressed:
            default_filter = LEVEL_FILE_FORMATS_FILTER_ARC_LZ
        else:
            default_filter = LEVEL_FILE_FORMATS_FILTER_ARC

        fn = qm(QtWidgets.QFileDialog.getSaveFileName)(self, 'Choose a new filename', '', LEVEL_FILE_FORMATS_FILTER_SAVE, default_filter)[0]
        if fn == '': return False
        fn = unicode(fn)

        global Dirty, AutoSaveDirty
        Dirty = False
        AutoSaveDirty = False
        Dirty = False

        Level.arcname = fn
        Level.filename = os.path.basename(fn)
        Level.hasName = True
        Level.isCompressed = fn.lower().endswith('.lz')

        data = Level.save()
        try:
            with open(fn, 'wb') as f:
                f.write(data)
        except IOError as e:
            QtWidgets.QMessageBox.warning(None, 'Error', 'Error while Reggie was trying to save the level:\n(#%d) %s\n\n(Your work has not been saved! Try saving it under a different filename or in a different folder.)' % (e.args[0], e.args[1]))
            return False
        settings.setValue('AutoSaveFilePath', fn)
        settings.setValue('AutoSaveFileData', b'x')

        self.UpdateTitle()
        return True


    @QtCoreSlot()
    def HandleExit(self):
        """Exit the editor. Why would you want to do this anyway?"""
        self.close()


    @QtCoreSlot(int)
    def HandleSwitchArea(self, idx):
        """Handle activated signals for areaComboBox"""
        currentIdx = Level.areanum - 1

        if idx == currentIdx:
            return

        if self.CheckDirty() or not self.LoadLevel(Level.arcname, idx+1):
            self.areaComboBox.setCurrentIndex(currentIdx)


    @QtCoreSlot(bool)
    def HandleUpdateLayer0(self, checked):
        """Handle toggling of layer 0 being showed"""
        global ShowLayer0
        ShowLayer0 = checked

        for obj in Level.layers[0]:
            obj.setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdateLayer1(self, checked):
        """Handle toggling of layer 1 being showed"""
        global ShowLayer1
        ShowLayer1 = checked

        for obj in Level.layers[1]:
            obj.setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdateLayer2(self, checked):
        """Handle toggling of layer 2 being showed"""
        global ShowLayer2
        ShowLayer2 = checked

        for obj in Level.layers[2]:
            obj.setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdateSprites(self, checked):
        """Handle toggling of sprites being showed"""
        global ShowSprites
        ShowSprites = checked

        for spr in Level.sprites:
            spr.setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdateSpriteImages(self, checked):
        """Handle toggling of sprite images being showed"""
        global ShowSpriteImages
        ShowSpriteImages = checked

        for spr in Level.sprites:
            spr.InitialiseSprite()

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdateEntrances(self, checked):
        """Handle toggling of entrances being showed"""
        global ShowEntrances
        ShowEntrances = checked

        for ent in Level.entrances:
            ent.setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdateLocations(self, checked):
        """Handle toggling of locations being showed"""
        global ShowLocations
        ShowLocations = checked

        for loc in Level.locations:
            loc.setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleUpdatePaths(self, checked):
        """Handle toggling of paths being showed"""
        global ShowPaths
        ShowPaths = checked

        for node in Level.paths:
            node.setVisible(checked)

        for path in Level.pathdata:
            path['peline'].setVisible(checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleTilesetSlotsMod(self, checked):
        """Handle toggling of the tileset-slots mod"""
        settings.setValue('TilesetSlotsModEnabled', checked)

        global TilesetSlotsModEnabled
        TilesetSlotsModEnabled = checked

        for layer in Level.layers:
            for obj in layer:
                obj.updateObjCache()

        self.objPicker.LoadFromTilesets()

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleObjectsFreeze(self, checked):
        """Handle toggling of objects being frozen"""
        settings.setValue('FreezeObjects', checked)

        checked = not checked

        global ObjectsNonFrozen
        ObjectsNonFrozen = checked
        flag1 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flag2 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable

        for layer in Level.layers:
            for obj in layer:
                obj.setFlag(flag1, checked)
                obj.setFlag(flag2, checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleSpritesFreeze(self, checked):
        """Handle toggling of sprites being frozen"""
        settings.setValue('FreezeSprites', checked)

        checked = not checked

        global SpritesNonFrozen
        SpritesNonFrozen = checked
        flag1 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flag2 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable

        for spr in Level.sprites:
            spr.setFlag(flag1, checked)
            spr.setFlag(flag2, checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleEntrancesFreeze(self, checked):
        """Handle toggling of entrances being frozen"""
        settings.setValue('FreezeEntrances', checked)

        checked = not checked

        global EntrancesNonFrozen
        EntrancesNonFrozen = checked
        flag1 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flag2 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable

        for ent in Level.entrances:
            ent.setFlag(flag1, checked)
            ent.setFlag(flag2, checked)

        self.scene.update()

    @QtCoreSlot(bool)
    def HandlePathsFreeze(self, checked):
        """Handle toggling of paths being frozen"""
        settings.setValue('FreezePaths', checked)

        checked = not checked

        global PathsNonFrozen
        PathsNonFrozen = checked
        flag1 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flag2 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable

        for node in Level.paths:
            node.setFlag(flag1, checked)
            node.setFlag(flag2, checked)

        self.scene.update()

    @QtCoreSlot(bool)
    def HandleLocationsFreeze(self, checked):
        """Handle toggling of locations being frozen"""
        settings.setValue('FreezeLocations', checked)

        checked = not checked

        global LocationsNonFrozen
        LocationsNonFrozen = checked
        flag1 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flag2 = QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable

        for loc in Level.locations:
            loc.setFlag(flag1, checked)
            loc.setFlag(flag2, checked)

        self.scene.update()


    @QtCoreSlot(bool)
    def HandleShowGrid(self, checked):
        """Handle toggling of the grid being showed"""
        settings.setValue('GridEnabled', checked)

        global GridEnabled
        GridEnabled = checked
        self.scene.update()


    @QtCoreSlot(bool)
    def HandleDarkMode(self, checked):
        """Handle toggling of dark mode"""
        settings.setValue('DarkMode', checked)

        if checked != DarkMode:
            QtWidgets.QMessageBox.information(None, 'Dark Mode', 'This change will take effect when you restart Reggie!.')


    @QtCoreSlot(bool)
    def HandleFullScreenMode(self, checked):
        """Handle toggling of full-screen mode"""
        if self.isFullScreen():
            if self.wasMaximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self.wasMaximized = self.isMaximized()
            self.showFullScreen()
            shortcut = str(self.actions['fullscreen'].shortcut().toString())
            self.statusBar().showMessage('Press %s to exit full-screen mode.' % shortcut, 6000)


    @QtCoreSlot()
    def HandleZoomIn(self, towardsCursor=False):
        """Handle zooming in"""
        z = self.ZoomLevel
        zi = self.ZoomLevels.index(z)
        zi += 1
        if zi < len(self.ZoomLevels):
            self.ZoomTo(self.ZoomLevels[zi], towardsCursor=towardsCursor)


    @QtCoreSlot()
    def HandleZoomOut(self, towardsCursor=False):
        """Handle zooming out"""
        z = self.ZoomLevel
        zi = self.ZoomLevels.index(z)
        zi -= 1
        if zi >= 0:
            self.ZoomTo(self.ZoomLevels[zi], towardsCursor=towardsCursor)


    @QtCoreSlot()
    def HandleZoomActual(self):
        """Handle zooming to the actual size"""
        self.ZoomTo(100.0)

    @QtCoreSlot()
    def HandleZoomMin(self):
        """Handle zooming to the minimum size"""
        self.ZoomTo(10.0)

    @QtCoreSlot()
    def HandleZoomMax(self):
        """Handle zooming to the maximum size"""
        self.ZoomTo(300.0)


    def ZoomTo(self, z, towardsCursor=False):
        """Zoom to a specific level"""
        if towardsCursor:
            self.view.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        tr = QtGui.QTransform()
        tr.scale(z / 100.0, z / 100.0)
        self.ZoomLevel = z
        self.view.setTransform(tr)
        self.levelOverview.mainWindowScale = z/100.0

        if towardsCursor:
            # (reset back to original transformation anchor)
            self.view.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)

        zi = self.ZoomLevels.index(z)
        self.actions['zoomin'].setEnabled(zi < len(self.ZoomLevels) - 1)
        self.actions['zoomactual'].setEnabled(z != 100.0)
        self.actions['zoommin'].setEnabled(z != 10.0)
        self.actions['zoommax'].setEnabled(z != 300.0)
        self.actions['zoomout'].setEnabled(zi > 0)

        self.scene.update()


    @QtCoreSlot(int, int)
    def HandleOverviewClick(self, x, y):
        """Handle position changes from the level overview"""
        self.view.centerOn(x, y)
        self.levelOverview.update()


    def closeEvent(self, event):
        """Handler for the main window close event"""

        if self.CheckDirty():
            event.ignore()
        else:
            # save our state
            self.spriteEditorDock.setActive(False)
            self.entranceEditorDock.setActive(False)
            self.pathEditorDock.setActive(False)
            self.locationEditorDock.setActive(False)
            self.defaultPropDock.setVisible(False)

            settings.setValue('MainWindowGeometry', self.saveGeometry())
            settings.setValue('MainWindowState', self.saveState(0))

            if hasattr(self, 'HelpBoxInstance'):
                self.HelpBoxInstance.close()

            if hasattr(self, 'TipsBoxInstance'):
                self.TipsBoxInstance.close()

            settings.setValue('LastLevel', unicode(Level.arcname))

            settings.setValue('AutoSaveFilePath', 'none')
            settings.setValue('AutoSaveFileData', b'x')

            event.accept()


    def LoadLevelFromName(self, name, area):
        """Load a level from just its name (example: '01-01') and area number"""

        name_arc = '%s.arc' % name
        name_lz = '%s.arc.LZ' % name
        fullpath_arc = os.path.join(gamePath, name_arc)
        fullpath_lz = os.path.join(gamePath, name_lz)

        if os.path.isfile(fullpath_lz) and os.path.isfile(fullpath_arc):
            msg = QtWidgets.QMessageBox()
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            msg.setText('"%s" and "%s" both exist in the Stage folder.' % (name_arc, name_lz))
            msg.setInformativeText('Which would you like to load?')
            button_arc = msg.addButton(name_arc, QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            button_lz = msg.addButton(name_lz, QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            msg.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(button_lz)  # Newer prioritizes .LZ
            ret = execQtObject(msg)

            if msg.clickedButton() is button_arc:
                return self.LoadLevel(fullpath_arc, area)
            elif msg.clickedButton() is button_lz:
                return self.LoadLevel(fullpath_lz, area)
            else:
                return False

        elif os.path.isfile(fullpath_lz):
            return self.LoadLevel(fullpath_lz, area)

        return self.LoadLevel(fullpath_arc, area)


    def LoadLevelFromAutosave(self):
        """Load level data from the AutoSave* globals"""
        return self.LoadLevel(None, 1, autosave=True)


    def LoadNewLevel(self):
        """Load a blank level"""
        return self.LoadLevel(None, 1)


    def LoadLevel(self, name, area, autosave=False):
        """Load a level into the editor"""

        if name is not None:
            if not IsNSMBLevel(name):
                QtWidgets.QMessageBox.warning(self, 'Reggie!', "This file doesn't seem to be a valid level.", QtWidgets.QMessageBox.StandardButton.Ok)
                return False

        global Dirty, DirtyOverride
        Dirty = False
        DirtyOverride += 1

        # first clear out what we have
        self.scene.clearSelection()
        self.CurrentSelection = []
        self.scene.clear()

        # reset these here, because if the showlayer variables are set
        # after creating the objects, it uses the old values
        global CurrentLayer, ShowLayer0, ShowLayer1, ShowLayer2
        global ShowSprites, ShowSpriteImages
        global ShowEntrances
        global ShowLocations
        global ShowPaths
        CurrentLayer = 1
        ShowLayer0 = True
        ShowLayer1 = True
        ShowLayer2 = True
        ShowSprites = True
        ShowSpriteImages = True
        ShowEntrances = True
        ShowLocations = True
        ShowPaths = True


        # track progress.. but we'll only do this if we don't have
        # the NSMBLib because otherwise it's far too fast
        if HaveNSMBLib:
            progress = None
        else:
            progress = QtWidgets.QProgressDialog(self)
            # yes, I did alphabetise the setX calls on purpose..
            # code OCD is wonderful x_x
            progress.setCancelButton(None)
            progress.setMinimumDuration(0)
            progress.setRange(0,7)
            progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            progress.setWindowTitle('Reggie!')

        # this tracks progress
        # current stages:
        # - 0: Loading level data
        # [LevelUnit.__init__ is entered here]
        # - 1: Loading tilesets [1/2/3/4 allocated for each tileset]
        # - 5: Loading layers
        # [Control is returned to LoadLevel]
        # - 6: Loading objects
        # - 7: Preparing editor

        # stop it from snapping when created...
        global OverrideSnapping
        OverrideSnapping = True

        if progress is not None:
            progress.setLabelText('Loading level data...')
            progress.setValue(0)

        global Level
        Level = LevelUnit()

        if name is None:
            if autosave:
                Level.loadLevelFromAutosave(progress)
            else:
                Level.newLevel()
        else:
            Level.loadLevel(name, area, progress)

        OverrideSnapping = False

        # prepare the object picker
        if progress is not None:
            progress.setLabelText('Loading objects...')
            progress.setValue(6)

        self.objUseLayer1.setChecked(True)

        self.objPicker.LoadFromTilesets()

        self.creationTabs.setCurrentIndex(0)
        self.creationTabs.setTabEnabled(0, (Level.tileset0 != ''))
        self.creationTabs.setTabEnabled(1, (Level.tileset1 != ''))
        self.creationTabs.setTabEnabled(2, (Level.tileset2 != ''))
        self.creationTabs.setTabEnabled(3, (Level.tileset3 != ''))

        # add all the objects to the scene
        if progress is not None:
            progress.setLabelText('Preparing editor...')
            progress.setValue(7)

        scene = self.scene
        scene.clear()

        entlist = self.entranceList
        entlist.clear()

        pathlist = self.pathList
        pathlist.clear()

        addItem = scene.addItem

        pcEvent = self.HandleObjPosChange
        for layer in reversed(Level.layers):
            for obj in layer:
                obj.positionChanged = pcEvent
                addItem(obj)

        pcEvent = self.HandleSprPosChange
        for spr in Level.sprites:
            spr.positionChanged = pcEvent
            addItem(spr)

        pcEvent = self.HandleEntPosChange
        for ent in Level.entrances:
            addItem(ent)
            ent.positionChanged = pcEvent
            ent.listitem = QtWidgets.QListWidgetItem(ent.ListString())
            entlist.addItem(ent.listitem)

        for zone in Level.zones:
            addItem(zone)

        pcEvent = self.HandleLocPosChange
        scEvent = self.HandleLocSizeChange
        for location in Level.locations:
            addItem(location)
            location.positionChanged = pcEvent
            location.sizeChanged = scEvent

        for path in Level.paths:
            addItem(path)
            path.positionChanged = self.HandlePathPosChange
            path.listitem = QtWidgets.QListWidgetItem(path.ListString())
            pathlist.addItem(path.listitem)

        for path in Level.pathdata:
            peline = PathEditorLineItem(path['nodes'])
            path['peline'] = peline
            addItem(peline)


        # fill up the area list
        self.areaComboBox.clear()
        for i in range(1,Level.areacount+1):
            self.areaComboBox.addItem('Area '+str(i))

        self.areaComboBox.setCurrentIndex(area-1)
        self.levelOverview.update()

        # scroll to the initial entrance
        startEntID = Level.startEntrance
        startEnt = None
        for ent in Level.entrances:
            if ent.entid == startEntID: startEnt = ent

        self.view.centerOn(0,0)
        if startEnt is not None: self.view.centerOn(startEnt.objx*1.5, startEnt.objy*1.5)
        self.ZoomTo(100.0)

        # reset some editor things
        self.actions['showlayer0'].setChecked(True)
        self.actions['showlayer1'].setChecked(True)
        self.actions['showlayer2'].setChecked(True)
        self.actions['showsprites'].setChecked(True)
        self.actions['showspriteimages'].setChecked(True)
        self.actions['showentrances'].setChecked(True)
        self.actions['showlocations'].setChecked(True)
        self.actions['showpaths'].setChecked(True)
        self.actions['addarea'].setEnabled(Level.areacount < 4)
        self.actions['importarea'].setEnabled(Level.areacount < 4)
        self.actions['deletearea'].setEnabled(Level.areacount > 1)
        DirtyOverride -= 1
        self.UpdateTitle()

        self.scene.update()

        self.levelOverview.Reset()
        self.levelOverview.update()
        QtCore.QTimer.singleShot(20, self.levelOverview.update)

        return True


    @QtCoreSlot()
    def ReloadTilesets(self):
        tilesets = [Level.tileset0, Level.tileset1, Level.tileset2, Level.tileset3]
        for idx, name in zip(range(4), tilesets):
            if name is not None and name != '':
                LoadTileset(idx, name)

        self.objPicker.LoadFromTilesets()

        for layer in Level.layers:
            for obj in layer:
                obj.updateObjCache()

        self.scene.update()


        global Sprites
        Sprites = None
        LoadSpriteData()


    @QtCoreSlot()
    def ChangeSelectionHandler(self):
        """Update the visible panels whenever the selection changes"""
        if self.SelectionUpdateFlag: return

        try:
            selitems = self.scene.selectedItems()
        except RuntimeError:
            # must catch this error: if you close the app while something is selected,
            # you get a RuntimeError about the "underlying C++ object being deleted"
            return

        # do this to avoid flicker
        showSpritePanel = False
        showEntrancePanel = False
        showLocationPanel = False
        showPathPanel = False
        updateModeInfo = False

        # clear our variables
        self.selObj = None
        self.selObjs = None

        self.entranceList.setCurrentItem(None)
        self.pathList.setCurrentItem(None)
        # possibly a small optimisation
        func_ii = isinstance
        type_obj = LevelObjectEditorItem
        type_spr = SpriteEditorItem
        type_ent = EntranceEditorItem
        type_loc = LocationEditorItem
        type_path = PathEditorItem

        if len(selitems) == 0:
            # nothing is selected
            self.actions['cut'].setEnabled(False)
            self.actions['copy'].setEnabled(False)
            self.actions['shiftobjects'].setEnabled(False)
            self.actions['mergelocations'].setEnabled(False)

        elif len(selitems) == 1:
            # only one item, check the type
            self.actions['cut'].setEnabled(True)
            self.actions['copy'].setEnabled(True)
            self.actions['shiftobjects'].setEnabled(True)
            self.actions['mergelocations'].setEnabled(True)

            item = selitems[0]
            self.selObj = item
            if func_ii(item, type_spr):
                showSpritePanel = True
                updateModeInfo = True
            elif func_ii(item, type_ent):
                self.creationTabs.setCurrentIndex(5)
                self.UpdateFlag = True
                self.entranceList.setCurrentItem(item.listitem)
                self.UpdateFlag = False
                showEntrancePanel = True
                updateModeInfo = True
            elif func_ii(item, type_path):
                self.creationTabs.setCurrentIndex(6)
                self.UpdateFlag = True
                self.pathList.setCurrentItem(item.listitem)
                self.UpdateFlag = False
                showPathPanel = True
                updateModeInfo = True
            elif func_ii(item, type_loc):
                showLocationPanel = True
                updateModeInfo = True

        else:
            updateModeInfo = True

            # more than one item
            self.actions['cut'].setEnabled(True)
            self.actions['copy'].setEnabled(True)
            self.actions['shiftobjects'].setEnabled(True)
            self.actions['mergelocations'].setEnabled(True)

        #for x in self.CurrentSelection:
        #    s = x.scene()
        #    if s is not None:
        #        s.update(x.x(), x.y(), x.BoundingRect.width(), x.BoundingRect.height())
        #
        #for x in selitems:
        #    x.scene().update(x.x(), x.y(), x.BoundingRect.width(), x.BoundingRect.height())

        self.CurrentSelection = selitems

        self.spriteEditorDock.setActive(showSpritePanel)
        self.entranceEditorDock.setActive(showEntrancePanel)

        self.locationEditorDock.setActive(showLocationPanel)
        self.pathEditorDock.setActive(showPathPanel)

        if updateModeInfo: self.UpdateModeInfo()


    def HandleObjPosChange(self, obj, oldx, oldy, x, y):
        """Handle the object being dragged"""
        if obj == self.selObj:
            if oldx == x and oldy == y: return
            SetDirty()
        self.levelOverview.update()


    @QtCoreSlot(int)
    def CreationTabChanged(self, nt):
        """Handles the selected tab in the creation panel changing"""
        if hasattr(self, 'objPicker'):
            if nt >= 0 and nt <= 3:
                self.objPicker.ShowTileset(nt)
                self.creationTabs.widget(nt).setLayout(self.createObjectLayout)
            self.defaultPropDock.setVisible(False)
        global CurrentPaintType
        CurrentPaintType = nt


    @QtCoreSlot(int)
    def LayerChoiceChanged(self, nl):
        """Handles the selected layer changing"""
        global CurrentLayer
        CurrentLayer = nl

        # should we replace?
        if QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.AltModifier:
            items = self.scene.selectedItems()
            type_obj = LevelObjectEditorItem
            tileset = CurrentPaintType
            level = Level
            change = []

            if nl == 0:
                newLayer = level.layers[0]
            elif nl == 1:
                newLayer = level.layers[1]
            else:
                newLayer = level.layers[2]

            for x in items:
                if isinstance(x, type_obj) and x.layer != nl:
                    change.append(x)

            if len(change) > 0:
                change.sort(key=lambda x: x.zValue())

                if len(newLayer) == 0:
                    z = (2 - nl) * 8192
                else:
                    z = newLayer[-1].zValue() + 1

                if nl == 0:
                    newVisibility = ShowLayer0
                elif nl == 1:
                    newVisibility = ShowLayer1
                else:
                    newVisibility = ShowLayer2

                for item in change:
                    level.RemoveFromLayer(item)
                    item.layer = nl
                    newLayer.append(item)
                    item.setZValue(z)
                    item.setVisible(newVisibility)
                    item.update()
                    z += 1

            self.scene.update()
            SetDirty()


    @QtCoreSlot(int)
    def ObjectChoiceChanged(self, type):
        """Handles a new object being chosen"""
        global CurrentObject
        CurrentObject = type


    @QtCoreSlot(int)
    def ObjectReplace(self, type):
        """Handles a new object being chosen to replace the selected objects"""
        items = self.scene.selectedItems()
        type_obj = LevelObjectEditorItem
        tileset = CurrentPaintType
        changed = False

        for x in items:
            if isinstance(x, type_obj) and (x.tileset != tileset or x.type != type):
                x.SetType(tileset, type)
                x.update()
                changed = True

        if changed:
            SetDirty()


    @QtCoreSlot(int)
    def SpriteChoiceChanged(self, type):
        """Handles a new sprite being chosen"""
        global CurrentSprite
        CurrentSprite = type
        if type != 1000 and type >= 0:
            self.defaultDataEditor.setSprite(type)
            self.defaultDataEditor.data = b'\0\0\0\0\0\0\0\0\0\0'
            self.defaultDataEditor.update()
            self.defaultPropButton.setEnabled(True)
        else:
            self.defaultPropButton.setEnabled(False)
            self.defaultPropDock.setVisible(False)
            self.defaultDataEditor.update()


    @QtCoreSlot(int)
    def SpriteReplace(self, type):
        """Handles a new sprite type being chosen to replace the selected sprites"""
        items = self.scene.selectedItems()
        type_spr = SpriteEditorItem
        changed = False

        for x in items:
            if isinstance(x, type_spr):
                x.SetType(type)
                x.spritedata = self.defaultDataEditor.data
                x.update()
                changed = True

        if changed:
            SetDirty()

        self.ChangeSelectionHandler()


    @QtCoreSlot(int)
    def SelectNewSpriteView(self, type):
        """Handles a new sprite view being chosen"""
        cat = SpriteCategories[type]
        self.sprPicker.SwitchView(cat)

        isSearch = (type == len(SpriteCategories) - 1)
        layout = self.spriteSearchLayout
        layout.itemAt(0).widget().setVisible(isSearch)
        layout.itemAt(1).widget().setVisible(isSearch)

        settings.setValue('SpriteView', type)


    @QtCoreSlot(str)
    def NewSearchTerm(self, text):
        """Handles a new sprite search term being entered"""
        self.sprPicker.SetSearchString(text)


    @QtCoreSlot()
    def ShowDefaultProps(self):
        """Handles the Show Default Properties button being clicked"""
        self.defaultPropDock.setVisible(True)


    def HandleSprPosChange(self, obj, oldx, oldy, x, y):
        """Handle the sprite being dragged"""
        if obj == self.selObj:
            if oldx == x and oldy == y: return
            SetDirty()


    @QtCoreSlot(PyObject)
    def SpriteDataUpdated(self, data):
        """Handle the current sprite's data being updated"""
        if self.spriteEditorDock.isActive():
            obj = self.selObj
            obj.spritedata = data
            SetDirty()

            if obj.dynamicSize:
                obj.UpdateDynamicSizing()


    def HandleEntPosChange(self, obj, oldx, oldy, x, y):
        """Handle the entrance being dragged"""
        if oldx == x and oldy == y: return
        obj.listitem.setText(obj.ListString())
        if obj == self.selObj:
            SetDirty()

    def HandlePathPosChange(self, obj, oldx, oldy, x, y):
        """Handle the path being dragged"""
        if oldx == x and oldy == y: return
        obj.listitem.setText(obj.ListString())
        obj.updatePos()
        obj.pathinfo['peline'].nodePosChanged()
        if obj == self.selObj:
            SetDirty()



    @QtCoreSlot(QtWidgets.QListWidgetItem)
    def HandleEntranceSelectByList(self, item):
        """Handle an entrance being selected from the list"""
        if self.UpdateFlag: return

        # can't really think of any other way to do this
        #item = self.entranceList.item(row)
        ent = None
        for check in Level.entrances:
            if check.listitem == item:
                ent = check
                break
        if ent is None: return

        ent.ensureVisible(QtCore.QRectF(), 192, 192)
        self.scene.clearSelection()
        ent.setSelected(True)

    @QtCoreSlot(QtWidgets.QListWidgetItem)
    def HandlePathSelectByList(self, item):
        """Handle a path node being selected"""
        #if self.UpdateFlag: return

        #can't really think of any other way to do this
        #item = self.pathlist.item(row)
        path = None
        for check in Level.paths:
           if check.listitem == item:
                path = check
                break
        if path is None: return

        path.ensureVisible(QtCore.QRectF(), 192, 192)
        self.scene.clearSelection()
        path.setSelected(True)

    def HandleLocPosChange(self, loc, oldx, oldy, x, y):
        """Handle the location being dragged"""
        if loc == self.selObj:
            if oldx == x and oldy == y: return
            self.locationEditor.setLocation(loc)
            SetDirty()
        self.levelOverview.update()


    def HandleLocSizeChange(self, loc, width, height):
        """Handle the location being resized"""
        if loc == self.selObj:
            self.locationEditor.setLocation(loc)
            SetDirty()
        self.levelOverview.update()


    def UpdateModeInfo(self):
        """Change the info in the currently visible panel"""
        self.UpdateFlag = True

        if self.spriteEditorDock.isActive():
            obj = self.selObj
            self.spriteDataEditor.setSprite(obj.type)
            self.spriteDataEditor.data = obj.spritedata
            self.spriteDataEditor.update()
        elif self.entranceEditorDock.isActive():
            self.entranceEditor.setEntrance(self.selObj)
        elif self.pathEditorDock.isActive():
            self.pathEditor.setPath(self.selObj)
        elif self.locationEditorDock.isActive():
            self.locationEditor.setLocation(self.selObj)

        self.UpdateFlag = False


    @QtCoreSlot(int, int)
    def PositionHovered(self, x, y):
        """Handle a position being hovered in the view"""
        info = ''
        hovereditems = self.scene.items(QtCore.QPointF(x,y))
        hovered = None
        type_zone = ZoneItem
        type_loc = LocationEditorItem
        type_aux = sprites.AuxiliaryItem
        type_aux_img = sprites.AuxiliaryImage
        type_peline = PathEditorLineItem
        for item in hovereditems:
            if not isinstance(item, type_zone) and not isinstance(item, type_loc) and not (isinstance(item, type_aux) and not isinstance(item, type_aux_img)) and not isinstance(item, type_peline):
                hovered = item
                break

        if hovered is not None:
            if isinstance(hovered, LevelObjectEditorItem):
                info = ' - Object under mouse: size %dx%d at %d,%d on layer %d; type %d from tileset %d' % (hovered.width, hovered.height, hovered.objx, hovered.objy, hovered.layer, hovered.type, hovered.tileset+1)
            elif isinstance(hovered, SpriteEditorItem):
                info = ' - Sprite under mouse: %s at %d,%d' % (hovered.name, hovered.objx, hovered.objy)
            elif isinstance(hovered, EntranceEditorItem):
                info = ' - Entrance under mouse: %s at %d,%d %s' % (hovered.name, hovered.objx, hovered.objy, hovered.destination)
            elif isinstance(hovered, EntranceEditorItem):
                info = ' - Location under mouse: %s at %d,%d - width %d / height %d,  %s' % (hovered.name, hovered.objx, hovered.objy, hovered.width, hovered.height, hovered.destination)

        self.posLabel.setText('(%d,%d) - (%d,%d)%s' % (int(x/24),int(y/24),int(x/1.5),int(y/1.5),info))


    def keyPressEvent(self, event):
        """Handles key press events for the main window if needed"""
        if event.key() == QtCore.Qt.Key.Key_Delete or event.key() == QtCore.Qt.Key.Key_Backspace:
            sel = self.scene.selectedItems()
            if len(sel) > 0:
                self.SelectionUpdateFlag = True
                for obj in sel:
                    obj.delete()
                    obj.setSelected(False)
                    self.scene.removeItem(obj)
                    self.levelOverview.update()
                SetDirty()
                event.accept()
                self.SelectionUpdateFlag = False
                self.ChangeSelectionHandler()
                return
        self.levelOverview.update()

        QtWidgets.QMainWindow.keyPressEvent(self, event)


    @QtCoreSlot()
    def HandleAreaOptions(self):
        """Pops up the options for Area Dialogue"""
        dlg = AreaOptionsDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            SetDirty()
            Level.timeLimit = dlg.LoadingTab.timer.value() - 200
            Level.startEntrance = dlg.LoadingTab.entrance.value()

            if dlg.LoadingTab.wrap.isChecked():
                Level.wrapFlag |= 1
            else:
                Level.wrapFlag &= ~1

            tileset0tmp = Level.tileset0
            tileset1tmp = Level.tileset1
            tileset2tmp = Level.tileset2
            tileset3tmp = Level.tileset3

            oldnames = [Level.tileset0, Level.tileset1, Level.tileset2, Level.tileset3]
            assignments = ['Level.tileset0', 'Level.tileset1', 'Level.tileset2', 'Level.tileset3']
            widgets = [dlg.TilesetsTab.tile0, dlg.TilesetsTab.tile1, dlg.TilesetsTab.tile2, dlg.TilesetsTab.tile3]

            toUnload = []

            for idx, oldname, assignment, widget in zip(range(4), oldnames, assignments, widgets):
                ts_idx = widget.currentIndex()
                fname = str(qm(widget.itemData(ts_idx)))

                if fname == '':
                    toUnload.append(idx)
                    continue
                elif fname.startswith('[CUSTOM]'):
                    fname = fname[8:]
                    if fname == '': continue

                exec (assignment + ' = fname')
                LoadTileset(idx, fname)

            for idx in toUnload:
                exec ('Level.tileset%d = ""' % idx)
                UnloadTileset(idx)

            defEvents = 0
            eventChooser = dlg.LoadingTab.eventChooser
            checked = QtCore.Qt.CheckState.Checked
            for i in range(64):
                if eventChooser.item(i).checkState() == checked:
                    defEvents |= (1 << i)

            Level.defEvents = defEvents

            mainWindow.objPicker.LoadFromTilesets()
            self.creationTabs.setCurrentIndex(0)
            self.creationTabs.setTabEnabled(0, (Level.tileset0 != ''))
            self.creationTabs.setTabEnabled(1, (Level.tileset1 != ''))
            self.creationTabs.setTabEnabled(2, (Level.tileset2 != ''))
            self.creationTabs.setTabEnabled(3, (Level.tileset3 != ''))

            for layer in Level.layers:
                for obj in layer:
                    obj.updateObjCache()

            self.scene.update()

    @QtCoreSlot()
    def HandleZones(self):
        """Pops up the options for Zone dialogue"""
        dlg = ZonesDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            SetDirty()
            i = 0

            # resync the zones
            items = self.scene.items()
            func_ii = isinstance
            type_zone = ZoneItem

            for item in items:
                if func_ii(item, type_zone):
                    self.scene.removeItem(item)

            Level.zones = []

            for tab in dlg.zoneTabs:
                z = tab.zoneObj
                z.id = i
                z.UpdateTitle()
                Level.zones.append(z)
                self.scene.addItem(z)

                if tab.Zone_xpos.value() < 16:
                    z.objx = 16
                elif tab.Zone_xpos.value() > 24560:
                    z.objx = 24560
                else:
                    z.objx = tab.Zone_xpos.value()

                if tab.Zone_ypos.value() < 16:
                    z.objy = 16
                elif tab.Zone_ypos.value() > 12272:
                    z.objy = 12272
                else:
                    z.objy = tab.Zone_ypos.value()

                if (tab.Zone_width.value() + tab.Zone_xpos.value()) > 24560:
                    z.width = 24560 - tab.Zone_xpos.value()
                else:
                    z.width = tab.Zone_width.value()

                if (tab.Zone_height.value() + tab.Zone_ypos.value()) > 12272:
                    z.height = 12272 - tab.Zone_ypos.value()
                else:
                    z.height = tab.Zone_height.value()

                z.prepareGeometryChange()
                z.UpdateRects()
                z.setPos(z.objx*1.5, z.objy*1.5)


                z.cammode = tab.Zone_cammodezoom.modeButtonGroup.checkedId()
                z.camzoom = tab.Zone_cammodezoom.screenSizes.currentIndex()

                z.direction = tab.Zone_direction.currentIndex()

                if tab.Zone_yrestrict.isChecked():
                    z.mpcamzoomadjust = tab.Zone_mpzoomadjust.value()
                else:
                    z.mpcamzoomadjust = 15


                z.modeldark = tab.Zone_modeldark.currentIndex()
                z.terraindark = tab.Zone_terraindark.currentIndex()

                z.visibility = tab.Zone_visibility.currentIndex()
                if tab.Zone_vspotlight.isChecked():
                    z.visibility += 16
                if tab.Zone_vfulldark.isChecked():
                    z.visibility += 32


                z.yupperbound = tab.Zone_yboundup.value()
                z.ylowerbound = tab.Zone_ybounddown.value()
                z.yupperbound2 = tab.Zone_yboundup2.value()
                z.ylowerbound2 = tab.Zone_ybounddown2.value()
                z.yupperbound3 = tab.Zone_yboundup3.value()
                z.ylowerbound3 = tab.Zone_ybounddown3.value()

                z.music = tab.Zone_music_id.value()
                z.sfxmod = (tab.Zone_sfx.currentIndex() * 16)
                if tab.Zone_boss.isChecked():
                    z.sfxmod = z.sfxmod + 1

                i = i + 1
        self.levelOverview.update()

    #Handles setting the backgrounds
    @QtCoreSlot()
    def HandleBG(self):
        """Pops up the Background settings Dialog"""
        dlg = BGDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            SetDirty()
            i = 0
            for z in Level.zones:
                tab = dlg.BGTabs[i]

                z.XpositionA = tab.xposA.value()
                z.YpositionA = -tab.yposA.value()
                z.XscrollA = tab.xscrollA.currentIndex()
                z.YscrollA = tab.yscrollA.currentIndex()

                z.ZoomA = tab.zoomA.currentIndex()

                id = qm(tab.background_nameA.itemData(tab.background_nameA.currentIndex()))
                if tab.toscreenA.isChecked():
                    # mode 5
                    z.bg1A = id
                    z.bg2A = id
                    z.bg3A = id
                else:
                    # mode 2
                    z.bg1A = id
                    z.bg2A = 0x000A
                    z.bg3A = 0x000A


                z.XpositionB = tab.xposB.value()
                z.YpositionB = -tab.yposB.value()
                z.XscrollB = tab.xscrollB.currentIndex()
                z.YscrollB = tab.yscrollB.currentIndex()

                z.ZoomB = tab.zoomB.currentIndex()

                id = qm(tab.background_nameB.itemData(tab.background_nameB.currentIndex()))
                if tab.toscreenB.isChecked():
                    # mode 5
                    z.bg1B = id
                    z.bg2B = id
                    z.bg3B = id
                else:
                    # mode 2
                    z.bg1B = id
                    z.bg2B = 0x000A
                    z.bg3B = 0x000A

                i = i + 1

    @QtCoreSlot()
    def HandleCameraProfiles(self):
        """Pops up the options for camera profiles"""
        dlg = CameraProfilesDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            SetDirty()

            camprofiles = []
            for row in range(dlg.list.count()):
                item = dlg.list.item(row)
                camprofiles.append(qm(item.data(QtCore.Qt.ItemDataRole.UserRole)))

            Level.camprofiles = camprofiles

    @QtCoreSlot()
    def HandleScreenshot(self):
        """Takes a screenshot of the entire level and saves it"""

        dlg = ScreenCapChoiceDialog()
        if execQtObject(dlg) == QtWidgets.QDialog.DialogCode.Accepted:
            fn = qm(QtWidgets.QFileDialog.getSaveFileName)(mainWindow, 'Choose a new filename', '/untitled.png', 'Portable Network Graphics (*.png)')[0]
            if fn == '': return
            fn = unicode(fn)

            if dlg.zoneCombo.currentIndex() == 0:
                ScreenshotImage = QtGui.QImage(mainWindow.view.width(), mainWindow.view.height(), QtGui.QImage.Format.Format_ARGB32)
                ScreenshotImage.fill(QtCore.Qt.GlobalColor.transparent)

                RenderPainter = QtGui.QPainter(ScreenshotImage)
                mainWindow.view.render(RenderPainter, QtCore.QRectF(0,0,mainWindow.view.width(),  mainWindow.view.height()), QtCore.QRect(QtCore.QPoint(0,0), QtCore.QSize(mainWindow.view.width(),  mainWindow.view.height())))
                RenderPainter.end()
            elif dlg.zoneCombo.currentIndex() == 1:
                maxX = maxY = 0
                minX = minY = 0x0ddba11
                for z in Level.zones:
                    if maxX < ((z.objx*1.5) + (z.width*1.5)):
                        maxX = ((z.objx*1.5) + (z.width*1.5))
                    if maxY < ((z.objy*1.5) + (z.height*1.5)):
                        maxY = ((z.objy*1.5) + (z.height*1.5))
                    if minX > z.objx*1.5:
                        minX = z.objx*1.5
                    if minY > z.objy*1.5:
                        minY = z.objy*1.5
                maxX = (1024*24 if 1024*24 < maxX+40 else maxX+40)
                maxY = (512*24 if 512*24 < maxY+40 else maxY+40)
                minX = (0 if 40 > minX else minX-40)
                minY = (40 if 40 > minY else minY-40)

                ScreenshotImage = QtGui.QImage(int(maxX - minX), int(maxY - minY), QtGui.QImage.Format.Format_ARGB32)
                ScreenshotImage.fill(QtCore.Qt.GlobalColor.transparent)

                RenderPainter = QtGui.QPainter(ScreenshotImage)
                mainWindow.scene.render(RenderPainter, QtCore.QRectF(0,0,int(maxX - minX) ,int(maxY - minY)), QtCore.QRectF(int(minX), int(minY), int(maxX - minX), int(maxY - minY)))
                RenderPainter.end()


            else:
                i = dlg.zoneCombo.currentIndex() - 2
                ScreenshotImage = QtGui.QImage(int(Level.zones[i].width*1.5), int(Level.zones[i].height*1.5), QtGui.QImage.Format.Format_ARGB32)
                ScreenshotImage.fill(QtCore.Qt.GlobalColor.transparent)

                RenderPainter = QtGui.QPainter(ScreenshotImage)
                mainWindow.scene.render(RenderPainter, QtCore.QRectF(0,0,Level.zones[i].width*1.5, Level.zones[i].height*1.5), QtCore.QRectF(int(Level.zones[i].objx)*1.5, int(Level.zones[i].objy)*1.5, Level.zones[i].width*1.5, Level.zones[i].height*1.5))
                RenderPainter.end()

            ScreenshotImage.save(fn, 'PNG', 50)



def main():
    """Main startup function for Reggie"""

    global app, mainWindow, settings

    # create an application

    # The default high-dpi scaling looks really bad, unfortunately.
    if QtCompatVersion >= (5,14,0):
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.RoundPreferFloor)

    sys.argv[0] = ApplicationDisplayName  # only way to set the app display name on Qt 4
    app = QtWidgets.QApplication(sys.argv)

    # go to the script path
    path = module_path()
    if path is not None:
        os.chdir(path)

    # check if required files are missing
    if FilesAreMissing():
        sys.exit(1)

    # load required stuff
    LoadLevelNames()
    LoadTilesetNames()
    LoadObjDescriptions()
    LoadBgANames()
    LoadBgBNames()
    LoadSpriteData()
    LoadEntranceNames()
    LoadMusicNames()
    LoadNumberFont()
    LoadNumberFontBold()
    LoadOverrides()
    sprites.Setup()

    # load the settings
    if os.path.isfile('portable.txt'):
        settings = QtCore.QSettings('settings_Reggie_%s.ini' % QtName, QtCore.QSettings.IniFormat)
    else:
        settings = QtCore.QSettings('Reggie', 'Reggie Level Editor (%s)' % QtName)

    if '-clear-settings' in sys.argv:
        settings.clear()

    global EnableAlpha, GridEnabled, TilesetSlotsModEnabled, DarkMode
    global ObjectsNonFrozen, SpritesNonFrozen, EntrancesNonFrozen, LocationsNonFrozen, PathsNonFrozen

    # note: the str().lower() is for macOS, where bools in settings aren't automatically stringified
    TilesetSlotsModEnabled = (str(qm(settings.value('TilesetSlotsModEnabled', 'false'))).lower() == 'true')
    GridEnabled = (str(qm(settings.value('GridEnabled', 'false'))).lower() == 'true')
    DarkMode = (str(qm(settings.value('DarkMode', 'false'))).lower() == 'true')
    ObjectsNonFrozen = (str(qm(settings.value('FreezeObjects', 'false'))).lower() == 'false')
    SpritesNonFrozen = (str(qm(settings.value('FreezeSprites', 'false'))).lower() == 'false')
    EntrancesNonFrozen = (str(qm(settings.value('FreezeEntrances', 'false'))).lower() == 'false')
    PathsNonFrozen = (str(qm(settings.value('FreezePaths', 'false'))).lower() == 'false')
    LocationsNonFrozen = (str(qm(settings.value('FreezeLocations', 'false'))).lower() == 'false')

    if DarkMode:
        setUpDarkMode()

    for arg in sys.argv:
        if arg.startswith('-gamepath='):
            settings.setValue('GamePath', arg[10:])
            break

    if settings.contains('GamePath'):
        SetGamePath(qm(settings.value('GamePath')))

    # choose a folder for the game
    # let the user pick a folder without restarting the editor if they fail
    if not gamePath:
        path = PromptUserForNewGamePath()

        if not path:
            QtWidgets.QMessageBox.critical(None, 'Error',  "In order to use Reggie!, you need the Stage folder from <i>New Super Mario Bros. Wii</i>, including the Texture folder and the level files contained within it. You can dump it from your disc using a tool such as <a href='https://www.wiibrew.org/wiki/Reggie!_Dumper'>Reggie! Dumper</a> or <a href='https://www.wiibrew.org/wiki/CleanRip'>CleanRip</a>.")
            sys.exit(0)

        settings.setValue('GamePath', path)
        SetGamePath(path)

    # check to see if we have anything saved
    autofile = unicode(qm(settings.value('AutoSaveFilePath', 'none')))
    if autofile != 'none':
        try:
            autofiledata = qm(settings.value('AutoSaveFileData', b'x')).data()
        except Exception:
            autofiledata = b'x'
        if autofiledata != b'x':
            result = execQtObject(AutoSavedInfoDialog(autofile))
            if result == QtWidgets.QDialog.DialogCode.Accepted:
                global RestoredFromAutoSave, AutoSavePath, AutoSaveData
                RestoredFromAutoSave = True
                AutoSavePath = autofile
                AutoSaveData = autofiledata
            else:
                settings.setValue('AutoSaveFilePath', 'none')
                settings.setValue('AutoSaveFileData', b'x')

    # create and show the main window
    mainWindow = ReggieWindow()
    mainWindow.show()
    exitcodesys = execQtObject(app)
    app.deleteLater()
    sys.exit(exitcodesys)


EnableAlpha = True
if '-alpha' in sys.argv:
    EnableAlpha = False

DarkMode = False

# check version
if HaveNSMBLib:
    version = nsmblib.getVersion()
    if version < 4:
        HaveNSMBLib = False

if '-nolib' in sys.argv:
    HaveNSMBLib = False

if __name__ == '__main__':
    if HavePsyco:
        psyco.full()
    int(main())

