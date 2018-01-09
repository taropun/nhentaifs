#!/usr/bin/env python3

import errno
import os
import stat
import sys
import time

import fuse
import requests


def get_int_env(key):
    value = os.getenv(key)
    if value:
        return int(value)


USER_AGENT = 'NHentaiFS 0.0.1'
MAX_JSON_CACHE_AGE = get_int_env('MAX_JSON_CACHE_AGE') or 15*60
MAX_IMAGE_CACHE_SIZE = get_int_env('MAX_IMAGE_CACHE_SIZE') or 500*1024*1024
REQUESTS_TIMEOUT = get_int_env('REQUESTS_TIMEOUT') or 10
COVER_URL = 'https://t.nhentai.net/galleries/{}/cover.{}'
THUMB_URL = 'https://t.nhentai.net/galleries/{}/thumb.{}'
PAGE_URL = 'https://i.nhentai.net/galleries/{}/{}.{}'
PAGE_THUMB_URL = 'https://t.nhentai.net/galleries/{}/{}t.{}'
ALL_URL = 'https://nhentai.net/api/galleries/all?page={}'
GALLERY_URL = 'https://nhentai.net/api/gallery/{}'
SEARCH_URL = 'https://nhentai.net/api/galleries/search?query={}&page={}'
TAGGED_URL = 'https://nhentai.net/api/galleries/tagged?tag_id={}&page={}'
RELATED_URL = 'https://nhentai.net/api/gallery/{}/related'


def log(prefix, *args):
    if os.getenv('DEBUG'):
        print('[info] {}:'.format(prefix), *[repr(arg) for arg in args])


def try_convert(arg):
    try:
        return int(arg)
    except ValueError:
        return None


def dig(json, path):
    if not path:
        return json
    for segment in path.split('/'):
        if type(json) not in [list, dict]:
            raise ValueError
        index = try_convert(segment)
        if type(index) is int:
            segment = index
        json = json[segment]
    return json


def tag_to_search_term(tag):
    key = tag['type']
    name = tag['name'].replace(' ', '-')
    return key + ':' + name


def image_type_to_ext(image_type):
    if image_type == 'j':
        return 'jpg'
    elif image_type == 'p':
        return 'png'
    else:
        return 'gif'


def page_filename(page, ext, count):
    return '{}.{}'.format(str(page).rjust(len(str(count)), '0'), ext)


def json_to_gallery(json, _ctx=None):
    media_ID = json['media_id']
    cover_ext = image_type_to_ext(json['images']['cover']['t'])
    thumb_ext = image_type_to_ext(json['images']['thumbnail']['t'])
    image_exts = [image_type_to_ext(page['t'])
                  for page in json['images']['pages']]
    count = json['num_pages']
    filenames = [page_filename(page, ext, count)
                 for page, ext in enumerate(image_exts, 1)]
    page_urls = [PAGE_URL.format(media_ID, page, ext)
                 for page, ext in enumerate(image_exts, 1)]
    thumb_urls = [PAGE_THUMB_URL.format(media_ID, page, ext, True)
                  for page, ext in enumerate(image_exts, 1)]
    return {
        'id': json['id'],
        'title': {
            'english': json['title']['english'],
            'native': json['title']['japanese'],
            'pretty': json['title']['pretty']
        },
        'uploaded': json['upload_date'],
        'tags': {tag['id']: tag_to_search_term(tag)
                 for tag in json['tags']},
        'num_pages': count,
        'cover.{}'.format(cover_ext): COVER_URL.format(media_ID, cover_ext),
        'thumb.{}'.format(thumb_ext): THUMB_URL.format(media_ID, thumb_ext),
        'filenames': '\n'.join(filenames),
        'pages': dict(zip(filenames, page_urls)),
        'thumbs': dict(zip(filenames, thumb_urls))
    }


def walk_json(json, function, path='', ctx=None):
    function(json, path, ctx)
    if type(json) is list:
        for i, item in enumerate(json):
            walk_json(item, function, '{}/{}'.format(path, i), ctx=ctx)
    elif type(json) is dict:
        for key, value in json.items():
            walk_json(value, function, '{}/{}'.format(path, key), ctx=ctx)


def now():
    return int(time.time())


def make_attrs(ctime, isdir, content=''):
    mode = (stat.S_IFDIR | 0o755) if isdir else (stat.S_IFREG | 0o644)
    size = 4*1024 if isdir else len(str(content))
    nlinks = 2 if isdir else 1
    return dict(st_mode=mode, st_ctime=ctime, st_mtime=ctime,
                st_size=size, st_dev=0, st_nlink=nlinks,
                st_uid=os.getuid(), st_gid=os.getgid())


def split_path(path, maxsplit=1):
    segments = path.split('/', maxsplit=maxsplit)
    if len(segments) == 1:
        return [segments[0], '']
    else:
        return [segments[0], segments[-1]]


def check_response(response):
    if 400 <= response.status_code < 600:
        raise fuse.FuseOSError(errno.ENOENT)


def is_image_url(x):
    return (type(x) is str and
            (x.startswith('http://') or x.startswith('https://')) and
            x[-4:] in ['.jpg', '.png', '.gif'])


class TimeoutCache(object):
    def __init__(self, max_age):
        self.storage = {}
        self.max_age = max_age

    def add(self, key, value):
        self.storage[key] = (now(), value)
        return value

    def fetch(self, key, fetcher):
        if key in self.storage:
            log('tcache hit', key)
            timestamp, value = self.storage[key]
            if timestamp < now() - self.max_age:
                log('tcache outdated', key)
                return self.add(key, fetcher(key))
            return value
        else:
            log('tcache miss', key)
            return self.add(key, fetcher(key))


class CappedCache(object):
    def __init__(self, max_size):
        self.storage = {}
        self.keys = []
        self.max_size = max_size

    def cache_too_big(self):
        total_size = sum([len(x) for x in self.storage.values()])
        return total_size > self.max_size

    def truncate(self):
        while self.cache_too_big():
            key = self.keys.pop(0)
            value = self.storage.pop(key)
            log('ccache truncation', key, len(value))

    def add(self, key, value):
        self.storage[key] = value
        self.keys.append(key)
        self.truncate()
        return value

    def fetch(self, key, fetcher):
        if key in self.storage:
            log('ccache hit', key)
            return self.storage[key]
        else:
            log('ccache miss', key)
            return self.add(key, fetcher(key))


class NHentaiFS(fuse.Operations):
    def __init__(self, root):
        self.root = root
        self.ctime = now()
        self.json_cache = TimeoutCache(MAX_JSON_CACHE_AGE)
        self.image_cache = CappedCache(MAX_IMAGE_CACHE_SIZE)
        self.fs = {'all': {}, 'gallery': {}, 'search': {},
                   'tagged': {}, 'related': {}}

        self.attrs = {}
        self.attrs['/'] = make_attrs(self.ctime, True)
        self.attrs['/all'] = make_attrs(self.ctime, True)
        self.attrs['/gallery'] = make_attrs(self.ctime, True)
        self.attrs['/search'] = make_attrs(self.ctime, True)
        self.attrs['/tagged'] = make_attrs(self.ctime, True)
        self.attrs['/related'] = make_attrs(self.ctime, True)

    def request(self, url):
        log('request', url)
        response = requests.get(
            url, headers={'user-agent': USER_AGENT}, timeout=REQUESTS_TIMEOUT)
        check_response(response)
        if response.headers['content-type'] == 'application/json':
            return response.json()
        else:
            return response.content

    def fetch_json(self, url, transformer, ctx):
        def fetcher(x):
            return transformer(self.request(x), ctx)
        return self.json_cache.fetch(url, fetcher)

    def fetch_image(self, url):
        return self.image_cache.fetch(url, self.request)

    def add_attrs(self, loc, path, ctx):
        if type(loc) is dict and 'uploaded' in loc:
            ctx['ctime'] = loc['uploaded']
        isdir = True if type(loc) in [list, dict] else False
        self.attrs[path] = make_attrs(ctx['ctime'], isdir, loc)

    def json_to_galleries(self, json, ctx):
        galleries = [json_to_gallery(json) for json in json['result']]
        if not galleries:
            raise fuse.FuseOSError(errno.ENOENT)
        if 'num_pages' not in json:
            walk_json(galleries, self.add_attrs, path=ctx['path'], ctx=ctx)
            return galleries
        result = {i: gallery for i, gallery in enumerate(galleries)}
        result['num_pages'] = json['num_pages']
        result['per_page'] = json['per_page']
        walk_json(result, self.add_attrs, path=ctx['path'], ctx=ctx)
        return result

    def json_to_gallery(self, json, ctx):
        gallery = json_to_gallery(json)
        walk_json(gallery, self.add_attrs, path=ctx['path'], ctx=ctx)
        return gallery

    def getattr_all(self, path, subpath):
        page, rest = split_path(subpath)
        if type(try_convert(page)) is not int:
            raise fuse.FuseOSError(errno.ENOENT)
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch_json(ALL_URL.format(page),
                                    self.json_to_galleries, ctx)
        self.fs['all'][int(page)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError, TypeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_gallery(self, path, subpath):
        gallery_ID, rest = split_path(subpath)
        ctx = {'path': path, 'ctime': now()}
        gallery = self.fetch_json(GALLERY_URL.format(gallery_ID),
                                  self.json_to_gallery, ctx)
        self.fs['gallery'][int(gallery_ID)] = gallery
        try:
            dig(gallery, rest)
        except (KeyError, AttributeError, TypeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_search(self, path, subpath):
        query, rest = split_path(subpath)
        if not rest:
            if path not in self.attrs:
                self.fs['search'][query] = {}
                self.attrs[path] = make_attrs(now(), True)
            return self.attrs[path]
        page, rest = split_path(rest)
        if type(try_convert(page)) is not int:
            raise fuse.FuseOSError(errno.ENOENT)
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch_json(SEARCH_URL.format(query, page),
                                    self.json_to_galleries, ctx)
        self.fs['search'][query][int(page)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError, TypeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_tagged(self, path, subpath):
        tag_ID, rest = split_path(subpath)
        if type(try_convert(tag_ID)) is not int:
            raise fuse.FuseOSError(errno.ENOENT)
        if not rest:
            if path not in self.attrs:
                self.fs['tagged'][int(tag_ID)] = {}
                self.attrs[path] = make_attrs(now(), True)
            return self.attrs[path]
        page, rest = split_path(rest)
        if type(try_convert(page)) is not int:
            raise fuse.FuseOSError(errno.ENOENT)
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch_json(TAGGED_URL.format(tag_ID, page),
                                    self.json_to_galleries, ctx)
        if int(tag_ID) not in self.fs['tagged']:
            self.fs['tagged'][int(tag_ID)] = {}
        self.fs['tagged'][int(tag_ID)][int(page)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError, TypeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_related(self, path, subpath):
        gallery_ID, rest = split_path(subpath)
        if type(try_convert(gallery_ID)) is not int:
            raise fuse.FuseOSError(errno.ENOENT)
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch_json(RELATED_URL.format(gallery_ID),
                                    self.json_to_galleries, ctx)
        self.fs['related'][int(gallery_ID)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError, TypeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr(self, path, fh=None):
        log('getattr', path)
        if path in ['/', '/all', '/gallery', '/search', '/tagged', '/related']:
            return self.attrs[path]
        segment, rest = split_path(path[1:])
        if segment == 'all':
            return self.getattr_all(path, rest)
        elif segment == 'gallery':
            return self.getattr_gallery(path, rest)
        elif segment == 'search':
            return self.getattr_search(path, rest)
        elif segment == 'tagged':
            return self.getattr_tagged(path, rest)
        elif segment == 'related':
            return self.getattr_related(path, rest)
        else:
            raise fuse.FuseOSError(errno.ENOENT)

    def read(self, path, size, offset, _fh):
        log('read', path, size, offset)
        loc = str(dig(self.fs, path[1:]))
        if is_image_url(loc):
            loc = self.fetch_image(loc)
            attrs = self.attrs[path]
            attrs['st_size'] = len(loc)
            return loc[offset:offset+size]
        else:
            return bytes(loc[offset:offset+size], 'utf-8')

    def readdir(self, path, _fh):
        log('readdir', path)
        files = ['.', '..']
        loc = dig(self.fs, path[1:])
        if type(loc) is dict:
            files += [str(key) for key in loc.keys()]
        else:
            files += [str(i) for i in range(len(loc))]
        return files


def main(mountpoint):
    fuse.FUSE(NHentaiFS(mountpoint), mountpoint, foreground=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Usage: {} <mountpoint>'.format(sys.argv[0]))
        sys.exit(1)
    main(sys.argv[1])
