"""Do not open a shared-token HTTP service before component ingress exists."""

from unittest.mock import patch

import pytest

from tend_mcp.cli import main


@pytest.mark.parametrize("args", [["http"], ["http", "--host", "0.0.0.0"], ["http", "--port", "9999"]])
def test_http_denied_before_credentials_or_network(args, capsys):
    with patch("tend_mcp.cli._load_config") as config:
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2
        config.assert_not_called()
    assert "HTTP serving is disabled" in capsys.readouterr().err


def test_version_needs_no_credentials(capsys):
    with patch("tend_mcp.cli._load_config") as config:
        with pytest.raises(SystemExit) as error:
            main(["--version"])
        assert error.value.code == 0
        config.assert_not_called()
    assert capsys.readouterr().out.strip()
