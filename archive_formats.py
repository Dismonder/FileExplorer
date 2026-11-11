"""7z/RAR extraction through the system libarchive into a private staging directory."""
import ctypes
import ctypes.util
import os
import stat
from pathlib import Path, PurePosixPath


def archive_library():
    name = ctypes.util.find_library('archive')
    if not name:
        raise OSError('Do rozpakowania 7z/RAR potrzebna jest systemowa biblioteka libarchive. '
                      'Archiwum można także otworzyć w Ark.')
    library = ctypes.CDLL(name)
    signatures = {
        'archive_read_new': (ctypes.c_void_p, []),
        'archive_read_support_filter_all': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_read_support_format_all': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_read_open_filename': (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]),
        'archive_read_next_header': (ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
        'archive_read_data': (ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]),
        'archive_read_free': (ctypes.c_int, [ctypes.c_void_p]),
        'archive_error_string': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_pathname': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_filetype': (ctypes.c_uint, [ctypes.c_void_p]),
        'archive_entry_perm': (ctypes.c_uint, [ctypes.c_void_p]),
        'archive_entry_symlink': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_hardlink': (ctypes.c_char_p, [ctypes.c_void_p]),
        'archive_entry_mtime': (ctypes.c_int64, [ctypes.c_void_p]),
    }
    for function, (result, arguments) in signatures.items():
        method = getattr(library, function)
        method.restype = result
        method.argtypes = arguments
    return library


def safe_member_path(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Archiwum zawiera ścieżkę poza folderem: {name}')
    return path


def extract_extra_archive(source, destination):
    """Defer contained links until data is complete; caller publishes staging on success."""
    library = archive_library()
    reader = library.archive_read_new()
    if not reader:
        raise MemoryError('Nie można utworzyć czytnika archiwum.')
    destination = Path(destination)
    directories = []
    links = []
    root = destination.resolve()

    def check(status):
        if status < 0:
            message = library.archive_error_string(reader)
            text = message.decode('utf-8', errors='replace') if message else 'Błąd odczytu archiwum'
            raise OSError(text + '. Dla archiwów chronionych hasłem użyj Ark.')

    try:
        check(library.archive_read_support_filter_all(reader))
        check(library.archive_read_support_format_all(reader))
        check(library.archive_read_open_filename(reader, os.fsencode(source), 10240))
        header = ctypes.c_void_p()
        buffer = ctypes.create_string_buffer(256 * 1024)
        while True:
            status = library.archive_read_next_header(reader, ctypes.byref(header))
            if status == 1:  # ARCHIVE_EOF
                break
            check(status)
            raw = library.archive_entry_pathname(header)
            relative = safe_member_path(os.fsdecode(raw) if raw else '')
            kind = library.archive_entry_filetype(header)
            target = destination.joinpath(*relative.parts)
            mode = library.archive_entry_perm(header) & 0o777
            modified = library.archive_entry_mtime(header)
            symlink = library.archive_entry_symlink(header)
            hardlink = library.archive_entry_hardlink(header)
            if symlink or hardlink:
                value = os.fsdecode(symlink or hardlink)
                if PurePosixPath(value).is_absolute():
                    raise ValueError(f'Archiwum zawiera dowiązanie poza folderem: {relative}')
                source = target.parent / value if symlink else destination / safe_member_path(value)
                if not source.resolve().is_relative_to(root):
                    raise ValueError(f'Archiwum zawiera dowiązanie poza folderem: {relative}')
                target.parent.mkdir(parents=True, exist_ok=True)
                links.append((target, value, bool(symlink)))
                continue
            if kind == stat.S_IFDIR:
                target.mkdir(parents=True, exist_ok=True)
                if target != destination:
                    directories.append((target, mode, modified))
                continue
            if kind != stat.S_IFREG:
                raise ValueError(f'Archiwum zawiera plik specjalny: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            # Duplicate names fail instead of silently replacing an earlier member.
            with target.open('xb') as output:
                while True:
                    count = library.archive_read_data(reader, buffer, len(buffer))
                    check(count)
                    if count == 0:
                        break
                    output.write(buffer.raw[:count])
            target.chmod(mode)
            os.utime(target, (modified, modified))
        for target, value, symbolic in links:
            source = target.parent / value if symbolic else destination / safe_member_path(value)
            if not target.parent.resolve().is_relative_to(root) or not source.resolve().is_relative_to(root):
                raise ValueError(f'Archiwum zawiera dowiązanie poza folderem: {target.name}')
            if symbolic:
                target.symlink_to(value)
            elif stat.S_ISREG(source.lstat().st_mode):
                os.link(source, target, follow_symlinks=False)
            else:
                raise ValueError(f'Archiwum zawiera nieprawidłowe dowiązanie twarde: {target.name}')
        # Read-only directory modes are applied after their descendants are complete.
        for target, mode, modified in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
            target.chmod(mode)
            os.utime(target, (modified, modified))
    finally:
        library.archive_read_free(reader)
