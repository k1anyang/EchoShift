"""EchoShift -- audio/video to MP3 converter with QQ Music (QMC) decryption.

The package is layered so that the conversion core stays independent of the
GUI:

``echoshift.qmc``
    QQ Music encrypted container decoding (QMC1 / QMC2).
``echoshift.core``
    ffmpeg invocation, media probing, job pipeline, naming, verification.
``echoshift.gui``
    Tkinter desktop front end.
``echoshift.cli``
    Command line front end offering the same capabilities headlessly.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
