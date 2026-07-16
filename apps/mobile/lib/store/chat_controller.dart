import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../protocol/protocol.dart';
import '../transport/relay_transport.dart';

/// 채팅 메시지 (스트리밍 중이면 text가 조각마다 늘어난다).
class ChatMessage {
  final String id;
  final bool fromUser;
  final String text;
  final bool streaming;
  const ChatMessage({
    required this.id,
    required this.fromUser,
    this.text = '',
    this.streaming = false,
  });

  ChatMessage copyWith({String? text, bool? streaming}) => ChatMessage(
    id: id,
    fromUser: fromUser,
    text: text ?? this.text,
    streaming: streaming ?? this.streaming,
  );
}

class ChatState {
  final List<ChatMessage> messages;
  final AgentState agentState;
  final bool connected;
  const ChatState({
    this.messages = const [],
    this.agentState = AgentState.idle,
    this.connected = false,
  });

  ChatState copyWith({
    List<ChatMessage>? messages,
    AgentState? agentState,
    bool? connected,
  }) => ChatState(
    messages: messages ?? this.messages,
    agentState: agentState ?? this.agentState,
    connected: connected ?? this.connected,
  );
}

/// 세션 상태를 소유하고 transport 프레임을 채팅 상태로 반영한다.
class ChatController extends Notifier<ChatState> {
  RelayTransport? _transport;
  StreamSubscription<Frame>? _sub;
  StreamSubscription<bool>? _connSub;

  @override
  ChatState build() {
    ref.onDispose(() {
      _sub?.cancel();
      _connSub?.cancel();
      _transport?.dispose();
    });
    return const ChatState();
  }

  /// relay에 연결하고 프레임 수신을 시작한다(수동 페어링: pairingId 직접 입력).
  void connect({required String relayUrl, required String pairingId}) {
    _sub?.cancel();
    _connSub?.cancel();
    _transport?.dispose();

    final t = RelayTransport(relayHttpUrl: relayUrl, pairingId: pairingId);
    _transport = t;
    _sub = t.incoming.listen(_onFrame);
    _connSub = t.connectionState.listen(
      (c) => state = state.copyWith(connected: c),
    );
    unawaited(t.connect());
  }

  void sendMessage(String text) {
    final t = _transport;
    final trimmed = text.trim();
    if (t == null || trimmed.isEmpty) return;
    final id = DateTime.now().microsecondsSinceEpoch.toString();
    state = state.copyWith(
      messages: [
        ...state.messages,
        ChatMessage(id: id, fromUser: true, text: trimmed),
      ],
    );
    t.send(ChatUserMsg(clientMsgId: id, text: trimmed));
  }

  void _onFrame(Frame f) {
    switch (f) {
      case TokenDelta(:final streamId, :final text):
        _appendToken(streamId, text);
      case StreamEnd(:final streamId):
        _finishStream(streamId);
      case AgentStatus(state: final agentSt):
        state = state.copyWith(agentState: agentSt);
      case ErrorFrame(:final message):
        _appendSystem('오류: $message');
      default:
        break; // ApprovalRequest/Ack/Ping/Pong 등 — 후속 단계
    }
  }

  void _appendToken(String streamId, String text) {
    final msgs = [...state.messages];
    final idx = msgs.indexWhere((m) => m.id == 'a_$streamId');
    if (idx == -1) {
      msgs.add(
        ChatMessage(id: 'a_$streamId', fromUser: false, text: text, streaming: true),
      );
    } else {
      msgs[idx] = msgs[idx].copyWith(text: msgs[idx].text + text);
    }
    state = state.copyWith(messages: msgs);
  }

  void _finishStream(String streamId) {
    final msgs = [...state.messages];
    final idx = msgs.indexWhere((m) => m.id == 'a_$streamId');
    if (idx != -1) msgs[idx] = msgs[idx].copyWith(streaming: false);
    state = state.copyWith(messages: msgs, agentState: AgentState.idle);
  }

  void _appendSystem(String text) {
    state = state.copyWith(
      messages: [
        ...state.messages,
        ChatMessage(
          id: DateTime.now().microsecondsSinceEpoch.toString(),
          fromUser: false,
          text: text,
        ),
      ],
    );
  }
}

final chatControllerProvider = NotifierProvider<ChatController, ChatState>(
  ChatController.new,
);
