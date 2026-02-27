"""Tests for test mapper module."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from pr_test_oracle.test_mapper import (
    _CONFIG_FILES,
    _SOURCE_EXTENSIONS,
    TestMapper,
    _is_test_file,
    _strip_source_prefix,
    _strip_test_prefix,
)


@pytest.fixture
def temp_repo() -> Generator[Path, None, None]:
    """Create a temporary repository structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)

        # Create source files
        (repo / "src" / "myapp").mkdir(parents=True)
        (repo / "src" / "myapp" / "__init__.py").touch()
        (repo / "src" / "myapp" / "auth.py").write_text("def login(): pass")
        (repo / "src" / "myapp" / "config.py").write_text("DEBUG = True")
        (repo / "src" / "myapp" / "utils.py").write_text("def helper(): pass")

        # Create test files
        (repo / "tests").mkdir()
        (repo / "tests" / "__init__.py").touch()
        (repo / "tests" / "conftest.py").write_text("import pytest")
        (repo / "tests" / "test_auth.py").write_text("def test_login(): pass")
        (repo / "tests" / "test_config.py").write_text("def test_debug(): pass")
        (repo / "tests" / "test_integration.py").write_text("def test_full(): pass")

        yield repo


class TestDiscoverTestFiles:
    """Tests for test file discovery."""

    def test_discovers_test_files(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        files = mapper.discover_test_files()
        assert "tests/test_auth.py" in files
        assert "tests/test_config.py" in files

    def test_excludes_init_and_conftest(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        files = mapper.discover_test_files()
        assert "tests/__init__.py" not in files
        assert "tests/conftest.py" not in files

    def test_caches_results(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        files1 = mapper.discover_test_files()
        files2 = mapper.discover_test_files()
        assert files1 is files2  # Same object, not just equal

    def test_custom_patterns(self, temp_repo: Path) -> None:
        # Create a non-standard test file
        (temp_repo / "checks").mkdir()
        (temp_repo / "checks" / "check_auth.py").write_text("def check(): pass")

        mapper = TestMapper(str(temp_repo), test_patterns=["checks/**/*.py"])
        files = mapper.discover_test_files()
        assert "checks/check_auth.py" in files
        assert "tests/test_auth.py" not in files


class TestMapChangedFiles:
    """Tests for file-to-test mapping."""

    def test_maps_by_naming_convention(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(["src/myapp/auth.py"])
        assert len(mappings) == 1
        assert "tests/test_auth.py" in mappings[0].candidate_tests

    def test_config_file_maps_to_all_tests(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(["pyproject.toml"])
        assert len(mappings) == 1
        # Should map to ALL discovered test files
        assert len(mappings[0].candidate_tests) >= 3
        assert mappings[0].mapping_reason == "Config file change affects all tests"

    def test_test_file_maps_to_self(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(["tests/test_auth.py"])
        assert len(mappings) == 1
        assert "tests/test_auth.py" in mappings[0].candidate_tests
        assert mappings[0].mapping_reason == "Changed file is itself a test"

    def test_non_python_non_config_mapped(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(["README.md"])
        assert len(mappings) == 1
        assert mappings[0].candidate_tests == []
        assert "Non-source file" in mappings[0].mapping_reason

    def test_non_python_source_file_runs_candidate_mapping(
        self, temp_repo: Path
    ) -> None:
        """Non-Python source files with recognized extensions should attempt candidate mapping."""
        # Create a JS source file and a matching test file
        (temp_repo / "src" / "myapp" / "auth.js").write_text(
            "export function login() {}"
        )
        (temp_repo / "tests" / "test_auth.js").write_text("test('login', () => {})")

        mapper = TestMapper(
            str(temp_repo),
            test_patterns=["tests/**/*.py", "tests/**/*.js"],
        )
        mappings = mapper.map_changed_files(["src/myapp/auth.js"])
        assert len(mappings) == 1
        assert "tests/test_auth.js" in mappings[0].candidate_tests
        assert "Naming convention" in mappings[0].mapping_reason

    def test_non_python_source_file_no_candidates(self, temp_repo: Path) -> None:
        """Non-Python source files with no matching tests get an appropriate reason."""
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(["src/myapp/widget.ts"])
        assert len(mappings) == 1
        assert mappings[0].candidate_tests == []
        assert "No direct mapping found" in mappings[0].mapping_reason

    def test_no_candidates_found(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(["src/myapp/new_module.py"])
        assert len(mappings) == 1
        assert mappings[0].candidate_tests == []
        assert "AI will determine" in mappings[0].mapping_reason

    def test_multiple_changed_files(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        mappings = mapper.map_changed_files(
            ["src/myapp/auth.py", "src/myapp/config.py"]
        )
        assert len(mappings) == 2


class TestGetTestFileContents:
    """Tests for reading test file contents."""

    def test_reads_existing_files(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        contents = mapper.get_test_file_contents(["tests/test_auth.py"])
        assert "tests/test_auth.py" in contents
        assert "def test_login" in contents["tests/test_auth.py"]

    def test_skips_missing_files(self, temp_repo: Path) -> None:
        mapper = TestMapper(str(temp_repo))
        contents = mapper.get_test_file_contents(["tests/test_nonexistent.py"])
        assert contents == {}


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_is_test_file_by_prefix(self) -> None:
        assert _is_test_file(Path("test_foo.py")) is True

    def test_is_test_file_by_suffix(self) -> None:
        assert _is_test_file(Path("foo_test.py")) is True

    def test_is_test_file_in_tests_dir(self) -> None:
        # Directory location alone doesn't make a file a test
        # File must follow naming conventions (test_* or *_test pattern, etc.)
        assert _is_test_file(Path("tests/helpers.py")) is False
        # But test_helpers.py in tests/ is still a test by naming convention
        assert _is_test_file(Path("tests/test_helpers.py")) is True

    def test_is_not_test_file(self) -> None:
        assert _is_test_file(Path("src/foo.py")) is False

    def test_strip_source_prefix(self) -> None:
        parts = ("src", "myapp", "auth.py")
        result = _strip_source_prefix(parts)
        assert result == ["auth"]

    def test_strip_test_prefix(self) -> None:
        parts = ("tests", "test_auth.py")
        result = _strip_test_prefix(parts)
        assert result == ["test_auth"]

    def test_config_files_includes_pyproject(self) -> None:
        assert "pyproject.toml" in _CONFIG_FILES
        assert "setup.py" in _CONFIG_FILES
        assert "conftest.py" in _CONFIG_FILES

    def test_source_extensions_includes_common_languages(self) -> None:
        for ext in (".py", ".js", ".ts", ".go", ".java", ".rb", ".rs"):
            assert ext in _SOURCE_EXTENSIONS
