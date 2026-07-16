import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import '../protocol/protocol.dart';

/// relay로 아웃바운드 WS 연결. Envelope 프레임 송수신 + 간단 재연결(backoff).
///
/// 모바일도 NAT 뒤라 relay로 dial-out한다. 들어온 프레임은 [incoming]으로 흘리고,
/// presence control 메시지는 지금은 무시한다(추후 연결상태 반영).
class RelayTransport {
  final String relayHttpUrl; // 예: http://127.0.0.1:8787
  final String pairingId;

  WebSocketChannel? _channel;
  final _incoming = StreamController<Frame>.broadcast();
  final _connState = StreamController<bool>.broadcast();
  int _seq = 0;
  bool _stopped = false;
  bool _connected = false;

  RelayTransport({required this.relayHttpUrl, required this.pairingId});

  Stream<Frame> get incoming => _incoming.stream;
  Stream<bool> get connectionState => _connState.stream;
  bool get connected => _connected;

  String get _wsUrl {
    var base = relayHttpUrl.trim();
    if (base.startsWith('https://')) {
      base = 'wss://${base.substring(8)}';
    } else if (base.startsWith('http://')) {
      base = 'ws://${base.substring(7)}';
    } else if (!base.startsWith('ws://') && !base.startsWith('wss://')) {
      base = 'ws://$base';
    }
    return '$base/ws/mobile?pairing_id=$pairingId';
  }

  /// 중단(dispose)될 때까지 연결을 유지하며 재연결한다.
  Future<void> connect() async {
    _stopped = false;
    while (!_stopped) {
      try {
        final ch = WebSocketChannel.connect(Uri.parse(_wsUrl));
        await ch.ready;
        _channel = ch;
        _setConnected(true);
        await for (final raw in ch.stream) {
          _handleRaw(raw is String ? raw : raw.toString());
        }
      } catch (_) {
        // 연결 실패/끊김 → 아래에서 재연결
      }
      _setConnected(false);
      _channel = null;
      if (_stopped) break;
      await Future<void>.delayed(const Duration(seconds: 2));
    }
  }

  void _handleRaw(String raw) {
    try {
      final obj = jsonDecode(raw);
      if (obj is Map && obj.containsKey('control')) {
        return; // presence control — 프레임 아님
      }
      _incoming.add(Envelope.decode(raw).payload);
    } catch (_) {
      // 파싱 불가 프레임 무시
    }
  }

  /// 프레임을 Envelope(to_desktop)로 감싸 전송.
  void send(Frame frame) {
    final ch = _channel;
    if (ch == null) return;
    _seq += 1;
    ch.sink.add(
      Envelope(
        pairingId: pairingId,
        direction: Direction.toDesktop,
        seq: _seq,
        payload: frame,
      ).encode(),
    );
  }

  void _setConnected(bool v) {
    _connected = v;
    if (!_connState.isClosed) _connState.add(v);
  }

  Future<void> dispose() async {
    _stopped = true;
    await _channel?.sink.close();
    if (!_incoming.isClosed) await _incoming.close();
    if (!_connState.isClosed) await _connState.close();
  }
}
