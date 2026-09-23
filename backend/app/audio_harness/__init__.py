"""Offline audio-evaluation seams that live inside the app package.

Distinct from ``benchmark/audio_harness`` (the real-audio routing harness):
modules here are importable from ``backend`` without the repo root on
``sys.path`` and never touch a provider by default.
"""
