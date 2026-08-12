import crypto_quant_lab


def test_package_importable():
    assert crypto_quant_lab is not None


def test_version():
    assert crypto_quant_lab.__version__ == "0.1.0"
