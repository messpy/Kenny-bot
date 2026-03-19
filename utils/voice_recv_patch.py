from __future__ import annotations

import logging


logger = logging.getLogger(__name__)
_PATCHED = False


def apply_voice_recv_resilience_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        from discord.opus import OpusError
        from discord.ext.voice_recv.opus import PacketDecoder
        from discord.ext.voice_recv.router import PacketRouter
    except Exception:
        logger.debug("voice_recv resilience patch skipped; dependency not available")
        return

    original_decode_packet = PacketDecoder._decode_packet
    original_do_run = PacketRouter._do_run

    def patched_decode_packet(self, packet):
        try:
            return original_decode_packet(self, packet)
        except OpusError as exc:
            logger.warning("Dropped corrupted voice packet for ssrc=%s: %s", getattr(self, "ssrc", "unknown"), exc)
            self.reset()
            raise

    def patched_do_run(self):
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in list(self.waiter.items):
                    try:
                        data = decoder.pop_data()
                    except OpusError:
                        continue
                    if data is not None:
                        self.sink.write(data.source, data)

    PacketDecoder._decode_packet = patched_decode_packet
    PacketRouter._do_run = patched_do_run
    _PATCHED = True
    logger.info("Applied voice_recv resilience patch")
