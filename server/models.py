"""
Download required models
"""

from speechmatics.voice import SileroVAD, SmartTurnDetector


def load_models():
    SileroVAD.download_model()
    SmartTurnDetector.download_model()


if __name__ == "__main__":
    load_models()
