#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2008 Zuza Software Foundation
#
# This file is part of translate.
#
# translate is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# translate is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with translate; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Module to provide a translation memory database."""

from translate.search.lshtein import LevenshteinComparer

try:
    from pysqlite2 import dbapi2
except ImportError:
    from sqlite3 import dbapi2
import math
import time


class LanguageError(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return str(self.value)


class TMDB(object):
    _tm_dbs = {}
    def __init__(self, db_file, max_candidates=3, min_similarity=75, max_length=1000):
        
        self.max_candidates = max_candidates
        self.min_similarity = min_similarity
        self.max_length = max_length
        
        # share connections to same database file between different instances
        if not self._tm_dbs.has_key(db_file):
            self._tm_dbs[db_file] = dbapi2.connect(db_file)

        self.connection = self._tm_dbs[db_file]
        self.cursor = self.connection.cursor()

        #FIXME: do we want to do any checks before we initialize the DB?
        self.init_database()
        
        self.comparer = LevenshteinComparer(self.max_length)
    
    def init_database(self):
        """creates database tables and indices"""

        script = """
CREATE TABLE IF NOT EXISTS sources (
       sid INTEGER PRIMARY KEY AUTOINCREMENT,
       text VARCHAR NOT NULL,
       context VARCHAR DEFAULT NULL,
       lang VARCHAR NOT NULL,
       length INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sources_text_idx ON sources (text);
CREATE INDEX IF NOT EXISTS sources_context_idx ON sources (context);
CREATE INDEX IF NOT EXISTS sources_lang_idx ON sources (lang);
CREATE INDEX IF NOT EXISTS sources_length_idx ON sources (length);
CREATE UNIQUE INDEX IF NOT EXISTS sources_uniq_idx ON sources (text, context, lang);

CREATE TABLE IF NOT EXISTS targets (
       tid INTEGER PRIMARY KEY AUTOINCREMENT,
       sid INTEGER NOT NULL,
       text VARCHAR NOT NULL,
       lang VARCHAR NOT NULL,
       length INTEGER NOT NULL,
       time INTEGER DEFAULT NULL,
       FOREIGN KEY (sid) references sources(sid)
);
CREATE INDEX IF NOT EXISTS targets_sid_idx ON targets (sid);
CREATE INDEX IF NOT EXISTS targets_lang_idx ON targets (lang);
CREATE INDEX IF NOT EXISTS targets_time_idx ON targets (time);
CREATE UNIQUE INDEX IF NOT EXISTS targets_uniq_idx ON targets (sid, text, lang);
"""

        try:
            self.cursor.executescript(script)
            self.connection.commit()
        except:
            self.connection.rollback()
            raise
        
    def add_unit(self, unit, source_lang=None, target_lang=None, commit=True):
        """inserts unit in the database"""
        #TODO: is that really the best way to handle unspecified
        # source and target languages? what about conflicts between
        # unit attributes and passed arguments
        if unit.getsourcelanguage():
            source_lang = unit.getsourcelanguage()
        if unit.gettargetlanguage():
            target_lang = unit.gettargetlanguage()

        if not source_lang:
            raise LanguageError("undefined source language")
        if not target_lang:
            raise LanguageError("undefined target language")
        
        try:
            try:
                self.cursor.execute("INSERT INTO sources (text, context, lang, length) VALUES(?, ?, ?, ?)",
                                    (unit.source,
                                     unit.getcontext(),
                                     source_lang,
                                     len(unit.source)))
                sid = self.cursor.lastrowid
            except dbapi2.IntegrityError:
                # source string already exists in db, run query to find sid
                self.cursor.execute("SELECT sid FROM sources WHERE text=? AND context=? and lang=?",
                                    (unit.source,
                                     unit.getcontext(),
                                     source_lang))
                sid = self.cursor.fetchone()
                (sid,) = sid
            try:
                self.cursor.execute("INSERT INTO targets (sid, text, lang, length, time) VALUES (?, ?, ?, ?, ?)",
                                    (sid,
                                     unit.target,
                                     target_lang,
                                     len(unit.target),
                                     int(time.time())))
            except dbapi2.IntegrityError:
                # target string already exists in db, do nothing
                pass

            if commit:
                self.connection.commit()
        except:
            if commit:
                self.connection.rollback()
            raise

    def add_store(self, store, source_lang, target_lang, commit=True):
        """insert all units in store in database"""
        for unit in store.units:
            if unit.istranslatable() and unit.istranslated():
                self.add_unit(unit, source_lang, target_lang, commit=False)
        if commit:
            self.connection.commit()

    def translate_unit(self, unit_source, source_langs, target_langs):
        """return TM suggestions for unit_source"""
        if isinstance(unit_source, unicode):
            unit_source = unit_source.encode("utf-8")
        if isinstance(source_langs, list):
            source_langs = ','.join(source_langs)
        if isinstance(target_langs, list):
            target_langs = ','.join(target_langs)
        minlen = min_levenshtein_length(len(unit_source), self.min_similarity)
        maxlen = max_levenshtein_length(len(unit_source), self.min_similarity, self.max_length)
        
        query = """SELECT s.text, t.text , s.context, s.lang, t.lang FROM sources s JOIN targets t ON s.sid = t.sid
                   WHERE s.lang IN (?) AND t.lang IN (?) 
                   AND s.length >= ? AND s.length <= ?"""
        self.cursor.execute(query, (source_langs, target_langs, minlen, maxlen))
        
        results = []
        for row in self.cursor:
            result = {}
            result['source'] = row[0].encode("utf-8")
            result['target'] = row[1].encode("utf-8")
            result['context'] = row[2].encode("utf-8")
            result['quality'] = self.comparer.similarity(unit_source, result['source'], self.min_similarity)
            if result['quality'] >= self.min_similarity:
                results.append(result)
        results.sort(key=lambda match: match['quality'], reverse=True)
        results = results[:self.max_candidates]
        return results
        
def min_levenshtein_length(length, min_similarity):
    return math.ceil(max(length * (min_similarity/100.0), 2))

def max_levenshtein_length(length, min_similarity, max_length):
    return math.floor(min(length / (min_similarity/100.0), max_length))

    
