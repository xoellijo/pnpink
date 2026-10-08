from inkex_compat import etree


def test_etree_is_available_independently_of_inkex_export():
    root = etree.Element("root")

    assert etree.tostring(root) == b"<root/>"
