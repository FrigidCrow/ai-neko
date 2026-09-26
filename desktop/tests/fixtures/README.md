# Synthetic audio fixture

`synthetic-tone.mp3` is an original 4-second, 220 Hz quiet tone generated for playback and cancellation tests. It contains no recorded person, game audio or third-party media.

Reproduction command (FFmpeg):

```sh
ffmpeg -hide_banner -loglevel error -f lavfi -i 'sine=frequency=220:sample_rate=16000:duration=4' -af 'volume=0.02' -ac 1 -b:a 24k -y desktop/tests/fixtures/synthetic-tone.mp3
```

The fixture is checked in so running the acceptance harness does not require FFmpeg. Chromium's fake media device supplies test microphone input; the harness never opens the user's real microphone or invokes an actual desktop source capture. The test-only source is a synthetic BrowserWindow in its own nonpersistent session.
