#!/usr/bin/env python

import errno
import os
import stat
import sys
import time

import fuse
import requests


USER_AGENT = 'NHentaiFS 0.0.1'
MAX_CACHE_AGE = 60*60
REQUESTS_TIMEOUT = 10
COVER_URL = 'https://t.nhentai.net/galleries/{}/cover.jpg'
THUMB_URL = 'https://t.nhentai.net/galleries/{}/thumb.jpg'
PAGE_URL = 'https://i.nhentai.net/galleries/{}/{}.{}'
PAGE_THUMB_URL = 'https://t.nhentai.net/galleries/{}/{}t.{}'
ALL_URL = 'https://nhentai.net/api/galleries/all?page={}'
GALLERY_URL = 'https://nhentai.net/api/gallery/{}'
SEARCH_URL = 'https://nhentai.net/api/galleries/search?query={}&page={}'
TAGGED_URL = 'https://nhentai.net/api/galleries/tagged?tag_id={}&page={}'


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
        'cover.jpg': COVER_URL.format(media_ID),
        'thumb.jpg': THUMB_URL.format(media_ID),
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


def is_url(x):
    return type(x) is str and (x.startswith('http://') or
                               x.startswith('https://'))


class NHentaiFS(fuse.Operations):
    def __init__(self, root):
        self.root = root
        self.ctime = now()
        self.last_request = now()
        self.cache = {}
        self.query_max = 2048
        self.query = list(bytes(self.query_max))
        self.fs = {'all': {},
                   'gallery': {},
                   'search': {'query': [], 'results': {}},
                   'tagged': {}}

        self.attrs = {}
        self.attrs['/'] = make_attrs(self.ctime, True)
        self.attrs['/all'] = make_attrs(self.ctime, True)
        self.attrs['/gallery'] = make_attrs(self.ctime, True)
        self.attrs['/search'] = make_attrs(self.ctime, True)
        self.attrs['/search/query'] = make_attrs(self.ctime, False, self.query)
        self.attrs['/search/results'] = make_attrs(self.ctime, True)
        self.attrs['/tagged'] = make_attrs(self.ctime, True)

    def request(self, url):
        log('request', url)
        if self.last_request == now():
            time.sleep(1)
        response = requests.get(
            url, headers={'user-agent': USER_AGENT}, timeout=REQUESTS_TIMEOUT)
        check_response(response)
        self.last_request = now()
        if response.headers['content-type'] == 'application/json':
            return response.json()
        else:
            return response.content

    def add_to_cache(self, url, transformer, ctx):
        result = self.request(url)
        if transformer:
            result = transformer(result, ctx)
        self.cache[url] = [now(), result]
        return result

    def fetch(self, url, transformer=None, ctx=None):
        if url in self.cache:
            log('cache hit')
            timestamp, response = self.cache[url]
            if now() - timestamp > MAX_CACHE_AGE:
                log('cache outdated')
                return self.add_to_cache(url, transformer, ctx)
            return response
        else:
            log('cache missed')
            return self.add_to_cache(url, transformer, ctx)

    def add_to_attrs_cache(self, path, attrs):
        self.attrs[path] = attrs
        return attrs

    def add_attrs(self, loc, path, ctx):
        if type(loc) is dict and 'uploaded' in loc:
            ctx['ctime'] = loc['uploaded']
        isdir = True if type(loc) in [list, dict] else False
        self.attrs[path] = make_attrs(ctx['ctime'], isdir, loc)

    def json_to_galleries(self, json, ctx):
        galleries = [json_to_gallery(json) for json in json['result']]
        if not galleries:
            raise fuse.FuseOSError(errno.ENOENT)
        walk_json(galleries, self.add_attrs, path=ctx['path'], ctx=ctx)
        return galleries

    def json_to_gallery(self, json, ctx):
        gallery = json_to_gallery(json)
        walk_json(gallery, self.add_attrs, path=ctx['path'], ctx=ctx)
        return gallery

    def getattr_all(self, path, subpath):
        page, rest = split_path(subpath)
        if type(try_convert(page)) is int:
            raise fuse.FuseOSError(errno.ENOENT)
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch(ALL_URL.format(page),
                               self.json_to_galleries, ctx)
        self.fs['all'][int(page)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_gallery(self, path, subpath):
        gallery_ID, rest = split_path(subpath)
        ctx = {'path': path, 'ctime': now()}
        gallery = self.fetch(GALLERY_URL.format(gallery_ID),
                             self.json_to_gallery, ctx)
        self.fs['gallery'][int(gallery_ID)] = gallery
        try:
            dig(gallery, rest)
        except (KeyError, AttributeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_search(self, path, subpath):
        segment, rest = split_path(subpath)
        if segment != 'results':
            raise fuse.FuseOSError(errno.ENOENT)
        if not rest:
            return self.attrs[path]
        page, rest = split_path(rest)
        log('page, rest', path, subpath, segment, rest, split_path(rest))
        query = self.extract_query().split('\n', maxsplit=1)[0]
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch(SEARCH_URL.format(query, page),
                               self.json_to_galleries, ctx)
        self.fs['search']['results'][int(page)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr_tagged(self, path, subpath):
        tag_ID, rest = split_path(subpath)
        if not rest:
            if path not in self.attrs:
                self.fs['tagged'][int(tag_ID)] = {}
                self.attrs[path] = make_attrs(now(), True)
            return self.attrs[path]
        page, rest = split_path(rest)
        ctx = {'path': path, 'ctime': now()}
        galleries = self.fetch(TAGGED_URL.format(tag_ID, page),
                               self.json_to_galleries, ctx)
        if int(tag_ID) not in self.fs['tagged']:
            self.fs['tagged'][int(tag_ID)] = {}
        self.fs['tagged'][int(tag_ID)][int(page)] = galleries
        try:
            dig(galleries, rest)
        except (KeyError, AttributeError):
            raise fuse.FuseOSError(errno.ENOENT)
        return self.attrs[path]

    def getattr(self, path, fh=None):
        log('getattr', path)
        if path in self.attrs:
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
        else:
            raise fuse.FuseOSError(errno.ENOENT)

    def extract_query(self):
        try:
            end = self.query.index(0)
            self.attrs['/search/query']['st_size'] = end
            return str(bytes(self.query[0:end]), 'utf-8')
        except ValueError:
            self.attrs['/search/query']['st_size'] = self.query_max
            return str(bytes(self.query), 'utf-8')

    def read(self, path, size, offset, _fh):
        log('read', path, size, offset)
        loc = str(dig(self.fs, path[1:]))
        if path == '/search/query':
            query = self.extract_query()
            return bytes(query[offset:offset+size], 'utf-8')
        elif is_url(loc):
            loc = self.fetch(loc)
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

    def write(self, path, data, offset, _fh):
        log('write', path, data, offset)
        if path != '/search/query':
            raise fuse.FuseOSError(errno.EROFS)
        if offset + len(data) >= self.query_max:
            raise fuse.FuseOSError(errno.EFBIG)
        count = 0
        for i, byte in enumerate(data):
            self.query[offset+i] = byte
            count += 1
        if count < self.query_max:
            self.query[count] = 0
        self.attrs[path]['st_mtime'] = now()
        # reset search cache
        self.fs['search']['results'] = {}
        self.attrs = {key: value for key, value in self.attrs.items()
                      if not key.startswith('/search/results/')}
        return count

    # necessary for `echo 123 > search/query` to work
    def truncate(self, path, length, fh=None):
        log('truncate', path, length)
        if path != '/search/query':
            raise fuse.FuseOSError(errno.EROFS)
        return 0


def main(mountpoint):
    fuse.FUSE(NHentaiFS(mountpoint), mountpoint, foreground=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('Usage: {} <mountpoint>'.format(sys.argv[0]))
        sys.exit(1)
    main(sys.argv[1])
