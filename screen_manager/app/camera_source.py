"""What a camera gives (docs/CAMERA.md, "Live on the P4 boards"; docs/PICTURE_ARCHITECTURE.md, section 4): its newest
picture and its sound, for every screen that watches it live.

Most of the work a live camera costs lands on Home Assistant's host, often a Raspberry Pi, so each kind of camera takes
the cheapest road there is (measured 2026-10-10 on an EZVIZ, 1080p main stream and 512 x 288 substream):

- A camera with a stream (an RTSP camera through generic, ONVIF, Reolink, UniFi and others) comes through Home
  Assistant's own WebRTC (its built-in go2rtc relays the camera's H.264 as it is): 0.12 s behind the camera, frame by
  frame, where Home Assistant's LL-HLS was 0.6 to 1.2 s behind and came in bursts of a second. The H.264 is decoded
  here (aiortc, PyAV), the only place that can: the P4 has an H.264 encoder, no decoder. Decoding costs what the
  camera's resolution costs, so a camera's low-resolution stream (its substream entity) is the light one. Its JPEG is
  made straight from the decoder's YUV with ffmpeg's encoder, at the camera's own size (1 ms for 512 x 288 on a PC,
  where Pillow from RGB took 3), or scaled down to the room where the camera is larger (7 ms for 1080p to 720p, Pillow
  19). Until WebRTC's first frame (it waits for the camera's next key frame, seconds on some cameras) the screen gets
  the camera's still, each such part marked `X-Picture: still`: the screen turns a loader over it until the first part
  without the mark (live_view.h, "Until it is live"). A screen that knows no mark shows it as any picture.
- Any other camera (a snapshot camera, an ESPHome camera, a picture an integration draws) gives its snapshot, asked
  again as soon as the last came. A JPEG the P4 decodes as it is (baseline, at most MAX_SIDE a side) goes out untouched,
  which costs this app nothing; anything else is made into one.

One source per camera, shared by every screen that watches it; it stops a few seconds after the last one stops. What a
screen gets of a frame is live_pictures', how it travels live_stream's.
"""
import asyncio
import logging
import time

import catalogue

LOG = logging.getLogger(__name__)

LINGER_SECONDS = 5        # a camera nobody watches any more stops after this (a screen that asks again at once finds it)
SNAPSHOT_FPS = 15         # a camera without a stream is asked no more often than this
WEBRTC_FIRST_SECONDS = 20  # a WebRTC session without a frame by then gives way to the snapshots
LAG_REPORT_SECONDS = 60   # how often a camera's WebRTC pictures say how far behind the camera they are (Lag)
STILL_SECONDS = 2          # the camera's still, again this often, until WebRTC's first frame (the screen's
                           # live_view::QUIET_MS closes a stream silent for 6 s)
SOUND_RATE = 16000        # a full view's sound: 16-bit mono at this rate (the board's resampler makes it its bus's)
# A camera with a stream of its own (CameraEntityFeature.STREAM, by its name in Home Assistant's source).
STREAM_FEATURE = catalogue.bits('camera', 'STREAM')


class Frame:
    """A camera's picture; `still` when it is the camera's still from before its stream came."""
    __slots__ = ('number', 'jpeg', 'video', 'at', 'still')

    def __init__(self, number, jpeg=None, video=None, still=False):
        self.number, self.jpeg, self.video, self.at, self.still = number, jpeg, video, time.monotonic(), still


class Lag:
    """How far a camera's pictures come behind the camera: the time since the first picture on this host's clock less
    the time since it on the camera's (a frame's `time`, which aiortc starts at 0 with the first frame). A frame that
    came ahead of the first one's pace (the camera's timestamps jumped, as after a new key frame) starts the count again.
    Measured first, before anything acts on it: whether the pictures fall behind a screen over hours (2026-10-25, a
    D1001 with two live tiles, by eye), and where (`waiting`: decoded frames aiortc holds for this app)."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.origin, self.seen, self.reported = clock, None, [], clock()

    def feed(self, at):
        """The seconds behind the camera of a frame of camera time `at`."""
        now = self.clock()
        if self.origin is None:
            self.origin = (now, at)
        behind = (now - self.origin[0]) - (at - self.origin[1])
        if behind < 0:
            self.origin, behind = (now, at), 0.0
        self.seen.append(behind)
        return behind

    def report(self):
        """(average, most, pictures) since the last report once LAG_REPORT_SECONDS went by, else None."""
        if not self.seen or self.clock() - self.reported < LAG_REPORT_SECONDS:
            return None
        seen, self.seen, self.reported = self.seen, [], self.clock()
        return sum(seen) / len(seen), max(seen), len(seen)


class Source:
    """The newest picture of one camera, for every screen that watches it."""

    def __init__(self, feed, entity, streams):
        self.feed, self.entity, self.streams = feed, entity, streams
        self.frame, self.number, self.viewers = None, 0, 0
        self.changed = asyncio.Condition()
        self.task, self.idle_since = None, None
        self.route = 'snapshot'
        # When the way to the first picture went by, in seconds after the source started (the log of the first picture).
        self.started, self.marks = time.monotonic(), {}
        # The screens that listen to the camera's sound (a queue each), its WebRTC session of its own, and whether the
        # pictures' session brings it along (a screen with a speaker watches: `wants_sound`).
        self.listeners, self.sound, self.quiet_since = set(), None, None
        self.wants_sound = self.sound_along = False
        self.resampler = None
        self.sound_dropped = 0  # parts of sound a listener's full queue lost since the last report (Lag)

    def mark(self, what):
        self.marks.setdefault(what, time.monotonic() - self.started)

    async def publish(self, jpeg=None, video=None, still=False):
        self.number += 1
        self.frame = Frame(self.number, jpeg, video, still)
        async with self.changed:
            self.changed.notify_all()

    async def next(self, after, timeout):
        """The newest picture after `after`, or None when the camera gave none for `timeout` seconds."""
        try:
            async with self.changed:
                await asyncio.wait_for(self.changed.wait_for(lambda: self.frame and self.frame.number > after), timeout)
        except asyncio.TimeoutError:
            return None
        return self.frame

    def start(self):
        if self.task is None or self.task.done():
            self.started, self.marks = time.monotonic(), {}
            self.task = asyncio.create_task(self.run())

    async def run(self):
        """Pictures while anyone watches: WebRTC for a camera with a stream, its still until the first frame and if
        WebRTC fails; snapshots for any other camera."""
        try:
            if self.streams and await self.webrtc():
                return
            await self.snapshots()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a camera that fails ends its streams; screens ask again
            LOG.warning('Live %s ended: %s', self.entity, error)

    def watched(self):
        return self.viewers > 0 or (self.idle_since and time.monotonic() - self.idle_since < LINGER_SECONDS)

    async def snapshots(self):
        self.route = 'snapshot'
        failures = 0
        while self.watched():
            started = time.monotonic()
            try:
                raw = await self.feed.fetch(self.entity)
                failures = 0
                if raw:
                    await self.publish(jpeg=raw)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - tried again, a little later each time
                failures += 1
                if failures == 1:
                    LOG.info('Live %s: no snapshot (%s)', self.entity, type(error).__name__)
                await asyncio.sleep(min(10, failures))
            await asyncio.sleep(max(0.0, 1 / SNAPSHOT_FPS - (time.monotonic() - started)))

    async def webrtc(self):
        """Home Assistant's WebRTC of the camera's pictures until nobody watches; False when it gave no frame (no WebRTC
        here, no aiortc in this install, a camera go2rtc cannot relay): the snapshots take over. The camera's stills go
        first: WebRTC waits for the camera's next key frame."""
        first = asyncio.Event()
        still = asyncio.create_task(self.stills_until(first))

        async def take(kind, frame):
            if kind == 'audio':
                self.heard(frame)
                return
            self.mark('first WebRTC frame')
            first.set()
            await self.publish(video=frame)
        try:
            self.route = 'webrtc'
            # The sound along in the same session when a screen with a speaker watches: the full view has it at once.
            self.sound_along = self.wants_sound
            return await self.rtc(('video', 'audio') if self.sound_along else ('video',), take, self.watched)
        finally:
            self.sound_along = False
            still.cancel()

    async def rtc(self, kinds, take, keep):
        """One WebRTC session of Home Assistant's for the camera's `kinds` ('video', 'audio'): `take(kind, frame)` for
        each frame while `keep()`. False when no frame of the first kind came within WEBRTC_FIRST_SECONDS or the session
        could not start."""
        try:
            from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
            from aiortc.sdp import candidate_from_sdp
        except ImportError:
            return False
        kind = kinds[0]
        first = asyncio.Event()
        # No STUN server: this app and Home Assistant share a network (the Supervisor's, or the LAN), where the host's
        # own addresses reach each other. aiortc asks Google's STUN server by default and the offer waited for its answer,
        # about five seconds on a LAN without one, before every live camera started (2026-10-20, a full view and a tile).
        connection = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        for each in kinds:
            connection.addTransceiver(each, direction='recvonly')
        pulls = []

        @connection.on('track')
        def on_track(track):
            if track.kind not in kinds:
                return

            async def pull():
                lag = Lag()
                while True:
                    frame = await track.recv()
                    if track.kind == kind:
                        first.set()
                    # The sound measured as the pictures are: both behind together is this app (its loop) slow, the
                    # pictures alone behind is the host's H.264 decoder. The sound is never skipped.
                    if frame.time is not None:
                        lag.feed(frame.time)
                        told = lag.report()
                        if told:
                            # aiortc's own queue of decoded frames, read for the log alone (no public way to it).
                            waiting = getattr(track, '_queue', None)
                            dropped = ''
                            if track.kind == 'audio':
                                dropped = f'; {self.sound_dropped} parts dropped for a screen that fell behind'
                                self.sound_dropped = 0
                            LOG.info('Live %s %s: %.2f s behind the camera on average, %.2f s at most, over %d frames; '
                                     '%s decoded waiting%s', self.entity, 'video' if track.kind == 'video' else 'sound',
                                     *told, waiting.qsize() if waiting is not None else '?', dropped)
                    await take(track.kind, frame)
            pulls.append(asyncio.create_task(pull()))

        try:
            await connection.setLocalDescription(await connection.createOffer())
            if kind == 'video':
                self.mark('offer')
            async with self.feed.websocket() as ws:
                await ws.send_json({'id': 1, 'type': 'camera/webrtc/offer', 'entity_id': self.entity,
                                    'offer': connection.localDescription.sdp})

                async def signals():
                    async for message in ws:
                        data = message.json()
                        if data.get('type') == 'result' and not data.get('success'):
                            raise ConnectionError((data.get('error') or {}).get('message') or 'refused')
                        event = data.get('event') or {}
                        if event.get('type') == 'answer':
                            if kind == 'video':
                                self.mark('answer')
                            await connection.setRemoteDescription(RTCSessionDescription(event['answer'], 'answer'))
                        elif event.get('type') == 'candidate' and (event.get('candidate') or {}).get('candidate'):
                            found = event['candidate']
                            candidate = candidate_from_sdp(found['candidate'].split(':', 1)[1])
                            candidate.sdpMid, candidate.sdpMLineIndex = found.get('sdpMid'), found.get('sdpMLineIndex')
                            await connection.addIceCandidate(candidate)
                        elif event.get('type') == 'error':
                            raise ConnectionError(event.get('message') or 'error')

                listening = asyncio.create_task(signals())
                arrived = asyncio.create_task(first.wait())
                try:
                    # The first frame, or the end of the session before it: Home Assistant refuses the offer of a
                    # camera without WebRTC at once (a camera with HLS alone), and the snapshots take over then, not
                    # after WEBRTC_FIRST_SECONDS.
                    await asyncio.wait((arrived, listening), timeout=WEBRTC_FIRST_SECONDS,
                                       return_when=asyncio.FIRST_COMPLETED)
                    if not first.is_set():
                        why = ended(listening) or f'nothing in {WEBRTC_FIRST_SECONDS} s'
                        LOG.info('Live %s: no WebRTC %s (%s)', self.entity, kind, why)
                        return False
                    while keep() and not listening.done() and all(not p.done() for p in pulls):
                        await asyncio.sleep(1)
                    why = ended(listening)
                    if why:
                        LOG.info('Live %s: WebRTC %s ended (%s)', self.entity, kind, why)
                    return True
                finally:
                    arrived.cancel()
                    listening.cancel()
        except (ConnectionError, OSError, asyncio.TimeoutError) as error:
            LOG.info('Live %s: no WebRTC %s (%s)', self.entity, kind, error)
            return False
        finally:
            for task in pulls:
                ended(task)
                task.cancel()
            await connection.close()

    # ---- The camera's sound, for a full view with a speaker: 16-bit mono at SOUND_RATE, in parts as it comes ----
    def listening(self):
        return bool(self.listeners) or (self.quiet_since and time.monotonic() - self.quiet_since < LINGER_SECONDS)

    def listen(self, queue):
        self.listeners.add(queue)
        self.quiet_since = None
        # Sound that comes along with the pictures already needs no session of its own.
        if self.streams and not self.sound_along and (self.sound is None or self.sound.done()):
            self.sound = asyncio.create_task(self.hear())

    def heard(self, frame):
        """A frame of the camera's sound, made 16-bit mono at SOUND_RATE and handed to every listener; a listener that
        falls behind loses its oldest part. Nobody listening: dropped."""
        if not self.listeners:
            return
        if self.resampler is None:
            import av
            self.resampler = av.AudioResampler(format='s16', layout='mono', rate=SOUND_RATE)
        for out in self.resampler.resample(frame):
            data = bytes(out.planes[0])[:out.samples * 2]
            for queue in list(self.listeners):
                if queue.full():
                    queue.get_nowait()
                    self.sound_dropped += 1
                queue.put_nowait(data)

    def unlisten(self, queue):
        self.listeners.discard(queue)
        if not self.listeners:
            self.quiet_since = time.monotonic()

    async def hear(self):
        """The camera's sound through Home Assistant's WebRTC (a session of its own, only while someone listens),
        made 16-bit mono at SOUND_RATE and handed to every listener; a listener that falls behind loses its oldest."""
        async def take(kind, frame):
            self.heard(frame)
        try:
            await self.rtc(('audio',), take, self.listening)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - no sound, the pictures go on
            LOG.info('Live %s: no sound (%s)', self.entity, error)

    async def stills_until(self, first):
        """The camera's still, again every STILL_SECONDS, until WebRTC's first frame. A single still left the screen
        without a byte while a camera took longer for its key frame than the screen waits (live_view::QUIET_MS): it
        closed the stream, went black and asked again, over and over."""
        while not first.is_set():
            started = time.monotonic()
            try:
                raw = await self.feed.fetch(self.entity)
                self.mark('still')
                if raw and not first.is_set():
                    await self.publish(jpeg=raw, still=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - only the pictures before the stream's; the next is tried
                pass
            await asyncio.sleep(max(0.0, STILL_SECONDS - (time.monotonic() - started)))


def ended(task):
    """What ended a finished task (its exception, read so asyncio never logs it as never retrieved); None while it runs,
    when it was cancelled or ended without one."""
    if not task.done() or task.cancelled():
        return None
    return task.exception()
