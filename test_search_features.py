"""Searches use temporary files; indexing and tags do not modify user data."""
import codecs
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import search_features as search


class SearchFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fileexplorer-search-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def results(self, query, mode='name', **kwargs):
        engine = search.SearchEngine(self.root, query, mode, use_index=False, **kwargs)
        return {Path(item[0]).name for item in engine.results()}, engine

    def test_name_wildcards_and_content_modes_are_distinct(self):
        (self.root / 'report.txt').write_text('important phrase')
        (self.root / 'phrase.txt').write_text('different content')
        self.assertEqual(self.results('*.TXT')[0], {'report.txt', 'phrase.txt'})
        self.assertEqual(self.results('phrase', 'content')[0], {'report.txt'})
        self.assertEqual(self.results('phrase', 'all')[0], {'report.txt', 'phrase.txt'})

    def test_streaming_unicode_match_crosses_chunk_and_decoder_boundaries(self):
        path = self.root / 'text'
        path.write_text('x' * 15 + 'zażółćStraße' + 'y' * 30)
        with patch('search_features.CHUNK', 16):
            self.assertEqual(search.contains_text(path, 'zażółćSTRASSE'), (True, False))
        path.write_bytes(codecs.BOM_UTF16_LE + 'jakaś ważna treść'.encode('utf-16-le'))
        self.assertEqual(search.contains_text(path, 'ważna'), (True, False))

    def test_binary_fifos_and_symlinks_are_not_opened_as_text(self):
        binary = self.root / 'binary'
        binary.write_bytes(b'hello\x00target')
        os.mkfifo(self.root / 'pipe')
        (self.root / 'link').symlink_to(binary)
        for path in (binary, self.root / 'pipe', self.root / 'link'):
            with self.subTest(path=path):
                self.assertEqual(search.contains_text(path, 'target'), (False, False))

    def test_content_limit_is_reported_and_cancellation_stops_reads(self):
        path = self.root / 'large'
        path.write_bytes(b'x' * 100 + b'target')
        names, engine = self.results('target', 'content', content_limit=50)
        self.assertFalse(names)
        self.assertEqual(engine.stats.limited_content, 1)
        self.assertIn('limit treści', engine.note)
        self.assertEqual(search.contains_text(path, 'target', cancelled=lambda: True), (False, False))

    def test_hidden_and_symlink_directories_are_not_traversed(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret').write_text('target')
        actual = self.root / 'actual'
        actual.mkdir()
        (actual / '.hidden').write_text('target')
        (actual / 'linked').symlink_to(outside, target_is_directory=True)
        engine = search.SearchEngine(actual, 'target', 'content', use_index=False)
        self.assertEqual(list(engine.results()), [])
        engine = search.SearchEngine(actual, 'target', 'content', show_hidden=True, use_index=False)
        self.assertEqual({Path(item[0]).name for item in engine.results()}, {'.hidden'})

    def test_kde_tags_roundtrip_and_metadata_query(self):
        path = self.root / 'file.txt'
        path.write_text('content')
        try:
            search.set_tags(path, ['Projekt', 'zażółć', 'Projekt'])
            os.setxattr(path, 'user.xdg.comment', 'ważna notatka'.encode())
            os.setxattr(path, 'user.baloo.rating', b'7')
        except OSError as exc:
            self.skipTest(f'Test filesystem does not support user xattrs: {exc}')
        self.assertEqual(search.get_tags(path), ('Projekt', 'zażółć'))
        self.assertEqual(os.getxattr(path, 'user.xdg.tags'), 'Projekt,zażółć'.encode())
        self.assertEqual(self.results('projekt', 'tags')[0], {'file.txt'})
        self.assertEqual(self.results('tag:Projekt rating:>=7 mimetype:text/* size:>3B', 'metadata')[0], {'file.txt'})
        self.assertEqual(self.results('comment:"ważna notatka"', 'metadata')[0], {'file.txt'})
        self.assertFalse(self.results('rating:>7', 'metadata')[0])
        search.set_tags(path, [])
        self.assertFalse(search.get_tags(path))

    def test_invalid_tags_are_refused_before_xattr_write(self):
        with patch('search_features.os.setxattr') as write:
            for tags in (['comma,tag'], ['new\nline'], ['null\x00tag']):
                with self.assertRaises(ValueError):
                    search.set_tags(self.root / 'file', tags)
            write.assert_not_called()

    def test_indexed_results_are_scoped_filtered_deduplicated_and_supplemented(self):
        inside = self.root / 'indexed.txt'
        inside.write_text('target')
        extra = self.root / 'local.txt'
        extra.write_text('target')
        hidden = self.root / '.hidden'
        hidden.write_text('target')
        with patch.object(search.SearchEngine, 'indexed_paths', return_value=[str(inside), str(inside), '/etc/passwd', str(hidden)]):
            engine = search.SearchEngine(self.root, 'target', 'content')
            values = list(engine.results())
        self.assertEqual({Path(item[0]).name for item in values}, {'indexed.txt', 'local.txt'})
        self.assertEqual(len(values), 2)
        self.assertEqual(engine.stats.indexed, 1)

    def test_baloo_uses_content_property_as_one_argv_and_cancels_process(self):
        process = Mock()
        process.communicate.return_value = (str(self.root / 'file') + '\n', '')
        process.returncode = 0
        process.poll.return_value = 0
        with patch('search_features.shutil.which', return_value='/usr/bin/baloosearch6'), \
             patch('search_features.subprocess.Popen', return_value=process) as popen:
            engine = search.SearchEngine(self.root, 'target OR filename:evil', 'content')
            self.assertEqual(engine.indexed_paths(4), (str(self.root / 'file'),))
            argv = popen.call_args.args[0]
            self.assertEqual(argv[-2:], ['--', 'content:"target OR filename:evil"'])
            self.assertNotIn('shell', popen.call_args.kwargs)

    def test_cancelled_walk_and_saved_search_validation(self):
        (self.root / 'file').write_text('target')
        engine = search.SearchEngine(self.root, 'target', 'content', cancelled=lambda: True)
        self.assertFalse(list(engine.results()))
        valid = {'name': 'my search', 'root': str(self.root), 'query': 'target', 'mode': 'content'}
        self.assertEqual(search.validate_saved_search(valid), valid)
        for value in ({}, {'root': str(self.root), 'query': 'a', 'mode': 'unsafe'},
                      {'root': '../relative', 'query': 'a', 'mode': 'name'}):
            with self.assertRaises(ValueError):
                search.validate_saved_search(value)


if __name__ == '__main__':
    unittest.main()
