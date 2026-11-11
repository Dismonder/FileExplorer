"""Streaming local searches, optional Baloo results and KDE-compatible user tags."""
import codecs
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import errno
import fnmatch
import mimetypes
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import time


MODES = {'name', 'content', 'tags', 'metadata', 'all'}
CHUNK = 256 * 1024
CONTENT_LIMIT = 64 * 1024 * 1024
SKIPPED_ROOTS = frozenset({'/proc', '/sys', '/dev', '/run'})


def user_metadata(path):
    result = {}
    for key in ('xdg.tags', 'xdg.comment', 'baloo.rating', 'xdg.origin.url'):
        try:
            result[key] = os.getxattr(path, 'user.' + key, follow_symlinks=False).decode('utf-8', 'replace')
        except (OSError, AttributeError):
            result[key] = ''
    return result


def get_tags(path):
    return tuple(tag for tag in user_metadata(path)['xdg.tags'].split(',') if tag)


def normalize_tags(tags):
    result = []
    for tag in tags:
        tag = str(tag).strip()
        if any(character in tag for character in ',\x00\n\r'):
            raise ValueError('Tagi nie mogą zawierać przecinków ani znaków nowej linii.')
        if tag and tag not in result:
            result.append(tag)
    return tuple(result)


def set_tags(path, tags):
    tags = normalize_tags(tags)
    if not hasattr(os, 'setxattr'):
        raise OSError('Ten system nie obsługuje tagowania plików przez xattr.')
    if tags:
        os.setxattr(path, 'user.xdg.tags', ','.join(tags).encode('utf-8'), follow_symlinks=False)
    else:
        try:
            os.removexattr(path, 'user.xdg.tags', follow_symlinks=False)
        except OSError as exc:
            if exc.errno not in {errno.ENODATA, getattr(errno, 'ENOATTR', errno.ENODATA)}:
                raise


def contains_text(path, needle, cancelled=lambda: False, limit=CONTENT_LIMIT):
    """Read bounded chunks without following links or waiting on FIFOs/devices."""
    needle = needle.casefold()
    if not needle or cancelled():
        return False, False
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False, False
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            consumed, tail, decoder = 0, '', None
            while consumed < limit and not cancelled():
                chunk = stream.read(min(CHUNK, limit - consumed))
                if not chunk:
                    break
                consumed += len(chunk)
                if decoder is None:
                    encoding = ('utf-32' if chunk.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)) else
                                'utf-16' if chunk.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else 'utf-8-sig')
                    if encoding == 'utf-8-sig' and b'\x00' in chunk:
                        return False, False
                    decoder = codecs.getincrementaldecoder(encoding)(errors='replace')
                text = tail + decoder.decode(chunk).casefold()
                if needle in text:
                    return True, False
                tail = text[-max(0, len(needle) - 1):] if len(needle) > 1 else ''
            if cancelled():
                return False, False
            if decoder and needle in tail + decoder.decode(b'', final=True).casefold():
                return True, False
            return False, consumed >= limit and bool(stream.read(1))
    except OSError:
        return False, False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _compare(actual, expression, numeric=float):
    match = re.fullmatch(r'(>=|<=|>|<|=)?(.+)', expression)
    if not match:
        return False
    operator, expected = match.groups()
    try:
        expected = numeric(expected)
    except (ValueError, TypeError, OverflowError):
        return False
    return {'>': actual > expected, '<': actual < expected, '>=': actual >= expected,
            '<=': actual <= expected, '=': actual == expected, None: actual == expected}[operator]


def _bytes(value):
    match = re.fullmatch(r'([\d.]+)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)?', value, re.IGNORECASE)
    if not match:
        raise ValueError('Nieprawidłowy rozmiar.')
    unit = (match[2] or 'B').upper()
    exponent = {'B': 0, 'KB': 1, 'MB': 2, 'GB': 3, 'TB': 4, 'KIB': 1, 'MIB': 2, 'GIB': 3, 'TIB': 4}[unit]
    return float(match[1]) * (1024 if 'I' in unit else 1000) ** exponent


def metadata_matches(path, info, query):
    metadata = user_metadata(path)
    mime = 'inode/directory' if stat.S_ISDIR(info.st_mode) else mimetypes.guess_type(path)[0] or 'application/octet-stream'
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = [query]
    for token in tokens:
        if token.upper() == 'AND':
            continue
        key, separator, value = token.partition(':')
        key = key.casefold()
        value = value.casefold()
        if separator:
            if key in {'tag', 'tags'}:
                matched = any(value in tag.casefold() for tag in metadata['xdg.tags'].split(',') if tag)
            elif key in {'comment', 'usercomment'}:
                matched = value in metadata['xdg.comment'].casefold()
            elif key == 'rating':
                matched = _compare(int(metadata['baloo.rating'] or 0), value)
            elif key in {'mime', 'mimetype'}:
                matched = fnmatch.fnmatchcase(mime, value)
            elif key in {'name', 'filename'}:
                matched = value in os.path.basename(path).casefold()
            elif key == 'size':
                matched = _compare(info.st_size, value, _bytes)
            elif key in {'modified', 'mtime'}:
                matched = _compare(info.st_mtime, value, lambda date: datetime.fromisoformat(date).timestamp())
            elif key in {'type', 'kind'}:
                aliases = {'folder': 'inode/directory', 'text': 'text/*', 'image': 'image/*',
                           'audio': 'audio/*', 'video': 'video/*', 'document': 'application/pdf'}
                matched = fnmatch.fnmatchcase(mime, aliases.get(value, value))
            else:
                matched = False  # unknown properties require an actual Baloo index
        else:
            matched = token.casefold() in ' '.join([os.path.basename(path), mime, *metadata.values()]).casefold()
        if not matched:
            return False
    return bool(tokens)


@dataclass
class SearchStats:
    indexed: int = 0
    limited_content: int = 0
    unreadable: int = 0
    index_error: str = ''


class SearchEngine:
    def __init__(self, root, query, mode='name', show_hidden=False, cancelled=lambda: False,
                 use_index=True, content_limit=CONTENT_LIMIT):
        if mode not in MODES:
            raise ValueError('Nieznany tryb wyszukiwania.')
        self.root = os.path.abspath(os.fspath(root))
        self.query = query.strip()
        self.mode, self.show_hidden, self.cancelled = mode, show_hidden, cancelled
        self.use_index, self.content_limit = use_index, content_limit
        self.stats = SearchStats()
        self.pattern = re.compile(fnmatch.translate(self.query), re.IGNORECASE) if any(ch in self.query for ch in '*?[') else None

    def name_matches(self, name):
        return bool(self.pattern.match(name)) if self.pattern else self.query.casefold() in name.casefold()

    def indexed_paths(self, limit):
        executable = shutil.which('baloosearch6') or shutil.which('baloosearch')
        if not executable or not self.use_index or self.mode == 'name' or self.cancelled():
            return ()
        query = (f'content:{json_quote(self.query)}' if self.mode == 'content' else
                 f'tag:{json_quote(self.query)}' if self.mode == 'tags' else self.query)
        process = None
        try:
            process = subprocess.Popen([executable, '--directory', self.root, '--limit', str(limit),
                                        '--sort', 'none', '--', query], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 4
            while not self.cancelled() and time.monotonic() < deadline:
                try:
                    output, error = process.communicate(timeout=0.1)
                    if process.returncode:
                        self.stats.index_error = error.strip()
                        return ()
                    return tuple(path for path in output.splitlines() if path.startswith('/'))
                except subprocess.TimeoutExpired:
                    continue
            self.stats.index_error = 'Anulowano lub przekroczono czas odpowiedzi Baloo.'
        except OSError as exc:
            self.stats.index_error = str(exc)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.5)
            if process is not None:
                for pipe in (process.stdout, process.stderr):
                    if pipe:
                        pipe.close()
        return ()

    def results(self, limit=20000):
        seen = set()
        for path in self.indexed_paths(limit):
            if self.cancelled():
                return
            path = os.path.abspath(path)
            if not Path(path).is_relative_to(Path(self.root)) or path == self.root:
                continue
            if not self.show_hidden and any(part.startswith('.') for part in Path(path).relative_to(self.root).parts):
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or path in seen:
                continue
            seen.add(path)
            self.stats.indexed += 1
            yield path, stat.S_ISDIR(info.st_mode), info.st_size, info.st_mtime
            if len(seen) >= limit:
                return
        queue = deque([self.root])
        while queue and not self.cancelled():
            directory = queue.popleft()
            try:
                iterator = os.scandir(directory)
            except OSError:
                self.stats.unreadable += 1
                continue
            with iterator:
                for entry in iterator:
                    if self.cancelled():
                        return
                    if entry.name.startswith('.') and not self.show_hidden:
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        if is_dir and entry.path not in SKIPPED_ROOTS:
                            queue.append(entry.path)
                        if entry.path in seen:
                            continue
                        matched = self.mode in {'name', 'all'} and self.name_matches(entry.name)
                        info = None
                        if not matched and self.mode in {'metadata', 'tags', 'all'}:
                            info = entry.stat(follow_symlinks=False)
                            matched = (any(self.query.casefold() in tag.casefold() for tag in get_tags(entry.path))
                                       if self.mode == 'tags' else metadata_matches(entry.path, info, self.query))
                        if not matched and self.mode in {'content', 'all'} and not is_dir and not entry.is_symlink():
                            matched, limited = contains_text(entry.path, self.query, self.cancelled, self.content_limit)
                            self.stats.limited_content += int(limited)
                        if matched:
                            info = info or entry.stat(follow_symlinks=False)
                            seen.add(entry.path)
                            yield entry.path, is_dir, info.st_size, info.st_mtime
                            if len(seen) >= limit:
                                return
                    except (OSError, ValueError):
                        self.stats.unreadable += 1

    @property
    def note(self):
        parts = []
        if self.mode != 'name':
            parts.append(f'Baloo: {self.stats.indexed}; przeszukano też pliki lokalnie')
        if self.stats.limited_content:
            parts.append(f'limit treści {self.content_limit // (1024 * 1024)} MiB: {self.stats.limited_content} plików')
        if self.stats.unreadable:
            parts.append(f'nieczytelne elementy: {self.stats.unreadable}')
        return '; '.join(parts)


def json_quote(value):
    # Baloo accepts a quoted property value; this is a single argv argument.
    import json
    return json.dumps(value, ensure_ascii=False)


def validate_saved_search(value):
    if not isinstance(value, dict) or value.get('mode') not in MODES or not isinstance(value.get('query'), str):
        raise ValueError('Nieprawidłowe zapisane wyszukiwanie.')
    if not value['query'].strip() or not isinstance(value.get('root'), str) or not os.path.isabs(value['root']):
        raise ValueError('Wyszukiwanie musi zawierać folder i zapytanie.')
    return {'name': str(value.get('name') or value['query']), 'root': value['root'],
            'query': value['query'], 'mode': value['mode']}
