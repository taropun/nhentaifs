# Usage

```
mkdir mnt
./nhentaifs.py mnt & && cd mnt
ls all/1 # list first 25 galleries of the frontpage
ls gallery/123 # list contents of gallery 123
sxiv gallery/123/pages/* # read an evangelion doujin
echo 'futa language:japanese' > search/query # search for dickgirls in moon
ls search/results/1 # list first 25 search results
grep futa search/results/1/0/tags/* # figure out the dickgirls tag ID
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
- /gallery
  - :gallery_id
    - <gallery>
- /search
  - /query <- write into it
  - /results
    - :page_id
      - /0
        - <gallery>
      - ...
- /tagged
  - :tag_id
    - :page_id
      - /0
        - <gallery>
      - ...
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

- Make use of the `num_pages`/`per_page` information in the JSON
  responses
- Expose more endpoints (such as an artist or tag listing)
- Make caching/rate limiting configurable
- Consider changing the search interface (to something like
  `search/:query/:page_id`
- Emit an `index.html` for every directory for lazy browsing
