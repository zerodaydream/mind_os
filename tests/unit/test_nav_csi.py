from cli.term import _parse_nav_csi


def test_nav_csi_arrows():
    assert _parse_nav_csi("A") == "up"
    assert _parse_nav_csi("B") == "down"
    assert _parse_nav_csi("C") == "right"
    assert _parse_nav_csi("D") == "left"


def test_nav_csi_page_keys():
    assert _parse_nav_csi("5~") == "ppage"
    assert _parse_nav_csi("6~") == "npage"
