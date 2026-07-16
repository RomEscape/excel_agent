/// oc_protocol 대응 — 데스크톱·릴레이와 공유하는 와이어 프레임.
///
/// SSOT는 `packages/protocol`(Python). relay는 payload를 해석하지 않으므로(content-blind)
/// 이 프레임은 데스크톱↔모바일 두 endpoint 사이의 계약이다.
library;

/// 데스크톱 에이전트 상태 (모바일 상단 인디케이터용).
enum AgentState {
  idle,
  thinking,
  remoteControlling,
  offline;

  String get wire => switch (this) {
    AgentState.idle => 'idle',
    AgentState.thinking => 'thinking',
    AgentState.remoteControlling => 'remote_controlling',
    AgentState.offline => 'offline',
  };

  static AgentState fromWire(String s) => switch (s) {
    'thinking' => AgentState.thinking,
    'remote_controlling' => AgentState.remoteControlling,
    'offline' => AgentState.offline,
    _ => AgentState.idle,
  };
}

/// 프레임의 논리적 진행 방향.
enum Direction {
  toDesktop,
  toMobile;

  String get wire => this == Direction.toDesktop ? 'to_desktop' : 'to_mobile';
  static Direction fromWire(String s) =>
      s == 'to_desktop' ? Direction.toDesktop : Direction.toMobile;
}

/// Envelope.payload에 담기는 프레임 (discriminated union — `type`으로 분기).
sealed class Frame {
  const Frame();

  String get type;
  Map<String, dynamic> toJson();

  static Frame fromJson(Map<String, dynamic> j) {
    final t = j['type'] as String;
    return switch (t) {
      'chat_user_msg' => ChatUserMsg.fromJson(j),
      'token_delta' => TokenDelta.fromJson(j),
      'stream_end' => StreamEnd.fromJson(j),
      'agent_status' => AgentStatus.fromJson(j),
      'approval_request' => ApprovalRequest.fromJson(j),
      'approval_response' => ApprovalResponse.fromJson(j),
      'ack' => Ack.fromJson(j),
      'ping' => Ping.fromJson(j),
      'pong' => Pong.fromJson(j),
      'error' => ErrorFrame.fromJson(j),
      _ => throw FormatException('알 수 없는 frame type: $t'),
    };
  }
}

/// 모바일 → 데스크톱: 사용자 입력.
class ChatUserMsg extends Frame {
  final String clientMsgId;
  final String text;
  const ChatUserMsg({required this.clientMsgId, required this.text});

  @override
  String get type => 'chat_user_msg';
  @override
  Map<String, dynamic> toJson() => {
    'type': type,
    'client_msg_id': clientMsgId,
    'text': text,
  };
  factory ChatUserMsg.fromJson(Map<String, dynamic> j) =>
      ChatUserMsg(clientMsgId: j['client_msg_id'] as String, text: j['text'] as String);
}

/// 데스크톱 → 모바일: LLM 토큰 스트리밍 조각.
class TokenDelta extends Frame {
  final String streamId;
  final int index;
  final String text;
  const TokenDelta({required this.streamId, required this.index, required this.text});

  @override
  String get type => 'token_delta';
  @override
  Map<String, dynamic> toJson() => {
    'type': type,
    'stream_id': streamId,
    'index': index,
    'text': text,
  };
  factory TokenDelta.fromJson(Map<String, dynamic> j) => TokenDelta(
    streamId: j['stream_id'] as String,
    index: j['index'] as int,
    text: j['text'] as String,
  );
}

/// 데스크톱 → 모바일: 스트림 종료/중단.
class StreamEnd extends Frame {
  final String streamId;
  final String reason; // complete | aborted | error
  final String? error;
  const StreamEnd({required this.streamId, this.reason = 'complete', this.error});

  @override
  String get type => 'stream_end';
  @override
  Map<String, dynamic> toJson() => {
    'type': type,
    'stream_id': streamId,
    'reason': reason,
    if (error != null) 'error': error,
  };
  factory StreamEnd.fromJson(Map<String, dynamic> j) => StreamEnd(
    streamId: j['stream_id'] as String,
    reason: (j['reason'] as String?) ?? 'complete',
    error: j['error'] as String?,
  );
}

/// 데스크톱 → 모바일: 에이전트 상태 동기화.
class AgentStatus extends Frame {
  final AgentState state;
  const AgentStatus({required this.state});

  @override
  String get type => 'agent_status';
  @override
  Map<String, dynamic> toJson() => {'type': type, 'state': state.wire};
  factory AgentStatus.fromJson(Map<String, dynamic> j) =>
      AgentStatus(state: AgentState.fromWire(j['state'] as String));
}

/// 데스크톱 → 모바일: HITL 승인 요청 (CONFIRM 도구).
class ApprovalRequest extends Frame {
  final String requestId;
  final String command;
  final String reason;
  const ApprovalRequest({
    required this.requestId,
    required this.command,
    required this.reason,
  });

  @override
  String get type => 'approval_request';
  @override
  Map<String, dynamic> toJson() => {
    'type': type,
    'request_id': requestId,
    'command': command,
    'reason': reason,
  };
  factory ApprovalRequest.fromJson(Map<String, dynamic> j) => ApprovalRequest(
    requestId: j['request_id'] as String,
    command: j['command'] as String,
    reason: (j['reason'] as String?) ?? '',
  );
}

/// 모바일 → 데스크톱: 승인 응답.
class ApprovalResponse extends Frame {
  final String requestId;
  final bool approved;
  const ApprovalResponse({required this.requestId, required this.approved});

  @override
  String get type => 'approval_response';
  @override
  Map<String, dynamic> toJson() => {
    'type': type,
    'request_id': requestId,
    'approved': approved,
  };
  factory ApprovalResponse.fromJson(Map<String, dynamic> j) => ApprovalResponse(
    requestId: j['request_id'] as String,
    approved: j['approved'] as bool,
  );
}

/// 양방향: 수신 확인(재연결 재개용).
class Ack extends Frame {
  final int ackSeq;
  const Ack({required this.ackSeq});

  @override
  String get type => 'ack';
  @override
  Map<String, dynamic> toJson() => {'type': type, 'ack_seq': ackSeq};
  factory Ack.fromJson(Map<String, dynamic> j) => Ack(ackSeq: j['ack_seq'] as int);
}

/// 양방향: 앱 레벨 liveness.
class Ping extends Frame {
  final String nonce;
  const Ping({required this.nonce});

  @override
  String get type => 'ping';
  @override
  Map<String, dynamic> toJson() => {'type': type, 'nonce': nonce};
  factory Ping.fromJson(Map<String, dynamic> j) => Ping(nonce: j['nonce'] as String);
}

class Pong extends Frame {
  final String nonce;
  const Pong({required this.nonce});

  @override
  String get type => 'pong';
  @override
  Map<String, dynamic> toJson() => {'type': type, 'nonce': nonce};
  factory Pong.fromJson(Map<String, dynamic> j) => Pong(nonce: j['nonce'] as String);
}

/// relay 또는 endpoint → : 오류 통지.
class ErrorFrame extends Frame {
  final String code;
  final String message;
  const ErrorFrame({required this.code, required this.message});

  @override
  String get type => 'error';
  @override
  Map<String, dynamic> toJson() => {'type': type, 'code': code, 'message': message};
  factory ErrorFrame.fromJson(Map<String, dynamic> j) =>
      ErrorFrame(code: j['code'] as String, message: j['message'] as String);
}
