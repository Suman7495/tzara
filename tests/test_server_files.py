import json
import os
import stat
import sys

import pytest

from tzara.config import ServerSettings
from tzara.paths import DataLocation
from tzara.server.files import NameStore, ServerInfo, ServerInfoFile, ServerPaths, TokenStore

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestServerPaths:
    def test_uses_the_configured_directory(self, tmp_path):
        paths = ServerPaths.from_settings(ServerSettings(data_dir=tmp_path))
        assert paths.directory == tmp_path

    def test_defaults_to_the_platform_directory(self):
        paths = ServerPaths.from_settings(ServerSettings())
        assert paths.directory == DataLocation.default_dir()

    def test_names_the_files(self, tmp_path):
        paths = ServerPaths(tmp_path)
        assert paths.token_file == tmp_path / "token"
        assert paths.info_file == tmp_path / "server.json"
        assert paths.name_file == tmp_path / "name"


class TestTokenStore:
    def test_creates_a_token_and_the_directory(self, tmp_path):
        path = tmp_path / "nested" / "token"
        token = TokenStore(path).load_or_create()
        assert token
        assert path.read_text(encoding="utf-8") == token

    def test_tokens_are_long_and_different_each_time(self, tmp_path):
        first = TokenStore(tmp_path / "a").load_or_create()
        second = TokenStore(tmp_path / "b").load_or_create()
        assert len(first) >= 40
        assert first != second

    def test_keeps_the_existing_token_so_clients_stay_connected(self, tmp_path):
        store = TokenStore(tmp_path / "token")
        assert store.load_or_create() == store.load_or_create()

    def test_surrounding_whitespace_in_the_file_is_ignored(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("  secret-value \n", encoding="utf-8")
        assert TokenStore(path).load_or_create() == "secret-value"

    def test_an_empty_file_is_replaced(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("", encoding="utf-8")
        assert TokenStore(path).load_or_create()
        assert path.read_text(encoding="utf-8")

    @POSIX_ONLY
    def test_only_the_owner_can_read_the_token(self, tmp_path):
        path = tmp_path / "data" / "token"
        TokenStore(path).load_or_create()
        assert mode(path) == 0o600
        assert mode(path.parent) == 0o700

    @POSIX_ONLY
    def test_a_token_readable_by_others_is_locked_down(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("secret", encoding="utf-8")
        path.chmod(0o644)
        TokenStore(path).load_or_create()
        assert mode(path) == 0o600

    @POSIX_ONLY
    def test_the_file_is_never_readable_by_others_even_briefly(self, tmp_path, monkeypatch):
        """Created private from the start, not written and then restricted."""
        seen = []
        real_open = os.open

        def spying_open(path, flags, file_mode=0o777, **kwargs):
            seen.append(file_mode)
            return real_open(path, flags, file_mode, **kwargs)

        monkeypatch.setattr(os, "open", spying_open)
        TokenStore(tmp_path / "token").load_or_create()
        assert seen == [0o600]


class TestServerInfo:
    def test_url(self):
        assert ServerInfo("127.0.0.1", 8927, 1).url == "http://127.0.0.1:8927"


class TestServerInfoFile:
    def test_round_trip(self, tmp_path):
        record = ServerInfoFile(tmp_path / "dir" / "server.json")
        info = ServerInfo("127.0.0.1", 8927, 4242)
        record.write(info)
        assert record.read() == info

    def test_missing_file_reads_as_none(self, tmp_path):
        assert ServerInfoFile(tmp_path / "server.json").read() is None

    @pytest.mark.parametrize(
        "content", ["not json", "[]", "{}", '{"host": "h", "port": "x", "pid": 1}']
    )
    def test_unusable_content_reads_as_none(self, tmp_path, content):
        path = tmp_path / "server.json"
        path.write_text(content, encoding="utf-8")
        assert ServerInfoFile(path).read() is None

    def test_write_replaces_an_earlier_record(self, tmp_path):
        record = ServerInfoFile(tmp_path / "server.json")
        record.write(ServerInfo("127.0.0.1", 1, 1))
        record.write(ServerInfo("127.0.0.1", 2, 2))
        assert record.read() == ServerInfo("127.0.0.1", 2, 2)

    def test_remove_deletes_the_record_of_the_given_process(self, tmp_path):
        record = ServerInfoFile(tmp_path / "server.json")
        record.write(ServerInfo("127.0.0.1", 8927, 100))
        record.remove_if_owned_by(100)
        assert record.read() is None

    def test_remove_leaves_another_servers_record_alone(self, tmp_path):
        """A server that failed to start must not erase the one already running."""
        record = ServerInfoFile(tmp_path / "server.json")
        record.write(ServerInfo("127.0.0.1", 8927, 100))
        record.remove_if_owned_by(999)
        assert record.read() == ServerInfo("127.0.0.1", 8927, 100)

    def test_remove_with_no_record_is_harmless(self, tmp_path):
        ServerInfoFile(tmp_path / "server.json").remove_if_owned_by(1)

    def test_the_file_is_plain_json_for_other_programs_to_read(self, tmp_path):
        """The Swift app reads this file."""
        path = tmp_path / "server.json"
        ServerInfoFile(path).write(ServerInfo("127.0.0.1", 8927, 7))
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "host": "127.0.0.1",
            "port": 8927,
            "pid": 7,
        }


class TestNameStore:
    def test_round_trip(self, tmp_path):
        store = NameStore(tmp_path / "data" / "name")
        store.save("Jarvis")
        assert store.load() == "Jarvis"

    def test_nothing_saved_reads_as_none(self, tmp_path):
        assert NameStore(tmp_path / "name").load() is None

    def test_saving_again_replaces_the_name(self, tmp_path):
        store = NameStore(tmp_path / "name")
        store.save("Jarvis")
        store.save("Friday")
        assert store.load() == "Friday"

    @pytest.mark.parametrize("content", ["", "   \n", "two\nlines"])
    def test_an_unusable_file_reads_as_none(self, tmp_path, content):
        path = tmp_path / "name"
        path.write_text(content, encoding="utf-8")
        assert NameStore(path).load() is None

    def test_a_file_that_is_not_text_reads_as_none(self, tmp_path):
        path = tmp_path / "name"
        path.write_bytes(b"\xff\xfe\x00bad")
        assert NameStore(path).load() is None

    def test_surrounding_whitespace_is_ignored(self, tmp_path):
        path = tmp_path / "name"
        path.write_text("  Jarvis \n", encoding="utf-8")
        assert NameStore(path).load() == "Jarvis"
