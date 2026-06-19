# Disk usage investigation

Date: 2026-06-19

This note records the disk pressure investigation that was triggered by:

```text
OSError: [Errno 28] No space left on device
```

## Current state

The root filesystem was almost full.

```text
Filesystem      Size  Used Avail Use% Mounted on
/dev/mmcblk0p2  113G  102G  7.1G  94% /
```

Before cleanup, `Kenny-bot/runtime` contained about 1.2GB of generated data.
The largest local Kenny-bot contributors were:

- `runtime/history/meeting_audio_debug`: about 1.1GB
- `runtime/logs/events.log`: about 84MB

The generated meeting audio debug files and the oversized event log were
cleaned up. `Kenny-bot` is now about 515MB.

## Main disk consumers outside Kenny-bot

The largest remaining disk usage is outside this repository.

```text
/home/kennypi/work                         36G
/home/kennypi/work/voicechat              18G
/home/kennypi/work/sandbox               9.3G
/home/kennypi/work/stackchan             4.6G
/home/kennypi/.espressif                 4.7G
/home/kennypi/.platformio                2.6G
/var/log/journal                         4.0G
```

`/var/log/journal` could not be vacuumed without elevated permissions.
Use this when appropriate:

```sh
sudo journalctl --vacuum-size=500M
```

## voicechat breakdown

`/home/kennypi/work/voicechat` is about 18GB.

Major contributors:

```text
/home/kennypi/work/voicechat/.runtime                         12G
/home/kennypi/work/voicechat/.git                            3.5G
/home/kennypi/work/voicechat/.moonshine-venv                 1.1G
/home/kennypi/work/voicechat/.venv                           747M
/home/kennypi/work/voicechat/.moonshine-pi-venv              526M
```

Inside `.runtime`:

```text
.runtime/faster_whisper_models                               4.1G
.runtime/models                                              3.2G
.runtime/vosk                                                1.8G
.runtime/voicechat.db                                        844M
.runtime/events.jsonl                                        609M
```

Large model files include:

```text
.runtime/models/ggml-medium.bin                              1.5G
.runtime/models/ggml-large-v3-turbo-q5_0.bin                 574M
.runtime/models/ggml-kotoba-whisper-v2.0-q5_0.bin            538M
.runtime/models/ggml-small.bin                               488M
.runtime/vosk/vosk-model-ja-0.22/rescore/G.carpa             1.1G
.runtime/faster_whisper_models/.../model.bin                 682M
```

There are also incomplete Hugging Face downloads:

```text
.runtime/faster_whisper_models/kotoba-v1.0/.cache/**/*.incomplete     256M
.runtime/faster_whisper_models/kotoba-v2.2/.cache/**/*.incomplete     1.0G
.runtime/faster_whisper_models/models--Systran--faster-whisper-small/**/*.incomplete 268M
.runtime/faster_whisper_models/models--RoachLin--kotoba-whisper-v2.2-faster/**/*.incomplete 2.1G
```

The `.git` directory is also large:

```text
.git/objects/pack/pack-b2ecb3f179c4c6afd77ab6a1f4acd77bc45d4972.pack  1.8G
.git/objects/pack/tmp_pack_D9lwJL                                      1.2G
```

The temporary pack file is likely left over from a Git operation, but verify
the repository before deleting it.

## Cleanup candidates

Likely safe cleanup candidates:

- Remove `voicechat/.runtime/faster_whisper_models/**/*.incomplete`
- Rotate or truncate `voicechat/.runtime/events.jsonl`
- Delete `voicechat/.git/objects/pack/tmp_pack_*` after `git fsck` passes
- Vacuum systemd journal with `sudo journalctl --vacuum-size=500M`

Cleanup candidates that may require re-download or rebuild:

- `voicechat/.runtime/models`
- `voicechat/.runtime/faster_whisper_models`
- `voicechat/.runtime/vosk`
- `voicechat/.venv`
- `voicechat/.moonshine-venv`
- `voicechat/.moonshine-pi-venv`
- `/home/kennypi/.espressif`
- `/home/kennypi/.platformio`

Avoid committing generated runtime outputs unless explicitly needed as tracked
fixture data.
