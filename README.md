# Setup

Install [requests](http://docs.python-requests.org/en/master/) and [fusepy](https://github.com/terencehonles/fusepy) for Python 3.

# Usage

```
mkdir mnt
./nhentaifs.py mnt & && cd mnt
ls all/1 # list first 25 galleries of the frontpage
ls gallery/123 # list contents of gallery 123
sxiv gallery/123/pages/* # read an evangelion doujin
ls search/futa+language:japanese/1 # list first 25 search results for dickgirls in moon
grep futa search/futa+language:japanese/1/0/tags/* # figure out the dickgirls tag ID
ls tagged/779/1 # list first 25 dickgirl galleries
```

# File layout

## Tree

```
- /all
  - :page_id
    - /0
      - <gallery>
    - ...
    - /num_pages
    - /per_page
- /gallery
  - :gallery_id
    - <gallery>
- /search
  - :query
    - :page_id
      - /0
        - <gallery>
      - ...
      - /num_pages
      - /per_page
- /tagged
  - :tag_id
    - :page_id
      - /0
        - <gallery>
      - ...
      - /num_pages
      - /per_page
```

## Gallery

```
- /id
- /title
  - /english
  - /native
  - /pretty
- /uploaded (contains UNIX timestamp)
- /tags
  - :tag_id (contains query string)
  - ...
- /num_pages
- /filenames (contains filenames for both pages/thumbs)
- /cover.jpg (fetches image upon access)
- /thumb.jpg (fetches image upon access)
- /pages (zero-padded filenames)
  - /001.ext (fetches image upon access)
  - ...
- /thumbs (zero-padded filenames)
  - /001.ext (fetches image upon access)
  - ...
```

# Debugging

Run with `DEBUG=1` to log the FUSE "syscalls" and check the script's
terminal output.

# TODO

- Expose more endpoints (such as artist/tag listing and related
  galleries)
