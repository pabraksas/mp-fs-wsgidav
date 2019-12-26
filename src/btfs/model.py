# -*- coding: iso-8859-1 -*-
# (c) 2010 Martin Wendt; see CloudDAV http://clouddav.googlecode.com/
# Licensed under the MIT license: http://www.opensource.org/licenses/mit-license.php
#
# The original source for this module was taken from gaedav:
# (c) 2009 Haoyu Bai (http://gaedav.google.com/).

from __future__ import absolute_import

import datetime
import hashlib
import logging
import os.path
from builtins import object, range

from . import db

# from cache import cached_dir, cached_file, cached_content
from .cache import cached_resource
from .db import polymodel

DO_EXPENSIVE_CHECKS = False
# DO_EXPENSIVE_CHECKS = True


class UnmappedPath(object):
    """Dummy object to cache lookups for non-existent URLs."""

    pass


# TODO: may apply the technique described here:
# http://code.google.com/appengine/docs/python/datastore/keysandentitygroups.html

# ===============================================================================
# Path
# ===============================================================================
# class Path(db.Model):
class Path(polymodel.PolyModel):
    """Derived from PolyModel, so we can perform queries on objects of the parent class"""

    # path = db.StringProperty(required=True)
    # size = db.IntegerProperty(required=True, default=0) # cache the size of content, 0 for dir
    # create_time = db.DateTimeProperty(required=True, auto_now_add = True)
    # modify_time = db.DateTimeProperty(required=True, auto_now = True)
    _kind = "Path"
    _exclude_from_indexes = None
    _auto_now_add = ["create_time"]
    _auto_now = ["modify_time"]

    cache = cached_resource

    def _init_entity(self, **kwargs):
        super(Path, self)._init_entity(**kwargs)
        template = {
            "path": "",
            "size": 0,
            "create_time": datetime.datetime.utcnow(),
            "modify_time": datetime.datetime.utcnow(),
        }
        for key in template:
            self._entity.setdefault(key, template[key])

    def set_key(self):
        if len(self.path) > 128:
            self._entity.key = self._entity.key.completed_key(
                hashlib.md5(self.path.encode("utf-8")).hexdigest()
            )
        else:
            self._entity.key = self._entity.key.completed_key(self.path)

    def put(self):
        logging.debug("Path.put(%r)" % (self.path))
        if not self.is_saved():
            self.set_key()
        db.Model.put(self)
        self.cache.set(self.path, self)
        self.cache.del_list(os.path.dirname(self.path))
        return

    def delete(self):
        logging.debug("Path.delete(%r)" % (self.path))
        if self.path == "/":
            raise RuntimeError("Though shalt not delete root")
        self.cache.delete(self.path)
        self.cache.del_list(os.path.dirname(self.path))
        return db.Model.delete(self)

    def __repr__(self):
        return "%s('%s')" % (type(self).class_name(), self.path)

    @classmethod
    def list_by_path(cls, path):
        # result = list(cls.gql("WHERE path = :1", path))
        query = db.get_client().query(kind=cls._kind)
        query.add_filter("path", "=", path)
        result = []
        for entity in query.fetch():
            instance = cls.from_entity(entity)
            result.append(instance)
        return result

    # CHECKME: always calling Path here (to avoid asking once for Dir and once for File)
    @classmethod
    def list_by_parent_path(cls, parent_path):
        # result = list(Path.gql("WHERE parent_path=:1", self))
        # query = db.get_client().query(kind=cls._kind)
        query = db.get_client().query(kind="Path")
        # CHECKME: don't use parent here - ancestor queries return all descendants (at all levels)
        if isinstance(parent_path, db.Model):
            query.add_filter("parent_path", "=", parent_path.key())
        else:
            query.add_filter("parent_path", "=", parent_path)
        result = []
        for entity in query.fetch():
            instance = cls.from_entity(entity)
            result.append(instance)
        return result

    @classmethod
    def normalize(cls, p):
        """
         /foo/bar/ -> /foo/bar
         / -> /
         // -> /
        """
        # if not isinstance(p, unicode):
        #     logging.debug("Path.normalize: encoding str %s to unicode.", repr(p))
        #     p = str.decode(p, 'utf-8')
        if not isinstance(p, str):
            p = p.decode("utf-8")
        result = os.path.normpath(p)
        # mw: added for Windows:
        result = result.replace("\\", "/")
        result = result.replace("//", "/")
        # if not isinstance(result, unicode):
        #     result = result.decode('utf-8')
        if p != result:
            logging.debug("Path.normalize(%r): %r." % (p, result))
        return result

    @classmethod
    def basename(cls, p):
        return os.path.basename(p)

    @classmethod
    def get_parent_path(cls, p):
        """
         /foo/bar -> /foo
        """
        return os.path.dirname(cls.normalize(p))

    # @classmethod
    # def check_existence(cls, path):
    #     """Checking for a path existence.
    #
    #     Querying for the key should be faster than SELECET *.
    #     This also
    #     """
    #     path = cls.normalize(path)
    #     result = cls.cache.get(path)
    #     if result:
    #         return result
    #     logging.debug("check_existence(%r)" % path)
    #     result = db.GqlQuery("SELECT __key__ WHERE path = :1", path)
    #     return result is not None

    @classmethod
    def retrieve(cls, path):
        logging.debug("Path.retrieve(%s, %r)" % (cls.__name__, path))
        assert cls is Path
        path = cls.normalize(path)
        assert path.startswith("/")
        result = cls.cache.get(path)
        if result:
            # logging.debug('Cached result: %s' % result)
            return result
        # result = list(cls.gql("WHERE path = :1", path))
        result = cls.list_by_path(path)
        if len(result) == 1:
            result = result[0]
            # assert type(result) in (Path, cls)
            cls.cache.set(path, result)
            # logging.debug('New result: %s' % result)
            return result
        elif len(result) == 0:
            # TODO: cache 'Not found' also
            # logging.debug('No result')
            return None
        else:
            raise ValueError("The given path has more than one entities", path)

    @classmethod
    def new(cls, path):
        # Make sure, we don't instantiate <Path> objects
        assert cls in (Dir, File)
        logging.debug("%s.new(%r)" % (cls.__name__, path))
        path = cls.normalize(path)
        # here we use Dir.retrieve because the parent must be a Dir.
        # parent_path = Dir.retrieve(cls.get_parent_path(path))
        parent_path = Path.retrieve(cls.get_parent_path(path))
        if path != "/":
            if not parent_path:
                raise RuntimeError("Parent path does not exists for: %r" % path)
            if type(parent_path) is not Dir:
                raise RuntimeError("Parent must be a Dir for: %r" % path)
        if DO_EXPENSIVE_CHECKS:
            if Path.retrieve(path):
                raise RuntimeError("Path exists: %r" % path)
        # CHECKME: don't use parent here - ancestor queries return all descendants (at all levels)
        if isinstance(parent_path, db.Model):
            result = cls(path=path, parent_path=parent_path.key())
        else:
            result = cls(path=path, parent_path=parent_path)
        if not result.is_saved():
            logging.debug("No complete key available yet")
            result.set_key()
        result.put()
        return result


# ===============================================================================
# Dir
# ===============================================================================
class Dir(Path):
    # parent_path = db.ReferenceProperty(Path)
    # _kind = 'Dir'
    # _exclude_from_indexes = None
    # _auto_now_add = ['create_time']
    # _auto_now = ['modify_time']
    # cache = cached_dir

    def _init_entity(self, **kwargs):
        super(Dir, self)._init_entity(**kwargs)
        self._entity.setdefault("parent_path", None)

    def get_content(self):
        # result = list(self.dir_set) + list(self.file_set)
        # logging.debug("Dir.get_content: %r" % result)
        # TODO: ORDER BY
        # result = list(Path.gql("WHERE parent_path=:1", self))
        result = self.cache.get_list(self.path)
        if result:
            logging.debug("Dir.get_content: HIT %r" % result)
            return result
        result = Path.list_by_parent_path(self)
        logging.debug("Dir.get_content: MISS %r" % result)
        self.cache.set_list(self.path, result)
        # preset items in cache since we will probably need them right after this
        if isinstance(result, list) and len(result) > 0 and isinstance(result[0], Path):
            for item in result:
                self.cache.set(item.path, item)
        return result

    def delete(self, recursive=False):
        logging.debug("Dir.delete(%s): %r" % (recursive, self.path))
        if not recursive:
            # TODO: faster lookup (for __key__)
            if len(self.get_content()) > 0:
                raise RuntimeError("Dir must be empty")
        else:
            for p in self.get_content():
                logging.debug("Dir.delete(%s): %r, p=%r" % (recursive, self.path, p))
                if type(p) is Dir:
                    p.delete(recursive)
                elif type(p) is File:
                    p.delete()
                else:
                    RuntimeError("invalid child type")
        # for d in self.dir_set:
        #     logging.debug("Dir.delete(%s): %r, d=%r" % (recursive, self.path, d))
        #     d.delete(recursive)
        # for f in self.file_set:
        #     logging.debug("Dir.delete(%s): %r, f=%r" % (recursive, self.path, f))
        #     f.delete()
        Path.delete(self)
        return


# ===============================================================================
# File
# ===============================================================================
class File(Path):
    ChunkSize = 800 * 1024  # split file to chunks at most 800K

    # parent_path = db.ReferenceProperty(Path)
    # content = db.BlobProperty(default='')
    # content = db.ListProperty(db.Blob)
    # _kind = 'File'
    # _exclude_from_indexes = None
    # _auto_now_add = ['create_time']
    # _auto_now = ['modify_time']

    # cache = cached_file

    def _init_entity(self, **kwargs):
        super(File, self)._init_entity(**kwargs)
        self._entity.setdefault("parent_path", None)

    def put(self):
        if self.is_saved():
            # CHECKME: this doesn't return the chunks yet
            if self.size == 0:
                self.size = sum(
                    len(chunk["data"]) for chunk in Chunk.fetch_entities_by_file(self)
                )  # use ancestor instead?
        else:
            self.size = 0
        Path.put(self)
        return

    def get_content(self):
        """
        Join chunks together.
        """
        if self.is_saved():
            # chunks = Chunk.gql("WHERE file=:1 ORDER BY offset ASC", self)
            chunks = Chunk.fetch_entities_by_file(self)
        else:
            chunks = []
        result = b"".join(chunk["data"] for chunk in chunks)
        # logging.debug('Content: %s' % repr(result))
        return result

    def put_content(self, s):
        """
        Split the DB transaction to serveral small chunks,
        to keep we don't exceed appengine's limit.
        """
        size = len(s)
        # self.content = []
        if not self.is_saved():
            logging.error("No complete key available yet")
            self.set_key()
            # raise Exception
        else:
            # clear old chunks
            # for chunk in self.chunk_set:  # use ancestor instead?
            #    chunk.delete()
            chunk_keys = Chunk.list_keys_by_file(self)
            if chunk_keys and len(chunk_keys) > 0:
                db.get_client().delete_multi(chunk_keys)

        # put new datas
        for i in range(0, size, self.ChunkSize):
            logging.debug("File.put_content putting the chunk with offset = %d" % i)
            data = s[i : i + self.ChunkSize]
            # ck = Chunk(file=self.key(), offset=i, data=data, parent=self.key())  # use parent here?
            ck = Chunk(offset=i, data=data, parent=self.key())
            ck.put()
        self.size = size
        self.put()
        return

    def delete(self):
        """
        Also delete chunks.
        """
        logging.debug("File.delete %s" % repr(self.path))
        # for chunk in self.chunk_set:  # use ancestor instead?
        #    chunk.delete()
        chunk_keys = Chunk.list_keys_by_file(self)
        if chunk_keys and len(chunk_keys) > 0:
            db.get_client().delete_multi(chunk_keys)
        Path.delete(self)
        return


# ===============================================================================
# Chunk
# ===============================================================================
class Chunk(db.Model):
    # file = db.ReferenceProperty(File)
    # offset = db.IntegerProperty(required=True)
    # data = db.BlobProperty(default=b'')
    _kind = "Chunk"
    _exclude_from_indexes = ["data"]
    _auto_now_add = None
    _auto_now = None

    def _init_entity(self, **kwargs):
        super(Chunk, self)._init_entity(**kwargs)
        template = {
            #'file': None,
            "offset": 0,
            "data": b"",
        }
        for key in template:
            self._entity.setdefault(key, template[key])

    def __len__(self):
        return len(self.data)

    @classmethod
    def fetch_entities_by_file(cls, file):
        # chunks = Chunk.gql("WHERE file=:1 ORDER BY offset ASC", self)
        # query = db.get_client().query(kind=cls._kind)  # use ancestor instead?
        query = db.get_client().query(kind=cls._kind, ancestor=file.key())
        # query.add_filter('file', '=', file.key())
        query.order = ["offset"]
        return query.fetch()

    @classmethod
    def list_keys_by_file(cls, file):
        # chunks = Chunk.gql("WHERE file=:1 ORDER BY offset ASC", self)
        # query = db.get_client().query(kind=cls._kind)  # use ancestor instead?
        query = db.get_client().query(kind=cls._kind, ancestor=file.key())
        query.keys_only()
        # query.add_filter('file', '=', file.key())
        result = []
        for entity in query.fetch():
            result.append(entity.key)
        return result
