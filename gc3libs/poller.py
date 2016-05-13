#! /usr/bin/env python
#
"""This module contains various `pollers`. A Poller is an object that
keeps track of a generic URL and returns `events` whenever a new
object is created inside that URL.

"""
# Copyright (C) 2012-2013, GC3, University of Zurich. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
__docformat__ = 'reStructuredText'
__version__ = '$Revision$'

from .url import Url
import os
import logging
log = logging.getLogger('gc3.gc3libs')

# Conditional imports. Some pollers depend on the presence of specific
# Python modules.
try:
    import inotifyx
except:
    log.warning(
        "Module inotifyx not found. INotifyPoller class will not be available")
    inotifyx = None

try:
    import swiftclient
except ImportError:
    log.warning(
        "Module swiftclient not found. SwiftPoller will bot be available.")
    swiftclient = None


# These are directly took from inotifyx
events = {'IN_ACCESS': 1,
 'IN_ALL_EVENTS': 4095,
 'IN_ATTRIB': 4,
 'IN_CLOSE': 24,
 'IN_CLOSE_NOWRITE': 16,
 'IN_CLOSE_WRITE': 8,
 'IN_CREATE': 256,
 'IN_DELETE': 512,
 'IN_DELETE_SELF': 1024,
 'IN_DONT_FOLLOW': 33554432,
 'IN_IGNORED': 32768,
 'IN_ISDIR': 1073741824,
 'IN_MASK_ADD': 536870912,
 'IN_MODIFY': 2,
 'IN_MOVE': 192,
 'IN_MOVED_FROM': 64,
 'IN_MOVED_TO': 128,
 'IN_MOVE_SELF': 2048,
 'IN_ONESHOT': 2147483648,
 'IN_ONLYDIR': 16777216,
 'IN_OPEN': 32,
 'IN_Q_OVERFLOW': 16384,
 'IN_UNMOUNT': 8192}

def get_mask_description(mask):
    """
    Return an ASCII string describing the mask field in terms of
    bitwise-or'd IN_* constants, or 0.  The result is valid Python code
    that could be eval'd to get the value of the mask field.  In other
    words, for a given event:
    """
    parts = []
    for name, value in events.items():
        if mask&value:
            parts.append(name)
    if parts:
        return str.join("|", parts)
    else:
        return '0'


available_pollers = {}


class Poller(object):
    """Base class for all Pollers.
    """
    def __init__(self, url, mask, **kw):
        self.url = Url(url)
        self.mask = mask

    def get_events(self):
        """Returns a list of tuple (url, mask)"""
        raise NotImplementedError(
            "Abstract method `Poller.get_events()` called "
            " - this should have been defined in a derived class.")


def register_poller(scheme, cls):
    # We might want to add some check here...
    available_pollers[scheme] = cls


class INotifyPoller(Poller):
    """Poller implementation that uses inotifyx to track new events on the
    filesystem"""
    
    def __init__(self, url, mask, recurse=False, **kw):
        Poller.__init__(self, url, mask, **kw)

        self._ifds = {}
        # Ensure inbox directory exists
        if not os.path.exists(self.url.path):
            log.warning("Inbox directory `%s` does not exist,"
                        " creating it.", self.url.path)
            os.makedirs(self.url.path)

        ifd = inotifyx.init()
        inotifyx.add_watch(ifd, self.url.path, mask)
        log.debug("Adding watch for path %s" % self.url.path)
        self._ifds[self.url.path] = ifd

        if recurse:
            for dirpath, dirnames, filename in os.walk(self.url.path):
                for dirname in dirnames:
                    ifd = inotifyx.init()
                    abspath = os.path.join(self.url.path,
                                           dirpath,
                                           dirname)
                    log.debug("Adding watch for path %s" % abspath)
                    inotifyx.add_watch(abspath, mask)
                    self._ifds[abspath] = ifd

    def get_events(self):
        newevents = []
        for path, ifd in self._ifds.items():
            ievents = inotifyx.get_events(ifd, 0)
            for event in ievents:
                # if `name` is empty, it's the same directory
                url = Url(os.path.join(path, event.name)) if event.name else self.url
                newevents.append(
                    (url, event.mask))
        return newevents

if inotifyx:
    register_poller('file', INotifyPoller)


class FilePoller(Poller):
    """Poller implementation that uses regular `os` module to track for
    new events on a filesystem."""
    def __init__(self, url, mask, **kw):
        Poller.__init__(self, url, mask, **kw)
        self._path = self.url.path
        self._known_files = {}
        for path in os.listdir(self._path):
            abspath = os.path.join(self._path, path)
            stat = os.stat(abspath)
            self._known_files[path] = stat
                
    def get_events(self):
        dircontents = [os.path.join(self._path, path) for path in os.listdir(self._path)]
        # Check if new files have been created or old ones updated
        newevents = []
        for path in dircontents:
            stat = os.stat(path)

            # We can only check if:
            # 
            if path not in self._known_files:
                # We can only get 
                event = events['IN_CLOSE_WRITE']|events['IN_CREATE']
                if os.path.isdir(path):
                    event|=events['IN_ISDIR']
                self._known_files[path] = stat
                newevents.append((Url(path), event))
            elif stat.st_mtime > self._known_files[path].st_mtime:
                # File was updated?
                raise

        # check if some file was deleted
        for path in self._known_files:
            if path not in dircontents:
                newevents.append((Url(path), events['IN_DELETE']))
        return newevents

if not inotifyx:
    register_poller('file', FilePoller)


class SwiftPoller(Poller):
    pass

if swiftclient:
    register_poller('swift', SwiftPoller)


def get_poller(url, mask=events['IN_ALL_EVENTS'], **kw):
    url = Url(url)
    try:
        pollercls = available_pollers[url.scheme]
    except KeyError:
        if url.scheme not in available_pollers:
            raise ValueError(
                "No poller found for scheme `%s`", url.scheme)
    return pollercls(url, mask, **kw)


## main: run tests

if "__main__" == __name__:
    import doctest
    doctest.testmod(name="poller",
                    optionflags=doctest.NORMALIZE_WHITESPACE)
