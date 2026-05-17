from maix import app, display, image, time, touchscreen
import base64
import os
import socket
import ssl
import struct
import _thread


APP_TITLE = "QO-100 WB Monitor"

BATC_HOST = "eshail.batc.org.uk"
BATC_FALLBACK_IP = "185.83.169.27"
BATC_PORT = 443
BATC_PATH = "/wb/fft"
BATC_PROTOCOL = "fft"
CAFILE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/usr/lib/ssl/cert.pem",
    "/etc/ssl/cert.pem",
)

FFT_START_MHZ = 10490.5
FFT_SPAN_MHZ = 9.0
FFT_REL_START = 490.5

SAMPLE_FLOOR = 8192
SIGNAL_THRESHOLD = 16000
NOISE_LEVEL = 11000

SCREENSHOT_PATH = os.environ.get("QO100_WB_SCREENSHOT", "")
try:
    SCREENSHOT_AFTER_MS = int(float(os.environ.get("QO100_WB_SCREENSHOT_AFTER", "10")) * 1000)
except Exception:
    SCREENSHOT_AFTER_MS = 10000


try:
    image.load_font("font", "/maixapp/share/font/SourceHanSansCN-Regular.otf", size=16)
    image.set_default_font("font")
except Exception as e:
    print("font load skipped: {}".format(e))


def color(r, g, b):
    return image.Color.from_rgb(r, g, b)


COL_BG = image.COLOR_BLACK
COL_PANEL = color(12, 18, 24)
COL_GRID = color(70, 76, 84)
COL_MINOR = color(38, 44, 50)
COL_TEXT = color(232, 238, 242)
COL_MUTED = color(142, 152, 162)
COL_GREEN = color(0, 224, 112)
COL_YELLOW = color(255, 220, 40)
COL_BLUE = color(55, 110, 230)
COL_RED = color(240, 70, 70)
COL_BAND = color(34, 82, 158)


def now_ms():
    return time.ticks_ms()


class BatcFftClient:
    def __init__(self):
        self.lock = _thread.allocate_lock()
        self.latest = None
        self.frames = 0
        self.last_frame_ms = 0
        self.status = "starting"
        self.error = ""
        self.running = True
        self.sock = None

    def start(self):
        _thread.start_new_thread(self._run, ())

    def stop(self):
        self.running = False
        self._close()

    def snapshot(self):
        self.lock.acquire()
        try:
            return self.latest, self.frames, self.last_frame_ms, self.status, self.error
        finally:
            self.lock.release()

    def _set_status(self, status, error=""):
        self.lock.acquire()
        try:
            self.status = status
            self.error = error
        finally:
            self.lock.release()

    def _set_frame(self, frame):
        self.lock.acquire()
        try:
            self.latest = frame
            self.frames += 1
            self.last_frame_ms = now_ms()
            self.status = "live"
            self.error = ""
        finally:
            self.lock.release()

    def _close(self):
        s = self.sock
        self.sock = None
        if s:
            try:
                s.close()
            except Exception:
                pass

    def _run(self):
        while self.running:
            try:
                self._set_status("connecting")
                self._connect_and_read()
            except Exception as e:
                self._set_status("reconnecting", str(e))
                print("BATC websocket reconnect: {}".format(e))
            self._close()
            for _ in range(20):
                if not self.running:
                    break
                time.sleep_ms(100)

    def _connect_socket(self):
        last_error = None
        for target in (BATC_HOST, BATC_FALLBACK_IP):
            try:
                raw = socket.create_connection((target, BATC_PORT), timeout=10)
                ctx = self._ssl_context()
                return ctx.wrap_socket(raw, server_hostname=BATC_HOST)
            except Exception as e:
                last_error = e
                print("connect {} failed: {}".format(target, e))
        raise last_error

    def _ssl_context(self):
        for cafile in CAFILE_CANDIDATES:
            try:
                if os.path.exists(cafile):
                    return ssl.create_default_context(cafile=cafile)
            except Exception as e:
                print("CA bundle {} skipped: {}".format(cafile, e))
        return ssl.create_default_context()

    def _connect_and_read(self):
        s = self._connect_socket()
        s.settimeout(12)
        self.sock = s

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: {}\r\n"
            "Origin: https://{}\r\n"
            "User-Agent: qo100-wb-mon-maixcam/1.0\r\n"
            "\r\n"
        ).format(BATC_PATH, BATC_HOST, key, BATC_PROTOCOL, BATC_HOST)
        s.sendall(request.encode("ascii"))

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError("websocket closed during handshake")
            header += chunk
            if len(header) > 16384:
                raise RuntimeError("websocket handshake too large")

        head, rest = header.split(b"\r\n\r\n", 1)
        status_line = head.split(b"\r\n", 1)[0].decode("latin1")
        if "101" not in status_line:
            raise RuntimeError(status_line)

        self._set_status("waiting")
        buf = rest
        while self.running:
            opcode, payload, buf = self._read_frame(s, buf)
            if opcode == 2:
                n = len(payload) // 2
                if n > 4:
                    frame = struct.unpack("<{}H".format(n), payload[: n * 2])
                    self._set_frame(frame)
            elif opcode == 8:
                raise RuntimeError("server closed websocket")
            elif opcode == 9:
                self._send_control(s, 10, payload)

    def _need(self, s, buf, count):
        while len(buf) < count:
            chunk = s.recv(8192)
            if not chunk:
                raise RuntimeError("websocket closed")
            buf += chunk
        return buf

    def _read_frame(self, s, buf):
        buf = self._need(s, buf, 2)
        b1 = buf[0]
        b2 = buf[1]
        buf = buf[2:]
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        masked = (b2 & 0x80) != 0

        if length == 126:
            buf = self._need(s, buf, 2)
            length = struct.unpack("!H", buf[:2])[0]
            buf = buf[2:]
        elif length == 127:
            buf = self._need(s, buf, 8)
            length = struct.unpack("!Q", buf[:8])[0]
            buf = buf[8:]

        mask = None
        if masked:
            buf = self._need(s, buf, 4)
            mask = buf[:4]
            buf = buf[4:]

        buf = self._need(s, buf, length)
        payload = buf[:length]
        buf = buf[length:]

        if mask:
            payload = bytes(payload[i] ^ mask[i & 3] for i in range(length))
        return opcode, payload, buf

    def _send_control(self, s, opcode, payload):
        if payload is None:
            payload = b""
        if len(payload) > 125:
            payload = payload[:125]
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)])
        body = bytes(payload[i] ^ mask[i & 3] for i in range(len(payload)))
        s.sendall(header + mask + body)


def text_width(text, scale=1):
    return image.string_size(str(text), scale=scale).width()


def text_height(scale=1):
    return image.string_size("Ag", scale=scale).height()


def draw_text_center(img, x, y, text, col, scale=1):
    img.draw_string(int(x - text_width(text, scale) / 2), int(y), text, col, scale)


def draw_text_right(img, x, y, text, col, scale=1):
    img.draw_string(int(x - text_width(text, scale)), int(y), text, col, scale)


def freq_to_x(freq_mhz, plot_x, plot_w):
    return int(plot_x + ((freq_mhz - FFT_START_MHZ) / FFT_SPAN_MHZ) * plot_w)


def rel_freq_to_x(freq_rel, plot_x, plot_w):
    return int(plot_x + ((freq_rel - FFT_REL_START) / FFT_SPAN_MHZ) * plot_w)


def sample_to_y(sample, graph_top, graph_bottom):
    if sample < SAMPLE_FLOOR:
        sample = SAMPLE_FLOOR
    if sample > 65535:
        sample = 65535
    usable = graph_bottom - graph_top
    return int(graph_bottom - ((sample - SAMPLE_FLOOR) * usable / (65535 - SAMPLE_FLOOR)))


def align_symbolrate(width_mhz):
    if width_mhz < 0.022:
        return 0
    if width_mhz < 0.060:
        return 0.035
    if width_mhz < 0.086:
        return 0.066
    if width_mhz < 0.185:
        return 0.125
    if width_mhz < 0.277:
        return 0.250
    if width_mhz < 0.388:
        return 0.333
    if width_mhz < 0.700:
        return 0.500
    if width_mhz < 1.2:
        return 1.000
    if width_mhz < 1.6:
        return 1.500
    if width_mhz < 2.2:
        return 2.000
    return round(width_mhz * 5) / 5.0


def format_symbolrate(symrate_ms):
    if symrate_ms < 0.7:
        return "{}KS".format(int(round(symrate_ms * 1000)))
    return "{:.1f}MS".format(round(symrate_ms * 10) / 10)


def format_short_freq(freq_rel, symrate_ms):
    step = 80.0 if symrate_ms < 0.7 else 40.0
    return "'{:.3f}".format(round(freq_rel * step) / step)


def detect_signals(frame, plot_x, plot_w, graph_top, graph_bottom):
    signals = []
    in_signal = False
    start = 0
    length = len(frame)
    beacon_strength = 0

    def finish_signal(end):
        if end <= start + 3:
            return
        acc = 0
        acc_n = 0
        s0 = int(start + 0.3 * (end - start))
        s1 = int(start + 0.7 * (end - start))
        for j in range(s0, max(s0 + 1, s1)):
            acc += frame[j]
            acc_n += 1
        strength = acc / max(1, acc_n)

        real_start = start
        while real_start < end - 1 and frame[real_start] - NOISE_LEVEL < 0.75 * (strength - NOISE_LEVEL):
            real_start += 1
        real_end = end
        while real_end > real_start + 1 and frame[real_end - 1] - NOISE_LEVEL < 0.75 * (strength - NOISE_LEVEL):
            real_end -= 1

        mid = real_start + ((real_end - real_start) / 2.0)
        bw = align_symbolrate((real_end - real_start) * (FFT_SPAN_MHZ / length))
        rel_freq = FFT_REL_START + (((mid + 1) / length) * FFT_SPAN_MHZ)
        if rel_freq < 492.0 and bw >= 1.0:
            nonlocal_beacon[0] = strength
        if bw == 0:
            return

        x1 = int(plot_x + (real_start / length) * plot_w)
        x2 = int(plot_x + (real_end / length) * plot_w)
        top = sample_to_y(strength, graph_top, graph_bottom)
        signals.append((x1, x2, top, rel_freq, bw, strength))

    nonlocal_beacon = [0]
    for i in range(2, length):
        avg = (frame[i] + frame[i - 1] + frame[i - 2]) / 3.0
        if not in_signal and avg > SIGNAL_THRESHOLD:
            in_signal = True
            start = i
        elif in_signal and avg < SIGNAL_THRESHOLD:
            in_signal = False
            finish_signal(i)

    if in_signal:
        finish_signal(length - 1)

    beacon_strength = nonlocal_beacon[0]
    return signals, beacon_strength


def draw_grid(img, plot_x, plot_y, plot_w, plot_h, graph_top, graph_bottom):
    img.draw_rect(0, 0, img.width(), img.height(), COL_BG, -1)
    img.draw_rect(plot_x - 1, plot_y - 1, plot_w + 2, plot_h + 2, COL_PANEL, -1)

    draw_text_center(img, img.width() // 2, 5, APP_TITLE, COL_YELLOW, 1.35)

    for mhz in range(10491, 10500):
        x = freq_to_x(mhz, plot_x, plot_w)
        img.draw_line(x, graph_top, x, graph_bottom, COL_GRID, 1)
        draw_text_center(img, x, 27, "{:.3f}".format(mhz / 1000.0), COL_MUTED, 0.75)

    for i in range(0, 16):
        y = int(graph_top + i * (graph_bottom - graph_top) / 15.0)
        img.draw_line(plot_x, y, plot_x + plot_w, y, COL_GRID if i % 5 == 0 else COL_MINOR, 1)
        if i in (0, 5, 10):
            label = "{}dB".format(15 - i)
            img.draw_string(2, y - 7, label, COL_MUTED, 0.65)
            draw_text_right(img, img.width() - 2, y - 7, label, COL_MUTED, 0.65)

    for rel, y_factor in ((492.5, 0.94), (497.0, 0.97)):
        x = rel_freq_to_x(rel, plot_x, plot_w)
        img.draw_line(x, int(graph_bottom + 4), x, int(plot_y + plot_h * y_factor), COL_GRID, 1)

    band_y = graph_bottom + 14
    rolloff = 1.35 / 2.0

    def channel(center_rel, bandwidth, y, h=4):
        x1 = rel_freq_to_x(center_rel - rolloff * bandwidth, plot_x, plot_w)
        x2 = rel_freq_to_x(center_rel + rolloff * bandwidth, plot_x, plot_w)
        img.draw_rect(x1, y, max(1, x2 - x1), h, COL_BAND, -1)

    f = 493.25
    while f <= 496.251:
        channel(f, 1.0, band_y + 39, 5)
        f += 1.5
    f = 492.75
    while f <= 499.251:
        channel(f, 0.333, band_y + 21, 4)
        f += 0.5
    f = 492.75
    while f <= 499.251:
        channel(f, 0.125, band_y + 5, 3)
        f += 0.25

    draw_text_center(img, rel_freq_to_x(491.5, plot_x, plot_w), band_y + 53, "A71A DATV", COL_TEXT, 0.8)
    draw_text_center(img, rel_freq_to_x(491.5, plot_x, plot_w), band_y + 68, "10491.500", COL_MUTED, 0.75)
    draw_text_center(img, rel_freq_to_x(494.75, plot_x, plot_w), band_y + 68, "Wide + Narrow DATV", COL_TEXT, 0.75)
    draw_text_center(img, rel_freq_to_x(498.25, plot_x, plot_w), band_y + 68, "Narrow DATV", COL_TEXT, 0.75)


def draw_spectrum(img, frame, plot_x, plot_w, graph_top, graph_bottom):
    if not frame:
        draw_text_center(img, img.width() // 2, (graph_top + graph_bottom) // 2, "Connecting to BATC spectrum feed", COL_MUTED, 1)
        return [], 0

    n = len(frame)
    prev_x = plot_x
    prev_y = sample_to_y(frame[0], graph_top, graph_bottom)
    for x in range(plot_w):
        idx = int((x * n) / plot_w)
        sample = frame[idx]
        if sample > SAMPLE_FLOOR:
            px = plot_x + x
            y = sample_to_y(sample, graph_top, graph_bottom)
            img.draw_line(px, graph_bottom, px, y, COL_GREEN, 1)
            if x > 0:
                img.draw_line(prev_x, prev_y, px, y, COL_YELLOW if sample > 30000 else COL_GREEN, 1)
            prev_x = px
            prev_y = y

    signals, beacon_strength = detect_signals(frame, plot_x, plot_w, graph_top, graph_bottom)
    for x1, x2, top, rel_freq, bw, strength in signals:
        if rel_freq < 492.0:
            continue
        if x2 - x1 < 4:
            continue
        label = "{}, {}".format(format_symbolrate(bw), format_short_freq(rel_freq, bw))
        label_x = int((x1 + x2) / 2)
        if label_x > img.width() - 45:
            label_x = img.width() - 45
        if label_x < 45:
            label_x = 45
        label_y = max(graph_top + 4, top - 17)
        draw_text_center(img, label_x, label_y, label, COL_TEXT, 0.65)
    return signals, beacon_strength


def draw_status(img, frames, last_frame_ms, status, error):
    age_ms = now_ms() - last_frame_ms if last_frame_ms else 0
    if status == "live" and age_ms < 3000:
        msg = "Live frames: {}  age: {:.1f}s".format(frames, age_ms / 1000.0)
        col = COL_GREEN
    elif status in ("connecting", "waiting"):
        msg = "{} {}".format(status, BATC_HOST)
        col = COL_YELLOW
    else:
        short_error = error
        if len(short_error) > 42:
            short_error = short_error[:39] + "..."
        msg = "{} {}".format(status, short_error)
        col = COL_RED
    img.draw_string(8, img.height() - 24, msg, col, 0.8)
    draw_text_right(img, img.width() - 8, img.height() - 24, "tap X to exit", COL_MUTED, 0.75)


def draw_exit(img, touched):
    bg = COL_RED if touched else color(28, 34, 40)
    img.draw_rect(6, 4, 38, 26, bg, -1)
    draw_text_center(img, 25, 8, "X", COL_TEXT, 1)


def is_exit_touch(t):
    return t and t[2] and t[0] <= 54 and t[1] <= 42


def maybe_save_screenshot(img, start_ms, saved):
    if saved or not SCREENSHOT_PATH:
        return saved
    if now_ms() - start_ms < SCREENSHOT_AFTER_MS:
        return saved
    try:
        directory = os.path.dirname(SCREENSHOT_PATH)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        img.save(SCREENSHOT_PATH)
        print("saved screenshot {}".format(SCREENSHOT_PATH))
        return True
    except Exception as e:
        print("screenshot save failed: {}".format(e))
        return saved


disp = display.Display()
try:
    ts = touchscreen.TouchScreen()
except Exception as e:
    print("touch init skipped: {}".format(e))
    ts = None

screen_w = disp.width()
screen_h = disp.height()
base_img = image.Image(screen_w, screen_h, bg=COL_BG)

plot_x = 12
plot_y = 24
plot_w = screen_w - 24
plot_h = screen_h - 58
graph_top = plot_y + 26
graph_bottom = plot_y + int(plot_h * 0.78)

client = BatcFftClient()
client.start()

start_ms = now_ms()
last_render_ms = 0
screenshot_saved = False

try:
    while not app.need_exit():
        touched_exit = False
        if ts:
            try:
                touched_exit = is_exit_touch(ts.read())
            except Exception:
                touched_exit = False
        if touched_exit:
            app.set_exit_flag(True)
            break

        frame, frames, last_frame_ms, status, error = client.snapshot()
        draw_grid(base_img, plot_x, plot_y, plot_w, plot_h, graph_top, graph_bottom)
        draw_spectrum(base_img, frame, plot_x, plot_w, graph_top, graph_bottom)
        draw_status(base_img, frames, last_frame_ms, status, error)
        draw_exit(base_img, touched_exit)
        disp.show(base_img)
        screenshot_saved = maybe_save_screenshot(base_img, start_ms, screenshot_saved)

        last_render_ms = now_ms()
        elapsed = now_ms() - last_render_ms
        if elapsed < 180:
            time.sleep_ms(180 - elapsed)
finally:
    client.stop()
