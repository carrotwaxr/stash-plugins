"""
Unit tests for blacklist module.
Run with: python -m pytest plugins/tagManager/tests/test_blacklist.py -v
"""

import pytest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blacklist import Blacklist


class TestBlacklistParsing:
    """Test blacklist pattern parsing."""

    def test_empty_blacklist(self):
        bl = Blacklist(None)
        assert bl.count == 0

    def test_empty_string(self):
        bl = Blacklist('')
        assert bl.count == 0

    def test_literal_patterns(self):
        bl = Blacklist('4K Available\nFull HD Available')
        assert bl.count == 2
        assert len(bl.literals) == 2
        assert len(bl.regexes) == 0

    def test_regex_patterns(self):
        bl = Blacklist('/^\\d+p$\n/Available$')
        assert bl.count == 2
        assert len(bl.literals) == 0
        assert len(bl.regexes) == 2

    def test_mixed_patterns(self):
        bl = Blacklist('4K Available\n/Available$')
        assert bl.count == 2
        assert len(bl.literals) == 1
        assert len(bl.regexes) == 1

    def test_invalid_regex_skipped(self):
        bl = Blacklist('/[invalid/')
        assert bl.count == 0  # Invalid regex should be skipped

    def test_blank_lines_ignored(self):
        bl = Blacklist('Pattern1\n\n\nPattern2\n  \n')
        assert bl.count == 2


class TestBlacklistMatching:
    """Test tag name matching."""

    def test_literal_exact_match(self):
        bl = Blacklist('4K Available')
        assert bl.is_blacklisted('4K Available') is True

    def test_literal_case_insensitive(self):
        bl = Blacklist('4K Available')
        assert bl.is_blacklisted('4k available') is True
        assert bl.is_blacklisted('4K AVAILABLE') is True

    def test_literal_no_partial_match(self):
        bl = Blacklist('4K')
        assert bl.is_blacklisted('4K Available') is False

    def test_regex_match(self):
        bl = Blacklist('/Available$')
        assert bl.is_blacklisted('4K Available') is True
        assert bl.is_blacklisted('Full HD Available') is True
        assert bl.is_blacklisted('Available Now') is False  # Not at end

    def test_regex_resolution_pattern(self):
        bl = Blacklist('/^\\d+p$')
        assert bl.is_blacklisted('1080p') is True
        assert bl.is_blacklisted('720p') is True
        assert bl.is_blacklisted('1080p Video') is False

    def test_non_blacklisted_tag(self):
        bl = Blacklist('4K Available')
        assert bl.is_blacklisted('Action') is False

    def test_empty_tag_name(self):
        bl = Blacklist('Pattern')
        assert bl.is_blacklisted('') is False
        assert bl.is_blacklisted(None) is False


class TestBlacklistFilter:
    """Test tag list filtering."""

    def test_filter_dict_tags(self):
        bl = Blacklist('4K Available')
        tags = [
            {'name': 'Action'},
            {'name': '4K Available'},
            {'name': 'Comedy'}
        ]
        filtered, hidden = bl.filter_tags(tags)
        assert len(filtered) == 2
        assert hidden == 1
        assert all(t['name'] != '4K Available' for t in filtered)

    def test_filter_empty_blacklist(self):
        bl = Blacklist('')
        tags = [{'name': 'Action'}, {'name': 'Comedy'}]
        filtered, hidden = bl.filter_tags(tags)
        assert len(filtered) == 2
        assert hidden == 0

    def test_filter_all_blacklisted(self):
        bl = Blacklist('Action\nComedy')
        tags = [{'name': 'Action'}, {'name': 'Comedy'}]
        filtered, hidden = bl.filter_tags(tags)
        assert len(filtered) == 0
        assert hidden == 2
