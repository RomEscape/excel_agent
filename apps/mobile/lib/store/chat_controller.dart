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

  /// 데스크톱이 보낸 승인 대기 요청(CONFIRM 도구). null이면 대기 중인 승인 없음.
  ///
  /// 이걸 무시하면 데스크톱은 승인 대기로 멈춰 있는데 폰에는 아무 표시가 없어
  /// "요청했더니 그냥 아무 일도 안 일어남"으로 보인다.
  final ApprovalRequest? pendingApproval;

  const ChatState({
    this.messages = const [],
    this.agentState = AgentState.idle,
    this.connected = false,
    this.pendingApproval,
  });

  /// [clearApproval]이 true면 [pendingApproval]을 null로 지운다.
  /// (`??` 병합만으로는 값을 null로 되돌릴 수 없다.)
  ChatState copyWith({
    List<ChatMessage>? messages,
    AgentState? agentState,
    bool? connected,
    ApprovalRequest? pendingApproval,
    bool clearApproval = false,
  }) => ChatState(
    messages: messages ?? this.messages,
    agentState: agentState ?? this.agentState,
    connected: connected ?? this.connected,
    pendingApproval: clearApproval
        ? null
        : (pendingApproval ?? this.pendingApproval),
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

    // 이전 세션의 승인 대기는 새 연결에서 응답해봐야 데스크톱이 모르는 request_id다.
    state = state.copyWith(clearApproval: true);

    final t = RelayTransport(relayHttpUrl: relayUrl, pairingId: pairingId);
    _transport = t;
    _sub = t.incoming.listen(_onFrame);
    _connSub = t.connectionState.listen(
      (c) => state = state.copyWith(connected: c),
    );
    // connect()가 던지는 건 재시도 불가능한 주소 정책 위반뿐이다(전송 오류는 내부에서
    // backoff 재시도). 삼켜버리면 unhandled async error로 흘러 사용자는 이유를 못 보므로
    // 채팅에 사유를 남긴다.
    unawaited(
      t.connect().catchError((Object e) {
        _appendSystem('연결할 수 없습니다 — $e');
      }),
    );
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
      case StreamEnd(:final streamId, :final reason, :final error):
        _finishStream(streamId, reason: reason, error: error);
      case AgentStatus(state: final agentSt):
        state = state.copyWith(agentState: agentSt);
      case final ApprovalRequest req:
        // 승인 UI가 뜨는 동안 에이전트는 대기 상태다(데스크톱도 idle을 보낸다).
        state = state.copyWith(pendingApproval: req);
      case ErrorFrame(:final message):
        _appendSystem('오류: $message');
      default:
        break; // Ack/Ping/Pong 등 — 표시할 것 없음
    }
  }

  /// 승인 요청에 응답한다. 거부하면 데스크톱이 스트림을 aborted로 닫는다.
  void respondApproval(bool approved) {
    final pending = state.pendingApproval;
    final t = _transport;
    if (pending == null || t == null) return;
    // 먼저 지워야 같은 요청에 두 번 응답하는 걸 막는다.
    state = state.copyWith(clearApproval: true);
    t.send(
      ApprovalResponse(requestId: pending.requestId, approved: approved),
    );
    if (!approved) {
      _appendSystem('요청을 거부했습니다 — ${pending.command}');
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

  /// 스트림 종료 처리. [reason]이 error면 사유를 대화에 남긴다 —
  /// 무시하면 사용자에겐 **빈 말풍선**만 보이고 원인은 데스크톱 콘솔에만 남는다.
  void _finishStream(
    String streamId, {
    String reason = 'complete',
    String? error,
  }) {
    final msgs = [...state.messages];
    final idx = msgs.indexWhere((m) => m.id == 'a_$streamId');
    if (idx != -1) msgs[idx] = msgs[idx].copyWith(streaming: false);
    state = state.copyWith(messages: msgs, agentState: AgentState.idle);
    if (reason == 'error') {
      final detail = (error == null || error.trim().isEmpty)
          ? '알 수 없는 오류'
          : error;
      _appendSystem('응답 실패 — $detail');
    }
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
